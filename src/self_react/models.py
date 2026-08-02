"""Self-ReAct 的最小领域模型。

本模块只描述 ReAct 循环中跨边界传递的数据，不负责调用模型、查找工具或
执行循环。模型使用 Pydantic v2 的校验和 JSON 序列化能力，让后续的 LLM、
解析器、工具和 Agent 模块可以共享同一套稳定的数据契约。
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

JsonObject = dict[str, Any]
"""可以安全写入 JSON 的对象字段。

字段值仍由模型的校验器检查，因此不会把 Python 函数、客户端对象等运行时
资源悄悄塞进需要跨模块传递或持久化的领域对象。
"""

Identifier = Annotated[str, Field(strict=True, min_length=1)]
"""领域对象使用的非空字符串标识。"""


def _ensure_json_object(value: JsonObject) -> JsonObject:
    """拒绝无法稳定序列化的元数据和工具参数。"""

    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("字段必须只包含可 JSON 序列化的值") from exc
    return value


def _ensure_non_blank(value: str) -> str:
    """阻止只包含空白字符的领域标识或消息文本。"""

    if not value.strip():
        raise ValueError("文本不能只包含空白字符")
    return value


class MessageRole(str, Enum):
    """消息在 ReAct 上下文中的角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolResultStatus(str, Enum):
    """工具执行结果的互斥状态。"""

    SUCCESS = "success"
    FAILURE = "failure"


class ToolErrorCode(str, Enum):
    """工具边界可以稳定识别的错误类别。"""

    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    TIMEOUT = "TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"


class TraceErrorCode(str, Enum):
    """执行轨迹中不局限于工具本身的错误类别。"""

    MODEL_OUTPUT_PARSE_ERROR = "MODEL_OUTPUT_PARSE_ERROR"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    INVALID_TOOL_ARGUMENTS = "INVALID_TOOL_ARGUMENTS"


class TerminationReason(str, Enum):
    """一次 Agent 运行停止时对外报告的最终原因。"""

    FINAL_ANSWER = "FINAL_ANSWER"
    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"
    MODEL_OUTPUT_PARSE_ERROR = "MODEL_OUTPUT_PARSE_ERROR"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"


class ToolCall(BaseModel):
    """模型请求执行的一次工具动作。

    ``ToolCall`` 只携带可序列化的工具名和参数，不持有 Python 函数或工具
    注册表。``call_id`` 会贯穿助手消息、工具结果和观察，供后续模块准确
    关联同一次调用。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool_call"] = "tool_call"
    call_id: Identifier
    name: Identifier
    arguments: JsonObject

    _validate_call_id = field_validator("call_id", "name")(_ensure_non_blank)
    _validate_arguments = field_validator("arguments")(_ensure_json_object)


class FinalAnswer(BaseModel):
    """模型决定结束循环时给调用方的最终回答。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["final_answer"] = "final_answer"
    content: Annotated[str, Field(strict=True, min_length=1)]

    _validate_content = field_validator("content")(_ensure_non_blank)


class ToolError(BaseModel):
    """工具失败的结构化信息。

    ``message`` 是可以反馈给模型的稳定说明；原始异常对象和堆栈不属于领域
    模型。``retryable`` 让循环控制器决定是否把错误作为 Observation 继续
    运行，而不是让每个工具自行开启重试。
    """

    model_config = ConfigDict(extra="forbid")

    code: ToolErrorCode
    message: Annotated[str, Field(strict=True, min_length=1)]
    retryable: StrictBool
    details: JsonObject = Field(default_factory=dict)

    _validate_message = field_validator("message")(_ensure_non_blank)
    _validate_details = field_validator("details")(_ensure_json_object)


