"""Day 18 DeepSeek 适配器边界测试（配置校验、工具定义与畸形响应）。

复盘 Day 4 至 Day 17 的测试覆盖后发现，``src/self_react/deepseek.py``
有三类关键分支没有测试：构造参数校验（空 model/base_url、非法 timeout）、
工具定义序列化边界（重复名称、非法名称、非序列输入）与畸形供应商响应
（缺字段、类型错误、重复调用编号）。本文件只补这些边界，全部使用注入
客户端，不访问网络、不依赖真实 API Key；不修改任何生产代码。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import SimpleNamespace

import pytest

from self_react.deepseek import DeepSeekLLM
from self_react.llm import (
    LLMConfigurationError,
    LLMInputError,
    LLMResponseError,
)
from self_react.models import Message, MessageRole


class RecordingClient:
    """捕获一次请求并返回固定响应的最小客户端替身。"""

    def __init__(self, response: object | None = None) -> None:
        self.chat = SimpleNamespace(completions=self)
        self.response = response
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
        if self.response is None:
            self.response = {
                "choices": [{"message": {"role": "assistant", "content": "完成"}}]
            }
        return self.response


def response_message(
    *,
    content: object = "回答",
    tool_calls: object | None = None,
) -> dict[str, object]:
    """构造供应商响应消息；content 与 tool_calls 可传任意测试值。"""

    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def tool_call(**fields: object) -> dict[str, object]:
    """构造一条完整或残缺的工具调用，测试只覆盖缺字段分支。"""

    return {
        "id": "call-1",
        "type": "function",
        "function": {"name": "calculator", "arguments": "{}"},
        **fields,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": ""},
        {"model": "   "},
        {"base_url": ""},
        {"base_url": "   "},
        {"timeout": 0},
        {"timeout": -1},
        {"timeout": True},
        {"timeout": "30"},
    ],
)
def test_deepseek_rejects_invalid_construction_configuration(
    kwargs: dict[str, object],
) -> None:
    """空 model、空 base_url 与非法 timeout 在构造时抛稳定配置错误。"""

    with pytest.raises(LLMConfigurationError):
        DeepSeekLLM(client=RecordingClient(), **kwargs)  # type: ignore[arg-type]


def test_deepseek_accepts_positive_float_timeout_with_injected_client() -> None:
    """正数浮点 timeout 是合法配置，注入客户端时无需密钥。"""

    client = RecordingClient()
    llm = DeepSeekLLM(client=client, timeout=0.5, base_url="https://example.com")

    result = llm.complete([Message(role=MessageRole.USER, content="测试")])

    assert result.content == "完成"
    assert llm.timeout == 0.5


class SampleTool:
    """满足适配器读取名称与描述的最小工具替身。"""

    name = "calculator"
    description = "计算一个算术表达式"

    def execute(self, arguments: dict[str, object]) -> str:
        return "4"


def test_deepseek_rejects_duplicate_tool_names_before_request() -> None:
    """重复工具名在序列化阶段被拒绝，不发供应商请求。"""

    client = RecordingClient()

    with pytest.raises(LLMInputError) as caught:
        DeepSeekLLM(client=client).complete(
            [Message(role=MessageRole.USER, content="测试")],
            tools=[SampleTool(), SampleTool()],
        )

    assert "重复" in str(caught.value)
    assert client.calls == []


@pytest.mark.parametrize(
    ("tool_name", "message"),
    [
        (123, "name"),
        ("", "name"),
        ("   ", "name"),
    ],
)
def test_deepseek_rejects_invalid_tool_name_before_request(
    tool_name: object,
    message: str,
) -> None:
    """工具名非字符串或空白时在发请求前被拒绝。"""

    class BadNameTool:
        name = tool_name
        description = "名字非法的工具"

        def execute(self, arguments: dict[str, object]) -> str:
            return "ok"

    client = RecordingClient()

    with pytest.raises(LLMInputError) as caught:
        DeepSeekLLM(client=client).complete(
            [Message(role=MessageRole.USER, content="测试")],
            tools=[BadNameTool()],
        )

    assert message in str(caught.value)
    assert client.calls == []


def test_deepseek_rejects_non_sequence_tools_before_request() -> None:
    """tools 不是序列时按输入错误拒绝，不发供应商请求。"""

    client = RecordingClient()

    with pytest.raises(LLMInputError):
        DeepSeekLLM(client=client).complete(
            [Message(role=MessageRole.USER, content="测试")],
            tools="calculator",  # type: ignore[arg-type]
        )

    assert client.calls == []


def test_deepseek_uses_placeholder_for_non_string_description() -> None:
    """描述非字符串时使用稳定占位符，不把对象写进请求。"""

    class NumberDescriptionTool:
        name = "numbered"
        description = 123  # type: ignore[assignment]

        def execute(self, arguments: dict[str, object]) -> str:
            return "ok"

    client = RecordingClient()
    DeepSeekLLM(client=client).complete(
        [Message(role=MessageRole.USER, content="测试")],
        tools=[NumberDescriptionTool()],
    )

    function = client.calls[0]["tools"][0]["function"]  # type: ignore[index]
    assert function["description"] == "（无描述）"


@pytest.mark.parametrize(
    "raw_call",
    [
        tool_call(id=None),  # type: ignore[arg-type]
        tool_call(type="gadget"),
        tool_call(function=None),  # type: ignore[arg-type]
        tool_call(function={"type": "missing-name"}),
        tool_call(function={"name": "calculator"}),
    ],
)
def test_deepseek_rejects_malformed_tool_call_fields(
    raw_call: dict[str, object],
) -> None:
    """tool_call 缺 id、type 非 function、缺 function 或缺 name 都报稳定错误。"""

    client = RecordingClient(
        response=response_message(content=None, tool_calls=[raw_call])
    )

    with pytest.raises(LLMResponseError):
        DeepSeekLLM(client=client).complete(
            [Message(role=MessageRole.USER, content="测试")],
        )


@pytest.mark.parametrize(
    "response",
    [
        response_message(content=123),
        response_message(content=None, tool_calls={"not": "a sequence"}),
        response_message(content=None, tool_calls="not-a-sequence"),
        response_message(
            content=None,
            tool_calls=[
                tool_call(),
                tool_call(),
            ],
        ),
    ],
)
def test_deepseek_rejects_malformed_response_shapes(
    response: dict[str, object],
) -> None:
    """content 类型错误、tool_calls 非序列或重复调用编号都报稳定错误。"""

    with pytest.raises(LLMResponseError):
        DeepSeekLLM(client=RecordingClient(response=response)).complete(
            [Message(role=MessageRole.USER, content="测试")],
        )


def test_deepseek_boundary_errors_are_stable_and_do_not_leak_sdk_text() -> None:
    """边界错误消息保持稳定，不包含异常类名、堆栈或 SDK 原始文本。"""

    malformed = tool_call(id=None)  # type: ignore[arg-type]
    client = RecordingClient(
        response=response_message(content=None, tool_calls=[malformed])
    )

    with pytest.raises(LLMResponseError) as caught:
        DeepSeekLLM(client=client).complete(
            [Message(role=MessageRole.USER, content="测试")],
        )

    message = str(caught.value)
    assert message
    assert "Traceback" not in message
    assert "ValidationError" not in message
    assert "Line" not in message
    assert "File" not in message
