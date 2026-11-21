# harness

RSI loop 的五个阶段，一个阶段一个目录，按顺序循环：

```
observe → reasoning → decision → action → feedback ─┐
   ▲                                                │
   └────────────────────────────────────────────────┘
```

每个 `<stage>/` 下：

| 路径 | 内容 |
|---|---|
| `AGENTS.md` | 这个阶段要做的步骤、输入输出契约、禁止事项。执行该阶段的 agent 读它 |
| `scripts/` | 这个阶段专用的工具（可执行脚本，stdin/文件进、stdout 出、错误走 stderr） |
| `evals/` | 这个阶段的 ablation 实验。`cases.json` 由 `rsi eval <stage>` 读取 |
| `data/` | 这个阶段要用的数据（小文件提交，大文件只放获取脚本或说明） |

本目录是框架的「基础层」，随 Python 包一起发布。下游项目不要复制它，用 `rsi sync`
生成自己的 `harness/`，项目特有规则写在各阶段的 `AGENTS.local.md`。见
[`docs/downstream.md`](../docs/downstream.md)。

结构由 `rsi check` 检查（pre-commit 会跑）。