class ToolResult(BaseModel):
    """一次工具调用的成功或失败结果。

    成功结果必须提供 ``content`` 且不能附带 ``error``；失败结果必须提供
    ``ToolError`` 且不能把错误文本放进成功内容。这个互斥不变量避免异常被
    下游误当成正常工具输出。
    """

    model_config = ConfigDict(extra="forbid")

    status: ToolResultStatus
    tool_call_id: Identifier
    tool_name: Identifier
    content: Annotated[str | None, Field(strict=True)] = None
    error: ToolError | None = None
    metadata: JsonObject = Field(default_factory=dict)

    _validate_ids = field_validator("tool_call_id", "tool_name")(_ensure_non_blank)
    _validate_metadata = field_validator("metadata")(_ensure_json_object)

    @model_validator(mode="after")
    def validate_status_payload(self) -> ToolResult:
        """校验状态与载荷的互斥关系。"""

        if self.status is ToolResultStatus.SUCCESS:
            if self.content is None:
                raise ValueError("成功结果必须包含 content")
            if self.error is not None:
                raise ValueError("成功结果不能包含 error")
        else:
            if self.error is None:
                raise ValueError("失败结果必须包含 error")
            if self.content is not None:
                raise ValueError("失败结果不能把错误写入 content")
        return self

    @classmethod
    def success(
        cls,
        *,
        tool_call_id: str,
        tool_name: str,
        content: str,
        metadata: JsonObject | None = None,
    ) -> ToolResult:
        """构造成功结果，供工具适配层使用。"""

        return cls(
            status=ToolResultStatus.SUCCESS,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            content=content,
            metadata={} if metadata is None else metadata,
        )

    @classmethod
    def failure(
        cls,
        *,
        tool_call_id: str,
        tool_name: str,
        code: ToolErrorCode,
        message: str,
        retryable: bool,
        details: JsonObject | None = None,
        metadata: JsonObject | None = None,
    ) -> ToolResult:
        """构造失败结果，集中保证错误字段不会伪装成成功内容。"""

        return cls(
            status=ToolResultStatus.FAILURE,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            error=ToolError(
                code=code,
                message=message,
                retryable=retryable,
                details={} if details is None else details,
            ),
            metadata={} if metadata is None else metadata,
        )

    @property
    def is_success(self) -> bool:
        """返回结果是否为成功状态。"""

        return self.status is ToolResultStatus.SUCCESS


class Message(BaseModel):
    """进入模型上下文的一条消息。

    普通消息使用 ``content``；助手消息还可以携带一个或多个
    ``ToolCall``；工具消息必须通过 ``tool_call_id`` 回指对应调用。这样
    ``Message`` 表示对话上下文，而 ``ToolCall`` 仍表示需要执行的动作。
    """

    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: Annotated[str, Field(strict=True)]
    tool_call_id: Identifier | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @field_validator("tool_call_id")
    @classmethod
    def validate_tool_call_id(cls, value: str | None) -> str | None:
        """如果存在调用编号，确保它不是空白。"""

        return None if value is None else _ensure_non_blank(value)

    @model_validator(mode="after")
    def validate_role_payload(self) -> Message:
        """校验角色与调用关联是否一致。"""

        if self.role in (MessageRole.SYSTEM, MessageRole.USER):
            if not self.content.strip():
                raise ValueError("system/user 消息必须包含非空 content")
            if self.tool_call_id is not None or self.tool_calls:
                raise ValueError("system/user 消息不能关联工具调用")

        if self.role is MessageRole.ASSISTANT:
            if self.tool_call_id is not None:
                raise ValueError("assistant 消息不能使用 tool_call_id")
            if not self.content.strip() and not self.tool_calls:
                raise ValueError("assistant 消息必须包含回答或工具调用")
            call_ids = [call.call_id for call in self.tool_calls]
            if len(call_ids) != len(set(call_ids)):
                raise ValueError("assistant 消息中的工具调用编号必须唯一")

        if self.role is MessageRole.TOOL:
            if self.tool_call_id is None:
                raise ValueError("tool 消息必须关联 tool_call_id")
            if self.tool_calls:
                raise ValueError("tool 消息不能再携带 tool_calls")

        return self


class Observation(BaseModel):
    """从 ``ToolResult`` 提炼给模型的可读观察。

    Observation 不是另一种工具结果：它是执行结果写回消息上下文后的稳定
    表示。失败观察仍保留错误类别和可恢复性，便于控制器和模型分别做决定。
    """

    model_config = ConfigDict(extra="forbid")

    tool_call_id: Identifier
    tool_name: Identifier
    content: Annotated[str, Field(strict=True)]
    is_error: StrictBool
    error_code: ToolErrorCode | None = None
    retryable: StrictBool | None = None
    metadata: JsonObject = Field(default_factory=dict)

    _validate_ids = field_validator("tool_call_id", "tool_name")(_ensure_non_blank)
    _validate_metadata = field_validator("metadata")(_ensure_json_object)

    @model_validator(mode="after")
    def validate_error_payload(self) -> Observation:
        """保证成功观察和失败观察的字段互斥。"""

        if self.is_error:
            if self.error_code is None or self.retryable is None:
                raise ValueError("失败观察必须包含 error_code 和 retryable")
        elif self.error_code is not None or self.retryable is not None:
            raise ValueError("成功观察不能包含错误字段")
        return self

    @classmethod
    def from_tool_result(cls, result: ToolResult) -> Observation:
        """把工具结果转换成模型可读观察，不执行任何工具逻辑。"""

        if result.is_success:
            return cls(
                tool_call_id=result.tool_call_id,
                tool_name=result.tool_name,
                content=result.content or "",
                is_error=False,
                metadata=result.metadata,
            )

        assert result.error is not None
        return cls(
            tool_call_id=result.tool_call_id,
            tool_name=result.tool_name,
            content=result.error.message,
            is_error=True,
            error_code=result.error.code,
            retryable=result.error.retryable,
            metadata=result.metadata,
        )

    def as_message(self) -> Message:
        """把观察写成可回填消息，保留对原工具调用的关联。"""

        return Message(
            role=MessageRole.TOOL,
            content=self.content,
            tool_call_id=self.tool_call_id,
        )


