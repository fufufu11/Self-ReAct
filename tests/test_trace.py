"""Day 13 人类可读轨迹渲染的公开行为测试（Day 14 补充重复动作标签）。

测试覆盖确定性（相同状态两次渲染完全一致）、字段一一对应（输入摘要、决策、
观察、错误、耗时都出现在文本中且顺序一致）、四类轨迹（最终回答、工具调用、
解析失败、步数耗尽）以及端到端分支（未知工具、可恢复失败后恢复、不可恢复
失败终止、重复动作）。全部使用 Fake LLM 与三个真实工具，不访问网络、不调用
真实 API。公开缝是 ``render_trace(state: AgentState) -> str``，不触碰渲染
内部实现。
"""

from __future__ import annotations

import json

import pytest

from self_react.agent import Agent
from self_react.llm import FakeLLM
from self_react.models import (
    AgentState,
    FinalAnswer,
    Message,
    MessageRole,
    Observation,
    TerminationReason,
    ToolCall,
    ToolErrorCode,
    TraceError,
    TraceErrorCode,
    TraceStep,
)
from self_react.tools import (
    CalculatorTool,
    FileReaderTool,
    RetrieveTool,
    ToolExecutionError,
    ToolRegistry,
)
from self_react.trace import render_step, render_trace


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


def _success_observation(call_id: str, tool_name: str, content: str) -> Observation:
    """构造一条成功工具观察，供直接构造状态的测试复用。"""

    return Observation(
        tool_call_id=call_id,
        tool_name=tool_name,
        content=content,
        is_error=False,
    )


def test_render_final_answer_state_exact_output() -> None:
    """最终回答终态：任务、终止原因、步数和单步字段全部按固定格式输出。"""

    state = AgentState(
        task="计算 2 + 2",
        messages=[],
        available_tools=["calculator"],
        max_steps=3,
        steps_used=1,
        trace=[
            TraceStep(
                step_number=1,
                input_summary="计算 2 + 2",
                decision=FinalAnswer(content="答案是 4。"),
                duration_ms=12.5,
            )
        ],
        final_answer=FinalAnswer(content="答案是 4。"),
        termination_reason=TerminationReason.FINAL_ANSWER,
    )

    expected = (
        "任务：计算 2 + 2\n"
        "终止原因：最终回答（FINAL_ANSWER）\n"
        "步数：1 / 3\n"
        "\n"
        "第 1 步\n"
        "输入摘要：计算 2 + 2\n"
        "决策：最终回答\n"
        "回答内容：答案是 4。\n"
        "耗时：12.5 毫秒"
    )
    assert render_trace(state) == expected


def test_render_tool_call_success_step_exact_output() -> None:
    """工具调用步骤：决策、调用编号、参数、成功观察和耗时全部出现。"""

    state = AgentState(
        task="计算 2 + 2",
        messages=[],
        available_tools=["calculator"],
        max_steps=3,
        steps_used=2,
        trace=[
            TraceStep(
                step_number=1,
                input_summary="计算 2 + 2",
                decision=ToolCall(
                    call_id="call-1",
                    name="calculator",
                    arguments={"expression": "2 + 2"},
                ),
                observation=_success_observation("call-1", "calculator", "4"),
                duration_ms=10.0,
            ),
            TraceStep(
                step_number=2,
                input_summary="4",
                decision=FinalAnswer(content="结果是 4。"),
                duration_ms=20.25,
            ),
        ],
        final_answer=FinalAnswer(content="结果是 4。"),
        termination_reason=TerminationReason.FINAL_ANSWER,
    )

    text = render_trace(state)
    assert "第 1 步" in text
    assert "输入摘要：计算 2 + 2" in text
    assert "决策：调用工具 calculator" in text
    assert "调用编号：call-1" in text
    assert '参数：{"expression": "2 + 2"}' in text
    assert "观察（成功）：4" in text
    assert "耗时：10 毫秒" in text
    assert "第 2 步" in text
    assert "输入摘要：4" in text
    assert "决策：最终回答" in text
    assert "回答内容：结果是 4。" in text
    assert "耗时：20.25 毫秒" in text


