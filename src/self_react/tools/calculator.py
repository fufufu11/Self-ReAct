"""确定性计算器业务工具（Day 8）。

计算器只接受一个字符串表达式参数，使用受限 AST 白名单解析并求值，绝不
调用 ``eval``/``exec``。允许的语法元素是白名单内的常量、二元运算、一元
运算和括号；未知的节点类型一律在求值前被拒绝。表达式长度、语法树深度和
中间结果的大小都有上限，防止输入把解析或求值拖垮。

参数校验失败抛 ``ToolArgumentError``（注册表转 ``INVALID_ARGUMENTS``）；
除零、结果溢出等运行期失败抛 ``ToolExecutionError``（注册表转
``TOOL_EXECUTION_ERROR``）。工具本身不接触 ``Message``、``AgentState``
或注册表。
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Callable

from self_react.models import JsonObject
from self_react.tools.base import ToolArgumentError, ToolExecutionError

MAX_EXPRESSION_LENGTH = 1_000
"""表达式字符串的最大字符数，防止超长输入。"""

MAX_AST_DEPTH = 100
"""语法树的最大节点链深度，防止一长串运算把递归求值拖垮。"""

MAX_ABS_INT = 10**100
"""整数中间结果与最终结果的最大绝对值，保证结果可安全转成字符串。"""

MAX_POW_EXPONENT = 100
"""整数幂运算的最大指数，防止幂运算产生不可控的巨大整数。"""

_BINARY_OPERATORS: dict[type[ast.operator], Callable[[object, object], object]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
"""允许的二元运算符白名单（幂运算单独处理）。"""

_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[object], object]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
"""允许的一元运算符白名单。"""


def _extract_expression(arguments: JsonObject) -> str:
    """从参数字典中取出并校验表达式字符串。"""

    unexpected = sorted(set(arguments) - {"expression"})
    if unexpected:
        raise ToolArgumentError(f"不支持的参数：{', '.join(unexpected)}")

    expression = arguments.get("expression")
    if not isinstance(expression, str):
        raise ToolArgumentError("expression 必须是字符串")
    if not expression.strip():
        raise ToolArgumentError("expression 不能为空")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ToolArgumentError("表达式过长")
    return expression


def _check_depth(root: ast.AST) -> None:
    """迭代检查语法树深度，超过上限时拒绝输入。"""

    stack: list[tuple[ast.AST, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_AST_DEPTH:
            raise ToolArgumentError("表达式嵌套过深")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))


def _parse_expression(expression: str) -> ast.AST:
    """把表达式解析成语法树，并在进入求值前做语法与深度检查。"""

    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        message = getattr(exc, "msg", None) or "无效的表达式"
        raise ToolArgumentError(f"表达式语法错误：{message}") from exc

    root = tree.body
    _check_depth(root)
    return root


def _guard_result(result: object) -> int | float:
    """限制结果范围：整数有绝对值上限，浮点数必须有限。"""

    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise ToolExecutionError("计算结果不是数字")
    if isinstance(result, int):
        if abs(result) > MAX_ABS_INT:
            raise ToolExecutionError("计算结果超出可表示范围", retryable=True)
        return result
    if not math.isfinite(result):
        raise ToolExecutionError("计算结果超出可表示范围", retryable=True)
    return result


def _apply_binary(
    binary_op: Callable[[object, object], object],
    left: int | float,
    right: int | float,
) -> int | float:
    """执行二元运算并把运行期错误转成稳定工具异常。"""

    try:
        return _guard_result(binary_op(left, right))
    except ZeroDivisionError as exc:
        raise ToolExecutionError("除数不能为零", retryable=True) from exc
    except (ValueError, OverflowError) as exc:
        raise ToolExecutionError("无法计算该运算", retryable=True) from exc


def _apply_power(base: int | float, exponent: int | float) -> int | float:
    """执行幂运算，并限制整数指数的范围。"""

    if isinstance(base, int) and isinstance(exponent, int):
        if base == 0 and exponent < 0:
            raise ToolExecutionError("0 不能做负指数幂", retryable=True)
        if exponent >= 0 and exponent > MAX_POW_EXPONENT:
            raise ToolExecutionError("指数过大", retryable=True)

    try:
        return _guard_result(operator.pow(base, exponent))
    except ZeroDivisionError as exc:
        raise ToolExecutionError("0 不能做负指数幂", retryable=True) from exc
    except (ValueError, OverflowError) as exc:
        raise ToolExecutionError("无法计算该运算", retryable=True) from exc


def _evaluate(node: ast.AST) -> int | float:
    """递归求值白名单内的语法树节点。"""

    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolArgumentError("表达式只支持整数和浮点数字面量")
        return value

    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if type(node.op) is ast.Pow:
            return _apply_power(left, right)
        binary_op = _BINARY_OPERATORS.get(type(node.op))
        if binary_op is None:
            raise ToolArgumentError(f"不支持的运算符：{type(node.op).__name__}")
        return _apply_binary(binary_op, left, right)

    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand)
        unary_op = _UNARY_OPERATORS.get(type(node.op))
        if unary_op is None:
            raise ToolArgumentError(f"不支持的运算符：{type(node.op).__name__}")
        try:
            return _guard_result(unary_op(operand))
        except (ValueError, OverflowError) as exc:
            raise ToolExecutionError("无法计算该运算", retryable=True) from exc

    raise ToolArgumentError(f"不支持的表达式元素：{type(node).__name__}")


def _format_result(result: int | float) -> str:
    """把数字结果格式化为模型可读文本。"""

    if isinstance(result, int):
        return str(result)
    if result.is_integer():
        return str(int(result))
    return str(result)


class CalculatorTool:
    """基于受限 AST 的确定性计算器业务工具。

    工具本身无状态：``execute`` 只根据参数字典里的 ``expression`` 字符串做
    解析和求值，返回模型可读的结果文本。所有失败都通过
    ``ToolArgumentError`` 或 ``ToolExecutionError`` 表达，由注册表统一
    转换成 ``ToolResult``。
    """

    name = "calculator"
    description = (
        "计算一个算术表达式，例如 2 + 2 * 3。支持加、减、乘、除、整除、取模、幂和括号。"
    )
    parameters: JsonObject = {
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

    def execute(self, arguments: JsonObject) -> str:
        """执行一次计算器调用并返回结果字符串。"""

        expression = _extract_expression(arguments)
        tree = _parse_expression(expression)
        result = _evaluate(tree)
        return _format_result(result)


__all__ = [
    "CalculatorTool",
    "MAX_ABS_INT",
    "MAX_AST_DEPTH",
    "MAX_EXPRESSION_LENGTH",
    "MAX_POW_EXPONENT",
]
