"""命令行入口的最小自动化测试。"""

from pytest import CaptureFixture

from self_react.cli import HELLO_MESSAGE, main


def test_hello_command_prints_expected_message(capsys: CaptureFixture[str]) -> None:
    """``hello`` 应返回成功状态并输出稳定信息，不依赖网络或模型。"""

    exit_code = main(["hello"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == f"{HELLO_MESSAGE}\n"
    assert captured.err == ""
