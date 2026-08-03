# Day 7：工具接口与工具注册表

> Issue：[13 feat: 实现工具接口与工具注册表](https://github.com/fufufu11/Self-ReAct/issues/13)
>
> 本记录只描述 Day 7 的最小工具层：工具协议、注册表和统一调用边界。具体
> 业务工具、Agent 主循环、提示词和输出解析都不在本日范围内。

## 今天理解了什么

工具层要回答的是"模型说要调用工具之后，系统怎么安全地把它变成一次真实的
调用"。Day 4 的 `ToolCall` 只是模型表达的意图：它带着调用编号、工具名和
参数字典，但没有执行能力。Day 5 和 Day 6 的 LLM 适配器只负责把模型回答
翻译回 `Message`，也坚决不执行工具。因此需要一个独立于模型供应商的工具层，
它把 `ToolCall` 翻译成统一的 `ToolResult`。

工具层由两个角色组成：**协议**和**注册表**。协议（`Tool`）是一份"合同"，
规定工具必须提供精确名称、给模型的说明和执行方法；注册表
（`ToolRegistry`）是一本"花名册"，只允许从登记过的名称里找工具，绝不根据
名字动态导入或执行任意代码。模型拿不到 Python 函数，只能拿到名称字符串，
这正是安全边界的关键：注册表用精确字符串做字典查找，而不是把名称当代码跑。

统一调用边界的价值在于"所有出路都走同一扇门"：成功返回字符串、参数不对、
工具自己报错、工具抛了意料之外的异常，全都变成带稳定错误码和
`retryable` 的 `ToolResult`。调用方（未来的 Agent）只需要看错误码和
可恢复性做路由，不需要匹配异常文本。系统级的 `KeyboardInterrupt` 和
`SystemExit` 不属于普通异常，必须继续向上传播，不能伪装成工具观察。

## 今天交付了什么

- 在 [`src/self_react/tools/`](../../src/self_react/tools/) 新增工具层：
  - `Tool` 协议（`name`、`description`、`execute(arguments) -> str`）；
  - `ToolRegistry` 注册表，支持 `register`、`get`、`names`、`in` 和唯一的
    执行入口 `execute(ToolCall) -> ToolResult`；
  - `ToolArgumentError`、`ToolExecutionError` 和
    `ToolRegistrationError` 三个稳定异常。
- 在 [`tests/test_tools.py`](../../tests/test_tools.py) 用确定性
  `FakeTool` 替身覆盖成功调用、未知工具、非法参数、执行异常、非字符串
  返回、系统级异常传播、重复/空名称注册、注册表隔离、注册后外部修改、
  协议替换、领域输入约束、结果序列化和 FakeLLM 消费链路。
- 新增 [Day 7 工具层代码导读](../architecture/day-07-tool-registry-code-walkthrough.md)，
  按"先见森林、再见树木"的方式讲解协议、注册表、错误边界和测试。
- 没有修改 `LLM.complete` 接口、Day 4 领域模型、Day 6 DeepSeek 适配器或
  `self-react hello` 行为。

## 设计边界与不变量

- 工具调用输入必须来自领域 `ToolCall`；`execute` 收到其他对象直接抛
  `TypeError`。
- 注册表按精确名称查找；未知工具返回 `UNKNOWN_TOOL`（`retryable=True`），
  消息同时包含被请求的名称和允许使用的名称，绝不执行动态名称。
- 参数校验失败返回 `INVALID_ARGUMENTS`（`retryable=True`），工具自报的
  说明进入 `error.message`，不进入成功 `content`。
- 普通执行异常返回 `TOOL_EXECUTION_ERROR` 和稳定消息，不泄露原始异常文本；
  `ToolExecutionError` 允许工具携带安全说明和 `retryable`。
- 工具必须返回字符串；返回其他类型按协议违约处理，不把对象写进结果。
- `KeyboardInterrupt`、`SystemExit` 向上传播，不被吞掉。
- 注册时固定名称：重复名称、空名称和缺少协议成员的对象在 `register` 时被
  拒绝；注册后外部修改工具对象不会改变注册表键。
- 注册表持有工具对象是运行时行为，但永远不会把工具、客户端、密钥或注册表
  对象写进 `Message`、`AgentState` 或可序列化的 `ToolResult`。

## 遇到的问题与解决过程

### 问题一：`ToolError` 异常和领域模型里的 `ToolError` 会不会撞名

Day 4 的 `models.py` 已经有一个叫 `ToolError` 的 Pydantic 结构，它表示
"失败结果里的结构化信息"（错误码、说明、可恢复性）。如果工具异常也叫
`ToolError`，初学者容易混淆。因此本日把工具抛出的异常命名为
`ToolArgumentError`、`ToolExecutionError` 和 `ToolRegistrationError`，
没有复用 `ToolError` 这个名字，避免同名不同物。

### 问题二：参数校验应该由谁负责

曾考虑在注册表里放一套通用参数模式（schema）来统一校验，但 Day 8/9 才有
具体工具，现在引入 schema 语言会过早复杂化。本日选择让工具自己校验参数：
发现参数不对就抛 `ToolArgumentError`，注册表负责把它翻译成
`INVALID_ARGUMENTS`。错误边界统一在注册表，业务校验留在工具，两边各管
一段，符合 Day 7 的最小范围。

### 问题三：工具返回非字符串怎么处理

`Tool` 协议规定 `execute` 返回字符串，但运行时可能出现工具作者返回字典或
`None` 的情况。注册表在拿到返回值后检查类型，非字符串按协议违约处理为
`TOOL_EXECUTION_ERROR` 且 `retryable=False`：这是代码 bug，模型重试没有
意义，但调用方仍然得到稳定的失败结果而不是崩溃。

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

- `uv sync`：成功，解析并检查 24 个包，重新构建当前项目。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：成功，53 个测试全部通过（工具层 16 个、DeepSeek 16
  个、LLM 8 个、领域模型 12 个、CLI 1 个）。
- `uv run ruff check .`：根目录唯一失败来自受保护的
  `tmp/day04_success_tool_call_demo.py` 导入排序问题；Day 7 文件单独检查
  通过。
- `uv run ruff format --check .`：根目录失败来自受保护的 Day 4 导读和
  Day 6 导读示例格式；Day 7 文件单独检查通过。
- `git diff --check`：成功，无空白错误。

与 Day 6 一致，全仓库检查的两个 Ruff 例外均来自开始前已存在且明确受保护的
文件，没有修改、暂存或删除它们。Day 7 文件还在只包含仓库基线和本 Issue
文件的干净副本中复验通过，结果如下：

从基线提交 `5962f45` 创建临时仓库副本，只复制本 Issue 的 5 个变更文件
（`src/self_react/tools/` 两个文件、`tests/test_tools.py`、两份 Day 7
文档），并再次执行完全相同的六条命令：

- `uv sync`：成功创建隔离环境，解析并安装 24 个包。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：成功，53 个测试全部通过，其中工具层 16 个。
- `uv run ruff check .`：成功，输出 `All checks passed!`。
- `uv run ruff format --check .`：成功，确认 33 个文件均已格式化。
- `git diff --check`：成功，无空白错误。

临时副本在验证后已删除。这个结果验证的是仓库基线加 Day 7 Issue 文件，不
包含原工作区受保护的 Day 4/5/6 导读和 `tmp/`。

## 不在范围内

- 计算器、文件读取、天气等具体业务工具（Day 8/9）；
- Agent 主循环、提示词、输出解析、重试、流式或异步能力（Day 10 起）；
- 修改 `LLM.complete` 接口或 DeepSeek 适配器；
- 通用参数模式（schema）校验系统、并行工具调度或持久化。

## 明天要验证什么

- 基于本日 `Tool` 协议实现第一个确定性业务工具：计算器；
- 覆盖非法表达式、除零等边界，并确认这些边界都走
  `INVALID_ARGUMENTS` 或 `TOOL_EXECUTION_ERROR` 的统一出口；
- 确认计算器注册进 `ToolRegistry` 后，`ToolCall` 能端到端得到 `ToolResult`
  并转成 `Observation`。
