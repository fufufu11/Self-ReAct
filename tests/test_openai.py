"""OpenAI 原生适配器的离线请求/响应转换测试（R-01）。

本文件与 ``test_deepseek.py`` 同构：全部使用注入客户端，不访问网络、
不依赖真实 API Key。OpenAI 与 DeepSeek 共用 ``openai_compat`` 的转换
逻辑，因此这里只验证 OpenAI 特有的边界：默认常量、密钥来源
（``OPENAI_API_KEY``）、低推理档（``reasoning_effort`` 默认 ``low``、
经 ``extra_body`` 传递）、``base_url`` 的 ``OPENAI_BASE_URL`` 环境变量
覆盖，以及共享转换在 OpenAI 适配器下的回归。
"""

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

from self_react.llm import (
    LLM,
    LLMConfigurationError,
    LLMInputError,
    LLMProviderError,
    LLMProviderErrorCode,
    LLMResponseError,
    collect_stream,
)
from self_react.models import Message, MessageRole, ToolCall
from self_react.openai import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TIMEOUT,
    OpenAILLM,
)


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


def test_openai_defaults_are_stable() -> None:
    """默认地址、模型、低推理档与超时是适配器公开的稳定常量。"""

    assert DEFAULT_BASE_URL == "https://api.openai.com/v1"
    assert DEFAULT_MODEL == "gpt-5.6"
    assert DEFAULT_REASONING_EFFORT == "low"
    assert DEFAULT_TIMEOUT == 30.0


