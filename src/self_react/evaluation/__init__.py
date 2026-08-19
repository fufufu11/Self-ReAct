"""评估集与效果指标（roadmap 10.8 离线部分）。

对外暴露两类离线评估能力与评估集常量，供报告、演示与 pytest 复用：

- ``evaluate_runbook_search``：runbook 检索质量——合成扩展语料 + 固定查询集，
  top_k ∈ {1, 3, 5} 的宏平均召回率 / 精确率 / F1（确定性）；
- ``evaluate_log_query_accuracy``：log_query 查询准确性——场景 ground truth
  参数组合（736 / 733 / 小时桶 / keyword 防回归），全部离线确定。

真实模型端到端评估（任务成功率 / 收敛率 / 平均步数 / 平均耗时 / 关键数字
准确率）是非确定性手动验收记录，驱动脚本不入库，结果记录进架构导读。
"""

from self_react.evaluation.log_query_eval import (
    LOG_QUERY_EVAL_CASES,
    LogQueryCase,
    LogQueryCaseResult,
    LogQueryEvalResult,
    evaluate_log_query_accuracy,
)
from self_react.evaluation.metrics import (
    Metrics,
    macro_average,
    retrieval_metrics_at_k,
)
from self_react.evaluation.runbook_eval import (
    RUNBOOK_EVAL_ENTRIES,
    RUNBOOK_EVAL_QUERIES,
    RUNBOOK_EVAL_TOP_KS,
    QueryResult,
    RunbookMetricsResult,
    evaluate_runbook_search,
)

__all__ = [
    "LOG_QUERY_EVAL_CASES",
    "RUNBOOK_EVAL_ENTRIES",
    "RUNBOOK_EVAL_QUERIES",
    "RUNBOOK_EVAL_TOP_KS",
    "LogQueryCase",
    "LogQueryCaseResult",
    "LogQueryEvalResult",
    "Metrics",
    "QueryResult",
    "RunbookMetricsResult",
    "evaluate_log_query_accuracy",
    "evaluate_runbook_search",
    "macro_average",
    "retrieval_metrics_at_k",
]
