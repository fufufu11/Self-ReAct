"""Day 4 领域模型的公开行为测试。"""

import json

import pytest
from pydantic import ValidationError

from self_react.models import (
    AgentState,
    FinalAnswer,
    Message,
    MessageRole,
    Observation,
    TerminationReason,
    ToolCall,
    ToolError,
    ToolErrorCode,
    ToolResult,
    ToolResultStatus,
    TraceError,
    TraceErrorCode,
    TraceStep,
)


def test_message_associates_tool_call_with_assistant_and_tool_roles() -> None:
    """助手可以请求工具，工具消息必须回指同一个调用编号。"""

    call = ToolCall(call_id="call-1", name="calculator", arguments={"x": 2})
    assistant = Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])
    tool = Message(
        role=MessageRole.TOOL,
        content="4",
        tool_call_id=call.call_id,
    )

    assert assistant.tool_calls[0].call_id == tool.tool_call_id


def test_message_rejects_missing_content_and_invalid_role() -> None:
    """普通消息不能缺少内容，未知角色不能绕过枚举校验。"""

    with pytest.raises(ValidationError):
        Message(role="user", content="")

    with pytest.raises(ValidationError):
        Message(role="not-a-role", content="hello")


def test_message_rejects_wrong_type_and_invalid_tool_association() -> None:
    """严格文本和角色关联能尽早发现调用方传错数据。"""

    with pytest.raises(ValidationError):
        Message(role=MessageRole.USER, content=123)

    with pytest.raises(ValidationError):
        Message(role=MessageRole.TOOL, content="result")


def test_tool_call_requires_arguments_and_json_serializable_values() -> None:
    """工具动作必须有参数对象，且参数不能携带运行时对象。"""

    with pytest.raises(ValidationError):
        ToolCall(call_id="call-1", name="calculator")

    with pytest.raises(ValidationError):
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"callable": lambda: None},
        )


def test_tool_result_distinguishes_success_and_failure() -> None:
    """成功和失败载荷互斥，失败不会伪装成成功内容。"""

    success = ToolResult.success(
        tool_call_id="call-1",
        tool_name="calculator",
        content="4",
    )
    failure = ToolResult.failure(
        tool_call_id="call-2",
        tool_name="calculator",
        code=ToolErrorCode.INVALID_ARGUMENTS,
        message="表达式无效",
        retryable=True,
    )

    assert success.status is ToolResultStatus.SUCCESS
    assert success.is_success is True
    assert failure.status is ToolResultStatus.FAILURE
    assert failure.content is None
    assert failure.error is not None
    assert failure.error.retryable is True

    with pytest.raises(ValidationError):
        ToolResult(
            status=ToolResultStatus.SUCCESS,
            tool_call_id="call-1",
            tool_name="calculator",
        )

    with pytest.raises(ValidationError):
        ToolResult(
            status=ToolResultStatus.FAILURE,
            tool_call_id="call-1",
            tool_name="calculator",
            content="异常字符串",
            error=ToolError(
                code=ToolErrorCode.TOOL_EXECUTION_ERROR,
                message="执行失败",
                retryable=False,
            ),
        )


def test_observation_preserves_failure_semantics_and_message_link() -> None:
    """ToolResult 转为 Observation 后仍保留错误类别和调用关联。"""

    result = ToolResult.failure(
        tool_call_id="call-1",
        tool_name="calculator",
        code=ToolErrorCode.TOOL_EXECUTION_ERROR,
        message="除数不能为零",
        retryable=False,
    )

    observation = Observation.from_tool_result(result)
    message = observation.as_message()

    assert observation.is_error is True
    assert observation.error_code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert observation.retryable is False
    assert message.role is MessageRole.TOOL
    assert message.tool_call_id == "call-1"


def test_repeated_action_error_code_round_trips_through_observation() -> None:
    """重复动作是稳定错误类别：可以构造失败结果并转成可重试失败观察。"""

    result = ToolResult.failure(
        tool_call_id="call-1",
        tool_name="calculator",
        code=ToolErrorCode.REPEATED_ACTION,
        message="重复动作：工具 calculator 已调用过",
        retryable=True,
    )

    observation = Observation.from_tool_result(result)

    assert result.status is ToolResultStatus.FAILURE
    assert result.error is not None
    assert result.error.code is ToolErrorCode.REPEATED_ACTION
    assert result.error.retryable is True
    assert observation.is_error is True
    assert observation.error_code is ToolErrorCode.REPEATED_ACTION
    assert observation.retryable is True
    assert observation.tool_call_id == "call-1"


