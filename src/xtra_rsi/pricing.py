"""把 token 用量换算成美元（ADR 0007：预算按金额）。

阶段的外部命令在输出里用保留键 ``_usage`` 报告用量（见 :mod:`xtra_rsi.command_stage`），
每条用量要么直接给 ``cost_usd``，要么给 ``model`` 和 token 数，由单价表换算。

默认单价表只收录 Anthropic 官方公布的价格（2026-11 取自 claude-api 文档），
未公布的项（例如部分模型的缓存写入价）留空：用量里出现这类 token 而单价表没有对应价格时
报错，不猜。下游用 ``rsi`` 的 ``--prices`` 或 :func:`load_prices` 传入 YAML 覆盖或补全。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from xtra_rsi.yamlio import load_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from xtra_rsi.payload import Value

_PER_TOKEN = 1_000_000


@dataclass(frozen=True)
class ModelPrice:
    """一个模型每百万 token 的美元单价；None 表示未知。"""

    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None


DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-fable-5-2": ModelPrice(8.00, 40.00, cache_read=0.20),
    "claude-fable-5-1": ModelPrice(10.00, 50.00),
    "claude-fable-5": ModelPrice(10.00, 50.00),
    "claude-opus-5-5": ModelPrice(4.00, 20.00, cache_read=0.20),
    "claude-opus-5": ModelPrice(5.00, 25.00),
    "claude-opus-4-8": ModelPrice(5.00, 25.00),
    "claude-opus-4-7": ModelPrice(5.00, 25.00),
    "claude-opus-4-6": ModelPrice(5.00, 25.00),
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00, cache_read=0.10),
    "claude-sonnet-5": ModelPrice(2.00, 10.00),
    "claude-sonnet-4-6": ModelPrice(3.00, 15.00),
    "claude-haiku-5-5": ModelPrice(0.10, 0.50),  # 100K token 以内的提示；更长的提示价格不同
    "claude-haiku-4-5": ModelPrice(1.00, 5.00),
}


class PricingError(ValueError):
    """用量无法换算成金额。"""


def load_prices(path: Path | None = None) -> dict[str, ModelPrice]:
    """默认单价表，再用 ``path``（YAML：模型名 → input/output/cache_read/cache_write）覆盖。"""
    prices = dict(DEFAULT_PRICES)
    if path is None:
        return prices
    raw = load_mapping(path.read_text(encoding="utf-8"), where=str(path))
    for model, entry in raw.items():
        if not isinstance(entry, dict):
            msg = f"{path}: {model} 应为映射"
            raise PricingError(msg)
        prices[model] = ModelPrice(
            input=_price(entry, "input", f"{path}: {model}") or 0.0,
            output=_price(entry, "output", f"{path}: {model}") or 0.0,
            cache_read=_price(entry, "cache_read", f"{path}: {model}"),
            cache_write=_price(entry, "cache_write", f"{path}: {model}"),
        )
    return prices


def _price(entry: Mapping[str, Value], key: str, where: str) -> float | None:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        msg = f"{where}.{key} 应为非负数，实际为 {value!r}"
        raise PricingError(msg)
    return float(value)


def _tokens(usage: Mapping[str, Value], key: str) -> int:
    value = usage.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = f"_usage.{key} 应为非负整数，实际为 {value!r}"
        raise PricingError(msg)
    return value


def usage_cost(usage: Mapping[str, Value], prices: Mapping[str, ModelPrice]) -> float:
    """一条用量的美元金额。

    ``cost_usd`` 存在时直接采用；否则按 ``model`` 和 ``input_tokens`` / ``output_tokens`` /
    ``cache_read_input_tokens`` / ``cache_creation_input_tokens`` 换算。
    """
    cost = usage.get("cost_usd")
    if cost is not None:
        if isinstance(cost, bool) or not isinstance(cost, int | float) or cost < 0:
            msg = f"_usage.cost_usd 应为非负数，实际为 {cost!r}"
            raise PricingError(msg)
        return float(cost)
    model = usage.get("model")
    if not isinstance(model, str):
        msg = f"_usage 需要 cost_usd 或 model，实际为 {dict(usage)}"
        raise PricingError(msg)
    price = prices.get(model)
    if price is None:
        msg = f"单价表里没有模型 {model!r}，用 --prices 补充"
        raise PricingError(msg)
    total = _tokens(usage, "input_tokens") * price.input
    total += _tokens(usage, "output_tokens") * price.output
    for key, unit, name in (
        ("cache_read_input_tokens", price.cache_read, "cache_read"),
        ("cache_creation_input_tokens", price.cache_write, "cache_write"),
    ):
        n = _tokens(usage, key)
        if n == 0:
            continue
        if unit is None:
            msg = f"{model} 没有 {name} 单价，但用量里有 {n} 个 {key}；用 --prices 补充"
            raise PricingError(msg)
        total += n * unit
    return total / _PER_TOKEN


def usages_of(value: Value) -> list[Mapping[str, Value]]:
    """``_usage`` 可以是一条映射或映射列表。"""
    items = value if isinstance(value, list) else [value]
    out: list[Mapping[str, Value]] = []
    for item in items:
        if not isinstance(item, dict):
            msg = f"_usage 的每一项应为映射，实际为 {item!r}"
            raise PricingError(msg)
        out.append(item)
    return out
