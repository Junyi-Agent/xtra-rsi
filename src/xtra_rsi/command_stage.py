"""用外部命令实现一个阶段，用来接 LLM agent（如 ``claude -p``）或任意语言写的工具。

协议（YAML）：

- stdin：一个映射，键为 ``stage``、``iteration``、``instructions``（该阶段 AGENTS.md
  全文）、``upstream``（上一阶段的输出）。
- stdout：阶段输出，一个顶层为映射的 YAML 文档。整个输出被单个 ```` ```yaml ````
  代码块包住时会自动去掉围栏（LLM 常这样输出）。
- 非零退出视为失败。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xtra_rsi.yamlio import YAMLError, dump, load_mapping

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from xtra_rsi.core import StageContext
    from xtra_rsi.payload import Payload

_FENCE_RE = re.compile(r"\A\s*```(?:ya?ml)?[ \t]*\n(?P<body>.*?)\n```\s*\Z", re.DOTALL)


class CommandStageError(RuntimeError):
    """外部命令失败或输出不是 YAML 映射。"""


def strip_fence(text: str) -> str:
    """整个文本是单个 ```yaml 代码块时返回块内内容，否则原样返回。"""
    m = _FENCE_RE.match(text)
    return m.group("body") if m else text


@dataclass(frozen=True)
class CommandStage:
    """把外部命令包装成 :class:`xtra_rsi.core.StageFn`。"""

    argv: Sequence[str]
    agents_md: Path | None = None
    timeout: float | None = 600.0

    def __call__(self, ctx: StageContext, /) -> Payload:
        """运行命令并解析 stdout。"""
        instructions = self.agents_md.read_text(encoding="utf-8") if self.agents_md else ""
        request: Payload = {
            "stage": ctx.stage.value,
            "iteration": ctx.iteration,
            "instructions": instructions,
            "upstream": ctx.upstream,
        }
        proc = subprocess.run(  # noqa: S603 -- argv 由 profile 作者提供
            list(self.argv),
            input=dump(request),
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if proc.returncode != 0:
            msg = f"{ctx.stage.value}: 命令退出码 {proc.returncode}\nstderr:\n{proc.stderr}"
            raise CommandStageError(msg)
        try:
            return load_mapping(strip_fence(proc.stdout), where=f"{ctx.stage.value} stdout")
        except YAMLError as e:
            msg = f"{e}\nstdout:\n{proc.stdout[:2000]}"
            raise CommandStageError(msg) from e
