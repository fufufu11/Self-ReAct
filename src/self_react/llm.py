"""与模型供应商解耦的 LLM 接口和确定性测试适配器。

本模块的接口只接收模型上下文并返回助手消息。工具调用仍只是返回消息中的
``ToolCall`` 数据；执行工具、构造 ``ToolResult``、修改 ``AgentState`` 以及决定
重试或终止，都属于后续 Agent 和工具模块的职责。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from self_react.models import Message, MessageRole


class LLMError(Exception):
    """所有 LLM 接口稳定错误的基类。"""


class LLMInputError(LLMError, ValueError):
    """调用方提供的消息上下文不满足 LLM 接口约束。"""


class LLMResponseError(LLMError, ValueError):
    """LLM 适配器准备返回的响应不满足助手消息约束。"""


class LLMResponseExhaustedError(LLMError):
    """Fake LLM 已消费完全部预置响应。"""


@runtime_checkable
class LLM(Protocol):
    """模型调用模块向 Agent 暴露的最小接口。

    实现接收至少一条已经由领域模型校验的 ``Message``，且不得修改调用方的
    消息序列；返回值必须是 ``assistant`` 角色的 ``Message``。调用方只依赖
    这一接口，因此未来供应商适配器可以替换 Fake LLM，而无需改变 Agent。
    """

    def complete(self, messages: Sequence[Message]) -> Message:
        """根据完整消息上下文生成下一条助手消息。"""
        ...


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

    def complete(self, messages: Sequence[Message]) -> Message:
        """记录输入并返回下一条独立的预置响应副本。"""

        snapshot = _snapshot_input(messages)
        self._calls.append(snapshot)

        if self._next_response_index >= len(self._responses):
            raise LLMResponseExhaustedError("Fake LLM 的预置响应已耗尽")

        response = self._responses[self._next_response_index]
        self._next_response_index += 1
        return response.model_copy(deep=True)

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


__all__ = [
    "FakeLLM",
    "LLM",
    "LLMError",
    "LLMInputError",
    "LLMResponseError",
    "LLMResponseExhaustedError",
]
