"""工具参数 JSON Schema 的自动生成与最小校验（R-03）。

Day 17 让业务工具声明手写 ``parameters`` JSON Schema 并随工具定义下发给
模型；本模块把"手写字典"升级为"声明即校验"：

- ``generate_parameters_schema`` 从 Pydantic v2 参数模型（优先）或普通函数
  签名自动生成参数 JSON Schema，避免手写字典与业务校验分叉；
- ``validate_parameters`` 用项目内最小 JSON Schema 子集校验参数字典，
  供 ``ToolRegistry.execute`` 在业务校验之前拒绝非法参数，不新增
  ``jsonschema`` 依赖。

最小校验器只覆盖本项目工具声明实际使用的子集：``type``（object / string /
integer / number / boolean / array / null）、``properties``、``required``、
``additionalProperties``（仅 ``False`` 生效）、``enum``、``minLength`` /
``maxLength``、``minimum`` / ``maximum``、``pattern`` 与数组 ``items``。
未知关键字按 JSON Schema 语义忽略，不阻断合法参数。
"""

from __future__ import annotations

import inspect
import json
import re
import types
import typing
from collections.abc import Callable
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from self_react.models import JsonObject

_PY_TYPE_TO_JSON_TYPE: dict[type[Any], str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    type(None): "null",
}
"""轻量转换支持的内建类型到 JSON Schema 类型的映射。"""

_JSON_TYPES = frozenset(
    {"null", "boolean", "object", "array", "number", "string", "integer"}
)
"""JSON Schema 的合法 type 关键字值。"""


def _annotation_to_json_type(annotation: Any) -> str:
    """把函数参数类型标注映射成 JSON Schema 类型；无法表达时显式报错。"""

    if annotation is inspect.Parameter.empty:
        raise ValueError("参数缺少类型标注，请补充类型标注或改用 Pydantic 参数模型")

    origin = get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        non_null = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(non_null) == 1:
            return _annotation_to_json_type(non_null[0])
        raise ValueError(f"不支持的参数类型标注：{annotation}")

    if annotation in _PY_TYPE_TO_JSON_TYPE:
        return _PY_TYPE_TO_JSON_TYPE[annotation]
    raise ValueError(f"不支持的参数类型标注：{annotation}")


def _normalize_model_schema(raw: dict[str, Any]) -> JsonObject:
    """把 Pydantic v2 生成的 Schema 规范成工具层参数形状。

    工具层参数形状固定为 ``{"type": "object", "properties": ...,
    "required": [...], "additionalProperties": False}``：去掉 Pydantic 的
    ``title`` 装饰字段，并强制拒绝多余属性（与业务工具"不支持的参数"规则
    一致）。嵌套模型会产生 ``$defs``，超出最小校验范围，直接拒绝。
    """

    if "$defs" in raw:
        raise ValueError("暂不支持包含嵌套类型的参数模型，请保持参数模型为扁平结构")

    properties = raw.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = raw.get("required")
    if not isinstance(required, list):
        required = []
    required = [
        name for name in required if isinstance(name, str) and name in properties
    ]

    normalized_properties: dict[str, Any] = {}
    for name, prop in properties.items():
        if isinstance(prop, dict):
            prop = {key: value for key, value in prop.items() if key != "title"}
        normalized_properties[name] = prop

    return {
        "type": "object",
        "properties": normalized_properties,
        "required": required,
        "additionalProperties": False,
    }


def model_to_parameters_schema(model: type[BaseModel]) -> JsonObject:
    """从 Pydantic v2 参数模型生成工具参数 JSON Schema。

    优先使用 Pydantic v2 内置 ``model_json_schema()``，不新增运行时依赖；
    生成结果经 ``_normalize_model_schema`` 规范成工具层参数形状。只支持
    扁平参数模型，嵌套类型显式拒绝。
    """

    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise TypeError("只能从 Pydantic BaseModel 子类生成参数 Schema")
    raw = model.model_json_schema()
    if not isinstance(raw, dict):
        raise ValueError("Pydantic model 生成的 Schema 不是 JSON 对象")
    return _normalize_model_schema(raw)