def test_openai_serializes_message_history_and_returns_normal_response() -> None:
    """system/user/assistant/tool 消息应保留角色和工具关联。"""

    call = ToolCall(
        call_id="call-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )
    client = RecordingClient(response=response_message(content="4"))
    llm = OpenAILLM(client=client)

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
    # 默认低推理档：reasoning_effort 经 extra_body 传给 Chat Completions
    assert request["extra_body"] == {"reasoning_effort": "low"}
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


def test_openai_deserializes_tool_call_response_without_executing_it() -> None:
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

    result = OpenAILLM(client=client).complete(
        [Message(role=MessageRole.USER, content="计算 6 * 7")]
    )

    assert result.role is MessageRole.ASSISTANT
    assert result.content == ""
    assert result.tool_calls[0].call_id == "call-9"
    assert result.tool_calls[0].name == "calculator"
    assert result.tool_calls[0].arguments == {"expression": "6 * 7"}


def test_openai_serializes_tool_definitions() -> None:
    """传入工具清单时，请求应包含 function 定义。"""

    class SampleTool:
        name = "calculator"
        description = "计算一个算术表达式"

    client = RecordingClient(response=response_message(content="4"))
    llm = OpenAILLM(client=client)

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
def test_openai_rejects_invalid_or_incomplete_provider_response(
    response: dict[str, object],
) -> None:
    """空 choices、错误角色和非法参数不能返回半合法消息。"""

    with pytest.raises(LLMResponseError):
        OpenAILLM(client=RecordingClient(response=response)).complete(
            [Message(role=MessageRole.USER, content="测试")]
        )


def test_openai_rejects_invalid_input_before_client_call() -> None:
    """输入校验失败时不能发出供应商请求。"""

    client = RecordingClient(response=response_message())
    llm = OpenAILLM(client=client)

    with pytest.raises(LLMInputError):
        llm.complete([])

    assert client.calls == []


def test_openai_requires_runtime_key_without_injected_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实客户端路径只从 OPENAI_API_KEY 读取密钥。"""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(LLMConfigurationError):
        OpenAILLM()


def test_openai_reasoning_effort_none_omits_extra_body() -> None:
    """reasoning_effort=None 时不传额外配置，使用供应商默认推理档。"""

    client = RecordingClient(response=response_message(content="4"))
    llm = OpenAILLM(client=client, reasoning_effort=None)

    result = llm.complete([Message(role=MessageRole.USER, content="计算 2 + 2")])

    assert result.content == "4"
    assert llm.reasoning_effort is None
    assert client.calls[0]["extra_body"] is None


def test_openai_reasoning_effort_medium_is_sent_as_extra_body() -> None:
    """显式 reasoning_effort 覆盖默认低档并随请求发送。"""

    client = RecordingClient(response=response_message(content="4"))
    llm = OpenAILLM(client=client, reasoning_effort="medium")

    llm.complete([Message(role=MessageRole.USER, content="计算 2 + 2")])

    assert llm.reasoning_effort == "medium"
    assert client.calls[0]["extra_body"] == {"reasoning_effort": "medium"}


@pytest.mark.parametrize(
    "reasoning_effort",
    ["", "   ", "ultra", "LOW", 123, True],
)
def test_openai_rejects_invalid_reasoning_effort(
    reasoning_effort: object,
) -> None:
    """空串、未知档位或非字符串在构造时抛稳定配置错误。"""

    with pytest.raises(LLMConfigurationError):
        OpenAILLM(client=RecordingClient(), reasoning_effort=reasoning_effort)  # type: ignore[arg-type]


def test_openai_base_url_reads_env_when_not_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未显式传 base_url 时优先读 OPENAI_BASE_URL 环境变量。"""

    monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")

    llm = OpenAILLM(client=RecordingClient(response=response_message()))

    assert llm.base_url == "https://relay.example.com/v1"


def test_openai_base_url_explicit_argument_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式 base_url 参数优先于 OPENAI_BASE_URL 环境变量。"""

    monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")

    llm = OpenAILLM(client=RecordingClient(), base_url="https://example.com")

    assert llm.base_url == "https://example.com"


def test_openai_base_url_falls_back_to_default_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """环境变量缺失或空白时回退官方默认地址。"""

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    llm = OpenAILLM(client=RecordingClient())
    assert llm.base_url == DEFAULT_BASE_URL

    monkeypatch.setenv("OPENAI_BASE_URL", "   ")
    llm = OpenAILLM(client=RecordingClient())
    assert llm.base_url == DEFAULT_BASE_URL


def test_openai_real_client_construction_uses_env_key_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实客户端路径同时消费 OPENAI_API_KEY 与 OPENAI_BASE_URL。"""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")

    llm = OpenAILLM()

    assert llm.base_url == "https://relay.example.com/v1"


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
def test_openai_rejects_invalid_construction_configuration(
    kwargs: dict[str, object],
) -> None:
    """空 model、空 base_url 与非法 timeout 在构造时抛稳定配置错误。"""

    with pytest.raises(LLMConfigurationError):
        OpenAILLM(client=RecordingClient(), **kwargs)  # type: ignore[arg-type]


def test_openai_accepts_positive_float_timeout_with_injected_client() -> None:
    """正数浮点 timeout 是合法配置，注入客户端时无需密钥。"""

    client = RecordingClient(response=response_message(content="完成"))
    llm = OpenAILLM(client=client, timeout=0.5, base_url="https://example.com")

    result = llm.complete([Message(role=MessageRole.USER, content="测试")])

    assert result.content == "完成"
    assert llm.timeout == 0.5


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
def test_openai_maps_provider_errors_without_leaking_sdk_text(
    error_factory: Callable[[], Exception],
    expected_code: LLMProviderErrorCode,
) -> None:
    """认证、超时、网络和服务错误只暴露稳定类别。"""

    client = RecordingClient(error=error_factory())

    with pytest.raises(LLMProviderError) as caught:
        OpenAILLM(client=client).complete(
            [Message(role=MessageRole.USER, content="测试")]
        )

    assert caught.value.code is expected_code
    assert "secret" not in str(caught.value)
    assert len(client.calls) == 1


def test_openai_maps_unknown_provider_exception_to_unknown() -> None:
    """未识别的客户端异常也不应把原始文本传播给调用方。"""

    client = RecordingClient(error=RuntimeError("secret transport detail"))

    with pytest.raises(LLMProviderError) as caught:
        OpenAILLM(client=client).complete(
            [Message(role=MessageRole.USER, content="测试")]
        )

    assert caught.value.code is LLMProviderErrorCode.UNKNOWN
    assert "secret" not in str(caught.value)


def _stream_delta_chunk(
    *,
    content: str | None = None,
    tool_calls: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """构造一个 OpenAI 兼容的流式 delta 块。"""

    delta: dict[str, object] = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {"choices": [{"delta": delta}]}


def test_openai_complete_stream_requests_stream_and_assembles_content() -> None:
    """流式请求应带 stream=True 且无额外配置，内容增量可组装。"""

    client = RecordingClient(
        response=[
            _stream_delta_chunk(content="你"),
            _stream_delta_chunk(content="好"),
            {"choices": []},
        ]
    )
    llm = OpenAILLM(client=client)

    chunks = list(llm.complete_stream([Message(role=MessageRole.USER, content="测试")]))

    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["stream"] is True
    assert request["extra_body"] == {"reasoning_effort": "low"}
    assert "".join(chunk.content for chunk in chunks) == "你好"
    assert collect_stream(chunks) == Message(role=MessageRole.ASSISTANT, content="你好")


def test_openai_complete_stream_assembles_tool_call_from_fragments() -> None:
    """工具调用参数跨多个块时按 index 增量拼接，最终等价于一次性响应。"""

    client = RecordingClient(
        response=[
            _stream_delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call-9",
                        "type": "function",
                        "function": {"name": "calculator", "arguments": ""},
                    }
                ]
            ),
            _stream_delta_chunk(
                tool_calls=[{"index": 0, "function": {"arguments": '{"expr'}}]
            ),
            _stream_delta_chunk(
                tool_calls=[
                    {"index": 0, "function": {"arguments": 'ession": "6 * 7"}'}}
                ]
            ),
        ]
    )
    llm = OpenAILLM(client=client)

    message = collect_stream(
        llm.complete_stream([Message(role=MessageRole.USER, content="计算 6 * 7")])
    )

    assert message.content == ""
    assert message.tool_calls == [
        ToolCall(
            call_id="call-9",
            name="calculator",
            arguments={"expression": "6 * 7"},
        )
    ]


