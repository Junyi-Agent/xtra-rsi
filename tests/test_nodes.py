from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from xtra_rsi.cli import main
from xtra_rsi.core import STAGE_ORDER, Profile, Stage
from xtra_rsi.node_loop import NODE_KEY, SPAWN_RESULT_KEY, run_node
from xtra_rsi.nodes import (
    BudgetExceededError,
    NodeError,
    NodeStatus,
    NodeStore,
)
from xtra_rsi.pricing import DEFAULT_PRICES, ModelPrice, PricingError, load_prices, usage_cost
from xtra_rsi.yamlio import load_mapping

if TYPE_CHECKING:
    from pathlib import Path

    from xtra_rsi.core import StageContext
    from xtra_rsi.payload import Payload, Value


# ---- pricing ----


def test_cost_from_tokens() -> None:
    usage: dict[str, Value] = {
        "model": "claude-opus-5-5",
        "input_tokens": 1_000_000,
        "output_tokens": 500_000,
        "cache_read_input_tokens": 2_000_000,
    }
    assert usage_cost(usage, DEFAULT_PRICES) == pytest.approx(4.0 + 10.0 + 0.4)


def test_cost_usd_wins() -> None:
    assert usage_cost({"cost_usd": 0.25, "model": "nope"}, DEFAULT_PRICES) == 0.25


@pytest.mark.parametrize(
    ("usage", "match"),
    [
        ({"model": "unknown-model", "input_tokens": 1}, "单价表里没有"),
        ({"model": "claude-opus-5-5", "cache_creation_input_tokens": 5}, "cache_write"),
        ({"input_tokens": 5}, "cost_usd 或 model"),
        ({"model": "claude-opus-5-5", "input_tokens": -1}, "非负整数"),
        ({"cost_usd": -1}, "非负数"),
    ],
)
def test_cost_errors(usage: dict[str, Value], match: str) -> None:
    with pytest.raises(PricingError, match=match):
        _ = usage_cost(usage, DEFAULT_PRICES)


def test_load_prices_overrides(tmp_path: Path) -> None:
    path = tmp_path / "prices.yaml"
    _ = path.write_text(
        "claude-opus-5-5: {input: 1, output: 2, cache_write: 3}\nmine: {input: 0.5, output: 1}"
    )
    prices = load_prices(path)
    assert prices["claude-opus-5-5"] == ModelPrice(1.0, 2.0, cache_read=None, cache_write=3.0)
    assert prices["mine"].input == 0.5
    assert prices["claude-sonnet-5-5"] == DEFAULT_PRICES["claude-sonnet-5-5"]


# ---- node store ----


@pytest.fixture
def store(tmp_path: Path) -> NodeStore:
    s = NodeStore(tmp_path / "rsi" / "nodes")
    _ = s.init_root(target="fraud", metric="pr_auc", budget_usd=10.0)
    return s


def test_root_created_once(store: NodeStore) -> None:
    root = store.get("root")
    assert root.status is NodeStatus.RUNNING
    assert root.remaining_usd == 10.0
    assert store.contract("root").budget_usd == 10.0
    with pytest.raises(NodeError, match="已存在"):
        _ = store.init_root(target="x", metric="m", budget_usd=1.0)


def test_child_lifecycle_moves_budget(store: NodeStore) -> None:
    child = store.request_child("root", Stage.OBSERVE, budget_usd=3.0, rationale="漏判集中在新商户")
    assert child.status is NodeStatus.PENDING_APPROVAL
    assert child.target == "harness/observe"
    assert store.get("root").remaining_usd == 10.0  # 待确认不占预算

    _ = store.approve(child.id)
    assert store.get("root").remaining_usd == 7.0
    # 子节点有了自己的 harness，可以再往下开子节点
    assert (store.project_root / child.harness / "observe" / "AGENTS.md").is_file()

    _ = store.record_spend(child.id, 1.25, {"stage": "reasoning"})
    _ = store.close(child.id, NodeStatus.RETIRED, "3 轮无收益")
    root = store.get("root")
    assert root.allocated_usd == 0.0
    assert root.spent_usd == 1.25
    assert root.remaining_usd == pytest.approx(8.75)


