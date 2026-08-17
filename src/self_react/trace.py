"""人类可读的状态与轨迹渲染（Day 13）。

本模块只负责把 Day 12 主循环产生的 ``AgentState``（尤其是
``AgentState.trace``）渲染成稳定的中文文本：相同状态永远得到完全相同的输出，
不访问网络、不读取环境变量、不修改输入。渲染只消费领域对象里的可序列化
字段，不接触模型客户端、注册表或密钥；解析失败只展示稳定错误说明，
不展示 ``details`` 调试细节、``metadata`` 或模型原始输出。

渲染是纯函数展示层：它不做任何决策，不改变 ``AgentState``，也不修改 Day 4
领域模型、Day 12 主循环或任何工具。``LLM.complete`` 接口、DeepSeek 适配器、
提示词、解析器和工具注册表都原封不动。单步渲染 ``render_step`` 单独导出，
供 CLI ``--stream`` 与完整轨迹共用同一套决策/观察文本。
"""

from __future__ import annotations

import json

from self_react.models import (
    AgentState,
    FinalAnswer,
    Observation,
    Plan,
    Reflection,
    TerminationReason,
    ToolCall,
    ToolErrorCode,
    TraceError,
    TraceErrorCode,
    TraceStep,
)

_DURATION_PRECISION = 3
"""耗时保留的小数位数；固定精度保证相同状态渲染结果一致。"""

_TERMINATION_LABELS: dict[TerminationReason, str] = {
    TerminationReason.FINAL_ANSWER: "最终回答",
    TerminationReason.MAX_STEPS_EXCEEDED: "步数耗尽",
    TerminationReason.MODEL_OUTPUT_PARSE_ERROR: "模型输出解析失败",
    TerminationReason.UNKNOWN_TOOL: "未知工具",
    TerminationReason.TOOL_EXECUTION_ERROR: "工具执行失败",
}
"""终止原因到中文标签的固定映射。"""

_TRACE_ERROR_LABELS: dict[TraceErrorCode, str] = {
    TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR: "模型输出解析失败",
    TraceErrorCode.UNKNOWN_TOOL: "未知工具",
    TraceErrorCode.TOOL_EXECUTION_ERROR: "工具执行失败",
    TraceErrorCode.INVALID_TOOL_ARGUMENTS: "工具参数无效",
}
"""轨迹错误码到中文标签的固定映射。"""

_TOOL_ERROR_LABELS: dict[ToolErrorCode, str] = {
    ToolErrorCode.INVALID_ARGUMENTS: "参数无效",
    ToolErrorCode.UNKNOWN_TOOL: "未知工具",
    ToolErrorCode.TOOL_EXECUTION_ERROR: "工具执行失败",
    ToolErrorCode.REPEATED_ACTION: "重复动作",
    ToolErrorCode.TIMEOUT: "超时",
    ToolErrorCode.PERMISSION_DENIED: "权限不足",
}
"""工具错误码到中文标签的固定映射。"""


def _labeled(value: object, labels: dict[object, str]) -> str:
    """把枚举值渲染成"中文标签（枚举值）"的稳定文本。

    未登记的枚举值只显示原始枚举名，``None`` 显示占位符，保证渲染器在领域
    模型未来扩展时仍然不会崩溃。
    """

    if value is None:
        return "（未知）"
    name = getattr(value, "value", str(value))
    label = labels.get(value)
    if label is None:
        return str(name)
    return f"{label}（{name}）"


def _format_duration(duration_ms: float | None) -> str:
    """把耗时格式化为固定精度的中文文本。"""

    if duration_ms is None:
        return "（未记录）"
    text = f"{duration_ms:.{_DURATION_PRECISION}f}".rstrip("0").rstrip(".")
    return f"{text} 毫秒"


def _format_json(value: dict[str, object]) -> str:
    """把参数字典格式化为键排序的稳定 JSON 文本。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _render_header(state: AgentState) -> str:
    """渲染状态头部：任务、终止原因与步数预算。"""

    reason = state.termination_reason
    reason_text = (
        _labeled(reason, _TERMINATION_LABELS) if reason is not None else "（未终止）"
    )
    return "\n".join(
        [
            f"任务：{state.task}",
            f"终止原因：{reason_text}",
            f"步数：{state.steps_used} / {state.max_steps}",
        ]
    )


def _render_decision(
    decision: ToolCall | FinalAnswer | Plan | Reflection,
) -> list[str]:
    """渲染决策行：最终回答、工具调用，或 R-06 的计划/反思。"""

    if isinstance(decision, FinalAnswer):
        return [
            "决策：最终回答",
            f"回答内容：{decision.content}",
        ]
    if isinstance(decision, ToolCall):
        return [
            f"决策：调用工具 {decision.name}",
            f"调用编号：{decision.call_id}",
            f"参数：{_format_json(decision.arguments)}",
        ]
    if isinstance(decision, Plan):
        return [
            "决策：计划",
            f"计划内容：{decision.content}",
        ]
    if isinstance(decision, Reflection):
        return [
            "决策：反思",
            f"反思内容：{decision.content}",
        ]
    return ["决策：（未知）"]


def _render_observation(observation: Observation) -> list[str]:
    """渲染观察行：成功内容，或失败内容加错误码与可重试标记。"""

    status = "成功" if not observation.is_error else "失败"
    lines = [f"观察（{status}）：{observation.content}"]
    if observation.is_error:
        lines.append(f"错误码：{_labeled(observation.error_code, _TOOL_ERROR_LABELS)}")
        retryable = "是" if observation.retryable else "否"
        lines.append(f"可重试：{retryable}")
    return lines


def _render_error(error: TraceError) -> list[str]:
    """渲染轨迹错误行：稳定错误码、中文标签与面向调用方的说明。"""

    return [
        f"错误：{_labeled(error.code, _TRACE_ERROR_LABELS)}：{error.message}",
        f"可重试：{'是' if error.retryable else '否'}",
    ]


def render_step(step: TraceStep) -> str:
    """渲染一个轨迹步骤，字段顺序与领域模型一致。"""

    lines = [f"第 {step.step_number} 步"]
    if step.input_summary is None:
        lines.append("输入摘要：（无）")
    else:
        lines.append(f"输入摘要：{step.input_summary}")
    if step.decision is not None:
        lines.extend(_render_decision(step.decision))
    if step.observation is not None:
        lines.extend(_render_observation(step.observation))
    if step.error is not None:
        lines.extend(_render_error(step.error))
    lines.append(f"耗时：{_format_duration(step.duration_ms)}")
    return "\n".join(lines)


def render_trace(state: AgentState) -> str:
    """把 ``AgentState`` 渲染成稳定的人类可读中文轨迹文本。

    输出包含状态头部（任务、终止原因、步数预算）和按顺序排列的每一步：
    输入摘要、决策、观察、错误与耗时。相同状态两次调用结果完全一致；
    空轨迹只输出头部，不伪造任何步骤。渲染不修改状态、不访问网络。
    """

    if not isinstance(state, AgentState):
        raise TypeError("render_trace 只接受 AgentState")

    sections = [_render_header(state)]
    sections.extend(render_step(step) for step in state.trace)
    return "\n\n".join(sections)


__all__ = ["render_step", "render_trace"]
