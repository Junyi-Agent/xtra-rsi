# feedback：反馈

## 目标

评估本轮改动的效果，决定保留还是回滚，并把经验写回，作为下一轮 observe 的输入。这是「自我改进」落地的阶段。

## 输入

- `upstream`：action 的输出。
- `history`：之前各轮记录。

## 步骤

1. 按 decision 定的 `success_criteria` 在同一评测集上测量，和执行前对比。
2. 判断：达标保留；不达标或 `status` 不是 `done` 时回滚到 `before`。
3. 归因：结果是否符合 reasoning 的预期；不符合时写明哪个假设被否定（`falsified_if` 是否出现）。
4. 调整自身：根据结果更新下一轮的参数（例如步长、预算、探索比例），写明调整理由。
5. 给下一轮 observe 留下「重点看什么」。
6. 达到总体目标或预算用尽时输出 `stop: true`。

## 输出

一个 YAML 映射（写到 stdout 时可以用 ```yaml 代码块包住），至少包含：

- 下一轮需要的状态字段（这是下一轮 observe 的 `upstream`）。
- `verdict`：`keep` / `rollback`，以及指标前后对比。
- `lessons`：本轮得到的经验，供 reasoning 查 `history`。
- `next_focus`：下一轮 observe 的重点。
- `stop`：是否结束 loop。

示例：

```yaml
x: 0.45
lr: 0.072
verdict:
  decision: keep
  metric: test_api 失败数
  before: 3
  after: 0
lessons:
  - 超时假设成立；大响应仍偏慢，分页可作为后续方向
next_focus: 关注整体测试耗时是否上升
stop: false
```

布尔值只写 `true` / `false`；含 `:`、`#` 或以特殊字符开头的字符串加引号。

## 工具与数据

- `scripts/`：评测、对比、回滚工具。
- `data/`：固定评测集和历史指标。

## 评估

`evals/cases.yaml`：把 feedback 换成直通，loop 失去自我调整能力，最终指标应明显变差。

## 不要

- 不换评测集或指标来让结果好看。
- 不只报好消息：没达标就是没达标。
- 不在没有测量的情况下判 `keep`。
