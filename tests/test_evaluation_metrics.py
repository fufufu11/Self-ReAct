"""roadmap 10.8 离线评估集与效果指标的公开行为测试。

覆盖三块：
- 指标计算纯函数（``retrieval_metrics_at_k`` / ``macro_average``）的公式口径；
- runbook 检索质量评估（``evaluate_runbook_search``）：合成扩展语料的确定性、
  边界与回归基线，真实 fixture 冒烟；
- log_query 查询准确性评估（``evaluate_log_query_accuracy``）：场景 ground
  truth 参数组合全部通过。

真实模型端到端评估是非确定性手动验收记录，不在本文件内自动化。
"""

from __future__ import annotations

import pytest

from self_react.evaluation import (
    LOG_QUERY_EVAL_CASES,
    RUNBOOK_EVAL_ENTRIES,
    RUNBOOK_EVAL_QUERIES,
    evaluate_log_query_accuracy,
    evaluate_runbook_search,
)
from self_react.evaluation.metrics import (
    Metrics,
    macro_average,
    retrieval_metrics_at_k,
)
from self_react.scenarios.log_troubleshooting import build_registry

# ---------------------------------------------------------------------------
# 一、指标计算纯函数（公式口径）
# ---------------------------------------------------------------------------


def test_retrieval_metrics_single_relevant_hit() -> None:
    """top-1 恰好命中唯一相关条目：召回率/精确率/F1 全为 1。"""

    metrics = retrieval_metrics_at_k(
        ranked_ids=["a", "b", "c"], relevant_ids=["a"], k=1
    )

    assert metrics == Metrics(recall=1.0, precision=1.0, f1=1.0)


def test_retrieval_metrics_partial_recall_and_precision() -> None:
    """两个相关条目只命中一个：召回率 1/2，精确率按档位分母计算。"""

    metrics = retrieval_metrics_at_k(
        ranked_ids=["a", "b", "c"], relevant_ids=["a", "c"], k=1
    )

    assert metrics.recall == pytest.approx(0.5)
    assert metrics.precision == pytest.approx(1.0)
    assert metrics.f1 == pytest.approx(2 * 0.5 * 1.0 / (0.5 + 1.0))


def test_retrieval_metrics_full_hit_at_k3() -> None:
    """top-3 命中两个相关条目：召回率 1.0、精确率 2/3、F1 0.8。"""

    metrics = retrieval_metrics_at_k(
        ranked_ids=["a", "b", "c"], relevant_ids=["a", "c"], k=3
    )

    assert metrics.recall == pytest.approx(1.0)
    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.f1 == pytest.approx(0.8)


def test_retrieval_metrics_zero_hit() -> None:
    """没有命中：三项指标全为 0，F1 不产生除零。"""

    metrics = retrieval_metrics_at_k(
        ranked_ids=["a", "b", "c"], relevant_ids=["x"], k=3
    )

    assert metrics == Metrics(recall=0.0, precision=0.0, f1=0.0)


def test_retrieval_metrics_k_larger_than_ranked() -> None:
    """k 超过检索结果长度时，精确率分母仍按请求档位 k（标准 precision@k）。"""

    metrics = retrieval_metrics_at_k(
        ranked_ids=["a", "b", "c"], relevant_ids=["a"], k=5
    )

    assert metrics.recall == pytest.approx(1.0)
    assert metrics.precision == pytest.approx(0.2)


def test_retrieval_metrics_empty_relevant_is_zero() -> None:
    """相关条目为空时记为全 0，避免除零。"""

    metrics = retrieval_metrics_at_k(ranked_ids=["a"], relevant_ids=[], k=3)

    assert metrics == Metrics(recall=0.0, precision=0.0, f1=0.0)


def test_retrieval_metrics_rejects_non_positive_k() -> None:
    """k 必须为正整数。"""

    with pytest.raises(ValueError):
        retrieval_metrics_at_k(ranked_ids=["a"], relevant_ids=["a"], k=0)


