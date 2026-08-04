"""工具协议、工具注册表与统一调用边界。

Day 7 提供最小工具层，Day 8 加入计算器，Day 9 加入受限文件读取与确定性
知识检索工具。子包集中重导出公开名称，调用方可以只依赖
``self_react.tools``，而不必关心内部文件划分。
"""

from self_react.tools.base import (
    Tool,
    ToolArgumentError,
    ToolExecutionError,
    ToolRegistrationError,
    ToolRegistry,
)
from self_react.tools.calculator import CalculatorTool
from self_react.tools.file_reader import FileReaderTool
from self_react.tools.retrieve import RetrieveTool

__all__ = [
    "CalculatorTool",
    "FileReaderTool",
    "RetrieveTool",
    "Tool",
    "ToolArgumentError",
    "ToolExecutionError",
    "ToolRegistrationError",
    "ToolRegistry",
]
