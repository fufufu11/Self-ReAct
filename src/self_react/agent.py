"""ReAct 主循环（Day 12；Day 14 补充重复动作检测）。

本模块把 Day 5 的 ``LLM``、Day 7 的 ``ToolRegistry``、Day 10 的系统提示词和
Day 11 的输出解析器串成一个有界闭环：每一轮用当前消息上下文请求模型，把模型
原始输出解析成 ``FinalAnswer`` 或 ``ToolCall``，工具调用交给注册表执行并把
``ToolResult`` 转成 ``Observation`` 写回上下文，直到给出最终回答、解析失败
（至多一次重试后仍失败）、遇到不可恢复的工具失败或步数预算耗尽。

循环控制器（``Agent``）拥有唯一的步数计数与终止判断；``AgentState`` 是唯一
运行状态，只保存任务、消息、可用工具名称、步数预算、轨迹和终止信息，不保存
模型客户端、注册表、密钥或其他不可序列化运行时资源。本模块不实现适配器级
重试、流式、异步、持久化或并行工具调度；``LLM.complete`` 抛出的适配器错误
按原样向上传播，由调用方决定如何处理。解析失败只做至多一次的有界重试：把
稳定错误消息回写给模型、消耗一步预算；重试仍失败或预算不足以发起重试时，以
``MODEL_OUTPUT_PARSE_ERROR`` 终止，杜绝解析重试的无限子循环。重复动作（复用
``call_id`` 或同一工具连续使用相同参数）在分派前被拦截，作为带
``REPEATED_ACTION`` 错误码且可重试的失败观察回写，让模型在预算内换一种方式
继续。
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
from self_react.tools.final_answer import FinalAnswerTool

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


def _repeated_action_reason(decision: ToolCall, state: AgentState) -> str | None:
    """返回重复动作的稳定说明；没有重复时返回 ``None``。

    识别两种重复形态：``call_id`` 在任意更早步骤中使用过（编号语义在任何
    位置复用都错误），或者同一工具紧挨着使用完全相同的参数（连续重复才是
    模型"卡住"的信号）。中间隔了其他动作的相同参数调用属于合法新调用，
    不会被误判。重复动作在分派前被拦截，不会再次执行工具。
    """

    for step in state.trace:
        prior = step.decision
        if isinstance(prior, ToolCall):
            if prior.call_id == decision.call_id:
                return f"重复动作：调用编号 {decision.call_id} 已被使用"

    prior = state.trace[-1].decision if state.trace else None
    if (
        isinstance(prior, ToolCall)
        and prior.name == decision.name
        and prior.arguments == decision.arguments
    ):
        return (
            f"重复动作：工具 {decision.name} 已用相同参数调用过；"
            f"如需再次调用请更换参数或使用新编号"
        )
    return None


def _termination_reason_for(result: ToolResult) -> TerminationReason:
    """把不可恢复的工具失败映射为稳定终止原因。"""

    if result.error is not None and result.error.code is ToolErrorCode.UNKNOWN_TOOL:
        return TerminationReason.UNKNOWN_TOOL
    return TerminationReason.TOOL_EXECUTION_ERROR


def _parse_error_feedback(exc: ParseError) -> Message:
    """构造解析失败时回写给模型的稳定错误反馈消息。

    只复用 ``ParseError`` 的稳定中文说明（``str(exc)``），不泄漏模型原始
    输出、异常对象或堆栈；引导模型按 Day 10 格式契约重新输出一个 JSON
    对象，与提示词的输出纪律保持一致。反馈作为 user 角色消息追加到上下文，
    让重试轮模型能看到失败原因。
    """

    return Message(
        role=MessageRole.USER,
        content=(
            f"你的上一条输出无法解析：{exc}。请重新输出，只输出一个 "
            "JSON 对象，kind 只能是 final_answer 或 tool_call，"
            "不要包含 JSON 以外的文字、解释或代码块标记。"
        ),
    )


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

        parse_retried = False
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
            response = self._llm.complete(messages, tools=tools)
            duration_ms = (time.perf_counter() - started) * 1_000.0
            messages.append(response)

            if response.tool_calls:
                if len(response.tool_calls) > 1:
                    # 供应商一次性返回多个工具调用：领域模型每轮只支持一个
                    # 决策，因此只执行第一个，其余写成可恢复失败观察并回写
                    # 消息，让 API 历史中每个 tool_call_id 都有对应 tool
                    # 消息，同时提示模型把其余工具留到后续轮次。
                    decision: FinalAnswer | ToolCall = response.tool_calls[0]
                    result = self._registry.execute(decision)
                    observation = Observation.from_tool_result(result)
                    messages.append(observation.as_message())
                    for extra_call in response.tool_calls[1:]:
                        extra_observation = Observation(
                            tool_call_id=extra_call.call_id,
                            tool_name=extra_call.name,
                            content="本轮只执行了一个工具；请在后续轮次再请求该工具",
                            is_error=True,
                            error_code=ToolErrorCode.TOOL_EXECUTION_ERROR,
                            retryable=True,
                        )
                        messages.append(extra_observation.as_message())
                    step = TraceStep(
                        step_number=step_number,
                        input_summary=input_summary,
                        decision=decision,
                        observation=observation,
                        duration_ms=duration_ms,
                    )
                    state = self._rebuild_state(
                        task=task,
                        tool_names=tool_names,
                        messages=messages,
                        steps_used=step_number,
                        trace=[*state.trace, step],
                    )
                    continue
                # 供应商原生工具调用：每轮一个工具，直接作为本轮决策，
                # 不经过文本 JSON 解析。
                decision: FinalAnswer | ToolCall = response.tool_calls[0]
            else:
                try:
                    decision = parse_decision(response.content)
                except ParseError as exc:
                    # 解析失败有界重试：至多重试一次。第一次失败回写稳定
                    # 错误消息并消耗一步预算；重试仍失败或预算不足以发起
                    # 重试时，以 MODEL_OUTPUT_PARSE_ERROR 终止。
                    retryable = not parse_retried and step_number < state.max_steps
                    step = TraceStep(
                        step_number=step_number,
                        input_summary=input_summary,
                        error=TraceError(
                            code=exc.code,
                            message=str(exc),
                            retryable=retryable,
                        ),
                        duration_ms=duration_ms,
                    )
                    trace = [*state.trace, step]
                    if not retryable:
                        state = self._rebuild_state(
                            task=task,
                            tool_names=tool_names,
                            messages=messages,
                            steps_used=step_number,
                            trace=trace,
                            termination_reason=TerminationReason.MODEL_OUTPUT_PARSE_ERROR,
                        )
                        break
                    state = self._rebuild_state(
                        task=task,
                        tool_names=tool_names,
                        messages=messages,
                        steps_used=step_number,
                        trace=trace,
                    )
                    messages.append(_parse_error_feedback(exc))
                    parse_retried = True
                    continue

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
                # 类型收窄的防御分支，正常情况下不可达。
                raise RuntimeError("LLM 返回了未知决策类型")

            if decision.name == FinalAnswerTool.name:
                # 特殊工具：模型用原生 tool_calls 交付最终回答。直接转换为
                # FinalAnswer 决策并终止；写回一条 tool 消息保持 API 历史
                # 完整（每个 tool_call_id 都有对应响应），轨迹只记录决策。
                content = decision.arguments.get("content")
                if not isinstance(content, str) or not content.strip():
                    content = "（无内容）"
                answer = FinalAnswer(content=content)
                messages.append(
                    Observation(
                        tool_call_id=decision.call_id,
                        tool_name=FinalAnswerTool.name,
                        content=content,
                        is_error=False,
                    ).as_message()
                )
                step = TraceStep(
                    step_number=step_number,
                    input_summary=input_summary,
                    decision=answer,
                    duration_ms=duration_ms,
                )
                state = self._rebuild_state(
                    task=task,
                    tool_names=tool_names,
                    messages=messages,
                    steps_used=step_number,
                    trace=[*state.trace, step],
                    final_answer=answer,
                    termination_reason=TerminationReason.FINAL_ANSWER,
                )
                break

            repeated_message = _repeated_action_reason(decision, state)
            if repeated_message is not None:
                result = ToolResult.failure(
                    tool_call_id=decision.call_id,
                    tool_name=decision.name,
                    code=ToolErrorCode.REPEATED_ACTION,
                    message=repeated_message,
                    retryable=True,
                )
            else:
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

            continue

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
