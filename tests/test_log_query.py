"""R-07 日志查询工具（``LogQueryTool``）的公开行为测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from self_react.models import ToolCall, ToolErrorCode, ToolResultStatus
from self_react.tools import (
    LogQueryTool,
    Tool,
    ToolArgumentError,
    ToolExecutionError,
    ToolRegistry,
)

_SAMPLE_ROWS = [
    ("2026-08-12 10:00:00", "a", "ERROR", "500", "database timeout"),
    ("2026-08-12 10:05:00", "a", "ERROR", "500", "database timeout"),
    ("2026-08-12 10:10:00", "a", "INFO", None, "ok"),
    ("2026-08-12 11:00:00", "b", "ERROR", "503", "overload"),
    ("2026-08-12 11:05:00", "b", "WARN", None, "slow"),
]


def _sample_text() -> str:
    """把样例行序列化成 NDJSON 文本，保持代码行短于 88 字符。"""

    return (
        "\n".join(
            json.dumps(
                {
                    "timestamp": timestamp,
                    "service": service,
                    "level": level,
                    "error_code": error_code,
                    "message": message,
                },
                ensure_ascii=False,
            )
            for timestamp, service, level, error_code, message in _SAMPLE_ROWS
        )
        + "\n"
    )


def _registry(tmp_path: Path) -> tuple[ToolRegistry, LogQueryTool]:
    """在临时目录写一份样例日志，并返回注册表与工具。"""

    file = tmp_path / "logs.ndjson"
    file.write_text(_sample_text(), encoding="utf-8")
    tool = LogQueryTool(root_directory=tmp_path)
    registry = ToolRegistry()
    registry.register(tool)
    return registry, tool


def test_log_query_filters_by_service_and_error_code(tmp_path: Path) -> None:
    """按服务与错误码精确过滤，返回命中数与文件总数。"""

    registry, _ = _registry(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="log_query",
            arguments={"path": "logs.ndjson", "service": "a", "error_code": "500"},
        )
    )

    assert result.is_success is True
    assert result.status is ToolResultStatus.SUCCESS
    assert "匹配 2 条 / 共 5 条" in result.content
    assert result.content.count("error_code") == 2


def test_log_query_keyword_is_case_insensitive_substring(tmp_path: Path) -> None:
    """keyword 对 message 做大小写不敏感子串匹配。"""

    tool = _registry(tmp_path)[1]

    assert "匹配 2 条" in tool.execute({"path": "logs.ndjson", "keyword": "TIMEOUT"})


def test_log_query_time_range_is_inclusive(tmp_path: Path) -> None:
    """时间窗过滤是闭区间，按字符串字典序比较。"""

    tool = _registry(tmp_path)[1]

    output = tool.execute(
        {
            "path": "logs.ndjson",
            "time_start": "2026-08-12 10:00:00",
            "time_end": "2026-08-12 10:05:00",
        }
    )
    assert "匹配 2 条 / 共 5 条" in output


def test_log_query_group_by_error_code_sorts_descending(tmp_path: Path) -> None:
    """聚合计数按降序排列，平局按键升序。"""

    tool = _registry(tmp_path)[1]

    output = tool.execute(
        {"path": "logs.ndjson", "level": "ERROR", "group_by": "error_code"}
    )

    assert "匹配 3 条 / 共 5 条" in output
    assert output.index("500: 2") < output.index("503: 1")


def test_log_query_group_by_hour_buckets_timestamp(tmp_path: Path) -> None:
    """hour 维度把时间戳取整到整点桶。"""

    tool = _registry(tmp_path)[1]

    output = tool.execute(
        {"path": "logs.ndjson", "error_code": "503", "group_by": "hour"}
    )

    assert "匹配 1 条 / 共 5 条" in output
    assert "2026-08-12 11:00:00: 1" in output


def test_log_query_limit_truncates_matched_lines(tmp_path: Path) -> None:
    """limit 限制命中行数量，并在截断时附加稳定标记。"""

    tool = _registry(tmp_path)[1]

    output = tool.execute({"path": "logs.ndjson", "limit": 1})

    assert "匹配 5 条 / 共 5 条" in output
    assert "仅显示前 1 条" in output
    assert output.count("- ") == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": ""},
        {"path": "logs.ndjson", "group_by": "bogus"},
        {"path": "logs.ndjson", "time_start": "2026/08/12 10:00:00"},
        {"path": "logs.ndjson", "limit": 0},
        {"path": "logs.ndjson", "extra": "x"},
    ],
)
def test_log_query_rejects_invalid_arguments(
    arguments: dict[str, object],
    tmp_path: Path,
) -> None:
    """缺失、非法或多余参数在工具边界返回 INVALID_ARGUMENTS。"""

    registry, _ = _registry(tmp_path)

    result = registry.execute(
        ToolCall(call_id="call-1", name="log_query", arguments=arguments)
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True


@pytest.mark.parametrize("path", ["C:/outside.ndjson", "../outside.ndjson"])
def test_log_query_rejects_unsafe_path(path: str, tmp_path: Path) -> None:
    """绝对路径与越界路径在访问文件系统前被拒绝。"""

    registry, _ = _registry(tmp_path)

    result = registry.execute(
        ToolCall(call_id="call-1", name="log_query", arguments={"path": path})
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS


def test_log_query_missing_file_is_stable_execution_error(tmp_path: Path) -> None:
    """文件不存在返回可重试的 TOOL_EXECUTION_ERROR。"""

    registry, _ = _registry(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="log_query",
            arguments={"path": "missing.ndjson"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True
    assert "文件不存在" in result.error.message


def test_log_query_satisfies_tool_protocol(tmp_path: Path) -> None:
    """日志查询工具满足 Day 7 的 Tool 协议。"""

    tool = LogQueryTool(root_directory=tmp_path)

    assert isinstance(tool, Tool)
    assert tool.name == "log_query"
    assert tool.description.strip()
    assert tool.parameters["type"] == "object"


def test_log_query_argument_error_is_stable_at_tool_boundary(tmp_path: Path) -> None:
    """工具直接抛出的参数错误也是稳定异常。"""

    tool = LogQueryTool(root_directory=tmp_path)

    with pytest.raises(ToolArgumentError):
        tool.execute({})


def test_log_query_is_deterministic(tmp_path: Path) -> None:
    """相同输入连续调用两次返回完全相同的结果。"""

    tool = _registry(tmp_path)[1]

    first = tool.execute({"path": "logs.ndjson", "service": "a"})
    second = tool.execute({"path": "logs.ndjson", "service": "a"})

    assert first == second


def test_log_query_execution_error_is_stable_at_tool_boundary(tmp_path: Path) -> None:
    """根目录缺失时工具直接抛出可重试执行错误。"""

    tool = LogQueryTool(root_directory=tmp_path / "missing-dir")

    with pytest.raises(ToolExecutionError):
        tool.execute({"path": "logs.ndjson"})
