"""Day 12 Agent 主循环的公开行为测试。

测试只依赖 Fake LLM、三个真实工具与一个确定性失败工具，不访问网络、不调用
真实 API。公开缝是 ``Agent.run(task) -> AgentState``：断言终止原因、轨迹、
消息上下文和 ``steps_used``/``trace`` 不变量，不触碰 Agent 内部实现。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from self_react.agent import Agent
from self_react.llm import LLM, FakeLLM
from self_react.models import (
    FinalAnswer,
    Message,
    MessageRole,
    TerminationReason,
    ToolCall,
    ToolErrorCode,
    TraceErrorCode,
)
from self_react.prompts import render_system_prompt
from self_react.tools import (
    CalculatorTool,
    FileReaderTool,
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


def _default_registry() -> ToolRegistry:
    """注册三个真实业务工具；file_reader 根目录在测试中不会被真正读取。"""

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(FileReaderTool(root_directory="C:/allowed"))
    registry.register(RetrieveTool())
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
        [CalculatorTool(), FileReaderTool(root_directory="C:/allowed"), RetrieveTool()]
    )
    assert state.messages[1] == Message(role=MessageRole.USER, content="回答我")
    assert state.available_tools == ["calculator", "file_reader", "retrieve"]


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


def test_parse_error_records_trace_error_and_terminates() -> None:
    """解析失败：记录 MODEL_OUTPUT_PARSE_ERROR 轨迹步骤，不伪造决策。"""

    llm = FakeLLM([_json_message("这不是 JSON")])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=3).run("任务")

    assert state.termination_reason is TerminationReason.MODEL_OUTPUT_PARSE_ERROR
    assert state.final_answer is None
    assert state.steps_used == 1
    assert len(state.trace) == 1
    step = state.trace[0]
    assert step.decision is None
    assert step.observation is None
    assert step.error is not None
    assert step.error.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    assert step.error.retryable is False
    assert "Traceback" not in step.error.message
    assert "这不是 JSON" not in step.error.message
    assert llm.call_count == 1


def test_parse_error_message_is_stable_and_does_not_leak_raw_output() -> None:
    """解析失败的消息保持稳定，不携带模型原始输出。"""

    llm = FakeLLM([_json_message('{"kind": "final_answer", "content": 123}')])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=2).run("任务")

    message = state.trace[0].error.message
    assert message == "content 必须是字符串"
    assert "123" not in message
    assert "ValidationError" not in message


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
        ([_json_message("坏输出")], 2, TerminationReason.MODEL_OUTPUT_PARSE_ERROR),
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


def test_agent_accepts_any_llm_protocol_adapter() -> None:
    """只要满足 LLM 协议，Agent 就可以替换任何适配器。"""

    class FixedLLM:
        def complete(self, messages: Sequence[Message]) -> Message:
            return _final_answer_json("固定回答")

    adapter = FixedLLM()
    assert isinstance(adapter, LLM)

    state = Agent(llm=adapter, registry=_default_registry(), max_steps=1).run("任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.final_answer == FinalAnswer(content="固定回答")
