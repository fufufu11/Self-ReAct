"""Day 16 端到端示例的公开行为测试。

测试只通过 ``self_react.examples`` 公开入口与 ``main(["example", ...])``
CLI 入口出题：三个示例（单工具、多工具、工具失败后恢复）使用 Fake LLM 与
确定性工具，不访问网络、不依赖真实 API Key；相同输入得到相同决策与观察。
示例只组合已有模块，不复制主循环逻辑，``Agent`` 仍是唯一控制者。
"""

from __future__ import annotations

import pytest
from pytest import CaptureFixture

from self_react.cli import main
from self_react.examples import EXAMPLES, build_example_llm, run_example
from self_react.llm import LLM
from self_react.models import (
    MessageRole,
    Plan,
    Reflection,
    TerminationReason,
    ToolErrorCode,
)
from self_react.tools.retrieve import KNOWLEDGE_BASE


def test_examples_defines_five_fixed_scenarios() -> None:
    """示例表恰好包含五条主线，且每个示例都有稳定名称、标题与任务。"""

    assert sorted(EXAMPLES) == [
        "failure-recovery",
        "multi-tool",
        "plan-demo",
        "reflection-demo",
        "single-tool",
    ]
    assert EXAMPLES["single-tool"].title == "单工具"
    assert EXAMPLES["multi-tool"].title == "多工具"
    assert EXAMPLES["failure-recovery"].title == "工具失败后恢复"
    assert EXAMPLES["plan-demo"].title == "先规划后执行"
    assert EXAMPLES["reflection-demo"].title == "失败后反思"
    assert EXAMPLES["single-tool"].task == "计算 2 + 2"
    assert EXAMPLES["multi-tool"].task == "计算 2 + 2，并检索 react 主题"
    assert EXAMPLES["plan-demo"].plan_mode is True
    assert EXAMPLES["reflection-demo"].reflection_mode is True
    assert EXAMPLES["single-tool"].plan_mode is False
    assert EXAMPLES["single-tool"].reflection_mode is False


def test_build_example_llm_returns_llm_protocol_adapter() -> None:
    """每个示例的预置响应都能构造满足 LLM 协议的 Fake LLM。"""

    for name in EXAMPLES:
        llm = build_example_llm(name)
        assert isinstance(llm, LLM)


def test_single_tool_example_runs_calculator_then_answers() -> None:
    """单工具示例：只调用计算器，得到观察 4，然后最终回答。"""

    state = run_example("single-tool")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.final_answer is not None
    assert state.final_answer.content == "2 + 2 = 4。"
    assert state.steps_used == 2
    assert state.steps_used == len(state.trace)

    tool_step = state.trace[0]
    assert tool_step.decision is not None
    assert tool_step.decision.kind == "tool_call"
    assert tool_step.decision.name == "calculator"
    assert tool_step.observation is not None
    assert tool_step.observation.is_error is False
    assert tool_step.observation.content == "4"

    final_step = state.trace[1]
    assert final_step.decision is not None
    assert final_step.decision.kind == "final_answer"


def test_multi_tool_example_runs_calculator_then_retrieve() -> None:
    """多工具示例：依次调用计算器与检索，两条成功观察写回上下文。"""

    state = run_example("multi-tool")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 3
    assert state.final_answer is not None
    assert "4" in state.final_answer.content
    assert "ReAct" in state.final_answer.content

    observations = [step.observation for step in state.trace[:2]]
    assert observations[0] is not None and observations[0].content == "4"
    assert observations[1] is not None
    assert observations[1].content == KNOWLEDGE_BASE["react"]

    tool_messages = [
        message for message in state.messages if message.role is MessageRole.TOOL
    ]
    assert [message.tool_call_id for message in tool_messages] == ["call-1", "call-2"]
    assert state.available_tools == [
        "calculator",
        "file_reader",
        "retrieve",
        "final_answer",
    ]


