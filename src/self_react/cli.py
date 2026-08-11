"""Self-ReAct 的最小命令行入口（Day 3 的 hello 基线 + Day 15 的 run 子命令）。

``hello`` 命令用于验证 ``uv``、打包安装、命令行入口和测试工具之间的整条
链路，行为保持不变。``run`` 命令负责把任务交给 Day 12 的 ``Agent`` 执行：
CLI 只做参数解析、组装工具注册表与模型适配器、把终态结果打印给人看，不复制
主循环逻辑——``Agent`` 仍是唯一的循环控制者。``--show-trace`` 时打印的文本
与 Day 13 的 ``render_trace`` 完全一致；``--stream`` 时每个步骤完成即打印
``render_step`` 的可读文本，最终答案随后输出。

CLI 默认不要求真实 API Key 即可运行：``--help``、参数校验错误路径和
``--model fake`` 的确定性演示都不会读取任何 API Key；只有真正选择
``--model deepseek`` 或 ``--model openai`` 并发起运行时才构造真实适配器，
配置缺失会得到一行稳定说明与非零退出码，而不是堆栈。模型适配器由
``providers`` 注册表按 ``--model`` 选择，新增供应商只需注册 provider，
不需要改 CLI 分支。自动化测试通过 ``build_llm`` 参数注入返回 Fake LLM
的工厂，不访问网络。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Callable

from self_react.agent import Agent
from self_react.examples import EXAMPLES, run_example
from self_react.llm import LLM, LLMError, LLMProviderError, StreamChunk
from self_react.memory import DEFAULT_CONTEXT_WINDOW, ContextPolicy
from self_react.models import TerminationReason
from self_react.providers import available_providers, create_provider
from self_react.tools import (
    CalculatorTool,
    FileReaderTool,
    FinalAnswerTool,
    RetrieveTool,
    ToolRegistry,
)
from self_react.trace import render_trace

HELLO_MESSAGE = "Hello from Self-ReAct!"
"""``hello`` 命令的固定输出，作为 CLI 和测试共享的明确契约。"""

DEFAULT_MAX_STEPS = 5
"""``run`` 子命令的默认最大步数。"""

_MODEL_CHOICES = available_providers()
"""``--model`` 可选的模型名：来自 provider 注册表（按名称排序）。"""

_TERMINATION_LABELS: dict[TerminationReason, str] = {
    TerminationReason.FINAL_ANSWER: "最终回答",
    TerminationReason.MAX_STEPS_EXCEEDED: "步数耗尽",
    TerminationReason.MODEL_OUTPUT_PARSE_ERROR: "模型输出解析失败",
    TerminationReason.UNKNOWN_TOOL: "未知工具",
    TerminationReason.TOOL_EXECUTION_ERROR: "工具执行失败",
}
"""CLI 对非最终回答终止原因的中文标签，与 Day 13 渲染层保持一致。"""

_JSON_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
"""JSON 字符串转义到实际字符的映射（不含 \\u，单独处理）。"""


class _FinalAnswerStreamRenderer:
    """从流式增量中实时提取 ``final_answer`` 的 ``content`` 并逐字打印。

    只消费内容增量（``StreamChunk.content``）；工具调用轮次、解析重试等
    中间响应不打印任何内容。无法实时提取时（例如原生 ``final_answer``
    工具调用），由 :meth:`finish` 在运行结束后补齐最终回答文本。
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._pos = 0
        self._in_string = False
        self._escaped = False
        self._unicode_hex: list[str] = []
        self._depth = 0
        self._after_colon = False
        self._current = ""
        self._last_key: str | None = None
        self._is_final = False
        self._printing = False
        self._printed = ""

    def __call__(self, chunk: StreamChunk) -> None:
        """消费一个增量块，并尽可能实时打印最终回答内容。"""

        if not chunk.content:
            return
        self._buffer += chunk.content
        self._scan()

    def finish(self, expected: str) -> None:
        """运行结束后补齐未被实时打印的剩余部分，保证最终输出完整。"""

        if self._printed == expected:
            return
        if expected.startswith(self._printed):
            sys.stdout.write(expected[len(self._printed) :])
        else:
            sys.stdout.write(expected)
        sys.stdout.flush()
        self._printed = expected

    def _scan(self) -> None:
        """逐字符推进：识别 JSON 结构，定位并打印 final_answer 的 content。"""

        buf = self._buffer
        while self._pos < len(buf):
            ch = buf[self._pos]
            self._pos += 1
            if self._printing:
                self._feed_content(ch)
                continue
            if self._unicode_hex:
                self._unicode_hex.append(ch)
                if len(self._unicode_hex) == 4:
                    code = "".join(self._unicode_hex)
                    self._unicode_hex.clear()
                    try:
                        text = chr(int(code, 16))
                    except ValueError:
                        text = "\\u" + code
                    self._write(text)
                continue
            if self._escaped:
                self._escaped = False
                continue
            if self._in_string:
                if ch == "\\":
                    self._escaped = True
                elif ch == '"':
                    self._in_string = False
                    self._on_string_end()
                else:
                    self._current += ch
                continue
            if ch == '"':
                self._in_string = True
                self._current = ""
                if self._after_colon and self._is_final and self._last_key == "content":
                    self._printing = True
            elif ch == ":":
                self._after_colon = True
            elif ch == "{":
                if self._depth == 0:
                    self._reset_response()
                self._depth += 1
                self._after_colon = False
            elif ch == "}":
                if self._depth > 0:
                    self._depth -= 1
                    if self._depth == 0:
                        self._reset_response()
                self._after_colon = False
            elif ch == ",":
                self._after_colon = False

    def _feed_content(self, ch: str) -> None:
        """打印 content 字符串值里的字符，含转义与 \\uXXXX 处理。"""

        if self._unicode_hex:
            self._unicode_hex.append(ch)
            if len(self._unicode_hex) == 4:
                code = "".join(self._unicode_hex)
                self._unicode_hex.clear()
                try:
                    text = chr(int(code, 16))
                except ValueError:
                    text = "\\u" + code
                self._write(text)
            return
        if self._escaped:
            self._escaped = False
            if ch == "u":
                self._unicode_hex = []
            else:
                self._write(_JSON_ESCAPES.get(ch, ch))
            return
        if ch == "\\":
            self._escaped = True
            return
        if ch == '"':
            self._printing = False
            self._in_string = False
            self._last_key = None
            self._after_colon = False
            return
        self._write(ch)

    def _on_string_end(self) -> None:
        """普通字符串结束：区分 key 与 value，更新 kind/content 状态。"""

        value = self._current
        if self._after_colon:
            self._after_colon = False
            if self._last_key == "kind":
                self._is_final = value == "final_answer"
            self._last_key = None
        else:
            self._last_key = value

    def _reset_response(self) -> None:
        """响应结束或新响应开始时清空单轮解析状态（保留已打印文本）。"""

        self._in_string = False
        self._escaped = False
        self._unicode_hex = []
        self._after_colon = False
        self._current = ""
        self._last_key = None
        self._is_final = False
        self._printing = False

    def _write(self, text: str) -> None:
        self._printed += text
        sys.stdout.write(text)
        sys.stdout.flush()


