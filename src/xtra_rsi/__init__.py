"""xtra-rsi：Recursive Self-Improvement agent 框架。"""

from xtra_rsi.core import (
    STAGE_ORDER,
    IterationRecord,
    Metric,
    Profile,
    ProfileError,
    Stage,
    StageContext,
    StageFn,
    StopLoop,
    run_loop,
)
from xtra_rsi.payload import Payload, Value

__all__ = [
    "STAGE_ORDER",
    "IterationRecord",
    "Metric",
    "Payload",
    "Profile",
    "ProfileError",
    "Stage",
    "StageContext",
    "StageFn",
    "StopLoop",
    "Value",
    "run_loop",
]
