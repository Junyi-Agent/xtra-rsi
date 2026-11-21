"""用外部命令实现一个阶段，用来接 LLM agent（如 ``claude -p``）或任意语言写的工具。

协议：stdin 收到一个 JSON 对象
``{"stage", "iteration", "instructions", "upstream"}``，其中 ``instructions``
是该阶段 AGENTS.md 的全文；命令把阶段输出（JSON 对象）写到 stdout，
非零退出视为失败。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from xtra_rsi.core import JSON, JSONObject, StageContext


class CommandStageError(RuntimeError):
    """外部命令失败或输出不是 JSON 对象。"""


@dataclass(frozen=True)
class CommandStage:
    """把外部命令包装成 :class:`xtra_rsi.core.StageFn`。"""

    argv: Sequence[str]
    agents_md: Path | None = None
    timeout: float | None = 600.0

    def __call__(self, ctx: StageContext, /) -> JSONObject:
        """运行命令并解析 stdout。"""
        instructions = self.agents_md.read_text(encoding="utf-8") if self.agents_md else ""
        request: JSONObject = {
            "stage": ctx.stage.value,
            "iteration": ctx.iteration,
            "instructions": instructions,
            "upstream": ctx.upstream,
        }
        proc = subprocess.run(  # noqa: S603 -- argv 由 profile 作者提供
            list(self.argv),
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if proc.returncode != 0:
            msg = f"{ctx.stage.value}: 命令退出码 {proc.returncode}\nstderr:\n{proc.stderr}"
            raise CommandStageError(msg)
        try:
            out = cast("JSON", json.loads(proc.stdout))
        except json.JSONDecodeError as e:
            msg = f"{ctx.stage.value}: stdout 不是 JSON: {e}\nstdout:\n{proc.stdout[:2000]}"
            raise CommandStageError(msg) from e
        if not isinstance(out, dict):
            msg = f"{ctx.stage.value}: stdout 应为 JSON 对象，实际为 {type(out).__name__}"
            raise CommandStageError(msg)
        return out
