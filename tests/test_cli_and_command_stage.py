from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, cast

import pytest

from xtra_rsi.cli import main
from xtra_rsi.command_stage import CommandStage, CommandStageError
from xtra_rsi.core import Stage, StageContext

if TYPE_CHECKING:
    from pathlib import Path

ECHO = (
    "import json,sys; r=json.load(sys.stdin); "
    "print(json.dumps({'seen': r['stage'], 'n': len(r['instructions']), **r['upstream']}))"
)


def test_command_stage_round_trip(tmp_path: Path) -> None:
    md = tmp_path / "AGENTS.md"
    _ = md.write_text("abc")
    stage = CommandStage([sys.executable, "-c", ECHO], agents_md=md)
    out = stage(StageContext(stage=Stage.REASONING, iteration=0, upstream={"x": 1}))
    assert out == {"seen": "reasoning", "n": 3, "x": 1}


@pytest.mark.parametrize(
    ("code", "match"),
    [
        ("import sys; sys.exit(3)", "退出码 3"),
        ("print('no')", "不是 JSON"),
        ("print(1)", "JSON 对象"),
    ],
)
def test_command_stage_errors(code: str, match: str) -> None:
    stage = CommandStage([sys.executable, "-c", code])
    with pytest.raises(CommandStageError, match=match):
        _ = stage(StageContext(stage=Stage.ACTION, iteration=0, upstream={}))


def test_cli_run_outputs_jsonl(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--profile", "toy", "--iterations", "3"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["iteration"] for line in lines] == [0, 1, 2]


def test_cli_unknown_profile(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--profile", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_cli_eval_single_stage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "feedback"]) == 0
    result = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert result["stage"] == "feedback"
    assert result["passed"] is True


def test_cli_sync_check_exit_codes(tmp_path: Path) -> None:
    root = str(tmp_path / "harness")
    assert main(["sync", "--check", root]) == 1
    assert main(["sync", root]) == 0
    assert main(["sync", "--check", root]) == 0
