"""确定性 NDJSON 日志查询与聚合工具（R-07）。

``LogQueryTool`` 在构造时固定的根目录内读取 JSON Lines（NDJSON）日志文件，
按服务、级别、错误码、关键词与时间窗过滤，并返回命中行、命中数与文件总数；
可选 ``group_by`` 按 ``error_code`` / ``service`` / ``level`` / ``hour`` 做简单
聚合。所有比较与排序都是纯函数，相同输入永远得到相同输出，不访问网络、不依赖
真实 API。

参数校验失败抛 ``ToolArgumentError``（注册表转 ``INVALID_ARGUMENTS``）；根目录
缺失、文件不存在、目标不是常规文件、JSON 解析失败等业务问题抛
``ToolExecutionError``（注册表转 ``TOOL_EXECUTION_ERROR``）。返回命中行数量由
``limit`` 限制，避免把超大文件塞进模型上下文。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from self_react.models import JsonObject
from self_react.tools.base import ToolArgumentError, ToolExecutionError
from self_react.tools.schema import generate_parameters_schema

MAX_PATH_LENGTH = 1_000
"""path 参数的最大字符数。"""

DEFAULT_LIMIT = 20
"""非聚合查询默认返回的命中行上限。"""

MAX_LIMIT = 50
"""``limit`` 参数的硬上限。"""

_TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
"""时间参数必须匹配的固定格式，保证字典序比较可确定。"""

_GROUP_BY_CHOICES = ("error_code", "service", "level", "hour")
"""``group_by`` 允许的聚合维度。"""

_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
"""Windows 保留设备名；打开这些名字会访问系统设备而不是普通文件。"""


class LogQueryParameters(BaseModel):
    """日志查询工具的参数声明（R-03 Schema 自动生成的声明源）。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="日志文件相对路径，例如 logs.ndjson")
    service: str | None = Field(default=None, description="按服务名精确过滤")
    level: str | None = Field(default=None, description="按级别精确过滤，例如 ERROR")
    error_code: str | None = Field(
        default=None, description="按错误码精确过滤，例如 500"
    )
    keyword: str | None = Field(
        default=None,
        description="对 message 做大小写不敏感的子串过滤",
    )
    time_start: str | None = Field(
        default=None,
        description="起始时间，格式 YYYY-MM-DD HH:MM:SS，闭区间",
    )
    time_end: str | None = Field(
        default=None,
        description="结束时间，格式 YYYY-MM-DD HH:MM:SS，闭区间",
    )
    group_by: str | None = Field(
        default=None,
        description="聚合维度：error_code / service / level / hour",
    )
    limit: int = Field(
        default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="返回条数上限"
    )


def _extract_arguments(arguments: JsonObject) -> dict[str, object]:
    """从参数字典中取出并校验日志查询参数。"""

    allowed = {
        "path",
        "service",
        "level",
        "error_code",
        "keyword",
        "time_start",
        "time_end",
        "group_by",
        "limit",
    }
    unexpected = sorted(set(arguments) - allowed)
    if unexpected:
        raise ToolArgumentError(f"不支持的参数：{', '.join(unexpected)}")

    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ToolArgumentError("path 必须是非空字符串")
    if len(path) > MAX_PATH_LENGTH:
        raise ToolArgumentError("路径过长")
    if "\x00" in path:
        raise ToolArgumentError("路径不能包含空字节")

    normalized: dict[str, object] = {"path": path}
    for name in ("service", "level", "error_code", "keyword", "time_start", "time_end"):
        value = arguments.get(name)
        if value is not None and not isinstance(value, str):
            raise ToolArgumentError(f"{name} 必须是字符串")
        normalized[name] = value

    for name in ("time_start", "time_end"):
        value = normalized[name]
        if value is not None and _TIME_PATTERN.match(value) is None:
            raise ToolArgumentError(f"{name} 必须是 YYYY-MM-DD HH:MM:SS 格式")

    group_by = arguments.get("group_by")
    if group_by is not None:
        if not isinstance(group_by, str) or group_by not in _GROUP_BY_CHOICES:
            raise ToolArgumentError(
                f"group_by 必须是 {', '.join(_GROUP_BY_CHOICES)} 之一"
            )
    normalized["group_by"] = group_by

    limit = arguments.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ToolArgumentError("limit 必须是整数")
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolArgumentError(f"limit 必须在 1 到 {MAX_LIMIT} 之间")
    normalized["limit"] = limit
    return normalized


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


def _load_lines(resolved: Path) -> list[dict[str, object]]:
    """读取 NDJSON 文件并把每一行解析成 JSON 对象。"""

    if not resolved.exists():
        raise ToolExecutionError("文件不存在", retryable=True)
    if not resolved.is_file():
        raise ToolExecutionError("目标不是常规文件", retryable=True)

    try:
        with resolved.open("r", encoding="utf-8") as handle:
            raw_lines = handle.read().splitlines()
    except UnicodeDecodeError as exc:
        raise ToolExecutionError("文件不是有效的 UTF-8 文本", retryable=True) from exc
    except OSError as exc:
        raise ToolExecutionError("读取文件失败", retryable=True) from exc

    parsed: list[dict[str, object]] = []
    for index, raw in enumerate(raw_lines, start=1):
        text = raw.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolExecutionError(
                f"第 {index} 行不是合法 JSON", retryable=True
            ) from exc
        if not isinstance(value, dict):
            raise ToolExecutionError(f"第 {index} 行不是 JSON 对象", retryable=True)
        parsed.append(value)
    return parsed