def test_grandchild_is_just_another_child(store: NodeStore) -> None:
    child = store.request_child("root", Stage.REASONING, budget_usd=4.0, rationale="r")
    _ = store.approve(child.id)
    grand = store.request_child(child.id, Stage.OBSERVE, budget_usd=1.0, rationale="r")
    assert grand.target == f"{child.harness}/observe"
    _ = store.approve(grand.id)
    assert store.get(child.id).remaining_usd == 3.0
    with pytest.raises(NodeError, match="未结束的子节点"):
        _ = store.close(child.id, NodeStatus.DONE)
    _ = store.close(grand.id, NodeStatus.DONE)
    _ = store.close(child.id, NodeStatus.DONE)
    assert store.get("root").remaining_usd == 10.0


def test_request_child_checks(store: NodeStore) -> None:
    with pytest.raises(BudgetExceededError, match="不够"):
        _ = store.request_child("root", Stage.OBSERVE, budget_usd=11.0, rationale="r")
    _ = store.request_child("root", Stage.OBSERVE, budget_usd=1.0, rationale="r")
    with pytest.raises(NodeError, match="已有未结束的子节点"):
        _ = store.request_child("root", Stage.OBSERVE, budget_usd=1.0, rationale="r")


def test_approve_rechecks_budget(store: NodeStore) -> None:
    a = store.request_child("root", Stage.OBSERVE, budget_usd=6.0, rationale="r")
    b = store.request_child("root", Stage.ACTION, budget_usd=6.0, rationale="r")
    _ = store.approve(a.id)
    with pytest.raises(BudgetExceededError):
        _ = store.approve(b.id)
    _ = store.reject(b.id, "预算不够")
    assert store.get(b.id).status is NodeStatus.REJECTED
    with pytest.raises(NodeError, match="不是待确认"):
        _ = store.approve(b.id)


def test_overspend_is_recorded_then_raises(store: NodeStore) -> None:
    with pytest.raises(BudgetExceededError, match="超出预算"):
        _ = store.record_spend("root", 12.0, {})
    assert store.get("root").spent_usd == 12.0
    ledger = (store.dir / "root" / "ledger.yaml").read_text()
    assert "cost_usd: 12.0" in ledger


def test_view_is_local(store: NodeStore) -> None:
    child = store.request_child("root", Stage.OBSERVE, budget_usd=2.0, rationale="r")
    _ = store.approve(child.id)
    grand = store.request_child(child.id, Stage.DECISION, budget_usd=1.0, rationale="r")
    store.write_summary(child.id, {"proposal": "加一个按商户分组的观测"})

    root_view = store.view("root").to_dict()
    children = root_view["children"]
    assert isinstance(children, list)
    assert [c["id"] for c in children if isinstance(c, dict)] == [child.id]  # 看不到孙节点
    assert grand.id not in str(root_view)

    child_view = store.view(child.id).to_dict()
    assert "parent" not in child_view  # 只有约定，没有父节点本身
    contract = child_view["contract"]
    assert isinstance(contract, dict)
    assert contract["target"] == "harness/observe"


def test_bad_ids_rejected(store: NodeStore) -> None:
    with pytest.raises(NodeError, match="id 不合法"):
        _ = store.get("../root")


# ---- node loop ----


def _profile(outputs: dict[Stage, Payload]) -> Profile:
    def make(stage: Stage):  # noqa: ANN202
        def fn(ctx: StageContext) -> Payload:
            return {**ctx.upstream, **outputs.get(stage, {})}

        return fn

    return Profile(name="t", stages={s: make(s) for s in STAGE_ORDER})


USAGE: Payload = {"_usage": {"cost_usd": 0.5}}


def test_run_node_records_spend_and_strips_usage(store: NodeStore) -> None:
    profile = _profile({Stage.REASONING: USAGE})
    result = run_node(store, "root", profile, iterations=2, prices=DEFAULT_PRICES)
    assert result.stopped is None
    assert result.spent_usd == pytest.approx(1.0)
    assert store.get("root").spent_usd == pytest.approx(1.0)
    assert all("_usage" not in out for r in result.records for out in r.outputs.values())
    written = (
        store.dir / "root" / "runs" / "run-0001" / "iter-0000" / "reasoning.yaml"
    ).read_text()
    assert "_usage" not in written


def test_run_node_stops_when_budget_runs_out(store: NodeStore) -> None:
    profile = _profile({Stage.REASONING: {"_usage": {"cost_usd": 4.0}}})
    result = run_node(store, "root", profile, iterations=10, prices=DEFAULT_PRICES)
    assert result.stopped is not None
    assert "超出预算" in result.stopped
    assert len(result.records) == 2  # 第 3 轮 reasoning 超支，停在那里


