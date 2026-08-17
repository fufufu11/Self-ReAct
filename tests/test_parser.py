"""Day 11 JSON 输出解析器的公开行为测试。

测试只依赖确定性字符串输入与 Fake LLM，不访问网络、不调用真实 API。核心
契约是：合法模型输出解析成 Day 4 领域对象且字段保持原样；非法输出一律返回
稳定 ``ParseError``，并与 ``TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR``
对齐，不向调用方泄漏原始异常文本或堆栈。
"""

from __future__ import annotations

import pytest

from self_react.llm import FakeLLM
from self_react.models import (
    FinalAnswer,
    Message,
    MessageRole,
    Plan,
    Reflection,
    ToolCall,
    ToolErrorCode,
    TraceErrorCode,
    TraceStep,
)
from self_react.parser import ParseError, parse_decision
from self_react.tools import (
    CalculatorTool,
    FileReaderTool,
    RetrieveTool,
    ToolRegistry,
)


def _user_message() -> Message:
    """构造一条普通用户消息，作为 Fake LLM 的调用上下文。"""

    return Message(role=MessageRole.USER, content="请继续")


def _assistant_json(content: str) -> Message:
    """构造一条把原始 JSON 放在 content 里的助手消息。"""

    return Message(role=MessageRole.ASSISTANT, content=content)


def test_parse_valid_final_answer_preserves_content() -> None:
    """合法 final_answer 解析为 FinalAnswer，内容保持原样。"""

    decision = parse_decision('{"kind": "final_answer", "content": "答案是 4。"}')

    assert decision == FinalAnswer(content="答案是 4。")
    assert isinstance(decision, FinalAnswer)
    assert decision.kind == "final_answer"


def test_parse_valid_tool_call_preserves_every_field() -> None:
    """合法 tool_call 解析为 ToolCall，编号、名称与参数保持原样。"""

    raw = (
        '{"kind": "tool_call", "call_id": "call-1", "name": "calculator", '
        '"arguments": {"expression": "2 + 2 * 3"}}'
    )
    decision = parse_decision(raw)

    assert decision == ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2 * 3"},
    )
    assert isinstance(decision, ToolCall)
    assert decision.kind == "tool_call"


def test_parse_tool_call_preserves_nested_arguments() -> None:
    """arguments 内的嵌套 JSON 对象按原样保留。"""

    raw = (
        '{"kind": "tool_call", "call_id": "call-2", "name": "file_reader", '
        '"arguments": {"path": "notes/2026.txt", "options": {"encoding": "utf-8"}}}'
    )

    assert parse_decision(raw) == ToolCall(
        call_id="call-2",
        name="file_reader",
        arguments={"path": "notes/2026.txt", "options": {"encoding": "utf-8"}},
    )


def test_parse_is_deterministic_pure_function() -> None:
    """相同输入两次解析得到完全相同的结果。"""

    raw = '{"kind": "final_answer", "content": "确定性的答案"}'

    first = parse_decision(raw)
    second = parse_decision(raw)

    assert first == second
    assert isinstance(first, FinalAnswer)
    assert isinstance(second, FinalAnswer)


def test_parse_tolerates_surrounding_whitespace() -> None:
    """JSON 对象外部的空白不改变解析结果。"""

    raw = '\n  {"kind": "final_answer", "content": "有空白的答案"}  \n'

    assert parse_decision(raw) == FinalAnswer(content="有空白的答案")


@pytest.mark.parametrize(
    "raw",
    ["", "hello", "{invalid", '{"kind": "final_answer"', "no json at all"],
)
def test_parse_rejects_non_json_input(raw: str) -> None:
    """非 JSON 文本必须返回稳定解析错误，而不是残缺对象。"""

    with pytest.raises(ParseError):
        parse_decision(raw)


@pytest.mark.parametrize(
    "raw",
    ["[1, 2]", '["a"]', "42", '"text"', "null", "true"],
)
def test_parse_rejects_json_that_is_not_an_object(raw: str) -> None:
    """JSON 是数组、数字、字符串、null 或布尔时返回稳定错误。"""

    with pytest.raises(ParseError):
        parse_decision(raw)


@pytest.mark.parametrize(
    "raw",
    [
        '{"content": "没有 kind"}',
        '{"kind": null, "content": "kind 是 null"}',
        '{"kind": 123, "content": "kind 是数字"}',
        '{"kind": "answer", "content": "未知决策"}',
        '{"kind": "tool_calls", "call_id": "c1", "name": "calculator", '
        '"arguments": {}}',
    ],
)
def test_parse_rejects_missing_or_invalid_kind(raw: str) -> None:
    """kind 缺失或不是 final_answer/tool_call 时返回稳定错误。"""

    with pytest.raises(ParseError):
        parse_decision(raw)


