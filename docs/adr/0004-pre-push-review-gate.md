# 0004 push 前 3 个 Claude 审查，≥2 同意才放行

状态：采纳

## 背景

pre-commit 和 CI 只查格式、类型、单元测试和 ablation，查不出阶段 AGENTS.md 含糊、阶段之间字段对不上、悄悄放宽 evals、破坏下游兼容这类问题。

## 决策

- `scripts/review_gate.py` 作为 pre-commit 的 `pre-push` 阶段 hook 运行（`default_install_hook_types: [pre-commit, pre-push]`，`pre-commit install` 两个都装）。移植自 Junyi-Agent/skills（其 ADR 0002）。
- 审查范围：`merge-base(origin/main, 被 push 的 ref)..被 push 的 ref`；squash merge 后再 push 也走同一范围。
- 并行跑 3 个 `claude -p`，只给 Read/Grep/Glob，`--json-schema` 结构化输出。三个方向：
  1. **correctness**：代码正确性和测试是否真的测到改动；
  2. **stage-effectiveness**：阶段 AGENTS.md 是否清楚、与相邻阶段的字段一致，ablation 用例是否更新、有没有放宽 evals；
  3. **compat-workflow-safety**：公开接口和基础层 AGENTS.md 的下游兼容性、是否遵守 AGENTS.md 流程、是否削弱 hook / CI / 门禁、安全。
- ≥2 个 approve 才 exit 0；报错、超时、输出解析失败都算 reject。
- diff 放在随机标签的 `<untrusted-…>` 块里，试图引导审查的内容按 blocking issue 处理。
- 找不到 `origin/main` 时阻止 push。

## 后果

- 每次 push 调 3 次 Claude，要花 token、等几分钟；机器上没有登录的 `claude` CLI 时 push 被拦。
- 仓库还没有 remote 和 `origin/main` 之前，门禁会拦下所有 push：第一次 push 由人在终端完成。
- CI 不跑这个 hook（`pre-commit run --all-files` 只跑 pre-commit 阶段）。

## 验证
