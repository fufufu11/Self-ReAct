"""Day 8 计算器工具的公开行为测试。

测试通过 ``ToolRegistry`` 与领域 ``ToolCall`` 驱动计算器，覆盖成功运算、
非法表达式、除零等运行期错误、调用编号关联和注册表集成。所有输入都是
确定性本地字符串，不访问网络或真实 API。
"""

from __future__ import annotations

import pytest

from self_react.llm import FakeLLM
from self_react.models import (
    Message,
    MessageRole,
    Observation,
    ToolCall,
    ToolErrorCode,
    ToolResultStatus,
)
from self_react.tools import (
    CalculatorTool,
    Tool,
    ToolArgumentError,
    ToolRegistrationError,
    ToolRegistry,
)


def _registry_with_calculator() -> ToolRegistry:
    """创建并注册计算器的标准测试注册表。"""

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


def test_calculator_evaluates_simple_expression() -> None:
    """合法表达式返回正确内容，调用编号与工具名保持一致。"""

    registry = _registry_with_calculator()
    call = ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )

    result = registry.execute(call)

    assert result.is_success is True
    assert result.status is ToolResultStatus.SUCCESS
    assert result.content == "4"
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "calculator"


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 2 * 3", "8"),
        ("2 * (3 + 4)", "14"),
        ("(2 + 3) * 4", "20"),
    ],
)
def test_calculator_respects_precedence_and_parentheses(
    expression: str,
    expected: str,
) -> None:
    """运算符优先级和括号按标准算术规则计算。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": expression},
        )
    )

    assert result.is_success is True
    assert result.content == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 / 4", "0.5"),
        ("4 / 2", "2"),
        ("2.0 + 2", "4"),
    ],
)
def test_calculator_supports_division_and_normalizes_integral_floats(
    expression: str,
    expected: str,
) -> None:
    """除法得到浮点数，整数结果去掉无意义的 .0 后缀。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": expression},
        )
    )

    assert result.is_success is True
    assert result.content == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("7 // 2", "3"),
        ("7 % 3", "1"),
        ("2 ** 10", "1024"),
        ("-3 + 5", "2"),
        ("+5", "5"),
    ],
)
def test_calculator_supports_floor_modulo_power_and_unary_operators(
    expression: str,
    expected: str,
) -> None:
    """整除、取模、幂和一元正负号在允许范围内可用。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": expression},
        )
    )

    assert result.is_success is True
    assert result.content == expected


def test_calculator_executes_directly_without_registry() -> None:
    """工具本身可以直接调用，返回字符串内容。"""

    tool = CalculatorTool()

    assert tool.execute({"expression": "2 + 2"}) == "4"
    assert isinstance(tool.execute({"expression": "2 + 2"}), str)


@pytest.mark.parametrize("expression", ["2 +", "$ + 1", "(1 + 2"])
def test_calculator_rejects_syntax_errors_as_invalid_arguments(
    expression: str,
) -> None:
    """语法错误返回 INVALID_ARGUMENTS，不把异常文本伪装成成功内容。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": expression},
        )
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True
    assert "语法错误" in result.error.message


@pytest.mark.parametrize(
    "expression",
    [
        "abc",
        "__import__('os')",
        "().__class__",
        "True",
        "lambda: 1",
        "[1, 2]",
    ],
)
def test_calculator_rejects_unknown_expression_elements(expression: str) -> None:
    """非白名单节点（名字、调用、属性等）一律拒绝，绝不执行。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": expression},
        )
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True


def test_calculator_rejects_unknown_element_with_explainable_message() -> None:
    """被拒绝的表达式元素会说明不支持什么，而不是暴露堆栈。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": "abc"},
        )
    )

    assert result.error is not None
    assert "不支持" in result.error.message
    assert "Traceback" not in str(result)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"expression": 42},
        {"expression": ""},
        {"expression": "   "},
        {"expression": "2", "extra": "x"},
    ],
)
def test_calculator_rejects_missing_or_non_string_expression(
    arguments: dict[str, object],
) -> None:
    """缺失、非字符串、空白或多余参数在工具边界被拒。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(call_id="call-1", name="calculator", arguments=arguments)
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True


def test_calculator_rejects_overlong_expression() -> None:
    """超过长度上限的表达式返回 INVALID_ARGUMENTS。"""

    registry = _registry_with_calculator()
    overlong = "1+" * 500 + "1"

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": overlong},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert "过长" in result.error.message


def test_calculator_rejects_deep_expression_chain() -> None:
    """过深的运算链返回 INVALID_ARGUMENTS，防止递归求值被拖垮。"""

    registry = _registry_with_calculator()
    too_deep = "+".join(["1"] * 101)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": too_deep},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert "嵌套过深" in result.error.message


def test_calculator_accepts_expression_chain_at_depth_limit() -> None:
    """深度等于上限的合法运算仍然可用。"""

    registry = _registry_with_calculator()
    at_limit = "+".join(["1"] * 100)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": at_limit},
        )
    )

    assert result.is_success is True
    assert result.content == "100"


@pytest.mark.parametrize("expression", ["1 / 0", "1 // 0", "1 % 0"])
def test_calculator_division_by_zero_is_stable_execution_error(
    expression: str,
) -> None:
    """除零等运行期错误返回稳定的 TOOL_EXECUTION_ERROR 且允许重试。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": expression},
        )
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True
    assert result.error.message == "除数不能为零"


