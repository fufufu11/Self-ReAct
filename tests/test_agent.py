"""Day 12 Agent 主循环的公开行为测试（Day 14 补充鲁棒性边界）。

测试只依赖 Fake LLM、三个真实工具与一个确定性失败工具，不访问网络、不调用
真实 API。公开缝是 ``Agent.run(task) -> AgentState``：断言终止原因、轨迹、
消息上下文和 ``steps_used``/``trace`` 不变量，不触碰 Agent 内部实现。
Day 14 补充了模型超时/连接失败按原样传播、重复动作（同一 ``call_id`` 复用或
同一工具连续相同参数）先作为 Observation 回写、预算耗尽兜底等回归测试。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence

import pytest

from self_react.agent import Agent
from self_react.llm import (
    LLM,
    FakeLLM,
    LLMProviderError,
    LLMProviderErrorCode,
    StreamChunk,
)
from self_react.memory import SUMMARY_HEADING, ContextPolicy
from self_react.models import (
    FinalAnswer,
    Message,
    MessageRole,
    Plan,
    Reflection,
    TerminationReason,
    ToolCall,
    ToolErrorCode,
    TraceErrorCode,
    TraceStep,
)
from self_react.prompts import render_system_prompt
from self_react.tools import (
    CalculatorTool,
    FileReaderTool,
    FinalAnswerTool,
    RetrieveTool,
    ToolExecutionError,
    ToolRegistry,
)
from self_react.tools.retrieve import KNOWLEDGE_BASE


def _json_message(raw: str) -> Message:
    """构造一条把原始 JSON 放在 content 里的助手消息。"""

    return Message(role=MessageRole.ASSISTANT, content=raw)


def _final_answer_json(content: str) -> Message:
    """构造一条符合 Day 10 契约的最终回答原始输出。"""

    return _json_message(
        json.dumps(
            {"kind": "final_answer", "content": content},
            ensure_ascii=False,
        )
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


def _plan_json(content: str) -> Message:
    """构造一条符合 R-06 规划契约的计划原始输出。"""

    return _json_message(
        json.dumps({"kind": "plan", "content": content}, ensure_ascii=False)
    )


def _reflection_json(content: str) -> Message:
    """构造一条符合 R-06 反思契约的反思原始输出。"""

    return _json_message(
        json.dumps({"kind": "reflection", "content": content}, ensure_ascii=False)
    )


def _default_registry() -> ToolRegistry:
    """注册三个真实业务工具与 final_answer 特殊工具。"""

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(FileReaderTool(root_directory="C:/allowed"))
    registry.register(RetrieveTool())
    registry.register(FinalAnswerTool())
    return registry


def test_task_to_final_answer_terminates_without_tools() -> None:
    """任务直达最终回答：一轮结束，带 final_answer，不调用任何工具。"""

    llm = FakeLLM([_final_answer_json("答案是 4。")])
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=3).run("计算 2 + 2")

    assert state.is_terminated is True
    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.final_answer == FinalAnswer(content="答案是 4。")
    assert state.steps_used == 1
    assert len(state.trace) == 1
    assert state.steps_used == len(state.trace)
    assert state.trace[0].decision == FinalAnswer(content="答案是 4。")
    assert state.trace[0].observation is None
    assert state.trace[0].error is None
    assert llm.call_count == 1
    assert [message.role for message in state.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]


def test_system_message_uses_day10_prompt_and_state_tracks_tools() -> None:
    """system 消息由 Day 10 提示词渲染，状态记录可用工具名称。"""

    registry = _default_registry()
    llm = FakeLLM([_final_answer_json("完成")])

    state = Agent(llm=llm, registry=registry, max_steps=1).run("回答我")

    assert state.messages[0].role is MessageRole.SYSTEM
    assert state.messages[0].content == render_system_prompt(
        [
            CalculatorTool(),
            FileReaderTool(root_directory="C:/allowed"),
            FinalAnswerTool(),
            RetrieveTool(),
        ]
    )
    assert state.messages[1] == Message(role=MessageRole.USER, content="回答我")
    assert state.available_tools == [
        "calculator",
        "file_reader",
        "retrieve",
        "final_answer",
    ]


def test_run_injects_extra_instructions_into_system_message() -> None:
    """``run`` 的 extra_instructions 出现在 system 消息，默认与基线一致。"""

    registry = _default_registry()
    extra = "【本次任务指引】\n只用 logs.ndjson。"

    with_extra = Agent(
        llm=FakeLLM([_final_answer_json("完成")]),
        registry=registry,
        max_steps=1,
    ).run("回答我", extra_instructions=extra)
    without_extra = Agent(
        llm=FakeLLM([_final_answer_json("完成")]),
        registry=registry,
        max_steps=1,
    ).run("回答我")

    baseline_tools = [
        CalculatorTool(),
        FileReaderTool(root_directory="C:/allowed"),
        FinalAnswerTool(),
        RetrieveTool(),
    ]
    assert with_extra.messages[0].content == render_system_prompt(
        baseline_tools, extra_instructions=extra
    )
    assert extra in with_extra.messages[0].content
    assert without_extra.messages[0].content == render_system_prompt(baseline_tools)
    assert extra not in without_extra.messages[0].content


def test_run_rejects_non_string_extra_instructions() -> None:
    """``extra_instructions`` 必须是字符串，非法类型明确拒绝。"""

    agent = Agent(
        llm=FakeLLM([_final_answer_json("完成")]),
        registry=_default_registry(),
        max_steps=1,
    )

    with pytest.raises(TypeError):
        agent.run("回答我", extra_instructions=123)  # type: ignore[arg-type]


def test_single_tool_call_writes_observation_then_final_answer() -> None:
    """单轮工具调用：ToolCall -> ToolResult -> Observation 写回 -> 下一轮。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _final_answer_json("结果是 4。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=3).run("计算 2 + 2")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 2
    assert len(state.trace) == 2
    assert state.steps_used == len(state.trace)

    tool_step = state.trace[0]
    assert isinstance(tool_step.decision, ToolCall)
    assert tool_step.decision.name == "calculator"
    assert tool_step.observation is not None
    assert tool_step.observation.is_error is False
    assert tool_step.observation.content == "4"
    assert tool_step.observation.tool_call_id == "call-1"

    final_step = state.trace[1]
    assert final_step.decision == FinalAnswer(content="结果是 4。")

    assert [message.role for message in state.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    tool_message = state.messages[3]
    assert tool_message.role is MessageRole.TOOL
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.content == "4"

    # 第二轮模型调用必须能看到写回的观察
    second_call = llm.calls[1]
    assert second_call[-1].role is MessageRole.TOOL
    assert second_call[-1].tool_call_id == "call-1"
    assert second_call[-1].content == "4"


def test_multi_round_tool_calls_cover_three_real_tools() -> None:
    """多轮工具调用串起三个真实工具，观察逐一写回消息上下文。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "2 * 3"}),
            _tool_call_json("call-2", "retrieve", {"query": "react"}),
            _tool_call_json("call-3", "file_reader", {"path": "notes.txt"}),
            _final_answer_json("计算完成，并查到了资料与文件。"),
        ]
    )
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=5).run("综合任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 4
    assert len(state.trace) == 4
    assert state.steps_used == len(state.trace)

    observations = [step.observation for step in state.trace]
    assert observations[0] is not None and observations[0].content == "6"
    assert (
        observations[1] is not None
        and observations[1].content == KNOWLEDGE_BASE["react"]
    )
    assert observations[2] is not None
    assert observations[2].is_error is True
    assert observations[2].error_code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert observations[2].retryable is True

    # 工具消息按调用顺序写回，且各自回指对应 call_id
    tool_messages = [
        message for message in state.messages if message.role is MessageRole.TOOL
    ]
    assert [message.tool_call_id for message in tool_messages] == [
        "call-1",
        "call-2",
        "call-3",
    ]


def test_file_reader_observation_round_trip_with_tmp_root(tmp_path: object) -> None:
    """file_reader 真实读取临时文件，观察内容与文件内容一致。"""

    notes = tmp_path / "notes.txt"
    notes.write_text("采购清单：牛奶", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(FileReaderTool(root_directory=str(tmp_path)))
    llm = FakeLLM(
        [
            _tool_call_json("call-1", "file_reader", {"path": "notes.txt"}),
            _final_answer_json("已读取文件。"),
        ]
    )

    state = Agent(llm=llm, registry=registry, max_steps=2).run("读取 notes.txt")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    observation = state.trace[0].observation
    assert observation is not None
    assert observation.is_error is False
    assert observation.content == "采购清单：牛奶"


def test_steps_exhausted_never_calls_model_beyond_budget() -> None:
    """步数耗尽：恰好消耗完预算，绝不发起第 max_steps + 1 次模型调用。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "1 + 1"}),
            _tool_call_json("call-2", "calculator", {"expression": "2 + 2"}),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=2).run("算两步")

    assert state.termination_reason is TerminationReason.MAX_STEPS_EXCEEDED
    assert state.final_answer is None
    assert state.steps_used == 2
    assert len(state.trace) == 2
    assert state.steps_used == len(state.trace)
    assert llm.call_count == 2
    # 最后一步的观察已写回，但循环在此停止，不再调用模型
    assert state.messages[-1].role is MessageRole.TOOL
    assert state.messages[-1].content == "4"


