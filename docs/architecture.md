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

- 每个阶段是一个 `StageFn`：输入 `StageContext`（`stage`、`iteration`、`upstream`、`history`），输出 JSON 对象。
- `upstream` 是上一个阶段的输出。observe 的 `upstream` 是上一轮 feedback 的输出，第 0 轮是 `Profile.seed`。
- 阶段之间只通过 JSON payload 通信，所以每个阶段都能单独替换、单独 ablation，也能写到磁盘复现（`rsi run --run-dir`）。
- feedback 输出 `stop: true` 时 loop 提前结束。

## 自我改进落在哪里

loop 改进的对象有两层：

1. **任务层**：action 改任务的参数、策略、产物。
2. **agent 自身**：action 也可以改 harness——某个阶段的 AGENTS.md、scripts、data；feedback 调整 loop 自身的参数（toy profile 里是学习率）。

第二层就是「recursive」：loop 产出的改动会改变下一轮 loop 的行为。所有这类改动都要通过阶段 evals 证明没有变差。

## Profile

`Profile` = 五个阶段的实现 + `seed` + `metrics`。通过 entry point `xtra_rsi.profiles` 注册，`rsi --profile <name>` 按名字加载。框架自带 `toy`：在一维函数上找最优点，feedback 自适应调学习率，用来示范和测试。

接 LLM agent 时用 `CommandStage`：把阶段 AGENTS.md 全文和 `upstream` 作为 JSON 写进命令的 stdin，命令把阶段输出 JSON 写到 stdout。

## 阶段 ablation

`rsi eval <stage>` 读 `harness/<stage>/evals/cases.json`，分别跑完整 profile 和「把该阶段换成直通」的 profile，比较指标：

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
