"""R-07~R-09 日志/故障排查场景的公开行为测试。"""

from __future__ import annotations

import pytest
from pytest import CaptureFixture

from self_react.cli import main
from self_react.llm import LLM
from self_react.models import TerminationReason
from self_react.scenarios.log_troubleshooting import (
    SCENARIO_EXAMPLES,
    SCENARIO_EXTRA_INSTRUCTIONS,
    build_example_llm,
    build_registry,
    run_scenario_example,
)


def test_scenario_registry_contains_expected_tools() -> None:
    """场景注册表包含五个工具，按注册顺序返回名册。"""

    registry = build_registry()

    assert registry.names == (
        "calculator",
        "file_reader",
        "log_query",
        "runbook_search",
        "final_answer",
    )


def test_scenario_extra_instructions_cover_three_failure_modes() -> None:
    """场景指引必须覆盖 R-09 真实验收的三个失败模式（day-28 §5）。"""

    assert isinstance(SCENARIO_EXTRA_INSTRUCTIONS, str)
    # 失败模式一：猜测不存在的文件名 -> 固定三个数据文件；
    assert "logs.ndjson" in SCENARIO_EXTRA_INSTRUCTIONS
    assert "runbook.ndjson" in SCENARIO_EXTRA_INSTRUCTIONS
    assert "deploys.ndjson" in SCENARIO_EXTRA_INSTRUCTIONS
    assert "路径参数只能填这三个文件名" in SCENARIO_EXTRA_INSTRUCTIONS
    # 失败模式二：把状态码当 keyword 过滤 -> 必须用 error_code；
    assert "error_code" in SCENARIO_EXTRA_INSTRUCTIONS
    assert "keyword" in SCENARIO_EXTRA_INSTRUCTIONS
    assert "只匹配 message" in SCENARIO_EXTRA_INSTRUCTIONS
    # 失败模式三：证据足够仍深挖 -> 立即输出 final_answer 止损。
    assert "final_answer" in SCENARIO_EXTRA_INSTRUCTIONS
    assert "证据足以回答时立即输出" in SCENARIO_EXTRA_INSTRUCTIONS


def test_scenario_examples_render_scenario_guidance_in_system_message() -> None:
    """三个场景示例的 system 消息都包含场景指引，且终局仍为最终回答。"""

    for name in SCENARIO_EXAMPLES:
        state = run_scenario_example(name)
        assert SCENARIO_EXTRA_INSTRUCTIONS.strip() in state.messages[0].content
        assert state.termination_reason is TerminationReason.FINAL_ANSWER


def test_scenario_examples_run_to_final_answer() -> None:
    """三个场景示例均以 FINAL_ANSWER 结束，步数与预置响应一致。"""

    for name, scenario in SCENARIO_EXAMPLES.items():
        state = run_scenario_example(name)
        assert state.termination_reason is TerminationReason.FINAL_ANSWER
        assert state.final_answer is not None
        assert state.steps_used == len(scenario.responses)
        assert state.steps_used == len(state.trace)


def test_scenario_examples_are_deterministic() -> None:
    """相同示例两次运行产生相同的决策、观察与最终回答。"""

    for name in SCENARIO_EXAMPLES:
        first = run_scenario_example(name)
        second = run_scenario_example(name)
        assert first.final_answer == second.final_answer
        for left, right in zip(first.trace, second.trace, strict=True):
            assert left.decision == right.decision
            assert left.observation == right.observation


def test_scenario_example_final_answers_are_stable() -> None:
    """三个示例的最终回答文本固定，作为回归基准。"""

    answers = {
        name: run_scenario_example(name).final_answer for name in SCENARIO_EXAMPLES
    }

    assert "79.1%" in answers["log-404-spike"].content
    assert "03:14" in answers["log-error-window"].content
    assert "无关" in answers["log-release-correlation"].content


def test_scenario_examples_tool_calls_succeed() -> None:
    """场景示例中的每个工具观察都应是成功结果，没有失败观察。"""

    for name in SCENARIO_EXAMPLES:
        state = run_scenario_example(name)
        for step in state.trace:
            if step.observation is not None:
                assert step.observation.is_error is False, name


def test_scenario_log_data_matches_expected_counts() -> None:
    """真实日志 fixture 的计数满足示例设计。"""

    registry = build_registry()
    log_query = registry.get("log_query")
    assert log_query is not None

    not_found = log_query.execute({"path": "logs.ndjson", "error_code": "404"})
    assert "匹配 736 条 / 共 931 条" in not_found

    spike_window = log_query.execute(
        {
            "path": "logs.ndjson",
            "error_code": "404",
            "time_start": "2021-12-17 03:14:00",
            "time_end": "2021-12-17 03:18:59",
        }
    )
    assert "匹配 733 条 / 共 931 条" in spike_window

    error_distribution = log_query.execute(
        {
            "path": "logs.ndjson",
            "group_by": "error_code",
        }
    )
    assert "404: 736" in error_distribution


def test_scenario_deploys_readable_via_file_reader() -> None:
    """发布记录可通过 file_reader 读取。"""

    registry = build_registry()
    file_reader = registry.get("file_reader")
    assert file_reader is not None

    content = file_reader.execute({"path": "deploys.ndjson"})

    assert "jet" in content
    assert "1.2.0" in content
    assert "2021-12-16 22:00:00" in content


def test_build_example_llm_returns_llm_protocol_adapter() -> None:
    """每个场景示例的预置响应都能构造满足 LLM 协议的 Fake LLM。"""

    for name in SCENARIO_EXAMPLES:
        assert isinstance(build_example_llm(name), LLM)


