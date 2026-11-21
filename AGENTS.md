# xtra-rsi

Recursive Self-Improvement agent 框架。loop：observe → reasoning → decision → action → feedback → observe。

## 布局

- `src/xtra_rsi/`：框架代码。`core.py`（Stage / Profile / run_loop）、`registry.py`（entry point `xtra_rsi.profiles`）、`ablation.py`、`command_stage.py`（外部命令 / LLM agent 当阶段）、`harness.py`（目录检查与 `rsi sync`）、`cli.py`（`rsi` 命令）、`profiles/toy.py`（示例 profile）。
- `harness/<stage>/`：每个阶段的 `AGENTS.md`、`scripts/`、`evals/`、`data/`。这是随包发布给下游的基础层。**改某阶段的工作方式，先读那个阶段的 AGENTS.md。**
- 根目录 `scripts/`、`docs/`、`evals/`、`data/`：仓库级（跨阶段）的工具、文档、评估、数据。
- `vendor/skills/`：git submodule，`.claude/skills/jy-*` 是指向它的软链接。

## 命令

```bash
uv sync                                  # 安装依赖（含 dev 组）
uv run pre-commit install                # 新 clone / 新 worktree 先装 hook（pre-commit + pre-push）
uv run pre-commit run --all-files        # 全量检查：ruff format、ruff check、basedpyright(all)、rsi check、pytest
uv run rsi eval [stage]                  # 阶段 ablation
uv run python scripts/smoke_downstream.py  # 构建 wheel 装进临时项目，验证下游接入
git submodule update --init              # 拉 vendor/skills；更新用 git submodule update --remote
```

## 规则

- 每个新功能在仓库目录之外建 worktree 开发：`git worktree add ../xtra-rsi-<feature> -b <feature> origin/main`；不在当前目录切换分支，不在 `main` 上提交，不 push 到 `main`。push feature 分支后开 PR，CI 通过后合并。
  - 新 worktree 先 `git submodule update --init && uv sync && uv run pre-commit install`。
  - 合并后清理：仓库含 submodule，`git worktree remove` 会拒绝，先确认 `git -C <worktree> status --short` 和 `git -C <worktree>/vendor/skills status --short` 都为空，再 `git worktree remove --force <worktree> && git branch -D <feature>`。有未提交内容时不要 `--force`。
- 不用 `--no-verify` / `SKIP=` / `core.hooksPath` 跳过 hook。
- 以上由 `.claude/hooks/git_guard.py`（PreToolUse hook）强制；`gh pr merge` 前会跑 `gh pr checks`，没全过就拒绝（ADR 0003、0005）。改这个 hook 前先跑 `uv run pytest tests/test_git_guard.py`：hook 出错会拦下所有 Bash 命令。
- push 时 pre-push hook 跑 `scripts/review_gate.py`：3 个 Claude 分别从正确性、阶段有效性、下游兼容与流程安全审查 `origin/main` 之后的改动，≥2 个同意才放行。被拒时按输出里的 blocking issues 改完再 push，不要绕过（ADR 0004）。需要本机装好并登录 `claude` CLI。
- 发布：改 `pyproject.toml` 的 `version`，合并后打 `vX.Y.Z` tag，`release.yml` 检查并发布（ADR 0005）。
- 不用 `# type: ignore` / `# noqa` 绕过检查，确有必要时用带规则名的 `# pyright: ignore[rule]` / `# noqa: RULE` 并在同一行写原因。
- 新增或修改阶段时，同步更新它的 `evals/cases.json`，并保证 `rsi eval` 通过：ablation 证明不了有效的阶段不应该存在。
- 改 `harness/*/AGENTS.md` 会通过 `rsi sync` 传到所有下游项目，改动要向后兼容；破坏性改动升 minor 版本并在 `docs/downstream.md` 写迁移说明。
- `Profile`、`StageContext`、`StageFn`、`rsi` 子命令和 `cases.json` 格式是对下游的公开接口，改它们按同样的方式处理。
- 流程或架构决策写 ADR（`docs/adr/`）。状态三种：提议 = 只写了文档；采纳 = 允许实现，可能已实现；已验证 = 人类测试了代码并确认。agent 最多改到「采纳」，「已验证」只由人类改。
- loop 运行产物（`runs/`）和 evals 结果（`evals/results/`）不提交。
