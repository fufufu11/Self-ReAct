"""Day 9 确定性知识检索工具的公开行为测试。

测试通过 ``ToolRegistry`` 与领域 ``ToolCall`` 驱动 ``RetrieveTool``，
覆盖已知条目、相同输入相同输出、未知条目、参数校验、调用编号关联和
注册表集成。检索结果来自模块内固定知识库，不访问网络或真实 API。
"""

from __future__ import annotations

from pathlib import Path

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
    FileReaderTool,
    RetrieveTool,
    Tool,
    ToolArgumentError,
    ToolRegistrationError,
    ToolRegistry,
)
from self_react.tools.retrieve import KNOWLEDGE_BASE, MAX_QUERY_LENGTH


def _registry_with_retrieve() -> ToolRegistry:
    """创建并注册检索工具的标准测试注册表。"""

    registry = ToolRegistry()
    registry.register(RetrieveTool())
    return registry


@pytest.mark.parametrize("topic", sorted(KNOWLEDGE_BASE))
def test_retrieve_returns_known_entry_for_each_topic(topic: str) -> None:
    """每个内置主题都能返回对应说明，调用编号保持一致。"""

    registry = _registry_with_retrieve()

    result = registry.execute(
        ToolCall(call_id="call-1", name="retrieve", arguments={"query": topic})
    )

    assert result.is_success is True
    assert result.status is ToolResultStatus.SUCCESS
    assert result.content == KNOWLEDGE_BASE[topic]
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "retrieve"


def test_retrieve_is_deterministic_for_same_input() -> None:
    """相同输入连续调用两次，返回完全相同的结果。"""

    tool = RetrieveTool()

    first = tool.execute({"query": "python"})
    second = tool.execute({"query": "python"})

    assert first == second
    assert first == KNOWLEDGE_BASE["python"]


def test_retrieve_normalizes_case_and_whitespace() -> None:
    """查询会统一大小写并折叠空白，因此 REACT 与 react 结果一致。"""

    tool = RetrieveTool()

    assert tool.execute({"query": "REACT"}) == KNOWLEDGE_BASE["react"]
    assert tool.execute({"query": "  react  "}) == KNOWLEDGE_BASE["react"]
    assert tool.execute({"query": "DeepSeek"}) == KNOWLEDGE_BASE["deepseek"]


def test_retrieve_unknown_entry_is_stable_execution_error() -> None:
    """未知主题返回稳定执行错误，绝不伪造成功内容。"""

    registry = _registry_with_retrieve()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="retrieve",
            arguments={"query": "nonexistent-topic"},
        )
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True
    assert "nonexistent-topic" in result.error.message
    assert "可用主题" in result.error.message
    assert "react" in result.error.message


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": 42},
        {"query": ""},
        {"query": "   "},
        {"query": "react", "extra": "x"},
    ],
)
def test_retrieve_rejects_missing_or_non_string_query(
    arguments: dict[str, object],
) -> None:
    """缺失、非字符串、空白或多余参数在工具边界被拒。"""

    registry = _registry_with_retrieve()

    result = registry.execute(
        ToolCall(call_id="call-1", name="retrieve", arguments=arguments)
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True


def test_retrieve_rejects_overlong_query() -> None:
    """超过查询长度上限的参数返回 INVALID_ARGUMENTS。"""

    registry = _registry_with_retrieve()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="retrieve",
            arguments={"query": "q" * (MAX_QUERY_LENGTH + 1)},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert "过长" in result.error.message


def test_retrieve_query_at_length_limit_passes_validation() -> None:
    """查询长度恰好等于上限时通过参数校验，只在执行期报未知条目。"""

    registry = _registry_with_retrieve()

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="retrieve",
            arguments={"query": "q" * MAX_QUERY_LENGTH},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert "可用主题" in result.error.message


def test_retrieve_executes_directly_without_registry() -> None:
    """工具本身可以直接调用，返回字符串内容。"""

    tool = RetrieveTool()

    assert tool.execute({"query": "uv"}) == KNOWLEDGE_BASE["uv"]
    assert isinstance(tool.execute({"query": "uv"}), str)


def test_retrieve_satisfies_tool_protocol() -> None:
    """检索工具满足 Day 7 的 Tool 协议。"""

    tool = RetrieveTool()

    assert isinstance(tool, Tool)
    assert tool.name == "retrieve"
    assert tool.description.strip()


def test_retrieve_preserves_call_identity_through_observation() -> None:
    """调用编号贯穿 ToolCall、ToolResult 与 Observation。"""

    registry = _registry_with_retrieve()
    call = ToolCall(call_id="call-7", name="retrieve", arguments={"query": "pydantic"})

    result = registry.execute(call)
    observation = Observation.from_tool_result(result)
    tool_message = observation.as_message()

    assert result.is_success is True
    assert result.tool_call_id == "call-7"
    assert observation.tool_call_id == "call-7"
    assert tool_message.tool_call_id == "call-7"
    assert observation.content == KNOWLEDGE_BASE["pydantic"]
    assert tool_message.role is MessageRole.TOOL


def test_retrieve_duplicate_registration_is_rejected() -> None:
    """重复注册检索工具与 Day 7 的注册纪律保持一致。"""

    registry = _registry_with_retrieve()

    with pytest.raises(ToolRegistrationError):
        registry.register(RetrieveTool())


def test_retrieve_registry_instances_are_isolated() -> None:
    """检索工具只在注册过的注册表中可用。"""

    first = _registry_with_retrieve()
    second = ToolRegistry()

    assert "retrieve" in first
    assert "retrieve" not in second

    result = second.execute(
        ToolCall(call_id="call-1", name="retrieve", arguments={"query": "react"})
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL


def test_retrieve_argument_error_is_stable_at_tool_boundary() -> None:
    """工具直接抛出的参数错误也是稳定异常。"""

    tool = RetrieveTool()

    with pytest.raises(ToolArgumentError):
        tool.execute({})


def test_three_tools_coexist_in_one_registry(tmp_path: Path) -> None:
    """计算器、文件读取和检索工具同时注册后名册包含全部三个。"""

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(FileReaderTool(tmp_path))
    registry.register(RetrieveTool())

    assert registry.names == ("calculator", "file_reader", "retrieve")
    assert len(registry) == 3


def test_retrieve_consumes_tool_call_returned_by_fake_llm() -> None:
    """FakeLLM 返回的 ToolCall 能端到端驱动检索并转回观察。"""

    call = ToolCall(call_id="call-1", name="retrieve", arguments={"query": "python"})
    requesting_llm = FakeLLM(
        [Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])]
    )
    registry = _registry_with_retrieve()

    decision_message = requesting_llm.complete(
        [Message(role=MessageRole.USER, content="请检索 python")]
    )
    result = registry.execute(decision_message.tool_calls[0])
    observation = Observation.from_tool_result(result)
    tool_message = observation.as_message()

    assert decision_message.tool_calls[0].call_id == "call-1"
    assert result.is_success is True
    assert result.content == KNOWLEDGE_BASE["python"]
    assert tool_message.role is MessageRole.TOOL
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.content == KNOWLEDGE_BASE["python"]
