"""确定性 runbook 知识检索工具（R-07）。

``RunbookSearchTool`` 对构造时注入的结构化 runbook 条目做 BM25 检索：把每个
条目的标题、错误码、服务、原因、检查项与动作拼接成可检索正文，用字符 bigram
与 ASCII/数字段的确定性 tokenizer 建索引。相同查询永远返回相同的 top-k 条目，
分数相同时按条目 ``id`` 字典序兜底，不引入向量数据库或外部检索依赖。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from self_react.models import JsonObject
from self_react.tools.base import ToolArgumentError
from self_react.tools.schema import generate_parameters_schema

K1 = 1.5
"""Okapi BM25 的 term frequency 饱和参数。"""

B = 0.75
"""Okapi BM25 的文档长度归一化参数。"""

MAX_QUERY_LENGTH = 500
"""query 参数的最大字符数。"""

_ASCII_TOKEN = re.compile(r"[a-z0-9]+")
"""ASCII/数字连续段的正则，大小写已折叠。"""

_CJK_SEGMENT = re.compile(r"[\u4e00-\u9fff]+")
"""中文连续段的正则，用于生成字符 bigram 与单字兜底。"""


class RunbookEntry(BaseModel):
    """一条结构化 runbook：错误码到诊断知识的最小单位。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    error_code: str = Field(min_length=1)
    service: str = Field(min_length=1)
    title: str = Field(min_length=1)
    causes: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)

    @field_validator("id", "error_code", "service", "title")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("runbook 关键字段不能只包含空白字符")
        return value


class RunbookSearchParameters(BaseModel):
    """runbook 检索工具的参数声明。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="检索关键词，例如 checkout 5xx 突增")
    top_k: int = Field(default=3, ge=1, le=5, description="返回条目数")


def tokenize(text: str) -> list[str]:
    """把文本切成确定性 token：ASCII/数字段 + 中文 bigram + 中文单字。"""

    lowered = text.casefold()
    tokens: list[str] = list(_ASCII_TOKEN.findall(lowered))
    for segment in _CJK_SEGMENT.findall(lowered):
        tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
        tokens.extend(segment)
    return tokens


def _entry_search_text(entry: RunbookEntry) -> str:
    """把结构化条目的可检索字段拼接成一段正文。"""

    return " ".join(
        [
            entry.title,
            entry.error_code,
            entry.service,
            *entry.causes,
            *entry.checks,
            *entry.actions,
        ]
    )


def _coerce_entry(entry: RunbookEntry | dict[str, object]) -> RunbookEntry:
    """把字典或模型统一转换成 ``RunbookEntry``。"""

    if isinstance(entry, RunbookEntry):
        return entry
    if isinstance(entry, dict):
        return RunbookEntry.model_validate(entry)
    raise TypeError("runbook 条目必须是 RunbookEntry 或字典")


class RunbookSearchTool:
    """对固定 runbook 语料做确定性 BM25 检索的工具。"""

    name = "runbook_search"
    description = (
        "在注入的 runbook 知识库中检索与查询最相关的诊断条目。参数 query 是"
        "自然语言关键词，例如 checkout 5xx 突增；top_k 是返回条目数（默认 3，"
        "最大 5）。相同查询永远返回相同结果，排序稳定。"
    )
    parameters: JsonObject = generate_parameters_schema(RunbookSearchParameters)

    def __init__(self, entries: Sequence[RunbookEntry | dict[str, object]]) -> None:
        """校验条目并建立确定性的 BM25 索引。"""

        if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
            raise TypeError("entries 必须是 runbook 条目序列")
        coerced = [_coerce_entry(entry) for entry in entries]
        ids = [entry.id for entry in coerced]
        if len(ids) != len(set(ids)):
            raise ValueError("runbook 条目 id 必须唯一")

        self._entries = coerced
        self._doc_tokens = [tokenize(_entry_search_text(entry)) for entry in coerced]
        self._document_frequency: dict[str, int] = {}
        for tokens in self._doc_tokens:
            for token in set(tokens):
                self._document_frequency[token] = (
                    self._document_frequency.get(token, 0) + 1
                )
        self._average_length = (
            sum(len(tokens) for tokens in self._doc_tokens) / len(self._doc_tokens)
            if self._doc_tokens
            else 0.0
        )

    def _score(self, query_tokens: list[str], doc_tokens: list[str]) -> float:
        """按 Okapi BM25 计算一个查询对一个文档的分数。"""

        total_docs = len(self._doc_tokens)
        if total_docs == 0:
            return 0.0
        term_frequency = Counter(doc_tokens)
        score = 0.0
        for token in query_tokens:
            document_frequency = self._document_frequency.get(token, 0)
            if document_frequency == 0:
                continue
            inverse_frequency = math.log(
                1 + (total_docs - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            frequency = term_frequency.get(token, 0)
            if frequency == 0:
                continue
            denominator = frequency + K1 * (
                1 - B + B * len(doc_tokens) / self._average_length
            )
            score += inverse_frequency * (frequency * (K1 + 1)) / denominator
        return score

    def search(self, query: str, top_k: int = 3) -> list[RunbookEntry]:
        """返回与查询最相关的 top-k 条目，分数相同时按 id 字典序兜底。"""

        query_tokens = tokenize(query)
        scored: list[tuple[RunbookEntry, float]] = []
        for entry, doc_tokens in zip(self._entries, self._doc_tokens, strict=True):
            score = self._score(query_tokens, doc_tokens)
            if score > 0.0:
                scored.append((entry, score))
        scored.sort(key=lambda item: (-item[1], item[0].id))
        return [entry for entry, _ in scored[:top_k]]

    def execute(self, arguments: JsonObject) -> str:
        """执行一次 runbook 检索并返回稳定文本。"""

        query, top_k = self._extract(arguments)
        entries = self.search(query, top_k)
        return self._format(entries)

    @staticmethod
    def _extract(arguments: JsonObject) -> tuple[str, int]:
        """从参数字典中取出并校验 query 与 top_k。"""

        unexpected = sorted(set(arguments) - {"query", "top_k"})
        if unexpected:
            raise ToolArgumentError(f"不支持的参数：{', '.join(unexpected)}")

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolArgumentError("query 必须是非空字符串")
        if len(query) > MAX_QUERY_LENGTH:
            raise ToolArgumentError("查询过长")

        top_k = arguments.get("top_k", 3)
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise ToolArgumentError("top_k 必须是整数")
        if not 1 <= top_k <= 5:
            raise ToolArgumentError("top_k 必须在 1 到 5 之间")
        return query, top_k

    @staticmethod
    def _format(entries: Sequence[RunbookEntry]) -> str:
        """把命中的条目渲染成稳定文本。"""

        if not entries:
            return "命中 0 条：没有找到相关 runbook 条目。"

        blocks = [f"命中 {len(entries)} 条："]
        for entry in entries:
            lines = [
                f"--- {entry.id} ---",
                f"标题：{entry.title}",
                f"错误码：{entry.error_code}",
                f"服务：{entry.service}",
                "常见原因：",
                *[f"- {cause}" for cause in entry.causes],
                "检查项：",
                *[f"- {check}" for check in entry.checks],
                "建议动作：",
                *[f"- {action}" for action in entry.actions],
            ]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)


__all__ = [
    "B",
    "K1",
    "MAX_QUERY_LENGTH",
    "RunbookEntry",
    "RunbookSearchParameters",
    "RunbookSearchTool",
    "tokenize",
]
