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
    # myproj.agent：从 stdin 读 YAML（stage / iteration / instructions / upstream），
    # 调用 LLM agent 执行该阶段，把阶段输出（YAML 映射）打印到 stdout。
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

然后在每个 `harness/<stage>/evals/cases.yaml` 写自己的 ablation 用例：

```yaml
- name: pass-rate
  profile: myproj
  metric: pass_rate
  iterations: 10
  min_delta: 0.02
```

没有用例时 `rsi eval` 会失败：每个阶段都要有证据证明它有用。

## 项目特有规则

写在 `harness/<stage>/AGENTS.local.md`，`rsi sync` 会追加到生成的 `AGENTS.md` 末尾。不要直接改生成的 `AGENTS.md`：下次 sync 会拒绝覆盖手改过的文件并报错。

## 升级

```bash
uv lock --upgrade-package xtra-rsi   # 或改 tag 后 uv lock
uv sync
uv run rsi sync                      # 把新版基础层规则合进来
```

在 pre-commit 或 CI 里加 `uv run rsi sync --check`：框架升级后忘了 sync 时退出码 1。框架的代码（loop、ablation、CLI）随依赖升级直接生效，不需要额外步骤。

## 嵌套 RSI（0.3 起，ADR 0007）

```bash
uv run rsi node init --target fraud --metric pr_auc --budget 20   # 人创建根节点，预算 $20
uv run rsi run --profile fraud --node root --iterations 10        # 以根节点身份运行，按 _usage 记账
uv run rsi node list                                              # 看所有节点和预算
uv run rsi approve n0001                                          # 人确认开子节点（在终端里自己运行）
uv run rsi reject n0001 --reason "证据不足"
uv run rsi node close n0001 --status retired --reason "3 轮无收益"
```

- 你的 `CommandStage` 适配器要在阶段输出里带 `_usage`，否则花费不会被记账。默认单价表在 `xtra_rsi.pricing`，用 `--prices prices.yaml` 覆盖或补全（单位：美元/百万 token）。单价表里没有的模型或缓存类型会让 loop 停下，不会猜。
- `rsi/nodes/` 建议提交（节点表和花费流水是审计记录），其中的 `runs/` 已被 `.gitignore` 忽略。

## 版本兼容

公开接口：`Profile`、`Stage`、`StageContext`、`StageFn`、`run_loop`、`CommandStage` 的 stdin/stdout 协议、`rsi` 子命令、`cases.yaml` 格式、基础层 AGENTS.md 里约定的输出字段。0.x 期间 minor 版本可能有破坏性改动，会在这里写迁移说明。

## 迁移说明

### 0.2 → 0.3：嵌套 RSI（ADR 0007）

不破坏现有用法，只新增功能：

- 新命令 `rsi node init|list|show|close`、`rsi approve`、`rsi reject`，`rsi run --node`。
- 新的保留键：阶段输出的 `_usage`、decision 的可选 `spawn_request`、observe 输入里的 `_node`。如果你的阶段恰好用了这些键名，需要改名。
- `run_loop` 新增可选参数 `on_output`；新增 `StopLoop`、`Profile.with_seed`。

### 0.1 → 0.2：阶段数据改用 YAML（ADR 0006）

- `harness/<stage>/evals/cases.json` 改名为 `cases.yaml`，内容转成 YAML 列表（字段不变）。
- `CommandStage` 协议：stdin 从 JSON 改为 YAML；命令的 stdout 要输出 YAML 映射（可包在 ```yaml 代码块里）。用 JSON 输出的 agent 大多仍能解析（JSON 基本是 YAML 的子集），但建议改成 YAML。
- `rsi run --run-dir` 写 `iter-N/<stage>.yaml`；`rsi run` / `rsi eval` 的 stdout 从 JSON Lines 改为以 `---` 分隔的 YAML 文档流。
- 类型改名：`JSON` → `Value`，`JSONObject` → `Payload`（`from xtra_rsi import Payload, Value`）；`IterationRecord.to_json()` → `to_dict()`，`AblationResult.to_json()` → `to_dict()`。
- 升级后跑 `uv run rsi sync`，基础层 AGENTS.md 的输出格式说明会更新为 YAML。
