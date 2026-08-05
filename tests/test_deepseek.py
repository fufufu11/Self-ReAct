"""DeepSeek 适配器的离线请求/响应转换测试。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace

import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from self_react.deepseek import DEFAULT_MODEL, DeepSeekLLM
from self_react.llm import (
    LLM,
    LLMConfigurationError,
    LLMInputError,
    LLMProviderError,
    LLMProviderErrorCode,
    LLMResponseError,
)
from self_react.models import Message, MessageRole, ToolCall


class RecordingClient:
    """捕获一次请求并返回固定响应的最小客户端替身。"""

    def __init__(
        self, response: object | None = None, error: Exception | None = None
    ) -> None:
        self.chat = SimpleNamespace(completions=self)
        self.response = response
        self.error = error
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
        if self.error is not None:
            raise self.error
        return self.response


def response_message(
    *,
    content: str | None = "回答",
    role: str = "assistant",
    tool_calls: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """构造同时兼容 SDK 对象和字典响应的测试数据。"""

    message: dict[str, object] = {"role": role, "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def test_deepseek_serializes_message_history_and_returns_normal_response() -> None:
    """system/user/assistant/tool 消息应保留角色和工具关联。"""

    call = ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )
    client = RecordingClient(response=response_message(content="4"))
    llm = DeepSeekLLM(client=client)

    result = llm.complete(
        [
            Message(role=MessageRole.SYSTEM, content="你是计算器"),
            Message(role=MessageRole.USER, content="计算 2 + 2"),
            Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call]),
            Message(
                role=MessageRole.TOOL,
                content="4",
                tool_call_id=call.call_id,
            ),
        ]
    )

    assert isinstance(llm, LLM)
    assert result == Message(role=MessageRole.ASSISTANT, content="4")
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["model"] == DEFAULT_MODEL
    assert request["stream"] is False
    assert request["tools"] is None
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request["messages"] == [
        {"role": "system", "content": "你是计算器"},
        {"role": "user", "content": "计算 2 + 2"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": '{"expression":"2 + 2"}',
                    },
                }
            ],
        },
        {"role": "tool", "content": "4", "tool_call_id": "call-1"},
    ]


def test_deepseek_deserializes_tool_call_response_without_executing_it() -> None:
    """工具响应只转换为 ToolCall，不执行工具或生成 ToolResult。"""

    client = RecordingClient(
        response=response_message(
            content=None,
            tool_calls=[
                {
                    "id": "call-9",
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": '{"expression":"6 * 7"}',
                    },
                }
            ],
        )
    )

    result = DeepSeekLLM(client=client).complete(
        [Message(role=MessageRole.USER, content="计算 6 * 7")]
    )

    assert result.role is MessageRole.ASSISTANT
    assert result.content == ""
    assert result.tool_calls[0].call_id == "call-9"
    assert result.tool_calls[0].name == "calculator"
    assert result.tool_calls[0].arguments == {"expression": "6 * 7"}


def test_deepseek_serializes_tool_definitions_and_thinking_disabled() -> None:
    """传入工具清单时，请求应包含 function 定义与思考模式禁用配置。"""

    class SampleTool:
        name = "calculator"
        description = "计算一个算术表达式"

    client = RecordingClient(response=response_message(content="4"))
    llm = DeepSeekLLM(client=client)

    result = llm.complete(
        [Message(role=MessageRole.USER, content="计算 2 + 2")],
        tools=[SampleTool()],
    )

    assert result == Message(role=MessageRole.ASSISTANT, content="4")
    request = client.calls[0]
    assert request["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "计算一个算术表达式",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


def test_deepseek_thinking_can_be_enabled_explicitly() -> None:
    """``thinking_disabled=False`` 时请求不携带禁用配置。"""

    client = RecordingClient(response=response_message(content="4"))
    llm = DeepSeekLLM(client=client, thinking_disabled=False)

    llm.complete([Message(role=MessageRole.USER, content="测试")])

    assert client.calls[0]["extra_body"] == {}


@pytest.mark.parametrize(
    "response",
    [
        {"choices": []},
        response_message(role="user"),
        response_message(content=None),
        response_message(
            content=None,
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": "[]",
                    },
                }
            ],
        ),
        response_message(
            content=None,
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": "not-json",
                    },
                }
            ],
        ),
    ],
)
def test_deepseek_rejects_invalid_or_incomplete_provider_response(
    response: dict[str, object],
) -> None:
    """空 choices、错误角色和非法参数不能返回半合法消息。"""

    with pytest.raises(LLMResponseError):
        DeepSeekLLM(client=RecordingClient(response=response)).complete(
            [Message(role=MessageRole.USER, content="测试")]
        )


def test_deepseek_rejects_invalid_input_before_client_call() -> None:
    """输入校验失败时不能发出供应商请求。"""

    client = RecordingClient(response=response_message())
    llm = DeepSeekLLM(client=client)

    with pytest.raises(LLMInputError):
        llm.complete([])

    assert client.calls == []


def test_deepseek_requires_runtime_key_without_injected_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实客户端路径只从 DEEPSEEK_API_KEY 读取密钥。"""

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(LLMConfigurationError):
        DeepSeekLLM()


