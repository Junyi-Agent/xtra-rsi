"""``rsi viz`` 的数据层：在目录里找 loop 和节点表，读成前端要的形状（ADR 0008）。

一个 **loop 目录** 是 ``run_loop(run_dir=...)`` 的输出：含 ``status.yaml`` 或
``iter-<n>/<stage>.yaml``。一个 **节点表** 是含 ``<id>/node.yaml`` 的目录（ADR 0007）。

下游通过 entry point ``xtra_rsi.viz`` 给 loop 补充业务信息（标题、指标、每个阶段的摘要、
被测 agent 的内部过程）::

    [project.entry-points."xtra_rsi.viz"]
    myproj = "myproj.viz:describe"

``describe(loop_dir: Path, *, detail: bool) -> Payload | None``：不认识这个 loop 时返回 None。
返回的映射里可以有（都可选）：

- ``title`` / ``subtitle`` / ``group``：卡片标题、副标题、分组
- ``goal``：并入 status.yaml 的 goal（约定键 ``target`` / ``metric``）
- ``kpis``：``[{label, value, target?, unit?}]``；value 是数字且有 target 时画进度条
- ``series``：``[{label, values: [number|null], target?, x?, x_label?, y_label?}]``，按迭代的曲线。
  ``x`` 是和 values 等长的迭代号（从 0 开始，显示为第 x+1 轮），不给时第 i 个值算第 i 轮；
  详情里的大图 x 轴是轮次（``x_label`` 默认「轮次」），y 轴标题是 ``y_label``（默认 label）
- ``stage_notes``：``{"<iteration>": {"<stage>": "一行摘要"}}``；还没有输出文件的阶段（正在跑）
  也可以给，显示为进展
- ``sections``：``[{key, title, note?, collapsed?}]``，详情里把阶段输出按顶层键拆成带标题的块，
  按这个顺序显示（例如把「LLM 的原样输出」和「框架记录」「透传状态」分开）；没列出的键归到最后的
  「其他」块，``collapsed: true`` 的块默认折叠。原始文件仍可在最后展开查看
- ``inner``（``detail=True`` 时）：``{"<iteration>": [事件 | 会话]}``，阶段内部的过程。

  - 事件 ``{kind, text, detail?}``：kind 取 text / thinking / tool / result / error
  - 会话 ``{kind: "session", title, events: [事件], stage?, fresh?, resumes?, session?, inputs?,
    output?, status?, meta?}``：一次 LLM / agent 调用。``stage`` 让它也显示在那个阶段的详情里；
    ``fresh: true`` 表示新进程、不带对话历史（false 表示续接，``resumes`` 写续接了谁）；
    ``inputs: [{title, text, note?, collapsed?}]`` 是会话开始时拿到的全部内容（例如上一阶段的
    交接、任务上下文、阶段说明），``output`` 是它交给下一步的内容；
    ``status`` 取 running / done / error

  只给事件的旧格式照旧显示成一条时间线。

时间线视图（:func:`loop_timeline`）用框架记录的阶段起止时间（``timeline.yaml``，见
:mod:`xtra_rsi.status`）；没有这个文件的旧 run 用阶段输出文件的修改时间估计，标为 ``estimated``。
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, cast

from xtra_rsi.core import STAGE_ORDER
from xtra_rsi.status import HEARTBEAT_S, STATUS_FILE, TIMELINE_FILE
from xtra_rsi.yamlio import YAMLError, dump, load_mapping, loads

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from xtra_rsi.payload import Payload, Value

PLUGIN_GROUP = "xtra_rsi.viz"
NODE_FILE = "node.yaml"
MAX_DEPTH = 8
MAX_LOOPS = 2000
MAX_YAML_CHARS = 200_000
MAX_TIMELINE_IDS = 400
"""``/api/timeline`` 一次最多返回多少个 loop。"""
_ITER_RE = re.compile(r"^iter-(\d+)$")
_ID_RE = re.compile(r"^(?:root|n[0-9]{4,})$")
_SKIP_DIRS = {"workspace", "node_modules", "__pycache__", "harness", "site-packages"}
_STAGES = [s.value for s in STAGE_ORDER]

type Describe = Callable[..., object]


@dataclass
class Found:
    """一次扫描的结果。"""

    loops: dict[str, Path] = field(default_factory=dict[str, Path])
    """loop id → loop 目录。"""
    loop_node: dict[str, str] = field(default_factory=dict[str, str])
    """loop id → 所属节点的 key（节点表里的 loop）。"""
    loop_group: dict[str, str] = field(default_factory=dict[str, str])
    nodes: dict[str, Path] = field(default_factory=dict[str, Path])
    """节点 key（``<表 id>/<节点 id>``）→ 节点目录。"""


def loop_id(path: Path) -> str:
    """Loop 目录的稳定 id。"""
    return hashlib.sha1(str(path).encode(), usedforsecurity=False).hexdigest()[:12]


def is_loop_dir(path: Path) -> bool:
    """是不是 run_loop 的输出目录：有 status.yaml，或有含 ``<stage>.yaml`` 的 ``iter-<n>`` 子目录。

    只有 ``iter-<n>`` 目录名不算：下游常按轮次建别的目录（例如每轮的 LLM 日志），不能当成 loop。
    """
    if (path / STATUS_FILE).is_file():
        return True
    try:
        return any(
            _ITER_RE.match(p.name) and any((p / f"{s}.yaml").is_file() for s in _STAGES)
            for p in path.iterdir()
        )
    except OSError:
        return False


def _subdirs(path: Path) -> list[Path]:
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name)
    except OSError:
        return []
    return [
        Path(e.path)
        for e in entries
        if e.is_dir(follow_symlinks=False)
        and not e.name.startswith(".")
        and e.name not in _SKIP_DIRS
    ]


def discover(roots: list[Path]) -> Found:
    """在 roots 下找 loop 目录和节点表。"""
    found = Found()
    for root in roots:
        root = root.expanduser().resolve()  # noqa: PLW2901 -- 规范化后的同一个根
        for path, depth in _walk(root):
            if len(found.loops) >= MAX_LOOPS:
                return found
            if is_loop_dir(path):
                _add_loop(found, root if depth > 0 else root.parent, path)
            if (path / NODE_FILE).is_file() and _ID_RE.match(path.name):
                table = loop_id(path.parent)
                found.nodes[f"{table}/{path.name}"] = path
    for lid, lp in found.loops.items():
        for key, np in found.nodes.items():
            if np in lp.parents:
                found.loop_node[lid] = key
                found.loop_group[lid] = f"节点 {key.split('/')[1]}"
    return found


def _walk(root: Path) -> Iterator[tuple[Path, int]]:
    stack = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        yield path, depth
        if depth >= MAX_DEPTH or is_loop_dir(path) or not path.is_dir():
            continue
        stack.extend((p, depth + 1) for p in reversed(_subdirs(path)))


def _add_loop(found: Found, root: Path, path: Path) -> None:
    lid = loop_id(path)
    found.loops[lid] = path
    rel = path.parent.relative_to(root) if path.parent.is_relative_to(root) else path.parent
    found.loop_group[lid] = str(rel) if str(rel) != "." else root.name


# ---------- 读文件 ----------

_cache: dict[Path, tuple[int, int, Payload]] = {}


def read_yaml(path: Path) -> Payload | None:
    """读 YAML 映射，按 mtime 缓存；读不了时返回 None。"""
    try:
        st = path.stat()
    except OSError:
        return None
    hit = _cache.get(path)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        obj = load_mapping(path.read_text(encoding="utf-8"), where=str(path))
    except (OSError, YAMLError, ValueError, UnicodeDecodeError):
        return None
    _cache[path] = (st.st_mtime_ns, st.st_size, obj)
    return obj


def _iterations(path: Path) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for p in _subdirs(path):
        m = _ITER_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def _parse_time(s: Value) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def liveness(status: Payload, now: datetime) -> tuple[str, float | None]:
    """status.yaml 的 state 加上存活判断：running 但进程没了时返回 ``lost``。"""
    state = str(status.get("state") or "unknown")
    hb = _parse_time(status.get("heartbeat_at"))
    age = (now - hb).total_seconds() if hb else None
    if state != "running":
        return state, age
    pid = status.get("pid")
    if status.get("host") == socket.gethostname() and isinstance(pid, int) and not _pid_alive(pid):
        return "lost", age
    interval = status.get("heartbeat_s")
    interval = float(interval) if isinstance(interval, int | float) else HEARTBEAT_S
    if age is not None and age > 3 * interval + 5:
        return "lost", age
    return state, age


# ---------- 插件 ----------


def _plugins() -> list[Describe]:
    out: list[Describe] = []
    for ep in entry_points(group=PLUGIN_GROUP):
        try:
            fn = cast("object", ep.load())
        except Exception:  # noqa: BLE001, S112 -- 插件装坏了不能拖垮整个面板
            continue
        if callable(fn):
            out.append(fn)
    return out


def describe(path: Path, *, detail: bool) -> tuple[Payload, str | None]:
    """依次问插件，返回第一个认识这个 loop 的插件的描述，以及插件报错。"""
    for fn in _plugins():
        try:
            obj = fn(path, detail=detail)
        except Exception as e:  # noqa: BLE001 -- 插件报错显示在卡片上，不中断面板
            return {}, f"{type(e).__name__}: {e}"
        if isinstance(obj, dict):
            return cast("Payload", obj), None
    return {}, None


# ---------- 给前端的形状 ----------


def _short(v: Value, limit: int = 80) -> str:
    if isinstance(v, dict):
        return f"{{{len(v)} 项}}"
    if isinstance(v, list):
        return f"[{len(v)}]"
    if isinstance(v, float):
        return f"{v:.4g}"
    s = "null" if v is None else str(v)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def stage_summary(obj: Payload) -> list[list[str]]:
    """阶段输出的顶层标量，作为没有插件时的摘要。"""
    scalars = [(k, v) for k, v in obj.items() if not k.startswith("_")]
    scalars.sort(key=lambda kv: isinstance(kv[1], dict | list))
    return [[k, _short(v)] for k, v in scalars[:6]]


def loop_summary(lid: str, path: Path, found: Found, now: datetime) -> Payload:
    """一张卡片要的全部信息（不含阶段输出全文）。"""
    status = read_yaml(path / STATUS_FILE)
    iters = _iterations(path)
    last_iter, last_stage, stages_done = None, None, list[str]()
    if iters:
        last_iter = iters[-1][0]
        stages_done = [s for s in _STAGES if (iters[-1][1] / f"{s}.yaml").is_file()]
        last_stage = stages_done[-1] if stages_done else None
    plugin, err = describe(path, detail=False)
    if status is not None:
        state, age = liveness(status, now)
        goal = cast("Payload", status.get("goal") or {})
        iteration, stage = status.get("iteration"), status.get("stage")
    else:
        state, age, goal = "unknown", None, {}
        iteration, stage = last_iter, last_stage
    if isinstance(plugin.get("goal"), dict):
        goal = {**goal, **cast("Payload", plugin["goal"])}
    if iteration is not None and iteration != last_iter:
        stages_done = []
    return {
        "id": lid,
        "path": str(path),
        "group": plugin.get("group") or found.loop_group.get(lid, ""),
        "node": found.loop_node.get(lid),
        "title": plugin.get("title") or path.name,
        "subtitle": plugin.get("subtitle") or "",
        "state": state,
        "heartbeat_age_s": age,
        "goal": goal,
        "profile": status.get("profile") if status else None,
        "iteration": iteration,
        "iterations": status.get("iterations") if status else None,
        "iterations_seen": len(iters),
        "stage": stage,
        "stages_done": cast("Value", stages_done),
        "started_at": status.get("started_at") if status else None,
        "updated_at": status.get("updated_at") if status else None,
        "pid": status.get("pid") if status else None,
        "host": status.get("host") if status else None,
        "reason": status.get("reason") if status else None,
        "error": status.get("error") if status else None,
        "kpis": cast("Value", plugin.get("kpis") or []),
        "series": cast("Value", plugin.get("series") or []),
        "plugin_error": err,
    }


def loop_detail(lid: str, path: Path, *, full: bool) -> Payload:
    """每轮每个阶段的摘要；full 时附上阶段输出全文和插件的内部过程。"""
    plugin, err = describe(path, detail=full)
    notes = cast("dict[str, dict[str, Value]]", plugin.get("stage_notes") or {})
    iterations: list[Value] = []
    for i, d in _iterations(path):
        stages: Payload = {}
        for s in _STAGES:
            f = d / f"{s}.yaml"
            note = notes.get(str(i), {}).get(s)
            if not f.is_file():
                # 还在跑的阶段没有输出文件；插件可以给一行进展（例如 agent 已经跑了多少步）
                if isinstance(note, str):
                    stages[s] = {"note": note, "summary": [], "pending": True}
                continue
            obj = read_yaml(f)
            entry: Payload = {
                "note": note if isinstance(note, str) else None,
                "summary": cast("Value", stage_summary(obj) if obj is not None else []),
                "mtime": datetime.fromtimestamp(f.stat().st_mtime, UTC).isoformat(
                    timespec="seconds"
                ),
            }
            if full:
                entry["yaml"] = _clip(f.read_text(encoding="utf-8", errors="replace"))
                if obj is not None:
                    entry["sections"] = cast("Value", split_sections(obj, plugin.get("sections")))
            stages[s] = entry
        iterations.append({"iteration": i, "stages": stages})
    out: Payload = {"id": lid, "iterations": iterations, "plugin_error": err}
    if full:
        out["inner"] = cast("Value", plugin.get("inner") or {})
    return out


def _clip(text: str) -> str:
    return text if len(text) <= MAX_YAML_CHARS else text[:MAX_YAML_CHARS] + "\n# …截断"


def split_sections(obj: Payload, spec: Value | None) -> list[Payload]:
    """按插件给的 ``sections`` 把阶段输出拆成带标题的 YAML 块；没有 spec 时返回空。"""
    if not isinstance(spec, list):
        return []
    out: list[Payload] = []
    used: set[str] = set()
    for sp in spec:
        if not isinstance(sp, dict):
            continue
        key = sp.get("key")
        if not isinstance(key, str) or key not in obj or key in used:
            continue
        used.add(key)
        v = obj[key]
        note = sp.get("note")
        out.append(
            {
                "title": str(sp.get("title") or key),
                "note": note if isinstance(note, str) else None,
                "collapsed": sp.get("collapsed") is True,
                "yaml": _clip(dump(v) if isinstance(v, dict) else dump({key: v})),
            }
        )
    rest: Payload = {k: v for k, v in obj.items() if k not in used}
    if out and rest:
        out.append({"title": "其他", "note": None, "collapsed": False, "yaml": _clip(dump(rest))})
    return out


# ---------- 时间线 ----------

_tl_cache: dict[Path, tuple[int, int, list[Payload] | None]] = {}


def _read_timeline(path: Path) -> list[Payload] | None:
    """读 timeline.yaml（按 mtime 缓存）；文件不存在或读不了时返回 None。"""
    try:
        st = path.stat()
    except OSError:
        return None
    hit = _tl_cache.get(path)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        raw = loads(path.read_text(encoding="utf-8"), where=str(path))
    except (OSError, YAMLError, ValueError, UnicodeDecodeError):
        # 进程在追加到一半时被杀会留下半项；整份当作读不了，退回估计
        raw = None
    items: list[Payload] | None
    if raw is None and st.st_size == 0:
        items = []
    elif isinstance(raw, list):
        items = [
            cast("Payload", x)
            for x in raw
            if isinstance(x, dict)
            and isinstance(x.get("iteration"), int)
            and x.get("stage") in _STAGES
        ]
    else:
        items = None
    _tl_cache[path] = (st.st_mtime_ns, st.st_size, items)
    return items


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _estimated_segments(path: Path, status: Payload | None) -> list[Payload]:
    """没有 timeline.yaml 时：阶段结束 = 输出文件的修改时间，开始 = 上一个阶段结束。"""
    segs: list[Payload] = []
    prev = _parse_time(status.get("started_at")) if status else None
    for i, d in _iterations(path):
        for s in _STAGES:
            f = d / f"{s}.yaml"
            try:
                end = datetime.fromtimestamp(f.stat().st_mtime, UTC)
            except OSError:
                continue
            start = prev if prev is not None and prev <= end else end
            segs.append(
                {
                    "iteration": i,
                    "stage": s,
                    "start": _iso(start),
                    "end": _iso(end),
                    "outcome": "ok",
                }
            )
            prev = end
    return segs


def loop_timeline(path: Path, now: datetime) -> Payload:
    """一个 loop 每个阶段的起止时间，给时间线视图。

    ``segments`` 按时间排序，每项 ``{iteration, stage, start, end, outcome}``；``end`` 为 null 表示
    正在跑，``outcome`` 取 ok / failed / running / lost。``estimated`` 为真表示时间是按文件修改
    时间估计的（旧 run 没有 timeline.yaml）。
    """
    status = read_yaml(path / STATUS_FILE)
    recorded = _read_timeline(path / TIMELINE_FILE)
    # 空文件只在新格式的 status 下可信（刚开始、还没有阶段跑完）
    precise = recorded is not None and (
        bool(recorded) or (status is not None and "stage_started_at" in status)
    )
    segs = list(recorded or []) if precise else _estimated_segments(path, status)
    estimated = not precise
    state, _age = liveness(status, now) if status is not None else ("unknown", None)
    if status is not None and state in {"running", "lost"}:
        it, st = status.get("iteration"), status.get("stage")
        done = {(x.get("iteration"), x.get("stage")) for x in segs}
        if isinstance(it, int) and st in _STAGES and (it, st) not in done:
            started = _parse_time(status.get("stage_started_at"))
            if started is None:  # 估计：上一个阶段结束时开始
                last = _parse_time(segs[-1]["end"]) if segs else None
                started = last or _parse_time(status.get("started_at"))
            if started is not None:
                hb = _parse_time(status.get("heartbeat_at"))
                lost = state == "lost"
                segs.append(
                    {
                        "iteration": it,
                        "stage": st,
                        "start": _iso(started),
                        "end": _iso(hb) if lost and hb is not None else None,
                        "outcome": "lost" if lost else "running",
                    }
                )
    return {"segments": cast("Value", segs), "estimated": estimated}


def timelines(found: Found, ids: list[str]) -> Payload:
    """``/api/timeline`` 的内容：指定 loop 的时间线（不认识的 id 跳过）。"""
    now = datetime.now(UTC)
    out: Payload = {}
    for lid in ids[:MAX_TIMELINE_IDS]:
        path = found.loops.get(lid)
        if path is not None:
            out[lid] = loop_timeline(path, now)
    return {"now": _iso(now), "loops": out}


def node_summary(key: str, path: Path, found: Found) -> Payload:
    """节点表里一个节点：目标、预算、状态、它的 loop。"""
    node = read_yaml(path / NODE_FILE) or {}
    contract = read_yaml(path / "contract.yaml") or {}
    table = key.split("/", maxsplit=1)[0]
    parent = node.get("parent")
    return {
        "key": key,
        "id": node.get("id") or path.name,
        "parent": f"{table}/{parent}" if isinstance(parent, str) else None,
        "stage": node.get("stage"),
        "target": node.get("target"),
        "metric": node.get("metric"),
        "status": node.get("status"),
        "budget_usd": node.get("budget_usd"),
        "spent_usd": node.get("spent_usd"),
        "allocated_usd": node.get("allocated_usd"),
        "rationale": contract.get("rationale"),
        "constraints": contract.get("constraints"),
        "loops": cast("Value", [lid for lid, k in found.loop_node.items() if k == key]),
    }


def snapshot(found: Found) -> Payload:
    """``/api/loops`` 的内容。"""
    now = datetime.now(UTC)
    return {
        "now": now.isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "loops": [loop_summary(lid, p, found, now) for lid, p in found.loops.items()],
        "nodes": [node_summary(k, p, found) for k, p in found.nodes.items()],
    }
