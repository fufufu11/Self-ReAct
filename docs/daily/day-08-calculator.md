# Day 8：第一个确定性业务工具——计算器

> Issue：[15 feat: 实现第一个确定性业务工具：计算器](https://github.com/fufufu11/Self-ReAct/issues/15)
>
> 本记录只描述 Day 8 的计算器工具：表达式解析边界、错误出口和注册表消费
> 位置。文件读取、天气检索等其他业务工具属于 Day 9。

## 今天理解了什么

Day 7 建立了工具协议和注册表，但注册表里还没有一个真实业务工具。今天要
回答的问题是：一个"真的能干活"的工具，怎样在"绝不执行任意代码"的约束下
把模型传来的参数字符串变成结果。

计算器接收的输入是字符串表达式，例如 `"2 + 2 * 3"`。Python 里有现成的
`eval` 可以直接算，但 `eval` 会把输入当代码执行：`__import__('os')` 这种
字符串也能跑起来，这正是工具边界绝对不能接受的行为。因此本日采用**受限
AST 白名单**：先让 Python 把表达式解析成一棵语法树（AST），再只允许树里
出现我们认识并同意的节点（数字常量、加减乘除、括号等），其他节点（名字、
函数调用、属性访问等）一律拒绝。这样"看起来像普通算式"的输入才能被求值，
而任何想借表达式执行代码的输入都会在求值前被拦下。

参数问题与运行期问题必须分两个出口。参数**长什么样**不对（不是字符串、
空、语法错误、含未知符号、嵌套过深）属于 `INVALID_ARGUMENTS`，是模型可以
改正的；表达式**算不出来**（除零、结果溢出、指数过大）属于
`TOOL_EXECUTION_ERROR`，是合法式子但在求值时失败。两个出口都由 Day 7 的
注册表统一转换成 `ToolResult`，工具自己只抛出稳定异常，不接触结果对象。

## 今天交付了什么

- 在 [`src/self_react/tools/calculator.py`](../../src/self_react/tools/calculator.py)
  实现 `CalculatorTool`：满足 `Tool` 协议（`name="calculator"`、非空
  `description`、`execute(arguments) -> str`），用受限 AST 白名单解析并
  求值表达式。
- 支持整数与浮点字面量、括号、加/减/乘/除、整除、取模、幂和一元正负号；
  明确拒绝名字、函数调用、属性访问、布尔字面量等非白名单节点。
- 定义四个可解释的资源边界：表达式长度 ≤ 1000 字符、语法树深度 ≤ 100、
  整数结果绝对值 ≤ 10^100、整数幂指数 ≤ 100。
- 错误出口与 Day 7 一致：参数/解析问题抛 `ToolArgumentError` 走
  `INVALID_ARGUMENTS`；除零、结果溢出等运行期问题抛 `ToolExecutionError`
  走 `TOOL_EXECUTION_ERROR`，`retryable=True`。
- 在 [`tests/test_calculator.py`](../../tests/test_calculator.py) 用 45 个
  确定性测试覆盖成功运算、优先级、非法输入、除零、调用编号关联和注册表
  集成，不访问网络、不依赖真实 API。
- 在 [`src/self_react/tools/__init__.py`](../../src/self_react/tools/__init__.py)
  导出 `CalculatorTool`，调用方继续只依赖 `self_react.tools` 一个入口。
- 新增本记录与 [Day 8 计算器代码导读](../architecture/day-08-calculator-code-walkthrough.md)。

## 设计边界与不变量

- 输入契约：`arguments` 必须是 JSON 对象，且只允许 `expression` 一个键；
  值必须是长度在 1 到 1000 之间的非空白字符串。多余键、非字符串、空白或
  超长都会在工具边界被拒。
- 解析边界：只用 `ast.parse(expression, mode="eval")` 得到语法树，再按
  白名单逐节点求值。任何不是 `Constant`、`BinOp`、`UnaryOp` 的节点，以及
  不在运算符白名单里的运算符，都抛 `ToolArgumentError`。
- 运行期边界：除零（含整除和取模）返回 `TOOL_EXECUTION_ERROR`（消息
  "除数不能为零"）；0 的负指数幂、指数超过 100、整数结果超过 10^100、
  浮点结果不是有限数，都返回带安全说明的 `TOOL_EXECUTION_ERROR`。
- `retryable` 语义：上述运行期错误全部 `retryable=True`，因为模型可以换
  一个式子重试；没有把任何失败伪装成成功内容，`content` 始终为 `None`。
- 结果格式：整数直接转字符串；浮点数若是整数值（如 `4 / 2` 得到 `2.0`）
  去掉 `.0` 后缀，否则用 Python 的字符串表示（如 `2 / 4` 得到 `0.5`）。
  浮点二进制精度问题（如 `0.1 + 0.2`）如实保留，不在本日做四舍五入。
- 工具无状态：`CalculatorTool` 不持有注册表、消息或密钥，多个注册表实例
  可以各自注册独立实例，互不影响。

## 遇到的问题与解决过程

### 问题一：不用 `eval`，怎么求值

最早考虑的方案是"参数化运算"：把表达式字符串按运算符拆分再逐段计算。但这
要自己实现优先级和括号，容易出错。最终选择 Python 标准库的 `ast` 模块：
`ast.parse(mode="eval")` 只负责把字符串解析成语法树，不执行任何代码；我们
自己递归遍历这棵树，只对白名单内的节点调用对应的 `operator` 函数。解析和
执行是两步，未知节点在第二步开始前就被拒绝，因此任意代码没有执行机会。

### 问题二：除零算参数错误还是执行错误

`"1 / 0"` 的语法完全合法，问题只出现在求值那一刻，因此把它归为运行期
错误 `TOOL_EXECUTION_ERROR` 而不是 `INVALID_ARGUMENTS`。这样分类清晰：
凡是"输入本身不合规"走参数错误，凡是"输入合规但算不出来"走执行错误，
两者都允许模型换一种方式重试。

### 问题三：怎样防止超长或超深输入拖垮解析和求值

即使语法受限，`"1+1+1+..."` 这种超长链或 `"9 ** 99 ** 99"` 这种大幂也会
消耗大量内存和时间。本日用四个显式上限解决：表达式长度上限挡住超长字符串；
语法树深度上限挡住超深运算链；整数结果绝对值上限挡住天文数字；整数指数
上限挡住幂爆炸。深度检查用显式栈迭代完成，不依赖 Python 的递归限制。

## 验收结果

以下命令已在 Windows、CPython 3.13.5 环境中实际执行：

```powershell
uv sync
uv run self-react hello
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

- `uv sync`：成功，依赖解析无变化。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：成功，98 个测试全部通过（计算器 45、工具层 16、
  DeepSeek 16、LLM 8、领域模型 12、CLI 1）。
- `uv run ruff check .`：根目录唯一失败仍来自受保护的
  `tmp/day04_success_tool_call_demo.py` 导入排序问题；Day 8 文件单独检查
  通过。
- `uv run ruff format --check .`：根目录失败仍来自受保护的 Day 4 导读和
  Day 6 导读示例格式；Day 8 文件单独检查通过。
- `git diff --check`：成功，无空白错误。

与 Day 6/Day 7 一致，全仓库检查的两个 Ruff 例外均来自开始前已存在且明确
受保护的文件，没有修改、暂存或删除它们。Day 8 文件在只包含仓库基线和本
Issue 文件的干净副本中复验通过：从基线提交 `bd40b0f` 创建临时仓库副本，
只复制本 Issue 的 5 个变更文件（`src/self_react/tools/` 两个文件、
`tests/test_calculator.py`、两份 Day 8 文档），再次执行完全相同的六条
命令，结果如下：

- `uv sync`：成功创建隔离环境，解析并安装 24 个包。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：成功，98 个测试全部通过。
- `uv run ruff check .`：成功，输出 `All checks passed!`。
- `uv run ruff format --check .`：成功，确认 37 个文件均已格式化。
- `git diff --check`：成功，无空白错误。

临时副本在验证后已删除。这个结果验证的是仓库基线加 Day 8 Issue 文件，不
包含原工作区受保护的 Day 4 导读、Day 6 导读、交接文档和 `tmp/`。

## 不在范围内

- 文件读取、天气检索等其他业务工具（Day 9）。
- Agent 主循环、提示词、输出解析、重试、流式或异步能力。
- 修改 `LLM.complete` 接口、Day 4 领域模型或 Day 6 DeepSeek 适配器。
- 浮点结果的四舍五入、科学计数法美化或单位换算等展示增强。

## 明天要验证什么

- 实现受限文件读取工具和确定性检索/模拟工具（Day 9），并继续复用 Day 7
  的协议、注册表和错误出口。
- 确认多个工具共存时，注册表的名册、未知工具消息和错误分类仍然一致。
- 为 Day 10 的提示词准备每个工具的真实 `description` 作为模型可读说明。