def test_render_tool_failure_observation_includes_error_fields() -> None:
    """失败观察：观察内容、错误码中文标签与可重试标记都出现在文本中。"""

    state = AgentState(
        task="查一个主题",
        messages=[],
        available_tools=["retrieve"],
        max_steps=2,
        steps_used=1,
        trace=[
            TraceStep(
                step_number=1,
                input_summary="查一个主题",
                decision=ToolCall(
                    call_id="call-1",
                    name="retrieve",
                    arguments={"query": "unknown-topic"},
                ),
                observation=Observation(
                    tool_call_id="call-1",
                    tool_name="retrieve",
                    content="知识库中没有与查询「unknown-topic」匹配的条目",
                    is_error=True,
                    error_code=ToolErrorCode.TOOL_EXECUTION_ERROR,
                    retryable=True,
                ),
                duration_ms=3.333,
            )
        ],
        termination_reason=TerminationReason.MAX_STEPS_EXCEEDED,
    )

    text = render_trace(state)
    assert "观察（失败）：知识库中没有与查询「unknown-topic」匹配的条目" in text
    assert "错误码：工具执行失败（TOOL_EXECUTION_ERROR）" in text
    assert "可重试：是" in text
    assert "耗时：3.333 毫秒" in text


def test_render_parse_error_step_exact_output() -> None:
    """解析失败步骤：稳定错误说明与枚举值出现，不展示调试细节。"""

    state = AgentState(
        task="任务",
        messages=[],
        available_tools=[],
        max_steps=3,
        steps_used=1,
        trace=[
            TraceStep(
                step_number=1,
                input_summary="任务",
                error=TraceError(
                    code=TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR,
                    message="模型输出不是合法 JSON",
                    retryable=False,
                    details={"raw_output": "模型原始输出"},
                ),
                duration_ms=1.234,
            )
        ],
        termination_reason=TerminationReason.MODEL_OUTPUT_PARSE_ERROR,
    )

    text = render_trace(state)
    assert (
        "错误：模型输出解析失败（MODEL_OUTPUT_PARSE_ERROR）：模型输出不是合法 JSON"
        in text
    )
    assert "可重试：否" in text
    assert "耗时：1.234 毫秒" in text
    assert "模型原始输出" not in text


def test_render_steps_exhausted_termination_header() -> None:
    """步数耗尽：终止原因与步数预算成对出现，轨迹步骤按原顺序渲染。"""

    state = AgentState(
        task="算两步",
        messages=[],
        available_tools=["calculator"],
        max_steps=2,
        steps_used=2,
        trace=[
            TraceStep(
                step_number=1,
                input_summary="算两步",
                decision=ToolCall(
                    call_id="call-1",
                    name="calculator",
                    arguments={"expression": "1 + 1"},
                ),
                observation=_success_observation("call-1", "calculator", "2"),
                duration_ms=0.5,
            ),
            TraceStep(
                step_number=2,
                input_summary="2",
                decision=ToolCall(
                    call_id="call-2",
                    name="calculator",
                    arguments={"expression": "2 + 2"},
                ),
                observation=_success_observation("call-2", "calculator", "4"),
                duration_ms=0.5,
            ),
        ],
        termination_reason=TerminationReason.MAX_STEPS_EXCEEDED,
    )

    text = render_trace(state)
    assert text.startswith(
        "任务：算两步\n终止原因：步数耗尽（MAX_STEPS_EXCEEDED）\n步数：2 / 2"
    )
    assert text.index("第 1 步") < text.index("第 2 步")
    assert "观察（成功）：2" in text
    assert "观察（成功）：4" in text


def test_render_empty_trace_max_steps_zero() -> None:
    """空轨迹（max_steps=0）：只有头部三行，没有伪造任何步骤。"""

    state = AgentState(
        task="任何任务",
        messages=[],
        available_tools=[],
        max_steps=0,
        steps_used=0,
        trace=[],
        termination_reason=TerminationReason.MAX_STEPS_EXCEEDED,
    )

    assert render_trace(state) == (
        "任务：任何任务\n终止原因：步数耗尽（MAX_STEPS_EXCEEDED）\n步数：0 / 0"
    )


