"""确定性知识检索业务工具（Day 9）。

``RetrieveTool`` 从模块内固定知识库中按主题返回确定性说明：相同输入得到
完全相同的结果，不访问网络、不依赖真实 API。查询会统一大小写并折叠空白；
未知主题返回稳定执行错误，绝不伪装成功。

参数校验失败抛 ``ToolArgumentError``（注册表转 ``INVALID_ARGUMENTS``）；
输入合规但查不到条目抛 ``ToolExecutionError``（注册表转
``TOOL_EXECUTION_ERROR``），并允许模型换个说法重试。
"""

from __future__ import annotations

import re

from self_react.models import JsonObject
from self_react.tools.base import ToolArgumentError, ToolExecutionError

MAX_QUERY_LENGTH = 200
"""query 参数的最大字符数，防止超长输入。"""

KNOWLEDGE_BASE: dict[str, str] = {
    "react": (
        "ReAct（Reason + Act）是一种让模型推理与行动交错的智能体范式，"
        "由 Yao 等人在 2022 年提出：模型先用推理规划，再执行动作获取新信息。"
    ),
    "python": (
        "Python 是一种解释型、动态类型的通用编程语言，"
        "本项目使用 Python 3.11+ 实现 ReAct 框架。"
    ),
    "deepseek": (
        "DeepSeek 提供与 OpenAI Chat Completions 兼容的 API，"
        "本项目通过 DeepSeekLLM 适配器调用它。"
    ),
    "uv": (
        "uv 是 Python 的包管理与虚拟环境工具，"
        "本项目用它同步依赖并运行 pytest、ruff 等命令。"
    ),
    "pydantic": (
        "Pydantic 是基于类型标注的数据校验库，本项目用它定义跨模块共享的领域模型。"
    ),
}
"""内置知识库：主题到确定性说明的固定映射。"""


def _extract_query(arguments: JsonObject) -> str:
    """从参数字典中取出并校验 query 字符串。"""

    unexpected = sorted(set(arguments) - {"query"})
    if unexpected:
        raise ToolArgumentError(f"不支持的参数：{', '.join(unexpected)}")

    query = arguments.get("query")
    if not isinstance(query, str):
        raise ToolArgumentError("query 必须是字符串")
    if not query.strip():
        raise ToolArgumentError("query 不能为空")
    if len(query) > MAX_QUERY_LENGTH:
        raise ToolArgumentError("查询过长")
    return query


def _normalize_query(query: str) -> str:
    """把查询统一为小写并折叠空白，保证匹配是确定性的。"""

    return re.sub(r"\s+", " ", query.strip()).casefold()


class RetrieveTool:
    """从内置知识库返回确定性说明的检索工具。

    工具无状态：``execute`` 只根据参数字典里的 ``query`` 做规范化并查表，
    相同输入永远返回相同输出。未知主题抛 ``ToolExecutionError``。
    """

    name = "retrieve"
    description = (
        "在项目内置知识库中按主题检索确定性说明。参数 query 是主题词，"
        "例如 react、python、deepseek、uv、pydantic；相同输入返回相同结果，"
        "未知主题返回稳定错误。"
    )
    parameters: JsonObject = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "知识库主题词，例如 react、python、deepseek、uv、pydantic"
                ),
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def execute(self, arguments: JsonObject) -> str:
        """执行一次知识检索并返回主题说明。"""

        query = _extract_query(arguments)
        entry = KNOWLEDGE_BASE.get(_normalize_query(query))
        if entry is None:
            raise ToolExecutionError(
                f"知识库中没有与查询「{query}」匹配的条目；"
                f"可用主题：{', '.join(sorted(KNOWLEDGE_BASE))}",
                retryable=True,
            )
        return entry


__all__ = [
    "KNOWLEDGE_BASE",
    "MAX_QUERY_LENGTH",
    "RetrieveTool",
]
