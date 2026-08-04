"""ReAct 主循环（Day 12）。

本模块把 Day 5 的 ``LLM``、Day 7 的 ``ToolRegistry``、Day 10 的系统提示词和
Day 11 的输出解析器串成一个有界闭环：每一轮用当前消息上下文请求模型，把模型
原始输出解析成 ``FinalAnswer`` 或 ``ToolCall``，工具调用交给注册表执行并把
``ToolResult`` 转成 ``Observation`` 写回上下文，直到给出最终回答、解析失败、
遇到不可恢复的工具失败或步数预算耗尽。

循环控制器（``Agent``）拥有唯一的步数计数与终止判断；``AgentState`` 是唯一
运行状态，只保存任务、消息、可用工具名称、步数预算、轨迹和终止信息，不保存
模型客户端、注册表、密钥或其他不可序列化运行时资源。本模块不实现重试、流式、
异步、持久化或并行工具调度；``LLM.complete`` 抛出的适配器错误按原样向上传播，
由调用方决定如何处理。
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from self_react.llm import LLM
from self_react.models import (
    AgentState,
    FinalAnswer,
    Message,
    MessageRole,
    Observation,
    TerminationReason,
    ToolCall,
    ToolErrorCode,
    ToolResult,
    TraceError,
    TraceStep,
)
from self_react.parser import ParseError, parse_decision
from self_react.prompts import render_system_prompt
from self_react.tools import ToolRegistry

_SUMMARY_LIMIT = 2_000
"""输入摘要的最大字符数，与 ``TraceStep.input_summary`` 的领域上限一致。"""


def _summarize_input(state: AgentState) -> str:
    """生成一轮模型输入的摘要。

    首轮没有工具观察，使用任务文本；后续轮次使用最近一条工具观察的内容，因为
    那是自上一轮以来真正新增的输入。返回前按领域模型上限截断，避免构造
    ``TraceStep`` 时触发校验错误。
    """

    for message in reversed(state.messages):
        if message.role is MessageRole.TOOL:
            return message.content[:_SUMMARY_LIMIT]
    return state.task[:_SUMMARY_LIMIT]


def _termination_reason_for(result: ToolResult) -> TerminationReason:
    """把不可恢复的工具失败映射为稳定终止原因。"""

    if result.error is not None and result.error.code is ToolErrorCode.UNKNOWN_TOOL:
        return TerminationReason.UNKNOWN_TOOL
    return TerminationReason.TOOL_EXECUTION_ERROR


class Agent:
    """拥有唯一步数计数与终止判断的 ReAct 循环控制器。

    ``Agent`` 只依赖 ``LLM`` 协议和 ``ToolRegistry`` 公开接口：任何满足协议的
    适配器（Fake LLM、DeepSeekLLM）和任何注册了确定性工具的注册表都可以传入。
    每次 ``run`` 返回一个满足 Day 4 状态不变量的终态 ``AgentState``。
    """

    def __init__(
        self,
        llm: LLM,
        registry: ToolRegistry,
        *,
        max_steps: int,
    ) -> None:
        """校验并保存循环依赖与步数预算。"""

        if not isinstance(llm, LLM):
            raise TypeError("llm 必须满足 LLM 协议")
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry 必须是 ToolRegistry")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int):
            raise ValueError("max_steps 必须是非负整数")
        if max_steps < 0:
            raise ValueError("max_steps 必须是非负整数")

        self._llm = llm
        self._registry = registry
        self._max_steps = max_steps

    def run(self, task: str) -> AgentState:
        """执行一次 ReAct 运行，并返回终态 ``AgentState``。"""

        if not isinstance(task, str):
            raise TypeError("task 必须是字符串")

        tool_names = tuple(self._registry.names)
        tools = [
            tool
            for name in tool_names
            if (tool := self._registry.get(name)) is not None
        ]
        messages = [
            Message(role=MessageRole.SYSTEM, content=render_system_prompt(tools)),
            Message(role=MessageRole.USER, content=task),
        ]

        state = self._rebuild_state(
            task=task,
            tool_names=tool_names,
            messages=messages,
            steps_used=0,
            trace=[],
        )

        while not state.is_terminated:
            if state.steps_used >= state.max_steps:
                state = self._rebuild_state(
                    task=task,
                    tool_names=tool_names,
                    messages=messages,
                    steps_used=state.steps_used,
                    trace=list(state.trace),
                    termination_reason=TerminationReason.MAX_STEPS_EXCEEDED,
                )
                break

            step_number = state.steps_used + 1
            input_summary = _summarize_input(state)
            started = time.perf_counter()
            response = self._llm.complete(messages)
            duration_ms = (time.perf_counter() - started) * 1_000.0
            messages.append(response)

            try:
                decision = parse_decision(response.content)
            except ParseError as exc:
                step = TraceStep(
                    step_number=step_number,
                    input_summary=input_summary,
                    error=TraceError(
                        code=exc.code,
                        message=str(exc),
                        retryable=False,
                    ),
                    duration_ms=duration_ms,
                )
                state = self._rebuild_state(
                    task=task,
                    tool_names=tool_names,
                    messages=messages,
                    steps_used=step_number,
                    trace=[*state.trace, step],
                    termination_reason=TerminationReason.MODEL_OUTPUT_PARSE_ERROR,
                )
                break

            if isinstance(decision, FinalAnswer):
                step = TraceStep(
                    step_number=step_number,
                    input_summary=input_summary,
                    decision=decision,
                    duration_ms=duration_ms,
                )
                state = self._rebuild_state(
                    task=task,
                    tool_names=tool_names,
                    messages=messages,
                    steps_used=step_number,
                    trace=[*state.trace, step],
                    final_answer=decision,
                    termination_reason=TerminationReason.FINAL_ANSWER,
                )
                break

            if not isinstance(decision, ToolCall):
                # parse_decision 只返回 FinalAnswer 或 ToolCall；此处为类型收窄
                # 的防御分支，正常情况下不可达。
                raise RuntimeError("parse_decision 返回了未知决策类型")

            result = self._registry.execute(decision)
            observation = Observation.from_tool_result(result)
            messages.append(observation.as_message())
            step = TraceStep(
                step_number=step_number,
                input_summary=input_summary,
                decision=decision,
                observation=observation,
                duration_ms=duration_ms,
            )
            terminated = (
                not result.is_success
                and result.error is not None
                and not result.error.retryable
            )
            state = self._rebuild_state(
                task=task,
                tool_names=tool_names,
                messages=messages,
                steps_used=step_number,
                trace=[*state.trace, step],
                termination_reason=(
                    _termination_reason_for(result) if terminated else None
                ),
            )

        return state

    def _rebuild_state(
        self,
        *,
        task: str,
        tool_names: Sequence[str],
        messages: Sequence[Message],
        steps_used: int,
        trace: Sequence[TraceStep],
        final_answer: FinalAnswer | None = None,
        termination_reason: TerminationReason | None = None,
    ) -> AgentState:
        """用完整字段重建 ``AgentState``，保证每轮状态满足 Day 4 不变量。"""

        return AgentState(
            task=task,
            messages=list(messages),
            available_tools=list(tool_names),
            max_steps=self._max_steps,
            steps_used=steps_used,
            trace=list(trace),
            final_answer=final_answer,
            termination_reason=termination_reason,
        )


__all__ = ["Agent"]