def test_calculator_negative_power_of_zero_is_execution_error() -> None:
    """0 的负指数幂是运行期错误，不是参数格式问题。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": "0 ** -1"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True


def test_calculator_rejects_oversized_exponent() -> None:
    """超过指数上限的幂运算返回稳定的执行错误。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": "2 ** 101"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert "指数过大" in result.error.message


@pytest.mark.parametrize("expression", ["11 ** 100", "1e308 * 10"])
def test_calculator_rejects_oversized_result(expression: str) -> None:
    """结果超出可表示范围时返回稳定执行错误，不返回 inf 或巨大整数。"""

    registry = _registry_with_calculator()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": expression},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True
    assert "超出可表示范围" in result.error.message


def test_calculator_preserves_call_identity_through_observation() -> None:
    """调用编号贯穿 ToolCall、ToolResult 与 Observation，保持一致。"""

    registry = _registry_with_calculator()
    call = ToolCall(
        call_id="call-7",
        name="calculator",
        arguments={"expression": "3 * 3"},
    )

    result = registry.execute(call)
    observation = Observation.from_tool_result(result)
    tool_message = observation.as_message()

    assert result.is_success is True
    assert result.tool_call_id == "call-7"
    assert observation.tool_call_id == "call-7"
    assert tool_message.tool_call_id == "call-7"
    assert observation.content == "9"
    assert tool_message.role is MessageRole.TOOL


def test_calculator_satisfies_tool_protocol() -> None:
    """计算器满足 Day 7 的 Tool 协议，无需继承基类。"""

    tool = CalculatorTool()

    assert isinstance(tool, Tool)
    assert tool.name == "calculator"
    assert tool.description.strip()


def test_calculator_duplicate_registration_is_rejected() -> None:
    """重复注册计算器与 Day 7 的注册纪律保持一致。"""

    registry = _registry_with_calculator()

    with pytest.raises(ToolRegistrationError):
        registry.register(CalculatorTool())


def test_calculator_registry_instances_are_isolated() -> None:
    """计算器只在注册过的注册表中可用，其他注册表按未知工具处理。"""

    first = _registry_with_calculator()
    second = ToolRegistry()

    assert "calculator" in first
    assert "calculator" not in second
    assert first.names == ("calculator",)
    assert second.names == ()

    result = second.execute(
        ToolCall(
            call_id="call-1",
            name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL


def test_calculator_unknown_tool_message_lists_calculator() -> None:
    """未知工具失败消息包含请求名与可用名（计算器）。"""

    registry = _registry_with_calculator()

    result = registry.execute(ToolCall(call_id="call-1", name="gamma", arguments={}))

    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL
    assert "gamma" in result.error.message
    assert "calculator" in result.error.message


def test_calculator_consumes_tool_call_returned_by_fake_llm() -> None:
    """FakeLLM 返回的 ToolCall 能端到端驱动计算器并转回观察。"""

    call = ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )
    requesting_llm = FakeLLM(
        [Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])]
    )
    registry = _registry_with_calculator()

    decision_message = requesting_llm.complete(
        [Message(role=MessageRole.USER, content="请计算 2 + 2")]
    )
    result = registry.execute(decision_message.tool_calls[0])
    observation = Observation.from_tool_result(result)
    tool_message = observation.as_message()

    assert decision_message.tool_calls[0].call_id == "call-1"
    assert result.is_success is True
    assert result.content == "4"
    assert tool_message.role is MessageRole.TOOL
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.content == "4"


def test_calculator_argument_error_is_stable_at_tool_boundary() -> None:
    """工具直接抛出的参数错误也是稳定异常，可被注册表转换。"""

    tool = CalculatorTool()

    with pytest.raises(ToolArgumentError):
        tool.execute({})
