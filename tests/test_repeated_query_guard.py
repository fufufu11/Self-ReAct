"""查询类工具护栏的公开行为测试（roadmap 10.9 起步，Issue #90 升级为「数有效次数」）。

测试只依赖 Fake LLM 与确定性工具，不访问网络、不调用真实 API。公开缝是
``Agent.run(task) -> AgentState``：查询/检索类工具（log_query / retrieve /
runbook_search）的护栏区分「有效」与「无效」——0 命中、参数与最近一次查询
完全相同的查询在分派前/执行后被拦下，作为 ``REPEATED_QUERY`` 失败观察回写
并引导模型直接 final_answer；有效查询累计达到阈值（默认 3）时在分派前兜底
拦截；``file_reader`` 读发布记录不打断有效查询计数与「最近一次查询」记忆，
``calculator`` 等非查询工具仍会清零；``repeated_query_limit`` 置 0 时关闭
该护栏。
"""

from __future__ import annotations

import json

from self_react.agent import Agent
from self_react.llm import FakeLLM
from self_react.models import (
    Message,
    MessageRole,
    TerminationReason,
    ToolErrorCode,
)
from self_react.tools import (
    CalculatorTool,
    RetrieveTool,
    RunbookSearchTool,
    ToolRegistry,
)


def _json_message(raw: str) -> Message:
    """构造一条把原始 JSON 放在 content 里的助手消息。"""

    return Message(role=MessageRole.ASSISTANT, content=raw)


def _final_answer_json(content: str) -> Message:
    """构造一条符合 Day 10 契约的最终回答原始输出。"""

    return _json_message(
        json.dumps({"kind": "final_answer", "content": content}, ensure_ascii=False)
    )