def test_openai_complete_stream_streams_final_answer_content_live() -> None:
    """final_answer 工具调用时 content 参数增量实时经 final_answer_content 透出。"""

    client = RecordingClient(
        response=[
            _stream_delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call-9",
                        "type": "function",
                        "function": {"name": "final_answer", "arguments": ""},
                    }
                ]
            ),
            _stream_delta_chunk(
                tool_calls=[{"index": 0, "function": {"arguments": '{"content": "2 '}}]
            ),
            _stream_delta_chunk(
                tool_calls=[{"index": 0, "function": {"arguments": "+ 2 = "}}]
            ),
            _stream_delta_chunk(
                tool_calls=[{"index": 0, "function": {"arguments": '4。"}'}}]
            ),
        ]
    )
    llm = OpenAILLM(client=client)

    chunks = list(
        llm.complete_stream([Message(role=MessageRole.USER, content="计算 2 + 2")])
    )

    assert "".join(chunk.final_answer_content for chunk in chunks) == "2 + 2 = 4。"
    message = collect_stream(chunks)
    assert message.content == ""
    assert message.tool_calls == [
        ToolCall(
            call_id="call-9",
            name="final_answer",
            arguments={"content": "2 + 2 = 4。"},
        )
    ]


def test_openai_complete_stream_maps_create_error_to_stable_code() -> None:
    """发起流式请求失败时映射稳定错误类别，不泄漏 SDK 文本。"""

    client = RecordingClient(error=APIConnectionError(request=SimpleNamespace()))

    with pytest.raises(LLMProviderError) as caught:
        list(
            OpenAILLM(client=client).complete_stream(
                [Message(role=MessageRole.USER, content="测试")]
            )
        )

    assert caught.value.code is LLMProviderErrorCode.CONNECTION
    assert "secret" not in str(caught.value)


def test_openai_complete_stream_maps_mid_stream_error_to_stable_code() -> None:
    """流中间失败同样映射稳定错误类别，不返回半成品消息。"""

    def failing_stream():
        yield _stream_delta_chunk(content="部分")
        raise APITimeoutError(request=SimpleNamespace())

    client = RecordingClient(response=failing_stream())

    with pytest.raises(LLMProviderError) as caught:
        list(
            OpenAILLM(client=client).complete_stream(
                [Message(role=MessageRole.USER, content="测试")]
            )
        )

    assert caught.value.code is LLMProviderErrorCode.TIMEOUT


def test_openai_complete_stream_rejects_malformed_delta() -> None:
    """delta.content 非字符串时报稳定响应错误。"""

    client = RecordingClient(response=[{"choices": [{"delta": {"content": 123}}]}])

    with pytest.raises(LLMResponseError):
        list(
            OpenAILLM(client=client).complete_stream(
                [Message(role=MessageRole.USER, content="测试")]
            )
        )
