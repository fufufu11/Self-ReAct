"""日志/故障排查场景的确定性端到端示例（R-07 场景 + R-08/R-09 真实日志）。

示例定义成数据：任务文本 + Fake LLM 预置响应，并复用 ``Agent`` 主循环与场景
注册表。三个示例分别覆盖“过滤 + 统计”、“聚合定位时间窗口”、“跨数据源发布关联”，
全部使用确定性工具与预置响应，不访问网络、不依赖真实 API Key。日志 fixture 来自
promjet.ru 2021-12 真实 Apache 访问日志（GitHub ``vberkutovv/ApacheLog-Dataset``，
MIT），三个示例围绕真实事件“2021-12-17 03:14-03:18 之间 733 条 404（整站备份/
源码文件探测，疑似外部扫描）”展开。
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
    "log-404-spike": ScenarioExample(
        name="log-404-spike",
        title="排查 404 突增",
        task=(
            "排查 promjet 网站 2021-12-17 凌晨的 404 突增，判断是外部扫描还是"
            "应用故障，给出根因假设与下一步动作。"
        ),
        responses=(
            _tool_call_message(
                "call-1",
                "log_query",
                {"path": "logs.ndjson", "error_code": "404"},
            ),
            _tool_call_message(
                "call-2",
                "log_query",
                {
                    "path": "logs.ndjson",
                    "error_code": "404",
                    "time_start": "2021-12-17 03:14:00",
                    "time_end": "2021-12-17 03:18:59",
                },
            ),
            _tool_call_message(
                "call-3", "calculator", {"expression": "733 / 736 * 100"}
            ),
            _tool_call_message(
                "call-4",
                "runbook_search",
                {"query": "404 突增 备份 源码探测"},
            ),
            _final_answer_message(
                "promjet 网站的 404 突增：736 条 404、占该小时日志约 79.1%，"
                "其中 733 条（约 99.6%）集中在 2021-12-17 03:14-03:18；路径为"
                "整站备份/源码文件探测（/promjet.ru.sql、/backup/root.rar、"
                "/tmp/root.tar.gz 等），判定为外部扫描而非应用故障，建议封禁"
                "来源 IP、检查备份/源码泄露，无需回滚发布。"
            ),
        ),
    ),
    "log-error-window": ScenarioExample(
        name="log-error-window",
        title="定位错误集中窗口",
        task="找出 404 错误集中出现的时间窗口。",
        responses=(
            _tool_call_message(
                "call-1",
                "log_query",
                {"path": "logs.ndjson", "error_code": "404", "group_by": "hour"},
            ),
            _tool_call_message(
                "call-2",
                "log_query",
                {
                    "path": "logs.ndjson",
                    "error_code": "404",
                    "time_start": "2021-12-17 03:14:00",
                    "time_end": "2021-12-17 03:18:59",
                },
            ),
            _final_answer_message(
                "404 共 736 条，全部集中在 2021-12-17 03 点小时桶；其中 733 条"
                "（约 99.6%）出现在 03:14-03:18，其余时段为 0。"
            ),
        ),
    ),
    "log-release-correlation": ScenarioExample(
        name="log-release-correlation",
        title="判断发布相关",
        task="判断 404 突增是否与最近发布相关。",
        responses=(
            _tool_call_message(
                "call-1",
                "log_query",
                {
                    "path": "logs.ndjson",
                    "error_code": "404",
                    "time_start": "2021-12-17 03:14:00",
                    "time_end": "2021-12-17 03:18:59",
                },
            ),
            _tool_call_message("call-2", "file_reader", {"path": "deploys.ndjson"}),
            _final_answer_message(
                "404 突增（736 条，733 条集中在 03:14-03:18）与最近发布 jet "
                "1.2.0（2021-12-16 22:00，演示 fixture）间隔约 5 小时且不重合；"
                "突增内容为备份/源码探测路径，判定与发布无关、疑似外部扫描，"
                "无需回滚。"
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