def _tool_call_json(
    call_id: str,
    name: str,
    arguments: dict[str, object],
) -> Message:
    """构造一条符合 Day 10 契约的工具调用原始输出。"""

    return _json_message(
        json.dumps(
            {
                "kind": "tool_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            },
            ensure_ascii=False,
        )
    )


def _retrieve_registry() -> ToolRegistry:
    """只注册检索工具的最小注册表。"""

    registry = ToolRegistry()
    registry.register(RetrieveTool())
    return registry


def _runbook_entry() -> dict[str, object]:
    """构造一条最小合法 runbook 条目。"""

    return {
        "id": "RB-404",
        "error_code": "404",
        "service": "web",
        "title": "404 突增排查",
        "causes": ["外部扫描"],
        "checks": ["核对错误码分布"],
        "actions": ["收敛为 final_answer"],
    }


def test_third_consecutive_query_is_intercepted_as_repeated_query() -> None:
    """第三次连续查询（不同参数）被拦截，回写 REPEATED_QUERY 收尾引导。"""

    llm = FakeLLM(
        [
            _tool_call_json("q1", "retrieve", {"query": "python"}),
            _tool_call_json("q2", "retrieve", {"query": "react"}),
            _tool_call_json("q3", "retrieve", {"query": "deepseek"}),
            _final_answer_json("我基于现有证据给出回答。"),
        ]
    )

    state = Agent(llm=llm, registry=_retrieve_registry(), max_steps=4).run(
        "检索多个主题"
    )

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    # 前两次连续查询正常执行
    assert state.trace[0].observation is not None
    assert state.trace[0].observation.is_error is False
    assert state.trace[1].observation is not None
    assert state.trace[1].observation.is_error is False
    # 第三次被拦截
    intercepted = state.trace[2].observation
    assert intercepted is not None
    assert intercepted.is_error is True
    assert intercepted.error_code is ToolErrorCode.REPEATED_QUERY
    assert intercepted.retryable is True
    assert "final_answer" in intercepted.content


def test_repeated_query_guard_skips_tool_execution() -> None:
    """第三次连续查询在分派前被拦截：查询工具不能被再次执行。"""

    class CountingRetrieve(RetrieveTool):
        """带调用计数，用来断言通报前的拦截没有触达工具层。"""

        def __init__(self) -> None:
            self.call_count = 0

        def execute(self, arguments: dict[str, object]) -> str:
            self.call_count += 1
            return super().execute(arguments)

    tool = CountingRetrieve()
    registry = ToolRegistry()
    registry.register(tool)
    llm = FakeLLM(
        [
            _tool_call_json("q1", "retrieve", {"query": "python"}),
            _tool_call_json("q2", "retrieve", {"query": "react"}),
            _tool_call_json("q3", "retrieve", {"query": "deepseek"}),
            _final_answer_json("已拦截第三次查询。"),
        ]
    )

    state = Agent(llm=llm, registry=registry, max_steps=4).run("检索多个主题")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert tool.call_count == 2
    assert state.trace[2].observation is not None
    assert state.trace[2].observation.error_code is ToolErrorCode.REPEATED_QUERY


def test_non_query_tool_resets_consecutive_count() -> None:
    """非查询工具（calculator）打断连续查询，后续查询重新从 1 计数。"""

    registry = ToolRegistry()
    registry.register(RetrieveTool())
    registry.register(CalculatorTool())
    llm = FakeLLM(
        [
            _tool_call_json("q1", "retrieve", {"query": "python"}),
            _tool_call_json("q2", "retrieve", {"query": "react"}),
            _tool_call_json("c1", "calculator", {"expression": "1 + 1"}),
            _tool_call_json("q4", "retrieve", {"query": "deepseek"}),
            _final_answer_json("完成。"),
        ]
    )

    state = Agent(llm=llm, registry=registry, max_steps=5).run("综合任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    # 第 4 步 retrieve 是 calculator 打断后的第一次查询，应正常执行
    assert state.trace[3].observation is not None
    assert state.trace[3].observation.is_error is False


def test_repeated_query_limit_zero_disables_guard() -> None:
    """repeated_query_limit=0 关闭该护栏，连续查询不被拦截。"""

    llm = FakeLLM(
        [
            _tool_call_json("q1", "retrieve", {"query": "python"}),
            _tool_call_json("q2", "retrieve", {"query": "react"}),
            _tool_call_json("q3", "retrieve", {"query": "deepseek"}),
            _final_answer_json("完成。"),
        ]
    )

    state = Agent(
        llm=llm,
        registry=_retrieve_registry(),
        max_steps=4,
        repeated_query_limit=0,
    ).run("检索多个主题")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert all(
        step.observation is not None and step.observation.is_error is False
        for step in state.trace[:3]
    )


def test_mixed_query_tools_accumulate_together() -> None:
    """不同查询/检索类工具（retrieve + runbook_search）连调也累计。"""

    registry = ToolRegistry()
    registry.register(RetrieveTool())
    registry.register(RunbookSearchTool(entries=[_runbook_entry()]))
    llm = FakeLLM(
        [
            _tool_call_json("q1", "retrieve", {"query": "python"}),
            _tool_call_json("q2", "runbook_search", {"query": "404"}),
            _tool_call_json("q3", "retrieve", {"query": "react"}),
            _final_answer_json("完成。"),
        ]
    )

    state = Agent(llm=llm, registry=registry, max_steps=4).run("综合检索")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    intercepted = state.trace[2].observation
    assert intercepted is not None
    assert intercepted.error_code is ToolErrorCode.REPEATED_QUERY


class _FakeFileReader:
    """name 为 ``file_reader`` 的最小只读工具，测护栏按名字区分证据读取。"""

    name = "file_reader"
    description = "读取发布记录等证据文件"

    def execute(self, arguments: dict[str, object]) -> str:
        return "（发布记录内容）"


def test_zero_hit_query_is_intercepted() -> None:
    """命中 0 条的查询被拦下，回写 REPEATED_QUERY 收尾引导，不当作有效步骤。"""

    registry = ToolRegistry()
    registry.register(RunbookSearchTool(entries=[_runbook_entry()]))
    llm = FakeLLM(
        [
            _tool_call_json("q1", "runbook_search", {"query": "xyzzy 不存在的主题"}),
            _final_answer_json("0 命中，无法定位，基于现有证据给出结论。"),
        ]
    )

    state = Agent(llm=llm, registry=registry, max_steps=2).run("检索无结果")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    intercepted = state.trace[0].observation
    assert intercepted is not None
    assert intercepted.is_error is True
    assert intercepted.error_code is ToolErrorCode.REPEATED_QUERY
    assert intercepted.retryable is True
    assert "final_answer" in intercepted.content


def test_repeated_query_arguments_across_file_reader_is_intercepted() -> None:
    """跨 file_reader 的相同参数查询仍被判为无效，回写 REPEATED_QUERY。"""

    registry = ToolRegistry()
    registry.register(RetrieveTool())
    registry.register(_FakeFileReader())
    llm = FakeLLM(
        [
            _tool_call_json("q1", "retrieve", {"query": "react"}),
            _tool_call_json("f1", "file_reader", {"path": "deploys.ndjson"}),
            _tool_call_json("q2", "retrieve", {"query": "react"}),
            _final_answer_json("重复查询，直接回答。"),
        ]
    )

    state = Agent(llm=llm, registry=registry, max_steps=4).run("检索")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    intercepted = state.trace[2].observation
    assert intercepted is not None
    assert intercepted.error_code is ToolErrorCode.REPEATED_QUERY
    assert "final_answer" in intercepted.content


def test_file_reader_does_not_break_effective_query_count() -> None:
    """file_reader 不打断有效查询计数，跨它仍累计到阈值并兜底拦截。"""

    registry = ToolRegistry()
    registry.register(RetrieveTool())
    registry.register(_FakeFileReader())
    llm = FakeLLM(
        [
            _tool_call_json("q1", "retrieve", {"query": "python"}),
            _tool_call_json("f1", "file_reader", {"path": "deploys.ndjson"}),
            _tool_call_json("q2", "retrieve", {"query": "react"}),
            _tool_call_json("q3", "retrieve", {"query": "deepseek"}),
            _final_answer_json("完成。"),
        ]
    )

    state = Agent(llm=llm, registry=registry, max_steps=5).run("综合任务")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    # q1、q2 两次有效查询后，q3 是第 3 次有效查询，分派前被拦
    intercepted = state.trace[3].observation
    assert intercepted is not None
    assert intercepted.error_code is ToolErrorCode.REPEATED_QUERY
