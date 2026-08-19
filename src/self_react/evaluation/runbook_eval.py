"""runbook 检索质量评估集（roadmap 10.8 离线部分）。

评估对象是 ``RunbookSearchTool`` 的确定性 BM25 检索：用一组**合成扩展语料**
（``RUNBOOK_EVAL_ENTRIES``，覆盖 404/403/503/502/504/429/500/超时/慢查询/
磁盘/内存/配置/发布/扫描等主题，风格与真实 fixture 一致）与**固定查询集**
（``RUNBOOK_EVAL_QUERIES``，每查询给出标准命中，多数 1 个相关条目、部分
2 个相关条目以让指标有区分度），按标准 top-k 口径统计宏平均召回率 / 精确率 /
F1。相同输入永远产生相同指标，可进自动化测试；真实 3 条 fixture 只做冒烟
断言，不修改任何数据文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from self_react.evaluation.metrics import (
    Metrics,
    macro_average,
    retrieval_metrics_at_k,
)
from self_react.tools import RunbookEntry, RunbookSearchTool

RUNBOOK_EVAL_TOP_KS: tuple[int, ...] = (1, 3, 5)
"""按 roadmap 10.8 口径报告的 top_k 档位。"""

RUNBOOK_EVAL_ENTRIES: list[dict[str, object]] = [
    {
        "id": "RB-404",
        "error_code": "404",
        "service": "web",
        "title": "网站 404 突增排查（备份/源码探测）",
        "causes": [
            "整站备份或源码文件被扫描探测",
            "失效或外部旧链接",
            "爬虫抓取不存在路径",
        ],
        "checks": [
            "按路径聚合确认是否备份/源码文件名",
            "看错误是否短时集中出现",
            "对比发布窗口",
        ],
        "actions": [
            "封禁来源 IP 并加固访问控制",
            "移除或保护备份/源码文件",
            "修复失效链接",
        ],
    },
    {
        "id": "RB-403",
        "error_code": "403",
        "service": "web",
        "title": "网站 403 访问被拒排查",
        "causes": ["目录或文件权限配置错误", "WAF 或限流规则误伤"],
        "checks": ["查看 403 集中在哪些路径与来源", "核对权限与 WAF 规则"],
        "actions": ["修正目录权限", "调整 WAF 或限流规则"],
    },
    {
        "id": "RB-503",
        "error_code": "503",
        "service": "web",
        "title": "网站 503 服务不可用排查",
        "causes": ["后端或上游服务超时", "过载或维护窗口"],
        "checks": ["核对 503 时间与监控/维护记录", "检查后端与上游可用性"],
        "actions": ["扩容或限流", "联系上游并恢复服务"],
    },
    {
        "id": "RB-502",
        "error_code": "502",
        "service": "web",
        "title": "网站 502 网关错误排查（Bad Gateway）",
        "causes": ["反向代理到上游连接失败", "上游进程崩溃或未启动"],
        "checks": ["查看上游进程与端口健康", "核对代理配置与上游地址"],
        "actions": ["重启或拉起上游进程", "修正代理配置"],
    },
    {
        "id": "RB-504",
        "error_code": "504",
        "service": "web",
        "title": "网站 504 网关超时排查",
        "causes": ["上游响应超过网关超时阈值", "慢查询或大响应阻塞上游"],
        "checks": ["核对上游响应耗时", "检查数据库慢查询"],
        "actions": ["调大网关超时或优化上游", "处理慢查询"],
    },
    {
        "id": "RB-429",
        "error_code": "429",
        "service": "web",
        "title": "网站 429 请求限流排查",
        "causes": ["触发 WAF 或网关限流策略", "客户端请求频率过高"],
        "checks": ["核对限流策略与阈值", "查看来源 IP 分布"],
        "actions": ["调整限流阈值或白名单", "通知客户端降频"],
    },
    {
        "id": "RB-500",
        "error_code": "500",
        "service": "app",
        "title": "应用 500 内部错误排查",
        "causes": ["代码未捕获异常", "依赖服务返回异常数据"],
        "checks": ["查看应用日志堆栈", "核对依赖服务状态"],
        "actions": ["修复并发布代码", "降级或熔断依赖"],
    },
    {
        "id": "RB-CONN",
        "error_code": "500",
        "service": "mysql",
        "title": "数据库连接池耗尽排查",
        "causes": ["连接未释放导致泄漏", "并发超过连接池上限"],
        "checks": ["查看连接池使用率", "检查慢查询占用连接"],
        "actions": ["扩容连接池或调大上限", "修复连接泄漏并优化慢查询"],
    },
    {
        "id": "RB-SLOW",
        "error_code": "500",
        "service": "mysql",
        "title": "数据库慢查询排查",
        "causes": ["缺失索引导致全表扫描", "数据量增长查询计划变差"],
        "checks": ["定位慢查询 SQL", "查看执行计划与索引"],
        "actions": ["补充索引", "改写 SQL 或归档历史数据"],
    },
    {
        "id": "RB-DISK",
        "error_code": "500",
        "service": "app",
        "title": "磁盘写满导致服务异常排查",
        "causes": ["日志或临时文件无限增长", "数据文件膨胀"],
        "checks": ["查看磁盘使用率与增长趋势", "定位占用大的目录"],
        "actions": ["清理日志并配置轮转", "扩容磁盘或迁移数据"],
    },
    {
        "id": "RB-MEM",
        "error_code": "500",
        "service": "app",
        "title": "内存泄漏与 OOM 排查",
        "causes": ["缓存无上限增长", "对象未释放"],
        "checks": ["查看内存占用趋势", "检查堆转储"],
        "actions": ["限制缓存大小", "修复泄漏并扩容内存"],
    },
    {
        "id": "RB-CONFIG",
        "error_code": "5xx",
        "service": "web",
        "title": "配置错误导致 5xx 排查",
        "causes": ["发布变更带入错误配置", "环境变量或密钥缺失"],
        "checks": ["对比最近发布变更", "核对配置与密钥"],
        "actions": ["回滚配置或修正", "补齐缺失密钥"],
    },
    {
        "id": "RB-DEPLOY",
        "error_code": "500",
        "service": "web",
        "title": "发布关联排查（发布后错误突增）",
        "causes": ["发布配置或代码缺陷", "依赖服务未同步发布"],
        "checks": ["对比错误时间与发布窗口", "检查发布回滚预案"],
        "actions": ["回滚或热修复", "验证依赖服务版本兼容"],
    },
    {
        "id": "RB-SCAN",
        "error_code": "404",
        "service": "web",
        "title": "外部扫描与备份探测识别",
        "causes": ["攻击者枚举备份/源码文件", "扫描器探测常见路径"],
        "checks": ["按路径聚合确认备份/源码文件名", "核对来源 IP 集中度"],
        "actions": ["封禁来源 IP", "移除或保护备份/源码文件"],
    },
]

RUNBOOK_EVAL_QUERIES: list[dict[str, object]] = [
    {"query": "404 突增 备份 源码探测", "relevant": ["RB-404", "RB-SCAN"]},
    {"query": "503 服务不可用", "relevant": ["RB-503"]},
    {"query": "发布关联排查", "relevant": ["RB-DEPLOY"]},
    {"query": "403 访问被拒", "relevant": ["RB-403"]},
    {"query": "502 网关错误", "relevant": ["RB-502"]},
    {"query": "连接池耗尽 慢查询", "relevant": ["RB-CONN", "RB-SLOW"]},
    {"query": "磁盘写满", "relevant": ["RB-DISK"]},
    {"query": "内存泄漏 OOM", "relevant": ["RB-MEM"]},
    {"query": "429 请求限流", "relevant": ["RB-429"]},
    {"query": "504 网关超时", "relevant": ["RB-504"]},
    {"query": "配置错误导致 5xx", "relevant": ["RB-CONFIG"]},
    {"query": "500 内部错误", "relevant": ["RB-500"]},
]


@dataclass(frozen=True)
class QueryResult:
    """一条查询的评估结果：标准命中、top-5 排序与各档位指标。"""

    query: str
    relevant: tuple[str, ...]
    ranked: tuple[str, ...]
    per_k: dict[int, Metrics] = field(default_factory=dict)


@dataclass(frozen=True)
class RunbookMetricsResult:
    """runbook 检索质量评估结果：按 top_k 聚合的宏平均 + 逐查询明细。"""

    per_k: dict[int, Metrics] = field(default_factory=dict)
    queries: tuple[QueryResult, ...] = ()


def evaluate_runbook_search() -> RunbookMetricsResult:
    """对合成扩展语料跑固定查询集，返回三档 top-k 宏平均指标与逐查询明细。

    相同输入永远返回相同结果（BM25 + 固定语料，确定性），可进自动化测试。
    """

    tool = RunbookSearchTool(
        entries=[RunbookEntry.model_validate(entry) for entry in RUNBOOK_EVAL_ENTRIES]
    )
    query_results: list[QueryResult] = []
    for item in RUNBOOK_EVAL_QUERIES:
        query = str(item["query"])
        relevant = tuple(str(entry_id) for entry_id in item["relevant"])
        ranked = tuple(
            entry.id for entry in tool.search(query, max(RUNBOOK_EVAL_TOP_KS))
        )
        per_k = {
            k: retrieval_metrics_at_k(ranked, relevant, k) for k in RUNBOOK_EVAL_TOP_KS
        }
        query_results.append(
            QueryResult(query=query, relevant=relevant, ranked=ranked, per_k=per_k)
        )
    per_k = {
        k: macro_average(query_result.per_k[k] for query_result in query_results)
        for k in RUNBOOK_EVAL_TOP_KS
    }
    return RunbookMetricsResult(per_k=per_k, queries=tuple(query_results))


__all__ = [
    "QueryResult",
    "RUNBOOK_EVAL_ENTRIES",
    "RUNBOOK_EVAL_QUERIES",
    "RUNBOOK_EVAL_TOP_KS",
    "RunbookMetricsResult",
    "evaluate_runbook_search",
]
