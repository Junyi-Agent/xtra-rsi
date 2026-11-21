# xtra-rsi

Recursive Self-Improvement（RSI）agent 框架。一次迭代按五个阶段运行，feedback 的输出接回下一轮 observe：

```
observe → reasoning → decision → action → feedback ─┐
   ▲                                                │
   └────────────────────────────────────────────────┘
```

每个阶段在 [`harness/<stage>/`](harness) 下有：`AGENTS.md`（这个阶段要做什么）、`scripts/`（工具）、`evals/`（ablation 实验，证明这个阶段有效）、`data/`（数据）。

## 快速开始

```bash
uv sync
uv run pre-commit install
uv run rsi run --profile toy --iterations 10   # 跑示例 loop，每轮输出一个 YAML 文档
uv run rsi eval                                # 对五个阶段跑 ablation
```

## 目录

| 路径 | 内容 |
|---|---|
| `src/xtra_rsi/` | 框架代码：loop、profile 注册、ablation、外部命令阶段、harness 同步、CLI |
| `harness/<stage>/` | 五个阶段的基础层（随包发布给下游） |
| `scripts/` | 仓库级工具 |
| `docs/` | 架构说明、下游接入、ADR |
| `evals/` | 跨阶段（整条 loop）的评估 |
| `data/` | 跨阶段共享的数据 |
| `tests/` | 单元测试 |
| `vendor/skills/` | [Junyi-Agent/skills](https://github.com/Junyi-Agent/skills)（git submodule），软链接到 `.claude/skills/` |

## 给别的项目用

下游项目把 xtra-rsi 作为依赖，用 entry point 注册自己的阶段实现，用 `rsi sync` 生成自己的 `harness/`。框架升级后 `uv lock --upgrade-package xtra-rsi && uv run rsi sync`，基础层的阶段规则就跟着更新。详见 [`docs/downstream.md`](docs/downstream.md)。

## 文档

- [`docs/architecture.md`](docs/architecture.md)：核心概念和数据流
- [`docs/downstream.md`](docs/downstream.md)：下游项目接入与升级
- [`docs/adr/`](docs/adr)：架构决策
- [`AGENTS.md`](AGENTS.md)：在本仓库工作的 agent 须知
