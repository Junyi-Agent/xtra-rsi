# 0006 阶段数据改用 YAML

状态：采纳

## 背景

0.1 里阶段输出、`CommandStage` 协议、运行产物、evals 用例和 CLI 输出都是 JSON。阶段 AGENTS.md 主要由人和 LLM agent 读写，项目所有者更希望用 YAML：多行文本可读、可以写注释。

## 决策

- 进程内阶段数据仍是 `Payload = dict[str, Value]`（`xtra_rsi.payload`）。所有进程外的表示改为 YAML：阶段 AGENTS.md 的输出格式、`CommandStage` 的 stdin/stdout、`rsi run --run-dir` 的 `iter-N/<stage>.yaml`、`harness/<stage>/evals/cases.yaml`、`rsi run` / `rsi eval` 的 stdout（以 `---` 分隔的文档流）。
- 统一经 `xtra_rsi.yamlio` 读写：基于 PyYAML 的 safe loader，但标量按 YAML 1.2 core schema 解析，避开 PyYAML 默认 YAML 1.1 的隐式转换——只有 `true`/`false` 是布尔值（`yes`/`no`/`on`/`off` 是字符串）；`010` 是 10、`1:30` 是字符串（1.1 读成 8 和 90）；`1e3` 是 float；日期是字符串；解析结果只允许 str/int/float/bool/None/list/键为 str 的 dict。写出时保留键顺序和中文，多行字符串用 `|` 块。
- `CommandStage` 接受被单个 ```` ```yaml ```` 代码块包住的输出。
- 外部工具规定的格式（Claude Code `settings.json`、`pyrightconfig.json`、`claude -p --output-format json`）不变。
- 破坏性改动，版本升到 0.2.0，迁移说明写在 docs/downstream.md。

## 后果

- 新增运行时依赖 `pyyaml`（类型检查用 `types-pyyaml`）。
- `rsi` 的输出不再能直接接 `jq`，可用 `yq`。
- 和 PyYAML 默认行为不同（例如 `1_000`、`0b11` 是字符串）。直接用 `yaml.safe_load` 读阶段数据会得到不同结果，所以规定一律走 `yamlio`。
- 含 `:`、`#` 或以特殊字符开头的字符串仍需加引号，阶段 AGENTS.md 里有提醒。

## 验证
