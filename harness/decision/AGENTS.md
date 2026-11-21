# decision：决策

## 目标

从 reasoning 的候选里选出本轮要做的一件事，定好范围、预算和回滚方式，交给 action。

## 输入

- `upstream`：reasoning 的输出。
- `history`：之前各轮的决策和结果。

## 步骤

1. 按「预期收益 × 置信度 ÷ 成本与风险」给候选排序，写下打分依据。
2. 本轮只选一个改动（或一组不可分割的改动），保证 feedback 能把结果归因到它。
3. 定执行计划：具体改哪些文件/参数、改动上限（步长、文件数、token 预算）。
4. 定成功标准：哪个指标、阈值多少、在哪个评测集上。
5. 定回滚方式：失败时怎么恢复到本轮开始的状态。
6. 高风险改动（删数据、改评测集、改本阶段之外的 AGENTS.md 规则）标记为需要人工确认。
7. （以节点身份运行时）可以改为申请为某个阶段开子 RSI（sub-RSI），专门改进那个阶段。只在 reasoning 给出证据时申请：失败集中在该阶段、该阶段已有足够的运行记录可供回放、`_node.remaining_usd` 够划出预算、`_node.children` 里没有这个阶段未结束的子节点，`history` 里也没有「为该阶段开 sub-RSI 无效」的经验。申请要等人确认，本轮照常给出 `plan`。

## 输出

一个 YAML 映射（写到 stdout 时可以用 ```yaml 代码块包住），至少包含：

- 透传后续阶段需要的状态字段。
- `plan`：`change`、`steps`、`budget`、`success_criteria`、`rollback`、`needs_human`。
- `rejected`：没选的候选和理由。
- 可选 `spawn_request`：`stage`、`budget_usd`（美元，从本节点剩余预算里划出）、`rationale`（引用证据）。框架会创建待人确认的子节点，结果写在 `spawn_request_result` 里传给 action。

示例：

```yaml
x: 0.42
lr: 0.06
plan:
  change: 把 API 客户端超时从 5s 调到 15s
  steps:
    - 修改 configs/client.yaml 的 timeout
  budget: {files: 1, minutes: 5}
  success_criteria: tests/test_api.py 全部通过，其他测试不变差
  rollback: git checkout -- configs/client.yaml
  needs_human: false
rejected:
  - change: 给大响应加分页
    reason: 置信度低、改动大，先验证超时假设
```

申请开 sub-RSI 时额外加：

```yaml
spawn_request:
  stage: observe
  budget_usd: 2.0
  rationale: 最近 6 轮里 4 次回滚都因为 observe 没按商户分组，reasoning 拿不到漏判集中在新商户的证据
```

布尔值只写 `true` / `false`；含 `:`、`#` 或以特殊字符开头的字符串加引号。

## 工具与数据

- `scripts/`：打分、预算估计工具。
- `data/`：风险规则、预算上限配置。

## 评估

`evals/cases.yaml`：把 decision 换成直通，action 拿不到计划，最终指标应明显变差。

## 不要

- 不同时做多个互相独立的改动（会让 feedback 无法归因）。
- 不修改评测集或指标定义来「提高分数」。
- 不跳过回滚方案。
- 不在没有证据时申请 sub-RSI，也不为同一个阶段重复申请。