def test_zero_max_steps_terminates_immediately() -> None:
    """max_steps 为 0 时不调用模型，直接返回 MAX_STEPS_EXCEEDED 与空轨迹。"""

    llm = FakeLLM([])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=0).run("任何任务")

    assert state.termination_reason is TerminationReason.MAX_STEPS_EXCEEDED
    assert state.final_answer is None
    assert state.steps_used == 0
    assert state.trace == []
    assert llm.call_count == 0


def test_parse_error_retry_once_then_tool_call_and_final_answer() -> None:
    """解析失败有界重试（重试一次成功）：回写稳定错误、消耗一步，重试成功后走正常工具分支。"""

    llm = FakeLLM(
        [
            _json_message("这不是 JSON"),
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _final_answer_json("结果是 4。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=4).run("计算 2 + 2")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.final_answer == FinalAnswer(content="结果是 4。")
    assert state.steps_used == 3
    assert len(state.trace) == 3
    assert llm.call_count == 3

    error_step = state.trace[0]
    assert error_step.decision is None
    assert error_step.observation is None
    assert error_step.error is not None
    assert error_step.error.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    assert error_step.error.retryable is True
    assert "Traceback" not in error_step.error.message
    assert "这不是 JSON" not in error_step.error.message

    tool_step = state.trace[1]
    assert isinstance(tool_step.decision, ToolCall)
    assert tool_step.decision.name == "calculator"
    assert tool_step.observation is not None
    assert tool_step.observation.content == "4"
    assert state.trace[2].decision == FinalAnswer(content="结果是 4。")

    # 重试轮（第二轮模型调用）的消息上下文以稳定错误反馈结尾
    retry_messages = llm.calls[1]
    feedback = retry_messages[-1]
    assert feedback.role is MessageRole.USER
    assert "你的上一条输出无法解析" in feedback.content
    assert "这不是 JSON" not in feedback.content


def test_parse_error_retry_still_fails_terminates() -> None:
    """解析失败重试仍失败：第二次失败不再重试，以 MODEL_OUTPUT_PARSE_ERROR 终止。"""

    llm = FakeLLM(
        [
            _json_message("这不是 JSON"),
            _json_message('{"kind": "tool_call"}'),
        ]
    )
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=3).run("任务")

    assert state.termination_reason is TerminationReason.MODEL_OUTPUT_PARSE_ERROR
    assert state.final_answer is None
    assert state.steps_used == 2
    assert len(state.trace) == 2
    assert llm.call_count == 2

    first, second = state.trace
    assert first.error is not None
    assert first.error.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    assert first.error.retryable is True
    assert second.error is not None
    assert second.error.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    assert second.error.retryable is False
    assert second.decision is None
    assert second.observation is None
    # 重试仍失败后直接终止，没有第三次模型调用
    assert state.messages[-1].role is MessageRole.ASSISTANT


def test_parse_error_budget_exhausted_terminates_without_retry() -> None:
    """预算恰好耗尽：解析失败消耗唯一一步后无预算重试，直接以解析失败终止。"""

    llm = FakeLLM([_json_message("这不是 JSON")])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=1).run("任务")

    assert state.termination_reason is TerminationReason.MODEL_OUTPUT_PARSE_ERROR
    assert state.final_answer is None
    assert state.steps_used == 1
    assert len(state.trace) == 1
    assert llm.call_count == 1
    step = state.trace[0]
    assert step.decision is None
    assert step.observation is None
    assert step.error is not None
    assert step.error.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    assert step.error.retryable is False
    # 没有预算就不会发起重试，也没有写回错误反馈
    assert [message.role for message in state.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]


def test_parse_error_message_is_stable_and_does_not_leak_raw_output() -> None:
    """解析失败的消息保持稳定，不携带模型原始输出。"""

    llm = FakeLLM(
        [
            _json_message('{"kind": "final_answer", "content": 123}'),
            _final_answer_json("完成。"),
        ]
    )
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=2).run("任务")

    message = state.trace[0].error.message
    assert message == "content 必须是字符串"
    assert "123" not in message
    assert "ValidationError" not in message


