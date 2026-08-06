"""OpenAI 兼容 Chat Completions 的共享请求/响应转换层（R-01）。

DeepSeek 与 OpenAI 都走 OpenAI 兼容的 Chat Completions 接口：请求都是
``role/content/tool_calls/tool_call_id`` 消息序列加 function 工具定义，
响应都是 ``choices[0].message`` 加可选的 ``tool_calls``。本模块把这段
转换逻辑抽成两者共用的纯函数，避免两个适配器各复制一份：

- ``serialize_message`` / ``serialize_messages``：领域消息 -> 供应商请求；
- ``serialize_tools``：工具清单 -> function 工具定义；
- ``deserialize_response``：供应商响应 -> assistant Message；
- ``provider_error_code``：SDK 异常 -> 稳定的供应商错误类别。

本模块只做转换，不发起网络请求、不读取环境变量、不执行工具；适配器负责
客户端构造、配置与真正的 ``chat.completions.create`` 调用。测试通过注入
客户端覆盖适配器，共享转换逻辑则被两个适配器的测试共同回归。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from self_react.llm import (
    LLMInputError,
    LLMProviderErrorCode,
    LLMResponseError,
)
from self_react.models import Message, MessageRole, ToolCall
from self_react.tools.base import DEFAULT_PARAMETERS_SCHEMA


class CompletionsClient(Protocol):
    """测试注入客户端所需的最小结构。"""

    def create(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        stream: bool,
        tools: list[dict[str, Any]] | None,
        extra_body: dict[str, Any] | None,
    ) -> Any:
        """创建一次非流式 Chat Completions 请求。"""
        ...


class ChatClient(Protocol):
    """OpenAI 客户端的 chat 子树最小结构。"""

    completions: CompletionsClient


class Client(Protocol):
    """OpenAI 客户端注入边界。"""

    chat: ChatClient


_MISSING = object()


def _field(value: Any, name: str, default: Any = _MISSING) -> Any:
    """同时读取 SDK 对象和测试字典中的字段。"""

    if isinstance(value, Mapping):
        if name in value:
            return value[name]
    else:
        candidate = getattr(value, name, _MISSING)
        if candidate is not _MISSING:
            return candidate
    if default is not _MISSING:
        return default
    raise LLMResponseError(f"供应商响应缺少字段: {name}")


def _text(value: Any, *, field_name: str) -> str:
    """读取 SDK 枚举或字符串字段，并拒绝其他类型。"""

    if isinstance(value, str):
        return value
    enum_value = getattr(value, "value", _MISSING)
    if isinstance(enum_value, str):
        return enum_value
    raise LLMResponseError(f"供应商响应字段 {field_name} 必须是字符串")


def serialize_message(message: Message) -> dict[str, Any]:
    """把领域消息转换成 OpenAI 兼容 Chat Completions 消息。"""

    payload: dict[str, Any] = {
        "role": message.role.value,
        "content": message.content,
    }

    if message.role is MessageRole.ASSISTANT and message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(
                        call.arguments,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ),
                },
            }
            for call in message.tool_calls
        ]
    elif message.role is MessageRole.TOOL:
        if message.tool_call_id is None:
            raise LLMInputError("tool 消息缺少 tool_call_id")
        payload["tool_call_id"] = message.tool_call_id

    return payload


def serialize_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """校验并复制完整上下文，避免请求过程修改调用方消息。"""

    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
        raise LLMInputError("messages 必须是 Message 的序列")
    if not messages:
        raise LLMInputError("messages 不能为空")
    if not all(isinstance(message, Message) for message in messages):
        raise LLMInputError("messages 中的每一项都必须是 Message")
    return [serialize_message(message.model_copy(deep=True)) for message in messages]


def _tool_name(tool: object) -> str:
    """读取工具名称，供工具定义序列化使用。"""

    name = getattr(tool, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise LLMInputError("工具 name 必须是非空字符串")
    return name


def _tool_description(tool: object) -> str:
    """读取工具描述，供工具定义序列化使用；缺失时使用稳定占位符。"""

    description = getattr(tool, "description", "")
    if not isinstance(description, str) or not description.strip():
        return "（无描述）"
    return description


def _tool_parameters(tool: object, name: str) -> dict[str, Any]:
    """读取工具声明的参数 JSON Schema；缺省时返回宽松对象。

    ``parameters`` 是工具层的可选约定（Day 17）：未声明时回退到
    ``DEFAULT_PARAMETERS_SCHEMA``，与 Day 6b 的既有行为一致；有声明但
    不是 JSON 对象或不可 JSON 序列化时抛 ``LLMInputError``，避免把非法
    schema 悄悄下发给供应商。
    """

    parameters = getattr(tool, "parameters", None)
    if parameters is None:
        return dict(DEFAULT_PARAMETERS_SCHEMA)
    if not isinstance(parameters, dict):
        raise LLMInputError(f"工具 {name} 的 parameters 必须是 JSON 对象")
    try:
        json.dumps(parameters, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LLMInputError(
            f"工具 {name} 的 parameters 必须只包含可 JSON 序列化的值"
        ) from exc
    return parameters


def serialize_tools(tools: Sequence[object]) -> list[dict[str, Any]]:
    """把工具清单序列化成供应商 function 定义。

    参数形状优先使用工具声明的 ``parameters`` JSON Schema（Day 17），
    未声明时回退到宽松对象。参数的实际校验仍由工具层在 Day 7 注册表边界
    完成，适配器不复制工具业务逻辑。
    """

    if isinstance(tools, (str, bytes)) or not isinstance(tools, Sequence):
        raise LLMInputError("tools 必须是工具序列")

    serialized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in tools:
        name = _tool_name(tool)
        if name in seen:
            raise LLMInputError(f"工具定义重复：{name}")
        seen.add(name)
        serialized.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": _tool_description(tool),
                    "parameters": _tool_parameters(tool, name),
                },
            }
        )
    return serialized


def _deserialize_tool_call(raw_call: Any) -> ToolCall:
    """把供应商工具调用严格转换成领域 ToolCall。"""

    call_id = _text(_field(raw_call, "id"), field_name="tool_call.id")
    call_type = _text(_field(raw_call, "type"), field_name="tool_call.type")
    if call_type != "function":
        raise LLMResponseError("供应商工具调用 type 必须是 function")
    function = _field(raw_call, "function")
    name = _text(_field(function, "name"), field_name="tool_call.function.name")
    arguments_text = _text(
        _field(function, "arguments"),
        field_name="tool_call.function.arguments",
    )
    try:
        arguments = json.loads(arguments_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LLMResponseError("供应商工具参数不是合法 JSON") from exc
    if not isinstance(arguments, dict):
        raise LLMResponseError("供应商工具参数必须是 JSON 对象")
    try:
        return ToolCall(call_id=call_id, name=name, arguments=arguments)
    except Exception as exc:
        raise LLMResponseError("供应商工具调用字段不满足领域模型约束") from exc


def deserialize_response(response: Any) -> Message:
    """把一次供应商响应转换成合法 assistant Message。"""

    choices = _field(response, "choices")
    if (
        isinstance(choices, (str, bytes))
        or not isinstance(choices, Sequence)
        or not choices
    ):
        raise LLMResponseError("供应商响应 choices 必须是非空序列")
    raw_message = _field(choices[0], "message")
    role = _text(_field(raw_message, "role"), field_name="message.role")
    if role != MessageRole.ASSISTANT.value:
        raise LLMResponseError("供应商响应 message.role 必须是 assistant")

    content = _field(raw_message, "content", None)
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise LLMResponseError("供应商响应 message.content 必须是字符串或 null")

    raw_tool_calls = _field(raw_message, "tool_calls", None)
    if raw_tool_calls is None:
        raw_tool_calls = []
    if isinstance(raw_tool_calls, (str, bytes)) or not isinstance(
        raw_tool_calls, Sequence
    ):
        raise LLMResponseError("供应商响应 tool_calls 必须是序列")
    tool_calls = [_deserialize_tool_call(raw_call) for raw_call in raw_tool_calls]
    try:
        return Message(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        )
    except Exception as exc:
        raise LLMResponseError("供应商响应无法构造为 assistant Message") from exc


def provider_error_code(error: Exception) -> LLMProviderErrorCode:
    """将 SDK 异常映射成不依赖供应商文本的稳定类别。"""

    if isinstance(error, AuthenticationError):
        return LLMProviderErrorCode.AUTHENTICATION
    if isinstance(error, APITimeoutError):
        return LLMProviderErrorCode.TIMEOUT
    if isinstance(error, APIConnectionError):
        return LLMProviderErrorCode.CONNECTION
    if isinstance(error, RateLimitError):
        return LLMProviderErrorCode.RATE_LIMIT
    if isinstance(error, BadRequestError):
        return LLMProviderErrorCode.BAD_REQUEST
    if isinstance(error, APIStatusError):
        status_code = getattr(error, "status_code", None)
        if status_code in (401, 403):
            return LLMProviderErrorCode.AUTHENTICATION
        if status_code == 408:
            return LLMProviderErrorCode.TIMEOUT
        if status_code == 429:
            return LLMProviderErrorCode.RATE_LIMIT
        if isinstance(status_code, int) and 400 <= status_code < 500:
            return LLMProviderErrorCode.BAD_REQUEST
        if isinstance(status_code, int) and status_code >= 500:
            return LLMProviderErrorCode.SERVICE
        return LLMProviderErrorCode.UNKNOWN
    if isinstance(error, APIError):
        return LLMProviderErrorCode.SERVICE
    return LLMProviderErrorCode.UNKNOWN


__all__ = [
    "Client",
    "CompletionsClient",
    "deserialize_response",
    "provider_error_code",
    "serialize_message",
    "serialize_messages",
    "serialize_tools",
]
