"""Day 5 LLM 接口和 Fake LLM 的公开行为测试。"""

from collections.abc import Iterator, Sequence

import pytest

from self_react.llm import (
    LLM,
    FakeLLM,
    LLMInputError,
    LLMResponseError,
    LLMResponseExhaustedError,
    StreamChunk,
    collect_stream,
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
    """调用方可以只依赖 LLM 接口替换适配器（含流式方法）。"""

    class AlternateLLM:
        def complete(
            self,
            messages: Sequence[Message],
            *,
            tools: Sequence[object] | None = None,
        ) -> Message:
            assert messages
            return assistant_message("来自另一适配器")

        def complete_stream(
            self,
            messages: Sequence[Message],
            *,
            tools: Sequence[object] | None = None,
        ) -> Iterator[StreamChunk]:
            assert messages
            yield StreamChunk(content="来自另一适配器")

    adapter = AlternateLLM()

    assert isinstance(adapter, LLM)
    assert isinstance(FakeLLM([]), LLM)
    assert (
        adapter.complete([Message(role=MessageRole.USER, content="测试替换")]).content
        == "来自另一适配器"
    )
    streamed = list(
        adapter.complete_stream([Message(role=MessageRole.USER, content="测试替换")])
    )
    assert streamed == [StreamChunk(content="来自另一适配器")]


def test_llm_protocol_rejects_adapter_without_complete_stream() -> None:
    """缺少 complete_stream 的旧适配器不再满足 LLM 协议。"""

    class LegacyLLM:
        def complete(
            self,
            messages: Sequence[Message],
            *,
            tools: Sequence[object] | None = None,
        ) -> Message:
            assert messages
            return assistant_message("旧适配器")

    assert not isinstance(LegacyLLM(), LLM)


def test_fake_llm_complete_stream_chunks_content_deterministically() -> None:
    """Fake 流按固定大小切块：相同输入永远得到相同块序列。"""

    llm = FakeLLM([assistant_message("0123456789")])

    chunks = list(
        llm.complete_stream([Message(role=MessageRole.USER, content="计算 2 + 2")])
    )

    assert [chunk.content for chunk in chunks] == ["01234567", "89"]
    assert all(chunk.tool_calls == () for chunk in chunks)
    assert llm.call_count == 1


def test_fake_llm_complete_stream_carries_tool_calls_in_final_chunk() -> None:
    """工具调用不逐字符流式，而是在末尾块一次性携带完整 ToolCall。"""

    call = ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )
    llm = FakeLLM([Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])])

    chunks = list(
        llm.complete_stream([Message(role=MessageRole.USER, content="计算 2 + 2")])
    )

    assert chunks == [StreamChunk(content="", tool_calls=(call,))]
    assert collect_stream(chunks) == Message(
        role=MessageRole.ASSISTANT, content="", tool_calls=[call]
    )


def test_fake_llm_complete_stream_streams_native_final_answer_content() -> None:
    """原生 final_answer 工具调用的 content 参数按块经 final_answer_content 透出。"""

    call = ToolCall(
        call_id="call-1",
        name="final_answer",
        arguments={"content": "2 + 2 = 4。"},
    )
    llm = FakeLLM([Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])])

    chunks = list(
        llm.complete_stream([Message(role=MessageRole.USER, content="计算 2 + 2")])
    )

    assert "".join(chunk.final_answer_content for chunk in chunks) == "2 + 2 = 4。"
    assert all(chunk.content == "" for chunk in chunks)
    assert chunks[-1] == StreamChunk(content="", tool_calls=(call,))
    assert collect_stream(chunks) == Message(
        role=MessageRole.ASSISTANT, content="", tool_calls=[call]
    )


def test_fake_llm_complete_stream_ignores_non_final_tool_content() -> None:
    """非 final_answer 工具调用不产生 final_answer_content 增量。"""

    call = ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )
    llm = FakeLLM([Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])])

    chunks = list(
        llm.complete_stream([Message(role=MessageRole.USER, content="计算 2 + 2")])
    )

    assert all(chunk.final_answer_content == "" for chunk in chunks)
    assert collect_stream(chunks) == Message(
        role=MessageRole.ASSISTANT, content="", tool_calls=[call]
    )


