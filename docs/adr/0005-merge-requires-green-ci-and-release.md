# 0005 合并要求 CI 通过；打 tag 发布

状态：提议

## 背景

下游通过 git 依赖锁定框架版本，`main` 上的每次合并和每个 tag 都可能被下游拉到，需要保证它们通过检查。

## 决策

- `main` 开启 GitHub 分支保护：必须走 PR、要求 `ci / checks` 通过、禁止 force push 和删除、对管理员同样生效。
- `git_guard.py` 拦截 `gh pr merge`：`gh pr checks` 全部通过才放行（hook 部分已实现）。
- 发布：改 `pyproject.toml` 的 `version`，合并后打 `vX.Y.Z` tag；`.github/workflows/release.yml` 重跑全部检查和下游冒烟测试，校验 tag 与版本一致，构建并创建 GitHub Release。
- 下游用 `git+https://github.com/Junyi-Agent/xtra-rsi@vX.Y.Z` 锁定版本，升级见 docs/downstream.md。

## 后果

- 状态是「提议」：仓库 `Junyi-Agent/xtra-rsi` 是私有的，GitHub 免费版开不了分支保护，目前「CI 通过才能合并」只靠 `git_guard.py` 拦 agent 的 `gh pr merge`，网页上合并不受限。仓库改为公开或升级套餐后再开服务端保护。
- 只在 GitHub Release 发 wheel，不发 PyPI。

## 验证