class TraceError(BaseModel):
    """轨迹中记录的模型解析、分派或执行错误。"""

    model_config = ConfigDict(extra="forbid")

    code: TraceErrorCode
    message: Annotated[str, Field(strict=True, min_length=1)]
    retryable: StrictBool
    details: JsonObject = Field(default_factory=dict)

    _validate_message = field_validator("message")(_ensure_non_blank)
    _validate_details = field_validator("details")(_ensure_json_object)


Decision = Annotated[ToolCall | FinalAnswer, Field(discriminator="kind")]
"""一个轨迹步骤的判别决策：请求工具或直接给出最终回答。"""


class TraceStep(BaseModel):
    """一次决策尝试的可序列化执行轨迹。

    ``input_summary`` 只允许摘要，不默认保存完整隐藏推理；决策、观察和
    错误可以分别为空，以便记录解析失败或尚未执行完的中间步骤。
    """

    model_config = ConfigDict(extra="forbid")

    step_number: Annotated[int, Field(strict=True, ge=1)]
    input_summary: Annotated[str | None, Field(strict=True, max_length=2_000)] = None
    decision: Decision | None = None
    observation: Observation | None = None
    error: TraceError | None = None
    duration_ms: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_step_payload(self) -> TraceStep:
        """确保每个轨迹步骤至少记录一种可解释结果。"""

        if self.decision is None and self.observation is None and self.error is None:
            raise ValueError("TraceStep 至少需要 decision、observation 或 error 之一")

        if isinstance(self.decision, FinalAnswer):
            if self.observation is not None:
                raise ValueError("最终回答步骤不能包含工具 observation")
            if self.error is not None:
                raise ValueError("最终回答步骤不能同时记录 error")

        if isinstance(self.decision, ToolCall) and self.observation is not None:
            if self.observation.tool_call_id != self.decision.call_id:
                raise ValueError("observation 必须回指当前 ToolCall 的 call_id")
        return self


class AgentState(BaseModel):
    """ReAct 后续循环真正需要的最小状态。

    状态只保存任务、消息、可用工具名称、步数预算、轨迹和终止信息，不保存
    模型客户端、Python 函数、密钥或其他不可序列化运行时资源。
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    task: Identifier
    messages: list[Message] = Field(default_factory=list)
    available_tools: list[Identifier] = Field(default_factory=list)
    max_steps: Annotated[int, Field(strict=True, ge=0)]
    steps_used: Annotated[int, Field(strict=True, ge=0)] = 0
    trace: list[TraceStep] = Field(default_factory=list)
    final_answer: FinalAnswer | None = None
    termination_reason: TerminationReason | None = None

    _validate_task = field_validator("task")(_ensure_non_blank)

    @field_validator("available_tools")
    @classmethod
    def validate_available_tools(cls, value: list[str]) -> list[str]:
        """工具清单只保存精确名称，并拒绝重复项。"""

        normalized = [_ensure_non_blank(name) for name in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("available_tools 不能包含重复名称")
        return normalized

    @model_validator(mode="after")
    def validate_state_invariants(self) -> AgentState:
        """保持步数、轨迹和终止信息之间的一致性。"""

        if self.steps_used != len(self.trace):
            raise ValueError("steps_used 必须等于 trace 的步骤数量")
        if self.steps_used > self.max_steps:
            raise ValueError("steps_used 不能超过 max_steps")

        if self.termination_reason is TerminationReason.FINAL_ANSWER:
            if self.final_answer is None:
                raise ValueError("FINAL_ANSWER 必须同时提供 final_answer")
        elif self.final_answer is not None:
            raise ValueError("只有 FINAL_ANSWER 才能提供 final_answer")
        return self

    @property
    def is_terminated(self) -> bool:
        """返回循环是否已经有最终终止原因。"""

        return self.termination_reason is not None

    @property
    def remaining_steps(self) -> int:
        """返回当前还可尝试的决策轮数。"""

        return self.max_steps - self.steps_used


__all__ = [
    "AgentState",
    "Decision",
    "FinalAnswer",
    "JsonObject",
    "Message",
    "MessageRole",
    "Observation",
    "TerminationReason",
    "ToolCall",
    "ToolError",
    "ToolErrorCode",
    "ToolResult",
    "ToolResultStatus",
    "TraceError",
    "TraceErrorCode",
    "TraceStep",
]