@pytest.mark.parametrize("name", sorted(SCENARIO_EXAMPLES))
def test_example_command_runs_scenario_examples(
    name: str,
    capsys: CaptureFixture[str],
) -> None:
    """``example`` 子命令支持场景示例名，并打印标题、回答与轨迹。"""

    exit_code = main(["example", name])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert f"=== 示例：{SCENARIO_EXAMPLES[name].title}（{name}） ===" in captured.out
    assert "终止原因：最终回答（FINAL_ANSWER）" in captured.out
    assert "决策：调用工具" in captured.out


def test_run_with_scenario_uses_scenario_registry(capsys: CaptureFixture[str]) -> None:
    """``run --scenario log-troubleshooting`` 用场景工具包执行任务。"""

    import json

    from self_react.llm import FakeLLM
    from self_react.models import Message, MessageRole

    def factory(model: str, max_steps: int, task: str) -> LLM:
        return FakeLLM(
            [
                Message(
                    role=MessageRole.ASSISTANT,
                    content=json.dumps(
                        {"kind": "final_answer", "content": "完成。"},
                        ensure_ascii=False,
                    ),
                )
            ]
        )

    exit_code = main(
        ["run", "任务", "--scenario", "log-troubleshooting"],
        build_llm=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "最终回答：完成。\n"
    assert captured.err == ""


def test_run_with_scenario_injects_guidance_into_system_message() -> None:
    """``run --scenario log-troubleshooting`` 把场景指引注入 system 消息。"""

    import json

    from self_react.llm import FakeLLM
    from self_react.models import Message, MessageRole

    llm = FakeLLM(
        [
            Message(
                role=MessageRole.ASSISTANT,
                content=json.dumps(
                    {"kind": "final_answer", "content": "完成。"},
                    ensure_ascii=False,
                ),
            )
        ]
    )

    exit_code = main(
        ["run", "任务", "--scenario", "log-troubleshooting"],
        build_llm=lambda model, max_steps, task: llm,
    )

    assert exit_code == 0
    assert llm.call_count == 1
    system_message = llm.calls[0][0]
    assert system_message.role is MessageRole.SYSTEM
    assert SCENARIO_EXTRA_INSTRUCTIONS.strip() in system_message.content


def test_run_without_scenario_does_not_inject_scenario_guidance(
    capsys: CaptureFixture[str],
) -> None:
    """``run`` 不指定场景时不注入场景指引，保持默认提示词。"""

    from self_react.llm import FakeLLM
    from self_react.models import Message, MessageRole

    llm = FakeLLM(
        [
            Message(
                role=MessageRole.ASSISTANT,
                content='{"kind": "final_answer", "content": "完成。"}',
            )
        ]
    )

    exit_code = main(
        ["run", "任务", "--model", "fake"],
        build_llm=lambda model, max_steps, task: llm,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert llm.call_count == 1
    assert SCENARIO_EXTRA_INSTRUCTIONS.strip() not in llm.calls[0][0].content
    assert captured.out == "最终回答：完成。\n"


def _scenario_execute(
    registry: object, name: str, arguments: dict[str, object]
) -> tuple[bool, str | None]:
    """直接执行场景注册表里的工具，返回 (是否成功, 错误消息)。"""

    from self_react.models import ToolCall, ToolErrorCode

    tool = registry.get(name)
    assert tool is not None
    call = ToolCall(call_id="call-1", name=name, arguments=arguments)
    result = registry.execute(call)
    if result.is_success:
        return True, None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True
    return False, result.error.message


def test_scenario_log_query_rejects_site_name_as_service() -> None:
    """场景硬校验：把站点名 promjet 当 service 过滤值被稳定拒绝并引导。"""

    registry = build_registry()

    ok, message = _scenario_execute(
        registry, "log_query", {"path": "logs.ndjson", "service": "promjet"}
    )

    assert ok is False
    assert message is not None
    assert "promjet" in message
    assert "主机名" in message


def test_scenario_log_query_rejects_digit_keyword() -> None:
    """场景硬校验：把状态码当 keyword 过滤被稳定拒绝并引导用 error_code。"""

    registry = build_registry()

    ok, message = _scenario_execute(
        registry, "log_query", {"path": "logs.ndjson", "keyword": "404"}
    )

    assert ok is False
    assert message is not None
    assert "error_code" in message


def test_scenario_log_query_rejects_path_outside_three_files() -> None:
    """场景硬校验：log_query 的 path 只能填三个固定数据文件名。"""

    registry = build_registry()

    ok, message = _scenario_execute(registry, "log_query", {"path": "app.log"})

    assert ok is False
    assert message is not None
    assert "logs.ndjson" in message
    assert "runbook.ndjson" in message
    assert "deploys.ndjson" in message


def test_scenario_log_query_accepts_three_data_files() -> None:
    """场景硬校验：三个固定数据文件都能被 log_query 查询。"""

    registry = build_registry()

    for path in ("logs.ndjson", "runbook.ndjson", "deploys.ndjson"):
        ok, _ = _scenario_execute(registry, "log_query", {"path": path})
        assert ok is True, path


def test_scenario_file_reader_rejects_logs_and_runbook() -> None:
    """场景硬校验：file_reader 只读发布记录，logs/runbook 被硬拒绝。"""

    registry = build_registry()

    for path in ("logs.ndjson", "runbook.ndjson"):
        ok, message = _scenario_execute(registry, "file_reader", {"path": path})
        assert ok is False, path
        assert message is not None
        assert "deploys.ndjson" in message


def test_scenario_file_reader_accepts_deploys() -> None:
    """场景硬校验：file_reader 可读发布记录 deploys.ndjson。"""

    registry = build_registry()

    ok, _ = _scenario_execute(registry, "file_reader", {"path": "deploys.ndjson"})

    assert ok is True
