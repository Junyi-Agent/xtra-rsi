"""toy profile：用 loop 在一维函数上找最优点，并在 feedback 里自适应调学习率。

它不是真实任务，作用是：
1. 端到端跑通 loop，给测试和 CLI 一个确定性的 profile；
2. 示范每个阶段的职责，并让每个阶段的 ablation 都有可测的效果——
   去掉任何一个阶段，最终得分都会变差。

环境：``score(x) = -(x - TARGET)**2``，越大越好。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xtra_rsi.core import Profile, Stage, get_float

if TYPE_CHECKING:
    from collections.abc import Sequence

    from xtra_rsi.core import IterationRecord, JSONObject, StageContext

TARGET = 0.7
PROBE_EPS = 1e-3
MAX_STEP = 0.5
LR_UP = 1.2
LR_DOWN = 0.5


def score(x: float) -> float:
    """环境的打分函数。"""
    return -((x - TARGET) ** 2)


def observe(ctx: StageContext) -> JSONObject:
    """测量当前位置和附近一点的得分。"""
    x = get_float(ctx.upstream, "x", 0.0)
    return {
        "x": x,
        "lr": get_float(ctx.upstream, "lr", 0.0),
        "score": score(x),
        "probe": score(x + PROBE_EPS),
    }


def reasoning(ctx: StageContext) -> JSONObject:
    """由观测估计梯度。"""
    up = ctx.upstream
    s, probe = up.get("score"), up.get("probe")
    grad = (
        (float(probe) - float(s)) / PROBE_EPS
        if isinstance(s, float) and isinstance(probe, float)
        else 0.0
    )
    return {"x": get_float(up, "x", 0.0), "lr": get_float(up, "lr", 0.0), "grad": grad}


def decision(ctx: StageContext) -> JSONObject:
    """按学习率决定步长，并限制最大步长。"""
    up = ctx.upstream
    lr = get_float(up, "lr", 0.0)
    step = max(-MAX_STEP, min(MAX_STEP, lr * get_float(up, "grad", 0.0)))
    return {"x": get_float(up, "x", 0.0), "lr": lr, "step": step}


def action(ctx: StageContext) -> JSONObject:
    """执行：移动到新位置。"""
    up = ctx.upstream
    return {
        "x": get_float(up, "x", 0.0) + get_float(up, "step", 0.0),
        "lr": get_float(up, "lr", 0.0),
        "prev_x": get_float(up, "x", 0.0),
    }


def feedback(ctx: StageContext) -> JSONObject:
    """评估结果并自我改进：变好就加大学习率，变差就减小。"""
    up = ctx.upstream
    x = get_float(up, "x", 0.0)
    new, old = score(x), score(get_float(up, "prev_x", x))
    improved = new > old
    lr = get_float(up, "lr", 0.0) * (LR_UP if improved else LR_DOWN)
    return {"x": x, "lr": lr, "score": new, "improved": improved}


def final_score(records: Sequence[IterationRecord]) -> float:
    """最后一轮结束时所处位置的得分（不依赖 feedback 是否存在）。"""
    if not records:
        return score(0.0)
    return score(get_float(records[-1].last, "x", 0.0))


def profile() -> Profile:
    """Entry point：``xtra_rsi.profiles`` / ``toy``。"""
    return Profile(
        name="toy",
        stages={
            Stage.OBSERVE: observe,
            Stage.REASONING: reasoning,
            Stage.DECISION: decision,
            Stage.ACTION: action,
            Stage.FEEDBACK: feedback,
        },
        seed={"x": 0.0, "lr": 0.05},
        metrics={"final_score": final_score},
    )
