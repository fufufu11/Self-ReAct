"""ReAct 主循环（Day 12；Day 14 补充重复动作检测）。

本模块把 Day 5 的 ``LLM``、Day 7 的 ``ToolRegistry``、Day 10 的系统提示词和
Day 11 的输出解析器串成一个有界闭环：每一轮用当前消息上下文请求模型，把模型
原始输出解析成 ``FinalAnswer`` 或 ``ToolCall``，工具调用交给注册表执行并把
``ToolResult`` 转成 ``Observation`` 写回上下文，直到给出最终回答、解析失败
（至多一次重试后仍失败）、遇到不可恢复的工具失败或步数预算耗尽。

循环控制器（``Agent``）拥有唯一的步数计数与终止判断；``AgentState`` 是唯一
运行状态，只保存任务、消息、可用工具名称、步数预算、轨迹和终止信息，不保存
模型客户端、注册表、密钥或其他不可序列化运行时资源。本模块不实现适配器级
重试、异步、持久化或并行工具调度；流式通过可选 ``stream`` 参数消费
``complete_stream`` 的增量并组装出与非流式完全相同的消息。``LLM.complete``
抛出的适配器错误按原样向上传播，由调用方决定如何处理。解析失败只做至多
一次的有界重试：把
稳定错误消息回写给模型、消耗一步预算；重试仍失败或预算不足以发起重试时，以
``MODEL_OUTPUT_PARSE_ERROR`` 终止，杜绝解析重试的无限子循环。重复动作（复用
``call_id`` 或同一工具连续使用相同参数）在分派前被拦截，作为带
``REPEATED_ACTION`` 错误码且可重试的失败观察回写，让模型在预算内换一种方式
继续。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence

from self_react.llm import LLM, StreamChunk, collect_stream
from self_react.memory import ContextPolicy
from self_react.models import (
    AgentState,
    FinalAnswer,
    Message,
    MessageRole,
    Observation,
    Plan,
    Reflection,
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

_PLAN_INSTRUCTION = (
    '请先输出计划：只输出一个 JSON 对象 {"kind": "plan", "content": '
    '"简短计划"}，用 1-3 句话说明你将如何完成任务（要调用哪些工具、按什么'
    "顺序、证据足够时何时给出最终回答）。不要输出其他内容。"
)
"""plan-then-execute 模式（R-06）规划阶段的稳定指令消息。"""

_REFLECTION_INSTRUCTION = (
    "刚才的工具调用失败了。请先反思：只输出一个 JSON 对象 "
    '{"kind": "reflection", "content": "失败原因总结与下一步方案"}，'
    "先一句话总结失败原因，再说明下一步方案。不要输出其他内容。"
)
"""reflection 模式（R-06）反思阶段的稳定指令消息。"""


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


def _notify_step(step: TraceStep, on_step: Callable[[TraceStep], None] | None) -> None:
    """把完成的轨迹步骤交给可选的展示回调。"""

    if on_step is not None:
        on_step(step)


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


def _aux_parse_feedback(exc: ParseError, kind_label: str) -> Message:
    """构造规划/反思阶段解析失败时回写给模型的稳定错误反馈消息。

    与 :func:`_parse_error_feedback` 同一风格，只复用 ``ParseError`` 的
    稳定中文说明；``kind_label`` 是当前阶段唯一允许的 kind 名称（``plan``
    或 ``reflection``），引导模型重新输出对应形态的 JSON 对象。
    """

    return Message(
        role=MessageRole.USER,
        content=(
            f"你的上一条输出无法解析：{exc}。请重新输出，只输出一个 "
            f"JSON 对象，kind 只能是 {kind_label}，"
            "不要包含 JSON 以外的文字、解释或代码块标记。"
        ),
    )


class Agent:
    """拥有唯一步数计数与终止判断的 ReAct 循环控制器。

    ``Agent`` 只依赖 ``LLM`` 协议和 ``ToolRegistry`` 公开接口：任何满足协议的
    适配器（Fake LLM、DeepSeekLLM）和任何注册了确定性工具的注册表都可以传入。
    每次 ``run`` 返回一个满足 Day 4 状态不变量的终态 ``AgentState``；可选
    ``stream`` 参数让循环消费流式增量，并通过 ``on_chunk`` / ``on_step``
    回调把增量和逐步结果即时交给展示层。
    """

    def __init__(
        self,
        llm: LLM,
        registry: ToolRegistry,
        *,
        max_steps: int,
        context_policy: ContextPolicy | None = None,
    ) -> None:
        """校验并保存循环依赖、步数预算与可选的上下文策略。"""

        if not isinstance(llm, LLM):
            raise TypeError("llm 必须满足 LLM 协议")
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry 必须是 ToolRegistry")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int):
            raise ValueError("max_steps 必须是非负整数")
        if max_steps < 0:
            raise ValueError("max_steps 必须是非负整数")
        if context_policy is not None and not isinstance(context_policy, ContextPolicy):
            raise TypeError("context_policy 必须是 ContextPolicy")

        self._llm = llm
        self._registry = registry
        self._max_steps = max_steps
        self._context_policy = context_policy

    def run(
        self,
        task: str,
        *,
        stream: bool = False,
        on_chunk: Callable[[StreamChunk], None] | None = None,
        on_step: Callable[[TraceStep], None] | None = None,
        extra_instructions: str = "",
        plan_mode: bool = False,
        reflection_mode: bool = False,
    ) -> AgentState:
        """执行一次 ReAct 运行，并返回终态 ``AgentState``。
        默认走 ``LLM.complete`` 非流式路径，行为与 R-04 之前逐字节一致；
        ``stream=True`` 时每轮改用 ``complete_stream`` 消费增量，组装出的
        消息与非流式完全等价。``on_chunk`` 在每个增量块到达时触发；
        ``on_step`` 在每个 TraceStep 完成后触发，供 CLI 边产生边显示。
        ``extra_instructions`` 是可选的场景/任务附加指引，非空时作为
        系统提示词最后一个小节渲染（见 ``render_system_prompt``）；默认
        空字符串，输出与既有版本完全一致。

        ``plan_mode`` / ``reflection_mode`` 是 R-06 的可选模式（默认关闭，
        关闭时行为与既有版本逐字节一致）：开启 ``plan_mode`` 时任务开始
        先进入规划阶段（一次不传工具定义的模型调用，解析为 ``Plan`` 并
        计入一步预算），再进入既有循环；开启 ``reflection_mode`` 时，可
        重试的工具调用失败后会强制进入一次反思阶段（解析为
        ``Reflection`` 并计入一步预算）再继续。两种特化阶段都受
        ``max_steps`` 硬预算约束，解析失败有界重试一次。
        """

        if not isinstance(task, str):
            raise TypeError("task 必须是字符串")
        if on_chunk is not None and not callable(on_chunk):
            raise TypeError("on_chunk 必须是可调用对象")
        if on_step is not None and not callable(on_step):
            raise TypeError("on_step 必须是可调用对象")
        if not isinstance(extra_instructions, str):
            raise TypeError("extra_instructions 必须是字符串")
        if not isinstance(plan_mode, bool):
            raise TypeError("plan_mode 必须是布尔值")
        if not isinstance(reflection_mode, bool):
            raise TypeError("reflection_mode 必须是布尔值")

        tool_names = tuple(self._registry.names)
        tools = [
            tool
            for name in tool_names
            if (tool := self._registry.get(name)) is not None
        ]
        messages = [
            Message(
                role=MessageRole.SYSTEM,
                content=render_system_prompt(
                    tools,
                    extra_instructions=extra_instructions,
                    plan_mode=plan_mode,
                    reflection_mode=reflection_mode,
                ),
            ),
            Message(role=MessageRole.USER, content=task),
        ]

        state = self._rebuild_state(
            task=task,
            tool_names=tool_names,
            messages=messages,
            steps_used=0,
            trace=[],
        )

        if plan_mode:
            # 规划阶段：任务开始先让模型输出结构化计划，再进入既有循环。
            # 不传工具定义，模型只能输出文本 JSON 计划；解析失败有界重试
            # 一次，仍失败以 MODEL_OUTPUT_PARSE_ERROR 终止。
            messages, state = self._aux_phase(
                task=task,
                tool_names=tool_names,
                messages=messages,
                state=state,
                on_step=on_step,
                instruction=_PLAN_INSTRUCTION,
                allowed_kinds=frozenset({"plan"}),
                kind_label="plan",
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
            request_messages = (
                messages
                if self._context_policy is None
                else self._context_policy.prepare(messages)
            )
            if stream:
                response = collect_stream(
                    self._stream_with_callback(
                        self._llm.complete_stream(request_messages, tools=tools),
                        on_chunk,
                    )
                )
            else:
                response = self._llm.complete(request_messages, tools=tools)
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
                    _notify_step(step, on_step)
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
                    _notify_step(step, on_step)
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
                if isinstance(decision, ToolCall) and not response.tool_calls:
                    # 文本 JSON 工具调用补成原生 tool_calls 写回历史：OpenAI
                    # 兼容 API 要求 tool 消息紧跟带 tool_calls 的 assistant
                    # 消息（流式与非流式同样适用），否则下一轮请求会被拒绝。
                    messages[-1] = Message(
                        role=MessageRole.ASSISTANT,
                        content=response.content,
                        tool_calls=[decision],
                    )

            if isinstance(decision, FinalAnswer):
                step = TraceStep(
                    step_number=step_number,
                    input_summary=input_summary,
                    decision=decision,
                    duration_ms=duration_ms,
                )
                _notify_step(step, on_step)
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
                _notify_step(step, on_step)
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
            _notify_step(step, on_step)
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

            if reflection_mode and not result.is_success and not terminated:
                # 反思阶段：可重试的工具调用失败后，强制一步"总结原因 +
                # 下一步方案"再继续。不传工具定义，模型只能输出文本 JSON
                # 反思；解析失败有界重试一次，仍失败以
                # MODEL_OUTPUT_PARSE_ERROR 终止（``continue`` 后循环在
                # 顶部检查终止原因退出）。
                messages, state = self._aux_phase(
                    task=task,
                    tool_names=tool_names,
                    messages=messages,
                    state=state,
                    on_step=on_step,
                    instruction=_REFLECTION_INSTRUCTION,
                    allowed_kinds=frozenset({"reflection"}),
                    kind_label="reflection",
                )

            continue

        return state

    @staticmethod
    def _stream_with_callback(
        chunks: Iterator[StreamChunk],
        on_chunk: Callable[[StreamChunk], None] | None,
    ) -> Iterator[StreamChunk]:
        """透传流式增量，并在每个块到达时通知展示回调。"""

        for chunk in chunks:
            if on_chunk is not None:
                on_chunk(chunk)
            yield chunk

    def _aux_phase(
        self,
        *,
        task: str,
        tool_names: Sequence[str],
        messages: list[Message],
        state: AgentState,
        on_step: Callable[[TraceStep], None] | None,
        instruction: str,
        allowed_kinds: frozenset[str],
        kind_label: str,
    ) -> tuple[list[Message], AgentState]:
        """执行一次规划/反思特化阶段并返回更新后的消息与状态（R-06）。

        步骤：追加稳定指令消息 -> 一次不传工具定义的模型调用 -> 按
        ``allowed_kinds`` 受限解析 -> 成功则记录对应决策步骤；解析失败有界
        重试一次（回写稳定错误反馈、消耗一步），仍失败以
        ``MODEL_OUTPUT_PARSE_ERROR`` 终止。阶段本身受 ``max_steps`` 硬
        预算约束：预算不足时直接以 ``MAX_STEPS_EXCEEDED`` 终止，不发起
        模型调用。返回的 ``(messages, state)`` 与主循环共用同一套消息序列
        与状态重建，保证 ``steps_used == len(trace)`` 不变量。
        """

        messages.append(Message(role=MessageRole.USER, content=instruction))
        retried = False
        while True:
            if state.steps_used >= state.max_steps:
                state = self._rebuild_state(
                    task=task,
                    tool_names=tool_names,
                    messages=messages,
                    steps_used=state.steps_used,
                    trace=list(state.trace),
                    termination_reason=TerminationReason.MAX_STEPS_EXCEEDED,
                )
                return messages, state

            step_number = state.steps_used + 1
            input_summary = _summarize_input(state)
            started = time.perf_counter()
            response = self._llm.complete(messages)
            duration_ms = (time.perf_counter() - started) * 1_000.0
            messages.append(response)

            try:
                decision = parse_decision(response.content, allowed=allowed_kinds)
            except ParseError as exc:
                retryable = not retried and step_number < state.max_steps
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
                _notify_step(step, on_step)
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
                    return messages, state
                state = self._rebuild_state(
                    task=task,
                    tool_names=tool_names,
                    messages=messages,
                    steps_used=step_number,
                    trace=trace,
                )
                messages.append(_aux_parse_feedback(exc, kind_label))
                retried = True
                continue

            if not isinstance(decision, (Plan, Reflection)):
                # 类型收窄的防御分支：allowed 已限定只接受 plan/reflection，
                # 正常情况下不可达。
                raise RuntimeError("特化阶段返回了未知决策类型")

            step = TraceStep(
                step_number=step_number,
                input_summary=input_summary,
                decision=decision,
                duration_ms=duration_ms,
            )
            _notify_step(step, on_step)
            state = self._rebuild_state(
                task=task,
                tool_names=tool_names,
                messages=messages,
                steps_used=step_number,
                trace=[*state.trace, step],
            )
            return messages, state

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
