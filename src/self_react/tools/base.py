"""工具协议、工具注册表与统一调用边界。

Day 7 只提供最小工具层：``Tool`` 协议描述工具长什么样，``ToolRegistry``
按精确名称登记工具并把领域 ``ToolCall`` 转换为统一的 ``ToolResult``。
具体业务工具（计算器、文件读取、天气等）属于 Day 8/9；Agent 主循环属于
Day 12。本模块不修改 Day 5 的 ``LLM.complete`` 接口，也不执行任何工具
调用以外的逻辑。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from self_react.models import (
    JsonObject,
    ToolCall,
    ToolErrorCode,
    ToolResult,
)

DEFAULT_PARAMETERS_SCHEMA: JsonObject = {"type": "object", "properties": {}}
"""工具未声明 ``parameters`` 时 LLM 适配器下发的宽松参数形状。

Day 17 对照 LangChain/LangGraph 吸收的改进是让工具自述参数：业务工具可以
声明可选的 ``parameters`` 类属性（JSON Schema 对象），适配器把它随工具
定义下发给模型；未声明时使用本常量，保持既有行为不变。
"""


class ToolArgumentError(ValueError):
    """工具发现参数不满足业务要求时抛出的稳定异常。

    注册表会把这类异常转换为 ``INVALID_ARGUMENTS`` 失败结果。异常消息由
    工具作者编写，是面向模型的稳定说明，不应包含密钥或内部对象。
    """


class ToolExecutionError(Exception):
    """工具业务执行失败时抛出的稳定异常。

    ``retryable`` 表达这次失败是否值得让模型换一种方式再试；默认是可重试。
    注册表会把这类异常转换为 ``TOOL_EXECUTION_ERROR`` 失败结果。
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        """保存安全说明和可恢复性。"""

        self.retryable = retryable
        super().__init__(message)


class ToolRegistrationError(ValueError):
    """注册表拒绝无效、重复或空名称的工具时抛出的稳定异常。"""


@runtime_checkable
class Tool(Protocol):
    """确定性本地工具的最小协议。

    ``name`` 是注册表使用的精确名称；``description`` 是后续提示词提供给
    模型的说明；可选的 ``parameters`` 是描述参数的 JSON Schema 对象（例如
    ``{"type": "object", "properties": {"query": {"type": "string"}},
    "required": ["query"], "additionalProperties": False}``），LLM 适配器
    把它下发给模型以生成合法参数，未声明时使用
    ``DEFAULT_PARAMETERS_SCHEMA``；``execute`` 接收已由领域模型校验过的
    参数字典并返回模型可读的字符串内容。工具只做自己的业务，不接触
    ``Message``、``AgentState``、注册表或密钥；失败时抛出
    ``ToolArgumentError`` 或 ``ToolExecutionError``，由注册表统一转换。

    注意：``parameters`` 是可选约定，不是协议必需成员。协议只要求
    ``name``、``description`` 与 ``execute``；适配器通过 ``getattr`` 读取
    可选的 ``parameters``，因此不声明 schema 的简单工具仍然满足协议。
    """

    name: str
    description: str

    def execute(self, arguments: JsonObject) -> str:
        """执行一次工具调用并返回内容；失败时抛出稳定工具异常。"""
        ...


class ToolRegistry:
    """按精确名称登记工具并统一执行调用边界的注册表。

    注册表持有工具对象是运行时行为；它永远不会把工具、客户端或密钥写进
    ``Message``、``AgentState`` 或可序列化的 ``ToolResult``。注册名称在
    注册时固定，因此注册后外部修改工具对象不会改变注册表键。
    """

    def __init__(self) -> None:
        """创建一个空的工具注册表。"""

        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """按工具名称登记一个满足 ``Tool`` 协议的对象。"""

        if not isinstance(tool, Tool):
            raise ToolRegistrationError("只能注册满足 Tool 协议的对象")
        if not isinstance(tool.name, str) or not tool.name.strip():
            raise ToolRegistrationError("工具 name 必须是非空字符串")
        if not isinstance(tool.description, str) or not tool.description.strip():
            raise ToolRegistrationError("工具 description 必须是非空字符串")
        if tool.name in self._tools:
            raise ToolRegistrationError(f"工具已注册：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按精确名称返回已注册工具；未注册时返回 ``None``。"""

        return self._tools.get(name)

    @property
    def names(self) -> tuple[str, ...]:
        """返回已注册工具名称的只读元组。"""

        return tuple(self._tools)

    def __contains__(self, name: object) -> bool:
        """支持 ``name in registry`` 的精确名称检查。"""

        return name in self._tools

    def __len__(self) -> int:
        """返回已注册工具数量。"""

        return len(self._tools)

    def execute(self, call: ToolCall) -> ToolResult:
        """执行一次领域工具调用并统一转换为 ``ToolResult``。

        未知工具返回 ``UNKNOWN_TOOL`` 失败结果，绝不按名称动态导入或执行
        代码。参数校验失败返回 ``INVALID_ARGUMENTS``；执行异常返回
        ``TOOL_EXECUTION_ERROR``。``KeyboardInterrupt`` 和 ``SystemExit``
        属于系统级控制流，不在这里被吞掉。
        """

        if not isinstance(call, ToolCall):
            raise TypeError("execute 只接受领域 ToolCall")

        tool = self._tools.get(call.name)
        if tool is None:
            available = tuple(sorted(self._tools))
            return ToolResult.failure(
                tool_call_id=call.call_id,
                tool_name=call.name,
                code=ToolErrorCode.UNKNOWN_TOOL,
                message=(
                    f"未知工具：{call.name}；可用工具：{', '.join(available) or '无'}"
                ),
                retryable=True,
                details={
                    "requested_tool": call.name,
                    "available_tools": list(available),
                },
            )

        try:
            content = tool.execute(call.arguments)
        except ToolArgumentError as exc:
            return ToolResult.failure(
                tool_call_id=call.call_id,
                tool_name=call.name,
                code=ToolErrorCode.INVALID_ARGUMENTS,
                message=str(exc),
                retryable=True,
            )
        except ToolExecutionError as exc:
            return ToolResult.failure(
                tool_call_id=call.call_id,
                tool_name=call.name,
                code=ToolErrorCode.TOOL_EXECUTION_ERROR,
                message=str(exc),
                retryable=exc.retryable,
            )
        except Exception:
            return ToolResult.failure(
                tool_call_id=call.call_id,
                tool_name=call.name,
                code=ToolErrorCode.TOOL_EXECUTION_ERROR,
                message=f"工具执行失败：{call.name}",
                retryable=True,
            )

        if not isinstance(content, str):
            return ToolResult.failure(
                tool_call_id=call.call_id,
                tool_name=call.name,
                code=ToolErrorCode.TOOL_EXECUTION_ERROR,
                message=f"工具 {call.name} 必须返回字符串内容",
                retryable=False,
            )

        return ToolResult.success(
            tool_call_id=call.call_id,
            tool_name=call.name,
            content=content,
        )


__all__ = [
    "DEFAULT_PARAMETERS_SCHEMA",
    "Tool",
    "ToolArgumentError",
    "ToolExecutionError",
    "ToolRegistrationError",
    "ToolRegistry",
]
