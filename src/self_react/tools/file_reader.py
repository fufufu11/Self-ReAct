"""受限文件读取业务工具（Day 9）。

``FileReaderTool`` 只在构造时指定的根目录内读取 UTF-8 文本文件。参数
``path`` 必须是相对路径：绝对路径、盘符路径、UNC 路径、包含 ``..`` 的路径
和 Windows 保留设备名在语法检查阶段就被拒绝；符号链接会在解析后被再次
核对，指向根目录之外的目标一律拒绝。

参数校验失败抛 ``ToolArgumentError``（注册表转 ``INVALID_ARGUMENTS``）；
根目录缺失、文件不存在、目标不是常规文件、无法解码或读取失败等业务问题
抛 ``ToolExecutionError``（注册表转 ``TOOL_EXECUTION_ERROR``）。单次最多
返回 ``MAX_OUTPUT_CHARS`` 个字符，超出部分截断并附加标记。
"""

from __future__ import annotations

import os
from pathlib import Path

from self_react.models import JsonObject
from self_react.tools.base import ToolArgumentError, ToolExecutionError

MAX_PATH_LENGTH = 1_000
"""path 参数的最大字符数，防止超长输入。"""

MAX_OUTPUT_CHARS = 10_000
"""单次返回内容的最大字符数，防止把超大文件塞进模型上下文。"""

TRUNCATION_MARKER = "\n…（内容过长，已截断）"
"""内容被截断时附加在末尾的稳定标记。"""

_READ_CHUNK = MAX_OUTPUT_CHARS + len(TRUNCATION_MARKER) + 1
"""单次最多读取的字符数：等于上限加上截断标记长度，用于发现超长内容。"""

_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
"""Windows 保留设备名；打开这些名字会访问系统设备而不是普通文件。"""


def _extract_path(arguments: JsonObject) -> str:
    """从参数字典中取出并校验 path 字符串。"""

    unexpected = sorted(set(arguments) - {"path"})
    if unexpected:
        raise ToolArgumentError(f"不支持的参数：{', '.join(unexpected)}")

    path = arguments.get("path")
    if not isinstance(path, str):
        raise ToolArgumentError("path 必须是字符串")
    if not path.strip():
        raise ToolArgumentError("path 不能为空")
    if len(path) > MAX_PATH_LENGTH:
        raise ToolArgumentError("路径过长")
    if "\x00" in path:
        raise ToolArgumentError("路径不能包含空字节")
    return path


def _reject_unsafe_path(candidate: Path) -> None:
    """在访问文件系统之前拒绝语法上不合规的路径。"""

    if candidate.is_absolute() or candidate.drive:
        raise ToolArgumentError("路径必须是根目录内的相对路径")
    if ".." in candidate.parts:
        raise ToolArgumentError("路径不能包含 .. 越界")
    if os.name == "nt":
        for part in candidate.parts:
            if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
                raise ToolArgumentError("路径包含 Windows 保留设备名")


def _resolve_safe_path(path_text: str, root: Path) -> Path:
    """解析路径并确保它落在允许的根目录内。"""

    try:
        resolved_root = root.resolve()
    except OSError as exc:
        raise ToolExecutionError("允许的根目录无法解析", retryable=True) from exc

    if not resolved_root.is_dir():
        raise ToolExecutionError("允许的根目录不存在或不是目录", retryable=True)

    candidate = Path(path_text)
    _reject_unsafe_path(candidate)

    try:
        resolved_target = (resolved_root / candidate).resolve()
    except OSError as exc:
        raise ToolExecutionError("路径解析失败", retryable=True) from exc

    if not resolved_target.is_relative_to(resolved_root):
        raise ToolExecutionError("路径解析后超出允许的根目录", retryable=True)
    return resolved_target


def _read_text(resolved: Path) -> str:
    """读取常规文本文件，并把读取失败统一转换为稳定工具异常。"""

    if not resolved.exists():
        raise ToolExecutionError("文件不存在", retryable=True)
    if not resolved.is_file():
        raise ToolExecutionError("目标不是常规文件", retryable=True)

    try:
        with resolved.open("r", encoding="utf-8") as handle:
            content = handle.read(_READ_CHUNK)
    except UnicodeDecodeError as exc:
        raise ToolExecutionError("文件不是有效的 UTF-8 文本", retryable=True) from exc
    except OSError as exc:
        raise ToolExecutionError("读取文件失败", retryable=True) from exc

    if len(content) > MAX_OUTPUT_CHARS:
        return content[:MAX_OUTPUT_CHARS] + TRUNCATION_MARKER
    return content


class FileReaderTool:
    """只在允许的根目录内读取常规文本文件的确定性工具。

    根目录是工具的配置（安全边界），不是每次调用的参数：调用方只能传根目录
    内的相对路径。工具本身不持有注册表、消息或密钥。
    """

    name = "file_reader"
    description = (
        "读取允许目录内的 UTF-8 文本文件。参数 path 必须是相对于允许目录的相对路径，"
        "例如 notes/todo.txt；绝对路径、盘符路径和 .. 越界会被拒绝。"
        f"单次最多返回 {MAX_OUTPUT_CHARS} 个字符，超出部分会截断并标注。"
    )

    def __init__(self, root_directory: str | os.PathLike[str]) -> None:
        """固定允许读取的根目录；该目录是工具的安全边界。"""

        if not isinstance(root_directory, (str, os.PathLike)):
            raise TypeError("root_directory 必须是路径")
        if isinstance(root_directory, str) and not root_directory.strip():
            raise ValueError("root_directory 不能为空")
        self.root = Path(root_directory)

    def execute(self, arguments: JsonObject) -> str:
        """执行一次文件读取并返回文本内容。"""

        path_text = _extract_path(arguments)
        resolved = _resolve_safe_path(path_text, self.root)
        return _read_text(resolved)


__all__ = [
    "FileReaderTool",
    "MAX_OUTPUT_CHARS",
    "MAX_PATH_LENGTH",
    "TRUNCATION_MARKER",
]