def test_parse_final_answer_requires_content() -> None:
    """final_answer 缺少 content 时必须返回稳定错误。"""

    with pytest.raises(ParseError):
        parse_decision('{"kind": "final_answer"}')


@pytest.mark.parametrize(
    "raw",
    [
        '{"kind": "final_answer", "content": 123}',
        '{"kind": "final_answer", "content": null}',
        '{"kind": "final_answer", "content": ["列表"]}',
    ],
)
def test_parse_final_answer_rejects_non_string_content(raw: str) -> None:
    """final_answer 的 content 类型错误时返回稳定错误。"""

    with pytest.raises(ParseError):
        parse_decision(raw)


@pytest.mark.parametrize(
    "raw",
    [
        '{"kind": "final_answer", "content": ""}',
        '{"kind": "final_answer", "content": "   "}',
    ],
)
def test_parse_final_answer_rejects_blank_content(raw: str) -> None:
    """契约要求 content 非空，空白内容不能构造残缺领域对象。"""

    with pytest.raises(ParseError):
        parse_decision(raw)


def test_parse_final_answer_rejects_extra_fields() -> None:
    """final_answer 的多余字段超出格式契约，应返回稳定错误。"""

    raw = '{"kind": "final_answer", "content": "答案", "extra": true}'

    with pytest.raises(ParseError):
        parse_decision(raw)


@pytest.mark.parametrize(
    "raw",
    [
        '{"kind": "tool_call", "name": "calculator", "arguments": {}}',
        '{"kind": "tool_call", "call_id": "call-1", "arguments": {}}',
        '{"kind": "tool_call", "call_id": "call-1", "name": "calculator"}',
    ],
)
def test_parse_tool_call_requires_all_three_fields(raw: str) -> None:
    """tool_call 缺少 call_id、name 或 arguments 时返回稳定错误。"""

    with pytest.raises(ParseError):
        parse_decision(raw)


@pytest.mark.parametrize(
    "raw",
    [
        '{"kind": "tool_call", "call_id": 1, "name": "calculator", "arguments": {}}',
        '{"kind": "tool_call", "call_id": null, "name": "calculator", "arguments": {}}',
        '{"kind": "tool_call", "call_id": "call-1", "name": 2, "arguments": {}}',
        '{"kind": "tool_call", "call_id": "call-1", "name": null, "arguments": {}}',
    ],
)
def test_parse_tool_call_rejects_non_string_ids(raw: str) -> None:
    """call_id 与 name 的类型错误返回稳定错误。"""

    with pytest.raises(ParseError):
        parse_decision(raw)


@pytest.mark.parametrize(
    "raw",
    [
        '{"kind": "tool_call", "call_id": "call-1", "name": "calculator", '
        '"arguments": []}',
        '{"kind": "tool_call", "call_id": "call-1", "name": "calculator", '
        '"arguments": "x"}',
        '{"kind": "tool_call", "call_id": "call-1", "name": "calculator", '
        '"arguments": 42}',
        '{"kind": "tool_call", "call_id": "call-1", "name": "calculator", '
        '"arguments": null}',
    ],
)
def test_parse_tool_call_rejects_non_object_arguments(raw: str) -> None:
    """arguments 必须是 JSON 对象，其他类型返回稳定错误。"""

    with pytest.raises(ParseError):
        parse_decision(raw)


@pytest.mark.parametrize(
    "raw",
    [
        '{"kind": "tool_call", "call_id": "", "name": "calculator", "arguments": {}}',
        '{"kind": "tool_call", "call_id": "   ", "name": "calculator", '
        '"arguments": {}}',
        '{"kind": "tool_call", "call_id": "call-1", "name": "", "arguments": {}}',
        '{"kind": "tool_call", "call_id": "call-1", "name": "   ", "arguments": {}}',
    ],
)
def test_parse_tool_call_rejects_blank_ids(raw: str) -> None:
    """契约要求 call_id 与 name 非空，空白字符串返回稳定错误。"""

    with pytest.raises(ParseError):
        parse_decision(raw)


def test_parse_tool_call_rejects_extra_fields() -> None:
    """tool_call 的多余字段超出格式契约，应返回稳定错误。"""

    raw = (
        '{"kind": "tool_call", "call_id": "call-1", "name": "calculator", '
        '"arguments": {}, "extra": true}'
    )

    with pytest.raises(ParseError):
        parse_decision(raw)


