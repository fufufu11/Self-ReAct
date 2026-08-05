"""Day 17 工具参数 Schema 契约测试（对照 LangChain/LangGraph 的改进）。

本文件验证 Day 17 吸收的唯一改进：业务工具声明结构化参数 JSON Schema，
DeepSeek 适配器把它随工具定义下发给模型。测试只使用注入客户端与真实业务
工具类，不访问网络、不依赖真实 API。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from types import SimpleNamespace

import pytest

from self_react.deepseek import DeepSeekLLM
from self_react.llm import LLMInputError
from self_react.models import Message, MessageRole
from self_react.tools import (
    DEFAULT_PARAMETERS_SCHEMA,
    CalculatorTool,
    FileReaderTool,
    FinalAnswerTool,
    RetrieveTool,
)


class RecordingClient:
    """捕获一次请求并返回固定响应的最小客户端替身。"""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=self)
        self.calls: list[dict[str, object]] = []

    def create(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, object]],
        stream: bool,
        tools: list[dict[str, object]] | None,
        extra_body: dict[str, object] | None,
    ) -> object:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "stream": stream,
                "tools": tools,
                "extra_body": extra_body,
            }
        )
        return {"choices": [{"message": {"role": "assistant", "content": "完成"}}]}


def test_default_parameters_schema_is_loose_object() -> None:
    """工具未声明 schema 时使用宽松对象回退，保持向后兼容。"""

    assert DEFAULT_PARAMETERS_SCHEMA == {"type": "object", "properties": {}}


@pytest.mark.parametrize(
    ("tool", "expected_properties", "expected_required"),
    [
        (CalculatorTool(), {"expression"}, ["expression"]),
        (
            FileReaderTool(root_directory="C:/allowed"),
            {"path"},
            ["path"],
        ),
        (RetrieveTool(), {"query"}, ["query"]),
        (FinalAnswerTool(), {"content"}, ["content"]),
    ],
)
def test_business_tools_declare_structured_parameter_schema(
    tool: object,
    expected_properties: set[str],
    expected_required: list[str],
) -> None:
    """四个业务工具都声明结构化参数 Schema，与各自参数校验一致。"""

    parameters = tool.parameters  # type: ignore[attr-defined]
    assert isinstance(parameters, dict)
    assert parameters["type"] == "object"
    assert parameters["additionalProperties"] is False
    assert set(parameters["properties"]) == expected_properties
    assert parameters["required"] == expected_required
    # 必须可 JSON 序列化，才能随工具定义下发给供应商
    json.dumps(parameters, ensure_ascii=False)


def test_each_parameter_property_carries_type_and_description() -> None:
    """每个参数都带 type 与中文描述，供模型理解字段含义。"""

    tools = [
        CalculatorTool(),
        FileReaderTool(root_directory="C:/allowed"),
        RetrieveTool(),
        FinalAnswerTool(),
    ]
    for tool in tools:
        properties = tool.parameters["properties"]  # type: ignore[attr-defined]
        for name, schema in properties.items():
            assert isinstance(schema, dict), name
            assert "type" in schema, name
            assert isinstance(schema.get("description"), str), name
            assert schema["description"].strip(), name


def test_adapter_serializes_declared_parameters_schema() -> None:
    """DeepSeek 适配器把工具的 parameters 原样放进 function 定义。"""

    client = RecordingClient()
    llm = DeepSeekLLM(client=client)

    llm.complete(
        [Message(role=MessageRole.USER, content="计算 2 + 2")],
        tools=[CalculatorTool()],
    )

    function = client.calls[0]["tools"][0]["function"]
    assert function["parameters"] == CalculatorTool().parameters


def test_adapter_falls_back_to_loose_schema_without_parameters() -> None:
    """工具未声明 parameters 时回退到宽松对象，不改变既有行为。"""

    class BareTool:
        name = "bare"
        description = "没有声明 schema 的工具"

        def execute(self, arguments: dict[str, object]) -> str:
            return "ok"

    client = RecordingClient()
    DeepSeekLLM(client=client).complete(
        [Message(role=MessageRole.USER, content="测试")],
        tools=[BareTool()],
    )

    function = client.calls[0]["tools"][0]["function"]
    assert function["parameters"] == {"type": "object", "properties": {}}


@pytest.mark.parametrize(
    "bad_parameters",
    [
        ["不是", "对象"],
        {"bad": object()},
    ],
)
def test_adapter_rejects_invalid_parameters_before_request(
    bad_parameters: object,
) -> None:
    """parameters 非对象或不可 JSON 序列化时拒绝，且不发供应商请求。"""

    class BadTool:
        name = "bad"
        description = "schema 非法的工具"
        parameters = bad_parameters

        def execute(self, arguments: dict[str, object]) -> str:
            return "ok"

    client = RecordingClient()

    with pytest.raises(LLMInputError):
        DeepSeekLLM(client=client).complete(
            [Message(role=MessageRole.USER, content="测试")],
            tools=[BadTool()],
        )

    assert client.calls == []