def test_macro_average_weights_queries_equally() -> None:
    """宏平均对每条查询等权：F1 1.0 与 0.8 平均为 0.9。"""

    average = macro_average(
        [
            Metrics(recall=1.0, precision=1.0, f1=1.0),
            Metrics(recall=0.5, precision=1.0, f1=2 / 3),
        ]
    )

    assert average.recall == pytest.approx(0.75)
    assert average.precision == pytest.approx(1.0)
    assert average.f1 == pytest.approx((1.0 + 2 / 3) / 2)


def test_macro_average_empty_is_zero() -> None:
    """没有查询时宏平均为全 0。"""

    assert macro_average([]) == Metrics(recall=0.0, precision=0.0, f1=0.0)


# ---------------------------------------------------------------------------
# 二、runbook 检索质量评估（合成语料）
# ---------------------------------------------------------------------------


def test_runbook_eval_set_is_well_formed() -> None:
    """评估集常量满足设计约束：语料规模、id 唯一、相关条目都在语料内。"""

    ids = [entry["id"] for entry in RUNBOOK_EVAL_ENTRIES]
    assert len(ids) == len(set(ids))
    assert 12 <= len(ids) <= 15
    assert 10 <= len(RUNBOOK_EVAL_QUERIES) <= 12
    for item in RUNBOOK_EVAL_QUERIES:
        assert item["query"].strip()
        assert item["relevant"], item["query"]
        for entry_id in item["relevant"]:
            assert entry_id in ids, (item["query"], entry_id)


def test_runbook_eval_is_deterministic() -> None:
    """相同语料与查询集两次评估产生完全相同的指标（BM25 确定性）。"""

    first = evaluate_runbook_search()
    second = evaluate_runbook_search()

    assert first == second


def test_runbook_eval_reports_three_top_k_levels() -> None:
    """按 roadmap 口径报告 top_k ∈ {1, 3, 5} 三档指标。"""

    result = evaluate_runbook_search()

    assert set(result.per_k) == {1, 3, 5}
    for metrics in result.per_k.values():
        assert 0.0 <= metrics.recall <= 1.0
        assert 0.0 <= metrics.precision <= 1.0
        assert 0.0 <= metrics.f1 <= 1.0


def test_runbook_eval_recall_is_non_decreasing_in_k() -> None:
    """宏平均召回率随 top_k 增大不下降（评估集有区分度的必要条件）。"""

    result = evaluate_runbook_search()

    assert result.per_k[1].recall <= result.per_k[3].recall
    assert result.per_k[3].recall <= result.per_k[5].recall


def test_runbook_eval_recall_strictly_improves_between_k1_and_k3() -> None:
    """双相关查询让 top-1 召回率低于 1、top-3 全命中：评估集有区分度。

    标准 top-k 口径下精确率随 k 增大而下降（相关条目数远小于档位），F1 并不
    随 k 单调；区分度的体现是召回率严格上升而非 F1 上升。
    """

    result = evaluate_runbook_search()

    assert result.per_k[1].recall < result.per_k[3].recall
    assert result.per_k[3].recall == pytest.approx(1.0)


def test_runbook_eval_macro_recall_at_k5_is_full() -> None:
    """top-5 覆盖语料规模（14 条）内全部相关条目：宏平均召回率为 1.0。"""

    result = evaluate_runbook_search()

    assert result.per_k[5].recall == pytest.approx(1.0)


def test_runbook_eval_reports_per_query_ranking() -> None:
    """每条查询都记录相关条目与 top-5 排序结果，供报告逐条展示。"""

    result = evaluate_runbook_search()

    assert len(result.queries) == len(RUNBOOK_EVAL_QUERIES)
    for query_result in result.queries:
        assert query_result.ranked
        assert set(query_result.per_k) == {1, 3, 5}


