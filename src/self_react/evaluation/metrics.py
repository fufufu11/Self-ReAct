"""评估指标的计算纯函数（roadmap 10.8 离线部分）。

只做指标数学：给定排序结果与标准命中，按标准 top-k 口径计算召回率 / 精确率 /
F1，以及跨查询的宏平均。不依赖工具、数据文件或真实模型，输入相同输出相同，
可进确定性自动化测试。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Metrics:
    """一组 (召回率, 精确率, F1) 指标，全部在 [0, 1] 区间。"""

    recall: float
    precision: float
    f1: float


def retrieval_metrics_at_k(
    ranked_ids: Sequence[str],
    relevant_ids: Sequence[str],
    k: int,
) -> Metrics:
    """按标准 top-k 口径计算单次检索的召回率 / 精确率 / F1。

    - ``recall@k = |relevant ∩ top-k| / |relevant|``，相关条目为空时记为 0；
    - ``precision@k = |relevant ∩ top-k| / k``，k 是请求档位，即使检索结果
      不足 k 条也按 k 作分母（标准 precision@k 口径）；
    - ``F1 = 2 * P * R / (P + R)``，P 与 R 都为 0 时记为 0（避免除零）。
    """

    if k < 1:
        raise ValueError("k 必须是正整数")
    relevant = set(relevant_ids)
    if not relevant:
        return Metrics(recall=0.0, precision=0.0, f1=0.0)
    hits = len(relevant & set(ranked_ids[:k]))
    recall = hits / len(relevant)
    precision = hits / k
    f1 = (
        0.0
        if recall + precision == 0.0
        else 2 * recall * precision / (recall + precision)
    )
    return Metrics(recall=recall, precision=precision, f1=f1)


def macro_average(metrics: Iterable[Metrics]) -> Metrics:
    """对一组查询的指标做宏平均（每条查询等权）。"""

    items = list(metrics)
    if not items:
        return Metrics(recall=0.0, precision=0.0, f1=0.0)
    count = len(items)
    return Metrics(
        recall=sum(item.recall for item in items) / count,
        precision=sum(item.precision for item in items) / count,
        f1=sum(item.f1 for item in items) / count,
    )


__all__ = ["Metrics", "macro_average", "retrieval_metrics_at_k"]
