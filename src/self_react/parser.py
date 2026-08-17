"""模型原始输出到领域决策的确定性解析器（Day 11；R-06 增加规划/反思 kind）。

本模块只负责把模型按 Day 10 格式契约输出的 JSON 字符串解析成 Day 4 的
领域决策对象：``kind == "final_answer"`` 构造 ``FinalAnswer(content)``，
``kind == "tool_call"`` 构造 ``ToolCall(call_id, name, arguments)``，
R-06 可选模式另支持 ``kind == "plan"``（``Plan``）与
``kind == "reflection"``（``Reflection``）。解析器不做工具查找、不执行
工具、不修改任何状态；未知工具由 Day 7 注册表在分派阶段判断。

``parse_decision`` 的 ``allowed`` 关键字限制本次调用接受的 kind 集合：
默认（``None``）只接受 ``final_answer`` / ``tool_call``，与 R-06 之前的
行为逐字节一致；规划阶段传 ``allowed=frozenset({"plan"})``，反思阶段传
``allowed=frozenset({"reflection"})``，让主循环之外的特化阶段也能复用同一
套稳定校验与错误文本。

解析器是确定性纯函数：相同字符串输入永远返回相同结果，不访问网络、不读取
环境变量。非法输出统一抛 ``ParseError``，其 ``code`` 与
``TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR`` 对齐；错误消息是稳定文本，
不会泄漏原始异常对象、堆栈或模型原始输出。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from self_react.models import (
    FinalAnswer,
    Plan,
    Reflection,
    ToolCall,
    TraceErrorCode,
)

_FINAL_ANSWER_KIND = "final_answer"
"""格式契约中最终回答的判别字段值。"""

_TOOL_CALL_KIND = "tool_call"
"""格式契约中工具调用的判别字段值。"""

_PLAN_KIND = "plan"
"""R-06 规划阶段计划的判别字段值。"""

_REFLECTION_KIND = "reflection"
"""R-06 反思阶段反思的判别字段值。"""

_KNOWN_KINDS = frozenset(
    {_FINAL_ANSWER_KIND, _TOOL_CALL_KIND, _PLAN_KIND, _REFLECTION_KIND}
)
"""解析器认识的全部 kind；``allowed`` 只能是它的非空子集。"""

_DEFAULT_ALLOWED_KINDS = frozenset({_FINAL_ANSWER_KIND, _TOOL_CALL_KIND})
"""默认接受的 kind：主循环只消费最终回答与工具调用，与 R-06 之前一致。"""


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


def _restricted_kind_message(kinds: frozenset[str]) -> str:
    """受限阶段（规划/反思）的稳定错误文本。"""

    return f"此阶段只接受 kind={'/'.join(sorted(kinds))}"


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


def _parse_content_kind(
    data: dict[str, Any], kind: str
) -> FinalAnswer | Plan | Reflection:
    """按契约把 JSON 对象构造成带单一文本的决策（final_answer/plan/reflection）。"""

    _reject_unexpected_fields(data, {"kind", "content"})

    if "content" not in data:
        raise ParseError(f"{kind} 缺少 content 字段")
    content = data["content"]
    if not isinstance(content, str):
        raise ParseError("content 必须是字符串")
    try:
        if kind == _PLAN_KIND:
            return Plan(content=content)
        if kind == _REFLECTION_KIND:
            return Reflection(content=content)
        return FinalAnswer(content=content)
    except ValidationError:
        raise ParseError("content 必须是非空字符串") from None


def _parse_final_answer(data: dict[str, Any]) -> FinalAnswer:
    """按契约把 JSON 对象构造成 FinalAnswer。"""

    return _parse_content_kind(data, _FINAL_ANSWER_KIND)


def _parse_plan(data: dict[str, Any]) -> Plan:
    """按契约把 JSON 对象构造成 Plan（R-06 规划阶段）。"""

    return _parse_content_kind(data, _PLAN_KIND)


def _parse_reflection(data: dict[str, Any]) -> Reflection:
    """按契约把 JSON 对象构造成 Reflection（R-06 反思阶段）。"""

    return _parse_content_kind(data, _REFLECTION_KIND)


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


def parse_decision(
    raw: str,
    *,
    allowed: frozenset[str] | None = None,
) -> FinalAnswer | ToolCall | Plan | Reflection:
    """把模型原始 JSON 字符串解析成 FinalAnswer / ToolCall / Plan / Reflection。

    只接受字符串输入；非法 JSON、JSON 不是对象、``kind`` 缺失或非法、字段
    缺失、类型错误和多余字段都抛 ``ParseError``。解析不访问网络、不读取
    环境变量，相同输入永远返回相同结果。

    ``allowed`` 是可选的关键字参数：传非空 frozenset 时只接受其中的 kind
    （必须是解析器认识的 kind 的子集），供 R-06 的规划/反思阶段限制模型
    只能输出对应形态；默认 ``None`` 等价于
    ``frozenset({"final_answer", "tool_call"})``，与 R-06 之前的主循环
    行为及错误文本逐字节一致。
    """

    if not isinstance(raw, str):
        raise TypeError("parse_decision 只接受字符串输入")

    kinds = _DEFAULT_ALLOWED_KINDS if allowed is None else allowed
    if not isinstance(kinds, frozenset) or not kinds or not kinds <= _KNOWN_KINDS:
        raise TypeError("allowed 必须是已知 kind 的非空 frozenset")

    data = _parse_json_object(raw)

    if "kind" not in data:
        raise ParseError("模型输出缺少 kind 字段")
    kind = data["kind"]
    if not isinstance(kind, str):
        raise ParseError("kind 必须是字符串")

    if kind not in _KNOWN_KINDS or kind not in kinds:
        if allowed is None:
            raise ParseError("kind 只能是 final_answer 或 tool_call")
        raise ParseError(_restricted_kind_message(kinds))

    if kind == _FINAL_ANSWER_KIND:
        return _parse_final_answer(data)
    if kind == _TOOL_CALL_KIND:
        return _parse_tool_call(data)
    if kind == _PLAN_KIND:
        return _parse_plan(data)
    return _parse_reflection(data)


__all__ = ["ParseError", "parse_decision"]
