# 0001 五阶段 loop，阶段间只用 JSON payload 通信

状态：采纳

## 背景

RSI agent 需要能单独替换、单独评估每个环节，否则无法判断哪一部分带来了改进。

## 决策

- 固定五个阶段：observe → reasoning → decision → action → feedback，feedback 输出接回下一轮 observe。
- 阶段实现是 `StageFn(StageContext) -> JSON 对象`，阶段之间只传 JSON。
- 每个阶段在 `harness/<stage>/` 下有 `AGENTS.md`、`scripts/`、`evals/`、`data/`，由 `rsi check` 检查。
- 阶段有效性用 ablation 衡量：换成直通后指标下降不少于 `min_delta`，用例在 `harness/<stage>/evals/cases.json`，pytest 和 `rsi eval` 都会跑。

## 后果

- 阶段之间传什么完全靠约定（各阶段 AGENTS.md 的「输出」一节），框架不做 schema 校验。需要时再加。
- 直通 ablation 只能证明「去掉会变差」，不能证明「当前实现最优」；更细的对比（换成另一个实现）可以用 `Profile.with_stage` 自己写。

## 验证
