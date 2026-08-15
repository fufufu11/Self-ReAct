"""R-07~R-09 日志/故障排查场景的公开行为测试。"""

from __future__ import annotations

import pytest
from pytest import CaptureFixture

from self_react.cli import main
from self_react.llm import LLM
from self_react.models import TerminationReason
from self_react.scenarios.log_troubleshooting import (
    SCENARIO_EXAMPLES,
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
