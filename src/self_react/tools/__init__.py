"""工具协议、工具注册表与统一调用边界。

Day 7 只提供最小工具层，具体业务工具由后续 Day 8/9 实现。子包集中重导出
公开名称，调用方可以只依赖 ``self_react.tools``，而不必关心内部文件划分。
"""

from self_react.tools.base import (
    Tool,
    ToolArgumentError,
    ToolExecutionError,
    ToolRegistrationError,
    ToolRegistry,
)

__all__ = [
    "Tool",
    "ToolArgumentError",
    "ToolExecutionError",
    "ToolRegistrationError",
    "ToolRegistry",
]
