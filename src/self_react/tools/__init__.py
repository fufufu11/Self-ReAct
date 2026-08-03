"""工具协议、工具注册表与统一调用边界。

Day 7 提供最小工具层，Day 8 在此基础上加入第一个确定性业务工具（计算器）。
子包集中重导出公开名称，调用方可以只依赖 ``self_react.tools``，而不必关心
内部文件划分。
"""

from self_react.tools.base import (
    Tool,
    ToolArgumentError,
    ToolExecutionError,
    ToolRegistrationError,
    ToolRegistry,
)
from self_react.tools.calculator import CalculatorTool

__all__ = [
    "CalculatorTool",
    "Tool",
    "ToolArgumentError",
    "ToolExecutionError",
    "ToolRegistrationError",
    "ToolRegistry",
]
