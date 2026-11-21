"""以节点身份运行 loop：记账、处理开子节点的申请、给阶段注入局部视野（ADR 0007）。

阶段输出里的保留键：

- ``_usage``（阶段 → 框架）：本阶段的 LLM 用量，一条映射或映射列表，格式见
  :func:`xtra_rsi.pricing.usage_cost`。框架记账后从输出里删掉，不传给下一阶段、不写盘。
- ``spawn_request``（decision → 框架）：申请为某个阶段开子节点，
  ``{stage, budget_usd, rationale}``。框架创建待人确认的子节点，把结果写进
  ``spawn_request_result`` 传给 action。
- ``_node``（框架 → observe）：节点的局部视野（:meth:`NodeView.to_dict`），在第 0 轮的
  seed 和每轮 feedback 的输出里注入，所以 observe 总能拿到最新的约定、预算和子节点摘要。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from xtra_rsi.core import IterationRecord, Stage, StopLoop, run_loop
from xtra_rsi.nodes import BudgetExceededError, NodeError, NodeStatus
from xtra_rsi.pricing import PricingError, usage_cost, usages_of

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from xtra_rsi.core import Profile, StageContext
    from xtra_rsi.nodes import NodeStore
    from xtra_rsi.payload import Payload, Value
    from xtra_rsi.pricing import ModelPrice

USAGE_KEY = "_usage"
NODE_KEY = "_node"
SPAWN_KEY = "spawn_request"
SPAWN_RESULT_KEY = "spawn_request_result"


@dataclass(frozen=True)
class NodeRunResult:
    """一次节点运行的结果。"""

    records: list[IterationRecord]
    stopped: str | None
    """提前停止的原因（例如预算用完）；正常跑完为 None。"""
    spent_usd: float
    """本次运行记账的金额。"""


def run_node(  # noqa: PLR0913 -- 后几个都是 keyword-only，打包成对象反而难用
    store: NodeStore,
    node_id: str,
    profile: Profile,
    *,
    iterations: int,
    prices: Mapping[str, ModelPrice],
    run_dir: Path | None = None,
) -> NodeRunResult:
    """以 ``node_id`` 的身份运行 ``profile``。节点必须处于 running。"""
    node = store.get(node_id)
    if node.status is not NodeStatus.RUNNING:
        msg = f"{node_id} 状态为 {node.status}，只有 running 的节点能运行"
        raise NodeError(msg)
    if run_dir is None:
        runs = store.dir / node_id / "runs"
        n = len([p for p in runs.iterdir() if p.is_dir()]) if runs.is_dir() else 0
        run_dir = runs / f"run-{n + 1:04d}"
    spent_before = node.spent_usd
    stopped: list[str] = []

    def on_output(ctx: StageContext, out: Payload) -> Payload:
        out = dict(out)
        usage = out.pop(USAGE_KEY, None)
        # 先记账：后面的预算检查和 _node 视野都要看到本阶段的花费
        if usage is not None:
            try:
                for item in usages_of(usage):
                    cost = usage_cost(item, prices)
                    _ = store.record_spend(
                        node_id, cost, {"stage": ctx.stage.value, "iteration": ctx.iteration}
                    )
            except BudgetExceededError as e:
                stopped.append(str(e))
                raise StopLoop(str(e), out) from e
            except PricingError as e:
                # 记不了账就不能继续花钱
                stopped.append(f"无法记账: {e}")
                raise StopLoop(str(e), out) from e
        if ctx.stage is Stage.DECISION and SPAWN_KEY in out:
            out[SPAWN_RESULT_KEY] = _handle_spawn(store, node_id, out[SPAWN_KEY])
        if ctx.stage is Stage.FEEDBACK:
            out[NODE_KEY] = store.view(node_id).to_dict()
        return out

    seeded = profile.with_seed({**profile.seed, NODE_KEY: store.view(node_id).to_dict()})
    # status.yaml 里的 goal：节点的约定优先，profile 自己的 goal 补充
    seeded = seeded.with_goal(
        {
            **profile.goal,
            "node": node_id,
            "target": node.target,
            "metric": node.metric,
            "budget_usd": node.budget_usd,
        }
    )
    records = run_loop(seeded, iterations=iterations, run_dir=run_dir, on_output=on_output)
    spent = store.get(node_id).spent_usd - spent_before
    return NodeRunResult(records=records, stopped=stopped[0] if stopped else None, spent_usd=spent)


def _handle_spawn(store: NodeStore, node_id: str, request: Value) -> Payload:
    if not isinstance(request, dict):
        return {"error": "spawn_request 应为映射 {stage, budget_usd, rationale}"}
    stage, budget, rationale = (
        request.get("stage"),
        request.get("budget_usd"),
        request.get("rationale"),
    )
    if not isinstance(stage, str) or stage not in {s.value for s in Stage}:
        return {"error": f"spawn_request.stage 不是已知阶段: {stage!r}"}
    if isinstance(budget, bool) or not isinstance(budget, int | float):
        return {"error": f"spawn_request.budget_usd 应为数字: {budget!r}"}
    if not isinstance(rationale, str) or not rationale.strip():
        return {"error": "spawn_request.rationale 必须写明证据"}
    try:
        child = store.request_child(
            node_id, Stage(stage), budget_usd=float(budget), rationale=rationale
        )
    except NodeError as e:
        return {"error": str(e)}
    return {
        "node": child.id,
        "status": child.status.value,
        "note": "等人运行 rsi approve / rsi reject",
    }