def test_failure_recovery_example_recovers_after_retryable_failure() -> None:
    """工具失败后恢复示例：首次检索失败作为可恢复观察，换主题后成功。"""

    state = run_example("failure-recovery")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 3
    assert state.final_answer is not None
    assert "react" in state.final_answer.content

    first = state.trace[0].observation
    assert first is not None
    assert first.is_error is True
    assert first.error_code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert first.retryable is True
    assert "unknown-topic" in first.content

    second = state.trace[1].observation
    assert second is not None
    assert second.is_error is False
    assert second.content == KNOWLEDGE_BASE["react"]
    assert state.trace[1].decision is not None
    assert state.trace[1].decision.kind == "tool_call"
    assert state.trace[1].decision.name == "retrieve"


def test_examples_are_deterministic() -> None:
    """相同示例两次运行产生相同的决策、观察与错误（耗时除外）。"""

    for name in EXAMPLES:
        first = run_example(name)
        second = run_example(name)
        for left, right in zip(first.trace, second.trace, strict=True):
            assert left.decision == right.decision
            assert left.observation == right.observation
            assert left.error == right.error


@pytest.mark.parametrize(
    ("name", "title", "final_answer"),
    [
        ("single-tool", "单工具", "2 + 2 = 4。"),
        (
            "multi-tool",
            "多工具",
            "计算结果是 4；ReAct 是一种让模型推理与行动交错的智能体范式。",
        ),
        (
            "failure-recovery",
            "工具失败后恢复",
            "第一次检索失败后改用 react，成功找到 ReAct 的说明。",
        ),
        (
            "plan-demo",
            "先规划后执行",
            "计算结果是 4；ReAct 是一种让模型推理与行动交错的智能体范式。",
        ),
        (
            "reflection-demo",
            "失败后反思",
            "第一次检索失败后反思原因，改用 react 成功找到 ReAct 的说明。",
        ),
    ],
)
def test_example_command_prints_answer_and_trace(
    name: str,
    title: str,
    final_answer: str,
    capsys: CaptureFixture[str],
) -> None:
    """``example`` 子命令打印示例标题、最终回答与完整人类可读轨迹。"""

    exit_code = main(["example", name])

    captured = capsys.readouterr()
    assert exit_code == 0
    lines = captured.out.splitlines()
    assert lines[0] == f"=== 示例：{title}（{name}） ==="
    assert lines[1] == f"最终回答：{final_answer}"
    assert lines[2] == ""
    text = "\n".join(lines[3:])
    assert text.startswith("任务：")
    assert "终止原因：最终回答（FINAL_ANSWER）" in text
    assert "第 1 步" in text
    assert "决策：调用工具" in text
    assert captured.err == ""


def test_plan_demo_trace_contains_plan_step() -> None:
    """plan-demo 的轨迹第一步是计划，且计划计入步数预算。"""

    state = run_example("plan-demo")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 4
    assert isinstance(state.trace[0].decision, Plan)
    assert state.trace[0].decision.kind == "plan"
    assert "计算器" in state.trace[0].decision.content


def test_reflection_demo_trace_contains_reflection_step() -> None:
    """reflection-demo 的轨迹在失败后包含反思步骤。"""

    state = run_example("reflection-demo")

    assert state.termination_reason is TerminationReason.FINAL_ANSWER
    assert state.steps_used == 4
    assert state.trace[0].decision is not None
    assert state.trace[0].decision.kind == "tool_call"
    assert isinstance(state.trace[1].decision, Reflection)
    assert state.trace[1].decision.kind == "reflection"
    assert "react" in state.trace[1].decision.content


def test_example_command_rejects_unknown_name(capsys: CaptureFixture[str]) -> None:
    """未知示例名属于参数错误：argparse 拒绝并返回退出码 2。"""

    with pytest.raises(SystemExit) as caught:
        main(["example", "unknown"])

    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err


def test_example_command_does_not_require_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """``example`` 子命令不读取 DEEPSEEK_API_KEY，离线即可运行。"""

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    exit_code = main(["example", "single-tool"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "最终回答：2 + 2 = 4。" in captured.out
    assert "Traceback" not in captured.out
    assert captured.err == ""
