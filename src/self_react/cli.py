"""Self-ReAct 的最小命令行入口。

本模块故意只提供 ``hello`` 命令，用于验证 ``uv``、打包安装、命令行入口
和测试工具之间的整条链路。它不读取环境变量、不请求网络，也不接触模型；
因此后续接入真实能力时，可以把这条确定性的基线作为环境健康检查。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

HELLO_MESSAGE = "Hello from Self-ReAct!"
"""``hello`` 命令的固定输出，作为 CLI 和测试共享的明确契约。"""


def _create_parser() -> argparse.ArgumentParser:
    """创建命令参数解析器。

    解析器集中在这里而不是写进 :func:`main`，让入口函数只负责连接
    ``self-react`` 脚本、参数解析和具体命令。随着后续增加 agent 命令，新的
    子命令可在此处登记，而不会改变当前 ``hello`` 的行为。
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行命令行入口，并返回适合进程退出状态的整数。

    参数 ``argv`` 默认使用实际命令行参数；测试传入列表即可在同一 Python
    进程中覆盖命令分派，不需要启动子进程。打包后，``pyproject.toml`` 中的
    ``self-react = self_react.cli:main`` 会调用本函数，再由这里把输出写到
    标准输出。
    """

    arguments = _create_parser().parse_args(argv)
    if arguments.command == "hello":
        print(HELLO_MESSAGE)
        return 0

    # ``argparse`` 已保证 command 只能是已登记子命令；保留防御分支，使未来
    # 新增命令却遗漏实现时能得到非零退出码，而不是静默成功。
    return 2
