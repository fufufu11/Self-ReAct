"""与模型供应商解耦的 LLM 接口和确定性测试适配器。
本模块的接口只接收模型上下文并返回助手消息；流式调用通过 ``complete_stream``
逐块产出增量，最终用 :func:`collect_stream` 组装出与 ``complete`` 等价的
消息。工具调用仍只是返回消息中的 ``ToolCall`` 数据；执行工具、构造
``ToolResult``、修改 ``AgentState`` 以及决定重试或终止，都属于后续 Agent
和工具模块的职责。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from self_react.models import Message, MessageRole, ToolCall


class LLMError(Exception):
    """所有 LLM 接口稳定错误的基类。"""


class LLMInputError(LLMError, ValueError):
    """调用方提供的消息上下文不满足 LLM 接口约束。"""


class LLMResponseError(LLMError, ValueError):
    """LLM 适配器准备返回的响应不满足助手消息约束。"""


class LLMConfigurationError(LLMError, ValueError):
    """LLM 适配器启动配置无效或缺少必要配置。"""


class LLMProviderErrorCode(str, Enum):
    """供应商调用失败时对上层稳定暴露的错误类别。"""

    AUTHENTICATION = "AUTHENTICATION"
    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    RATE_LIMIT = "RATE_LIMIT"
    BAD_REQUEST = "BAD_REQUEST"
    SERVICE = "SERVICE"
    UNKNOWN = "UNKNOWN"


class LLMProviderError(LLMError):
    """供应商请求失败，但调用方不应依赖 SDK 的异常类型或文本。"""

    def __init__(self, code: LLMProviderErrorCode, message: str) -> None:
        """保存稳定类别和面向调用方的安全说明。"""

        self.code = code
        super().__init__(message)


class LLMResponseExhaustedError(LLMError):
    """Fake LLM 已消费完全部预置响应。"""


@runtime_checkable
class LLM(Protocol):
    """模型调用模块向 Agent 暴露的最小接口。

    实现接收至少一条已经由领域模型校验的 ``Message``，且不得修改调用方的
    消息序列；返回值必须是 ``assistant`` 角色的 ``Message``。调用方只依赖
    这一接口，因此未来供应商适配器可以替换 Fake LLM，而无需改变 Agent。
    """

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[object] | None = None,
    ) -> Message:
        """根据完整消息上下文生成下一条助手消息。"""
        ...

    def complete_stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[object] | None = None,
    ) -> Iterator[StreamChunk]:
        """生成式流式调用：逐块产出增量，组装后与 ``complete`` 等价。"""
        ...


FAKE_STREAM_CHUNK_SIZE = 8
"""Fake 流把每条响应内容切成等长块的字数；固定值保证确定性可断言。"""


@dataclass(frozen=True)
class StreamChunk:
    """一次流式调用中的一段增量数据。

    ``content`` 是本块新增的文本片段（可为空串）；``final_answer_content``
    是本块携带的原生 ``final_answer`` 工具调用 ``content`` 参数增量——已从
    跨块累积的 arguments JSON 片段中提取出的纯文本，仅供流式渲染实时展示，
    不参与 :func:`collect_stream` 的消息组装；``tool_calls`` 是到当前块为止
    已完成组装的工具调用，通常只在最后一个块出现；``reasoning_content``
    （DeepSeek 思考模式）通常只在最后一个块完整携带，由
    :func:`collect_stream` 组装进最终 Message。调用方可以消费
    ``content`` / ``final_answer_content`` 做实时展示，最终用
    :func:`collect_stream` 组装出与 ``complete`` 等价的 assistant Message。
    """

    content: str
    final_answer_content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    reasoning_content: str = ""

    def __post_init__(self) -> None:
        """拒绝非字符串内容与非 ToolCall 元素，避免组装阶段才暴露类型错误。"""

        if not isinstance(self.content, str):
            raise TypeError("StreamChunk.content 必须是字符串")
        if not isinstance(self.final_answer_content, str):
            raise TypeError("StreamChunk.final_answer_content 必须是字符串")
        if not isinstance(self.reasoning_content, str):
            raise TypeError("StreamChunk.reasoning_content 必须是字符串")
        if isinstance(self.tool_calls, (str, bytes)) or not isinstance(
            self.tool_calls, tuple
        ):
            raise TypeError("StreamChunk.tool_calls 必须是 ToolCall 元组")
        if not all(isinstance(call, ToolCall) for call in self.tool_calls):
            raise TypeError("StreamChunk.tool_calls 必须只包含 ToolCall")


def collect_stream(chunks: Iterable[StreamChunk]) -> Message:
    """把完整增量序列组装成与 ``complete`` 等价的 assistant Message。

    拼接所有 ``content`` 增量并按出现顺序收集 ``tool_calls``，同时把各块
    ``reasoning_content`` 拼接写回最终消息；无法组装出合法 assistant Message
    （例如重复工具调用编号）时报稳定响应错误。只做纯转换，不访问网络、
    不读取环境变量、不修改输入。
    """

    content_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    reasoning_parts: list[str] = []
    for chunk in chunks:
        if not isinstance(chunk, StreamChunk):
            raise LLMResponseError("流式增量序列只能包含 StreamChunk")
        content_parts.append(chunk.content)
        tool_calls.extend(chunk.tool_calls)
        reasoning_parts.append(chunk.reasoning_content)
    try:
        return Message(
            role=MessageRole.ASSISTANT,
            content="".join(content_parts),
            tool_calls=tool_calls,
            reasoning_content="".join(reasoning_parts) or None,
        )
    except Exception:
        raise LLMResponseError("流式增量无法组装为 assistant Message") from None


def _snapshot_input(messages: Sequence[Message]) -> tuple[Message, ...]:
    """校验一次调用输入，并创建与调用方可变对象隔离的快照。"""

    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
        raise LLMInputError("messages 必须是 Message 的序列")
    if not messages:
        raise LLMInputError("messages 不能为空")
    if not all(isinstance(message, Message) for message in messages):
        raise LLMInputError("messages 中的每一项都必须是 Message")
    return tuple(message.model_copy(deep=True) for message in messages)


def _snapshot_responses(responses: Sequence[Message]) -> tuple[Message, ...]:
    """校验 Fake LLM 的预置响应，并保存独立快照。"""

    if isinstance(responses, (str, bytes)) or not isinstance(responses, Sequence):
        raise LLMResponseError("responses 必须是 Message 的序列")

    snapshots: list[Message] = []
    for response in responses:
        if not isinstance(response, Message):
            raise LLMResponseError("responses 中的每一项都必须是 Message")
        if response.role is not MessageRole.ASSISTANT:
            raise LLMResponseError("LLM 响应必须使用 assistant 角色")
        snapshots.append(response.model_copy(deep=True))
    return tuple(snapshots)


class FakeLLM:
    """按顺序返回预置助手消息的确定性 LLM 适配器。

    Fake LLM 不访问网络、环境变量、API Key 或真实模型。每次合法调用都会先
    保存输入快照，再消费一条响应；响应已耗尽时，该次调用仍会进入历史并抛出
    ``LLMResponseExhaustedError``。这些运行时记录用于测试，不属于领域状态，
    不能放入 ``AgentState``。
    """

    def __init__(self, responses: Sequence[Message]) -> None:
        """复制并校验预置响应；空序列表示第一次调用就会耗尽。"""

        self._responses = _snapshot_responses(responses)
        self._next_response_index = 0
        self._calls: list[tuple[Message, ...]] = []
        self._calls_with_tools: list[
            tuple[tuple[Message, ...], tuple[object, ...]]
        ] = []

    def _prepare_call(
        self,
        messages: Sequence[Message],
        tools: Sequence[object] | None,
    ) -> None:
        """校验并快照一次调用输入，写入调用历史（含工具清单快照）。"""

        snapshot = _snapshot_input(messages)
        if tools is None:
            tools_snapshot: tuple[object, ...] = ()
        elif isinstance(tools, (str, bytes)) or not isinstance(tools, Sequence):
            raise LLMInputError("tools 必须是工具序列")
        else:
            tools_snapshot = tuple(tools)
        self._calls_with_tools.append((snapshot, tools_snapshot))
        self._calls.append(snapshot)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[object] | None = None,
    ) -> Message:
        """记录输入并返回下一条独立的预置响应副本。

        ``tools`` 是可选工具清单，供供应商适配器生成工具定义；Fake LLM
        只做记录，不改变返回行为。
        """

        self._prepare_call(messages, tools)

        if self._next_response_index >= len(self._responses):
            raise LLMResponseExhaustedError("Fake LLM 的预置响应已耗尽")

        response = self._responses[self._next_response_index]
        self._next_response_index += 1
        return response.model_copy(deep=True)

    def complete_stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[object] | None = None,
    ) -> Iterator[StreamChunk]:
        """按固定大小切块产出预置响应的内容增量；末尾块携带完整工具调用。
        与 ``complete`` 一样先校验并记录调用、按序消耗预置响应；耗尽时报
        ``LLMResponseExhaustedError`` 且计入调用历史。块大小由
        ``FAKE_STREAM_CHUNK_SIZE`` 固定，保证相同输入永远得到相同块序列。
        预置响应是原生 ``final_answer`` 工具调用时，其 ``content`` 参数值
        也按同样块大小切块，经 ``final_answer_content`` 增量透出，模拟真实
        供应商的原生工具调用流式形态。
        """

        self._prepare_call(messages, tools)
        if self._next_response_index >= len(self._responses):
            raise LLMResponseExhaustedError("Fake LLM 的预置响应已耗尽")

        response = self._responses[self._next_response_index]
        self._next_response_index += 1
        content = response.content
        final_answer_content = ""
        if len(response.tool_calls) == 1:
            call = response.tool_calls[0]
            if call.name == "final_answer" and isinstance(call.arguments, dict):
                value = call.arguments.get("content")
                if isinstance(value, str):
                    final_answer_content = value
        for index in range(0, len(content), FAKE_STREAM_CHUNK_SIZE):
            yield StreamChunk(content=content[index : index + FAKE_STREAM_CHUNK_SIZE])
        for index in range(0, len(final_answer_content), FAKE_STREAM_CHUNK_SIZE):
            yield StreamChunk(
                content="",
                final_answer_content=final_answer_content[
                    index : index + FAKE_STREAM_CHUNK_SIZE
                ],
            )
        if response.tool_calls:
            yield StreamChunk(
                content="",
                tool_calls=tuple(response.tool_calls),
            )

    @property
    def calls(self) -> tuple[tuple[Message, ...], ...]:
        """返回调用历史的深拷贝，避免测试意外修改 Fake 内部记录。"""

        return tuple(
            tuple(message.model_copy(deep=True) for message in call)
            for call in self._calls
        )

    @property
    def call_count(self) -> int:
        """返回合法调用尝试次数，包括响应耗尽的尝试。"""

        return len(self._calls)

    @property
    def calls_with_tools(
        self,
    ) -> tuple[tuple[tuple[Message, ...], tuple[object, ...]], ...]:
        """返回每次调用的消息快照与工具清单快照，供测试断言适配器输入。"""

        return tuple(
            (
                tuple(message.model_copy(deep=True) for message in messages),
                tools,
            )
            for messages, tools in self._calls_with_tools
        )


__all__ = [
    "FAKE_STREAM_CHUNK_SIZE",
    "FakeLLM",
    "LLM",
    "LLMError",
    "LLMConfigurationError",
    "LLMInputError",
    "LLMProviderError",
    "LLMProviderErrorCode",
    "LLMResponseError",
    "LLMResponseExhaustedError",
    "StreamChunk",
    "collect_stream",
]