def build_llm(model: str, max_steps: int, task: str) -> LLM:
    """默认模型工厂：把 ``--model`` 变成可用的 LLM 适配器。

    委托给 ``providers.create_provider``：按模型名查 provider 注册表并构造
    适配器（``deepseek`` / ``openai`` 密钥缺失时抛稳定配置错误；``fake``
    构造确定性离线演示适配器）。工厂在参数校验通过后才被调用，因此
    ``--help`` 与错误路径不会读取 API Key。``max_steps`` 与 ``task`` 保留在
    签名中，供测试工厂断言 CLI 是否正确传递参数；注册的 provider 工厂
    目前不消费它们。
    """

    return create_provider(model)


BuildLLM = Callable[[str, int, str], LLM]
"""模型工厂的公开形态：``(model, max_steps, task) -> LLM``。"""


def _positive_int(value: str) -> int:
    """把 ``--max-steps`` 解析成正整数；非法值由 argparse 显示错误。"""

    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("必须是正整数") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def _build_registry() -> ToolRegistry:
    """构造 ``run`` 使用的默认工具注册表：三个确定性业务工具。"""

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(FileReaderTool(root_directory="C:/allowed"))
    registry.register(RetrieveTool())
    registry.register(FinalAnswerTool())
    return registry


def _create_parser() -> argparse.ArgumentParser:
    """创建命令参数解析器。

    解析器集中在这里而不是写进 :func:`main`，让入口函数只负责连接
    ``self-react`` 脚本、参数解析和具体命令。``run`` 子命令在此登记，
    而不会改变当前 ``hello`` 的行为。
    """

    parser = argparse.ArgumentParser(
        prog="self-react",
        description="Self-ReAct 的命令行工具。",
    )
    subcommands = parser.add_subparsers(
        dest="command",
        required=True,
        help="要执行的命令。",
    )
    subcommands.add_parser(
        "hello",
        help="输出用于验证项目环境的确定性问候信息。",
    )
    run_parser = subcommands.add_parser(
        "run",
        help="用 ReAct 智能体执行一次任务。",
    )
    run_parser.add_argument(
        "task",
        help='要执行的任务文本，例如 "计算 2 + 2"。',
    )
    run_parser.add_argument(
        "--model",
        choices=_MODEL_CHOICES,
        default="deepseek",
        help=(
            "模型适配器：deepseek（真实 DeepSeek API）、"
            "openai（真实 OpenAI API）或 fake（确定性离线演示）。"
        ),
    )
    run_parser.add_argument(
        "--max-steps",
        type=_positive_int,
        default=DEFAULT_MAX_STEPS,
        metavar="N",
        help="最大决策步数（正整数），默认 5。",
    )
    run_parser.add_argument(
        "--context-window",
        type=_positive_int,
        default=DEFAULT_CONTEXT_WINDOW,
        metavar="N",
        help=(
            "上下文窗口（字符数，正整数），默认 20000；超过后自动按整轮"
            "裁剪旧历史并回填规则式摘要（Claude auto-compact 风格）。"
        ),
    )
    run_parser.add_argument(
        "--show-trace",
        dest="show_trace",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="是否打印人类可读执行轨迹；默认不打印（可用 --no-show-trace 显式关闭）。",
    )
    run_parser.add_argument(
        "--stream",
        action="store_true",
        default=False,
        help=(
            "内部走流式调用，只输出最终回答文本；不打印逐步轨迹，"
            "默认关闭，不影响既有输出。"
        ),
    )
    example_parser = subcommands.add_parser(
        "example",
        help="运行 Day 16 确定性端到端示例（无需网络与 API Key）。",
    )
    example_parser.add_argument(
        "name",
        choices=sorted(EXAMPLES),
        metavar="NAME",
        help=(
            "示例名称：single-tool（单工具）、multi-tool（多工具）、"
            "failure-recovery（工具失败后恢复）。"
        ),
    )
    return parser


