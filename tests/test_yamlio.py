from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

from xtra_rsi.yamlio import YAMLError, dump, load_mapping, loads

if TYPE_CHECKING:
    from xtra_rsi.payload import Payload


@pytest.mark.parametrize("word", ["yes", "no", "on", "off", "y", "n", "Yes", "NO"])
def test_yaml11_booleans_stay_strings(word: str) -> None:
    assert loads(f"v: {word}") == {"v": word}


@pytest.mark.parametrize(("word", "want"), [("true", True), ("False", False), ("TRUE", True)])
def test_true_false_are_booleans(word: str, *, want: bool) -> None:
    assert loads(f"v: {word}") == {"v": want}


def test_dates_stay_strings() -> None:
    assert loads("d: 2026-11-21\nt: 2026-11-21T07:00:00Z") == {
        "d": "2026-11-21",
        "t": "2026-11-21T07:00:00Z",
    }


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("010", 10),
        ("0o17", 15),
        ("0x1f", 31),
        ("-3", -3),
        ("1e3", 1000.0),
        ("1.0e3", 1000.0),
        (".5", 0.5),
        ("1:30", "1:30"),
        ("08", 8),
        ("1_000", "1_000"),
        ("0b11", "0b11"),
        ("1.2.3", "1.2.3"),
    ],
)
def test_yaml12_numbers(text: str, want: object) -> None:
    value = loads(f"v: {text}")
    assert isinstance(value, dict)
    assert value["v"] == want
    assert type(value["v"]) is type(want)


def test_special_floats() -> None:
    value = loads("a: .inf\nb: -.Inf\nc: .nan")
    assert isinstance(value, dict)
    a, b, c = value["a"], value["b"], value["c"]
    assert isinstance(a, float)
    assert isinstance(b, float)
    assert isinstance(c, float)
    assert a == math.inf
    assert b == -math.inf
    assert math.isnan(c)


def test_numbers_and_null() -> None:
    assert loads("i: 3\nf: 0.5\ne: 1.0e-3\nz: null\nt: ~") == {
        "i": 3,
        "f": 0.5,
        "e": 0.001,
        "z": None,
        "t": None,
    }


def test_round_trip_keeps_order_unicode_and_multiline() -> None:
    obj: Payload = {"z": 1, "a": "中文", "md": "# 标题\n\n- 一\n- 二\n", "nested": [{"k": True}]}
    text = dump(obj)
    assert text.index("z:") < text.index("a:")
    assert "中文" in text
    assert "md: |" in text
    assert loads(text) == obj


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("1: a", "键应为字符串"),
        ("v: !!binary aGk=", "不支持的 YAML 类型"),
        ("v: !!python/object:os.system x", "不是合法的 YAML"),
        ("a: [1", "不是合法的 YAML"),
    ],
)
def test_rejects_non_plain_data(text: str, match: str) -> None:
    with pytest.raises(YAMLError, match=match):
        _ = loads(text)


def test_load_mapping_requires_mapping() -> None:
    with pytest.raises(YAMLError, match="顶层应为映射"):
        _ = load_mapping("- 1\n- 2")
