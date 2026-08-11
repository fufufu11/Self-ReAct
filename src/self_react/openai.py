"""OpenAI 原生 Chat Completions 的同步 LLM 适配器（R-01）。

适配器只负责把领域消息转换成 OpenAI Chat Completions 请求，并把一次响应
转换回 assistant Message；消息、工具定义与响应转换和 DeepSeek 适配器共用
``openai_compat`` 模块，本文件只保留 OpenAI 特有的默认配置与客户端构造。
适配器不执行 ToolCall、不修改 AgentState、不读取隐藏状态，也不在供应商
失败时自行重试。默认从 ``OPENAI_API_KEY`` 读取密钥；``base_url``、
``model``、``timeout`` 均可配置，测试可以注入一个具有
``chat.completions.create`` 方法的客户端，因此自动化测试不需要网络或密钥。
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence

from openai import OpenAI

from self_react.llm import (
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseError,
    StreamChunk,
)
from self_react.models import Message
from self_react.openai_compat import (
    Client,
    StreamAccumulator,
    deserialize_response,
    provider_error_code,
    serialize_messages,
    serialize_tools,
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.6"
DEFAULT_TIMEOUT = 30.0
"""适配器默认值。

``DEFAULT_MODEL`` 采用 OpenAI 当前的默认模型别名 ``gpt-5.6``（官方模型
指南中的最新默认）；模型名始终可通过 ``OpenAILLM(model=...)`` 覆盖，
不把模型名写进领域状态。
"""


class OpenAILLM:
    """使用 OpenAI Python SDK 调用 OpenAI Chat Completions 的同步适配器。

    默认从 OPENAI_API_KEY 读取密钥，并将客户端最大自动重试次数设为零。
    传入 client 后可以完全替换 SDK 客户端，适合无网络单元测试；注入客户端
    时不要求提供密钥。
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        client: Client | None = None,
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

        resolved_key = os.getenv("OPENAI_API_KEY")
        if not isinstance(resolved_key, str) or not resolved_key.strip():
            raise LLMConfigurationError("缺少 OPENAI_API_KEY")
        self._client = OpenAI(
            api_key=resolved_key,
            base_url=base_url,
            timeout=self.timeout,
            max_retries=0,
        )

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[object] | None = None,
    ) -> Message:
        """发起一次非流式请求，并返回 assistant Message。"""

        payload = serialize_messages(messages)
        serialized_tools = serialize_tools(tools) if tools is not None else None
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=payload,
                stream=False,
                tools=serialized_tools,
                extra_body=None,
            )
        except Exception as exc:
            code = provider_error_code(exc)
            raise LLMProviderError(code, f"OpenAI 请求失败（{code.value}）") from None
        return deserialize_response(response)

    def complete_stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[object] | None = None,
    ) -> Iterator[StreamChunk]:
        """发起一次流式请求，逐块产出内容增量；组装后与 ``complete`` 等价。
        工具调用参数跨块按 index 增量拼接，在末尾块一次性携带。
        """

        payload = serialize_messages(messages)
        serialized_tools = serialize_tools(tools) if tools is not None else None
        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=payload,
                stream=True,
                tools=serialized_tools,
                extra_body=None,
            )
        except Exception as exc:
            code = provider_error_code(exc)
            raise LLMProviderError(
                code, f"OpenAI 流式请求失败（{code.value}）"
            ) from None

        accumulator = StreamAccumulator()
        try:
            for chunk in stream:
                delta = accumulator.feed(chunk)
                if delta.content:
                    yield delta
            message = accumulator.message()
            if message.tool_calls:
                yield StreamChunk(
                    content="",
                    tool_calls=tuple(message.tool_calls),
                )
        except LLMResponseError:
            raise
        except Exception as exc:
            code = provider_error_code(exc)
            raise LLMProviderError(
                code, f"OpenAI 流式读取失败（{code.value}）"
            ) from None


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "OpenAILLM",
]
