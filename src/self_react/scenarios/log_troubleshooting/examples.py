"""日志/故障排查场景的确定性端到端示例（R-07）。

示例定义成数据：任务文本 + Fake LLM 预置响应，并复用 ``Agent`` 主循环与场景
注册表。三个示例分别覆盖“过滤 + 统计”、“聚合定位时间窗口”、“跨数据源发布关联”，
全部使用确定性工具与预置响应，不访问网络、不依赖真实 API Key。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from self_react.agent import Agent
from self_react.llm import FakeLLM
from self_react.models import AgentState, Message, MessageRole
from self_react.scenarios.log_troubleshooting.scenario import build_registry


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
class ScenarioExample:
    """一个确定性场景示例：名称、标题、任务与 Fake LLM 预置响应。"""

    name: str
    title: str
    task: str
    responses: tuple[Message, ...]


SCENARIO_EXAMPLES: dict[str, ScenarioExample] = {
    "log-5xx-spike": ScenarioExample(
        name="log-5xx-spike",
        title="排查 5xx 突增",
        task="排查 checkout 服务的 5xx 错误突增，给出根因假设与下一步动作。",
        responses=(
            _tool_call_message(
                "call-1", "log_query", {"path": "logs.ndjson", "service": "checkout"}
            ),
            _tool_call_message(
                "call-2",
                "log_query",
                {"path": "logs.ndjson", "service": "checkout", "error_code": "500"},
            ),
            _tool_call_message("call-3", "calculator", {"expression": "5 / 40 * 100"}),
            _tool_call_message(
                "call-4",
                "log_query",
                {
                    "path": "logs.ndjson",
                    "service": "checkout",
                    "level": "ERROR",
                    "group_by": "error_code",
                },
            ),
            _tool_call_message(
                "call-5", "runbook_search", {"query": "checkout 5xx 突增"}
            ),
            _final_answer_message(
                "checkout 服务的 5xx 突增：500 错误 5 条、占 checkout 日志 12.5%，"
                "5xx 总计 17 条；疑似最近发布引入回归或数据库连接池耗尽，"
                "建议回滚最近发布并扩容连接池。"
            ),
        ),
    ),
    "log-error-window": ScenarioExample(
        name="log-error-window",
        title="定位错误集中窗口",
        task="找出错误码 503 集中出现的时间窗口。",
        responses=(
            _tool_call_message(
                "call-1",
                "log_query",
                {"path": "logs.ndjson", "error_code": "503", "group_by": "hour"},
            ),
            _final_answer_message(
                "错误码 503 集中在 2026-08-12 11:00 整点桶（5 条），"
                "10:00 与 12:00 各 1 条。"
            ),
        ),
    ),
    "log-release-correlation": ScenarioExample(
        name="log-release-correlation",
        title="判断发布相关",
        task="判断 checkout 服务的 500 错误是否与最近发布相关。",
        responses=(
            _tool_call_message(
                "call-1",
                "log_query",
                {
                    "path": "logs.ndjson",
                    "service": "checkout",
                    "error_code": "500",
                    "time_start": "2026-08-12 09:00:00",
                    "time_end": "2026-08-12 09:59:59",
                },
            ),
            _tool_call_message(
                "call-2",
                "log_query",
                {
                    "path": "logs.ndjson",
                    "service": "checkout",
                    "error_code": "500",
                    "time_start": "2026-08-12 10:00:00",
                    "time_end": "2026-08-12 10:59:59",
                },
            ),
            _tool_call_message("call-3", "file_reader", {"path": "deploys.ndjson"}),
            _final_answer_message(
                "checkout 的 500 错误自 10:00 起出现，与 checkout 1.2.0 在 10:00 "
                "发布的时间重合，判断故障与发布相关，建议先回滚。"
            ),
        ),
    ),
}


def build_example_llm(name: str) -> FakeLLM:
    """按示例名构造确定性 Fake LLM。"""

    scenario = SCENARIO_EXAMPLES[name]
    return FakeLLM(list(scenario.responses))


def run_scenario_example(name: str) -> AgentState:
    """运行一个确定性场景示例并返回终态 ``AgentState``。"""

    scenario = SCENARIO_EXAMPLES[name]
    llm = build_example_llm(name)
    agent = Agent(
        llm=llm,
        registry=build_registry(),
        max_steps=len(scenario.responses),
    )
    return agent.run(scenario.task)


__all__ = [
    "SCENARIO_EXAMPLES",
    "ScenarioExample",
    "build_example_llm",
    "run_scenario_example",
]