def test_parse_rejects_non_json_serializable_arguments() -> None:
    """arguments 含 NaN 等无法稳定序列化的值时返回稳定错误。"""

    raw = (
        '{"kind": "tool_call", "call_id": "call-1", "name": "calculator", '
        '"arguments": {"value": NaN}}'
    )

    with pytest.raises(ParseError):
        parse_decision(raw)


@pytest.mark.parametrize("raw", [None, 42, ["json"], b'{"kind": "final_answer"}'])
def test_parse_rejects_non_string_input(raw: object) -> None:
    """解析器只接受字符串输入，其他类型直接拒绝。"""

    with pytest.raises(TypeError):
        parse_decision(raw)  # type: ignore[arg-type]


def test_parse_error_aligns_with_model_output_parse_error_code() -> None:
    """所有解析错误必须与轨迹错误码 MODEL_OUTPUT_PARSE_ERROR 对齐。"""

    with pytest.raises(ParseError) as exc:
        parse_decision("不是 JSON")

    assert exc.value.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR


def test_parse_error_message_does_not_leak_raw_exception_or_stack() -> None:
    """错误消息不包含原始输入、异常类名或堆栈文本。"""

    raw = '{"kind": "final_answer", "content": 123}'

    with pytest.raises(ParseError) as exc:
        parse_decision(raw)

    message = str(exc.value)
    assert message
    assert raw not in message
    assert "Traceback" not in message
    assert "ValidationError" not in message
    assert "File " not in message
    assert "line " not in message


def test_fake_llm_valid_final_answer_is_parsed() -> None:
    """Fake LLM 返回合法 final_answer 原始输出时可解析成领域对象。"""

    llm = FakeLLM(
        [_assistant_json('{"kind": "final_answer", "content": "答案是 4。"}')]
    )
    response = llm.complete([_user_message()])

    assert parse_decision(response.content) == FinalAnswer(content="答案是 4。")


def test_fake_llm_valid_tool_call_is_parsed() -> None:
    """Fake LLM 返回合法 tool_call 原始输出时可解析成 ToolCall。"""

    llm = FakeLLM(
        [
            _assistant_json(
                '{"kind": "tool_call", "call_id": "call-1", "name": "retrieve", '
                '"arguments": {"query": "react"}}'
            )
        ]
    )
    response = llm.complete([_user_message()])

    assert parse_decision(response.content) == ToolCall(
        call_id="call-1",
        name="retrieve",
        arguments={"query": "react"},
    )


def test_fake_llm_missing_fields_raise_stable_parse_error() -> None:
    """Fake LLM 返回缺字段输出时，解析器返回稳定错误而非残缺对象。"""

    llm = FakeLLM([_assistant_json('{"kind": "tool_call", "name": "calculator"}')])
    response = llm.complete([_user_message()])

    with pytest.raises(ParseError) as exc:
        parse_decision(response.content)

    assert exc.value.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR
    assert "Traceback" not in str(exc.value)
    assert "ValidationError" not in str(exc.value)


