from __future__ import annotations

import http.client
import json
import socket
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from xtra_rsi import status as status_mod
from xtra_rsi.core import STAGE_ORDER, Profile, Stage, StopLoop, run_loop
from xtra_rsi.nodes import NodeStore
from xtra_rsi.profiles.toy import profile as toy
from xtra_rsi.status import STATUS_FILE, StatusWriter
from xtra_rsi.viz import data
from xtra_rsi.viz.server import serve
from xtra_rsi.yamlio import dump, load_mapping

if TYPE_CHECKING:
    from pathlib import Path

    from xtra_rsi.core import StageContext
    from xtra_rsi.payload import Payload


def _status(run_dir: Path) -> Payload:
    return load_mapping((run_dir / STATUS_FILE).read_text())


# ---------- status.yaml ----------


def test_status_written_with_goal_and_done(tmp_path: Path) -> None:
    _ = run_loop(toy(), iterations=3, run_dir=tmp_path)
    s = _status(tmp_path)
    assert s["state"] == "done"
    assert s["iteration"] == 2
    assert s["stage"] == "feedback"
    assert s["iterations"] == 3
    goal = cast("dict[str, object]", s["goal"])
    assert "target" in goal
    assert "metric" in goal


def test_status_tracks_current_stage(tmp_path: Path) -> None:
    seen: list[tuple[object, object]] = []

    def spy(ctx: StageContext) -> Payload:
        s = _status(tmp_path)
        seen.append((s["iteration"], s["stage"]))
        assert s["state"] == "running"
        return {"i": ctx.iteration}

    p = Profile(name="spy", stages=dict.fromkeys(STAGE_ORDER, spy))
    _ = run_loop(p, iterations=2, run_dir=tmp_path)
    assert seen == [(i, s.value) for i in range(2) for s in STAGE_ORDER]


def test_status_records_failure(tmp_path: Path) -> None:
    def boom(_ctx: StageContext) -> Payload:
        msg = "坏了"
        raise RuntimeError(msg)

    p = toy().with_stage(Stage.ACTION, boom)
    with pytest.raises(RuntimeError):
        _ = run_loop(p, iterations=2, run_dir=tmp_path)
    s = _status(tmp_path)
    assert s["state"] == "failed"
    assert s["stage"] == "action"
    assert "坏了" in str(s["error"])


def test_status_records_stop_reasons(tmp_path: Path) -> None:
    p = toy().with_stage(Stage.FEEDBACK, lambda _ctx: {"stop": True, "reason": "达标"})
    _ = run_loop(p, iterations=5, run_dir=tmp_path / "a")
    assert _status(tmp_path / "a")["state"] == "done"
    assert _status(tmp_path / "a")["reason"] == "达标"

    def hook(ctx: StageContext, out: Payload) -> Payload:
        if ctx.stage is Stage.DECISION:
            msg = "预算用完"
            raise StopLoop(msg)
        return out

    _ = run_loop(toy(), iterations=5, run_dir=tmp_path / "b", on_output=hook)
    s = _status(tmp_path / "b")
    assert s["state"] == "stopped"
    assert s["reason"] == "预算用完"


def test_heartbeat_thread_refreshes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_mod, "HEARTBEAT_S", 0.05)
    beat = threading.Event()
    with StatusWriter(tmp_path, profile="p", goal={}, iterations=1) as w:
        old = (tmp_path / STATUS_FILE).stat().st_mtime_ns
        for _ in range(100):
            if (tmp_path / STATUS_FILE).stat().st_mtime_ns != old:
                beat.set()
                break
            _ = threading.Event().wait(0.02)
        w.finish(status_mod.RunState.DONE)
    assert beat.is_set()


def test_no_run_dir_no_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _ = run_loop(toy(), iterations=1)
    assert not list(tmp_path.rglob(STATUS_FILE))


def test_node_run_goal_comes_from_node(tmp_path: Path) -> None:
    store = NodeStore(tmp_path / "nodes", project_root=tmp_path)
    _ = store.init_root(target="fraud", metric="pr_auc", budget_usd=10.0)
    from xtra_rsi.node_loop import run_node  # noqa: PLC0415 -- 只有这个测试用

    _ = run_node(store, "root", toy(), iterations=1, prices={})
    runs = list((tmp_path / "nodes" / "root" / "runs").iterdir())
    goal = cast("dict[str, object]", _status(runs[0])["goal"])
    assert goal["target"] == "fraud"
    assert goal["node"] == "root"


# ---------- 数据层 ----------


def _fake_status(path: Path, **fields: object) -> None:
    path.mkdir(parents=True, exist_ok=True)
    base: dict[str, object] = {
        "profile": "p",
        "goal": {"target": "t", "metric": "m"},
        "state": "running",
        "iteration": 0,
        "iterations": 3,
        "stage": "action",
        "pid": 1,
        "host": "elsewhere",
        "heartbeat_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "heartbeat_s": 10.0,
    }
    base.update(fields)
    _ = (path / STATUS_FILE).write_text(dump(cast("Payload", base)))


def test_liveness() -> None:
    now = datetime.now(UTC)
    fresh = {"state": "running", "host": "x", "heartbeat_at": now.isoformat(), "heartbeat_s": 10.0}
    assert data.liveness(cast("Payload", fresh), now)[0] == "running"
    stale = {**fresh, "heartbeat_at": (now - timedelta(minutes=5)).isoformat()}
    assert data.liveness(cast("Payload", stale), now)[0] == "lost"
    assert data.liveness({"state": "done"}, now)[0] == "done"
    dead = {**fresh, "host": socket.gethostname(), "pid": 2**22 + 12345}
    assert data.liveness(cast("Payload", dead), now)[0] == "lost"


