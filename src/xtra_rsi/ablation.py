"""阶段 ablation：把某个阶段换成直通（identity），比较指标，判断该阶段是否有效。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from xtra_rsi.core import JSON, JSONObject, Profile, Stage, StageContext, run_loop
from xtra_rsi.registry import load_profile

if TYPE_CHECKING:
    from pathlib import Path


def identity(ctx: StageContext) -> JSONObject:
    """直通：原样输出上游 payload，相当于去掉这个阶段。"""
    return dict(ctx.upstream)


def ablate(profile: Profile, stage: Stage) -> Profile:
    """返回去掉 ``stage`` 的 profile。"""
    return profile.with_stage(stage, identity, suffix=f"-no-{stage.value}")


@dataclass(frozen=True)
class AblationCase:
    """``harness/<stage>/evals/cases.json`` 里的一条用例。"""

    name: str
    profile: str
    metric: str
    iterations: int
    min_delta: float
    """full - ablated 至少要达到的差值；指标越大越好。"""

    @classmethod
    def from_json(cls, obj: JSONObject) -> AblationCase:
        """从 JSON 对象解析，字段缺失或类型错误时抛 ValueError。"""
        try:
            name, profile, metric = (_req(obj, k, str) for k in ("name", "profile", "metric"))
            iterations = _req(obj, "iterations", int)
            min_delta = float(_req(obj, "min_delta", (int, float)))
        except (KeyError, TypeError) as e:
            msg = f"ablation case 不合法: {e}; 内容: {obj}"
            raise ValueError(msg) from e
        return cls(name, profile, metric, iterations, min_delta)


def _req[T](obj: JSONObject, key: str, typ: type[T] | tuple[type, ...]) -> T:
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, typ):
        msg = f"{key!r} 类型应为 {typ}，实际为 {type(value).__name__}"
        raise TypeError(msg)
    return cast("T", value)


def load_cases(path: Path) -> list[AblationCase]:
    """读取用例文件（JSON 数组）。"""
    raw = cast("JSON", json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw, list):
        msg = f"{path}: 顶层应为数组"
        raise TypeError(msg)
    cases: list[AblationCase] = []
    for item in raw:
        if not isinstance(item, dict):
            msg = f"{path}: 每条用例应为对象"
            raise TypeError(msg)
        cases.append(AblationCase.from_json(item))
    return cases


@dataclass(frozen=True)
class AblationResult:
    """一次 ablation 的结果。"""

    stage: Stage
    case: AblationCase
    full: float
    ablated: float

    @property
    def delta(self) -> float:
        """完整 profile 比去掉阶段后好多少。"""
        return self.full - self.ablated

    @property
    def passed(self) -> bool:
        """阶段是否被证明有效。"""
        return self.delta >= self.case.min_delta

    def to_json(self) -> JSONObject:
        """转成 JSON 对象，供 CLI 输出。"""
        return {
            "stage": self.stage.value,
            "case": self.case.name,
            "profile": self.case.profile,
            "metric": self.case.metric,
            "full": self.full,
            "ablated": self.ablated,
            "delta": self.delta,
            "min_delta": self.case.min_delta,
            "passed": self.passed,
        }


def run_ablation(stage: Stage, case: AblationCase) -> AblationResult:
    """分别跑完整 profile 和去掉 ``stage`` 的 profile，比较 ``case.metric``。"""
    profile = load_profile(case.profile)
    metric = profile.metrics.get(case.metric)
    if metric is None:
        msg = f"profile {profile.name!r} 没有指标 {case.metric!r}，已有: {sorted(profile.metrics)}"
        raise KeyError(msg)
    full = metric(run_loop(profile, iterations=case.iterations))
    ablated = metric(run_loop(ablate(profile, stage), iterations=case.iterations))
    return AblationResult(stage=stage, case=case, full=full, ablated=ablated)
