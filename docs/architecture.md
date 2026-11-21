# 架构

## loop

```
           seed
            │
            ▼
   ┌──► observe ──► reasoning ──► decision ──► action ──► feedback ──┐
   │                                                                 │
   └─────────────────────── upstream（下一轮）─────────────────────────┘
```

- 每个阶段是一个 `StageFn`：输入 `StageContext`（`stage`、`iteration`、`upstream`、`history`），输出 `Payload`（键为字符串的映射）。
- `upstream` 是上一个阶段的输出。observe 的 `upstream` 是上一轮 feedback 的输出，第 0 轮是 `Profile.seed`。
- 阶段之间只传 `Payload`，进程外一律序列化为 YAML（`xtra_rsi.yamlio`），所以每个阶段都能单独替换、单独 ablation，也能写到磁盘复现（`rsi run --run-dir`）。
- feedback 输出 `stop: true` 时 loop 提前结束。

## 自我改进落在哪里

loop 改进的对象有两层：

1. **任务层**：action 改任务的参数、策略、产物。
2. **agent 自身**：action 也可以改 harness——某个阶段的 AGENTS.md、scripts、data；feedback 调整 loop 自身的参数（toy profile 里是学习率）。

第二层就是「recursive」：loop 产出的改动会改变下一轮 loop 的行为。所有这类改动都要通过阶段 evals 证明没有变差。

## Profile

`Profile` = 五个阶段的实现 + `seed` + `metrics`。通过 entry point `xtra_rsi.profiles` 注册，`rsi --profile <name>` 按名字加载。框架自带 `toy`：在一维函数上找最优点，feedback 自适应调学习率，用来示范和测试。

接 LLM agent 时用 `CommandStage`：把阶段 AGENTS.md 全文和 `upstream` 作为 YAML 写进命令的 stdin，命令把阶段输出（YAML 映射，可以包在 ```yaml 代码块里）写到 stdout。

## 阶段 ablation

`rsi eval <stage>` 读 `harness/<stage>/evals/cases.yaml`，分别跑完整 profile 和「把该阶段换成直通」的 profile，比较指标：

```
delta = metric(完整) - metric(去掉该阶段)    通过条件：delta >= min_delta
```

toy profile 上的结果：前四个阶段任一去掉，loop 完全不动（delta ≈ 0.49）；去掉 feedback，学习率不再自适应（delta ≈ 0.021）。

## harness 分层

```
xtra-rsi 包内 _harness/<stage>/AGENTS.md   基础层（本仓库 harness/）
        +
下游 harness/<stage>/AGENTS.local.md         项目补充
        ║  rsi sync
        ▼
下游 harness/<stage>/AGENTS.md               生成文件，不手改
```

见 [downstream.md](downstream.md) 和 [ADR 0002](adr/0002-downstream-sync.md)。

## 嵌套 RSI（ADR 0007）

每个阶段本身也可以被一个 RSI 改进。所有层用同一种节点：

```
root（目标：任务本身，例如 fraud 模型）
 ├─ n0001（目标：root 的 harness/observe）
 │   └─ n0003（目标：n0001 的 harness/decision）……
 └─ n0002（目标：root 的 harness/reasoning）
```

- **节点表**：`rsi/nodes/<id>/` 下的 `node.yaml`、`contract.yaml`（父节点给的约定）、`summary.yaml`（交给父节点的摘要）、`ledger.yaml`（花费流水）、`harness/`（子节点自己的五个阶段）、`runs/`。
- **局部视野**：节点只通过 `NodeView` 看到自己、约定和子节点摘要，以 `_node` 注入 observe 的输入。
- **开子节点**：decision 输出 `spawn_request` → 待人确认的子节点 → 人运行 `! uv run rsi approve <id>`（agent 运行会被 `git_guard.py` 拒绝）→ 从父节点剩余预算里划出预算、生成子节点的 harness。
- **预算**：美元。阶段输出里的 `_usage` 按 `pricing.py` 的单价表换算后记账；超支或无法记账时 loop 停止。子节点结束时花费并入父节点，余额退回。
- **深度**：不设上限，靠预算只分不增、回放数据变少、无收益撤销自然停止。

尚未实现（ADR 0007「实现顺序」第 2、3 步）：回放评估、子节点提案的应用与把关、根节点定期回归、自动撤销。

## 运行状态与可视化（ADR 0008）

- `run_loop(run_dir=...)` 维护 `run_dir/status.yaml`：`goal`（`Profile.goal`）、`state`、当前 `iteration` / `stage`、`pid` / `host` / 心跳。阶段可能跑很久，心跳由守护线程刷新；读的一方据此区分「正在跑」和「进程没了」。
- `rsi viz`（`xtra_rsi.viz`）是只读的本地面板：扫描 loop 目录和节点表，在可缩放的画布上显示；下游通过 entry point `xtra_rsi.viz` 补充业务指标和阶段内部过程。
