# 0003 用 PreToolUse hook 强制 worktree 流程

状态：采纳

## 背景

AGENTS.md 规定新功能在仓库外的 worktree 开发、不在 `main` 上提交、不跳过 hook。只写在文档里，agent 可能不遵守。

## 决策

`.claude/hooks/git_guard.py` 注册为 Claude Code 的 PreToolUse(Bash) hook（`.claude/settings.json`），移植自 [Junyi-Agent/skills](https://github.com/Junyi-Agent/skills) 的同名 hook（其 ADR 0001），解析逻辑不变。拒绝：

- 在当前目录 `git switch` / `git checkout <branch>`；
- 在 `main` 上产生提交（commit、merge、cherry-pick、revert、am）、push 到 `main`；
- 用 `--no-verify` / `SKIP=` / `core.hooksPath` 跳过 git hook；
- `gh pr merge` 前 `gh pr checks` 没有全部通过（见 0005）。

hook 出错或读不了输入时拒绝（fail closed）。

## 后果

- 只约束 Claude Code 的 Bash 工具；人在终端里操作不受限。仓库第一次提交和 push 到 `main` 由人在终端完成。
- `export SKIP=...` 后单独 push、`eval`、脚本文件里的 git 命令拦不住，靠 AGENTS.md 约束。
- hook 本身出错会拦下所有 Bash 命令，改它时先跑 `uv run pytest tests/test_git_guard.py`。

## 验证
