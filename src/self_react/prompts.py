"""最小系统提示词渲染（Day 10）。

本模块只负责把"任务规则 + 工具清单 + 输出格式契约"渲染成一个确定性的
系统提示词字符串。渲染是纯函数：相同工具清单输入永远得到完全相同字符串，
不访问网络、不读取环境变量、不修改输入。输出解析（Day 11）与 Agent 主
循环（Day 12）会分别消费这里的格式契约与工具描述。

提示词只消费工具的 ``name`` 与 ``description``：工具描述由各业务工具自己
维护（Day 8/9），本模块不复制、不解释工具行为，避免描述与实际行为分叉。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

_EMPTY_DESCRIPTION_PLACEHOLDER = "（无描述）"
"""工具描述为空时使用的稳定占位符，保证每个工具条目格式完整。"""

_NO_TOOLS_TEXT = "当前没有可用工具。"
"""工具清单为空时提示词中使用的固定说明。"""

_SYSTEM_INTRO = (
    "你是 Self-ReAct 智能体。每轮你只能选择一种决策：直接给出最终回答，"
    "或请求调用一个工具。两种决策互斥，不能同时出现，也不能输出这两种 "
    "JSON 形态以外的内容。"
)
"""提示词开场白：先告诉模型只有两种互斥决策。"""

_FINAL_ANSWER_CONTRACT = """## 决策一：最终回答（FinalAnswer）

当任务已经完成、信息足够直接回答用户时，输出：

{"kind": "final_answer", "content": "给用户的最终回答文本"}

content 必须是非空字符串。"""
"""最终回答形态的稳定格式契约。"""

_TOOL_CALL_CONTRACT = (
    "## 决策二：工具调用（ToolCall）\n\n"
    "当需要计算、读取文件或检索知识才能继续时，请求调用一个工具：\n\n"
    '{"kind": "tool_call", "call_id": "本轮唯一的非空编号", "name": "工具名", '
    '"arguments": {"参数名": "参数值"}}\n\n'
    "- 每轮只能请求调用一个工具；需要多个工具时，分多轮依次请求；\n"
    "- call_id 是本次调用在本轮上下文中唯一的非空字符串编号；\n"
    '- name 必须与"可用工具"中的名称精确匹配；\n'
    "- arguments 必须是 JSON 对象，键名必须与对应工具描述中说明的参数一致；\n"
    "  描述中未出现的键会被工具拒绝。"
)
"""工具调用形态的稳定格式契约。"""

_OUTPUT_RULES = """## 输出规则

1. 只输出一个 JSON 对象，不要包含 JSON 以外的文字、解释或代码块标记。
2. kind 只能是 "final_answer" 或 "tool_call"，二者互斥，不能同时出现。
3. 每轮只能输出一个 tool_call；即使需要多个工具，也要分多轮依次请求，
   等待前一个工具的结果返回后再请求下一个。
4. 只能调用"可用工具"中列出的工具；未列出的工具会被拒绝。
5. 当前没有可用工具时，只能输出 final_answer。"""
"""无论工具清单如何变化都保持稳定的输出纪律。"""


@runtime_checkable
class PromptTool(Protocol):
    """提示词渲染消费的最小工具形态：名称与描述。

    真实业务工具（计算器、文件读取、检索）天然满足该协议；提示词不需要
    也不应该调用 ``execute``，因此这里不要求执行能力。
    """

    name: str
    description: str


def _normalize_tools(tools: Sequence[PromptTool]) -> list[tuple[str, str]]:
    """校验工具清单，并返回按名称排序的 (名称, 描述) 稳定列表。"""

    if isinstance(tools, (str, bytes)) or not isinstance(tools, Sequence):
        raise TypeError("tools 必须是工具序列")

    normalized: list[tuple[str, str]] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("工具 name 必须是非空字符串")
        description = getattr(tool, "description", "")
        if not isinstance(description, str):
            description = ""
        normalized.append((name.strip(), description))
    return sorted(normalized, key=lambda item: item[0])


def _render_tools_section(tools: list[tuple[str, str]]) -> str:
    """渲染"可用工具"小节；工具按名称排序，空描述使用稳定占位符。"""

    if not tools:
        return _NO_TOOLS_TEXT

    lines = ["可用工具（只能调用这里的工具，名称必须精确匹配）："]
    lines.extend(
        f"- {name}：{description.strip() or _EMPTY_DESCRIPTION_PLACEHOLDER}"
        for name, description in tools
    )
    return "\n".join(lines)


def render_system_prompt(
    tools: Sequence[PromptTool],
    *,
    extra_instructions: str = "",
) -> str:
    """渲染确定性的最小系统提示词。

    相同工具清单输入永远返回完全相同字符串：工具按名称排序保证清单顺序
    稳定，描述为空时使用稳定占位符，工具清单为空时说明无可用工具但保留
    完整格式契约。渲染不访问网络、不读取环境变量、不修改输入。

    ``extra_instructions`` 是可选的场景/任务附加指引（keyword-only，默认
    空字符串）：非空时作为最后一个小节原样追加（首尾空白会被剥掉），让
    场景层能注入"数据文件固定、过滤参数语义、止损规则"等通用提示词里
    没有的场景知识；默认空字符串时输出与既有版本逐字节一致，Day 16 三条
    示例与全部既有测试不受影响。
    """

    if not isinstance(extra_instructions, str):
        raise TypeError("extra_instructions 必须是字符串")

    normalized = _normalize_tools(tools)
    sections = [
        _SYSTEM_INTRO,
        _FINAL_ANSWER_CONTRACT,
        _TOOL_CALL_CONTRACT,
        _render_tools_section(normalized),
        _OUTPUT_RULES,
    ]
    extra = extra_instructions.strip()
    if extra:
        sections.append(extra)
    return "\n\n".join(sections).strip()


__all__ = ["PromptTool", "render_system_prompt"]
