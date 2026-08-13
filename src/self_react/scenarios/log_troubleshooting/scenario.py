"""日志/故障排查场景的组装层（R-07）。

本模块只负责把通用工具（``calculator`` / ``file_reader`` / ``log_query`` /
``runbook_search`` / ``final_answer``）与场景数据（合成日志、runbook、发布记录）
组装成一个工具注册表。数据文件放在同目录 ``data/`` 下，保持离线确定与可复现。
"""

from __future__ import annotations

import json
from pathlib import Path

from self_react.tools import (
    CalculatorTool,
    FileReaderTool,
    FinalAnswerTool,
    LogQueryTool,
    RunbookEntry,
    RunbookSearchTool,
    ToolRegistry,
)

SCENARIO_NAME = "log-troubleshooting"
"""``run --scenario`` 使用的场景标识。"""

_DATA_DIR = Path(__file__).resolve().parent / "data"
"""场景数据的根目录：``file_reader`` 与 ``log_query`` 的安全边界。"""


def _load_runbook_entries() -> list[RunbookEntry]:
    """从固定 fixture 读取并校验 runbook 条目。"""

    text = (_DATA_DIR / "runbook.ndjson").read_text(encoding="utf-8")
    return [
        RunbookEntry.model_validate(json.loads(line))
        for line in text.splitlines()
        if line.strip()
    ]


def build_registry() -> ToolRegistry:
    """构造日志/故障排查场景的工具注册表。"""

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(FileReaderTool(root_directory=_DATA_DIR))
    registry.register(LogQueryTool(root_directory=_DATA_DIR))
    registry.register(RunbookSearchTool(entries=_load_runbook_entries()))
    registry.register(FinalAnswerTool())
    return registry


__all__ = ["SCENARIO_NAME", "build_registry"]
