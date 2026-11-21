from __future__ import annotations

import re
from typing import cast

import pytest

from xtra_rsi.harness import StageDir, base_root, stage_dirs
from xtra_rsi.yamlio import load_mapping

_YAML_BLOCK = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)
_STAGE_DIRS = stage_dirs(base_root())


@pytest.mark.parametrize("sd", _STAGE_DIRS, ids=[sd.stage.value for sd in _STAGE_DIRS])
def test_stage_agents_md_yaml_examples_parse(sd: StageDir) -> None:
    """agent 会照抄 AGENTS.md 的示例，示例必须能被 yamlio 解析。"""
    text = sd.agents_md.read_text(encoding="utf-8")
    blocks = [cast("str", m.group(1)) for m in _YAML_BLOCK.finditer(text)]
    assert blocks, f"{sd.agents_md} 没有 yaml 示例"
    for block in blocks:
        _ = load_mapping(block, where=str(sd.agents_md))