def test_run_node_stops_on_unpriced_usage(store: NodeStore) -> None:
    profile = _profile({Stage.ACTION: {"_usage": {"model": "mystery", "input_tokens": 10}}})
    result = run_node(store, "root", profile, iterations=3, prices=DEFAULT_PRICES)
    assert result.stopped is not None
    assert "无法记账" in result.stopped
    assert result.records == []


def test_spawn_request_creates_pending_child(store: NodeStore) -> None:
    request: Payload = {
        "spawn_request": {"stage": "observe", "budget_usd": 2.0, "rationale": "失败集中在漏观测"}
    }
    result = run_node(
        store, "root", _profile({Stage.DECISION: request}), iterations=1, prices=DEFAULT_PRICES
    )
    spawn = result.records[0].outputs[Stage.DECISION][SPAWN_RESULT_KEY]
    assert isinstance(spawn, dict)
    assert spawn["status"] == "pending_approval"
    assert store.get(str(spawn["node"])).status is NodeStatus.PENDING_APPROVAL


@pytest.mark.parametrize(
    ("request_", "match"),
    [
        ({"stage": "planning", "budget_usd": 1, "rationale": "r"}, "不是已知阶段"),
        ({"stage": "observe", "budget_usd": "1", "rationale": "r"}, "应为数字"),
        ({"stage": "observe", "budget_usd": 1, "rationale": " "}, "证据"),
        ({"stage": "observe", "budget_usd": 99, "rationale": "r"}, "不够"),
        ("observe", "应为映射"),
    ],
)
def test_bad_spawn_requests_are_reported_not_raised(
    store: NodeStore, request_: Value, match: str
) -> None:
    profile = _profile({Stage.DECISION: {"spawn_request": request_}})
    result = run_node(store, "root", profile, iterations=1, prices=DEFAULT_PRICES)
    spawn = result.records[0].outputs[Stage.DECISION][SPAWN_RESULT_KEY]
    assert isinstance(spawn, dict)
    assert match in str(spawn["error"])
    assert len(store.all()) == 1


def test_observe_sees_fresh_node_view(store: NodeStore) -> None:
    seen: list[Value] = []

    def observe(ctx: StageContext) -> Payload:
        seen.append(ctx.upstream.get(NODE_KEY))
        return {"_usage": {"cost_usd": 1.0}}

    profile = _profile({}).with_stage(Stage.OBSERVE, observe)
    _ = run_node(store, "root", profile, iterations=2, prices=DEFAULT_PRICES)
    remaining = [v["remaining_usd"] for v in seen if isinstance(v, dict)]
    assert remaining == [10.0, 9.0]


def test_only_running_nodes_run(store: NodeStore) -> None:
    child = store.request_child("root", Stage.OBSERVE, budget_usd=1.0, rationale="r")
    with pytest.raises(NodeError, match="只有 running"):
        _ = run_node(store, child.id, _profile({}), iterations=1, prices=DEFAULT_PRICES)


# ---- CLI ----


def test_cli_node_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    nodes = str(tmp_path / "rsi" / "nodes")
    common = ["--nodes-dir", nodes]
    assert (
        main(["node", "init", "--target", "fraud", "--metric", "pr_auc", "--budget", "5", *common])
        == 0
    )
    store = NodeStore(tmp_path / "rsi" / "nodes")
    child = store.request_child("root", Stage.OBSERVE, budget_usd=2.0, rationale="r")
    assert main(["approve", child.id, *common]) == 0
    assert main(["node", "close", child.id, "--status", "done", *common]) == 0
    _ = capsys.readouterr()
    assert main(["node", "list", *common]) == 0
    docs = [load_mapping(d) for d in capsys.readouterr().out.split("---\n") if d.strip()]
    assert [(d["id"], d["status"]) for d in docs] == [("root", "running"), (child.id, "done")]
    assert main(["reject", "n9999", *common]) == 2  # 不存在


def test_cli_run_node_reports_stop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    nodes = str(tmp_path / "rsi" / "nodes")
    assert (
        main(
            [
                "node",
                "init",
                "--target",
                "t",
                "--metric",
                "m",
                "--budget",
                "1",
                "--nodes-dir",
                nodes,
            ]
        )
        == 0
    )
    assert (
        main(
            ["run", "--profile", "toy", "--node", "root", "--iterations", "2", "--nodes-dir", nodes]
        )
        == 0
    )
    assert "iteration: 1" in capsys.readouterr().out
