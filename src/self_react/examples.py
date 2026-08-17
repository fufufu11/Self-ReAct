"""Day 16 端到端示例：三个确定性离线演示（单工具、多工具、工具失败后恢复），
R-06 追加两个可选模式演示（plan-demo、reflection-demo）。

本模块把示例定义成数据：任务文本 + Fake LLM 预置响应 + 可选模式开关，并
提供一个运行入口。示例只组合已有模块——``FakeLLM``（Day 5）、
``ToolRegistry``（Day 7）、``Agent``（Day 12）与 ``render_trace``
（Day 13）——不复制主循环逻辑，``Agent`` 仍是唯一控制者。所有示例使用
确定性工具与预置响应，不访问网络、不依赖真实 API Key；相同命令永远得到
相同的决策与观察（耗时除外）。
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

ExampleName = Literal[
    "single-tool",
    "multi-tool",
    "failure-recovery",
    "plan-demo",
    "reflection-demo",
]
"""示例名称：Day 16 三条主线 + R-06 两个可选模式演示。"""


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


def _plan_message(content: str) -> Message:
    """构造一条符合 R-06 规划契约的计划原始输出。"""

    return Message(
        role=MessageRole.ASSISTANT,
        content=json.dumps(
            {"kind": "plan", "content": content},
            ensure_ascii=False,
        ),
    )


def _reflection_message(content: str) -> Message:
    """构造一条符合 R-06 反思契约的反思原始输出。"""

    return Message(
        role=MessageRole.ASSISTANT,
        content=json.dumps(
            {"kind": "reflection", "content": content},
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
    """一个确定性端到端示例：名称、标题、任务、Fake LLM 预置响应与可选模式。"""

    name: ExampleName
    title: str
    task: str
    responses: tuple[Message, ...]
    plan_mode: bool = False
    reflection_mode: bool = False


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
    "plan-demo": ExampleScenario(
        name="plan-demo",
        title="先规划后执行",
        task="计算 2 + 2，并检索 react 主题；先输出计划再执行",
        responses=(
            _plan_message("先调用计算器得到结果，再检索 react 主题，最后汇总回答"),
            _tool_call_message("call-1", "calculator", {"expression": "2 + 2"}),
            _tool_call_message("call-2", "retrieve", {"query": "react"}),
            _final_answer_message(
                "计算结果是 4；ReAct 是一种让模型推理与行动交错的智能体范式。"
            ),
        ),
        plan_mode=True,
    ),
    "reflection-demo": ExampleScenario(
        name="reflection-demo",
        title="失败后反思",
        task="先检索 unknown-topic 失败，反思后改用 react 继续",
        responses=(
            _tool_call_message("call-1", "retrieve", {"query": "unknown-topic"}),
            _reflection_message("检索失败，原因是主题不存在；下一步改用 react"),
            _tool_call_message("call-2", "retrieve", {"query": "react"}),
            _final_answer_message(
                "第一次检索失败后反思原因，改用 react 成功找到 ReAct 的说明。"
            ),
        ),
        reflection_mode=True,
    ),
}
"""五个示例的固定定义：任务、工具序列、模式开关与最终回答全部可复现。"""


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
    最终回答，因此不会出现步数耗尽，输出完全可复现。示例定义的可选模式
    开关（``plan_mode`` / ``reflection_mode``）原样传给 ``Agent.run``。
    """

    scenario = EXAMPLES[name]
    llm = build_example_llm(name)
    registry = build_example_registry()
    agent = Agent(llm=llm, registry=registry, max_steps=len(scenario.responses))
    return agent.run(
        scenario.task,
        plan_mode=scenario.plan_mode,
        reflection_mode=scenario.reflection_mode,
    )


__all__ = [
    "EXAMPLES",
    "ExampleName",
    "ExampleScenario",
    "build_example_llm",
    "build_example_registry",
    "run_example",
]