def test_stream_chunk_rejects_non_string_final_answer_content() -> None:
    """StreamChunk.final_answer_content 必须是字符串。"""

    with pytest.raises(TypeError):
        StreamChunk(content="", final_answer_content=123)  # type: ignore[arg-type]


def test_fake_llm_complete_stream_consumes_presets_and_exhausts() -> None:
    """完整流式调用同样按序消耗预置响应；耗尽时报稳定错误且计入历史。"""

    llm = FakeLLM([assistant_message("第一"), assistant_message("第二")])
    context = [Message(role=MessageRole.USER, content="继续")]

    first = list(llm.complete_stream(context))
    second = list(llm.complete_stream(context))

    assert "".join(chunk.content for chunk in first) == "第一"
    assert "".join(chunk.content for chunk in second) == "第二"
    with pytest.raises(LLMResponseExhaustedError):
        list(llm.complete_stream(context))
    assert llm.call_count == 3


def test_fake_llm_complete_stream_records_input_and_tools() -> None:
    """流式调用与一次性调用共享调用历史，输入与工具清单被快照记录。"""

    class SampleTool:
        name = "calculator"
        description = "计算器"

    tool = SampleTool()
    llm = FakeLLM([assistant_message("完成")])

    chunks = list(
        llm.complete_stream(
            [Message(role=MessageRole.USER, content="计算 2 + 2")],
            tools=[tool],
        )
    )

    assert "".join(chunk.content for chunk in chunks) == "完成"
    recorded = llm.calls_with_tools[0]
    assert recorded[1] == (tool,)
    assert recorded[0][0].role is MessageRole.USER


@pytest.mark.parametrize("messages", [[], [object()]])
def test_fake_llm_complete_stream_rejects_invalid_input_without_consuming(
    messages: list[object],
) -> None:
    """空上下文或非 Message 元素在消耗响应前失败，不计入调用历史。"""

    llm = FakeLLM([assistant_message("不会被消耗")])

    with pytest.raises(LLMInputError):
        list(llm.complete_stream(messages))  # type: ignore[arg-type]

    assert llm.call_count == 0


def test_collect_stream_assembles_message_equivalent_to_complete() -> None:
    """流式组装结果与一次性 complete 结果等价（内容与工具调用均一致）。"""

    context = [Message(role=MessageRole.USER, content="计算 2 + 2")]
    response = assistant_message("答案是 4。")

    streamed = collect_stream(FakeLLM([response]).complete_stream(context))
    assert streamed == response

    call = ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )
    tool_response = Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])
    streamed_tool = collect_stream(FakeLLM([tool_response]).complete_stream(context))
    assert streamed_tool == tool_response


def test_collect_stream_rejects_non_chunk_items() -> None:
    """收集器只接受 StreamChunk，非法元素报稳定响应错误。"""

    with pytest.raises(LLMResponseError):
        collect_stream([object()])  # type: ignore[list-item]


def test_collect_stream_rejects_duplicate_tool_call_ids() -> None:
    """重复工具调用编号无法组装出合法 assistant Message，报稳定错误。"""

    call = ToolCall(call_id="call-1", name="calculator", arguments={})
    chunks = [
        StreamChunk(content="", tool_calls=(call,)),
        StreamChunk(content="", tool_calls=(call,)),
    ]

    with pytest.raises(LLMResponseError):
        collect_stream(chunks)


def test_stream_chunk_rejects_invalid_fields() -> None:
    """StreamChunk 的内容必须是字符串，工具调用必须是 ToolCall 元组。"""

    with pytest.raises(TypeError):
        StreamChunk(content=123)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        StreamChunk(content="", tool_calls=("not-a-call",))  # type: ignore[arg-type]
