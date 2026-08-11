"""Day 3 hello 与 Day 15 run 子命令的公开行为测试。

测试只通过 ``main(argv, build_llm=...)`` 与 ``build_llm`` 公开入口出题：
参数校验失败路径（未知子命令、缺任务、非法最大步数、非法模型名）直接验证
argparse 行为；``run`` 的端到端路径注入返回 Fake LLM 的工厂，不访问网络、
不依赖真实 API Key。``--show-trace`` 的输出与 ``render_trace(终态)`` 一致，
CLI 本身不复制主循环逻辑。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest
from pytest import CaptureFixture

from self_react.cli import HELLO_MESSAGE, build_llm, main
from self_react.llm import (
    LLM,
    FakeLLM,
    LLMConfigurationError,
    LLMProviderError,
    LLMProviderErrorCode,
)
from self_react.memory import SUMMARY_HEADING
from self_react.models import Message, MessageRole


def _json_message(raw: str) -> Message:
    """构造一条把原始 JSON 放在 content 里的助手消息。"""

    return Message(role=MessageRole.ASSISTANT, content=raw)


def _final_answer_json(content: str) -> Message:
    """构造一条符合 Day 10 契约的最终回答原始输出。"""

    return _json_message(
        json.dumps(
            {"kind": "final_answer", "content": content},
            ensure_ascii=False,
        )
    )


def _tool_call_json(
    call_id: str,
    name: str,
    arguments: dict[str, object],
) -> Message:
    """构造一条符合 Day 10 契约的工具调用原始输出。"""

    return _json_message(
        json.dumps(
            {
                "kind": "tool_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            },
            ensure_ascii=False,
        )
    )


def _preset_llm_factory(model: str, max_steps: int, task: str) -> LLM:
    """测试用工厂：返回带确定性预置响应的 Fake LLM，并忽略模型名。"""

    return FakeLLM(
        [
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _tool_call_json("call-2", "retrieve", {"query": "react"}),
            _final_answer_json("计算完成，并查到了 ReAct 的说明。"),
        ]
    )


class RecordingLLMFactory:
    """记录模型名的工厂替身，用于断言 CLI 把 ``--model`` 原样传给工厂。"""

    def __init__(self, responses: Sequence[Message] | None = None) -> None:
        self.requested_models: list[str] = []
        self.responses = responses
        self.created: FakeLLM | None = None

    def __call__(self, model: str, max_steps: int, task: str) -> LLM:
        self.requested_models.append(model)
        responses = list(self.responses) if self.responses is not None else []
        self.created = FakeLLM(responses)
        return self.created


def test_hello_command_prints_expected_message(capsys: CaptureFixture[str]) -> None:
    """``hello`` 应返回成功状态并输出稳定信息，不依赖网络或模型。"""

    exit_code = main(["hello"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == f"{HELLO_MESSAGE}\n"
    assert captured.err == ""


def test_unknown_command_is_rejected(capsys: CaptureFixture[str]) -> None:
    """未知子命令属于参数错误：argparse 报错并返回退出码 2。"""

    with pytest.raises(SystemExit) as caught:
        main(["unknown"])

    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err


def test_run_without_task_is_rejected(capsys: CaptureFixture[str]) -> None:
    """``run`` 缺任务属于参数错误，不会走到 LLM 工厂，也不需要 API Key。"""

    with pytest.raises(SystemExit) as caught:
        main(["run"])

    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "task" in captured.err


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "1.5", "abc"],
)
def test_run_rejects_invalid_max_steps(
    value: str,
    capsys: CaptureFixture[str],
) -> None:
    """``--max-steps`` 必须是正整数；零、负数、浮点与非数字都被拒绝。"""

    with pytest.raises(SystemExit) as caught:
        main(["run", "任务", "--max-steps", value])

    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "max-steps" in captured.err


def test_run_rejects_unknown_model(capsys: CaptureFixture[str]) -> None:
    """``--model`` 只接受登记的模型名，未知模型在参数层被拒绝。"""

    with pytest.raises(SystemExit) as caught:
        main(["run", "任务", "--model", "gpt-4"])

    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err


def test_run_end_to_end_without_trace_prints_final_answer(
    capsys: CaptureFixture[str],
) -> None:
    """``run`` 端到端：任务 -> Agent.run -> 打印最终回答，不展示轨迹。"""

    exit_code = main(
        ["run", "计算 2 + 2，并检索 react", "--max-steps", "5"],
        build_llm=_preset_llm_factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "最终回答：计算完成，并查到了 ReAct 的说明。\n"
    assert captured.err == ""
    assert "第 1 步" not in captured.out


def test_run_end_to_end_with_trace_prints_rendered_trace(
    capsys: CaptureFixture[str],
) -> None:
    """``--show-trace`` 输出与 ``render_trace`` 的结构一致。

    耗时由 ``perf_counter`` 实测，两次运行可能有亚毫秒差异，因此不逐字比对
    整段文本；断言最终回答、空行与轨迹的行内容按 ``render_trace`` 的固定
    顺序出现，且每条轨迹行的文案与渲染层一致。
    """

    factory = RecordingLLMFactory(
        [
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _tool_call_json("call-2", "retrieve", {"query": "react"}),
            _final_answer_json("计算完成，并查到了 ReAct 的说明。"),
        ]
    )

    exit_code = main(
        ["run", "计算 2 + 2，并检索 react", "--model", "fake", "--show-trace"],
        build_llm=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert factory.requested_models == ["fake"]
    assert factory.created is not None
    lines = captured.out.splitlines()
    assert lines[0] == "最终回答：计算完成，并查到了 ReAct 的说明。"
    assert lines[1] == ""
    text = "\n".join(lines[2:])
    assert text.startswith("任务：计算 2 + 2，并检索 react")
    assert "终止原因：最终回答（FINAL_ANSWER）" in text
    assert "步数：3 / 5" in text
    positions = [
        text.index("第 1 步"),
        text.index("第 2 步"),
        text.index("第 3 步"),
    ]
    assert positions == sorted(positions)
    assert "决策：调用工具 calculator" in text
    assert "观察（成功）：4" in text
    assert "决策：调用工具 retrieve" in text
    assert "决策：最终回答" in text
    assert "回答内容：计算完成，并查到了 ReAct 的说明。" in text


def test_run_forwards_model_and_max_steps_to_factory(
    capsys: CaptureFixture[str],
) -> None:
    """CLI 把 ``--model`` 与 ``--max-steps`` 原样交给 LLM 工厂与 Agent。"""

    factory = RecordingLLMFactory([_final_answer_json("完成。")])

    exit_code = main(
        ["run", "任务", "--model", "deepseek", "--max-steps", "3"],
        build_llm=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert factory.requested_models == ["deepseek"]
    assert captured.out == "最终回答：完成。\n"


@pytest.mark.parametrize("model", ["fake", "deepseek", "openai"])
def test_run_accepts_all_registered_model_choices(
    model: str,
    capsys: CaptureFixture[str],
) -> None:
    """注册表里的每个模型名都是合法选项，并原样透传给工厂。"""

    factory = RecordingLLMFactory([_final_answer_json("完成。")])

    exit_code = main(
        ["run", "任务", "--model", model],
        build_llm=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert factory.requested_models == [model]
    assert captured.out == "最终回答：完成。\n"


def test_run_without_show_trace_flag_does_not_print_trace(
    capsys: CaptureFixture[str],
) -> None:
    """默认不展示轨迹：最终回答行之后没有任何轨迹文本。"""

    exit_code = main(
        ["run", "任务"],
        build_llm=_preset_llm_factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "最终回答：计算完成，并查到了 ReAct 的说明。\n"
    assert "终止原因" not in captured.out


class MissingKeyFactory:
    """模拟真实 DeepSeek 工厂缺少 API Key 时的配置错误。"""

    def __call__(self, model: str, max_steps: int, task: str) -> LLM:
        raise LLMConfigurationError("缺少 DEEPSEEK_API_KEY")


def test_run_reports_configuration_error_without_traceback(
    capsys: CaptureFixture[str],
) -> None:
    """配置缺失是稳定错误路径：打印一行说明并返回非零退出码。"""

    exit_code = main(
        ["run", "任务"],
        build_llm=MissingKeyFactory(),
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "缺少 DEEPSEEK_API_KEY" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


class TimeoutFactory:
    """模拟真实模型调用时的稳定供应商错误。"""

    def __call__(self, model: str, max_steps: int, task: str) -> LLM:
        raise LLMProviderError(
            code=LLMProviderErrorCode.TIMEOUT,
            message="DeepSeek 请求失败（TIMEOUT）",
        )


def test_run_reports_provider_error_without_traceback(
    capsys: CaptureFixture[str],
) -> None:
    """供应商错误按原样转成一行稳定说明，不泄漏堆栈。"""

    exit_code = main(
        ["run", "任务"],
        build_llm=TimeoutFactory(),
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "TIMEOUT" in captured.err
    assert "Traceback" not in captured.err


def test_build_llm_fake_returns_deterministic_llm() -> None:
    """``build_llm("fake", ...)`` 返回满足 LLM 协议的确定性适配器。"""

    llm = build_llm("fake", max_steps=5, task="计算 2 + 2")
    assert isinstance(llm, LLM)


def test_build_llm_deepseek_missing_key_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_llm("deepseek", ...)`` 无密钥时抛稳定配置错误。"""

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(LLMConfigurationError):
        build_llm("deepseek", max_steps=5, task="任务")


