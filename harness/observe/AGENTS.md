# observe：观测

## 目标

把「现在的状态」变成一份结构化、可复现的观测，交给 reasoning。只描述事实，不下结论。

## 输入

- `upstream`：上一轮 feedback 的输出（第 0 轮是 profile 的 `seed`）。以节点身份运行时其中有 `_node`：本节点的约定（`contract`：目标、指标、预算、限制）、剩余预算和子节点的摘要。
- 环境：任务环境、被改进的 agent 自身（它的 harness、代码、历史运行记录 `runs/`）。

## 步骤

1. 读上一轮 feedback 的结论和它标出的「下一轮要重点看什么」。
2. 采集当前状态：任务指标、失败样例、日志、被改进对象的当前版本（commit / 配置哈希）。
3. 每个观测记录来源（文件路径、命令、时间），让别人能复现。
4. 去重和截断：同类失败只留代表样例和计数，长日志只留相关片段并注明截断。
5. 标出相对上一轮的变化（新出现、消失、数值变化）。

## 输出

一个 YAML 映射（写到 stdout 时可以用 ```yaml 代码块包住），至少包含：

- 透传上一轮 feedback 中后续阶段需要的状态字段（例如当前参数、学习率）。
- `observations`：观测列表，每条带 `source`。
- `version`：被改进对象当前版本标识。

示例：

```yaml
x: 0.42            # 透传的状态
lr: 0.06
version: 3f2a9c1
observations:
  - source: "runs/iter-0003/feedback.yaml"
    fact: 上一轮 score 从 -0.12 升到 -0.08
  - source: "uv run pytest -q tests/test_api.py"
    fact: 3 个用例失败，均为超时（另有 12 个同类，已去重）
changes:
  - 新出现：test_api 超时
```

布尔值只写 `true` / `false`；含 `:`、`#` 或以特殊字符开头的字符串加引号。

## 工具与数据

- `scripts/`：采集、截断、去重类工具。
- `data/`：观测要对照的基准数据（例如固定评测集）。

## 评估

`evals/cases.yaml`：把 observe 换成直通后比较最终指标。observe 有效，说明后续阶段确实依赖它采集的信息。

## 不要

- 不做推断、不提方案（那是 reasoning / decision 的事）。
- 不修改环境或被改进对象。
- 不丢弃上一轮 feedback 透传过来、后续阶段需要的状态。
