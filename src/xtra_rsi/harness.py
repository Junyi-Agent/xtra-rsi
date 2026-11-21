"""harness 目录：每个阶段一个 ``harness/<stage>/``，含 AGENTS.md、scripts/、evals/、data/。

框架自带的 harness 是「基础层」，随包发布。下游项目用 ``rsi sync`` 生成自己的
``harness/<stage>/AGENTS.md``：内容是基础层 + 项目自己的 ``AGENTS.local.md``。
升级框架版本后再跑一次 ``rsi sync``，基础层的改动就跟着进来；``rsi sync --check``
在 pre-commit / CI 里检查是否忘了同步。
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from xtra_rsi.core import STAGE_ORDER, Stage

AGENTS_MD = "AGENTS.md"
LOCAL_MD = "AGENTS.local.md"
SUBDIRS = ("scripts", "evals", "data")
CASES_FILE = "cases.yaml"

_GENERATED_MARK = "<!-- xtra-rsi:generated"
_LOCAL_MARK = "<!-- xtra-rsi:local -->"


def framework_version() -> str:
    """已安装的 xtra-rsi 版本。"""
    try:
        return version("xtra-rsi")
    except PackageNotFoundError:
        return "0+unknown"


def base_root() -> Path:
    """框架基础层 harness 的位置。

    wheel 安装时在包内 ``xtra_rsi/_harness``；editable 安装（框架仓库自身开发）时
    在仓库根目录 ``harness/``。
    """
    here = Path(__file__).resolve().parent
    packaged = here / "_harness"
    if packaged.is_dir():
        return packaged
    return here.parents[1] / "harness"


@dataclass(frozen=True)
class StageDir:
    """一个阶段的 harness 目录。"""

    root: Path
    stage: Stage

    @property
    def path(self) -> Path:
        """``<root>/<stage>``。"""
        return self.root / self.stage.value

    @property
    def agents_md(self) -> Path:
        """该阶段的 AGENTS.md。"""
        return self.path / AGENTS_MD

    @property
    def cases(self) -> Path:
        """该阶段的 ablation 用例文件。"""
        return self.path / "evals" / CASES_FILE


def stage_dirs(root: Path) -> list[StageDir]:
    """按 loop 顺序返回 root 下的五个阶段目录。"""
    return [StageDir(root, s) for s in STAGE_ORDER]


def check_layout(root: Path) -> list[str]:
    """检查 harness 目录结构，返回问题列表（空表示合格）。"""
    problems: list[str] = []
    if not root.is_dir():
        return [f"{root}: 不存在"]
    known = {s.value for s in STAGE_ORDER}
    problems.extend(
        f"{p}: 不是已知阶段（{', '.join(known)}）"
        for p in sorted(root.iterdir())
        if p.is_dir() and not p.name.startswith((".", "_")) and p.name not in known
    )
    for sd in stage_dirs(root):
        if not sd.path.is_dir():
            problems.append(f"{sd.path}: 缺少阶段目录")
            continue
        if not sd.agents_md.is_file() or not sd.agents_md.read_text(encoding="utf-8").strip():
            problems.append(f"{sd.agents_md}: 缺失或为空")
        problems.extend(
            f"{sd.path / sub}: 缺少子目录" for sub in SUBDIRS if not (sd.path / sub).is_dir()
        )
    return problems


class SyncError(RuntimeError):
    """``rsi sync`` 无法执行。"""


def render_agents_md(stage: Stage, base: str, local: str, ver: str) -> str:
    """合成下游项目的 AGENTS.md。"""
    header = (
        f"{_GENERATED_MARK} by `rsi sync` from xtra-rsi {ver}; "
        f"stage={stage.value}. 不要直接编辑：改 {LOCAL_MD} 后重新运行 `rsi sync` -->\n\n"
    )
    body = base.rstrip() + "\n"
    if local.strip():
        body += f"\n{_LOCAL_MARK}\n\n## 项目补充（来自 {LOCAL_MD}）\n\n{local.strip()}\n"
    return header + body


@dataclass(frozen=True)
class SyncChange:
    """sync 计划中的一处改动。"""

    path: Path
    reason: str


def _plan_stage(base_sd: StageDir, sd: StageDir, ver: str) -> tuple[list[SyncChange], str]:
    """返回一个阶段需要的改动和目标 AGENTS.md 内容。"""
    changes = [
        SyncChange(sd.path / sub, "创建目录") for sub in SUBDIRS if not (sd.path / sub).is_dir()
    ]
    local_path = sd.path / LOCAL_MD
    if local_path.is_file():
        local = local_path.read_text(encoding="utf-8")
    else:
        local = ""
        changes.append(SyncChange(local_path, "创建空的项目补充文件"))
    want = render_agents_md(sd.stage, base_sd.agents_md.read_text(encoding="utf-8"), local, ver)
    have = sd.agents_md.read_text(encoding="utf-8") if sd.agents_md.is_file() else None
    if have is not None and not have.startswith(_GENERATED_MARK):
        msg = (
            f"{sd.agents_md} 不是 `rsi sync` 生成的；"
            f"把其中项目特有的内容移到 {LOCAL_MD} 后删掉它再 sync"
        )
        raise SyncError(msg)
    if have != want:
        changes.append(SyncChange(sd.agents_md, "创建" if have is None else "更新"))
    return changes, want


def sync(project_harness: Path, *, check: bool = False) -> list[SyncChange]:
    """把基础层同步到下游项目的 harness 目录。

    ``check=True`` 时只返回需要的改动，不写文件。
    """
    base = base_root()
    if project_harness.resolve() == base.resolve():
        msg = f"{project_harness} 就是框架基础层本身，不能对它 sync"
        raise SyncError(msg)
    problems = check_layout(base)
    if problems:
        msg = "框架基础层 harness 不完整:\n" + "\n".join(problems)
        raise SyncError(msg)

    ver = framework_version()
    plans = [
        (sd, *_plan_stage(base_sd, sd, ver))
        for base_sd, sd in zip(stage_dirs(base), stage_dirs(project_harness), strict=True)
    ]
    if not check:
        for sd, changes, want in plans:
            if not changes:
                continue
            for sub in SUBDIRS:
                (sd.path / sub).mkdir(parents=True, exist_ok=True)
            (sd.path / LOCAL_MD).touch()
            _ = sd.agents_md.write_text(want, encoding="utf-8")
    return [c for _, changes, _ in plans for c in changes]
