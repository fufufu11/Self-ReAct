"""R-07 runbook 检索工具（``RunbookSearchTool``）的公开行为测试。"""

from __future__ import annotations

import pytest

from self_react.models import ToolCall, ToolErrorCode
from self_react.tools import (
    RunbookEntry,
    RunbookSearchTool,
    Tool,
    ToolArgumentError,
    ToolRegistry,
)
from self_react.tools.runbook_search import tokenize


def _entry(
    entry_id: str,
    error_code: str = "500",
    service: str = "checkout",
    title: str | None = None,
) -> RunbookEntry:
    """构造一条最小 runbook 条目。"""

    return RunbookEntry(
        id=entry_id,
        error_code=error_code,
        service=service,
        title=title or f"checkout 服务 {error_code} 错误排查",
        causes=["数据库连接池耗尽"],
        checks=["查看连接池使用率"],
        actions=["扩容连接池"],
    )


def test_tokenize_splits_ascii_and_chinese_bigrams() -> None:
    """ASCII/数字段成词，中文切成 bigram 加单字兜底。"""

    assert tokenize("checkout 5xx") == ["checkout", "5xx"]
    assert tokenize("服务") == ["服务", "服", "务"]
    assert "服务" in tokenize("服务可用")
    assert "可用" in tokenize("服务可用")


def test_runbook_search_returns_unique_match() -> None:
    """唯一命中的查询只返回对应条目。"""

    tool = RunbookSearchTool(entries=[_entry("a"), _entry("b", error_code="503")])

    results = tool.search("503")

    assert [entry.id for entry in results] == ["b"]


def test_runbook_search_ties_break_by_id() -> None:
    """分数相同时按条目 id 字典序兜底，保证确定性。"""

    tool = RunbookSearchTool(
        entries=[_entry("b"), _entry("a"), _entry("c")],
    )

    results = tool.search("checkout 500")

    assert [entry.id for entry in results] == ["a", "b", "c"]


def test_runbook_search_executes_via_registry() -> None:
    """工具通过注册表执行，返回稳定文本并携带命中数。"""

    tool = RunbookSearchTool(entries=[_entry("a")])
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute(
        ToolCall(call_id="call-1", name="runbook_search", arguments={"query": "500"})
    )

    assert result.is_success is True
    assert "命中 1 条" in result.content
    assert "RB-500" not in result.content
    assert "--- a ---" in result.content
    assert "500" in result.content


def test_runbook_search_unknown_query_returns_zero_hits() -> None:
    """无命中查询返回稳定成功文本，而不是错误。"""

    tool = RunbookSearchTool(entries=[_entry("a")])

    output = tool.execute({"query": "完全不存在的关键词"})

    assert output == "命中 0 条：没有找到相关 runbook 条目。"


@pytest.mark.parametrize("arguments", [{}, {"query": ""}, {"query": "500", "top_k": 9}])
def test_runbook_search_rejects_invalid_arguments(
    arguments: dict[str, object],
) -> None:
    """缺失、空白 query 或非法 top_k 返回 INVALID_ARGUMENTS。"""

    tool = RunbookSearchTool(entries=[_entry("a")])
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute(
        ToolCall(call_id="call-1", name="runbook_search", arguments=arguments)
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS


def test_runbook_search_rejects_duplicate_ids() -> None:
    """重复 id 在构造索引时被拒绝。"""

    with pytest.raises(ValueError):
        RunbookSearchTool(entries=[_entry("a"), _entry("a")])


def test_runbook_search_coerces_dict_entries() -> None:
    """字典条目会被转换成 RunbookEntry。"""

    tool = RunbookSearchTool(
        entries=[
            {
                "id": "a",
                "error_code": "500",
                "service": "checkout",
                "title": "checkout 500",
                "causes": ["连接池耗尽"],
                "checks": ["查看连接池"],
                "actions": ["扩容"],
            }
        ]
    )

    assert [entry.id for entry in tool.search("500")] == ["a"]


def test_runbook_search_satisfies_tool_protocol() -> None:
    """检索工具满足 Tool 协议并声明参数 Schema。"""

    tool = RunbookSearchTool(entries=[_entry("a")])

    assert isinstance(tool, Tool)
    assert tool.name == "runbook_search"
    assert tool.description.strip()
    assert tool.parameters["type"] == "object"


def test_runbook_search_argument_error_is_stable() -> None:
    """工具直接抛出的参数错误也是稳定异常。"""

    tool = RunbookSearchTool(entries=[_entry("a")])

    with pytest.raises(ToolArgumentError):
        tool.execute({})