def _matches(
    line: dict[str, object],
    *,
    service: str | None,
    level: str | None,
    error_code: str | None,
    keyword: str | None,
    time_start: str | None,
    time_end: str | None,
) -> bool:
    """判断一条日志是否满足全部过滤条件。"""

    timestamp = line.get("timestamp")
    if time_start is not None or time_end is not None:
        if not isinstance(timestamp, str):
            return False
        if time_start is not None and timestamp < time_start:
            return False
        if time_end is not None and timestamp > time_end:
            return False
    if service is not None and line.get("service") != service:
        return False
    if level is not None and line.get("level") != level:
        return False
    if error_code is not None and line.get("error_code") != error_code:
        return False
    if keyword is not None:
        message = line.get("message")
        if not isinstance(message, str):
            return False
        if keyword.casefold() not in message.casefold():
            return False
    return True


def _group_key(line: dict[str, object], group_by: str) -> str | None:
    """返回一条日志在指定维度下的聚合键；无有效值时返回 ``None``。"""

    if group_by == "hour":
        timestamp = line.get("timestamp")
        if not isinstance(timestamp, str) or len(timestamp) < 13:
            return None
        return timestamp[:13] + ":00:00"

    value = line.get(group_by)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _format_lines(
    matched: list[dict[str, object]],
    total: int,
    limit: int,
) -> str:
    """渲染非聚合查询的稳定文本输出。"""

    lines = [f"匹配 {len(matched)} 条 / 共 {total} 条"]
    for line in matched[:limit]:
        lines.append("- " + json.dumps(line, ensure_ascii=False, sort_keys=True))
    if len(matched) > limit:
        lines.append(f"…（仅显示前 {limit} 条）")
    return "\n".join(lines)


def _format_group(
    counts: dict[str, int],
    matched_count: int,
    total: int,
    group_by: str,
    limit: int,
) -> str:
    """渲染聚合查询的稳定文本输出。"""

    items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    lines = [f"匹配 {matched_count} 条 / 共 {total} 条", f"按 {group_by} 聚合："]
    for key, count in items[:limit]:
        lines.append(f"{key}: {count}")
    if len(items) > limit:
        lines.append(f"…（仅显示前 {limit} 项）")
    return "\n".join(lines)


class LogQueryTool:
    """在允许的根目录内读取 NDJSON 日志并做过滤/聚合的确定性工具。"""

    name = "log_query"
    description = (
        "查询允许目录内的 NDJSON 日志文件。参数 path 是相对路径；可选按 "
        "service、level、error_code、keyword（message 子串）、time_start / "
        "time_end 过滤，返回命中行与命中数、文件总数。group_by 可选 "
        "error_code / service / level / hour 做聚合计数，只统计该维度非空的"
        "条目；limit 限制返回条数（默认 20，最大 50）。"
    )
    parameters: JsonObject = generate_parameters_schema(LogQueryParameters)

    def __init__(self, root_directory: str | os.PathLike[str]) -> None:
        """固定允许读取的根目录；该目录是工具的安全边界。"""

        if not isinstance(root_directory, (str, os.PathLike)):
            raise TypeError("root_directory 必须是路径")
        if isinstance(root_directory, str) and not root_directory.strip():
            raise ValueError("root_directory 不能为空")
        self.root = Path(root_directory)

    def execute(self, arguments: JsonObject) -> str:
        """执行一次日志查询并返回稳定文本。"""

        args = _extract_arguments(arguments)
        resolved = _resolve_safe_path(str(args["path"]), self.root)
        lines = _load_lines(resolved)
        total = len(lines)

        matched = [
            line
            for line in lines
            if _matches(
                line,
                service=args["service"],  # type: ignore[arg-type]
                level=args["level"],  # type: ignore[arg-type]
                error_code=args["error_code"],  # type: ignore[arg-type]
                keyword=args["keyword"],  # type: ignore[arg-type]
                time_start=args["time_start"],  # type: ignore[arg-type]
                time_end=args["time_end"],  # type: ignore[arg-type]
            )
        ]

        group_by = args["group_by"]
        limit = args["limit"]
        assert isinstance(limit, int)

        if group_by is None:
            matched.sort(
                key=lambda line: (
                    str(line.get("timestamp") or ""),
                    str(line.get("service") or ""),
                    str(line.get("message") or ""),
                )
            )
            return _format_lines(matched, total, limit)

        assert isinstance(group_by, str)
        counts: dict[str, int] = {}
        for line in matched:
            key = _group_key(line, group_by)
            if key is None:
                continue
            counts[key] = counts.get(key, 0) + 1
        return _format_group(counts, sum(counts.values()), total, group_by, limit)


__all__ = [
    "DEFAULT_LIMIT",
    "LogQueryParameters",
    "LogQueryTool",
    "MAX_LIMIT",
    "MAX_PATH_LENGTH",
]