def test_runbook_eval_macro_metrics_regression_baseline() -> None:
    """宏平均指标回归基线（BM25 首跑实测值，锁定防漂移）。

    标准 top-k 口径的固有形态：召回率随 k 单调上升（双相关查询让 top-1 召回
    率 < 1），精确率随 k 下降，F1 在 top-1 最高。
    """

    result = evaluate_runbook_search()

    assert result.per_k[1].recall == pytest.approx(0.9167, abs=1e-4)
    assert result.per_k[1].precision == pytest.approx(1.0)
    assert result.per_k[1].f1 == pytest.approx(0.9444, abs=1e-4)
    assert result.per_k[3].recall == pytest.approx(1.0)
    assert result.per_k[3].precision == pytest.approx(0.3889, abs=1e-4)
    assert result.per_k[3].f1 == pytest.approx(0.55)
    assert result.per_k[5].recall == pytest.approx(1.0)
    assert result.per_k[5].precision == pytest.approx(0.2333, abs=1e-4)
    assert result.per_k[5].f1 == pytest.approx(0.3730, abs=1e-4)


def test_runbook_eval_key_query_503_hits_relevant_entry_first() -> None:
    """代表性查询「503 服务不可用」在 top-1 就命中相关条目。"""

    result = evaluate_runbook_search()

    query_result = next(
        item for item in result.queries if item.query == "503 服务不可用"
    )
    assert query_result.relevant[0] in query_result.ranked[:1]


def test_runbook_eval_multi_relevant_query_finds_both() -> None:
    """双相关查询「404 突增 备份 源码探测」在 top-5 内同时命中两个相关条目。"""

    result = evaluate_runbook_search()

    query_result = next(
        item for item in result.queries if item.query == "404 突增 备份 源码探测"
    )
    for entry_id in query_result.relevant:
        assert entry_id in query_result.ranked


def test_runbook_eval_real_fixture_smoke() -> None:
    """真实 3 条 fixture 冒烟：代表性查询确定性命中 RB-404，且两次一致。"""

    registry = build_registry()
    tool = registry.get("runbook_search")
    assert tool is not None

    first = tool.execute({"query": "404 突增 备份 源码探测"})
    second = tool.execute({"query": "404 突增 备份 源码探测"})

    assert first == second
    assert "--- RB-404 ---" in first


# ---------------------------------------------------------------------------
# 三、log_query 查询准确性评估（场景 ground truth）
# ---------------------------------------------------------------------------


def test_log_query_eval_cases_cover_ground_truth_and_regression() -> None:
    """评估用例覆盖 736 / 733 / 小时桶与 keyword 防回归四组。"""

    assert len(LOG_QUERY_EVAL_CASES) == 4
    names = [case.name for case in LOG_QUERY_EVAL_CASES]
    assert names == [
        "404-count",
        "404-hour-bucket",
        "404-spike-window",
        "keyword-404-rejected",
    ]


def test_log_query_eval_all_cases_pass() -> None:
    """场景注册表下全部评估用例通过（离线确定性）。"""

    result = evaluate_log_query_accuracy()

    assert result.passed_count == result.total == 4
    for case_result in result.cases:
        assert case_result.passed, case_result.detail


def test_log_query_eval_details_include_ground_truth_numbers() -> None:
    """评估明细包含 736 / 733 与小时桶峰值等 ground truth 数字。"""

    result = evaluate_log_query_accuracy()

    detail = "\n".join(case_result.detail for case_result in result.cases)
    assert "736" in detail
    assert "733" in detail
    assert "03:00:00" in detail


def test_log_query_eval_reports_failure_when_ground_truth_moves() -> None:
    """用空注册表评估时用例失败并被如实报告（评估器不掩盖失败）。"""

    from self_react.tools import ToolRegistry

    result = evaluate_log_query_accuracy(registry=ToolRegistry())

    assert result.passed_count == 0
    assert all(not case_result.passed for case_result in result.cases)
