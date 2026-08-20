"""立项 A：解析失败每失败序列有界重试的公开行为测试（Issue #85）。

测试只依赖 Fake LLM 与确定性工具，不访问网络、不调用真实 API。公开缝是
``Agent.run(task) -> AgentState``：断言主循环文本 JSON 解析失败后按"每个
失败序列独立有界重试"语义处理——默认每个序列最多重试 2 次（连续失败 3 次
才以 ``MODEL_OUTPUT_PARSE_ERROR`` 终止），任一解析成功即恢复重试资格；
``parse_retry_limit`` 可配置，置 0 关闭（首次失败即终止）；预算恰好耗尽时
即便仍有重试资格也不发起重试。
"""

from __future__ import annotations

import json

from self_react.agent import Agent
from self_react.llm import FakeLLM
from self_react.models import Message, MessageRole, TerminationReason, TraceErrorCode
from self_react.tools import CalculatorTool, ToolRegistry


def _json_message(raw: str) -> Message:
    """构造一条把原始 JSON 放在 content 里的助手消息。"""

    return Message(role=MessageRole.ASSISTANT, content=raw)


def _final_answer_json(content: str) -> Message:
    """构造一条符合 Day 10 契约的最终回答原始输出。"""

    return _json_message(
        json.dumps({"kind": "final_answer", "content": content}, ensure_ascii=False)
    )


def _tool_call_json(
    call_id: str,
    name: str,
    arguments: dict[str, object],
) -> Message:
    """构造一条符合 Day 10 契约的工具调用原始输出。"""

    return _json_message(
        json.dumps(
            {
                "kind": "tool_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            },
            ensure_ascii=False,
        )
    )


def _registry() -> ToolRegistry:
    """注册 calculator 的最小注册表。"""

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


def test_parse_retry_recovers_between_failures() -> None:
    """失败→成功→再失败→再成功：两个失败序列各自恢复重试资格，最终正常收尾。"""

    llm = FakeLLM(
        [
            _json_message("坏输出 1"),
            _tool_call_json("c1", "calculator", {"expression": "1 + 1"}),
            _json_message("坏输出 2"),
            _final_answer_json("完成。"),
        ]
    )

    state = Agent(llm=llm, registry=_registry(), max_steps=5).run("计算")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.final_answer is not None
    assert state.steps_used == 4
    assert llm.call_count == 4

    first_failure = state.trace[0]
    assert first_failure.error is not None
    assert first_failure.error.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    assert first_failure.error.retryable is True

    second_failure = state.trace[2]
    assert second_failure.error is not None
    assert second_failure.error.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    # 关键：第二个失败序列（中间被成功的工具调用打断）仍未耗尽重试资格
    assert second_failure.error.retryable is True


def test_consecutive_parse_failures_terminate_after_default_limit() -> None:
    """连续失败达默认上限（重试 2 次）：第 3 次连续失败以解析错误终止。"""

    llm = FakeLLM(
        [
            _json_message("坏输出 1"),
            _json_message("坏输出 2"),
            _json_message("坏输出 3"),
        ]
    )

    state = Agent(llm=llm, registry=_registry(), max_steps=5).run("任务")

    assert state.termination_reason is TerminationReason.MODEL_OUTPUT_PARSE_ERROR
    assert state.final_answer is None
    assert state.steps_used == 3
    assert llm.call_count == 3

    for step in state.trace:
        assert step.error is not None
        assert step.error.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    assert state.trace[0].error.retryable is True
    assert state.trace[1].error.retryable is True
    assert state.trace[2].error.retryable is False


def test_parse_retry_limit_custom_value() -> None:
    """parse_retry_limit=1：每个失败序列最多重试 1 次，连续失败 2 次即终止。"""

    llm = FakeLLM([_json_message("坏输出 1"), _json_message("坏输出 2")])

    state = Agent(llm=llm, registry=_registry(), max_steps=5, parse_retry_limit=1).run(
        "任务"
    )

    assert state.termination_reason is TerminationReason.MODEL_OUTPUT_PARSE_ERROR
    assert state.steps_used == 2
    assert llm.call_count == 2
    assert state.trace[0].error is not None
    assert state.trace[0].error.retryable is True
    assert state.trace[1].error is not None
    assert state.trace[1].error.retryable is False


def test_parse_retry_limit_zero_disables_retry() -> None:
    """parse_retry_limit=0 关闭有界重试：首次解析失败即终止。"""

    llm = FakeLLM([_json_message("坏输出 1")])

    state = Agent(llm=llm, registry=_registry(), max_steps=5, parse_retry_limit=0).run(
        "任务"
    )

    assert state.termination_reason is TerminationReason.MODEL_OUTPUT_PARSE_ERROR
    assert state.steps_used == 1
    assert llm.call_count == 1
    assert state.trace[0].error is not None
    assert state.trace[0].error.retryable is False


def test_parse_retry_budget_exhausted_no_retry() -> None:
    """预算恰好耗尽时不发起重试：即便仍有重试资格，也以解析错误终止。"""

    llm = FakeLLM([_json_message("坏输出 1")])

    state = Agent(llm=llm, registry=_registry(), max_steps=1).run("任务")

    assert state.termination_reason is TerminationReason.MODEL_OUTPUT_PARSE_ERROR
    assert state.steps_used == 1
    assert llm.call_count == 1
    assert state.trace[0].error is not None
    assert state.trace[0].error.retryable is False
    # 没有预算就不会发起重试，也不会写回错误反馈
    assert [message.role for message in state.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
