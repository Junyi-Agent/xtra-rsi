# action/evals

action 阶段的 ablation 实验：把该阶段换成直通（原样输出上游 payload），比较完整 loop 与去掉该阶段后的指标。

- `cases.json`：用例列表。字段：`name`、`profile`（entry point 名）、`metric`（profile.metrics 里的名字）、`iterations`、`min_delta`（完整 − 去掉后 至少要达到的差值，指标越大越好）。
- 运行：`uv run rsi eval action`，每条用例输出一行 JSON，有用例不达标时退出码 1。
- 结果目录 `results/` 不提交。