def test_build_llm_openai_missing_key_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_llm("openai", ...)`` 无密钥时抛稳定配置错误。"""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMConfigurationError):
        build_llm("openai", max_steps=5, task="任务")


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc"])
def test_run_rejects_invalid_context_window(
    value: str,
    capsys: CaptureFixture[str],
) -> None:
    """``--context-window`` 必须是正整数；零、负数、浮点与非数字被拒绝。"""

    with pytest.raises(SystemExit) as caught:
        main(["run", "任务", "--context-window", value])

    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "context-window" in captured.err


def test_run_with_small_context_window_trims_request_and_keeps_result(
    capsys: CaptureFixture[str],
) -> None:
    """CLI 传入小窗口时模型请求出现摘要 system 消息，最终回答不受影响。"""

    factory = RecordingLLMFactory(
        [
            _tool_call_json("call-1", "calculator", {"expression": "2 + 2"}),
            _tool_call_json("call-2", "retrieve", {"query": "react"}),
            _final_answer_json("计算完成，并查到了 ReAct 的说明。"),
        ]
    )

    exit_code = main(
        ["run", "计算 2 + 2，并检索 react", "--context-window", "50"],
        build_llm=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "最终回答：计算完成，并查到了 ReAct 的说明。\n"
    assert factory.created is not None
    summary_calls = [
        call
        for call in factory.created.calls
        if len(call) >= 2
        and call[1].role is MessageRole.SYSTEM
        and SUMMARY_HEADING in call[1].content
    ]
    assert summary_calls


def test_run_default_context_window_keeps_short_context_unchanged(
    capsys: CaptureFixture[str],
) -> None:
    """默认 20,000 字符窗口下，短任务请求不含摘要（既有行为不变）。"""

    factory = RecordingLLMFactory([_final_answer_json("完成。")])

    exit_code = main(["run", "任务"], build_llm=factory)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "最终回答：完成。\n"
    assert factory.created is not None
    for call in factory.created.calls:
        assert not any(SUMMARY_HEADING in message.content for message in call)


def test_run_stream_prints_only_final_answer(
    capsys: CaptureFixture[str],
) -> None:
    """--stream 只输出最终回答文本：不打印步骤、决策/观察或原始 JSON。"""

    exit_code = main(
        ["run", "计算 2 + 2，并检索 react", "--stream"],
        build_llm=_preset_llm_factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out == "计算完成，并查到了 ReAct 的说明。\n"
    assert "第 1 步" not in captured.out
    assert "决策：" not in captured.out
    assert "观察：" not in captured.out
    assert '{"kind"' not in captured.out


def test_run_stream_with_show_trace_prints_full_trace(
    capsys: CaptureFixture[str],
) -> None:
    """--stream 与 --show-trace 可共存：结束时仍打印完整渲染轨迹。"""

    exit_code = main(
        ["run", "计算 2 + 2", "--stream", "--show-trace"],
        build_llm=_preset_llm_factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.startswith("计算完成，并查到了 ReAct 的说明。\n\n")
    assert "任务：计算 2 + 2" in captured.out
    assert "终止原因：最终回答（FINAL_ANSWER）" in captured.out
    assert "步数：3 / 5" in captured.out


def test_run_without_stream_does_not_print_steps(
    capsys: CaptureFixture[str],
) -> None:
    """默认不流式：不打印任何步骤文本，保持既有输出。"""

    exit_code = main(["run", "任务"], build_llm=_preset_llm_factory)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "第 1 步" not in captured.out
    assert "决策：" not in captured.out
