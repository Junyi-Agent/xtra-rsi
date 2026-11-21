"""阶段数据的类型。阶段之间、阶段与外部 agent 之间传递的都是 :data:`Payload`。"""

type Value = str | int | float | bool | list[Value] | dict[str, Value] | None
"""阶段数据里允许出现的值（可以无损写成 YAML）。"""

type Payload = dict[str, Value]
"""阶段之间传递的数据：键为字符串的映射。"""