def test_parse_error_feedback_message_is_stable_and_does_not_leak_raw_output() -> None:
    """错误反馈消息稳定且不泄漏原始输出：原始字符串、数值与异常细节都不出现。"""

    raw = '{"kind": "final_answer", "content": 123}'
    llm = FakeLLM([_json_message(raw), _final_answer_json("完成。")])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=2).run("任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    feedback = llm.calls[1][-1]
    assert feedback.role is MessageRole.USER
    assert "你的上一条输出无法解析" in feedback.content
    assert "content 必须是字符串" in feedback.content
    assert raw not in feedback.content
    assert "123" not in feedback.content
    assert "ValidationError" not in feedback.content
    assert "Traceback" not in feedback.content


def test_parse_error_retry_is_at_most_once_per_run() -> None:
    """一次运行内至多重试一次：重试成功后再次解析失败不再获得重试机会。"""

    llm = FakeLLM(
        [
            _json_message("这不是 JSON"),
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _json_message("又坏了"),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=4).run("计算 2 + 2")

    assert state.termination_reason is TerminationReason.MODEL_OUTPUT_PARSE_ERROR
    assert state.steps_used == 3
    assert len(state.trace) == 3
    assert llm.call_count == 3

    second_error = state.trace[2]
    assert second_error.error is not None
    assert second_error.error.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    assert second_error.error.retryable is False
    # 第二次解析失败没有触发又一次重试：消息末尾是失败的那条 assistant 消息
    assert state.messages[-1].role is MessageRole.ASSISTANT


def test_unknown_tool_becomes_observation_then_final_answer() -> None:
    """未知工具先作为 Observation 回写（含 UNKNOWN_TOOL 与 retryable），预算内继续。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "unknown_tool", {}),
            _final_answer_json("好的，我改用正确工具。"),
        ]
    )
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=3).run("任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    observation = state.trace[0].observation
    assert observation is not None
    assert observation.is_error is True
    assert observation.error_code is ToolErrorCode.UNKNOWN_TOOL
    assert observation.retryable is True
    assert "unknown_tool" in observation.content
    assert "calculator" in observation.content
    assert state.messages[3].role is MessageRole.TOOL
    assert state.messages[3].tool_call_id == "call-1"


def test_unknown_tool_followed_by_budget_exhaustion_terminates() -> None:
    """未知工具后预算耗尽：终止原因为 MAX_STEPS_EXCEEDED，观察仍被记录。"""

    llm = FakeLLM([_tool_call_json("call-1", "unknown_tool", {})])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=1).run("任务")

    assert state.termination_reason is TerminationReason.MAX_STEPS_EXCEEDED
    assert state.steps_used == 1
    assert state.trace[0].observation is not None
    assert state.trace[0].observation.error_code is ToolErrorCode.UNKNOWN_TOOL
    assert llm.call_count == 1


def test_retryable_tool_failure_then_recovery() -> None:
    """可恢复工具失败先作为 Observation 回写，模型换一种方式后正常结束。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "retrieve", {"query": "unknown-topic"}),
            _final_answer_json("没有找到该主题。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(RetrieveTool())

    state = Agent(llm=llm, registry=registry, max_steps=2).run("查一个主题")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    observation = state.trace[0].observation
    assert observation is not None
    assert observation.is_error is True
    assert observation.error_code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert observation.retryable is True
    assert "unknown-topic" in observation.content
    assert llm.calls[1][-1].role is MessageRole.TOOL


class FailingTool:
    """确定性失败工具：按配置抛出不可恢复的工具执行错误。"""

    name = "failing"
    description = "确定性失败工具"

    def execute(self, arguments: dict[str, object]) -> str:
        raise ToolExecutionError("存储已满", retryable=False)


def test_non_retryable_tool_failure_terminates_with_tool_reason() -> None:
    """不可恢复的工具失败成为终止原因，不再发起下一轮模型调用。"""

    registry = ToolRegistry()
    registry.register(FailingTool())
    llm = FakeLLM([_tool_call_json("call-1", "failing", {})])

    state = Agent(llm=llm, registry=registry, max_steps=3).run("任务")

    assert state.termination_reason is TerminationReason.TOOL_EXECUTION_ERROR
    assert state.final_answer is None
    assert state.steps_used == 1
    observation = state.trace[0].observation
    assert observation is not None
    assert observation.is_error is True
    assert observation.error_code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert observation.retryable is False
    assert llm.call_count == 1


def test_input_summary_tracks_task_and_latest_observation() -> None:
    """每轮输入摘要在首轮使用任务文本，之后使用最近一条工具观察。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "1 + 1"}),
            _final_answer_json("完成"),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=2).run("帮我计算")

    assert state.trace[0].input_summary == "帮我计算"
    assert state.trace[1].input_summary == "2"


@pytest.mark.parametrize(
    ("responses", "max_steps", "reason"),
    [
        ([_final_answer_json("完成")], 3, TerminationReason.FINAL_ANSWER),
        (
            [
                _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
                _final_answer_json("结果是 4。"),
            ],
            3,
            TerminationReason.FINAL_ANSWER,
        ),
        (
            [_tool_call_json("call-1", "calculator", {"expression": "2 + 2"})],
            1,
            TerminationReason.MAX_STEPS_EXCEEDED,
        ),
        (
            [_json_message("坏输出"), _json_message("坏输出")],
            2,
            TerminationReason.MODEL_OUTPUT_PARSE_ERROR,
        ),
    ],
)
def test_state_invariants_hold_after_every_run(
    responses: Sequence[Message],
    max_steps: int,
    reason: TerminationReason,
) -> None:
    """无论哪种终止路径，状态不变量 steps_used == len(trace) 且不超预算。"""

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    llm = FakeLLM(list(responses))

    state = Agent(llm=llm, registry=registry, max_steps=max_steps).run("任务")

    assert state.is_terminated is True
    assert state.termination_reason is reason
    assert state.steps_used == len(state.trace)
    assert state.steps_used <= state.max_steps


def test_trace_steps_record_duration_and_bounded_summary() -> None:
    """轨迹步骤记录非负耗时，且输入摘要不超过领域模型上限。"""

    llm = FakeLLM([_final_answer_json("完成")])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=2).run("任务")

    step = state.trace[0]
    assert step.duration_ms is not None
    assert step.duration_ms >= 0
    assert len(step.input_summary) <= 2_000


@pytest.mark.parametrize("max_steps", [-1, 1.5, True])
def test_agent_rejects_invalid_max_steps(max_steps: object) -> None:
    """max_steps 必须是非负整数；负数、浮点数和布尔值被拒绝。"""

    with pytest.raises(ValueError):
        Agent(llm=FakeLLM([]), registry=_default_registry(), max_steps=max_steps)  # type: ignore[arg-type]


def test_agent_rejects_non_llm_and_non_registry() -> None:
    """构造参数必须满足 LLM 协议且是 ToolRegistry 实例。"""

    with pytest.raises(TypeError):
        Agent(llm=object(), registry=_default_registry(), max_steps=1)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        Agent(llm=FakeLLM([]), registry=object(), max_steps=1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (LLMProviderErrorCode.TIMEOUT, "模型请求超时"),
        (LLMProviderErrorCode.CONNECTION, "无法连接模型服务"),
    ],
)
def test_llm_provider_error_propagates_unchanged(
    code: LLMProviderErrorCode,
    message: str,
) -> None:
    """模型超时/连接失败按原样向上传播：主循环不重试、不吞掉错误。"""

    class TimeoutLLM:
        """确定性适配器：每次 complete 都抛稳定供应商错误。"""

        def complete(
            self,
            messages: Sequence[Message],
            *,
            tools: Sequence[object] | None = None,
        ) -> Message:
            raise LLMProviderError(code, message)

        def complete_stream(
            self,
            messages: Sequence[Message],
            *,
            tools: Sequence[object] | None = None,
        ) -> Iterator[StreamChunk]:
            response = self.complete(messages, tools=tools)
            yield StreamChunk(content=response.content)

    registry = _default_registry()
    llm = TimeoutLLM()
    assert isinstance(llm, LLM)

    with pytest.raises(LLMProviderError) as caught:
        Agent(llm=llm, registry=registry, max_steps=3).run("任务")

    assert caught.value.code is code
    assert str(caught.value) == message
    assert caught.value.code is not LLMProviderErrorCode.UNKNOWN


def test_repeated_call_id_writes_observation_then_recovery() -> None:
    """重复动作（复用 call_id）：先作为失败 Observation 回写，模型换新编号后继续。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _final_answer_json("我换了一个新编号，结果还是 4。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=3).run("计算 2 + 2")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    repeated_step = state.trace[1]
    assert isinstance(repeated_step.decision, ToolCall)
    assert repeated_step.decision.call_id == "call-1"
    observation = repeated_step.observation
    assert observation is not None
    assert observation.is_error is True
    assert observation.error_code is ToolErrorCode.REPEATED_ACTION
    assert observation.retryable is True
    assert "call-1" in observation.content
    # 重复动作没有执行工具：观察必须由 Agent 在分派前拦截生成
    tool_messages = [
        message for message in state.messages if message.role is MessageRole.TOOL
    ]
    assert tool_messages[1].tool_call_id == "call-1"
    assert llm.call_count == 3


def test_consecutive_identical_tool_call_is_repeated_action() -> None:
    """重复动作（同一工具连续相同参数）：写回失败观察，预算内可恢复。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "retrieve", {"query": "python"}),
            _tool_call_json("call-2", "retrieve", {"query": "python"}),
            _final_answer_json("同样的查询不需要再执行一次。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(RetrieveTool())

    state = Agent(llm=llm, registry=registry, max_steps=3).run("检索 python")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 3
    repeated_step = state.trace[1]
    assert isinstance(repeated_step.decision, ToolCall)
    assert repeated_step.decision.name == "retrieve"
    observation = repeated_step.observation
    assert observation is not None
    assert observation.is_error is True
    assert observation.error_code is ToolErrorCode.REPEATED_ACTION
    assert observation.retryable is True
    assert "retrieve" in observation.content


def test_repeated_action_does_not_execute_tool_again() -> None:
    """重复动作在分派前被拦截：确定性工具不能因为重复调用被再次执行。"""

    class CountingCalculator(CalculatorTool):
        """带调用计数的计算器，用来断言重复动作没有触达工具层。"""

        def __init__(self) -> None:
            self.call_count = 0

        def execute(self, arguments: dict[str, object]) -> str:
            self.call_count += 1
            return super().execute(arguments)

    tool = CountingCalculator()
    registry = ToolRegistry()
    registry.register(tool)
    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _tool_call_json("call-2", "calculator", {"expression": "2 + 2"}),
            _final_answer_json("重复调用已被拦截。"),
        ]
    )

    state = Agent(llm=llm, registry=registry, max_steps=3).run("计算 2 + 2")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert tool.call_count == 1
    assert state.trace[1].observation is not None
    assert state.trace[1].observation.is_error is True
    assert state.trace[1].observation.error_code is ToolErrorCode.REPEATED_ACTION


def test_repeated_action_then_budget_exhaustion_terminates() -> None:
    """重复动作后预算耗尽：失败观察仍被记录，终止原因为 MAX_STEPS_EXCEEDED。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "1 + 1"}),
            _tool_call_json("call-2", "calculator", {"expression": "1 + 1"}),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=2).run("算一步")

    assert state.termination_reason is TerminationReason.MAX_STEPS_EXCEEDED
    assert state.steps_used == 2
    observation = state.trace[1].observation
    assert observation is not None
    assert observation.is_error is True
    assert observation.error_code is ToolErrorCode.REPEATED_ACTION
    assert state.messages[-1].role is MessageRole.TOOL
    assert llm.call_count == 2


