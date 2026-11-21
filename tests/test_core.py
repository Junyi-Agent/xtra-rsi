from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from xtra_rsi.core import STAGE_ORDER, Profile, ProfileError, Stage, StageOutputError, run_loop

if TYPE_CHECKING:
    from pathlib import Path

    from xtra_rsi.core import JSONObject, StageContext


def _tag(stage: Stage):  # noqa: ANN202
    def fn(ctx: StageContext) -> JSONObject:
        trail = ctx.upstream.get("trail", [])
        assert isinstance(trail, list)
        return {"trail": [*trail, f"{ctx.iteration}:{stage.value}"]}

    return fn


def _tracing_profile() -> Profile:
    return Profile(name="trace", stages={s: _tag(s) for s in STAGE_ORDER}, seed={"trail": []})


def test_stage_order_is_the_loop() -> None:
    assert [s.value for s in STAGE_ORDER] == [
        "observe",
        "reasoning",
        "decision",
        "action",
        "feedback",
    ]


def test_feedback_feeds_next_observe(tmp_path: Path) -> None:
    records = run_loop(_tracing_profile(), iterations=2, run_dir=tmp_path)
    assert records[-1].last["trail"] == [f"{i}:{s.value}" for i in range(2) for s in STAGE_ORDER]
    written = cast(
        "dict[str, list[str]]", json.loads((tmp_path / "iter-0001" / "observe.json").read_text())
    )
    assert written["trail"][-1] == "1:observe"
    assert written["trail"][-2] == "0:feedback"


def test_stop_flag_ends_loop() -> None:
    p = _tracing_profile().with_stage(Stage.FEEDBACK, lambda _ctx: {"stop": True})
    assert len(run_loop(p, iterations=5)) == 1


def test_missing_stage_rejected() -> None:
    with pytest.raises(ProfileError, match="decision"):
        _ = Profile(name="bad", stages={s: _tag(s) for s in STAGE_ORDER if s != Stage.DECISION})


def test_non_dict_output_rejected() -> None:
    p = _tracing_profile().with_stage(Stage.ACTION, lambda _ctx: [])  # pyright: ignore[reportArgumentType]
    with pytest.raises(StageOutputError, match="action"):
        _ = run_loop(p, iterations=1)


def test_negative_iterations_rejected() -> None:
    with pytest.raises(ValueError, match="iterations"):
        _ = run_loop(_tracing_profile(), iterations=-1)
