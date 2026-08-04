"""Day 10 最小系统提示词渲染的公开行为测试。

测试只依赖确定性本地输入：真实工具对象与记录型替身，不访问网络、不调用
真实 API。核心契约是"纯函数确定性"：相同工具清单输入必须渲染出完全相同
字符串，且提示词完整描述两种互斥决策形态，供 Day 11 解析器消费。
"""

from __future__ import annotations

import pytest

from self_react.prompts import PromptTool, render_system_prompt
from self_react.tools import CalculatorTool, FileReaderTool, RetrieveTool


class RecordTool:
    """只携带名称与描述的最简工具替身，用于测试空描述等边界。"""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description


def _three_tools() -> tuple[FileReaderTool, CalculatorTool, RetrieveTool]:
    """构造三个真实业务工具：文件读取、计算器与知识检索。"""

    return (
        FileReaderTool(root_directory="C:/allowed"),
        CalculatorTool(),
        RetrieveTool(),
    )


def test_render_with_three_tools_contains_names_and_descriptions() -> None:
    """提示词必须包含三个工具的精确名称与全部描述文本。"""

    prompt = render_system_prompt(_three_tools())

    assert "calculator" in prompt
    assert "file_reader" in prompt
    assert "retrieve" in prompt
    assert CalculatorTool.description in prompt
    assert FileReaderTool.description in prompt
    assert RetrieveTool.description in prompt


def test_render_documents_both_mutually_exclusive_decision_kinds() -> None:
    """最终回答与工具调用两种决策形态、判别字段和互斥说明都必须出现。"""

    prompt = render_system_prompt(_three_tools())

    assert "final_answer" in prompt
    assert "tool_call" in prompt
    assert '"kind": "final_answer"' in prompt
    assert '"kind": "tool_call"' in prompt
    assert "互斥" in prompt
    assert "call_id" in prompt
    assert "arguments" in prompt


def test_render_reminds_file_reader_relative_path_rule() -> None:
    """提示词必须提醒 file_reader 只能传根目录内相对路径。"""

    prompt = render_system_prompt(_three_tools())

    assert "相对路径" in prompt
    assert "file_reader" in prompt


def test_render_is_deterministic_pure_function() -> None:
    """相同工具清单输入两次渲染，必须得到完全相同字符串。"""

    tools = _three_tools()
    first = render_system_prompt(tools)
    second = render_system_prompt(tools)

    assert first == second


def test_render_is_independent_of_input_order() -> None:
    """工具传入顺序不同时，渲染结果必须完全一致（清单顺序稳定）。"""

    tools = _three_tools()
    forward = render_system_prompt(tools)
    backward = render_system_prompt(tuple(reversed(tools)))

    assert forward == backward


def test_render_does_not_mutate_input_sequence_or_tool_objects() -> None:
    """纯函数不得修改调用方的工具列表或工具对象。"""

    tools = list(_three_tools())
    snapshot = [(tool.name, tool.description) for tool in tools]

    render_system_prompt(tools)

    assert [(tool.name, tool.description) for tool in tools] == snapshot


def test_render_accepts_tuple_and_list_equivalently() -> None:
    """元组与列表传入同一组工具，渲染结果必须一致。"""

    tools = _three_tools()

    assert render_system_prompt(tools) == render_system_prompt(list(tools))


def test_render_with_empty_tool_list_is_stable_and_complete() -> None:
    """无工具可用时仍能稳定渲染，说明无法调用工具但保留完整格式契约。"""

    prompt = render_system_prompt([])

    assert "没有可用工具" in prompt
    assert '"kind": "final_answer"' in prompt
    assert '"kind": "tool_call"' in prompt
    assert "互斥" in prompt
    assert prompt.strip() == prompt


def test_render_with_blank_description_uses_stable_placeholder() -> None:
    """工具描述为空时不抛异常，也不输出残缺条目，而是使用稳定占位符。"""

    prompt = render_system_prompt([RecordTool(name="echo", description="")])

    assert "echo" in prompt
    assert "（无描述）" in prompt
    assert '"kind": "final_answer"' in prompt
    assert '"kind": "tool_call"' in prompt


def test_render_mixes_real_tools_and_blank_description_tool() -> None:
    """真实工具与空描述工具混用时，每个工具都有一条完整条目。"""

    tools = [RecordTool(name="echo"), CalculatorTool(), RetrieveTool()]
    prompt = render_system_prompt(tools)

    assert "calculator" in prompt
    assert "retrieve" in prompt
    assert "echo" in prompt
    assert "（无描述）" in prompt


def test_render_lists_tools_in_stable_alphabetical_order() -> None:
    """工具条目按名称排序渲染，顺序不依赖调用方传入顺序。"""

    prompt = render_system_prompt(_three_tools())

    assert prompt.index("calculator") < prompt.index("file_reader")
    assert prompt.index("file_reader") < prompt.index("retrieve")


def test_render_instructs_single_json_object_output() -> None:
    """提示词必须要求模型只输出一个 JSON 对象，避免夹杂解释文字。"""

    prompt = render_system_prompt(_three_tools())

    assert "只输出一个 JSON 对象" in prompt


def test_render_rejects_non_sequence_input() -> None:
    """渲染入口只接受工具序列；字符串、字典等输入应明确拒绝。"""

    with pytest.raises(TypeError):
        render_system_prompt("calculator")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        render_system_prompt({"name": "calculator"})  # type: ignore[arg-type]


def test_render_rejects_blank_or_missing_tool_name() -> None:
    """没有名字的工具无法被模型调用，渲染时应明确拒绝而不是输出残缺条目。"""

    with pytest.raises(ValueError):
        render_system_prompt([RecordTool(name="")])
    with pytest.raises(ValueError):
        render_system_prompt([RecordTool(name="   ")])
    with pytest.raises(ValueError):
        render_system_prompt([object()])  # type: ignore[list-item]


def test_real_tools_satisfy_prompt_tool_protocol() -> None:
    """三个真实业务工具都满足提示词渲染所需的最小协议。"""

    assert isinstance(CalculatorTool(), PromptTool)
    assert isinstance(FileReaderTool(root_directory="C:/allowed"), PromptTool)
    assert isinstance(RetrieveTool(), PromptTool)
