"""R-03 工具 Schema 自动生成 + 注册表 Schema 预校验测试。

本文件验证 Day 23 的三类新行为：

1. ``generate_parameters_schema`` 从 Pydantic 参数模型或函数签名自动生成
   参数 JSON Schema，且四个业务工具生成的结果与既有手写 Schema 等价；
2. 注册表在业务校验之前按工具声明的 Schema 预校验参数，非法参数以稳定
   错误码 ``INVALID_ARGUMENTS`` 被拒且工具不被执行；
3. 补 Day 18 记录的候选缺口：工具 Schema 与工具校验一致性交叉测试。

全部用例离线、确定性，不访问网络、不依赖真实 API。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from self_react.models import ToolCall, ToolErrorCode
from self_react.tools import (
    CalculatorTool,
    FileReaderTool,
    FinalAnswerTool,
    RetrieveTool,
    ToolArgumentError,
    ToolRegistrationError,
    ToolRegistry,
    generate_parameters_schema,
)
from self_react.tools.schema import (
    model_to_parameters_schema,
    signature_to_parameters_schema,
    validate_parameters,
)

# ---------------------------------------------------------------------------
# 一、生成 Schema 与手写 Schema 等价性
# ---------------------------------------------------------------------------


CALCULATOR_PARAMETERS = {
    "type": "object",
    "properties": {
        "expression": {
            "type": "string",
            "description": "要计算的算术表达式，例如 2 + 2 * 3",
        },
    },
    "required": ["expression"],
    "additionalProperties": False,
}

FILE_READER_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "允许目录内的相对路径，例如 notes/todo.txt",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}

RETRIEVE_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "知识库主题词，例如 react、python、deepseek、uv、pydantic",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

FINAL_ANSWER_PARAMETERS = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": "给用户的最终回答文本",
        },
    },
    "required": ["content"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        (CalculatorTool(), CALCULATOR_PARAMETERS),
        (FileReaderTool(root_directory="C:/allowed"), FILE_READER_PARAMETERS),
        (RetrieveTool(), RETRIEVE_PARAMETERS),
        (FinalAnswerTool(), FINAL_ANSWER_PARAMETERS),
    ],
)
def test_generated_schema_matches_handwritten_equivalent(
    tool: object,
    expected: dict[str, object],
) -> None:
    """四个业务工具自动生成的 Schema 与既有手写 Schema 完全等价。"""

    assert tool.parameters == expected  # type: ignore[attr-defined]


class UserQuery(BaseModel):
    """独立参数模型：带默认值的字段不进入 required。"""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(description="主题")
    limit: int = 10


def test_model_schema_generation_normalizes_pydantic_output() -> None:
    """Pydantic v2 输出被规范成工具层参数形状：去 title、强制拒绝多余字段。"""

    assert model_to_parameters_schema(UserQuery) == {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "主题"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["topic"],
        "additionalProperties": False,
    }


def test_generate_parameters_schema_dispatches_to_model_path() -> None:
    """generate_parameters_schema 对 Pydantic 模型走模型生成路径。"""

    assert generate_parameters_schema(UserQuery) == model_to_parameters_schema(
        UserQuery
    )


def test_generate_parameters_schema_rejects_non_declarable_source() -> None:
    """既不是 Pydantic 模型也不是可调用对象时拒绝生成。"""

    with pytest.raises(TypeError):
        generate_parameters_schema(42)  # type: ignore[arg-type]


class InnerModel(BaseModel):
    """嵌套模型用于验证扁平结构限制。"""

    value: int


class OuterModel(BaseModel):
    """包含嵌套类型的参数模型。"""

    inner: InnerModel


def test_model_schema_generation_rejects_nested_models() -> None:
    """嵌套类型会生成 $defs，超出最小校验范围，应当显式拒绝而非产出残缺 Schema。"""

    with pytest.raises(ValueError):
        model_to_parameters_schema(OuterModel)


# ---------------------------------------------------------------------------
# 二、函数签名轻量转换
# ---------------------------------------------------------------------------


def add_numbers(a: int, b: float = 0.0, *, label: str = "sum") -> str:
    """测试用函数：位置参数、默认值参数与关键字参数。"""

    return str(a + b)


def test_signature_schema_generation_maps_annotations_and_required() -> None:
    """类型标注映射到 JSON 类型，无默认值参数进入 required。"""

    assert signature_to_parameters_schema(add_numbers) == {
        "type": "object",
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "number"},
            "label": {"type": "string"},
        },
        "required": ["a"],
        "additionalProperties": False,
    }


def lookup(query: str | None, limit: int = 5) -> str:
    """测试用函数：Optional 标注只取非空类型。"""

    return "result"


def test_signature_schema_generation_supports_optional_annotation() -> None:
    """``X | None`` 标注按非空类型生成，不把 null 写进 type。"""

    assert signature_to_parameters_schema(lookup) == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def collect_items(*args: int) -> str:
    """测试用函数：可变位置参数。"""

    return "ok"


def test_signature_schema_generation_rejects_varargs() -> None:
    """可变参数无法表达为固定属性，显式拒绝。"""

    with pytest.raises(ValueError):
        signature_to_parameters_schema(collect_items)


def read_path(path: Path) -> str:
    """测试用函数：不支持的第三方类型标注。"""

    return "content"


def test_signature_schema_generation_rejects_unsupported_annotation() -> None:
    """不支持的类型标注显式报错，避免悄悄生成错误 Schema。"""

    with pytest.raises(ValueError):
        signature_to_parameters_schema(read_path)


def bare_parameter(value) -> str:  # noqa: ANN001
    """测试用函数：缺少类型标注。"""

    return "ok"


def test_signature_schema_generation_rejects_missing_annotation() -> None:
    """缺少类型标注无法推断 JSON 类型，显式拒绝。"""

    with pytest.raises(ValueError):
        signature_to_parameters_schema(bare_parameter)


# ---------------------------------------------------------------------------
# 三、最小 JSON Schema 校验器
# ---------------------------------------------------------------------------


VALIDATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 10},
        "count": {"type": "integer", "minimum": 0, "maximum": 100},
        "kind": {"type": "string", "enum": ["a", "b"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "ratio": {"type": "number"},
        "ok": {"type": "boolean"},
        "note": {"type": "string", "pattern": "^[a-z]+$"},
    },
    "required": ["text"],
    "additionalProperties": False,
}


def test_validate_parameters_accepts_fully_valid_object() -> None:
    """完全符合 Schema 的参数返回 None。"""

    arguments = {
        "text": "hi",
        "count": 3,
        "kind": "a",
        "tags": ["x", "y"],
        "ratio": 0.5,
        "ok": True,
        "note": "abc",
    }
    assert validate_parameters(arguments, VALIDATION_SCHEMA) is None


def test_validate_parameters_reports_missing_required() -> None:
    """缺少必需参数时返回稳定中文说明。"""

    assert validate_parameters({}, VALIDATION_SCHEMA) == "缺少必需参数：text"


def test_validate_parameters_reports_unexpected_keys_sorted() -> None:
    """多余参数按排序后的名称列出，保证消息确定性。"""

    assert (
        validate_parameters({"text": "x", "zeta": 1, "alpha": 2}, VALIDATION_SCHEMA)
        == "包含不支持的参数：alpha, zeta"
    )


def test_validate_parameters_reports_type_mismatch() -> None:
    """类型不匹配返回参数名、期望类型与实际类型。"""

    assert (
        validate_parameters({"text": 42}, VALIDATION_SCHEMA)
        == "参数 text 类型应为 string，实际为 integer"
    )


def test_validate_parameters_rejects_bool_as_integer() -> None:
    """布尔值不是整数，JSON Schema 语义下必须拒绝。"""

    assert (
        validate_parameters({"text": "x", "count": True}, VALIDATION_SCHEMA)
        == "参数 count 类型应为 integer，实际为 boolean"
    )


def test_validate_parameters_enforces_length_bounds() -> None:
    """minLength / maxLength 分别拒绝过短和过长的字符串。"""

    assert (
        validate_parameters({"text": ""}, VALIDATION_SCHEMA)
        == "参数 text 长度不能少于 1"
    )
    assert (
        validate_parameters({"text": "12345678901"}, VALIDATION_SCHEMA)
        == "参数 text 长度不能超过 10"
    )


def test_validate_parameters_enforces_numeric_bounds() -> None:
    """minimum / maximum 拒绝越界的数值。"""

    assert (
        validate_parameters({"text": "x", "count": -1}, VALIDATION_SCHEMA)
        == "参数 count 不能小于 0"
    )
    assert (
        validate_parameters({"text": "x", "count": 101}, VALIDATION_SCHEMA)
        == "参数 count 不能大于 100"
    )


def test_validate_parameters_enforces_enum_and_pattern() -> None:
    """enum 与 pattern 约束生效。"""

    assert (
        validate_parameters({"text": "x", "kind": "z"}, VALIDATION_SCHEMA)
        == "参数 kind 不在允许值范围内"
    )
    assert (
        validate_parameters({"text": "x", "note": "ABC"}, VALIDATION_SCHEMA)
        == "参数 note 不符合要求格式"
    )


def test_validate_parameters_validates_array_items() -> None:
    """数组元素按 items 子 Schema 逐个校验，错误路径包含下标。"""

    assert (
        validate_parameters({"text": "x", "tags": ["ok", 7]}, VALIDATION_SCHEMA)
        == "参数 tags[1] 类型应为 string，实际为 integer"
    )


def test_validate_parameters_ignores_unknown_schema_keywords() -> None:
    """未知 Schema 关键字按 JSON Schema 语义忽略，不阻断合法参数。"""

    schema = {
        "type": "object",
        "properties": {"x": {"type": "string", "format": "email"}},
    }
    assert validate_parameters({"x": "not-an-email"}, schema) is None


@pytest.mark.parametrize(
    "schema",
    [
        ["不是", "对象"],
        {"type": "array"},
        {"type": "object", "properties": "bad"},
        {"type": "object", "required": "bad"},
        {"type": "object", "additionalProperties": "yes"},
        {"type": "object", "bad": object()},
    ],
)
def test_validate_parameters_rejects_malformed_schema(schema: object) -> None:
    """畸形 Schema 抛出 ValueError，不静默放过或猜测。"""

    with pytest.raises(ValueError):
        validate_parameters({}, schema)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 四、注册表 Schema 预校验
# ---------------------------------------------------------------------------


class SchemaTool:
    """带可选 parameters 声明的工具替身：记录调用并可按配置失败。"""

    def __init__(
        self,
        *,
        name: str = "echo",
        description: str = "回声工具",
        parameters: dict[str, object] | None = None,
        result: object = "回声内容",
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.result = result
        self.calls: list[dict[str, object]] = []

    def execute(self, arguments: dict[str, object]) -> str:
        self.calls.append(arguments)
        return self.result  # type: ignore[return-value]


ECHO_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def test_registry_rejects_schema_invalid_arguments_before_execution() -> None:
    """Schema 非法参数在注册表边界以 INVALID_ARGUMENTS 被拒，工具不执行。"""

    tool = SchemaTool(parameters=ECHO_SCHEMA)
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute(
        ToolCall(call_id="call-1", name="echo", arguments={"text": 42})
    )

    assert result.is_success is False
    assert result.content is None
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.retryable is True
    assert "参数校验失败" in result.error.message
    assert "类型应为 string" in result.error.message
    assert tool.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"text": "x", "extra": 1},
    ],
)
def test_registry_rejects_missing_and_extra_keys_before_execution(
    arguments: dict[str, object],
) -> None:
    """缺少必需参数与多余参数都在注册表边界被拒。"""

    tool = SchemaTool(parameters=ECHO_SCHEMA)
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute(
        ToolCall(call_id="call-1", name="echo", arguments=arguments)
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert tool.calls == []


def test_registry_falls_back_to_loose_schema_without_parameters() -> None:
    """未声明 parameters 的工具回退到宽松对象，任意 JSON 对象参数都可执行。"""

    tool = SchemaTool(parameters=None)
    registry = ToolRegistry()
    registry.register(tool)

    result = registry.execute(
        ToolCall(call_id="call-1", name="echo", arguments={"anything": 1})
    )

    assert result.is_success is True
    assert tool.calls == [{"anything": 1}]


@pytest.mark.parametrize(
    "parameters",
    [
        ["不是", "对象"],
        {"bad": object()},
        {"type": "array"},
    ],
)
def test_registry_rejects_malformed_declared_parameters_at_registration(
    parameters: object,
) -> None:
    """声明了畸形 Schema 的工具在注册时就被拒绝，不能混进名册。"""

    registry = ToolRegistry()

    with pytest.raises(ToolRegistrationError):
        registry.register(
            SchemaTool(name="bad", parameters=parameters)  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# 五、工具 Schema 与工具校验一致性交叉测试（Day 18 候选缺口）
# ---------------------------------------------------------------------------


BUSINESS_TOOLS = [
    (CalculatorTool(), {"expression"}, ["expression"]),
    (FileReaderTool(root_directory="C:/allowed"), {"path"}, ["path"]),
    (RetrieveTool(), {"query"}, ["query"]),
    (FinalAnswerTool(), {"content"}, ["content"]),
]


def test_schema_declaration_matches_tool_business_keys() -> None:
    """Schema 声明的属性与 required 和工具业务校验读取的键完全一致。"""

    for tool, expected_keys, expected_required in BUSINESS_TOOLS:
        schema = tool.parameters  # type: ignore[attr-defined]
        assert set(schema["properties"]) == expected_keys
        assert schema["required"] == expected_required
        assert schema["additionalProperties"] is False


STRUCTURAL_INVALID_ARGUMENTS: dict[str, list[dict[str, object]]] = {
    "calculator": [
        {},
        {"expression": 42},
        {"expression": "2", "extra": "x"},
    ],
    "file_reader": [
        {},
        {"path": 42},
        {"path": "a.txt", "extra": "x"},
    ],
    "retrieve": [
        {},
        {"query": 42},
        {"query": "react", "extra": "x"},
    ],
}


def _registry_with(tool_name: str) -> ToolRegistry:
    """构造只注册指定业务工具的注册表。"""

    registry = ToolRegistry()
    if tool_name == "calculator":
        registry.register(CalculatorTool())
    elif tool_name == "file_reader":
        registry.register(FileReaderTool(root_directory="C:/allowed"))
    else:
        registry.register(RetrieveTool())
    return registry


def test_registry_schema_rejects_structural_invalid_arguments() -> None:
    """结构非法参数在注册表边界全部以 INVALID_ARGUMENTS 被拒。"""

    for tool_name, cases in STRUCTURAL_INVALID_ARGUMENTS.items():
        registry = _registry_with(tool_name)
        for arguments in cases:
            result = registry.execute(
                ToolCall(call_id="call-1", name=tool_name, arguments=arguments)
            )
            assert result.is_success is False
            assert result.error is not None
            assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS


def test_business_validation_rejects_same_structural_invalid_arguments() -> None:
    """同一批结构非法参数也被工具业务校验以 ToolArgumentError 拒绝。"""

    tools = {
        "calculator": CalculatorTool(),
        "file_reader": FileReaderTool(root_directory="C:/allowed"),
        "retrieve": RetrieveTool(),
    }
    for tool_name, cases in STRUCTURAL_INVALID_ARGUMENTS.items():
        for arguments in cases:
            with pytest.raises(ToolArgumentError):
                tools[tool_name].execute(arguments)


def test_schema_valid_arguments_reach_business_validation() -> None:
    """通过 Schema 的参数进入业务层，语义规则仍由工具校验兜底。"""

    calculator = _registry_with("calculator")
    result = calculator.execute(
        ToolCall(call_id="call-1", name="calculator", arguments={"expression": "++"})
    )
    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert "语法错误" in result.error.message

    retrieve = _registry_with("retrieve")
    result = retrieve.execute(
        ToolCall(
            call_id="call-1",
            name="retrieve",
            arguments={"query": "unknown-topic"},
        )
    )
    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_EXECUTION_ERROR
    assert result.error.retryable is True


def test_blank_string_passes_schema_but_is_still_rejected_by_business() -> None:
    """Schema 不表达非空约束，空白表达式仍由业务校验以 INVALID_ARGUMENTS 拒绝。"""

    registry = _registry_with("calculator")

    result = registry.execute(
        ToolCall(call_id="call-1", name="calculator", arguments={"expression": "   "})
    )

    assert result.is_success is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert "不能为空" in result.error.message
