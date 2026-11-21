"""``rsi`` 命令行。

结果写 stdout（以 ``---`` 分隔的 YAML 文档流或纯文本行），错误写 stderr，失败时非零退出。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from xtra_rsi.ablation import load_cases, run_ablation
from xtra_rsi.core import STAGE_ORDER, ProfileError, Stage, run_loop
from xtra_rsi.harness import SyncError, base_root, check_layout, stage_dirs, sync
from xtra_rsi.node_loop import run_node
from xtra_rsi.nodes import NodeStatus, NodeStore
from xtra_rsi.pricing import load_prices
from xtra_rsi.registry import available_profiles, load_profile
from xtra_rsi.yamlio import dump

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from xtra_rsi.payload import Payload


class Args(argparse.Namespace):
    """解析后的参数（给类型检查器用的声明）。"""

    func: Callable[[Args], int] | None = None
    harness: Path | None = None
    check: bool = False
    profile: str | None = None
    iterations: int = 10
    run_dir: Path | None = None
    stage: str | None = None
    nodes_dir: Path = Path("rsi/nodes")
    node: str | None = None
    node_id: str = ""
    target: str = ""
    metric: str = ""
    budget: float = 0.0
    reason: str = ""
    status: str = ""
    prices: Path | None = None
    paths: list[Path] | None = None
    host: str = "127.0.0.1"
    port: int = 8765


def _emit(obj: Payload) -> None:
    _ = sys.stdout.write(dump(obj, explicit_start=True))


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
    """运行 loop，每轮输出一个 YAML 文档。"""
    if args.profile is None:
        _err("缺少 --profile")
        return 2
    profile = load_profile(args.profile)
    if args.node is None:
        for record in run_loop(profile, iterations=args.iterations, run_dir=args.run_dir):
            _emit(record.to_dict())
        return 0
    result = run_node(
        NodeStore(args.nodes_dir),
        args.node,
        profile,
        iterations=args.iterations,
        prices=load_prices(args.prices),
        run_dir=args.run_dir,
    )
    for record in result.records:
        _emit(record.to_dict())
    if result.stopped:
        _err(f"{args.node} 提前停止: {result.stopped}")
        return 1
    return 0


def cmd_node_init(args: Args) -> int:
    """由人创建根节点。"""
    node = NodeStore(args.nodes_dir).init_root(
        target=args.target, metric=args.metric, budget_usd=args.budget
    )
    _emit(node.to_dict())
    return 0


def cmd_node_list(args: Args) -> int:
    """列出所有节点（给人看全局；节点自己只看局部视野）。"""
    for node in NodeStore(args.nodes_dir).all():
        _emit({**node.to_dict(), "remaining_usd": node.remaining_usd})
    return 0


def cmd_node_show(args: Args) -> int:
    """显示一个节点：自身、约定、子节点摘要。"""
    store = NodeStore(args.nodes_dir)
    _emit({"node": store.get(args.node_id).to_dict(), "view": store.view(args.node_id).to_dict()})
    return 0


def cmd_node_close(args: Args) -> int:
    """结束运行中的节点（retired / done）。"""
    _emit(
        NodeStore(args.nodes_dir)
        .close(args.node_id, NodeStatus(args.status), args.reason)
        .to_dict()
    )
    return 0


def cmd_approve(args: Args) -> int:
    """人确认开子节点。"""
    try:
        node = NodeStore(args.nodes_dir).approve(args.node_id)
    except SyncError as e:
        _err(str(e))
        return 2
    _emit(node.to_dict())
    return 0


def cmd_reject(args: Args) -> int:
    """人拒绝开子节点。"""
    _emit(NodeStore(args.nodes_dir).reject(args.node_id, args.reason).to_dict())
    return 0


def cmd_eval(args: Args) -> int:
    """对阶段跑 ablation 用例，每条用例输出一个 YAML 文档；有用例不达标时退出码 1。"""
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
            _emit(result.to_dict())
            failed = failed or not result.passed
    return 1 if failed else 0


def cmd_viz(args: Args) -> int:
    """启动只读的可视化面板。"""
    from xtra_rsi.viz import main as viz_main  # noqa: PLC0415 -- 只有 viz 需要 http.server

    return viz_main(args.paths or [Path()], host=args.host, port=args.port)


def _add_eval(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:  # pyright: ignore[reportPrivateUsage] -- argparse 没有公开这个类型
    e = sub.add_parser("eval", help="对阶段跑 ablation 用例")
    _ = e.add_argument("stage", nargs="?", choices=[s.value for s in STAGE_ORDER])
    _ = e.add_argument("--harness", type=Path)
    _ = e.add_argument("--profile", help="只跑这个 profile 的用例")
    e.set_defaults(func=cmd_eval)


def _add_viz(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:  # pyright: ignore[reportPrivateUsage] -- argparse 没有公开这个类型
    v = sub.add_parser("viz", help="在浏览器里看 loop 的目标、当前阶段、每轮结果（只读）")
    _ = v.add_argument(
        "paths", nargs="*", type=Path, help="扫描这些目录里的 run 和节点表，默认当前目录"
    )
    _ = v.add_argument("--host", default="127.0.0.1")
    _ = v.add_argument("--port", type=int, default=8765, help="0 表示随机端口")
    v.set_defaults(func=cmd_viz)


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
    _ = r.add_argument("--node", help="以这个节点的身份运行：记账、处理开子节点的申请")
    _ = r.add_argument("--nodes-dir", type=Path, default=Path("rsi/nodes"))
    _ = r.add_argument("--prices", type=Path, help="覆盖默认单价表的 YAML（美元/百万 token）")
    r.set_defaults(func=cmd_run)

    n = sub.add_parser("node", help="嵌套 RSI 的节点表")
    nsub = n.add_subparsers(dest="node_cmd", required=True)
    ni = nsub.add_parser("init", help="创建根节点")
    _ = ni.add_argument("--target", required=True, help="要改进的对象（相对项目根）")
    _ = ni.add_argument("--metric", required=True)
    _ = ni.add_argument("--budget", type=float, required=True, help="总预算（美元）")
    ni.set_defaults(func=cmd_node_init)
    _ = nsub.add_parser("list", help="列出所有节点").set_defaults(func=cmd_node_list)
    ns = nsub.add_parser("show", help="显示一个节点")
    _ = ns.add_argument("node_id")
    ns.set_defaults(func=cmd_node_show)
    nc = nsub.add_parser("close", help="结束运行中的节点，花费并入父节点、余额退回")
    _ = nc.add_argument("node_id")
    _ = nc.add_argument("--status", required=True, choices=["retired", "done"])
    _ = nc.add_argument("--reason", default="")
    nc.set_defaults(func=cmd_node_close)
    for sp in (ni, ns, nc, nsub.choices["list"]):
        _ = sp.add_argument("--nodes-dir", type=Path, default=Path("rsi/nodes"))

    ap = sub.add_parser("approve", help="（人）确认开子节点")
    _ = ap.add_argument("node_id")
    ap.set_defaults(func=cmd_approve)
    rj = sub.add_parser("reject", help="（人）拒绝开子节点")
    _ = rj.add_argument("node_id")
    _ = rj.add_argument("--reason", default="")
    rj.set_defaults(func=cmd_reject)
    for sp in (ap, rj):
        _ = sp.add_argument("--nodes-dir", type=Path, default=Path("rsi/nodes"))

    _add_eval(sub)
    _add_viz(sub)
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
