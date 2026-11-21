"""通过 entry point ``xtra_rsi.profiles`` 发现 profile。

下游项目在自己的 pyproject.toml 里写::

    [project.entry-points."xtra_rsi.profiles"]
    myproj = "myproj.rsi:profile"

``profile`` 是一个无参函数，返回 :class:`xtra_rsi.core.Profile`。
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import cast

from xtra_rsi.core import Profile, ProfileError

GROUP = "xtra_rsi.profiles"


def available_profiles() -> list[str]:
    """列出已安装的 profile 名。"""
    return sorted(ep.name for ep in entry_points(group=GROUP))


def load_profile(name: str) -> Profile:
    """按名字加载 profile。"""
    matches = entry_points(group=GROUP, name=name)
    if not matches:
        msg = f"找不到 profile {name!r}，已安装: {', '.join(available_profiles()) or '无'}"
        raise ProfileError(msg)
    factory = cast("object", next(iter(matches)).load())
    if not callable(factory):
        msg = f"profile {name!r} 的 entry point 不是可调用对象"
        raise ProfileError(msg)
    profile: object = factory()
    if not isinstance(profile, Profile):
        msg = f"profile {name!r} 的工厂函数返回了 {type(profile).__name__}，应为 Profile"
        raise ProfileError(msg)
    return profile
