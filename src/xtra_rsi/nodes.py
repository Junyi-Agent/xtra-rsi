"""嵌套 RSI 的节点表（ADR 0007）。

每个 RSI 节点是 ``<nodes_dir>/<id>/`` 下的一组 YAML 文件：

- ``node.yaml``：节点本身（父节点、目标、指标、预算、花费、状态）
- ``contract.yaml``：父节点给它的约定（根节点是人给的），节点只读
- ``summary.yaml``：节点交给父节点的摘要
- ``ledger.yaml``：花费流水，追加写入的 YAML 文档流
- ``harness/``：子节点自己五个阶段的 harness（根节点用项目的 ``harness/``）
- ``runs/``：节点的运行记录

预算单位是美元。``remaining = budget - spent - allocated``：开子节点时从父节点的剩余里划出
（``allocated``），子节点结束时把它的花费并入父节点的 ``spent``，未花完的部分退回。

节点只通过 :class:`NodeView` 看到局部：自己、父节点给的约定、子节点的摘要。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from xtra_rsi.core import Stage
from xtra_rsi.harness import sync
from xtra_rsi.yamlio import YAMLError, dump, load_mapping

if TYPE_CHECKING:
    from xtra_rsi.payload import Payload, Value

ROOT_ID = "root"
NODE_FILE = "node.yaml"
CONTRACT_FILE = "contract.yaml"
SUMMARY_FILE = "summary.yaml"
LEDGER_FILE = "ledger.yaml"
_ID_RE = re.compile(r"^(?:root|n[0-9]{4,})$")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class NodeStatus(StrEnum):
    """节点状态。"""

    PENDING_APPROVAL = "pending_approval"
    """子节点已申请，等人 ``rsi approve`` / ``rsi reject``；不运行、不占预算。"""
    RUNNING = "running"
    PROPOSED = "proposed"
    """交了改动提案，等父节点评估。"""
    REJECTED = "rejected"
    """人拒绝了申请。"""
    RETIRED = "retired"
    """被撤销（例如连续多轮没有被接受的改动）。"""
    DONE = "done"


OPEN_STATUSES = frozenset({NodeStatus.PENDING_APPROVAL, NodeStatus.RUNNING, NodeStatus.PROPOSED})
ACTIVE_STATUSES = frozenset({NodeStatus.RUNNING, NodeStatus.PROPOSED})
"""占用父节点预算的状态。"""


class NodeError(ValueError):
    """节点操作不合法。"""


class BudgetExceededError(NodeError):
    """花费超出了节点预算。"""


@dataclass(frozen=True)
class Contract:
    """父节点（或人）交给节点的约定。"""

    target: str
    """要改进的目录（相对项目根）。"""
    metric: str
    budget_usd: float
    constraints: tuple[str, ...] = ()
    """不能做的事，例如「不能修改 evals/」。"""
    rationale: str = ""
    """为什么开这个节点（来自父节点 reasoning 的证据）。"""

    def to_dict(self) -> Payload:
        """转成可写成 YAML 的映射。"""
        return {
            "target": self.target,
            "metric": self.metric,
            "budget_usd": self.budget_usd,
            "constraints": list(self.constraints),
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, obj: Payload, where: str) -> Contract:
        """从映射解析。"""
        constraints = obj.get("constraints", [])
        if not isinstance(constraints, list) or not all(isinstance(c, str) for c in constraints):
            msg = f"{where}: constraints 应为字符串列表"
            raise NodeError(msg)
        return cls(
            target=_str(obj, "target", where),
            metric=_str(obj, "metric", where),
            budget_usd=_money(obj, "budget_usd", where),
            constraints=tuple(c for c in constraints if isinstance(c, str)),
            rationale=_str(obj, "rationale", where, default=""),
        )


@dataclass(frozen=True)
class Node:
    """一个 RSI 节点。"""

    id: str
    parent: str | None
    stage: Stage | None
    """改进父节点的哪个阶段；根节点为 None。"""
    target: str
    harness: str
    """本节点五个阶段的 harness 目录（相对项目根）。"""
    metric: str
    status: NodeStatus
    budget_usd: float
    spent_usd: float = 0.0
    allocated_usd: float = 0.0
    created_by: str = "human"
    created_at: str = field(default_factory=_now)

    @property
    def remaining_usd(self) -> float:
        """还能花或划给子节点的钱。"""
        return self.budget_usd - self.spent_usd - self.allocated_usd

    def to_dict(self) -> Payload:
        """转成可写成 YAML 的映射。"""
        return {
            "id": self.id,
            "parent": self.parent,
            "stage": self.stage.value if self.stage else None,
            "target": self.target,
            "harness": self.harness,
            "metric": self.metric,
            "status": self.status.value,
            "budget_usd": self.budget_usd,
            "spent_usd": self.spent_usd,
            "allocated_usd": self.allocated_usd,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, obj: Payload, where: str) -> Node:
        """从映射解析。"""
        parent = obj.get("parent")
        stage = obj.get("stage")
        if parent is not None and not isinstance(parent, str):
            msg = f"{where}: parent 应为字符串或 null"
            raise NodeError(msg)
        if stage is not None and stage not in {s.value for s in Stage}:
            msg = f"{where}: stage 不是已知阶段: {stage!r}"
            raise NodeError(msg)
        status = _str(obj, "status", where)
        if status not in {s.value for s in NodeStatus}:
            msg = f"{where}: status 不是已知状态: {status!r}"
            raise NodeError(msg)
        return cls(
            id=_str(obj, "id", where),
            parent=parent,
            stage=Stage(stage) if isinstance(stage, str) else None,
            target=_str(obj, "target", where),
            harness=_str(obj, "harness", where),
            metric=_str(obj, "metric", where),
            status=NodeStatus(status),
            budget_usd=_money(obj, "budget_usd", where),
            spent_usd=_money(obj, "spent_usd", where),
            allocated_usd=_money(obj, "allocated_usd", where),
            created_by=_str(obj, "created_by", where),
            created_at=_str(obj, "created_at", where),
        )


@dataclass(frozen=True)
class ChildSummary:
    """父节点能看到的子节点信息。"""

    id: str
    stage: Stage | None
    status: NodeStatus
    budget_usd: float
    spent_usd: float
    summary: Payload
    """子节点自己写的 summary.yaml；还没写时为空。"""


class NodeStore:
    """``<nodes_dir>/`` 下所有节点的读写。

    单进程使用；同一个节点表不要被多个进程同时写。
    """

    def __init__(self, nodes_dir: Path, project_root: Path | None = None) -> None:
        """``project_root`` 默认是 ``nodes_dir`` 的上两级（``<root>/rsi/nodes``）。"""
        self.dir: Path = nodes_dir
        self.project_root: Path = project_root or nodes_dir.parent.parent

    # ---- 读 ----

    def _path(self, node_id: str) -> Path:
        if not _ID_RE.match(node_id):
            msg = f"节点 id 不合法: {node_id!r}"
            raise NodeError(msg)
        return self.dir / node_id

    def exists(self, node_id: str) -> bool:
        """节点是否存在。"""
        return (self._path(node_id) / NODE_FILE).is_file()

    def get(self, node_id: str) -> Node:
        """读取节点。"""
        path = self._path(node_id) / NODE_FILE
        if not path.is_file():
            msg = f"节点不存在: {node_id}"
            raise NodeError(msg)
        return Node.from_dict(_read(path), str(path))

    def contract(self, node_id: str) -> Contract:
        """读取节点的约定。"""
        path = self._path(node_id) / CONTRACT_FILE
        return Contract.from_dict(_read(path), str(path))

    def all(self) -> list[Node]:
        """所有节点，按 id 排序（root 在前）。"""
        if not self.dir.is_dir():
            return []
        ids = sorted(
            (p.name for p in self.dir.iterdir() if (p / NODE_FILE).is_file()),
            key=lambda i: (i != ROOT_ID, i),
        )
        return [self.get(i) for i in ids]

    def children(self, node_id: str) -> list[Node]:
        """直接子节点。"""
        return [n for n in self.all() if n.parent == node_id]

    def summary(self, node_id: str) -> Payload:
        """节点写给父节点的摘要；没写过时为空。"""
        path = self._path(node_id) / SUMMARY_FILE
        return _read(path) if path.is_file() else {}

    def view(self, node_id: str) -> NodeView:
        """节点的局部视野。"""
        return NodeView(self, self.get(node_id))

    # ---- 写 ----

    def _save(self, node: Node) -> None:
        _write(self._path(node.id) / NODE_FILE, node.to_dict())

    def init_root(
        self, *, target: str, metric: str, budget_usd: float, harness: str = "harness"
    ) -> Node:
        """由人创建根节点。"""
        if budget_usd <= 0:
            msg = f"预算应为正数，收到 {budget_usd}"
            raise NodeError(msg)
        if self.exists(ROOT_ID):
            msg = f"根节点已存在: {self._path(ROOT_ID)}"
            raise NodeError(msg)
        node = Node(
            id=ROOT_ID,
            parent=None,
            stage=None,
            target=target,
            harness=harness,
            metric=metric,
            status=NodeStatus.RUNNING,
            budget_usd=budget_usd,
        )
        contract = Contract(target=target, metric=metric, budget_usd=budget_usd)
        _write(self._path(ROOT_ID) / CONTRACT_FILE, contract.to_dict())
        self._save(node)
        return node

    def _next_id(self) -> str:
        nums = [int(n.id[1:]) for n in self.all() if n.id != ROOT_ID]
        return f"n{max(nums, default=0) + 1:04d}"

    def request_child(
        self, parent_id: str, stage: Stage, *, budget_usd: float, rationale: str
    ) -> Node:
        """父节点申请为自己的某个阶段开子节点；结果是等人确认的节点。"""
        parent = self.get(parent_id)
        if parent.status not in ACTIVE_STATUSES:
            msg = f"{parent_id} 状态为 {parent.status}，不能申请子节点"
            raise NodeError(msg)
        if budget_usd <= 0:
            msg = f"子节点预算应为正数，收到 {budget_usd}"
            raise NodeError(msg)
        if budget_usd > parent.remaining_usd:
            msg = f"{parent_id} 剩余预算 ${parent.remaining_usd:.4f}，不够划出 ${budget_usd:.4f}"
            raise BudgetExceededError(msg)
        clash = [
            c.id for c in self.children(parent_id) if c.stage == stage and c.status in OPEN_STATUSES
        ]
        if clash:
            msg = f"{parent_id} 的 {stage.value} 阶段已有未结束的子节点: {', '.join(clash)}"
            raise NodeError(msg)
        node_id = self._next_id()
        target = (Path(parent.harness) / stage.value).as_posix()
        harness = (self._path(node_id) / "harness").relative_to(self.project_root).as_posix()
        contract = Contract(
            target=target,
            metric=f"{stage.value}-replay",
            budget_usd=budget_usd,
            constraints=(
                "只能改 target 目录下的 AGENTS.local.md、scripts/、data/",
                "不能修改 target 的 evals/、回放数据和父节点交给你的指标",
                "改动以提案形式交给父节点，由父节点评估后应用",
            ),
            rationale=rationale,
        )
        node = Node(
            id=node_id,
            parent=parent_id,
            stage=stage,
            target=target,
            harness=harness,
            metric=contract.metric,
            status=NodeStatus.PENDING_APPROVAL,
            budget_usd=budget_usd,
            created_by=parent_id,
        )
        _write(self._path(node_id) / CONTRACT_FILE, contract.to_dict())
        self._save(node)
        return node

    def approve(self, node_id: str) -> Node:
        """人确认开子节点：从父节点划出预算，节点开始运行。"""
        node = self.get(node_id)
        if node.status is not NodeStatus.PENDING_APPROVAL or node.parent is None:
            msg = f"{node_id} 状态为 {node.status}，不是待确认的子节点"
            raise NodeError(msg)
        parent = self.get(node.parent)
        if parent.status not in ACTIVE_STATUSES:
            msg = f"父节点 {parent.id} 状态为 {parent.status}，不能再开子节点"
            raise NodeError(msg)
        if node.budget_usd > parent.remaining_usd:
            msg = (
                f"父节点 {parent.id} 剩余 ${parent.remaining_usd:.4f}，不够 ${node.budget_usd:.4f}"
            )
            raise BudgetExceededError(msg)
        # 子节点自己的五个阶段：基础层 + 子节点的 AGENTS.local.md，让它也能再往下开子节点
        _ = sync(self.project_root / node.harness)
        self._save(replace(parent, allocated_usd=parent.allocated_usd + node.budget_usd))
        node = replace(node, status=NodeStatus.RUNNING)
        self._save(node)
        self._append_ledger(node.id, {"event": "approved", "budget_usd": node.budget_usd})
        return node

    def reject(self, node_id: str, reason: str = "") -> Node:
        """人拒绝开子节点。"""
        node = self.get(node_id)
        if node.status is not NodeStatus.PENDING_APPROVAL:
            msg = f"{node_id} 状态为 {node.status}，不是待确认的子节点"
            raise NodeError(msg)
        node = replace(node, status=NodeStatus.REJECTED)
        self._save(node)
        self._append_ledger(node.id, {"event": "rejected", "reason": reason})
        return node

    def close(self, node_id: str, status: NodeStatus, reason: str = "") -> Node:
        """结束运行中的子节点（retired / done）：花费并入父节点，未花完的预算退回。"""
        if status not in {NodeStatus.RETIRED, NodeStatus.DONE}:
            msg = f"只能把节点结束为 retired 或 done，收到 {status}"
            raise NodeError(msg)
        node = self.get(node_id)
        if node.status not in ACTIVE_STATUSES:
            msg = f"{node_id} 状态为 {node.status}，不在运行"
            raise NodeError(msg)
        still_open = [c.id for c in self.children(node_id) if c.status in OPEN_STATUSES]
        if still_open:
            msg = f"{node_id} 还有未结束的子节点: {', '.join(still_open)}；先结束它们"
            raise NodeError(msg)
        if node.parent is not None:
            parent = self.get(node.parent)
            self._save(
                replace(
                    parent,
                    allocated_usd=max(0.0, parent.allocated_usd - node.budget_usd),
                    spent_usd=parent.spent_usd + node.spent_usd,
                )
            )
        node = replace(node, status=status)
        self._save(node)
        self._append_ledger(node.id, {"event": status.value, "reason": reason})
        return node

    def set_status(self, node_id: str, status: NodeStatus) -> Node:
        """在 running 和 proposed 之间切换。"""
        node = self.get(node_id)
        if node.status not in ACTIVE_STATUSES or status not in ACTIVE_STATUSES:
            msg = f"只能在 running / proposed 之间切换，{node_id} 当前为 {node.status}"
            raise NodeError(msg)
        node = replace(node, status=status)
        self._save(node)
        return node

    def record_spend(self, node_id: str, cost_usd: float, detail: Payload) -> Node:
        """记一笔花费。超出预算时仍然记账，然后抛 :class:`BudgetExceededError`。"""
        if cost_usd < 0:
            msg = f"花费不能为负: {cost_usd}"
            raise NodeError(msg)
        node = self.get(node_id)
        node = replace(node, spent_usd=node.spent_usd + cost_usd)
        self._save(node)
        self._append_ledger(node_id, {"event": "spend", "cost_usd": cost_usd, **detail})
        if node.remaining_usd < 0:
            msg = (
                f"{node_id} 超出预算：预算 ${node.budget_usd:.4f}，"
                f"已花 ${node.spent_usd:.4f}，划给子节点 ${node.allocated_usd:.4f}"
            )
            raise BudgetExceededError(msg)
        return node

    def write_summary(self, node_id: str, summary: Payload) -> None:
        """节点写给父节点的摘要。"""
        _ = self.get(node_id)
        _write(self._path(node_id) / SUMMARY_FILE, summary)

    def _append_ledger(self, node_id: str, entry: Payload) -> None:
        path = self._path(node_id) / LEDGER_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            _ = f.write(dump({"at": _now(), **entry}, explicit_start=True))


@dataclass(frozen=True)
class NodeView:
    """一个节点能看到的全部内容：自己、父节点给的约定、子节点的摘要。

    不提供读取父节点本身、祖先节点或孙节点的方法（ADR 0007「局部视野」）。
    """

    store: NodeStore
    node: Node

    @property
    def contract(self) -> Contract:
        """父节点（或人）给的约定。"""
        return self.store.contract(self.node.id)

    def children(self) -> list[ChildSummary]:
        """子节点的摘要。"""
        return [
            ChildSummary(
                id=c.id,
                stage=c.stage,
                status=c.status,
                budget_usd=c.budget_usd,
                spent_usd=c.spent_usd,
                summary=self.store.summary(c.id),
            )
            for c in self.store.children(self.node.id)
        ]

    def to_dict(self) -> Payload:
        """给阶段看的视野（可放进 payload）。"""
        children: list[Value] = [
            {
                "id": c.id,
                "stage": c.stage.value if c.stage else None,
                "status": c.status.value,
                "budget_usd": c.budget_usd,
                "spent_usd": c.spent_usd,
                "summary": c.summary,
            }
            for c in self.children()
        ]
        return {
            "id": self.node.id,
            "status": self.node.status.value,
            "remaining_usd": self.node.remaining_usd,
            "contract": self.contract.to_dict(),
            "children": children,
        }


def _read(path: Path) -> Payload:
    try:
        return load_mapping(path.read_text(encoding="utf-8"), where=str(path))
    except (OSError, YAMLError) as e:
        msg = f"读不了 {path}: {e}"
        raise NodeError(msg) from e


def _write(path: Path, obj: Payload) -> None:
    """先写临时文件再替换，避免写一半的文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    _ = tmp.write_text(dump(obj), encoding="utf-8")
    _ = tmp.replace(path)


def _str(obj: Payload, key: str, where: str, default: str | None = None) -> str:
    value = obj.get(key, default)
    if not isinstance(value, str):
        msg = f"{where}: {key} 应为字符串，实际为 {value!r}"
        raise NodeError(msg)
    return value


def _money(obj: Payload, key: str, where: str) -> float:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        msg = f"{where}: {key} 应为非负数，实际为 {value!r}"
        raise NodeError(msg)
    return float(value)
