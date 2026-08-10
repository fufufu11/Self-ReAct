"""Day 24 ContextPolicy 上下文裁剪与摘要回填测试（R-04）。

测试只依赖领域模型 ``Message``，不访问网络、不调用真实模型。覆盖：窗口
校验、未超限恒等、整轮原子裁剪、system/任务保留、摘要模板与截断、总量
上限、确定性，以及 ``prepare`` 不修改输入等公开行为。
"""

from __future__ import annotations

import json

import pytest

from self_react.memory import (
    DEFAULT_CONTEXT_WINDOW,
    SUMMARY_HEADING,
    SUMMARY_LINE_LIMIT,
    SUMMARY_TOTAL_LIMIT,
    ContextPolicy,
)
from self_react.models import Message, MessageRole, ToolCall


def _system(content: str = "系统提示词") -> Message:
    """构造 system 消息。"""

    return Message(role=MessageRole.SYSTEM, content=content)


def _task(content: str = "任务：计算并检索") -> Message:
    """构造首条 user 任务消息。"""

    return Message(role=MessageRole.USER, content=content)


def _assistant_tool_call(
    call_id: str,
    name: str,
    arguments: dict[str, object],
    *,
    content: str = "",
) -> Message:
    """构造携带原生工具调用的助手消息。"""

    return Message(
        role=MessageRole.ASSISTANT,
        content=content,
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
    )


def _tool_result(call_id: str, content: str) -> Message:
    """构造回指工具调用的 tool 消息。"""

    return Message(
        role=MessageRole.TOOL,
        content=content,
        tool_call_id=call_id,
    )


def _round(call_id: str, result: str) -> list[Message]:
    """构造"助手请求调用 calculator -> 工具结果"这一整轮。"""

    return [
        _assistant_tool_call(call_id, "calculator", {"expression": "2 + 2"}),
        _tool_result(call_id, result),
    ]


