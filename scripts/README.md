# scripts

仓库级工具（跨阶段）。阶段专用工具放 `harness/<stage>/scripts/`。

| 脚本 | 用途 |
|---|---|
| `smoke_downstream.py` | 构建 wheel，装进临时 uv 项目，跑 `rsi sync` / `check` / `eval`，验证下游接入路径 |
| `review_gate.py` | pre-push 审查门禁：3 个 Claude 不同方向审查，≥2 同意才放行 |
