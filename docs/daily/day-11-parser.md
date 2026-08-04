# Day 11：JSON 输出解析器

> Issue：[21 feat: 实现 JSON 输出解析器](https://github.com/fufufu11/Self-ReAct/issues/21)
>
> 本记录只描述 Day 11 的输出解析器：它把模型原始 JSON 字符串按 Day 10 的
> 格式契约解析成 Day 4 领域对象。Agent 主循环（Day 12）、重试、流式和异步
> 能力属于后续日期。

## 今天理解了什么

Day 10 的提示词只负责"要求模型输出什么形状"，Day 12 的主循环只负责"拿到
结果后怎么走下一步"。两者之间缺一个**机械拆信员**：模型返回的原始输出是
一个字符串，哪怕格式完全正确，也不能直接当成 `FinalAnswer` 或 `ToolCall`
使用——必须先验证字符串真的是 JSON 对象、`kind` 真的是两种合法取值、每个
字段真的符合契约，然后才能构造领域对象。

解析器的职责边界有三条：

1. **只拆信，不判断工具**：解析器不知道注册表里有哪些工具。`name` 是任何
   字符串它都接受，未知工具由 Day 7 注册表在分派阶段返回 `UNKNOWN_TOOL`。
   这样"格式是否正确"和"工具是否存在"是两个独立问题，各自有稳定的处理
   出口。
2. **只返回完整对象，绝不返回残缺对象**：字段缺失、类型错误、多余字段、
   `kind` 非法，全都返回稳定解析错误。调用方永远不需要处理"半成品的
   ToolCall"。
3. **错误必须稳定且安全**：解析错误与 `TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR`
   对齐，方便 Day 12 记录轨迹；错误消息只写"缺哪个契约字段、是哪类问题"，
   不携带模型原始输出、原始异常对象或堆栈。

另一个关键认识是**纯函数**的价值：解析不访问网络、不读取环境变量、不修改
任何状态，相同输入永远得到相同输出。这让 Day 12 的循环行为可复现，也让
解析器可以用纯字符串做单元测试，根本不需要真实模型。

## 今天交付了什么

- 新增 [`src/self_react/parser.py`](../../src/self_react/parser.py)：
  - `ParseError`：稳定解析错误，`code` 固定为
    `TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR`；
  - `parse_decision(raw)`：确定性纯函数，接受模型原始字符串，返回
    `FinalAnswer` 或 `ToolCall`；非法输入抛 `ParseError`（非字符串输入抛
    `TypeError`，因为那是调用方错误而不是模型输出问题）；
  - 内部小函数 `_parse_json_object`、`_reject_unexpected_fields`、
    `_parse_final_answer`、`_parse_tool_call`，把"解析 JSON、拒绝多余字段、
    构造两种领域对象"拆成可以单独读懂的步骤。
- 新增 [`tests/test_parser.py`](../../tests/test_parser.py)：56 个用例，
  覆盖合法 `final_answer`、合法 `tool_call`、非 JSON、JSON 不是对象、
  `kind` 缺失或非法、字段缺失、类型错误、空白字段、多余字段、NaN 参数、
  非字符串输入、错误码对齐、消息不泄漏，以及 Fake LLM 的合法/缺字段/未知
  工具三类输入。
- 新增本记录与 [Day 11 解析器代码导读](../architecture/day-11-parser-code-walkthrough.md)。

## 设计边界与不变量

- **输入契约**：`parse_decision` 只接受字符串；`None`、数字、列表、字节串
  等非字符串输入直接抛 `TypeError`，不进入解析流程。
- **输出契约**：合法 `final_answer` 构造 `FinalAnswer(content)`，合法
  `tool_call` 构造 `ToolCall(call_id, name, arguments)`，字段值保持原样。
- **完整对象**：字段缺失、类型错误、空白标识、多余字段、`kind` 缺失或非法
  全部返回 `ParseError`，绝不返回残缺领域对象。
- **严格形状**：`final_answer` 只允许 `kind` 与 `content` 两个字段，
  `tool_call` 只允许 `kind`、`call_id`、`name`、`arguments` 四个字段；多余
  字段说明模型没有遵守契约，一律拒绝。
- **错误安全**：`ParseError.code is TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR`；
  错误消息是固定中文说明，不含模型原始输出、异常类名、`Traceback` 或堆栈。
- **确定性**：解析是纯函数，相同输入两次解析结果完全一致；不访问网络、
  不读取环境变量、不修改输入。
- **职责边界**：解析器不查注册表、不执行工具、不构造 `ToolResult` 或
  `Observation`；未知工具由注册表在分派阶段返回 `UNKNOWN_TOOL`。

## 遇到的问题与解决过程

### 问题一：解析失败应该抛异常还是返回结果对象

曾考虑让 `parse_decision` 返回"结果或错误对象"的联合类型。但项目现有模块
（LLM 用 `LLMResponseError`、工具用 `ToolArgumentError` 等）都用稳定异常
表达失败，调用方按类型捕获；解析器沿用同一风格更一致。最终选择抛
`ParseError`，并把 `code` 属性固定为 `TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR`，
Day 12 捕获后可以直接记录成 `TraceError`。

### 问题二：怎样避免把 Pydantic 的 `ValidationError` 泄漏给调用方

构造领域对象时，`FinalAnswer`/`ToolCall` 自己会校验非空和可序列化性。如果
把这些校验异常原样抛出，调用方会看到 `ValidationError` 及其细节，违反"不
泄漏原始异常"的要求。解决：在构造处 `try/except ValidationError`，转成
`ParseError`，并用 `from None` 切断异常链；测试专门断言错误消息里没有
`Traceback` 和 `ValidationError`。

### 问题三：多余字段要不要拒绝

提示词契约只写两种精确 JSON 形状，领域模型也设置 `extra="forbid"`。如果
解析器容忍多余字段，等于悄悄放宽了格式契约，模型会越写越随意。解决：
解析器在构造对象前显式检查字段集合，契约之外的字段一律返回稳定错误
（`模型输出包含格式契约之外的字段`），两种决策共用同一个拒绝函数。

### 问题四：错误消息要不要包含模型原始输出

包含原始输出看似方便调试，但原始输出可能很长、可能含敏感内容，而且每个
非法输入的错误消息都会不同，破坏"稳定错误"承诺。解决：错误消息只写
"缺哪个契约字段/是哪类问题"，不包含任何原始输出。react-loop 文档要求记录
"经安全截断的原始输出"，那是 Day 12 写轨迹时的责任，不属于解析器异常消息。

## 验收结果

以下命令已在 Windows、CPython 3.13.5 环境中实际执行（`uv` 与 `.venv`
命令等价）：

```powershell
uv sync
uv run self-react hello
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

- `uv sync`：成功，解析并检查 24 个包，锁文件无变化。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：成功，236 个测试通过、3 个跳过（符号链接用例，与 Day 9/10
  相同），其中解析器 56 个、提示词 15 个、文件读取 47 个、检索 23 个、计算器
  45 个、工具层 16 个、DeepSeek 16 个、LLM 8 个、领域模型 12 个、CLI 1 个。
- `uv run ruff check .`：根目录唯一失败仍来自受保护的
  `tmp/day04_success_tool_call_demo.py` 导入排序问题；Day 11 文件单独检查
  通过。
- `uv run ruff format --check .`：根目录失败仍来自受保护的 Day 4 导读和
  Day 6 导读示例格式；Day 11 文件单独检查通过。
- `git diff --check`：成功，无空白错误。

与 Day 6 至 Day 10 一致，全仓库检查的两个 Ruff 例外均来自开始前已存在且
明确受保护的文件，没有修改、暂存或删除它们。

Day 11 文件还在只包含仓库基线和本 Issue 文件的干净副本中复验：从基线提交
`77650e7` 创建临时工作树，只复制本 Issue 的 4 个变更文件
（`src/self_react/parser.py`、`tests/test_parser.py`、两份 Day 11 文档），
再次执行完全相同的六条命令，结果如下：

- `uv sync`：成功创建隔离环境，解析并安装 24 个包，构建当前项目。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：236 个测试通过、3 个跳过（符号链接用例，与主工作区相同）。
- `uv run ruff check .`：成功，输出 `All checks passed!`。
- `uv run ruff format --check .`：成功，确认 51 个文件均已格式化。
- `git diff --check`：成功，无空白错误。

临时工作树在验证后已删除。这个结果验证的是仓库基线加 Day 11 Issue 文件，
不包含原工作区受保护的 Day 4/5/6 导读、交接文档和 `tmp/`。

## 不在范围内

- Agent 主循环（Day 12）、重试、流式或异步能力。
- 修改 `LLM.complete` 接口、Day 4 领域模型、Day 6 DeepSeek 适配器、Day 10
  提示词或三个已有工具（计算器、文件读取、检索）。
- 工具名与注册表的合法性校验：未知工具由注册表返回 `UNKNOWN_TOOL`。
- 代码围栏剥离、格式纠错、模糊容错等提示词契约之外的宽容解析。

## 明天要验证什么

- Day 12 主循环如何消费 `parse_decision` 的结果，并把 `ParseError` 记录成
  `TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR` 的轨迹步骤。
- 确认解析结果能直接作为 `Decision` 进入 `TraceStep`，`ToolCall` 再走注册表
  得到 `ToolResult` 并转回 `Observation`。
- 用 Fake LLM 端到端覆盖"模型输出 -> 解析 -> 注册表 -> Observation"的
  完整链路。