def _round_chars(round_messages: list[Message]) -> int:
    """按与实现一致的规则计算一轮的字符数。"""

    total = 0
    for message in round_messages:
        total += len(message.content)
        for call in message.tool_calls:
            total += (
                len(call.call_id)
                + len(call.name)
                + len(
                    json.dumps(
                        call.arguments,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            )
    return total


def _summary_messages(messages: list[Message]) -> list[Message]:
    """返回请求列表中紧随首条 system 的摘要消息（可能为空）。"""

    return [
        message
        for message in messages[1:]
        if message.role is MessageRole.SYSTEM
        and message.content.startswith(SUMMARY_HEADING)
    ]


def test_default_context_window_is_20000() -> None:
    """CLI 默认窗口是 20,000 字符，且为正整数。"""

    assert DEFAULT_CONTEXT_WINDOW == 20_000
    assert isinstance(DEFAULT_CONTEXT_WINDOW, int)


@pytest.mark.parametrize("value", [0, -1, 1.5, "100", True])
def test_context_policy_rejects_invalid_window(value: object) -> None:
    """窗口必须是正整数；零、负数、浮点、字符串与布尔值都被拒绝。"""

    with pytest.raises(ValueError, match="context_window"):
        ContextPolicy(context_window=value)  # type: ignore[arg-type]


def test_prepare_returns_copy_and_does_not_mutate_input() -> None:
    """``prepare`` 返回新列表，输入消息保持原样。"""

    policy = ContextPolicy(context_window=10_000)
    messages = [_system(), _task(), *_round("call-1", "4")]
    before = [message.model_copy(deep=True) for message in messages]

    result = policy.prepare(messages)

    assert result is not messages
    assert result == messages
    assert messages == before


def test_prepare_rejects_invalid_inputs() -> None:
    """非消息序列与含非 Message 项的输入被稳定拒绝。"""

    policy = ContextPolicy(context_window=100)
    with pytest.raises(TypeError, match="Message"):
        policy.prepare("不是消息列表")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Message"):
        policy.prepare([_system(), object()])  # type: ignore[list-item]
    assert policy.prepare([]) == []


def test_prepare_without_rounds_returns_unchanged() -> None:
    """只有 system + 任务时没有可裁剪内容，返回原样。"""

    messages = [_system(), _task()]

    result = ContextPolicy(context_window=1).prepare(messages)

    assert result == messages
    assert _summary_messages(result) == []


def test_prepare_under_window_keeps_all_messages() -> None:
    """未超限时不裁剪、不生成摘要，完整返回。"""

    rounds = [_round("call-1", "4"), _round("call-2", "6"), _round("call-3", "8")]
    messages = [_system(), _task(), *sum(rounds, [])]
    total = sum(_round_chars(round_messages) for round_messages in rounds)

    result = ContextPolicy(context_window=total + 100).prepare(messages)

    assert result == messages
    assert _summary_messages(result) == []


def test_prepare_at_exact_boundary_does_not_trim() -> None:
    """恰好等于窗口时同样不裁剪（上限是"超过才裁"）。"""

    rounds = [_round("call-1", "4"), _round("call-2", "6")]
    messages = [_system(), _task(), *sum(rounds, [])]
    total = sum(_round_chars(round_messages) for round_messages in rounds)

    result = ContextPolicy(context_window=total).prepare(messages)

    assert result == messages
    assert _summary_messages(result) == []


def test_prepare_trims_oldest_round_and_backfills_summary() -> None:
    """超限时从最旧轮开始整轮裁剪，并把被裁轮压成摘要回填。"""

    rounds = [_round("call-1", "4"), _round("call-2", "6"), _round("call-3", "8")]
    messages = [_system(), _task(), *sum(rounds, [])]
    total = sum(_round_chars(round_messages) for round_messages in rounds)

    result = ContextPolicy(context_window=total - _round_chars(rounds[0])).prepare(
        messages
    )

    # 只裁掉最旧一轮：摘要插在 system 与任务之间，剩余两轮原样保留
    assert [message.role for message in result] == [
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    summaries = _summary_messages(result)
    assert len(summaries) == 1
    assert "第 1 轮" in summaries[0].content
    assert "calculator" in summaries[0].content
    assert "2 + 2" in summaries[0].content
    assert "第 2 轮" not in summaries[0].content


def test_prepare_trims_until_under_window_and_keeps_system_task() -> None:
    """窗口极小（小于单轮）时裁掉全部轮次，system + 任务仍保留。"""

    rounds = [_round("call-1", "4"), _round("call-2", "6"), _round("call-3", "8")]
    messages = [_system(), _task(), *sum(rounds, [])]

    result = ContextPolicy(context_window=1).prepare(messages)

    assert [message.role for message in result] == [
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
        MessageRole.USER,
    ]
    summaries = _summary_messages(result)
    assert len(summaries) == 1
    for line in ("第 1 轮", "第 2 轮", "第 3 轮"):
        assert line in summaries[0].content


def test_prepare_never_splits_round_pairs() -> None:
    """裁剪以整轮为单位：请求里不会出现孤立的助手或工具消息。"""

    rounds = [_round("call-1", "4"), _round("call-2", "6")]
    messages = [_system(), _task(), *sum(rounds, [])]
    total = sum(_round_chars(round_messages) for round_messages in rounds)

    result = ContextPolicy(context_window=total - 1).prepare(messages)

    tool_call_ids = [
        call.call_id
        for message in result
        if message.role is MessageRole.ASSISTANT
        for call in message.tool_calls
    ]
    tool_message_ids = [
        message.tool_call_id for message in result if message.role is MessageRole.TOOL
    ]
    assert tool_call_ids == tool_message_ids


def test_prepare_summary_line_is_truncated() -> None:
    """超长工具结果会先压缩空白再按单行上限截断。"""

    long_result = "x " * 500
    messages = [
        _system(),
        _task(),
        *_round("call-1", long_result),
        *_round("call-2", "6"),
    ]

    result = ContextPolicy(context_window=1).prepare(messages)
    summaries = _summary_messages(result)
    line = summaries[0].content.splitlines()[1]

    assert len(line) <= SUMMARY_LINE_LIMIT
    assert line.endswith("…")


def test_prepare_summary_total_capped_with_marker() -> None:
    """摘要总量不超过上限，被省略的轮次用固定标记说明。"""

    rounds = [_round(f"call-{index}", "r" * 300) for index in range(1, 21)]
    messages = [_system(), _task(), *sum(rounds, [])]

    result = ContextPolicy(context_window=1).prepare(messages)
    summaries = _summary_messages(result)

    assert len(summaries) == 1
    assert len(summaries[0].content) <= SUMMARY_TOTAL_LIMIT
    assert summaries[0].content.endswith("（其余历史已省略）")
    assert "第 1 轮" in summaries[0].content
    assert "第 20 轮" not in summaries[0].content


def test_prepare_is_deterministic() -> None:
    """相同输入两次调用返回逐条相等的结果。"""

    rounds = [_round("call-1", "4"), _round("call-2", "6"), _round("call-3", "8")]
    messages = [_system(), _task(), *sum(rounds, [])]
    policy = ContextPolicy(context_window=1)

    first = policy.prepare(messages)
    second = policy.prepare(messages)

    assert first == second
    assert [message.content for message in first] == [
        message.content for message in second
    ]


def test_prepare_summarizes_parse_error_round_without_raw_output() -> None:
    """无工具调用的轮次（解析失败重试）用稳定描述，不泄漏原始输出。"""

    parse_round = [
        Message(
            role=MessageRole.ASSISTANT,
            content='{"kind": "bad", "secret": "raw-output"}',
        ),
        Message(role=MessageRole.USER, content="你的上一条输出无法解析，请重新输出。"),
    ]
    messages = [_system(), _task(), *parse_round, *_round("call-1", "4")]

    result = ContextPolicy(context_window=1).prepare(messages)
    summaries = _summary_messages(result)

    assert "未通过解析" in summaries[0].content
    assert "raw-output" not in summaries[0].content


def test_prepare_keeps_multi_tool_call_round_whole() -> None:
    """同一助手消息携带多个工具调用时，整轮一起保留或一起移除。"""

    multi_round = [
        Message(
            role=MessageRole.ASSISTANT,
            content="",
            tool_calls=[
                ToolCall(
                    call_id="call-1",
                    name="calculator",
                    arguments={"expression": "2 + 2"},
                ),
                ToolCall(
                    call_id="call-2",
                    name="retrieve",
                    arguments={"query": "react"},
                ),
            ],
        ),
        _tool_result("call-1", "4"),
        _tool_result("call-2", "react 说明"),
    ]
    messages = [_system(), _task(), *multi_round]
    total = _round_chars(multi_round)

    kept = ContextPolicy(context_window=total).prepare(messages)
    assert kept == messages

    trimmed = ContextPolicy(context_window=total - 1).prepare(messages)
    assert [message.role for message in trimmed] == [
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
        MessageRole.USER,
    ]
    assert "第 1 轮" in trimmed[1].content


def test_prepare_counts_tool_calls_toward_budget() -> None:
    """工具调用的 call_id、name 与参数 JSON 都计入字符预算。"""

    round_messages = [
        _assistant_tool_call("c", "calculator", {"expression": "2 + 2"}),
        _tool_result("c", "4"),
    ]
    messages = [_system(), _task(), *round_messages]
    total = _round_chars(round_messages)

    assert ContextPolicy(context_window=total).prepare(messages) == messages
    trimmed = ContextPolicy(context_window=total - 1).prepare(messages)
    assert len(trimmed) == 3
    assert _summary_messages(trimmed) != []