def signature_to_parameters_schema(func: Callable[..., Any]) -> JsonObject:
    """从普通函数签名轻量生成工具参数 JSON Schema。

    只支持位置参数、位置或关键字参数与关键字参数；可变参数和无法映射的
    类型标注显式报错，避免悄悄生成错误 Schema。带默认值的参数不作为
    ``required``。
    """

    if not callable(func):
        raise TypeError("只能从可调用对象生成参数 Schema")
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无法读取函数签名：{func}") from exc
    try:
        type_hints = typing.get_type_hints(func)
    except Exception:
        # 前向引用等无法解析的标注保持原样，交给 _annotation_to_json_type
        # 报出可读错误。
        type_hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise ValueError(f"不支持可变参数：{name}")
        annotation = type_hints.get(name, parameter.annotation)
        properties[name] = {
            "type": _annotation_to_json_type(annotation),
        }
        if parameter.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def generate_parameters_schema(
    source: type[BaseModel] | Callable[..., Any],
) -> JsonObject:
    """从声明源自动生成工具参数 JSON Schema。

    Pydantic v2 参数模型走 ``model_to_parameters_schema``；普通可调用对象
    走 ``signature_to_parameters_schema`` 轻量转换。
    """

    if isinstance(source, type) and issubclass(source, BaseModel):
        return model_to_parameters_schema(source)
    if callable(source):
        return signature_to_parameters_schema(source)
    raise TypeError("只能从 Pydantic BaseModel 子类或可调用对象生成参数 Schema")


def validate_parameters_schema(schema: JsonObject) -> None:
    """校验工具声明的 parameters 是否为可用的参数 JSON Schema。

    非法时抛 ``ValueError``：只检查结构（对象、type 为 object、properties
    为对象、required 为字符串列表、additionalProperties 为布尔值）与可
    JSON 序列化性，不做完整 JSON Schema 语法校验。
    """

    if not isinstance(schema, dict):
        raise ValueError("工具参数 Schema 必须是 JSON 对象")
    try:
        json.dumps(schema, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("工具参数 Schema 必须只包含可 JSON 序列化的值") from exc

    if schema.get("type") != "object":
        raise ValueError("工具参数 Schema 的 type 必须是 object")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("工具参数 Schema 的 properties 必须是 JSON 对象")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(name, str) for name in required
    ):
        raise ValueError("工具参数 Schema 的 required 必须是字符串列表")
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        raise ValueError("工具参数 Schema 的 additionalProperties 必须是布尔值")


def _actual_type_name(value: Any) -> str:
    """返回参数实际值的 JSON Schema 类型名，供稳定错误消息使用。"""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_matches(value: Any, expected: str) -> bool:
    """判断值是否满足 JSON Schema 的 type 关键字。"""

    if expected == "integer":
        return type(value) is int
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True


def _validate_property(
    value: Any, prop_schema: dict[str, Any], name: str
) -> str | None:
    """按属性子 Schema 校验单个参数，返回第一个稳定错误消息。"""

    expected = prop_schema.get("type")
    if isinstance(expected, str) and expected in _JSON_TYPES:
        if not _type_matches(value, expected):
            return f"参数 {name} 类型应为 {expected}，实际为 {_actual_type_name(value)}"

    enum = prop_schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return f"参数 {name} 不在允许值范围内"

    if isinstance(value, str):
        min_length = prop_schema.get("minLength")
        if isinstance(min_length, int) and not isinstance(min_length, bool):
            if len(value) < min_length:
                return f"参数 {name} 长度不能少于 {min_length}"
        max_length = prop_schema.get("maxLength")
        if isinstance(max_length, int) and not isinstance(max_length, bool):
            if len(value) > max_length:
                return f"参数 {name} 长度不能超过 {max_length}"
        pattern = prop_schema.get("pattern")
        if isinstance(pattern, str) and pattern:
            if re.search(pattern, value) is None:
                return f"参数 {name} 不符合要求格式"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = prop_schema.get("minimum")
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
            if value < minimum:
                return f"参数 {name} 不能小于 {minimum}"
        maximum = prop_schema.get("maximum")
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
            if value > maximum:
                return f"参数 {name} 不能大于 {maximum}"

    items = prop_schema.get("items")
    if isinstance(value, (list, tuple)) and isinstance(items, dict):
        for index, item in enumerate(value):
            message = _validate_property(item, items, f"{name}[{index}]")
            if message is not None:
                return message

    return None


def validate_parameters(arguments: JsonObject, schema: JsonObject) -> str | None:
    """按参数 Schema 校验参数字典，非法时返回第一个稳定错误消息。

    合法时返回 ``None``。错误消息只使用稳定中文说明与参数名，不包含原始
    对象或堆栈。``ToolRegistry.execute`` 在业务校验之前调用本函数。
    """

    validate_parameters_schema(schema)

    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in arguments:
            return f"缺少必需参数：{name}"

    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(arguments) - set(properties))
        if unexpected:
            return f"包含不支持的参数：{', '.join(unexpected)}"

    for name in sorted(properties):
        if name not in arguments:
            continue
        prop_schema = properties[name]
        if not isinstance(prop_schema, dict):
            continue
        message = _validate_property(arguments[name], prop_schema, name)
        if message is not None:
            return message

    return None


__all__ = [
    "generate_parameters_schema",
    "model_to_parameters_schema",
    "signature_to_parameters_schema",
    "validate_parameters",
    "validate_parameters_schema",
]