def test_render_unknown_tool_termination_label() -> None:
    """未知工具终止：终止原因与观察错误码都使用稳定中文标签。"""

    state = AgentState(
        task="任务",
        messages=[],
        available_tools=["calculator"],
        max_steps=1,
        steps_used=1,
        trace=[
            TraceStep(
                step_number=1,
                input_summary="任务",
                decision=ToolCall(call_id="call-1", name="ghost", arguments={}),
                observation=Observation(
                    tool_call_id="call-1",
                    tool_name="ghost",
                    content="未知工具：ghost",
                    is_error=True,
                    error_code=ToolErrorCode.UNKNOWN_TOOL,
                    retryable=False,
                ),
                duration_ms=0.1,
            )
        ],
        termination_reason=TerminationReason.UNKNOWN_TOOL,
    )

    text = render_trace(state)
    assert "终止原因：未知工具（UNKNOWN_TOOL）" in text
    assert "错误码：未知工具（UNKNOWN_TOOL）" in text
    assert "可重试：否" in text


def test_render_repeated_action_observation_label() -> None:
    """重复动作观察：新增错误码只影响标签映射，输出格式与既有失败观察一致。"""

    state = AgentState(
        task="计算 2 + 2",
        messages=[],
        available_tools=["calculator"],
        max_steps=2,
        steps_used=2,
        trace=[
            TraceStep(
                step_number=1,
                input_summary="计算 2 + 2",
                decision=ToolCall(
                    call_id="call-1",
                    name="calculator",
                    arguments={"expression": "2 + 2"},
                ),
                observation=_success_observation("call-1", "calculator", "4"),
                duration_ms=0.1,
            ),
            TraceStep(
                step_number=2,
                input_summary="4",
                decision=ToolCall(
                    call_id="call-2",
                    name="calculator",
                    arguments={"expression": "2 + 2"},
                ),
                observation=Observation(
                    tool_call_id="call-2",
                    tool_name="calculator",
                    content="重复动作：工具 calculator 已用相同参数调用过",
                    is_error=True,
                    error_code=ToolErrorCode.REPEATED_ACTION,
                    retryable=True,
                ),
                duration_ms=0.1,
            ),
        ],
        termination_reason=TerminationReason.MAX_STEPS_EXCEEDED,
    )

    text = render_trace(state)
    assert "观察（失败）：重复动作：工具 calculator 已用相同参数调用过" in text
    assert "错误码：重复动作（REPEATED_ACTION）" in text
    assert "可重试：是" in text


def test_render_is_deterministic_and_argument_order_stable() -> None:
    """相同状态两次渲染完全一致，参数 JSON 按键排序与插入顺序无关。"""

    def build(arguments: dict[str, object]) -> AgentState:
        return AgentState(
            task="任务",
            messages=[],
            available_tools=["calculator"],
            max_steps=1,
            steps_used=1,
            trace=[
                TraceStep(
                    step_number=1,
                    input_summary="任务",
                    decision=ToolCall(
                        call_id="call-1",
                        name="calculator",
                        arguments=arguments,
                    ),
                    observation=_success_observation("call-1", "calculator", "3"),
                    duration_ms=0.1,
                )
            ],
            termination_reason=TerminationReason.MAX_STEPS_EXCEEDED,
        )

    first = build({"b": 1, "a": "中文"})
    second = build({"a": "中文", "b": 1})

    assert render_trace(first) == render_trace(first)
    assert render_trace(first) == render_trace(second)
    assert '参数：{"a": "中文", "b": 1}' in render_trace(first)


