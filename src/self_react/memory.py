"""短程会话记忆 / 上下文管理（R-04，Day 24）。

本模块提供 ``ContextPolicy``：把 Agent 的完整消息列表压缩成发给模型的
请求列表。压缩只在超限时发生：按"整轮"为单位从旧到新裁剪（绝不拆开
"模型请求调用工具 -> 工具结果"这对消息），并把被裁掉的轮次压成固定模板
的规则式摘要，以第二条 system 消息回填（Claude auto-compact 风格）。

组件纪律：

- 纯函数：``prepare`` 不修改输入，相同输入永远得到相同输出，离线确定；
- 无状态：压缩与摘要每轮从完整历史重新计算，不把记忆状态写进
  ``AgentState``（领域模型 ``extra="forbid"`` 约束不受影响）；
- 保守：system 提示词与首条 user 任务消息永远保留，且不计入字符预算；
  最新一轮（触发超限的元凶）不参与裁剪。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from self_react.models import Message, MessageRole

DEFAULT_CONTEXT_WINDOW = 20_000
"""CLI 的默认上下文窗口（字符数）。

选值依据：远高于现有测试与 Day 16 示例的真实消息规模（全库最长字符串
字面量不足 2000 字符），既有行为可观测不变；同时是真实护栏——文件读取
单轮最多 10_000 字符，几次大读取即可触发压缩，且总量仍低于 DeepSeek
常见上下文上限。
"""

SUMMARY_HEADING = "以下是被裁剪的历史摘要："
"""摘要消息的固定开头，让模型识别这是背景材料而不是用户指令。"""

SUMMARY_LINE_LIMIT = 200
"""摘要中单个被裁轮次（一行）的最大字符数。"""

SUMMARY_TOTAL_LIMIT = 1_000
"""整条摘要内容的最大字符数（含开头与结尾标记）。"""

_MORE_MARKER = "（其余历史已省略）"
"""摘要因总量上限而省略后续轮次时的固定结尾。"""

_LINE_ELLIPSIS = "…"
"""单行截断时使用的固定省略号。"""

_PARSE_ERROR_SUMMARY = "模型输出未通过解析，已要求重新输出"
"""无工具调用轮次（解析失败重试轮）在摘要中的稳定描述。"""


def _message_char_count(message: Message) -> int:
    """返回一条消息对上下文预算贡献的稳定字符数。

    普通消息只计 ``content``；助手消息的每个 ``ToolCall`` 额外计入
    ``call_id``、``name`` 与参数 JSON（排序、紧凑序列化，保证确定性），
    让"模型请求调用工具"本身也占用预算。
    """

    total = len(message.content)
    for call in message.tool_calls:
        total += (
            len(call.call_id)
            + len(call.name)
            + len(
                json.dumps(
                    call.arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        )
    return total


def _split_prefix_and_rounds(
    messages: Sequence[Message],
) -> tuple[list[Message], list[list[Message]]]:
    """把消息列表拆成"永久保留的前缀"与"可裁剪的轮次列表"。

    前缀 = 第一条 assistant 消息之前的所有消息（system 提示词 + 首条
    user 任务，可能还有额外 system 消息）；轮次 = 每条 assistant 消息
    及其后紧随的 tool/user 反馈消息（直到下一条 assistant 消息为止）。
    这样裁剪永远以整轮为单位，工具调用与结果、解析失败与反馈消息都成对
    保留或成对移除。
    """

    messages_list = list(messages)
    first_assistant = next(
        (
            index
            for index, message in enumerate(messages_list)
            if message.role is MessageRole.ASSISTANT
        ),
        None,
    )
    if first_assistant is None:
        return messages_list, []

    rounds: list[list[Message]] = []
    current: list[Message] = []
    for message in messages_list[first_assistant:]:
        if message.role is MessageRole.ASSISTANT and current:
            rounds.append(current)
            current = [message]
        else:
            current.append(message)
    if current:
        rounds.append(current)
    return messages_list[:first_assistant], rounds


def _truncate_line(line: str) -> str:
    """按单行上限截断，超出部分用固定省略号标记。"""

    if len(line) <= SUMMARY_LINE_LIMIT:
        return line
    return line[: SUMMARY_LINE_LIMIT - len(_LINE_ELLIPSIS)] + _LINE_ELLIPSIS


def _round_summary_line(round_number: int, round_messages: Sequence[Message]) -> str:
    """把单个被裁轮次压缩成一行固定格式文本。

    优先提取"工具名 + 参数 + 工具结果"；没有工具调用时（解析失败重试轮）
    使用稳定描述，不把模型原始输出写进摘要。轮次序号是消息列表中 assistant
    轮次的位置（1 起），不是轨迹 ``TraceStep`` 的步号。
    """

    first = round_messages[0]
    assert first.role is MessageRole.ASSISTANT

    if first.tool_calls:
        call = first.tool_calls[0]
        args = json.dumps(
            call.arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        result = ""
        for message in round_messages:
            if message.role is MessageRole.TOOL:
                result = " ".join(message.content.split())
                break
        result_text = f" → {result}" if result else ""
        line = f"- 第 {round_number} 轮：调用 {call.name}，参数 {args}{result_text}"
    else:
        line = f"- 第 {round_number} 轮：{_PARSE_ERROR_SUMMARY}"

    return _truncate_line(line)


def _build_summary(removed_rounds: Sequence[Sequence[Message]]) -> str:
    """把被裁掉的轮次压成一条不超过总上限的摘要文本。

    从最早被裁的轮次开始逐行加入；某一行放不下时停止，并用固定标记说明
    还有历史被省略。整个算法不依赖随机性，相同输入永远返回相同文本。
    """

    lines: list[str] = []
    truncated = False
    for index, round_messages in enumerate(removed_rounds, start=1):
        line = _round_summary_line(index, round_messages)
        candidate = "\n".join([*lines, line])
        if len(f"{SUMMARY_HEADING}\n{candidate}") <= SUMMARY_TOTAL_LIMIT:
            lines.append(line)
        else:
            truncated = True
            break

    if not truncated:
        if lines:
            return f"{SUMMARY_HEADING}\n" + "\n".join(lines)
        return SUMMARY_HEADING

    # 总量超限：先尝试保留已接受的行并追加标记；放不下就逐行让位。
    while True:
        candidate = "\n".join([*lines, _MORE_MARKER])
        if len(f"{SUMMARY_HEADING}\n{candidate}") <= SUMMARY_TOTAL_LIMIT or not lines:
            return f"{SUMMARY_HEADING}\n" + candidate
        lines.pop()


class ContextPolicy:
    """把完整消息列表压缩成给模型的请求列表的纯函数组件。

    构造参数 ``context_window`` 是除 system 与首条 user 任务外的轮次
    字符预算。``prepare`` 在未超限时返回输入副本；超限时按整轮从旧到新
    裁剪，并把被裁轮次压成固定模板摘要，以第二条 system 消息插在 system
    与任务之间。组件不保存任何状态，每次调用都从完整历史重新计算。
    """

    def __init__(self, *, context_window: int) -> None:
        """校验并保存字符预算。"""

        if isinstance(context_window, bool) or not isinstance(context_window, int):
            raise ValueError("context_window 必须是正整数")
        if context_window <= 0:
            raise ValueError("context_window 必须是正整数")
        self._context_window = context_window

    @property
    def context_window(self) -> int:
        """返回当前字符预算。"""

        return self._context_window

    def prepare(self, messages: Sequence[Message]) -> list[Message]:
        """返回发给模型的请求消息列表；不修改输入。"""

        if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
            raise TypeError("messages 必须是 Message 的序列")
        if not messages:
            return []
        if not all(isinstance(message, Message) for message in messages):
            raise TypeError("messages 中的每一项都必须是 Message")

        prefix, rounds = _split_prefix_and_rounds(messages)
        total = sum(
            _message_char_count(message)
            for round_messages in rounds
            for message in round_messages
        )
        if total <= self._context_window:
            return list(messages)

        removed_rounds: list[list[Message]] = []
        kept_rounds = rounds
        while kept_rounds and total > self._context_window:
            removed = kept_rounds.pop(0)
            removed_rounds.append(removed)
            total -= sum(_message_char_count(message) for message in removed)

        summary_message = Message(
            role=MessageRole.SYSTEM,
            content=_build_summary(removed_rounds),
        )

        # 摘要插在 system 与任务之间：前缀中第一条 user 消息（任务）之前。
        insert_at = next(
            (
                index
                for index, message in enumerate(prefix)
                if message.role is MessageRole.USER
            ),
            len(prefix),
        )
        trimmed = [*prefix[:insert_at], summary_message, *prefix[insert_at:]]
        for round_messages in kept_rounds:
            trimmed.extend(round_messages)
        return trimmed


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "SUMMARY_HEADING",
    "SUMMARY_LINE_LIMIT",
    "SUMMARY_TOTAL_LIMIT",
    "ContextPolicy",
]
