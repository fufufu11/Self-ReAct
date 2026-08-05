"""Day 16 端到端示例：三个确定性离线演示（单工具、多工具、工具失败后恢复）。

本模块把示例定义成数据：任务文本 + Fake LLM 预置响应，并提供一个运行入口。
示例只组合已有模块——``FakeLLM``（Day 5）、``ToolRegistry``（Day 7）、
``Agent``（Day 12）与 ``render_trace``（Day 13）——不复制主循环逻辑，
``Agent`` 仍是唯一控制者。所有示例使用确定性工具与预置响应，不访问网络、
不依赖真实 API Key；相同命令永远得到相同的决策与观察（耗时除外）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from self_react.agent import Agent
from self_react.llm import FakeLLM
from self_react.models import AgentState, Message, MessageRole
from self_react.tools import (
    CalculatorTool,
    FileReaderTool,
    FinalAnswerTool,
    RetrieveTool,
    ToolRegistry,
)

ExampleName = Literal["single-tool", "multi-tool", "failure-recovery"]
"""Day 16 提供的三个示例名称。"""


def _tool_call_message(
    call_id: str, name: str, arguments: dict[str, object]
) -> Message:
    """构造一条符合 Day 10 格式契约的工具调用原始输出。"""

    return Message(
        role=MessageRole.ASSISTANT,
        content=json.dumps(
            {
                "kind": "tool_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            },
            ensure_ascii=False,
        ),
    )


def _final_answer_message(content: str) -> Message:
    """构造一条符合 Day 10 格式契约的最终回答原始输出。"""

    return Message(
        role=MessageRole.ASSISTANT,
        content=json.dumps(
            {"kind": "final_answer", "content": content},
            ensure_ascii=False,
        ),
    )


@dataclass(frozen=True)
class ExampleScenario:
    """一个确定性端到端示例：名称、标题、任务与 Fake LLM 预置响应。"""

    name: ExampleName
    title: str
    task: str
    responses: tuple[Message, ...]


EXAMPLES: dict[ExampleName, ExampleScenario] = {
    "single-tool": ExampleScenario(
        name="single-tool",
        title="单工具",
        task="计算 2 + 2",
        responses=(
            _tool_call_message("call-1", "calculator", {"expression": "2 + 2"}),
            _final_answer_message("2 + 2 = 4。"),
        ),
    ),
    "multi-tool": ExampleScenario(
        name="multi-tool",
        title="多工具",
        task="计算 2 + 2，并检索 react 主题",
        responses=(
            _tool_call_message("call-1", "calculator", {"expression": "2 + 2"}),
            _tool_call_message("call-2", "retrieve", {"query": "react"}),
            _final_answer_message(
                "计算结果是 4；ReAct 是一种让模型推理与行动交错的智能体范式。"
            ),
        ),
    ),
    "failure-recovery": ExampleScenario(
        name="failure-recovery",
        title="工具失败后恢复",
        task="先检索 unknown-topic，失败后换正确主题 react 继续",
        responses=(
            _tool_call_message("call-1", "retrieve", {"query": "unknown-topic"}),
            _tool_call_message("call-2", "retrieve", {"query": "react"}),
            _final_answer_message(
                "第一次检索失败后改用 react，成功找到 ReAct 的说明。"
            ),
        ),
    ),
}
"""三个示例的固定定义：任务、工具序列与最终回答全部可复现。"""


def build_example_llm(name: str) -> FakeLLM:
    """按示例名构造确定性 Fake LLM；未知名称抛 ``ValueError``。"""

    scenario = EXAMPLES[name]
    return FakeLLM(list(scenario.responses))


def build_example_registry() -> ToolRegistry:
    """构造示例使用的默认注册表，与 CLI ``run`` 的四个工具保持一致。"""

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(FileReaderTool(root_directory="C:/allowed"))
    registry.register(RetrieveTool())
    registry.register(FinalAnswerTool())
    return registry


def run_example(name: str) -> AgentState:
    """运行一个确定性端到端示例并返回终态 ``AgentState``。

    ``max_steps`` 固定等于预置响应数量：示例总是在最后一条响应处给出
    最终回答，因此不会出现步数耗尽，输出完全可复现。
    """

    scenario = EXAMPLES[name]
    llm = build_example_llm(name)
    registry = build_example_registry()
    agent = Agent(llm=llm, registry=registry, max_steps=len(scenario.responses))
    return agent.run(scenario.task)


__all__ = [
    "EXAMPLES",
    "ExampleName",
    "ExampleScenario",
    "build_example_llm",
    "build_example_registry",
    "run_example",
]
