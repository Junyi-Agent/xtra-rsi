from __future__ import annotations

import pytest

from xtra_rsi.ablation import ablate, load_cases, run_ablation
from xtra_rsi.core import STAGE_ORDER, Stage, run_loop
from xtra_rsi.harness import base_root, stage_dirs
from xtra_rsi.profiles import toy


def test_toy_converges() -> None:
    assert toy.final_score(run_loop(toy.profile(), iterations=15)) > -1e-3


@pytest.mark.parametrize("stage", STAGE_ORDER, ids=[s.value for s in STAGE_ORDER])
def test_every_stage_case_passes(stage: Stage) -> None:
    """harness/<stage>/evals/cases.yaml 里的用例都应证明该阶段有效。"""
    sd = next(d for d in stage_dirs(base_root()) if d.stage == stage)
    cases = load_cases(sd.cases)
    assert cases
    for case in cases:
        result = run_ablation(stage, case)
        assert result.passed, result.to_dict()


def test_ablate_renames_and_replaces() -> None:
    p = ablate(toy.profile(), Stage.REASONING)
    assert p.name == "toy-no-reasoning"
    assert p.stages[Stage.OBSERVE] is toy.observe
    assert p.stages[Stage.REASONING] is not toy.reasoning
