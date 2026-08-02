"""Day 5 LLM 接口和 Fake LLM 的公开行为测试。"""

from collections.abc import Sequence

import pytest

from self_react.llm import (
    LLM,
    FakeLLM,
    LLMInputError,
    LLMResponseError,
    LLMResponseExhaustedError,
)
from self_react.models import Message, MessageRole, ToolCall


def assistant_message(content: str) -> Message:
    """构造不携带工具调用的助手消息测试夹具。"""

    return Message(role=MessageRole.ASSISTANT, content=content)


def test_fake_llm_returns_normal_response_and_snapshots_tuple_input() -> None:
    """不可变消息序列可直接调用，历史与调用方对象保持隔离。"""

    response = assistant_message("答案是 4。")
    message = Message(role=MessageRole.USER, content="计算 2 + 2")
    llm = FakeLLM((response,))

    actual = llm.complete((message,))
    calls = llm.calls

    assert actual.content == "答案是 4。"

    message.content = "已被调用方修改"
    actual.content = "已被接收方修改"
    calls[0][0].content = "已被测试修改"

    assert llm.call_count == 1
    assert llm.calls[0][0].content == "计算 2 + 2"
    assert response.content == "答案是 4。"


def test_fake_llm_returns_tool_call_without_executing_it() -> None:
    """Fake LLM 只返回 ToolCall 数据，不产生工具执行结果。"""

    call = ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )
    llm = FakeLLM([Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])])

    response = llm.complete([Message(role=MessageRole.USER, content="计算 2 + 2")])

    assert response.role is MessageRole.ASSISTANT
    assert response.tool_calls == [call]
    assert llm.call_count == 1


def test_fake_llm_preserves_response_order_and_reports_exhaustion() -> None:
    """预置响应只消费一次，耗尽调用有显式错误且仍计入历史。"""

    context = [Message(role=MessageRole.USER, content="继续")]
    llm = FakeLLM([assistant_message("第一条"), assistant_message("第二条")])

    assert llm.complete(context).content == "第一条"
    assert llm.complete(context).content == "第二条"

    with pytest.raises(LLMResponseExhaustedError):
        llm.complete(context)

    assert llm.call_count == 3
    assert len(llm.calls) == 3


@pytest.mark.parametrize("messages", [[], [object()]])
def test_fake_llm_rejects_invalid_input_without_recording_call(
    messages: list[object],
) -> None:
    """空上下文或非 Message 元素在消费响应前失败。"""

    llm = FakeLLM([assistant_message("不会被消费")])

    with pytest.raises(LLMInputError):
        llm.complete(messages)  # type: ignore[arg-type]

    assert llm.call_count == 0


@pytest.mark.parametrize(
    "responses",
    [
        [Message(role=MessageRole.USER, content="不是助手响应")],
        [object()],
    ],
)
def test_fake_llm_rejects_invalid_preset_responses(
    responses: list[object],
) -> None:
    """错误角色和非 Message 响应不能进入 Fake LLM。"""

    with pytest.raises(LLMResponseError):
        FakeLLM(responses)  # type: ignore[arg-type]


def test_llm_protocol_accepts_an_independent_adapter() -> None:
    """调用方可以只依赖 LLM 接口替换适配器。"""

    class AlternateLLM:
        def complete(self, messages: Sequence[Message]) -> Message:
            assert messages
            return assistant_message("来自另一适配器")

    adapter = AlternateLLM()

    assert isinstance(adapter, LLM)
    assert isinstance(FakeLLM([]), LLM)
    assert (
        adapter.complete([Message(role=MessageRole.USER, content="测试替换")]).content
        == "来自另一适配器"
    )
