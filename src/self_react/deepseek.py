"""DeepSeek OpenAI 兼容 Chat Completions 的同步 LLM 适配器。

适配器只负责把领域消息转换成供应商请求，并把一次响应转换回 assistant
Message；消息、工具定义与响应转换和 OpenAI 适配器共用 ``openai_compat``
模块，本文件只保留 DeepSeek 特有的默认配置（地址、模型、思考模式开关）
与客户端构造。适配器不执行 ToolCall、不修改 AgentState、不读取隐藏状态，
也不在供应商失败时自行重试。测试可以注入一个具有
``chat.completions.create`` 方法的客户端，因此自动化测试不需要网络或密钥。
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from typing import Any

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

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT = 30.0
DEFAULT_THINKING_DISABLED = True
"""默认禁用 DeepSeek 思考模式。

思考模式会在响应里返回 ``reasoning_content``，DeepSeek 要求后续请求原样
传回它；本项目按 Day 10 契约只保留模型输出的 JSON 决策，无法保证
``reasoning_content`` 的完整往返，因此默认关闭思考模式，避免多轮工具调用
被 API 拒绝。
"""


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
        thinking_disabled: bool = DEFAULT_THINKING_DISABLED,
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
        self.thinking_disabled = bool(thinking_disabled)

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

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[object] | None = None,
    ) -> Message:
        """发起一次非流式请求，并返回 assistant Message。"""

        payload = serialize_messages(messages)
        serialized_tools = serialize_tools(tools) if tools is not None else None
        extra_body: dict[str, Any] = {}
        if self.thinking_disabled:
            extra_body["thinking"] = {"type": "disabled"}
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=payload,
                stream=False,
                tools=serialized_tools,
                extra_body=extra_body,
            )
        except Exception as exc:
            code = provider_error_code(exc)
            raise LLMProviderError(code, f"DeepSeek 请求失败（{code.value}）") from None
        return deserialize_response(response)

    def complete_stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[object] | None = None,
    ) -> Iterator[StreamChunk]:
        """发起一次流式请求，逐块产出内容增量；组装后与 ``complete`` 等价。
        流式块里的 ``reasoning_content``（思考模式）按非流式路径约定忽略；
        工具调用参数跨块按 index 增量拼接，``final_answer`` 工具的
        ``content`` 增量实时经 ``StreamChunk.final_answer_content`` 透出，
        末尾块仍一次性携带完整工具调用。
        """

        payload = serialize_messages(messages)
        serialized_tools = serialize_tools(tools) if tools is not None else None
        extra_body: dict[str, Any] = {}
        if self.thinking_disabled:
            extra_body["thinking"] = {"type": "disabled"}
        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=payload,
                stream=True,
                tools=serialized_tools,
                extra_body=extra_body,
            )
        except Exception as exc:
            code = provider_error_code(exc)
            raise LLMProviderError(
                code, f"DeepSeek 流式请求失败（{code.value}）"
            ) from None

        accumulator = StreamAccumulator()
        try:
            for chunk in stream:
                delta = accumulator.feed(chunk)
                if delta.content or delta.final_answer_content:
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
                code, f"DeepSeek 流式读取失败（{code.value}）"
            ) from None


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "DeepSeekLLM",
]
