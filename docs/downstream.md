# 下游项目接入与升级

## 接入

```bash
uv add "xtra-rsi @ git+https://github.com/Junyi-Agent/xtra-rsi@v0.1.0"
uv run rsi sync          # 生成 harness/<stage>/{AGENTS.md, AGENTS.local.md, scripts/, evals/, data/}
```

注册自己的 profile（`pyproject.toml`）：

```toml
[project.entry-points."xtra_rsi.profiles"]
myproj = "myproj.rsi:profile"
```

```python
# myproj/rsi.py
from xtra_rsi import Profile, Stage
from xtra_rsi.command_stage import CommandStage
from pathlib import Path

def profile() -> Profile:
    # myproj.agent：从 stdin 读 {"stage","iteration","instructions","upstream"}，
    # 调用 LLM agent 执行该阶段，把阶段输出 JSON 对象打印到 stdout。
    def agent(s: Stage) -> CommandStage:
        return CommandStage(
            ["python", "-m", "myproj.agent"],
            agents_md=Path("harness") / s.value / "AGENTS.md",
        )

    return Profile(
        name="myproj",
        stages={s: agent(s) for s in Stage},
        seed={},
        metrics={"pass_rate": my_pass_rate},
    )
```

然后在每个 `harness/<stage>/evals/cases.json` 写自己的 ablation 用例。没有用例时 `rsi eval` 会失败：每个阶段都要有证据证明它有用。

## 项目特有规则

写在 `harness/<stage>/AGENTS.local.md`，`rsi sync` 会追加到生成的 `AGENTS.md` 末尾。不要直接改生成的 `AGENTS.md`：下次 sync 会拒绝覆盖手改过的文件并报错。

## 升级

```bash
uv lock --upgrade-package xtra-rsi   # 或改 tag 后 uv lock
uv sync
uv run rsi sync                      # 把新版基础层规则合进来
```

在 pre-commit 或 CI 里加 `uv run rsi sync --check`：框架升级后忘了 sync 时退出码 1。框架的代码（loop、ablation、CLI）随依赖升级直接生效，不需要额外步骤。

## 版本兼容

公开接口：`Profile`、`Stage`、`StageContext`、`StageFn`、`run_loop`、`CommandStage` 的 stdin/stdout 协议、`rsi` 子命令、`cases.json` 格式、基础层 AGENTS.md 里约定的输出字段。0.x 期间 minor 版本可能有破坏性改动，会在这里写迁移说明。

## 迁移说明

（暂无）
