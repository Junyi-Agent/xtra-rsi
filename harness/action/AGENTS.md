# action：执行

## 目标

严格按 decision 的计划执行改动，并留下足够的记录让 feedback 评估、必要时回滚。

## 输入

- `upstream`：decision 的输出（`plan`）。

## 步骤

1. 记录执行前状态（版本、相关文件哈希），用于回滚。
2. 按 `plan.steps` 执行，不超出 `plan.budget`。
3. `plan.needs_human` 为真时停下，输出待确认内容，不执行。
4. 计划执行不下去时（前提不成立、超预算）停止并如实报告，不自行改计划。
5. 记录实际做了什么：改动的文件、命令、diff 摘要、耗时与花费。

## 输出

一个 YAML 映射（写到 stdout 时可以用 ```yaml 代码块包住），至少包含：

- 透传后续阶段需要的状态字段。
- `executed`：实际执行的步骤和结果。
- `before` / `after`：执行前后的版本标识。
- `status`：`done` / `aborted` / `waiting_human`，以及原因。

示例：

```yaml
x: 0.42
lr: 0.06
before: 3f2a9c1
after: 3f2a9c1+dirty
status: done
executed:
  - step: 修改 configs/client.yaml 的 timeout
    result: "timeout: 5 → 15"
```

布尔值只写 `true` / `false`；含 `:`、`#` 或以特殊字符开头的字符串加引号。

## 工具与数据

- `scripts/`：执行与回滚工具（应用 patch、改配置、创建快照）。
- `data/`：执行所需的模板、配置。

## 评估

`evals/cases.yaml`：把 action 换成直通，改动不会落地，最终指标应明显变差。

## 不要

- 不做计划外的改动，哪怕看起来是顺手的改进（记到输出里留给下一轮）。
- 不修改评测集、指标或 feedback 的判定逻辑。
- 不吞掉错误：失败就是 `aborted`。
