from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from xtra_rsi.cli import main
from xtra_rsi.command_stage import CommandStage, CommandStageError, strip_fence
from xtra_rsi.core import Stage, StageContext
from xtra_rsi.yamlio import load_mapping

if TYPE_CHECKING:
    from pathlib import Path

    from xtra_rsi.payload import Payload

ECHO = (
    "import sys, yaml; r = yaml.safe_load(sys.stdin); "
    "print(yaml.safe_dump({'seen': r['stage'], 'n': len(r['instructions']), **r['upstream']}))"
)


def test_command_stage_round_trip(tmp_path: Path) -> None:
    md = tmp_path / "AGENTS.md"
    _ = md.write_text("第一行\n第二行\n")
    stage = CommandStage([sys.executable, "-c", ECHO], agents_md=md)
    out = stage(StageContext(stage=Stage.REASONING, iteration=0, upstream={"x": 1}))
    assert out == {"seen": "reasoning", "n": 8, "x": 1}


def test_command_stage_sends_block_style_instructions(tmp_path: Path) -> None:
    md = tmp_path / "AGENTS.md"
    _ = md.write_text("# observe\n\n1. 读输入\n")
    dump_stdin = "import sys; print('raw: |'); [print('  ' + l, end='') for l in sys.stdin]"
    out = CommandStage([sys.executable, "-c", dump_stdin], agents_md=md)(
        StageContext(stage=Stage.OBSERVE, iteration=0, upstream={})
    )
    raw = out["raw"]
    assert isinstance(raw, str)
    assert "instructions: |" in raw
    assert "  1. 读输入" in raw


def test_command_stage_accepts_fenced_output() -> None:
    code = "print('```yaml\\nplan: ok\\n```')"
    out = CommandStage([sys.executable, "-c", code])(
        StageContext(stage=Stage.DECISION, iteration=0, upstream={})
    )
    assert out == {"plan": "ok"}


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("```yaml\na: 1\n```\n", "a: 1"),
        ("```\na: 1\n```", "a: 1"),
        ("a: 1\n", "a: 1\n"),
        ("前言\n```yaml\na: 1\n```", "前言\n```yaml\na: 1\n```"),
    ],
)
def test_strip_fence(text: str, want: str) -> None:
    assert strip_fence(text) == want


@pytest.mark.parametrize(
    ("code", "match"),
    [
        ("import sys; sys.exit(3)", "退出码 3"),
        ("print('a: [1')", "不是合法的 YAML"),
        ("print('- 1')", "顶层应为映射"),
    ],
)
def test_command_stage_errors(code: str, match: str) -> None:
    stage = CommandStage([sys.executable, "-c", code])
    with pytest.raises(CommandStageError, match=match):
        _ = stage(StageContext(stage=Stage.ACTION, iteration=0, upstream={}))


def _docs(out: str) -> list[Payload]:
    return [load_mapping(doc) for doc in out.split("---\n")[1:]]


def test_cli_run_outputs_yaml_stream(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--profile", "toy", "--iterations", "3"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("---\n")
    assert [d["iteration"] for d in _docs(out)] == [0, 1, 2]


def test_cli_unknown_profile(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--profile", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_cli_eval_single_stage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "feedback"]) == 0
    result = load_mapping(capsys.readouterr().out)
    assert result["stage"] == "feedback"
    assert result["passed"] is True


def test_cli_run_dir_writes_yaml(tmp_path: Path) -> None:
    assert main(["run", "--profile", "toy", "--iterations", "1", "--run-dir", str(tmp_path)]) == 0
    files = sorted(p.name for p in (tmp_path / "iter-0000").iterdir())
    assert files == [f"{s.value}.yaml" for s in sorted(Stage, key=lambda s: s.value)]


def test_cli_sync_check_exit_codes(tmp_path: Path) -> None:
    root = str(tmp_path / "harness")
    assert main(["sync", "--check", root]) == 1
    assert main(["sync", root]) == 0
    assert main(["sync", "--check", root]) == 0