def test_trace_step_validates_decision_observation_relationship() -> None:
    """工具轨迹必须把观察关联到同一个 ToolCall，最终回答不能执行工具。"""

    call = ToolCall(call_id="call-1", name="calculator", arguments={})
    observation = Observation.from_tool_result(
        ToolResult.success(
            tool_call_id="call-1",
            tool_name="calculator",
            content="4",
        )
    )
    step = TraceStep(
        step_number=1,
        decision=call,
        observation=observation,
        duration_ms=2.5,
    )
    assert step.decision == call

    with pytest.raises(ValidationError):
        TraceStep(step_number=1)

    with pytest.raises(ValidationError):
        TraceStep(
            step_number=1,
            decision=call,
            observation=Observation(
                tool_call_id="other-call",
                tool_name="calculator",
                content="4",
                is_error=False,
            ),
        )

    with pytest.raises(ValidationError):
        TraceStep(
            step_number=1,
            decision=FinalAnswer(content="完成"),
            observation=observation,
        )


def test_agent_state_rejects_invalid_termination_and_step_counts() -> None:
    """状态必须保持步数、轨迹和最终终止信息一致。"""

    with pytest.raises(ValidationError):
        AgentState(task="算一下", max_steps=1, steps_used=1)

    with pytest.raises(ValidationError):
        AgentState(
            task="算一下",
            max_steps=1,
            termination_reason=TerminationReason.FINAL_ANSWER,
        )

    with pytest.raises(ValidationError):
        AgentState(
            task="算一下",
            max_steps=1,
            final_answer=FinalAnswer(content="完成"),
            termination_reason=TerminationReason.MAX_STEPS_EXCEEDED,
        )


def test_agent_state_round_trips_through_json() -> None:
    """状态可以序列化再恢复，且不携带不可序列化运行时资源。"""

    state = AgentState(
        task="计算 2 + 2",
        messages=[Message(role=MessageRole.USER, content="计算 2 + 2")],
        available_tools=["calculator"],
        max_steps=2,
        steps_used=1,
        trace=[
            TraceStep(
                step_number=1,
                decision=ToolCall(
                    call_id="call-1",
                    name="calculator",
                    arguments={"expression": "2 + 2"},
                ),
                observation=Observation.from_tool_result(
                    ToolResult.success(
                        tool_call_id="call-1",
                        tool_name="calculator",
                        content="4",
                    )
                ),
            )
        ],
    )

    encoded = state.model_dump_json()
    decoded = AgentState.model_validate_json(encoded)

    assert json.loads(encoded)["termination_reason"] is None
    assert decoded == state
    assert decoded.remaining_steps == 1
    assert decoded.is_terminated is False


def test_invalid_enum_and_non_serializable_state_data_are_rejected() -> None:
    """非法枚举值和不可序列化元数据都必须在领域边界失败。"""

    with pytest.raises(ValidationError):
        ToolResult(
            status="unknown",
            tool_call_id="call-1",
            tool_name="calculator",
            content="4",
        )

    with pytest.raises(ValidationError):
        ToolResult.success(
            tool_call_id="call-1",
            tool_name="calculator",
            content="4",
            metadata={"resource": object()},
        )


def test_completed_agent_state_requires_final_answer_reason_pair() -> None:
    """正常完成必须同时记录回答和 FINAL_ANSWER 原因。"""

    state = AgentState(
        task="回答问题",
        max_steps=1,
        final_answer=FinalAnswer(content="答案"),
        termination_reason=TerminationReason.FINAL_ANSWER,
    )

    assert state.is_terminated is True
    assert state.remaining_steps == 1


def test_trace_error_can_record_parse_failure_without_fake_decision() -> None:
    """解析失败可以独立记录，不需要伪造一个工具动作。"""

    step = TraceStep(
        step_number=1,
        error=TraceError(
            code=TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR,
            message="模型输出缺少决策字段",
            retryable=False,
        ),
    )

    assert step.decision is None
    assert step.error is not None