def _request(url_status: int) -> SimpleNamespace:
    """为 OpenAI SDK 错误构造最小 HTTP 响应。"""

    return SimpleNamespace(
        request=SimpleNamespace(),
        status_code=url_status,
        headers={},
    )


@pytest.mark.parametrize(
    ("error_factory", "expected_code"),
    [
        (
            lambda: AuthenticationError(
                "secret response",
                response=_request(401),
                body={"error": "secret body"},
            ),
            LLMProviderErrorCode.AUTHENTICATION,
        ),
        (
            lambda: APITimeoutError(request=SimpleNamespace()),
            LLMProviderErrorCode.TIMEOUT,
        ),
        (
            lambda: APIConnectionError(request=SimpleNamespace()),
            LLMProviderErrorCode.CONNECTION,
        ),
        (
            lambda: RateLimitError(
                "secret response",
                response=_request(429),
                body={"error": "secret body"},
            ),
            LLMProviderErrorCode.RATE_LIMIT,
        ),
        (
            lambda: BadRequestError(
                "secret response",
                response=_request(400),
                body={"error": "secret body"},
            ),
            LLMProviderErrorCode.BAD_REQUEST,
        ),
        (
            lambda: APIStatusError(
                "secret response",
                response=_request(503),
                body={"error": "secret body"},
            ),
            LLMProviderErrorCode.SERVICE,
        ),
    ],
)
def test_deepseek_maps_provider_errors_without_leaking_sdk_text(
    error_factory: Callable[[], Exception],
    expected_code: LLMProviderErrorCode,
) -> None:
    """认证、超时、网络和服务错误只暴露稳定类别。"""

    client = RecordingClient(error=error_factory())

    with pytest.raises(LLMProviderError) as caught:
        DeepSeekLLM(client=client).complete(
            [Message(role=MessageRole.USER, content="测试")]
        )

    assert caught.value.code is expected_code
    assert "secret" not in str(caught.value)
    assert len(client.calls) == 1


def test_deepseek_maps_unknown_provider_exception_to_unknown() -> None:
    """未识别的客户端异常也不应把原始文本传播给调用方。"""

    client = RecordingClient(error=RuntimeError("secret transport detail"))

    with pytest.raises(LLMProviderError) as caught:
        DeepSeekLLM(client=client).complete(
            [Message(role=MessageRole.USER, content="测试")]
        )

    assert caught.value.code is LLMProviderErrorCode.UNKNOWN
    assert "secret" not in str(caught.value)


def test_deepseek_timeout_and_connection_codes_are_distinct_and_stable() -> None:
    """超时与连接失败是互不混淆的稳定类别，不依赖 SDK 异常文本。"""

    timeout_llm = DeepSeekLLM(
        client=RecordingClient(error=APITimeoutError(request=SimpleNamespace()))
    )
    connection_llm = DeepSeekLLM(
        client=RecordingClient(error=APIConnectionError(request=SimpleNamespace()))
    )

    with pytest.raises(LLMProviderError) as timeout:
        timeout_llm.complete([Message(role=MessageRole.USER, content="测试")])
    with pytest.raises(LLMProviderError) as connection:
        connection_llm.complete([Message(role=MessageRole.USER, content="测试")])

    assert timeout.value.code is LLMProviderErrorCode.TIMEOUT
    assert connection.value.code is LLMProviderErrorCode.CONNECTION
    assert timeout.value.code is not connection.value.code
