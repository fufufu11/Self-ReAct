"""最终回答工具（Day 16 真实模型适配）。

DeepSeek 原生工具调用模式下，模型可能把提示词中的 ``final_answer`` 当成
一个可调用的工具。为了让真实模型稳定结束对话，本项目把 ``final_answer``
注册为特殊工具：它不代表外部动作，Agent 在分派前拦截并把调用转换为
``FinalAnswer`` 决策。工具本身提供 ``execute`` 以满足 ``Tool`` 协议，
但正常流程中不会被注册表真正执行。
"""

from __future__ import annotations

from self_react.models import JsonObject


class FinalAnswerTool:
    """标记对话结束的特殊工具：把最终回答内容交付给 Agent。"""

    name = "final_answer"
    description = (
        "任务完成时使用本工具结束对话并交付最终回答。"
        "参数 content 是给用户的最终回答文本。"
    )
    parameters: JsonObject = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "给用户的最终回答文本",
            },
        },
        "required": ["content"],
        "additionalProperties": False,
    }

    def execute(self, arguments: JsonObject) -> str:
        """返回最终回答文本；Agent 会在分派前拦截本工具。"""

        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            return "（无内容）"
        return content


__all__ = ["FinalAnswerTool"]
