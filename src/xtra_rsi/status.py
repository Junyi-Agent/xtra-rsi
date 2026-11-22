"""loop 运行状态文件 ``<run_dir>/status.yaml``（ADR 0008）。

``run_loop(run_dir=...)`` 在运行期间维护这个文件，给 ``rsi viz`` 和人看「这个 loop 在改进
什么、跑到哪了、还活着没有」：

- ``goal``：loop 在改进什么（``Profile.goal``，约定键 ``target`` / ``metric``）
- ``state``：``running`` / ``done`` / ``stopped`` / ``failed``
- ``iteration`` / ``stage``：正在跑（或最后跑）的迭代和阶段
- ``pid`` / ``host`` / ``heartbeat_at``：后台线程每 :data:`HEARTBEAT_S` 秒刷新心跳；
  阶段可能跑很久（例如 action 里的 agent 跑一小时），心跳停了说明进程没了
- ``stage_started_at``：当前阶段的开始时间（毫秒精度），loop 结束后为 null
- ``reason`` / ``error``：提前结束的原因、失败时的异常

文件整体替换写入（先写临时文件再 rename），读的一方不会读到半个文件。

每个阶段结束时还往 ``<run_dir>/timeline.yaml`` 追加一项（:data:`TIMELINE_FILE`）::

    - iteration: 0
      stage: observe
      start: '2026-11-22T07:05:19.123+00:00'
      end: '2026-11-22T07:05:19.456+00:00'
      outcome: ok        # ok / failed（阶段抛异常或进程被中断）

整个文件是一个 YAML 列表，只追加不改写；每次 ``run_loop`` 开始时清空。正在跑的阶段不在里面，
看 status.yaml 的 ``iteration`` / ``stage`` / ``stage_started_at``。``rsi viz`` 的时间线视图读它。
"""

from __future__ import annotations

import os
import socket
import threading
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from xtra_rsi.yamlio import dump

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from xtra_rsi.payload import Payload, Value

STATUS_FILE = "status.yaml"
TIMELINE_FILE = "timeline.yaml"
HEARTBEAT_S = 10.0
"""心跳间隔（秒）。读的一方超过几倍间隔没看到心跳就认为进程没了。"""


class RunState(StrEnum):
    """loop 的运行状态。"""

    RUNNING = "running"
    DONE = "done"
    """跑满迭代数，或 feedback 输出 stop 提前结束。"""
    STOPPED = "stopped"
    """``on_output`` 抛 ``StopLoop`` 停止（例如预算用完）。"""
    FAILED = "failed"
    """阶段抛异常或进程被中断。"""


def now_iso() -> str:
    """当前 UTC 时间，秒精度。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


def now_iso_ms() -> str:
    """当前 UTC 时间，毫秒精度（阶段起止时间用；有的阶段不到一秒）。"""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class NullStatus:
    """不写文件的 status（run_loop 没有 run_dir 时用）。"""

    def stage(self, *_args: object) -> None:
        """什么都不做。"""

    def finish(self, *_args: object, **_kwargs: object) -> None:
        """什么都不做。"""


class StatusWriter:
    """维护一个 run 的 status.yaml；作为 context manager 使用，退出时停心跳线程。"""

    def __init__(self, run_dir: Path, *, profile: str, goal: Payload, iterations: int) -> None:
        """写入初始状态并启动心跳线程。"""
        self.path: Path = run_dir / STATUS_FILE
        self.timeline_path: Path = run_dir / TIMELINE_FILE
        self._cur: tuple[int, str, str] | None = None
        """正在跑的阶段：（迭代, 阶段, 开始时间）。"""
        now = now_iso()
        self._data: dict[str, Value] = {
            "profile": profile,
            "goal": dict(goal),
            "state": RunState.RUNNING.value,
            "iteration": 0,
            "iterations": iterations,
            "stage": None,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": now,
            "updated_at": now,
            "heartbeat_at": now,
            "heartbeat_s": HEARTBEAT_S,
            "stage_started_at": None,
            "reason": None,
            "error": None,
        }
        self._lock: threading.Lock = threading.Lock()
        self._stop: threading.Event = threading.Event()
        run_dir.mkdir(parents=True, exist_ok=True)
        _ = self.timeline_path.write_text("", encoding="utf-8")
        self._write()
        self._thread: threading.Thread = threading.Thread(
            target=self._beat, name="rsi-status", daemon=True
        )
        self._thread.start()

    def __enter__(self) -> Self:
        """进入时什么都不做（构造时已经开始）。"""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """有未处理的异常时记为 failed；停心跳线程。"""
        if exc is not None and self._data["state"] == RunState.RUNNING.value:
            self.finish(RunState.FAILED, error=f"{type(exc).__name__}: {exc}")
        self._stop.set()
        self._thread.join(timeout=HEARTBEAT_S)

    def stage(self, iteration: int, stage: str) -> None:
        """记录开始运行某个阶段（上一个阶段记为正常结束）。"""
        self._close("ok")
        started = now_iso_ms()
        self._cur = (iteration, stage, started)
        self._update(iteration=iteration, stage=stage, stage_started_at=started)

    def finish(
        self, state: RunState, *, reason: str | None = None, error: str | None = None
    ) -> None:
        """记录结束状态；正在跑的阶段在 failed 时记为失败，否则记为正常结束。"""
        self._close("failed" if state is RunState.FAILED else "ok")
        self._update(state=state.value, reason=reason, error=error, stage_started_at=None)

    def _close(self, outcome: str) -> None:
        """把正在跑的阶段追加到 timeline.yaml。"""
        if self._cur is None:
            return
        iteration, stage, started = self._cur
        self._cur = None
        item: Payload = {
            "iteration": iteration,
            "stage": stage,
            "start": started,
            "end": now_iso_ms(),
            "outcome": outcome,
        }
        with self.timeline_path.open("a", encoding="utf-8") as f:
            _ = f.write(dump([item]))

    def _update(self, **fields: Value) -> None:
        with self._lock:
            self._data.update(fields)
            self._data["updated_at"] = now_iso()
            self._data["heartbeat_at"] = self._data["updated_at"]
            self._write()

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            with self._lock:
                if self._data["state"] != RunState.RUNNING.value:
                    return
                self._data["heartbeat_at"] = now_iso()
                self._write()

    def _write(self) -> None:
        tmp = self.path.with_name(f".{STATUS_FILE}.{os.getpid()}.tmp")
        _ = tmp.write_text(dump(dict(self._data)), encoding="utf-8")
        _ = tmp.replace(self.path)