def test_render_none_summary_and_duration() -> None:
    """可选字段为 None 时使用稳定占位符，不抛异常。"""

    step = TraceStep(
        step_number=1,
        decision=FinalAnswer(content="完成"),
        duration_ms=None,
    )
    state = AgentState(
        task="任务",
        messages=[],
        available_tools=[],
        max_steps=1,
        steps_used=1,
        trace=[step],
        final_answer=FinalAnswer(content="完成"),
        termination_reason=TerminationReason.FINAL_ANSWER,
    )

    text = render_trace(state)
    assert "输入摘要：（无）" in text
    assert "耗时：（未记录）" in text


def test_render_hides_debug_details_and_metadata() -> None:
    """渲染不展示 TraceError.details 与 Observation.metadata 调试内容。"""

    state = AgentState(
        task="任务",
        messages=[],
        available_tools=["calculator"],
        max_steps=2,
        steps_used=2,
        trace=[
            TraceStep(
                step_number=1,
                input_summary="任务",
                decision=ToolCall(
                    call_id="call-1",
                    name="calculator",
                    arguments={"expression": "1 + 1"},
                ),
                observation=Observation(
                    tool_call_id="call-1",
                    tool_name="calculator",
                    content="2",
                    is_error=False,
                    metadata={"debug": "内部调试信息"},
                ),
                duration_ms=0.1,
            ),
            TraceStep(
                step_number=2,
                input_summary="2",
                error=TraceError(
                    code=TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR,
                    message="模型输出不是合法 JSON",
                    retryable=False,
                    details={"raw_output": "模型原始输出"},
                ),
                duration_ms=0.1,
            ),
        ],
        termination_reason=TerminationReason.MODEL_OUTPUT_PARSE_ERROR,
    )

    text = render_trace(state)
    assert "内部调试信息" not in text
    assert "模型原始输出" not in text
    assert "模型输出不是合法 JSON" in text


def test_render_rejects_non_state() -> None:
    """render_trace 只接受 AgentState，其他输入抛 TypeError。"""

    with pytest.raises(TypeError):
        render_trace(object())  # type: ignore[arg-type]


def test_render_step_is_public_and_matches_trace_section() -> None:
    """render_step 导出单步文本，流式展示可复用同一套渲染。"""

    step = TraceStep(
        step_number=1,
        input_summary="任务",
        decision=FinalAnswer(content="完成"),
        duration_ms=1.0,
    )
    state = AgentState(
        task="任务",
        messages=[],
        available_tools=[],
        max_steps=1,
        steps_used=1,
        trace=[step],
        final_answer=FinalAnswer(content="完成"),
        termination_reason=TerminationReason.FINAL_ANSWER,
    )

    section = render_step(step)

    assert section == (
        "第 1 步\n输入摘要：任务\n决策：最终回答\n回答内容：完成\n耗时：1 毫秒"
    )
    assert section in render_trace(state)


def test_end_to_end_task_to_final_answer_renders() -> None:
    """任务直达最终回答：端到端渲染包含任务、终止原因与回答内容。"""

    llm = FakeLLM([_final_answer_json("答案是 4。")])
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=3).run("计算 2 + 2")
    text = render_trace(state)

    assert text.startswith(
        "任务：计算 2 + 2\n终止原因：最终回答（FINAL_ANSWER）\n步数：1 / 3"
    )
    assert "决策：最终回答" in text
    assert "回答内容：答案是 4。" in text
    assert "耗时：" in text


