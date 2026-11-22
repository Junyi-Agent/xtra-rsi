# 0008 运行状态文件 status.yaml 与只读可视化面板 rsi viz

状态：采纳（2026-11-22 用户要求实现）

## 背景

loop 的运行产物只有 `iter-<n>/<stage>.yaml`，阶段跑完才写。下游（例如 kernel-benchmark-rsi）的 action 阶段里一个 agent 可能跑一小时，期间从磁盘上看不出：

- 这个 loop 在改进什么（目标、指标）；
- 现在跑到哪一轮、哪个阶段；
- 进程还活着没有。缺 `action.yaml` 既可能是正在跑，也可能是进程已经被杀。

同时跑几十个 loop（任务 × agent × 经验条件 × seed）时，人需要一个能看全局、也能放大看单个 loop 每轮每个阶段的界面。

## 决策

### status.yaml

`run_loop(run_dir=...)` 在 `run_dir/status.yaml` 维护运行状态（`xtra_rsi.status`）：

| 字段 | 含义 |
|---|---|
| `profile`、`goal` | profile 名；loop 在改进什么（`Profile.goal`，约定键 `target`、`metric`） |
| `state` | `running` / `done`（跑满或 feedback `stop`）/ `stopped`（`StopLoop`）/ `failed`（异常或中断） |
| `iteration`、`iterations`、`stage` | 正在跑（或最后跑）的迭代和阶段、计划的迭代数 |
| `pid`、`host`、`heartbeat_at`、`heartbeat_s` | 后台线程每 `heartbeat_s` 秒刷新心跳 |
| `started_at`、`updated_at`、`reason`、`error` | 时间、提前结束原因、异常 |
| `stage_started_at` | 当前阶段的开始时间（毫秒精度），loop 结束后为 null（0.4.5 起） |

- 进入每个阶段前写一次，结束时写最终状态；整体替换写入（临时文件 + rename）。
- 读的一方判断「失联」：同一台机器上 pid 不存在，或心跳超过 3 倍间隔 + 5 秒没更新。
- `Profile` 新增可选字段 `goal`（默认空）。以节点身份运行（`run_node`）时，goal 用节点的 target、metric、预算覆盖。
- 不传 `run_dir` 时不写任何文件，行为不变。

### timeline.yaml（0.4.5 起）

每个阶段结束时往 `run_dir/timeline.yaml` 追加一项 `{iteration, stage, start, end, outcome}`（毫秒精度，`outcome` 取 `ok` / `failed`）。整个文件是一个 YAML 列表，只追加不改写，每次 `run_loop` 开始时清空。正在跑的阶段不在里面，看 status.yaml 的 `stage_started_at`。追加到一半进程被杀会留下半项，读的一方整份当作读不了，退回下面的估计。

没有这个文件的旧 run（0.4.4 及以前写的）用阶段输出文件的修改时间估计：阶段结束 = 文件 mtime，开始 = 上一个阶段结束，面板上标为「估计」。

### rsi viz

`rsi viz [目录...] [--host 127.0.0.1] [--port 8765]`：只读的本地 HTTP 服务 + 单文件页面（无外部依赖、无构建步骤）。

- 扫描目录里的 loop（含 `status.yaml` 或 `iter-*/` 的目录）和节点表（含 `<id>/node.yaml` 的目录），不进入 `workspace/`、`harness/`、隐藏目录。没有 `status.yaml` 的旧 run 也能显示（状态记为「无状态」）。
- 页面是可平移缩放的画布，按缩放程度显示不同细节：远景每个 loop 一张卡片（标题、状态、目标、主指标）；中景是五个阶段的环（当前阶段高亮）、指标、曲线；近景是「每轮 × 五个阶段」的格子；点击打开抽屉看阶段输出全文和阶段内部过程。节点表画成树。支持触屏：单指平移、双指缩放、双击（两次轻点）聚焦。状态后面括号里附时间（运行中 / 失联是最后一次心跳，已结束是最后更新），不单列心跳。
- 时间线视图（顶栏「画布 / 时间线」切换）：每个 loop 一条泳道，横轴是时间，色块是阶段的起止（`GET /api/timeline?ids=...`，一次最多 400 个 loop），跟画布共用状态筛选和搜索。可切换「绝对时间」（看各个 loop 同时在跑什么）和「从开始算起」（比较进度）；Ctrl / ⌘ + 滚轮或双指横向缩放，拖动平移；点色块打开那一轮那个阶段的抽屉。正在跑的阶段画到现在，失联的画到最后一次心跳并标 ✗。
- 内部过程里连续的工具调用（tool / result / thinking / 出错的 result）折叠成一行「N 次连续工具调用」，附工具名分布、思考段数和出错数，点开看每一步；agent 说的话（text）照常显示。
- 服务只读扫描到的目录，按 id 取 loop，不接受任意路径；默认只监听 127.0.0.1。远程机器用 ssh 端口转发。

### 下游插件

entry point `xtra_rsi.viz`：`describe(loop_dir: Path, *, detail: bool) -> Payload | None`。不认识的 loop 返回 None。可返回 `title`、`subtitle`、`group`、`goal`、`kpis`、`series`、`stage_notes`，以及 `detail=True` 时的 `inner`（阶段内部过程，例如被测 agent 的工具调用；可以按会话分块，标明每个会话是否从头开始、开始时拿到了什么输入）。字段格式见 `xtra_rsi.viz.data` 的模块文档。插件报错显示在卡片上，不影响其他 loop。

## 后果

- 公开接口新增：`status.yaml` 格式（含 `stage_started_at`）、`timeline.yaml` 格式、`Profile.goal` / `Profile.with_goal`、`rsi viz`、entry point `xtra_rsi.viz` 及其返回格式。改动按 AGENTS.md 的公开接口规则处理。
- 每个带 `run_dir` 的 `run_loop` 多一个守护线程，每 10 秒写一次小文件。
- 面板靠轮询（2.5 秒刷新列表，近景和抽屉 4–5 秒刷新），没有推送；loop 很多时每次扫描是一次目录遍历，服务端最多每 5 秒扫描一次。

## 验证

（由人类填写：测了什么、结果如何）
