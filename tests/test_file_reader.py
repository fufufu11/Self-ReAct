"""Day 9 受限文件读取工具的公开行为测试。

测试通过 ``ToolRegistry`` 与领域 ``ToolCall`` 驱动 ``FileReaderTool``，
覆盖成功读取、路径越界、文件不存在、参数校验、截断策略、符号链接逃逸、
调用编号关联和注册表集成。所有文件都写在 pytest 的临时目录里，不访问
网络或真实 API。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from self_react.llm import FakeLLM
from self_react.models import (
    Message,
    MessageRole,
    Observation,
    ToolCall,
    ToolErrorCode,
    ToolResultStatus,
)
from self_react.tools import (
    CalculatorTool,
    FileReaderTool,
    RetrieveTool,
    Tool,
    ToolArgumentError,
    ToolRegistrationError,
    ToolRegistry,
)
from self_react.tools.file_reader import (
    MAX_OUTPUT_CHARS,
    MAX_PATH_LENGTH,
    TRUNCATION_MARKER,
)


def _registry_with_reader(root: Path) -> ToolRegistry:
    """创建并注册文件读取工具的标准测试注册表。"""

    registry = ToolRegistry()
    registry.register(FileReaderTool(root))
    return registry


def test_file_reader_reads_file_in_nested_relative_path(tmp_path: Path) -> None:
    """合法相对路径返回文件内容，调用编号与工具名保持一致。"""

    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "todo.txt").write_text("买牛奶", encoding="utf-8")
    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "notes/todo.txt"},
        )
    )

    assert result.is_success is True
    assert result.status is ToolResultStatus.SUCCESS
    assert result.content == "买牛奶"
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "file_reader"


def test_file_reader_reads_file_at_root_level(tmp_path: Path) -> None:
    """根目录下的文件用单段相对路径即可读取。"""

    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "readme.txt"},
        )
    )

    assert result.is_success is True
    assert result.content == "hello"


def test_file_reader_empty_file_returns_empty_content(tmp_path: Path) -> None:
    """空文件返回空字符串，仍然是一次成功结果。"""

    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "empty.txt"},
        )
    )

    assert result.is_success is True
    assert result.content == ""


def test_file_reader_truncates_overlong_content(tmp_path: Path) -> None:
    """内容超过输出上限时返回截断文本并附加截断标记。"""

    payload = "x" * (MAX_OUTPUT_CHARS + 10)
    (tmp_path / "big.txt").write_text(payload, encoding="utf-8")
    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "big.txt"},
        )
    )

    assert result.is_success is True
    assert result.content == "x" * MAX_OUTPUT_CHARS + TRUNCATION_MARKER
    assert len(result.content) == MAX_OUTPUT_CHARS + len(TRUNCATION_MARKER)


def test_file_reader_content_at_limit_is_not_truncated(tmp_path: Path) -> None:
    """恰好等于上限的内容原样返回，不附加截断标记。"""

    payload = "y" * MAX_OUTPUT_CHARS
    (tmp_path / "edge.txt").write_text(payload, encoding="utf-8")
    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "edge.txt"},
        )
    )

    assert result.is_success is True
    assert result.content == payload
    assert TRUNCATION_MARKER not in result.content


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "a/../b.txt",
        "sub/../../secret.txt",
        "..\\secret.txt",
        "sub/..\\secret.txt",
        "..",
    ],
)
def test_file_reader_rejects_parent_traversal_syntactically(
    tmp_path: Path,
    path: str,
) -> None:
    """包含 .. 组件的路径属于参数问题，返回 INVALID_ARGUMENTS。"""

    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(call_id="call-1", name="file_reader", arguments={"path": path})
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True
    assert ".." in result.error.message


@pytest.mark.parametrize(
    "path",
    [
        "C:\\Windows\\win.ini",
        "\\\\server\\share\\file.txt",
        "C:relative.txt",
    ],
)
@pytest.mark.skipif(os.name != "nt", reason="盘符/UNC 路径检查仅在 Windows 生效")
def test_file_reader_rejects_windows_drive_and_unc_paths(
    tmp_path: Path,
    path: str,
) -> None:
    """绝对路径、盘符相对路径和 UNC 路径属于参数问题。"""

    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(call_id="call-1", name="file_reader", arguments={"path": path})
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True


def test_file_reader_rejects_absolute_path(tmp_path: Path) -> None:
    """任何绝对路径都被拒绝，即使它恰好位于根目录内。"""

    target = tmp_path / "inside.txt"
    target.write_text("inside", encoding="utf-8")
    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": str(target)},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True


@pytest.mark.parametrize("name", ["CON", "con.txt", "NUL", "COM1", "LPT3.log"])
@pytest.mark.skipif(os.name != "nt", reason="Windows 保留设备名检查仅在 Windows 生效")
def test_file_reader_rejects_windows_reserved_device_names(
    tmp_path: Path,
    name: str,
) -> None:
    """Windows 保留设备名（如 CON、NUL）被拒绝，避免打开系统设备。"""

    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(call_id="call-1", name="file_reader", arguments={"path": name})
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True


def test_file_reader_rejects_symlink_escaping_root(tmp_path: Path) -> None:
    """符号链接指向根目录之外时，解析后越界并返回稳定执行错误。"""

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")
    registry = _registry_with_reader(root)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "link.txt"},
        )
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True
    assert "超出允许的根目录" in result.error.message


def test_file_reader_rejects_symlink_directory_escaping_root(
    tmp_path: Path,
) -> None:
    """指向根目录外目录的符号链接同样在解析后被拒绝。"""

    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "data.txt").write_text("secret", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    link_dir = root / "link_dir"
    try:
        link_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")
    registry = _registry_with_reader(root)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "link_dir/data.txt"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert "超出允许的根目录" in result.error.message


def test_file_reader_rejects_target_resolving_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """解析结果越界时被拒绝，即使当前环境无法创建真实符号链接。"""

    (tmp_path / "inside.txt").write_text("inside", encoding="utf-8")
    registry = _registry_with_reader(tmp_path)
    original_resolve = Path.resolve

    def fake_resolve(path: Path, strict: bool = False) -> Path:
        if path.name == "inside.txt":
            return tmp_path.parent / "outside.txt"
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "inside.txt"},
        )
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True
    assert "超出允许的根目录" in result.error.message


def test_file_reader_allows_symlink_staying_inside_root(tmp_path: Path) -> None:
    """指向根目录内文件的符号链接是合法读取，说明边界是解析后的路径。"""

    root = tmp_path / "root"
    root.mkdir()
    (root / "real.txt").write_text("inside", encoding="utf-8")
    link = root / "alias.txt"
    try:
        link.symlink_to(root / "real.txt")
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")
    registry = _registry_with_reader(root)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "alias.txt"},
        )
    )

    assert result.is_success is True
    assert result.content == "inside"


def test_file_reader_missing_file_is_stable_execution_error(tmp_path: Path) -> None:
    """文件不存在返回 TOOL_EXECUTION_ERROR 且允许模型换路径重试。"""

    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "missing.txt"},
        )
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True
    assert "文件不存在" in result.error.message


def test_file_reader_directory_target_is_stable_execution_error(
    tmp_path: Path,
) -> None:
    """目标不是常规文件（例如目录）返回稳定执行错误。"""

    (tmp_path / "sub").mkdir()
    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "sub"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert "不是常规文件" in result.error.message


def test_file_reader_missing_root_is_stable_execution_error(
    tmp_path: Path,
) -> None:
    """允许的根目录不存在时返回稳定执行错误。"""

    registry = _registry_with_reader(tmp_path / "nope")

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "a.txt"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert "根目录不存在" in result.error.message


def test_file_reader_root_is_file_is_stable_execution_error(
    tmp_path: Path,
) -> None:
    """允许的根目录本身是文件时返回稳定执行错误。"""

    root_file = tmp_path / "not_a_dir.txt"
    root_file.write_text("x", encoding="utf-8")
    registry = _registry_with_reader(root_file)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "a.txt"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert "根目录不存在" in result.error.message


def test_file_reader_non_utf8_file_is_stable_execution_error(
    tmp_path: Path,
) -> None:
    """无法按 UTF-8 解码的文件返回稳定执行错误。"""

    (tmp_path / "binary.dat").write_bytes(b"\xff\xfe\x00binary")
    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "binary.dat"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True
    assert "UTF-8" in result.error.message


def test_file_reader_maps_read_failure_to_stable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """打开文件时的 OSError 统一转换为稳定执行错误。"""

    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    registry = _registry_with_reader(tmp_path)

    def raise_os_error(*args: object, **kwargs: object) -> None:
        raise OSError("磁盘读取失败")

    monkeypatch.setattr(Path, "open", raise_os_error)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "a.txt"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True
    assert "读取文件失败" in result.error.message
    assert "磁盘读取失败" not in str(result)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": 42},
        {"path": ""},
        {"path": "   "},
        {"path": "a.txt", "extra": "x"},
        {"path": "a\x00b.txt"},
    ],
)
def test_file_reader_rejects_missing_or_non_string_path(
    tmp_path: Path,
    arguments: dict[str, object],
) -> None:
    """缺失、非字符串、空白、多余参数或空字节在工具边界被拒。"""

    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(call_id="call-1", name="file_reader", arguments=arguments)
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True


def test_file_reader_rejects_overlong_path(tmp_path: Path) -> None:
    """超过路径长度上限的参数返回 INVALID_ARGUMENTS。"""

    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "a" * (MAX_PATH_LENGTH + 1)},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert "过长" in result.error.message


def test_file_reader_path_at_length_limit_passes_validation(
    tmp_path: Path,
) -> None:
    """路径长度恰好等于上限时通过参数校验，只在执行期报文件不存在。"""

    registry = _registry_with_reader(tmp_path)

    result = registry.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "a" * MAX_PATH_LENGTH},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert "文件不存在" in result.error.message


def test_file_reader_executes_directly_without_registry(tmp_path: Path) -> None:
    """工具本身可以直接调用，返回字符串内容。"""

    (tmp_path / "a.txt").write_text("直接调用", encoding="utf-8")
    tool = FileReaderTool(tmp_path)

    assert tool.execute({"path": "a.txt"}) == "直接调用"
    assert isinstance(tool.execute({"path": "a.txt"}), str)


def test_file_reader_satisfies_tool_protocol(tmp_path: Path) -> None:
    """文件读取工具满足 Day 7 的 Tool 协议。"""

    tool = FileReaderTool(tmp_path)

    assert isinstance(tool, Tool)
    assert tool.name == "file_reader"
    assert tool.description.strip()


def test_file_reader_rejects_invalid_root_configuration() -> None:
    """根目录配置必须是合法路径。"""

    with pytest.raises(ValueError):
        FileReaderTool("   ")

    with pytest.raises(TypeError):
        FileReaderTool(123)  # type: ignore[arg-type]


def test_file_reader_preserves_call_identity_through_observation(
    tmp_path: Path,
) -> None:
    """调用编号贯穿 ToolCall、ToolResult 与 Observation。"""

    (tmp_path / "a.txt").write_text("内容", encoding="utf-8")
    registry = _registry_with_reader(tmp_path)
    call = ToolCall(
        call_id="call-7",
        name="file_reader",
        arguments={"path": "a.txt"},
    )

    result = registry.execute(call)
    observation = Observation.from_tool_result(result)
    tool_message = observation.as_message()

    assert result.is_success is True
    assert result.tool_call_id == "call-7"
    assert observation.tool_call_id == "call-7"
    assert tool_message.tool_call_id == "call-7"
    assert observation.content == "内容"
    assert tool_message.role is MessageRole.TOOL


def test_file_reader_duplicate_registration_is_rejected(tmp_path: Path) -> None:
    """重复注册文件读取工具与 Day 7 的注册纪律保持一致。"""

    registry = _registry_with_reader(tmp_path)

    with pytest.raises(ToolRegistrationError):
        registry.register(FileReaderTool(tmp_path))


def test_file_reader_registry_instances_are_isolated(tmp_path: Path) -> None:
    """文件读取工具只在注册过的注册表中可用。"""

    first = _registry_with_reader(tmp_path)
    second = ToolRegistry()

    assert "file_reader" in first
    assert "file_reader" not in second

    result = second.execute(
        ToolCall(
            call_id="call-1",
            name="file_reader",
            arguments={"path": "a.txt"},
        )
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL


def test_file_reader_argument_error_is_stable_at_tool_boundary(
    tmp_path: Path,
) -> None:
    """工具直接抛出的参数错误也是稳定异常。"""

    tool = FileReaderTool(tmp_path)

    with pytest.raises(ToolArgumentError):
        tool.execute({})


def test_three_tools_coexist_and_unknown_tool_lists_all(tmp_path: Path) -> None:
    """计算器、文件读取和检索工具可以同时注册，未知工具消息列出全部名称。"""

    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(FileReaderTool(tmp_path))
    registry.register(RetrieveTool())

    assert registry.names == ("calculator", "file_reader", "retrieve")

    result = registry.execute(ToolCall(call_id="call-1", name="gamma", arguments={}))

    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL
    assert "gamma" in result.error.message
    assert "calculator" in result.error.message
    assert "file_reader" in result.error.message
    assert "retrieve" in result.error.message


def test_file_reader_consumes_tool_call_returned_by_fake_llm(
    tmp_path: Path,
) -> None:
    """FakeLLM 返回的 ToolCall 能端到端驱动文件读取并转回观察。"""

    (tmp_path / "a.txt").write_text("你好", encoding="utf-8")
    call = ToolCall(
        call_id="call-1",
        name="file_reader",
        arguments={"path": "a.txt"},
    )
    requesting_llm = FakeLLM(
        [Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call])]
    )
    registry = _registry_with_reader(tmp_path)

    decision_message = requesting_llm.complete(
        [Message(role=MessageRole.USER, content="请读取 a.txt")]
    )
    result = registry.execute(decision_message.tool_calls[0])
    observation = Observation.from_tool_result(result)
    tool_message = observation.as_message()

    assert decision_message.tool_calls[0].call_id == "call-1"
    assert result.is_success is True
    assert result.content == "你好"
    assert tool_message.role is MessageRole.TOOL
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.content == "你好"
