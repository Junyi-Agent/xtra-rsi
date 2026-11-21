"""RSI loop 的核心类型：阶段、阶段间传递的 payload、profile 和 loop 本身。

一次迭代按 observe → reasoning → decision → action → feedback 依次运行，
每个阶段拿上一阶段的输出作为 ``upstream``；feedback 的输出成为下一次迭代
observe 的 ``upstream``，形成闭环。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

type JSON = str | int | float | bool | list[JSON] | dict[str, JSON] | None
type JSONObject = dict[str, JSON]


class Stage(StrEnum):
    """RSI loop 的五个阶段，定义顺序即运行顺序。"""

    OBSERVE = "observe"
    REASONING = "reasoning"
    DECISION = "decision"
    ACTION = "action"
    FEEDBACK = "feedback"


STAGE_ORDER: tuple[Stage, ...] = tuple(Stage)


@dataclass(frozen=True)
class StageContext:
    """传给阶段实现的全部输入。"""

    stage: Stage
    iteration: int
    upstream: JSONObject
    """上一阶段的输出；observe 拿到的是上一轮 feedback 的输出（第 0 轮是 seed）。"""
    history: tuple[IterationRecord, ...] = ()
    """已完成的迭代，供需要跨轮信息的阶段使用。"""


class StageFn(Protocol):
    """阶段实现：读 context，返回 JSON 对象。必须是纯函数式的输入输出，副作用自行负责。"""

    def __call__(self, ctx: StageContext, /) -> JSONObject:
        """运行阶段。"""
        ...


type Metric = Callable[[Sequence[IterationRecord]], float]


@dataclass(frozen=True)
class IterationRecord:
    """一次完整迭代里每个阶段的输出。"""

    iteration: int
    outputs: Mapping[Stage, JSONObject]

    @property
    def last(self) -> JSONObject:
        """本轮最后一个阶段（feedback）的输出。"""
        return self.outputs[STAGE_ORDER[-1]]

    def to_json(self) -> JSONObject:
        """转成可序列化的 JSON 对象。"""
        return {
            "iteration": self.iteration,
            "outputs": {stage.value: dict(self.outputs[stage]) for stage in STAGE_ORDER},
        }


class ProfileError(ValueError):
    """profile 不完整或不合法。"""


@dataclass(frozen=True)
class Profile:
    """一组阶段实现加上评估它们用的指标。下游项目通过 entry point 注册自己的 profile。"""

    name: str
    stages: Mapping[Stage, StageFn]
    seed: JSONObject = field(default_factory=dict[str, "JSON"])
    """第 0 轮 observe 的 upstream。"""
    metrics: Mapping[str, Metric] = field(default_factory=dict[str, Metric])

    def __post_init__(self) -> None:
        """检查五个阶段都有实现。"""
        missing = [s.value for s in STAGE_ORDER if s not in self.stages]
        if missing:
            msg = f"profile {self.name!r} 缺少阶段实现: {', '.join(missing)}"
            raise ProfileError(msg)

    def with_stage(self, stage: Stage, fn: StageFn, *, suffix: str = "") -> Profile:
        """返回替换了某个阶段实现的新 profile（ablation 用）。"""
        stages = dict(self.stages)
        stages[stage] = fn
        return replace(self, name=self.name + suffix, stages=stages)


class StageOutputError(TypeError):
    """阶段返回的不是 JSON 对象。"""


def run_loop(
    profile: Profile,
    *,
    iterations: int,
    run_dir: Path | None = None,
    stop_key: str = "stop",
) -> list[IterationRecord]:
    """运行 loop。

    ``run_dir`` 不为 None 时把每个阶段的输出写到
    ``run_dir/iter-<n>/<stage>.json``。feedback 输出里 ``stop_key`` 为真时提前结束。
    """
    if iterations < 0:
        msg = f"iterations 必须 >= 0，收到 {iterations}"
        raise ValueError(msg)
    records: list[IterationRecord] = []
    upstream: JSONObject = dict(profile.seed)
    for i in range(iterations):
        outputs: dict[Stage, JSONObject] = {}
        for stage in STAGE_ORDER:
            ctx = StageContext(stage=stage, iteration=i, upstream=upstream, history=tuple(records))
            out = profile.stages[stage](ctx)
            if not isinstance(out, dict):  # pyright: ignore[reportUnnecessaryIsInstance] -- 运行时防御第三方实现
                msg = f"{profile.name}.{stage.value} 返回了 {type(out).__name__}，应为 dict"
                raise StageOutputError(msg)
            outputs[stage] = out
            if run_dir is not None:
                _write_json(run_dir / f"iter-{i:04d}" / f"{stage.value}.json", out)
            upstream = out
        record = IterationRecord(iteration=i, outputs=outputs)
        records.append(record)
        if record.last.get(stop_key) is True:
            break
    return records


def _write_json(path: Path, obj: JSONObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_float(obj: Mapping[str, JSON], key: str, default: float) -> float:
    """从 payload 里取数值，缺失或类型不对时返回 default。"""
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value)
