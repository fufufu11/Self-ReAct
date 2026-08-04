"""模型原始输出到领域决策的确定性解析器（Day 11）。

本模块只负责把模型按 Day 10 格式契约输出的 JSON 字符串解析成 Day 4 的
领域决策对象：``kind == "final_answer"`` 构造 ``FinalAnswer(content)``，
``kind == "tool_call"`` 构造 ``ToolCall(call_id, name, arguments)``。解析
器不做工具查找、不执行工具、不修改任何状态；未知工具由 Day 7 注册表在
分派阶段判断。

解析器是确定性纯函数：相同字符串输入永远返回相同结果，不访问网络、不读取
环境变量。非法输出统一抛 ``ParseError``，其 ``code`` 与
``TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR`` 对齐；错误消息是稳定文本，
不会泄漏原始异常对象、堆栈或模型原始输出。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from self_react.models import FinalAnswer, ToolCall, TraceErrorCode

_FINAL_ANSWER_KIND = "final_answer"
"""格式契约中最终回答的判别字段值。"""

_TOOL_CALL_KIND = "tool_call"
"""格式契约中工具调用的判别字段值。"""


class ParseError(ValueError):
    """模型输出无法解析时抛出的稳定错误。

    ``code`` 固定为 ``TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR``，方便 Day
    12 主循环把解析失败记录到轨迹错误中；错误消息只包含稳定说明，不携带
    原始异常对象、堆栈或模型原始输出。
    """

    code = TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR

    def __init__(self, message: str) -> None:
        """保存面向调用方的稳定错误说明。"""

        super().__init__(message)


def _parse_json_object(raw: str) -> dict[str, Any]:
    """把字符串解析成 JSON 对象；任何结构问题都转成稳定错误。"""

    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise ParseError("模型输出不是合法 JSON") from None

    if not isinstance(data, dict):
        raise ParseError("模型输出必须是 JSON 对象")
    return data


def _reject_unexpected_fields(data: dict[str, Any], allowed: set[str]) -> None:
    """拒绝格式契约之外的字段，防止模型夹带无法消费的内容。"""

    unexpected = sorted(set(data) - allowed)
    if unexpected:
        raise ParseError("模型输出包含格式契约之外的字段")


def _parse_final_answer(data: dict[str, Any]) -> FinalAnswer:
    """按契约把 JSON 对象构造成 FinalAnswer。"""

    _reject_unexpected_fields(data, {"kind", "content"})

    if "content" not in data:
        raise ParseError("final_answer 缺少 content 字段")
    content = data["content"]
    if not isinstance(content, str):
        raise ParseError("content 必须是字符串")
    try:
        return FinalAnswer(content=content)
    except ValidationError:
        raise ParseError("content 必须是非空字符串") from None


def _parse_tool_call(data: dict[str, Any]) -> ToolCall:
    """按契约把 JSON 对象构造成 ToolCall。"""

    _reject_unexpected_fields(data, {"kind", "call_id", "name", "arguments"})

    if "call_id" not in data:
        raise ParseError("tool_call 缺少 call_id 字段")
    if "name" not in data:
        raise ParseError("tool_call 缺少 name 字段")
    if "arguments" not in data:
        raise ParseError("tool_call 缺少 arguments 字段")

    call_id = data["call_id"]
    name = data["name"]
    arguments = data["arguments"]

    if not isinstance(call_id, str):
        raise ParseError("call_id 必须是字符串")
    if not isinstance(name, str):
        raise ParseError("name 必须是字符串")
    if not isinstance(arguments, dict):
        raise ParseError("arguments 必须是 JSON 对象")
    if not call_id.strip():
        raise ParseError("call_id 必须是非空字符串")
    if not name.strip():
        raise ParseError("name 必须是非空字符串")
    try:
        return ToolCall(call_id=call_id, name=name, arguments=arguments)
    except ValidationError:
        raise ParseError("arguments 必须只包含可 JSON 序列化的值") from None


def parse_decision(raw: str) -> FinalAnswer | ToolCall:
    """把模型原始 JSON 字符串解析成 FinalAnswer 或 ToolCall。

    只接受字符串输入；非法 JSON、JSON 不是对象、``kind`` 缺失或非法、字段
    缺失、类型错误和多余字段都抛 ``ParseError``。解析不访问网络、不读取
    环境变量，相同输入永远返回相同结果。
    """

    if not isinstance(raw, str):
        raise TypeError("parse_decision 只接受字符串输入")

    data = _parse_json_object(raw)

    if "kind" not in data:
        raise ParseError("模型输出缺少 kind 字段")
    kind = data["kind"]
    if not isinstance(kind, str):
        raise ParseError("kind 必须是字符串")

    if kind == _FINAL_ANSWER_KIND:
        return _parse_final_answer(data)
    if kind == _TOOL_CALL_KIND:
        return _parse_tool_call(data)
    raise ParseError("kind 只能是 final_answer 或 tool_call")


__all__ = ["ParseError", "parse_decision"]
