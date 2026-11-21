from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from xtra_rsi import harness
from xtra_rsi.harness import LOCAL_MD, SyncError, base_root, check_layout, sync

if TYPE_CHECKING:
    from pathlib import Path


def test_base_layout_is_complete() -> None:
    assert check_layout(base_root()) == []


def test_check_reports_missing_and_unknown(tmp_path: Path) -> None:
    root = tmp_path / "harness"
    _ = shutil.copytree(base_root(), root)
    shutil.rmtree(root / "decision" / "data")
    (root / "planning").mkdir()
    problems = "\n".join(check_layout(root))
    assert "decision/data" in problems
    assert "planning" in problems


def test_sync_then_check_is_clean(tmp_path: Path) -> None:
    root = tmp_path / "harness"
    assert sync(root, check=True)
    _ = sync(root)
    assert check_layout(root) == []
    assert sync(root, check=True) == []


def test_local_rules_are_appended_and_base_updates_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "harness"
    _ = sync(root)
    _ = (root / "action" / LOCAL_MD).write_text("只允许改 configs/ 下的文件\n")
    changes = sync(root)
    assert [c.path.name for c in changes] == ["AGENTS.md"]
    text = (root / "action" / "AGENTS.md").read_text()
    assert text.index("# action") < text.index("只允许改 configs/")

    # 模拟框架升级：基础层内容变化后，check 应发现需要同步，sync 后带上新内容
    new_base = tmp_path / "base"
    _ = shutil.copytree(base_root(), new_base)
    _ = (new_base / "action" / "AGENTS.md").write_text("# action v2\n")
    monkeypatch.setattr(harness, "base_root", lambda: new_base)
    assert [c.path.parent.name for c in sync(root, check=True)] == ["action"]
    _ = sync(root)
    text = (root / "action" / "AGENTS.md").read_text()
    assert "# action v2" in text
    assert "只允许改 configs/" in text


def test_sync_refuses_hand_written_agents_md(tmp_path: Path) -> None:
    root = tmp_path / "harness"
    (root / "observe").mkdir(parents=True)
    _ = (root / "observe" / "AGENTS.md").write_text("手写的\n")
    with pytest.raises(SyncError, match=LOCAL_MD):
        _ = sync(root)


def test_sync_refuses_base_itself() -> None:
    with pytest.raises(SyncError, match="基础层本身"):
        _ = sync(base_root())
