"""Day 7 工具协议、注册表与统一调用边界的公开行为测试。"""

from __future__ import annotations

import pytest

from self_react.llm import FakeLLM
from self_react.models import (
    AgentState,
    Message,
    MessageRole,
    Observation,
    ToolCall,
    ToolErrorCode,
    ToolResultStatus,
    TraceStep,
)
from self_react.tools import (
    Tool,
    ToolArgumentError,
    ToolExecutionError,
    ToolRegistrationError,
    ToolRegistry,
)


class FakeTool:
    """确定性本地工具替身：记录参数并按配置返回内容或抛出异常。"""

    def __init__(
        self,
        name: str = "echo",
        description: str = "回声工具",
        result: object = "回声内容",
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    def execute(self, arguments: dict[str, object]) -> str:
        self.calls.append(arguments)
        if self.error is not None:
            raise self.error
        return self.result  # type: ignore[return-value]


def test_registry_executes_legal_tool_call_and_preserves_call_id() -> None:
    """合法 ToolCall 能找到注册工具并返回成功结果，调用编号保持一致。"""

    registry = ToolRegistry()
    tool = FakeTool(name="echo", result="你好")
    registry.register(tool)
    call = ToolCall(call_id="call-1", name="echo", arguments={"text": "你好"})

    result = registry.execute(call)

    assert result.is_success is True
    assert result.status is ToolResultStatus.SUCCESS
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "echo"
    assert result.content == "你好"
    assert tool.calls == [{"text": "你好"}]


def test_unknown_tool_returns_failure_without_executing_any_name() -> None:
    """未知工具返回 UNKNOWN_TOOL，且绝不按名字动态导入或执行代码。"""

    registry = ToolRegistry()
    tool = FakeTool(name="echo")
    registry.register(tool)

    call = ToolCall(
        call_id="call-1",
        name="__import__('os').system('should-not-run')",
        arguments={},
    )
    result = registry.execute(call)

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL
    assert result.error.retryable is True
    assert result.content is None
    assert tool.calls == []


def test_unknown_tool_message_lists_requested_and_available_tools() -> None:
    """失败说明应包含被请求的工具名和允许使用的工具名。"""

    registry = ToolRegistry()
    registry.register(FakeTool(name="beta"))
    registry.register(FakeTool(name="alpha"))

    result = registry.execute(ToolCall(call_id="call-1", name="gamma", arguments={}))

    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL
    assert "gamma" in result.error.message
    assert "alpha" in result.error.message
    assert "beta" in result.error.message


def test_invalid_arguments_are_not_disguised_as_success() -> None:
    """参数校验失败返回 INVALID_ARGUMENTS，错误说明不进入成功内容。"""

    registry = ToolRegistry()
    registry.register(
        FakeTool(
            name="echo",
            error=ToolArgumentError("表达式无效"),
        )
    )

    result = registry.execute(
        ToolCall(call_id="call-1", name="echo", arguments={"expression": "++"})
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.message == "表达式无效"
    assert result.error.retryable is True


def test_generic_execution_exception_maps_to_stable_error() -> None:
    """普通异常统一转换为 TOOL_EXECUTION_ERROR，不泄露原始异常文本。"""

    registry = ToolRegistry()
    registry.register(FakeTool(name="echo", error=RuntimeError("除数不能为零")))

    result = registry.execute(ToolCall(call_id="call-1", name="echo", arguments={}))

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.message == "工具执行失败：echo"
    assert result.error.retryable is True
    assert "除数不能为零" not in str(result)


def test_tool_execution_error_carries_safe_message_and_retryable() -> None:
    """工具自报的错误保留安全说明和可恢复性，不把异常伪装成成功。"""

    registry = ToolRegistry()
    registry.register(
        FakeTool(
            name="echo",
            error=ToolExecutionError("存储已满", retryable=False),
        )
    )

    result = registry.execute(ToolCall(call_id="call-1", name="echo", arguments={}))

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.message == "存储已满"
    assert result.error.retryable is False


def test_non_string_tool_return_is_rejected_as_execution_error() -> None:
    """工具返回非字符串时按协议违约处理，不把对象写进结果。"""

    registry = ToolRegistry()
    registry.register(FakeTool(name="bad", result={"not": "text"}))

    result = registry.execute(ToolCall(call_id="call-1", name="bad", arguments={}))

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is False


@pytest.mark.parametrize(
    "error",
    [SystemExit(3), KeyboardInterrupt()],
)
def test_system_level_exceptions_are_not_swallowed(
    error: BaseException,
) -> None:
    """系统级取消或退出应继续向上传播，不能被转成普通 ToolResult。"""

    registry = ToolRegistry()
    registry.register(FakeTool(name="boom", error=error))

    with pytest.raises(type(error)):
        registry.execute(ToolCall(call_id="call-1", name="boom", arguments={}))


def test_registry_rejects_duplicate_blank_and_invalid_tools() -> None:
    """重复名称、空名称和缺少协议成员的对象在注册时就被拒绝。"""

    registry = ToolRegistry()
    registry.register(FakeTool(name="echo"))

    with pytest.raises(ToolRegistrationError):
        registry.register(FakeTool(name="echo"))

    with pytest.raises(ToolRegistrationError):
        registry.register(FakeTool(name=""))

    with pytest.raises(ToolRegistrationError):
        registry.register(FakeTool(name="   "))

    with pytest.raises(ToolRegistrationError):
        registry.register(FakeTool(name=123))  # type: ignore[arg-type]

    with pytest.raises(ToolRegistrationError):
        registry.register(FakeTool(description=""))

    with pytest.raises(ToolRegistrationError):
        registry.register(FakeTool(description="   "))

    with pytest.raises(ToolRegistrationError):
        registry.register(object())


def test_registry_instances_are_isolated() -> None:
    """不同注册表实例互不影响，未注册名称按未知工具处理。"""

    first = ToolRegistry()
    second = ToolRegistry()
    first.register(FakeTool(name="echo"))

    assert "echo" in first
    assert "echo" not in second
    assert first.names == ("echo",)
    assert second.names == ()

    result = second.execute(ToolCall(call_id="call-1", name="echo", arguments={}))
    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL


def test_mutating_tool_after_registration_keeps_registered_name() -> None:
    """注册后的外部修改不能偷偷改变注册表名称或产生新名称。"""

    tool = FakeTool(name="echo")
    registry = ToolRegistry()
    registry.register(tool)

    tool.name = "renamed"

    assert "echo" in registry
    assert "renamed" not in registry
    assert registry.names == ("echo",)
    result = registry.execute(ToolCall(call_id="call-1", name="echo", arguments={}))
    assert result.is_success is True
    assert result.tool_name == "echo"


def test_tool_protocol_accepts_an_independent_tool() -> None:
    """满足协议形状的独立工具无需继承基类即可注册。"""

    class IndependentTool:
        name = "independent"
        description = "独立实现"

        def execute(self, arguments: dict[str, object]) -> str:
            return "独立结果"

    tool = IndependentTool()
    registry = ToolRegistry()
    registry.register(tool)

    assert isinstance(tool, Tool)
    result = registry.execute(
        ToolCall(call_id="call-1", name="independent", arguments={})
    )
    assert result.is_success is True
    assert result.content == "独立结果"


def test_registry_execute_requires_domain_tool_call() -> None:
    """调用边界只接受领域 ToolCall，不接收原始字典或其他对象。"""

    registry = ToolRegistry()

    with pytest.raises(TypeError):
        registry.execute({"name": "echo", "arguments": {}})  # type: ignore[arg-type]


def test_tool_result_round_trips_without_runtime_resources() -> None:
    """执行结果、观察和状态可以序列化，且不含工具或注册表对象。"""

    registry = ToolRegistry()
    registry.register(FakeTool(name="echo", result="回声"))
    call = ToolCall(call_id="call-1", name="echo", arguments={"text": "你好"})

    result = registry.execute(call)
    observation = Observation.from_tool_result(result)
    tool_message = observation.as_message()
    state = AgentState(
        task="测试工具边界",
        messages=[
            Message(role=MessageRole.USER, content="你好"),
            tool_message,
        ],
        available_tools=["echo"],
        max_steps=1,
        steps_used=1,
        trace=[TraceStep(step_number=1, decision=call, observation=observation)],
    )

    encoded = state.model_dump_json()
    decoded = AgentState.model_validate_json(encoded)

    assert "FakeTool" not in encoded
    assert "execute" not in encoded
    assert "<function" not in encoded
    assert decoded == state
    assert decoded.trace[0].observation is not None
    assert decoded.trace[0].observation.tool_call_id == "call-1"


def test_tool_layer_consumes_tool_call_returned_by_fake_llm() -> None:
    """FakeLLM 只返回 ToolCall，执行与结果转换由 Day 7 工具层完成。"""

    call = ToolCall(call_id="call-1", name="echo", arguments={"text": "你好"})
    requesting_llm = FakeLLM(
        [Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])]
    )
    answering_llm = FakeLLM([Message(role=MessageRole.ASSISTANT, content="收到")])
    registry = ToolRegistry()
    registry.register(FakeTool(name="echo", result="你好"))

    decision_message = requesting_llm.complete(
        [Message(role=MessageRole.USER, content="请回应我")]
    )
    result = registry.execute(decision_message.tool_calls[0])
    observation = Observation.from_tool_result(result)
    tool_message = observation.as_message()

    final = answering_llm.complete(
        [
            Message(role=MessageRole.USER, content="请回应我"),
            decision_message,
            tool_message,
        ]
    )

    assert decision_message.tool_calls[0].call_id == "call-1"
    assert result.is_success is True
    assert result.tool_call_id == "call-1"
    assert tool_message.role is MessageRole.TOOL
    assert tool_message.tool_call_id == "call-1"
    assert final.content == "收到"
