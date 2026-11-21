"""RSI loop 的核心类型：阶段、阶段间传递的 payload、profile 和 loop 本身。

一次迭代按 observe → reasoning → decision → action → feedback 依次运行，
每个阶段拿上一阶段的输出作为 ``upstream``；feedback 的输出成为下一次迭代
observe 的 ``upstream``，形成闭环。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from xtra_rsi.yamlio import dump as yaml_dump

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from xtra_rsi.payload import Payload, Value


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
    upstream: Payload
    """上一阶段的输出；observe 拿到的是上一轮 feedback 的输出（第 0 轮是 seed）。"""
    history: tuple[IterationRecord, ...] = ()
    """已完成的迭代，供需要跨轮信息的阶段使用。"""


class StageFn(Protocol):
    """阶段实现：读 context，返回映射。必须是纯函数式的输入输出，副作用自行负责。"""

    def __call__(self, ctx: StageContext, /) -> Payload:
        """运行阶段。"""
        ...


type Metric = Callable[[Sequence[IterationRecord]], float]


@dataclass(frozen=True)
class IterationRecord:
    """一次完整迭代里每个阶段的输出。"""

    iteration: int
    outputs: Mapping[Stage, Payload]

    @property
    def last(self) -> Payload:
        """本轮最后一个阶段（feedback）的输出。"""
        return self.outputs[STAGE_ORDER[-1]]

    def to_dict(self) -> Payload:
        """转成可序列化的映射。"""
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
    seed: Payload = field(default_factory=dict[str, "Value"])
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

    def with_seed(self, seed: Payload) -> Profile:
        """返回换了 seed 的新 profile。"""
        return replace(self, seed=seed)


class StopLoop(Exception):  # noqa: N818 -- 是控制流信号，不是错误
    """由 ``on_output`` 抛出，让 loop 在当前阶段之后停止（例如预算用完）。

    ``payload`` 不为 None 时用它作为该阶段的输出（hook 已经改写过的版本）。
    """

    def __init__(self, reason: str, payload: Payload | None = None) -> None:
        """记录停止原因和改写后的输出。"""
        super().__init__(reason)
        self.reason: str = reason
        self.payload: Payload | None = payload


type OutputHook = Callable[[StageContext, Payload], Payload]
"""阶段输出写盘、传给下一阶段之前调用；可以改写输出，或抛 :class:`StopLoop` 停止 loop。"""


class StageOutputError(TypeError):
    """阶段返回的不是映射。"""


def run_loop(
    profile: Profile,
    *,
    iterations: int,
    run_dir: Path | None = None,
    stop_key: str = "stop",
    on_output: OutputHook | None = None,
) -> list[IterationRecord]:
    """运行 loop。

    ``run_dir`` 不为 None 时把每个阶段的输出写到
    ``run_dir/iter-<n>/<stage>.yaml``。feedback 输出里 ``stop_key`` 为真时提前结束。

    ``on_output`` 不为 None 时，每个阶段的输出先经过它再写盘和传给下一阶段。它抛出
    :class:`StopLoop` 时 loop 停止，返回已完成的迭代；未完成那一轮的输出已写盘但不在返回值里。
    """
    if iterations < 0:
        msg = f"iterations 必须 >= 0，收到 {iterations}"
        raise ValueError(msg)
    records: list[IterationRecord] = []
    upstream: Payload = dict(profile.seed)
    for i in range(iterations):
        outputs: dict[Stage, Payload] = {}
        for stage in STAGE_ORDER:
            ctx = StageContext(stage=stage, iteration=i, upstream=upstream, history=tuple(records))
            out = profile.stages[stage](ctx)
            if not isinstance(out, dict):  # pyright: ignore[reportUnnecessaryIsInstance] -- 运行时防御第三方实现
                msg = f"{profile.name}.{stage.value} 返回了 {type(out).__name__}，应为 dict"
                raise StageOutputError(msg)
            stop = False
            if on_output is not None:
                try:
                    out = on_output(ctx, out)
                except StopLoop as e:
                    stop = True
                    out = e.payload if e.payload is not None else out
            outputs[stage] = out
            if run_dir is not None:
                _write_yaml(run_dir / f"iter-{i:04d}" / f"{stage.value}.yaml", out)
            upstream = out
            if stop:
                return records
        record = IterationRecord(iteration=i, outputs=outputs)
        records.append(record)
        if record.last.get(stop_key) is True:
            break
    return records


def _write_yaml(path: Path, obj: Payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(yaml_dump(obj), encoding="utf-8")


def get_float(obj: Mapping[str, Value], key: str, default: float) -> float:
    """从 payload 里取数值，缺失或类型不对时返回 default。"""
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value)
