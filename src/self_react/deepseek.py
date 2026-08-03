"""DeepSeek OpenAI 兼容 Chat Completions 的同步 LLM 适配器。

适配器只负责把领域消息转换成供应商请求，并把一次响应转换回 assistant
Message。它不执行 ToolCall、不修改 AgentState、不读取隐藏状态，也不在供应商
失败时自行重试。测试可以注入一个具有 chat.completions.create 方法的客户端，
因此自动化测试不需要网络或密钥。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

from self_react.llm import (
    LLMConfigurationError,
    LLMInputError,
    LLMProviderError,
    LLMProviderErrorCode,
    LLMResponseError,
)
from self_react.models import Message, MessageRole, ToolCall

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT = 30.0


class _CompletionsClient(Protocol):
    """测试注入客户端所需的最小结构。"""

    def create(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        stream: bool,
    ) -> Any:
        """创建一次非流式 Chat Completions 请求。"""
        ...


class _ChatClient(Protocol):
    """OpenAI 客户端的 chat 子树最小结构。"""

    completions: _CompletionsClient


class _Client(Protocol):
    """OpenAI 客户端注入边界。"""

    chat: _ChatClient


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


def _serialize_message(message: Message) -> dict[str, Any]:
    """把领域消息转换成 DeepSeek/OpenAI Chat Completions 消息。"""

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


def _serialize_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """校验并复制完整上下文，避免请求过程修改调用方消息。"""

    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
        raise LLMInputError("messages 必须是 Message 的序列")
    if not messages:
        raise LLMInputError("messages 不能为空")
    if not all(isinstance(message, Message) for message in messages):
        raise LLMInputError("messages 中的每一项都必须是 Message")
    return [_serialize_message(message.model_copy(deep=True)) for message in messages]


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


def _deserialize_response(response: Any) -> Message:
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


def _provider_error_code(error: Exception) -> LLMProviderErrorCode:
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


class DeepSeekLLM:
    """使用 OpenAI Python SDK 调用 DeepSeek Chat Completions 的同步适配器。

    默认从 DEEPSEEK_API_KEY 读取密钥，并将客户端最大自动重试次数设为零。
    传入 client 后可以完全替换 SDK 客户端，适合无网络单元测试；注入客户端
    时不要求提供密钥。
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        client: _Client | None = None,
    ) -> None:
        """校验配置并建立客户端；不会把密钥写入领域状态或日志。"""

        if not isinstance(model, str) or not model.strip():
            raise LLMConfigurationError("model 必须是非空字符串")
        if not isinstance(base_url, str) or not base_url.strip():
            raise LLMConfigurationError("base_url 必须是非空字符串")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            raise LLMConfigurationError("timeout 必须是正数")

        self.model = model
        self.base_url = base_url
        self.timeout = float(timeout)

        if client is not None:
            self._client = client
            return

        resolved_key = os.getenv("DEEPSEEK_API_KEY")
        if not isinstance(resolved_key, str) or not resolved_key.strip():
            raise LLMConfigurationError("缺少 DEEPSEEK_API_KEY")
        self._client = OpenAI(
            api_key=resolved_key,
            base_url=base_url,
            timeout=self.timeout,
            max_retries=0,
        )

    def complete(self, messages: Sequence[Message]) -> Message:
        """发起一次非流式请求，并返回 assistant Message。"""

        payload = _serialize_messages(messages)
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=payload,
                stream=False,
            )
        except Exception as exc:
            code = _provider_error_code(exc)
            raise LLMProviderError(code, f"DeepSeek 请求失败（{code.value}）") from None
        return _deserialize_response(response)


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "DeepSeekLLM",
]