def test_non_consecutive_identical_tool_call_is_not_repeated() -> None:
    """中间隔了其他动作的相同调用是合法新调用，不应被误判为重复动作。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "1 + 1"}),
            _tool_call_json("call-2", "retrieve", {"query": "react"}),
            _tool_call_json("call-3", "calculator", {"expression": "1 + 1"}),
            _final_answer_json("两次计算都执行了。"),
        ]
    )
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=4).run("综合任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 4
    assert all(step.observation is not None for step in state.trace[:3])
    assert all(
        step.observation is None or not step.observation.is_error
        for step in state.trace[:3]
    )
    assert state.trace[2].observation is not None
    assert state.trace[2].observation.content == "2"


def test_non_consecutive_call_id_reuse_is_still_repeated() -> None:
    """call_id 在任何位置复用都是重复动作，不受"连续"限制。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "1 + 1"}),
            _tool_call_json("call-2", "retrieve", {"query": "react"}),
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _final_answer_json("我换了新编号继续。"),
        ]
    )
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=4).run("综合任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    repeated_step = state.trace[2]
    assert isinstance(repeated_step.decision, ToolCall)
    assert repeated_step.decision.call_id == "call-1"
    observation = repeated_step.observation
    assert observation is not None
    assert observation.is_error is True
    assert observation.error_code is ToolErrorCode.REPEATED_ACTION
    assert "call-1" in observation.content


def test_agent_accepts_any_llm_protocol_adapter() -> None:
    """只要满足 LLM 协议，Agent 就可以替换任何适配器。"""

    class FixedLLM:
        def complete(
            self,
            messages: Sequence[Message],
            *,
            tools: Sequence[object] | None = None,
        ) -> Message:
            return _final_answer_json("固定回答")

        def complete_stream(
            self,
            messages: Sequence[Message],
            *,
            tools: Sequence[object] | None = None,
        ) -> Iterator[StreamChunk]:
            response = self.complete(messages, tools=tools)
            yield StreamChunk(content=response.content)

    adapter = FixedLLM()
    assert isinstance(adapter, LLM)

    state = Agent(llm=adapter, registry=_default_registry(), max_steps=1).run("任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.final_answer == FinalAnswer(content="固定回答")


def test_agent_consumes_native_tool_calls_from_assistant_message() -> None:
    """供应商原生 tool_calls：Agent 直接执行工具，不经过文本 JSON 解析。"""

    call = ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )
    llm = FakeLLM(
        [
            Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call]),
            _final_answer_json("结果是 4。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=3).run("计算 2 + 2")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 2
    tool_step = state.trace[0]
    assert tool_step.decision == call
    assert tool_step.observation is not None
    assert tool_step.observation.is_error is False
    assert tool_step.observation.content == "4"
    tool_messages = [
        message for message in state.messages if message.role is MessageRole.TOOL
    ]
    assert tool_messages[0].tool_call_id == "call-1"


def test_agent_executes_first_tool_and_answers_extra_calls_as_failure() -> None:
    """多 tool_calls：只执行第一个，其余写成可恢复失败观察并写回消息。"""

    calculator_call = ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )
    retrieve_call = ToolCall(
        call_id="call-2",
        name="retrieve",
        arguments={"query": "react"},
    )
    llm = FakeLLM(
        [
            Message(
                role=MessageRole.ASSISTANT,
                content="",
                tool_calls=[calculator_call, retrieve_call],
            ),
            _final_answer_json("完成。"),
        ]
    )
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=3).run("综合任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 2
    first_step = state.trace[0]
    assert first_step.decision == calculator_call
    assert first_step.observation is not None
    assert first_step.observation.is_error is False
    assert first_step.observation.content == "4"

    tool_messages = [
        message for message in state.messages if message.role is MessageRole.TOOL
    ]
    assert [message.tool_call_id for message in tool_messages] == [
        "call-1",
        "call-2",
    ]
    assert tool_messages[0].content == "4"
    assert "后续轮次" in tool_messages[1].content
    assert state.trace[1].decision == FinalAnswer(content="完成。")


def test_agent_passes_registry_tools_to_llm() -> None:
    """Agent 每轮把注册表工具清单传给 LLM，供适配器生成工具定义。"""

    llm = FakeLLM([_final_answer_json("完成")])
    registry = _default_registry()

    Agent(llm=llm, registry=registry, max_steps=2).run("任务")

    assert len(llm.calls_with_tools) == 1
    _, tools = llm.calls_with_tools[0]
    names = {getattr(tool, "name", None) for tool in tools}
    assert names == {"calculator", "file_reader", "final_answer", "retrieve"}


def test_agent_intercepts_final_answer_tool_call() -> None:
    """模型用原生 tool_calls 调用 final_answer 时，Agent 转换为最终回答并终止。"""

    call = ToolCall(
        call_id="call-9",
        name="final_answer",
        arguments={"content": "计算完成：4。"},
    )
    llm = FakeLLM([Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=3).run("计算 2 + 2")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.final_answer == FinalAnswer(content="计算完成：4。")
    assert state.steps_used == 1
    assert state.trace[0].decision == FinalAnswer(content="计算完成：4。")
    tool_messages = [
        message for message in state.messages if message.role is MessageRole.TOOL
    ]
    assert tool_messages[0].tool_call_id == "call-9"
    assert tool_messages[0].content == "计算完成：4。"


def test_agent_rejects_non_context_policy() -> None:
    """``context_policy`` 必须是 ContextPolicy，其他值在构造时被拒绝。"""

    llm = FakeLLM([_final_answer_json("完成")])
    with pytest.raises(TypeError, match="context_policy"):
        Agent(
            llm=llm,
            registry=ToolRegistry(),
            max_steps=1,
            context_policy=object(),  # type: ignore[arg-type]
        )


def test_agent_without_context_policy_sends_full_messages() -> None:
    """不传策略时模型请求与完整消息列表一致（恒等，既有行为不变）。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _final_answer_json("结果是 4。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=3).run("计算 2 + 2")

    second_call = llm.calls[1]
    assert [message.role for message in second_call] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    assert second_call == tuple(state.messages[:-1])


def test_agent_with_context_policy_trims_request_keeps_state_full() -> None:
    """传入 ContextPolicy 后模型请求被裁剪+摘要，终态消息仍完整。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _tool_call_json("call-2", "retrieve", {"query": "react"}),
            _final_answer_json("计算完成，并查到了资料。"),
        ]
    )
    registry = _default_registry()

    state = Agent(
        llm=llm,
        registry=registry,
        max_steps=5,
        context_policy=ContextPolicy(context_window=80),
    ).run("综合任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 3
    assert state.steps_used == len(state.trace)

    # 首轮请求（system + 任务，无可裁轮次）原样
    first_call = llm.calls[0]
    assert [message.role for message in first_call] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
    ]

    # 第二轮起模型请求出现摘要 system 消息
    second_call = llm.calls[1]
    assert second_call[0].role is MessageRole.SYSTEM
    assert second_call[1].role is MessageRole.SYSTEM
    assert SUMMARY_HEADING in second_call[1].content
    assert second_call[2].role is MessageRole.USER
    assert all(
        message.role is not MessageRole.TOOL or message.tool_call_id != "call-1"
        for message in second_call
    )

    # 终态消息保持完整：system + 任务 + 三轮（含被裁掉的第一轮）
    assert [message.role for message in state.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert all(SUMMARY_HEADING not in message.content for message in state.messages)


def test_stream_mode_state_matches_non_stream_mode() -> None:
    """流式模式与非流式模式产生相同的决策、观察、消息与终止信息。"""

    presets = [
        _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
        _final_answer_json("结果是 4。"),
    ]
    registry = _default_registry()

    streamed = Agent(llm=FakeLLM(list(presets)), registry=registry, max_steps=3).run(
        "计算 2 + 2", stream=True
    )
    plain = Agent(llm=FakeLLM(list(presets)), registry=registry, max_steps=3).run(
        "计算 2 + 2"
    )

    assert streamed.termination_reason is plain.termination_reason
    assert streamed.final_answer == plain.final_answer
    assert streamed.steps_used == plain.steps_used
    assert [step.decision for step in streamed.trace] == [
        step.decision for step in plain.trace
    ]
    assert [step.observation for step in streamed.trace] == [
        step.observation for step in plain.trace
    ]
    assert streamed.messages == plain.messages


def test_stream_mode_forwards_content_chunks_to_callback() -> None:
    """on_chunk 收到每个增量块；拼接后等于完整模型输出。"""

    presets = [
        _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
        _final_answer_json("结果是 4。"),
    ]
    received: list[str] = []
    registry = _default_registry()

    Agent(llm=FakeLLM(presets), registry=registry, max_steps=3).run(
        "计算 2 + 2",
        stream=True,
        on_chunk=lambda chunk: received.append(chunk.content),
    )

    joined = "".join(received)
    assert "tool_call" in joined
    assert "结果是 4。" in joined


def test_stream_mode_calls_on_step_for_each_trace_step() -> None:
    """on_step 按顺序收到每个 TraceStep，供 CLI 边产生边显示。"""

    presets = [
        _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
        _final_answer_json("结果是 4。"),
    ]
    steps: list[TraceStep] = []
    registry = _default_registry()

    Agent(llm=FakeLLM(presets), registry=registry, max_steps=3).run(
        "计算 2 + 2",
        stream=True,
        on_step=steps.append,
    )

    assert [step.step_number for step in steps] == [1, 2]
    assert steps[0].decision is not None
    assert steps[1].decision == FinalAnswer(content="结果是 4。")


def test_run_rejects_invalid_stream_callbacks() -> None:
    """on_chunk 与 on_step 必须是可调用对象。"""

    llm = FakeLLM([_final_answer_json("完成")])
    agent = Agent(llm=llm, registry=_default_registry(), max_steps=1)

    with pytest.raises(TypeError):
        agent.run("任务", stream=True, on_chunk=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        agent.run("任务", on_step=object())  # type: ignore[arg-type]


def test_stream_mode_native_final_answer_terminates_with_final_answer() -> None:
    """原生 final_answer 工具调用（真实模型形态）在流式模式下正常终止。"""

    call = ToolCall(
        call_id="call-1",
        name="final_answer",
        arguments={"content": "完成。"},
    )
    presets = [Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])]
    registry = _default_registry()

    state = Agent(llm=FakeLLM(presets), registry=registry, max_steps=3).run(
        "回答我", stream=True
    )

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.final_answer == FinalAnswer(content="完成。")
    assert state.steps_used == 1
    assert len(state.trace) == 1
    assert state.trace[0].decision == FinalAnswer(content="完成。")


