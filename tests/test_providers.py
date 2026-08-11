"""模型 provider 注册表与工厂测试（R-01）。

验证三个默认 provider 的注册与创建路径：``fake`` 离线确定性、
``deepseek`` / ``openai`` 无密钥时抛稳定配置错误；以及扩展点行为
（注册新 provider、拒绝重复名称、拒绝非法工厂与非法返回值）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from self_react.llm import LLM, LLMConfigurationError, StreamChunk
from self_react.models import Message, MessageRole
from self_react.providers import (
    available_providers,
    create_provider,
    register_provider,
)


class PresetLLM:
    """测试用确定性适配器替身：按顺序返回预置消息。"""

    def __init__(self, responses: list[Message]) -> None:
        self._responses = responses
        self._index = 0

    def complete(
        self,
        messages: object,
        *,
        tools: object | None = None,
    ) -> Message:
        response = self._responses[self._index]
        self._index += 1
        return response

    def complete_stream(
        self,
        messages: object,
        *,
        tools: object | None = None,
    ) -> Iterator[StreamChunk]:
        response = self.complete(messages, tools=tools)
        yield StreamChunk(content=response.content)


def test_default_providers_are_registered_and_sorted() -> None:
    """默认注册 fake、deepseek、openai 三个 provider。"""

    providers = set(available_providers())
    assert {"deepseek", "fake", "openai"} <= providers
    assert available_providers() == tuple(sorted(available_providers()))


def test_create_fake_provider_returns_llm() -> None:
    """``fake`` 返回满足 LLM 协议的确定性适配器。"""

    llm = create_provider("fake")
    assert isinstance(llm, LLM)


def test_create_deepseek_provider_requires_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``deepseek`` 无密钥时抛稳定配置错误，不创建适配器。"""

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(LLMConfigurationError):
        create_provider("deepseek")


def test_create_openai_provider_requires_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``openai`` 无密钥时抛稳定配置错误，不创建适配器。"""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(LLMConfigurationError):
        create_provider("openai")


@pytest.mark.parametrize("name", ["", "   ", "unknown-model", None, 123])
def test_create_provider_rejects_unknown_or_invalid_name(name: object) -> None:
    """未知或非法名称抛稳定配置错误。"""

    with pytest.raises(LLMConfigurationError):
        create_provider(name)  # type: ignore[arg-type]


def test_register_and_create_custom_provider() -> None:
    """扩展点：注册新 provider 后可按名称创建。"""

    response = Message(role=MessageRole.ASSISTANT, content="自定义回答")
    factory_calls: list[str] = []

    def custom_factory() -> LLM:
        factory_calls.append("called")
        return PresetLLM([response])

    register_provider("custom-test", custom_factory)

    assert "custom-test" in available_providers()
    llm = create_provider("custom-test")
    assert factory_calls == ["called"]
    assert llm.complete([]) == response


def test_register_provider_rejects_duplicate_name() -> None:
    """重复注册同一名称直接拒绝，避免扩展点被意外改写。"""

    with pytest.raises(ValueError) as caught:
        register_provider("deepseek", lambda: PresetLLM([]))

    assert "已注册" in str(caught.value)


@pytest.mark.parametrize(
    ("name", "factory"),
    [
        ("", lambda: PresetLLM([])),
        ("   ", lambda: PresetLLM([])),
        ("bad-factory", None),
    ],
)
def test_register_provider_rejects_invalid_name_or_factory(
    name: str,
    factory: object,
) -> None:
    """空名称与非可调用工厂在注册阶段被拒绝。"""

    with pytest.raises((ValueError, TypeError)):
        register_provider(name, factory)  # type: ignore[arg-type]


def test_create_provider_rejects_non_llm_result() -> None:
    """工厂返回不满足 LLM 协议的对象时抛稳定配置错误。"""

    register_provider("bad-result-test", lambda: object())  # type: ignore[arg-type]

    with pytest.raises(LLMConfigurationError) as caught:
        create_provider("bad-result-test")

    assert "LLM 协议" in str(caught.value)
