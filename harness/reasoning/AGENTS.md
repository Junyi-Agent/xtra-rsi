# reasoning：推理

## 目标

从观测解释「为什么是现在这样」，并提出可检验的改进假设，交给 decision。

## 输入

- `upstream`：observe 的输出。
- `history`：之前各轮记录，用来避免重复已经被否定的假设。

## 步骤

1. 归因：把每个主要问题对应到最可能的原因，引用支撑它的观测。
2. 区分事实与猜测：每个结论标注置信度（high / medium / low）和依据。
3. 提出候选改进：每条写清楚改什么（任务策略、提示词、工具、数据、harness 本身）、预期影响哪些指标、怎么验证。
4. 查 `history`：已经试过且失败的方向，除非有新证据，否则不再提出。
5. 给出反面检查：如果这个假设是错的，下一轮应该观测到什么。
6. 如果多数失败归因到同一个阶段本身做得不好（例如 observe 漏看了关键信息），把「为该阶段开 sub-RSI」作为一条候选，写出证据；有子节点交了摘要时，评估它的提案是否值得接受。

## 输出

一个 YAML 映射（写到 stdout 时可以用 ```yaml 代码块包住），至少包含：

- 透传后续阶段需要的状态字段。
- `hypotheses`：候选改进列表，每条含 `change`、`rationale`、`evidence`、`confidence`、`expected_effect`、`falsified_if`。

示例：

```yaml
x: 0.42
lr: 0.06
hypotheses:
  - change: 把 API 客户端超时从 5s 调到 15s
    rationale: 失败全部是超时，且集中在大响应
    evidence: ["observations[1]"]
    confidence: medium
    expected_effect: test_api 失败数 3 → 0
    falsified_if: 调整后仍有超时，或失败转为其他错误
  - change: 给大响应加分页
    rationale: 根因可能是响应过大
    evidence: ["observations[1]"]
    confidence: low
    expected_effect: 响应时间下降 50%
    falsified_if: 分页后响应时间不变
```

布尔值只写 `true` / `false`；含 `:`、`#` 或以特殊字符开头的字符串加引号。

## 工具与数据

- `scripts/`：分析工具（统计、对比两轮观测、错误聚类）。
- `data/`：已知问题与归因的知识库。

## 评估

`evals/cases.yaml`：把 reasoning 换成直通，decision 拿不到假设，最终指标应明显变差。

## 不要

- 不执行任何改动。
- 不在没有观测支撑时给 high 置信度。
- 不只给一个方向：至少比较两个候选（除非问题只有一个明显原因，此时写明理由）。