def test_plan_mode_plans_then_executes() -> None:
    """plan-then-execute：先输出计划（计一步），再进入既有循环直到最终回答。"""

    llm = FakeLLM(
        [
            _plan_json("先调用计算器，再给出最终回答"),
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _final_answer_json("结果是 4。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=4).run(
        "计算 2 + 2", plan_mode=True
    )

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.final_answer == FinalAnswer(content="结果是 4。")
    assert state.steps_used == 3
    assert len(state.trace) == 3
    assert state.steps_used == len(state.trace)
    assert llm.call_count == 3

    plan_step = state.trace[0]
    assert plan_step.decision == Plan(content="先调用计算器，再给出最终回答")
    assert plan_step.observation is None

    tool_step = state.trace[1]
    assert isinstance(tool_step.decision, ToolCall)
    assert tool_step.decision.name == "calculator"

    final_step = state.trace[2]
    assert final_step.decision == FinalAnswer(content="结果是 4。")

    # 计划指令作为 user 消息进入上下文
    assert any(
        message.role is MessageRole.USER and "先输出计划" in message.content
        for message in state.messages
    )


def test_plan_mode_injects_plan_contract_into_system_prompt() -> None:
    """plan_mode 开启时系统提示词包含规划阶段契约，默认不包含。"""

    registry = _default_registry()
    with_plan = Agent(
        llm=FakeLLM([_plan_json("计划"), _final_answer_json("完成")]),
        registry=registry,
        max_steps=3,
    ).run("任务", plan_mode=True)
    without = Agent(
        llm=FakeLLM([_final_answer_json("完成")]),
        registry=registry,
        max_steps=1,
    ).run("任务")

    assert "规划阶段" in with_plan.messages[0].content
    assert '"kind": "plan"' in with_plan.messages[0].content
    assert "规划阶段" not in without.messages[0].content


def test_plan_mode_parse_failure_retries_then_recovers() -> None:
    """规划阶段解析失败有界重试一次：回写稳定错误、消耗一步，重试成功继续。"""

    llm = FakeLLM(
        [
            _json_message("这不是计划 JSON"),
            _plan_json("先计算，再回答"),
            _tool_call_json("call-1", "calculator", {"expression": "1 + 1"}),
            _final_answer_json("结果是 2。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=5).run(
        "计算 1 + 1", plan_mode=True
    )

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 4
    assert llm.call_count == 4

    error_step = state.trace[0]
    assert error_step.decision is None
    assert error_step.error is not None
    assert error_step.error.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    assert error_step.error.retryable is True

    plan_step = state.trace[1]
    assert plan_step.decision == Plan(content="先计算，再回答")


def test_plan_mode_parse_failure_twice_terminates() -> None:
    """规划阶段连续两次解析失败：有界重试后仍失败，以解析错误终止。"""

    llm = FakeLLM([_json_message("坏输出 1"), _json_message("坏输出 2")])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=5).run("任务", plan_mode=True)

    assert state.termination_reason is TerminationReason.MODEL_OUTPUT_PARSE_ERROR
    assert state.final_answer is None
    assert state.steps_used == 2
    assert llm.call_count == 2
    assert state.trace[1].error is not None
    assert state.trace[1].error.retryable is False


def test_plan_mode_zero_budget_terminates_without_model_call() -> None:
    """规划阶段也受 max_steps 硬预算：0 步预算不发起任何模型调用。"""

    llm = FakeLLM([])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=0).run("任务", plan_mode=True)

    assert state.termination_reason is TerminationReason.MAX_STEPS_EXCEEDED
    assert state.steps_used == 0
    assert state.trace == []
    assert llm.call_count == 0


def test_plan_mode_counts_against_step_budget() -> None:
    """规划阶段计入步数预算：计划 + 一次工具调用恰好耗尽 2 步预算。"""

    llm = FakeLLM(
        [
            _plan_json("先调用计算器"),
            _tool_call_json("call-1", "calculator", {"expression": "1 + 1"}),
            _final_answer_json("结果是 2。"),  # 预算耗尽，永远不会被消费
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=2).run(
        "计算 1 + 1", plan_mode=True
    )

    assert state.termination_reason is TerminationReason.MAX_STEPS_EXCEEDED
    assert state.steps_used == 2
    assert llm.call_count == 2
    assert isinstance(state.trace[0].decision, Plan)
    assert isinstance(state.trace[1].decision, ToolCall)


def test_reflection_mode_reflects_after_retryable_failure() -> None:
    """反思模式：可重试工具失败后强制一步反思，再继续直到最终回答。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "retrieve", {"query": "unknown-topic"}),
            _reflection_json("检索失败，原因是主题不存在；下一步改用 react"),
            _tool_call_json("call-2", "retrieve", {"query": "react"}),
            _final_answer_json("已找到 ReAct 的说明。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(RetrieveTool())

    state = Agent(llm=llm, registry=registry, max_steps=5).run(
        "查一个主题", reflection_mode=True
    )

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 4
    assert len(state.trace) == 4
    assert state.steps_used == len(state.trace)
    assert llm.call_count == 4

    tool_step = state.trace[0]
    assert isinstance(tool_step.decision, ToolCall)
    assert tool_step.observation is not None
    assert tool_step.observation.is_error is True

    reflection_step = state.trace[1]
    assert reflection_step.decision == Reflection(
        content="检索失败，原因是主题不存在；下一步改用 react"
    )
    assert reflection_step.observation is None

    recovery_step = state.trace[2]
    assert isinstance(recovery_step.decision, ToolCall)
    assert recovery_step.decision.name == "retrieve"
    assert recovery_step.observation is not None
    assert recovery_step.observation.is_error is False


def test_reflection_mode_skips_reflection_for_fatal_failure() -> None:
    """反思模式不干预不可恢复失败：直接终止，不发起反思调用。"""

    registry = ToolRegistry()
    registry.register(FailingTool())
    llm = FakeLLM([_tool_call_json("call-1", "failing", {})])

    state = Agent(llm=llm, registry=registry, max_steps=3).run(
        "任务", reflection_mode=True
    )

    assert state.termination_reason is TerminationReason.TOOL_EXECUTION_ERROR
    assert state.steps_used == 1
    assert llm.call_count == 1
    assert all(
        step.decision is None or not isinstance(step.decision, Reflection)
        for step in state.trace
    )


def test_reflection_mode_parse_failure_retries_then_terminates() -> None:
    """反思阶段解析失败有界重试一次，仍失败以解析错误终止。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "retrieve", {"query": "unknown-topic"}),
            _json_message("坏反思 1"),
            _json_message("坏反思 2"),
        ]
    )
    registry = ToolRegistry()
    registry.register(RetrieveTool())

    state = Agent(llm=llm, registry=registry, max_steps=5).run(
        "查一个主题", reflection_mode=True
    )

    assert state.termination_reason is TerminationReason.MODEL_OUTPUT_PARSE_ERROR
    assert state.steps_used == 3
    assert llm.call_count == 3
    assert state.trace[0].decision is not None  # 失败的检索步骤
    assert state.trace[1].error is not None
    assert state.trace[1].error.retryable is True
    assert state.trace[2].error is not None
    assert state.trace[2].error.retryable is False


def test_aux_phases_notify_on_step_callback() -> None:
    """规划与反思步骤都通过 on_step 回调暴露给展示层。"""

    llm = FakeLLM(
        [
            _plan_json("先检索再回答"),
            _tool_call_json("call-1", "retrieve", {"query": "unknown-topic"}),
            _reflection_json("失败原因：主题不存在；下一步结束"),
            _final_answer_json("完成。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(RetrieveTool())
    steps: list[TraceStep] = []

    state = Agent(llm=llm, registry=registry, max_steps=5).run(
        "任务", plan_mode=True, reflection_mode=True, on_step=steps.append
    )

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert [step.step_number for step in steps] == [1, 2, 3, 4]
    assert isinstance(steps[0].decision, Plan)
    assert isinstance(steps[2].decision, Reflection)


def test_run_rejects_non_bool_mode_flags() -> None:
    """plan_mode / reflection_mode 必须是布尔值。"""

    agent = Agent(
        llm=FakeLLM([_final_answer_json("完成")]),
        registry=_default_registry(),
        max_steps=1,
    )

    with pytest.raises(TypeError):
        agent.run("任务", plan_mode=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        agent.run("任务", reflection_mode="yes")  # type: ignore[arg-type]
