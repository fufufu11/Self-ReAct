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

SCENARIO_EXTRA_INSTRUCTIONS = (
    "【本次任务指引】\n"
    "\n"
    "1. 数据文件固定为 logs.ndjson、runbook.ndjson、deploys.ndjson，"
    "路径参数只能填这三个文件名。\n"
    "2. 按状态码过滤必须用 error_code 参数；keyword 参数只匹配 "
    "message（请求行）子串，不能用来过滤状态码。\n"
    "3. service 参数是请求行里的主机名（如 jet、root、wp-content），"
    "不是站点名，不要用 promjet 作为 service 过滤值。\n"
    "4. 读取日志内容用 log_query 过滤/聚合，不要用 file_reader "
    "直接读 logs.ndjson 全文。\n"
    "5. 证据足以回答时立即输出 final_answer，不要继续额外查询；"
    "重复相同的过滤/聚合不会带来新信息。"
)
"""注入系统提示词的场景指引（R-09 真实验收后补：见 day-28 §5）。

针对真实模型在 5 步预算内步数耗尽的失败模式：猜测不存在的文件名、
把状态码当 ``keyword`` 过滤（0 命中）、证据足够仍继续深挖；2026-08-16
复跑后又补两条：把站点名 promjet 当 ``service`` 过滤值（0 命中）与用
``file_reader`` 直读 ``logs.ndjson`` 全文。由 CLI ``run --scenario
log-troubleshooting`` 与三个场景示例透传给 ``Agent.run`` 的
``extra_instructions``，作为系统提示词最后一个小节渲染。
"""

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


__all__ = [
    "SCENARIO_EXTRA_INSTRUCTIONS",
    "SCENARIO_NAME",
    "build_registry",
]