def test_unknown_tool_parses_then_registry_returns_unknown_tool() -> None:
    """未知工具由解析器正常解析，再由注册表在分派阶段拒绝。"""

    llm = FakeLLM(
        [
            _assistant_json(
                '{"kind": "tool_call", "call_id": "call-1", "name": "unknown_tool", '
                '"arguments": {}}'
            )
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(FileReaderTool(root_directory="C:/allowed"))
    registry.register(RetrieveTool())

    response = llm.complete([_user_message()])
    decision = parse_decision(response.content)

    assert isinstance(decision, ToolCall)
    assert decision.name == "unknown_tool"

    result = registry.execute(decision)

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL
    assert "unknown_tool" in result.error.message
    assert "calculator" in result.error.message
    assert "file_reader" in result.error.message
    assert "retrieve" in result.error.message


def test_parsed_decision_can_enter_trace_step_as_day12_consumer() -> None:
    """解析结果可以直接作为 Day 4 TraceStep 的 decision 消费。"""

    decision = parse_decision(
        '{"kind": "tool_call", "call_id": "call-1", "name": "calculator", '
        '"arguments": {"expression": "2 + 2"}}'
    )
    step = TraceStep(step_number=1, decision=decision)

    assert step.decision == decision


def test_parse_plan_with_allowed_plan_kind() -> None:
    """规划阶段（allowed 只含 plan）解析合法计划。"""

    raw = '{"kind": "plan", "content": "先调用计算器，再给出最终回答"}'

    decision = parse_decision(raw, allowed=frozenset({"plan"}))

    assert decision == Plan(content="先调用计算器，再给出最终回答")
    assert isinstance(decision, Plan)
    assert decision.kind == "plan"


def test_parse_reflection_with_allowed_reflection_kind() -> None:
    """反思阶段（allowed 只含 reflection）解析合法反思。"""

    raw = (
        '{"kind": "reflection", "content": "检索失败，原因是主题不存在；'
        '下一步改用 react"}'
    )

    decision = parse_decision(raw, allowed=frozenset({"reflection"}))

    assert decision == Reflection(
        content="检索失败，原因是主题不存在；下一步改用 react"
    )
    assert isinstance(decision, Reflection)
    assert decision.kind == "reflection"


def test_parse_plan_rejects_blank_or_non_string_content() -> None:
    """plan 的 content 必须是非空字符串，其余类型返回稳定错误。"""

    for raw in (
        '{"kind": "plan", "content": ""}',
        '{"kind": "plan", "content": "   "}',
        '{"kind": "plan", "content": 123}',
        '{"kind": "plan", "content": null}',
    ):
        with pytest.raises(ParseError):
            parse_decision(raw, allowed=frozenset({"plan"}))


def test_parse_reflection_rejects_blank_or_non_string_content() -> None:
    """reflection 的 content 必须是非空字符串，其余类型返回稳定错误。"""

    for raw in (
        '{"kind": "reflection", "content": ""}',
        '{"kind": "reflection", "content": "   "}',
        '{"kind": "reflection", "content": 123}',
        '{"kind": "reflection", "content": null}',
    ):
        with pytest.raises(ParseError):
            parse_decision(raw, allowed=frozenset({"reflection"}))


def test_parse_plan_and_reflection_reject_extra_fields() -> None:
    """plan / reflection 的多余字段超出格式契约，应返回稳定错误。"""

    with pytest.raises(ParseError):
        parse_decision(
            '{"kind": "plan", "content": "计划", "extra": true}',
            allowed=frozenset({"plan"}),
        )
    with pytest.raises(ParseError):
        parse_decision(
            '{"kind": "reflection", "content": "反思", "extra": true}',
            allowed=frozenset({"reflection"}),
        )


def test_restricted_parse_rejects_wrong_kind_with_stable_message() -> None:
    """受限阶段拒绝非目标 kind，错误文本稳定且不泄漏原始输入。"""

    with pytest.raises(ParseError) as exc:
        parse_decision(
            '{"kind": "tool_call", "call_id": "c1", "name": "calculator", '
            '"arguments": {}}',
            allowed=frozenset({"plan"}),
        )
    assert "此阶段只接受 kind=plan" in str(exc.value)

    with pytest.raises(ParseError) as exc:
        parse_decision(
            '{"kind": "final_answer", "content": "答案"}',
            allowed=frozenset({"reflection"}),
        )
    assert "此阶段只接受 kind=reflection" in str(exc.value)

    with pytest.raises(ParseError) as exc:
        parse_decision(
            '{"kind": "unknown", "content": "x"}', allowed=frozenset({"plan"})
        )
    assert "此阶段只接受 kind=plan" in str(exc.value)


def test_default_parse_still_rejects_plan_and_reflection_kinds() -> None:
    """默认模式（主循环）不接受 plan / reflection kind，错误文本与基线一致。"""

    with pytest.raises(ParseError) as exc:
        parse_decision('{"kind": "plan", "content": "计划"}')
    assert str(exc.value) == "kind 只能是 final_answer 或 tool_call"

    with pytest.raises(ParseError) as exc:
        parse_decision('{"kind": "reflection", "content": "反思"}')
    assert str(exc.value) == "kind 只能是 final_answer 或 tool_call"


def test_default_parse_unknown_kind_message_is_unchanged() -> None:
    """未知 kind 在默认模式下的错误文本必须与 R-06 之前完全一致。"""

    with pytest.raises(ParseError) as exc:
        parse_decision('{"kind": "answer", "content": "未知决策"}')
    assert str(exc.value) == "kind 只能是 final_answer 或 tool_call"


def test_parse_decision_rejects_invalid_allowed_argument() -> None:
    """``allowed`` 必须是已知 kind 的非空 frozenset。"""

    with pytest.raises(TypeError):
        parse_decision('{"kind": "plan", "content": "x"}', allowed={"plan"})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_decision('{"kind": "plan", "content": "x"}', allowed=frozenset())
    with pytest.raises(TypeError):
        parse_decision(
            '{"kind": "plan", "content": "x"}',
            allowed=frozenset({"unknown_kind"}),
        )
