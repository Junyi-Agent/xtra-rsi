# 0002 下游项目通过 Python 依赖 + `rsi sync` 跟随框架更新

状态：采纳

## 背景

其他项目会基于本框架做自己的 RSI agent。框架更新时（loop 代码、阶段规则），下游要能低成本跟上，同时保留项目自己的规则。

## 决策

- 框架作为 Python 包分发；`harness/` 通过 hatch `force-include` 打进 wheel 的 `xtra_rsi/_harness/`。代码更新随依赖升级生效。
- 下游项目不复制 harness。`rsi sync` 生成 `harness/<stage>/AGENTS.md` = 基础层 + 项目的 `AGENTS.local.md`，文件头带生成标记和框架版本。
- `rsi sync` 拒绝覆盖没有生成标记的 AGENTS.md，防止丢失手写内容。
- `rsi sync --check` 在需要同步时退出码 1，供下游放进 pre-commit / CI。
- 阶段实现通过 entry point `xtra_rsi.profiles` 注册，框架不需要知道下游。
- `scripts/smoke_downstream.py` 在 CI 里构建 wheel、装进临时项目跑一遍，保证这条路径不坏。

## 后果

- 考虑过 git submodule / copier 模板：submodule 无法合并项目自己的规则，模板更新需要处理三方合并冲突；分层生成更简单。
- 基础层 AGENTS.md 的改动会直接进入所有下游，需要向后兼容（见根 AGENTS.md 规则）。
- 下游的 `scripts/`、`evals/`、`data/` 只创建目录，不同步内容：这些是项目自己的。

## 验证