def test_end_to_end_multi_tool_rounds_render_in_order() -> None:
    """多轮工具调用：三个真实工具按调用顺序渲染，成功与失败观察并存。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "2 * 3"}),
            _tool_call_json("call-2", "retrieve", {"query": "react"}),
            _tool_call_json("call-3", "file_reader", {"path": "notes.txt"}),
            _final_answer_json("完成。"),
        ]
    )
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=5).run("综合任务")
    text = render_trace(state)

    positions = [
        text.index("第 1 步"),
        text.index("第 2 步"),
        text.index("第 3 步"),
        text.index("第 4 步"),
    ]
    assert positions == sorted(positions)
    assert text.index("决策：调用工具 calculator") < text.index(
        "决策：调用工具 retrieve"
    )
    assert text.index("决策：调用工具 retrieve") < text.index(
        "决策：调用工具 file_reader"
    )
    assert "观察（成功）：6" in text
    assert "观察（失败）：" in text
    assert "错误码：工具执行失败（TOOL_EXECUTION_ERROR）" in text
    assert "可重试：是" in text


def test_end_to_end_parse_error_renders_stable_message() -> None:
    """解析失败端到端：渲染只展示稳定错误说明，不泄漏模型原始输出。"""

    llm = FakeLLM([_json_message("这不是 JSON"), _json_message("坏输出")])
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=3).run("任务")
    text = render_trace(state)

    assert "终止原因：模型输出解析失败（MODEL_OUTPUT_PARSE_ERROR）" in text
    assert (
        "错误：模型输出解析失败（MODEL_OUTPUT_PARSE_ERROR）：模型输出不是合法 JSON"
        in text
    )
    assert "可重试：否" in text
    assert "这不是 JSON" not in text


def test_end_to_end_steps_exhausted_renders() -> None:
    """步数耗尽端到端：终止原因、预算和最后一步观察都出现在文本中。"""

    llm = FakeLLM([_tool_call_json("call-1", "calculator", {"expression": "1 + 1"})])
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    state = Agent(llm=llm, registry=registry, max_steps=1).run("算一步")
    text = render_trace(state)

    assert "终止原因：步数耗尽（MAX_STEPS_EXCEEDED）" in text
    assert "步数：1 / 1" in text
    assert "决策：调用工具 calculator" in text
    assert "观察（成功）：2" in text


def test_end_to_end_unknown_tool_then_recovery_renders() -> None:
    """未知工具端到端：先渲染失败观察与错误码，再渲染最终回答。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "unknown_tool", {}),
            _final_answer_json("好的，我改用正确工具。"),
        ]
    )
    registry = _default_registry()

    state = Agent(llm=llm, registry=registry, max_steps=3).run("任务")
    text = render_trace(state)

    assert "观察（失败）：未知工具：unknown_tool" in text
    assert "错误码：未知工具（UNKNOWN_TOOL）" in text
    assert "可重试：是" in text
    assert "终止原因：最终回答（FINAL_ANSWER）" in text


def test_end_to_end_retryable_failure_then_recovery_renders() -> None:
    """可恢复工具失败端到端：失败观察渲染后，模型下一轮给出最终回答。"""

    llm = FakeLLM(
        [
            _tool_call_json("call-1", "retrieve", {"query": "unknown-topic"}),
            _final_answer_json("没有找到该主题。"),
        ]
    )
    registry = ToolRegistry()
    registry.register(RetrieveTool())

    state = Agent(llm=llm, registry=registry, max_steps=2).run("查一个主题")
    text = render_trace(state)

    assert "观察（失败）：知识库中没有与查询「unknown-topic」匹配的条目" in text
    assert "错误码：工具执行失败（TOOL_EXECUTION_ERROR）" in text
    assert "可重试：是" in text
    assert "终止原因：最终回答（FINAL_ANSWER）" in text


class FailingTool:
    """确定性失败工具：按配置抛出不可恢复的工具执行错误。"""

    name = "failing"
    description = "确定性失败工具"

    def execute(self, arguments: dict[str, object]) -> str:
        raise ToolExecutionError("存储已满", retryable=False)


def test_end_to_end_non_retryable_failure_termination_renders() -> None:
    """不可恢复失败端到端：终止原因与失败观察的错误码都出现在文本中。"""

    registry = ToolRegistry()
    registry.register(FailingTool())
    llm = FakeLLM([_tool_call_json("call-1", "failing", {})])

    state = Agent(llm=llm, registry=registry, max_steps=3).run("任务")
    text = render_trace(state)

    assert "终止原因：工具执行失败（TOOL_EXECUTION_ERROR）" in text
    assert "观察（失败）：存储已满" in text
    assert "错误码：工具执行失败（TOOL_EXECUTION_ERROR）" in text
    assert "可重试：否" in text
