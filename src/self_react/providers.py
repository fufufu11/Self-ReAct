"""模型 provider 注册表与工厂（R-01）。

把"模型名 -> LLM 适配器工厂"的映射集中到一个可注册的注册表：CLI 与业务
代码按模型名选择后端，新增供应商时调用 ``register_provider`` 即可提供扩展
点，无需修改 CLI 的模型分支。默认注册三个 provider：

- ``fake``：确定性离线演示（Fake LLM，不访问网络、不需要密钥）；
- ``deepseek``：DeepSeek OpenAI 兼容接口（读 ``DEEPSEEK_API_KEY``）；
- ``openai``：OpenAI 原生接口（读 ``OPENAI_API_KEY``）。

工厂采用惰性导入：deepseek/openai 适配器只在真正创建时导入，因此
``--help``、参数校验路径与 ``fake`` 演示不会触碰 openai SDK 或读取
API Key。注册表只保存名称与工厂，不保存客户端、密钥或其它运行时资源。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from self_react.llm import LLM, FakeLLM, LLMConfigurationError
from self_react.models import Message, MessageRole

ProviderFactory = Callable[[], LLM]
"""provider 工厂的公开形态：``() -> LLM``。"""

_PROVIDERS: dict[str, ProviderFactory] = {}


def _fake_provider() -> LLM:
    """构造确定性离线演示用 Fake LLM。

    演示任务固定走"计算器 -> 检索 -> 最终回答"三步，与三个真实工具对应，
    让没有 API Key 的用户也能完整看到"任务 -> 工具 -> 观察 -> 回答"的
    流水线。相同输入永远得到相同输出，不访问网络、不读取环境变量。
    """

    return FakeLLM(
        [
            Message(
                role=MessageRole.ASSISTANT,
                content=json.dumps(
                    {
                        "kind": "tool_call",
                        "call_id": "call-1",
                        "name": "calculator",
                        "arguments": {"expression": "2 + 2"},
                    },
                    ensure_ascii=False,
                ),
            ),
            Message(
                role=MessageRole.ASSISTANT,
                content=json.dumps(
                    {
                        "kind": "tool_call",
                        "call_id": "call-2",
                        "name": "retrieve",
                        "arguments": {"query": "react"},
                    },
                    ensure_ascii=False,
                ),
            ),
            Message(
                role=MessageRole.ASSISTANT,
                content=json.dumps(
                    {
                        "kind": "final_answer",
                        "content": "计算完成，并查到了 ReAct 的说明。",
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
    )


def _deepseek_provider() -> LLM:
    """构造真实 DeepSeek 适配器；密钥缺失时抛稳定配置错误。"""

    from self_react.deepseek import DEFAULT_MODEL, DeepSeekLLM

    return DeepSeekLLM(model=DEFAULT_MODEL)


def _openai_provider() -> LLM:
    """构造真实 OpenAI 适配器；密钥缺失时抛稳定配置错误。"""

    from self_react.openai import DEFAULT_MODEL, OpenAILLM

    return OpenAILLM(model=DEFAULT_MODEL)


def register_provider(name: str, factory: ProviderFactory) -> None:
    """注册一个模型名到工厂的映射；重复名称直接拒绝。

    ``name`` 必须是非空字符串；``factory`` 必须是无参可调用对象并返回
    满足 ``LLM`` 协议的适配器（返回值在创建时校验）。注册表一旦建立就
    不覆盖既有名称，避免扩展点被意外改写。
    """

    if not isinstance(name, str) or not name.strip():
        raise ValueError("provider 名称必须是非空字符串")
    if not callable(factory):
        raise TypeError("factory 必须是可调用对象")
    normalized = name.strip()
    if normalized in _PROVIDERS:
        raise ValueError(f"provider 已注册：{normalized}")
    _PROVIDERS[normalized] = factory


def available_providers() -> tuple[str, ...]:
    """返回按名称排序的全部已注册模型名。"""

    return tuple(sorted(_PROVIDERS))


def create_provider(name: str) -> LLM:
    """按模型名创建 LLM 适配器；未知名称或非法结果抛稳定配置错误。"""

    if not isinstance(name, str) or not name.strip():
        raise LLMConfigurationError(f"未知模型：{name}")
    normalized = name.strip()
    factory = _PROVIDERS.get(normalized)
    if factory is None:
        raise LLMConfigurationError(f"未知模型：{normalized}")
    llm = factory()
    if not isinstance(llm, LLM):
        raise LLMConfigurationError(
            f"provider {normalized} 未返回满足 LLM 协议的适配器"
        )
    return llm


register_provider("fake", _fake_provider)
register_provider("deepseek", _deepseek_provider)
register_provider("openai", _openai_provider)


__all__ = [
    "ProviderFactory",
    "available_providers",
    "create_provider",
    "register_provider",
]