def test_discover_finds_loops_and_nodes(tmp_path: Path) -> None:
    _ = run_loop(toy(), iterations=2, run_dir=tmp_path / "exp" / "a")
    _fake_status(tmp_path / "exp" / "b")
    legacy = tmp_path / "old" / "stages" / "iter-0000"
    legacy.mkdir(parents=True)
    _ = (legacy / "observe.yaml").write_text("x: 1\n")
    # workspace 下的东西不扫
    _fake_status(tmp_path / "exp" / "workspace" / "c")
    store = NodeStore(tmp_path / "rsi" / "nodes", project_root=tmp_path)
    _ = store.init_root(target="fraud", metric="auc", budget_usd=5.0)
    from xtra_rsi.node_loop import run_node  # noqa: PLC0415 -- 只有这个测试用

    _ = run_node(store, "root", toy(), iterations=1, prices={})

    found = data.discover([tmp_path])
    names = sorted(p.name for p in found.loops.values())
    assert names == ["a", "b", "run-0001", "stages"]
    assert len(found.nodes) == 1
    snap = data.snapshot(found)
    loops = {cast("str", lp["title"]): lp for lp in cast("list[Payload]", snap["loops"])}
    assert loops["a"]["state"] == "done"
    assert loops["b"]["state"] == "running"
    assert loops["stages"]["state"] == "unknown"
    assert loops["stages"]["stage"] == "observe"
    assert loops["run-0001"]["node"] is not None
    node = cast("list[Payload]", snap["nodes"])[0]
    assert node["target"] == "fraud"
    assert node["loops"] == [loops["run-0001"]["id"]]


def test_loop_detail(tmp_path: Path) -> None:
    _ = run_loop(toy(), iterations=2, run_dir=tmp_path / "a")
    lid = data.loop_id(tmp_path / "a")
    d = data.loop_detail(lid, tmp_path / "a", full=False)
    its = cast("list[Payload]", d["iterations"])
    assert [i["iteration"] for i in its] == [0, 1]
    stages = cast("dict[str, Payload]", its[0]["stages"])
    assert list(stages) == [s.value for s in STAGE_ORDER]
    assert "yaml" not in stages["observe"]
    full = data.loop_detail(lid, tmp_path / "a", full=True)
    stages = cast("dict[str, Payload]", cast("list[Payload]", full["iterations"])[0]["stages"])
    assert "x:" in cast("str", stages["observe"]["yaml"])


def test_plugin_describe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def describe(path: Path, *, detail: bool) -> Payload | None:
        if path.name != "mine":
            return None
        return {
            "title": "我的",
            "goal": {"target": "kernel"},
            "kpis": [{"label": "a", "value": 1}],
            "stage_notes": {"0": {"observe": "看了看"}},
            "inner": {"0": [{"kind": "tool", "text": "ls"}]} if detail else {},
        }

    monkeypatch.setattr(data, "_plugins", lambda: [describe])
    _ = run_loop(toy(), iterations=1, run_dir=tmp_path / "mine")
    _ = run_loop(toy(), iterations=1, run_dir=tmp_path / "other")
    snap = data.snapshot(data.discover([tmp_path]))
    loops = {
        cast("str", lp["path"]).rsplit("/", 1)[-1]: lp
        for lp in cast("list[Payload]", snap["loops"])
    }
    assert loops["mine"]["title"] == "我的"
    assert cast("dict[str, object]", loops["mine"]["goal"])["target"] == "kernel"
    assert loops["other"]["title"] == "other"
    d = data.loop_detail("x", tmp_path / "mine", full=True)
    it0 = cast("list[Payload]", d["iterations"])[0]
    assert cast("dict[str, Payload]", it0["stages"])["observe"]["note"] == "看了看"
    assert cast("dict[str, object]", d["inner"])["0"]


def test_plugin_error_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def bad(path: Path, *, detail: bool) -> Payload | None:
        msg = f"{path.name} {detail}"
        raise ValueError(msg)

    monkeypatch.setattr(data, "_plugins", lambda: [bad])
    _ = run_loop(toy(), iterations=1, run_dir=tmp_path / "a")
    snap = data.snapshot(data.discover([tmp_path]))
    assert "ValueError" in str(cast("list[Payload]", snap["loops"])[0]["plugin_error"])


# ---------- HTTP ----------


def _get(port: int, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def test_server_endpoints(tmp_path: Path) -> None:
    _ = run_loop(toy(), iterations=2, run_dir=tmp_path / "a")
    server = serve([tmp_path], port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        port = server.server_address[1]
        code, body = _get(port, "/")
        assert code == 200
        assert b"<title>" in body
        code, body = _get(port, "/api/loops")
        snap = cast("dict[str, list[dict[str, str]]]", json.loads(body))
        lid = snap["loops"][0]["id"]
        code, body = _get(port, f"/api/loop?id={lid}&full=1")
        assert code == 200
        assert "yaml" in body.decode()
        # 只认扫描到的 id，不接受路径
        code, _body = _get(port, "/api/loop?id=../../etc/passwd")
        assert code == 404
    finally:
        server.shutdown()
        server.server_close()
