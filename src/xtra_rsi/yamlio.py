"""阶段数据的 YAML 读写。

阶段之间、阶段与外部 agent 之间、写到磁盘的数据都用 YAML。解析基于 PyYAML 的
safe loader，但标量按 YAML 1.2 core schema 解析，避免 LLM 写的值悄悄变类型：

- 布尔值只认 ``true`` / ``false``（及其大小写变体）；``yes`` / ``no`` / ``on`` /
  ``off`` 是字符串；
- 整数只认十进制、``0o`` 八进制、``0x`` 十六进制：``010`` 是 10，``1:30`` 是字符串
  （YAML 1.1 分别读成 8 和 90）；
- 浮点数按 YAML 1.2：``1e3`` 是 1000.0；
- ``2026-11-21`` 这类值是字符串，不转成 ``date``。

解析结果只含 str / int / float / bool / None / list / 键为 str 的 dict。
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, cast

import yaml

if TYPE_CHECKING:
    from xtra_rsi.payload import Payload, Value

_BOOL_TAG = "tag:yaml.org,2002:bool"
_INT_TAG = "tag:yaml.org,2002:int"
_FLOAT_TAG = "tag:yaml.org,2002:float"
_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
_REPLACED = {_BOOL_TAG, _INT_TAG, _FLOAT_TAG, _TIMESTAMP_TAG}

# YAML 1.2 core schema（https://yaml.org/spec/1.2.2/#1032-tag-resolution）
_BOOL_RE = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
_INT_RE = re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$")
_FLOAT_RES = (
    re.compile(r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$"),
    re.compile(r"^[-+]?\.(?:inf|Inf|INF)$"),
    re.compile(r"^\.(?:nan|NaN|NAN)$"),
)
_NUMBER_FIRST = list("-+0123456789.")


class _Loader(yaml.SafeLoader):
    """SafeLoader，标量按 YAML 1.2 core schema 解析，不识别 timestamp。"""


def _construct_int(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> int:
    text = loader.construct_scalar(node)
    # base=0 才识别 0o / 0x 前缀；十进制必须显式 base=10，否则 "010" 会报错
    return int(text, 0) if text.startswith(("0o", "0x")) else int(text, 10)


def _construct_float(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> float:
    text = loader.construct_scalar(node).lower()
    if text.endswith(".inf"):
        return -math.inf if text.startswith("-") else math.inf
    if text == ".nan":
        return math.nan
    return float(text)


_SAFE_RESOLVERS = cast(
    "dict[str | None, list[tuple[str, re.Pattern[str]]]]",
    yaml.SafeLoader.yaml_implicit_resolvers,
)
_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag not in _REPLACED]
    for first, resolvers in _SAFE_RESOLVERS.items()
}
# 先 int 后 float：float 的正则也能匹配纯整数
for _tag, _regexp, _first in (
    (_BOOL_TAG, _BOOL_RE, list("tTfF")),
    (_INT_TAG, _INT_RE, _NUMBER_FIRST),
    *((_FLOAT_TAG, regexp, _NUMBER_FIRST) for regexp in _FLOAT_RES),
):
    _Loader.add_implicit_resolver(_tag, _regexp, _first)  # pyright: ignore[reportUnknownMemberType] -- types-PyYAML 的签名含未标注参数
_Loader.add_constructor(_INT_TAG, _construct_int)
_Loader.add_constructor(_FLOAT_TAG, _construct_float)


class _Dumper(yaml.SafeDumper):
    """SafeDumper，多行字符串用块状 ``|`` 写出，便于人和 agent 阅读。"""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)  # pyright: ignore[reportUnknownMemberType] -- types-PyYAML 的签名含未标注参数


_Dumper.add_representer(str, _represent_str)


class YAMLError(ValueError):
    """YAML 解析失败或内容不是期望的结构。"""


def dump(obj: Value, *, explicit_start: bool = False) -> str:
    """序列化成块状 YAML，保留键顺序和中文。"""
    return yaml.dump(
        obj,
        Dumper=_Dumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        explicit_start=explicit_start,
    )


def loads(text: str, *, where: str = "<yaml>") -> Value:
    """解析一个 YAML 文档。"""
    try:
        raw = cast("object", yaml.load(text, Loader=_Loader))  # noqa: S506 -- _Loader 继承自 SafeLoader
    except yaml.YAMLError as e:
        msg = f"{where}: 不是合法的 YAML: {e}"
        raise YAMLError(msg) from e
    return _check(raw, where)


def load_mapping(text: str, *, where: str = "<yaml>") -> Payload:
    """解析一个顶层为映射的 YAML 文档。"""
    value = loads(text, where=where)
    if not isinstance(value, dict):
        msg = f"{where}: 顶层应为映射，实际为 {type(value).__name__}"
        raise YAMLError(msg)
    return value


def _check(raw: object, where: str) -> Value:
    if raw is None or isinstance(raw, str | bool | int | float):
        return raw
    if isinstance(raw, list):
        items = cast("list[object]", raw)
        return [_check(v, f"{where}[{i}]") for i, v in enumerate(items)]
    if isinstance(raw, dict):
        out: Payload = {}
        for k, v in cast("dict[object, object]", raw).items():
            if not isinstance(k, str):
                msg = f"{where}: 映射的键应为字符串，实际为 {k!r}（{type(k).__name__}），需要加引号"
                raise YAMLError(msg)
            out[k] = _check(v, f"{where}.{k}")
        return out
    msg = f"{where}: 不支持的 YAML 类型 {type(raw).__name__}"
    raise YAMLError(msg)