def _run_command(arguments: argparse.Namespace, build_llm: BuildLLM) -> int:
    """执行 ``run`` 子命令：组装依赖、运行 Agent、打印结果。"""

    try:
        llm = build_llm(arguments.model, arguments.max_steps, arguments.task)
    except LLMError as exc:
        print(f"模型配置失败：{exc}", file=sys.stderr)
        return 2

    registry = _build_registry()
    agent = Agent(
        llm=llm,
        registry=registry,
        max_steps=arguments.max_steps,
        context_policy=ContextPolicy(context_window=arguments.context_window),
    )
    try:
        if arguments.stream:
            renderer = _FinalAnswerStreamRenderer()
            state = agent.run(arguments.task, stream=True, on_chunk=renderer)
        else:
            state = agent.run(arguments.task)
    except LLMProviderError as exc:
        print(f"模型调用失败：{exc}", file=sys.stderr)
        return 3

    if state.final_answer is not None:
        if arguments.stream:
            renderer.finish(state.final_answer.content)
            print()
        else:
            print(f"最终回答：{state.final_answer.content}")
    elif state.termination_reason is not None:
        label = _TERMINATION_LABELS.get(
            state.termination_reason,
            state.termination_reason.value,
        )
        print(f"运行终止（{label}），没有最终回答。")
    else:
        print("运行未终止，且没有最终回答。")

    if arguments.show_trace:
        print()
        print(render_trace(state))
    return 0


def _example_command(arguments: argparse.Namespace) -> int:
    """执行 ``example`` 子命令：运行确定性端到端示例并打印轨迹。

    示例是 Day 16 的可复现演示：任务、工具序列与最终回答全部固定，使用
    Fake LLM 与确定性工具，不访问网络、不依赖 API Key。命令始终打印
    最终回答与 ``render_trace`` 的完整人类可读轨迹，让三条主线
    （单工具、多工具、工具失败后恢复）可以离线复现。
    """

    scenario = EXAMPLES[arguments.name]
    state = run_example(arguments.name)

    print(f"=== 示例：{scenario.title}（{scenario.name}） ===")
    if state.final_answer is not None:
        print(f"最终回答：{state.final_answer.content}")
    elif state.termination_reason is not None:
        label = _TERMINATION_LABELS.get(
            state.termination_reason,
            state.termination_reason.value,
        )
        print(f"运行终止（{label}），没有最终回答。")
    else:
        print("运行未终止，且没有最终回答。")
    print()
    print(render_trace(state))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    build_llm: BuildLLM = build_llm,
) -> int:
    """执行命令行入口，并返回适合进程退出状态的整数。

    参数 ``argv`` 默认使用实际命令行参数；测试传入列表即可在同一 Python
    进程中覆盖命令分派，不需要启动子进程。``build_llm`` 是模型工厂注入点：
    生产默认构造 DeepSeek 适配器，测试注入返回 Fake LLM 的工厂，因此
    自动化测试不访问网络、不依赖真实 API Key。打包后，
    ``pyproject.toml`` 中的 ``self-react = self_react.cli:main`` 会调用本
    函数，再由这里把输出写到标准输出。
    """

    arguments = _create_parser().parse_args(argv)
    if arguments.command == "hello":
        print(HELLO_MESSAGE)
        return 0
    if arguments.command == "run":
        return _run_command(arguments, build_llm)
    if arguments.command == "example":
        return _example_command(arguments)

    # ``argparse`` 已保证 command 只能是已登记子命令；保留防御分支，使未来
    # 新增命令却遗漏实现时能得到非零退出码，而不是静默成功。
    return 2
