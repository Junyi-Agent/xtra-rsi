"""``rsi`` 命令行。结果写 stdout（JSON Lines 或纯文本），错误写 stderr，失败时非零退出。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from xtra_rsi.ablation import load_cases, run_ablation
from xtra_rsi.core import STAGE_ORDER, ProfileError, Stage, run_loop
from xtra_rsi.harness import SyncError, base_root, check_layout, stage_dirs, sync
from xtra_rsi.registry import available_profiles, load_profile

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from xtra_rsi.core import JSONObject


class Args(argparse.Namespace):
    """解析后的参数（给类型检查器用的声明）。"""

    func: Callable[[Args], int] | None = None
    harness: Path | None = None
    check: bool = False
    profile: str | None = None
    iterations: int = 10
    run_dir: Path | None = None
    stage: str | None = None


def _emit(obj: JSONObject) -> None:
    _ = sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _err(msg: str) -> None:
    _ = sys.stderr.write(f"rsi: {msg}\n")


def _default_harness() -> Path:
    local = Path("harness")
    return local if local.is_dir() else base_root()


def cmd_stages(_args: Args) -> int:
    """按顺序打印阶段名。"""
    for s in STAGE_ORDER:
        _ = sys.stdout.write(s.value + "\n")
    return 0


def cmd_profiles(_args: Args) -> int:
    """打印已安装的 profile。"""
    for name in available_profiles():
        _ = sys.stdout.write(name + "\n")
    return 0


def cmd_check(args: Args) -> int:
    """检查 harness 目录结构。"""
    root = args.harness or _default_harness()
    problems = check_layout(root)
    for p in problems:
        _err(p)
    return 1 if problems else 0


def cmd_sync(args: Args) -> int:
    """把框架基础层同步到当前项目。"""
    root = args.harness or Path("harness")
    try:
        changes = sync(root, check=args.check)
    except SyncError as e:
        _err(str(e))
        return 2
    for c in changes:
        _ = sys.stdout.write(f"{c.reason}\t{c.path}\n")
    return 1 if args.check and changes else 0


def cmd_run(args: Args) -> int:
    """运行 loop，每轮输出一行 JSON。"""
    if args.profile is None:
        _err("缺少 --profile")
        return 2
    profile = load_profile(args.profile)
    for record in run_loop(profile, iterations=args.iterations, run_dir=args.run_dir):
        _emit(record.to_json())
    return 0


def cmd_eval(args: Args) -> int:
    """对阶段跑 ablation 用例，每条用例输出一行 JSON；有用例不达标时退出码 1。"""
    root = args.harness or _default_harness()
    stages = [Stage(args.stage)] if args.stage else list(STAGE_ORDER)
    failed = False
    for sd in (d for d in stage_dirs(root) if d.stage in stages):
        if not sd.cases.is_file():
            _err(f"{sd.cases}: 没有 ablation 用例")
            failed = True
            continue
        for case in load_cases(sd.cases):
            if args.profile and case.profile != args.profile:
                continue
            result = run_ablation(sd.stage, case)
            _emit(result.to_json())
            failed = failed or not result.passed
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器。"""
    p = argparse.ArgumentParser(prog="rsi", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    _ = sub.add_parser("stages", help="按顺序列出阶段").set_defaults(func=cmd_stages)
    _ = sub.add_parser("profiles", help="列出已安装的 profile").set_defaults(func=cmd_profiles)

    c = sub.add_parser("check", help="检查 harness 目录结构")
    _ = c.add_argument("harness", nargs="?", type=Path)
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("sync", help="把框架的阶段 AGENTS.md 同步到当前项目的 harness/")
    _ = s.add_argument("harness", nargs="?", type=Path)
    _ = s.add_argument("--check", action="store_true", help="只检查是否需要同步，需要时退出码 1")
    s.set_defaults(func=cmd_sync)

    r = sub.add_parser("run", help="运行 loop")
    _ = r.add_argument("--profile", required=True)
    _ = r.add_argument("--iterations", type=int, default=10)
    _ = r.add_argument("--run-dir", type=Path, help="把每个阶段的输出写到这个目录")
    r.set_defaults(func=cmd_run)

    e = sub.add_parser("eval", help="对阶段跑 ablation 用例")
    _ = e.add_argument("stage", nargs="?", choices=[s.value for s in STAGE_ORDER])
    _ = e.add_argument("--harness", type=Path)
    _ = e.add_argument("--profile", help="只跑这个 profile 的用例")
    e.set_defaults(func=cmd_eval)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """入口。"""
    args = build_parser().parse_args(argv, namespace=Args())
    if args.func is None:  # 子命令是 required，正常走不到
        return 2
    try:
        return args.func(args)
    except (ProfileError, KeyError, ValueError, TypeError) as e:
        _err(str(e))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
