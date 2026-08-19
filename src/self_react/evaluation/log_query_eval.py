"""log_query 查询准确性评估集（roadmap 10.8 离线部分）。

用场景 ground truth 做参数组合评估：真实 fixture 实测 ``error_code=404`` 命中
736 条（全部集中在 03 点小时桶）、``03:14:00~03:18:59`` 窗口命中 733 条；
防回归用例覆盖 defense-in-depth 之前 ``keyword=404`` 这类召回率为 0 的旧用法
（现被硬校验稳定拒绝）。全部离线确定，进 pytest；默认使用场景注册表
（``build_registry``），也可注入自定义注册表。
"""

from __future__ import annotations

from dataclasses import dataclass

from self_react.models import ToolCall, ToolErrorCode
from self_react.scenarios.log_troubleshooting import build_registry
from self_react.tools import ToolRegistry


@dataclass(frozen=True)
class LogQueryCase:
    """一条 log_query 评估用例：名字、查询参数与期望结果。"""

    name: str
    arguments: dict[str, object]
    expect_substring: str | None = None
    expect_invalid_arguments: bool = False


LOG_QUERY_EVAL_CASES: tuple[LogQueryCase, ...] = (
    LogQueryCase(
        name="404-count",
        arguments={"path": "logs.ndjson", "error_code": "404"},
        expect_substring="匹配 736 条 / 共 931 条",
    ),
    LogQueryCase(
        name="404-hour-bucket",
        arguments={"path": "logs.ndjson", "error_code": "404", "group_by": "hour"},
        expect_substring="2021-12-17 03:00:00: 736",
    ),
    LogQueryCase(
        name="404-spike-window",
        arguments={
            "path": "logs.ndjson",
            "error_code": "404",
            "time_start": "2021-12-17 03:14:00",
            "time_end": "2021-12-17 03:18:59",
        },
        expect_substring="匹配 733 条",
    ),
    LogQueryCase(
        name="keyword-404-rejected",
        arguments={"path": "logs.ndjson", "keyword": "404"},
        expect_invalid_arguments=True,
    ),
)


@dataclass(frozen=True)
class LogQueryCaseResult:
    """一条评估用例的结果：是否通过与说明（断言内容或失败原因）。"""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class LogQueryEvalResult:
    """log_query 查询准确性评估结果。"""

    cases: tuple[LogQueryCaseResult, ...]
    passed_count: int

    @property
    def total(self) -> int:
        return len(self.cases)


def _run_case(registry: ToolRegistry, case: LogQueryCase) -> LogQueryCaseResult:
    """执行一条用例并返回通过与否及说明。"""

    call = ToolCall(call_id="eval", name="log_query", arguments=case.arguments)
    result = registry.execute(call)
    if case.expect_invalid_arguments:
        rejected = (
            result.is_success is False
            and result.error is not None
            and result.error.code is ToolErrorCode.INVALID_ARGUMENTS
        )
        detail = (
            f"{case.name}: keyword 全数字被硬校验拒绝"
            if rejected
            else f"{case.name}: 期望 INVALID_ARGUMENTS，实际未拒绝"
        )
        return LogQueryCaseResult(name=case.name, passed=rejected, detail=detail)
    assert case.expect_substring is not None
    if result.is_success and result.content is not None:
        matched = case.expect_substring in result.content
        detail = (
            f"{case.name}: 命中「{case.expect_substring}」"
            if matched
            else f"{case.name}: 输出不含「{case.expect_substring}」"
        )
        return LogQueryCaseResult(name=case.name, passed=matched, detail=detail)
    error_message = result.error.message if result.error is not None else "未知错误"
    return LogQueryCaseResult(
        name=case.name, passed=False, detail=f"{case.name}: 工具失败：{error_message}"
    )


def evaluate_log_query_accuracy(
    registry: ToolRegistry | None = None,
) -> LogQueryEvalResult:
    """运行全部 log_query 评估用例并汇总通过数。

    默认使用场景注册表（``build_registry``，含 defense-in-depth 硬校验）；
    传入自定义注册表可用于验证评估器本身如实报告失败。
    """

    used_registry = registry if registry is not None else build_registry()
    results = [_run_case(used_registry, case) for case in LOG_QUERY_EVAL_CASES]
    passed_count = sum(1 for result in results if result.passed)
    return LogQueryEvalResult(cases=tuple(results), passed_count=passed_count)


__all__ = [
    "LOG_QUERY_EVAL_CASES",
    "LogQueryCase",
    "LogQueryCaseResult",
    "LogQueryEvalResult",
    "evaluate_log_query_accuracy",
]
