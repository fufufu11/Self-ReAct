"""roadmap 10.7：日志/故障排查场景固定 fixture 的 PII 守卫测试。

对三个已提交的 NDJSON 数据文件（``logs.ndjson`` / ``runbook.ndjson`` /
``deploys.ndjson``）做正则扫描，断言不含邮箱 / ``mailto:`` / 电话 / SSN
模式，防止未来重新提取 fixture 时把个人标识带入入库数据。

背景：规范化流程已丢弃原始日志的 IP / User-Agent / Referer 列（见
``data/PROVENANCE.md`` 的 PII 扫描说明）；本测试把"fixture 零 PII"变成
持续保证的回归防线。纯离线确定性测试，不访问网络、不依赖 API Key。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from self_react.scenarios import log_troubleshooting

DATA_DIR = Path(log_troubleshooting.__file__).resolve().parent / "data"
"""场景固定 fixture 目录（与 ``scenario.py`` 的 ``_DATA_DIR`` 同源）。"""

FIXTURE_FILES = ("logs.ndjson", "runbook.ndjson", "deploys.ndjson")

PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("mailto 链接", re.compile(r"mailto:")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("北美电话", re.compile(r"\b\d{3}-\d{3}-\d{4}\b")),
)
"""守卫模式集：邮箱（含 ``mailto:`` 链接）、SSN、北美连字符电话格式。

与 ``data/PROVENANCE.md`` 的 PII 扫描说明保持同一模式口径；只扫已提交的
fixture，不依赖未提交的 ``tmp/`` 原始文件。
"""


@pytest.mark.parametrize("filename", FIXTURE_FILES)
def test_fixture_file_exists(filename: str) -> None:
    """三个 fixture 文件必须存在，保证守卫测试实际扫描到了数据。"""

    assert (DATA_DIR / filename).is_file()


@pytest.mark.parametrize(
    ("filename", "label", "pattern"),
    [
        (filename, label, pattern)
        for filename in FIXTURE_FILES
        for label, pattern in PII_PATTERNS
    ],
    ids=[
        f"{filename}:{label}"
        for filename in FIXTURE_FILES
        for label, _pattern in PII_PATTERNS
    ],
)
def test_fixture_contains_no_pii(
    filename: str, label: str, pattern: re.Pattern[str]
) -> None:
    """fixture 不含任何 PII 模式；失败时报告首次命中的行号与内容。"""

    text = (DATA_DIR / filename).read_text(encoding="utf-8")
    hits = [
        (line_no, line.strip())
        for line_no, line in enumerate(text.splitlines(), start=1)
        if pattern.search(line)
    ]
    assert not hits, (
        f"{filename} 含 {label} 模式 {len(hits)} 处；"
        f"首次命中第 {hits[0][0]} 行：{hits[0][1]}"
    )
