# Day 14：鲁棒性（模型超时、重复动作与回归测试）

> Issue：[27 feat: 补齐鲁棒性错误分支与回归测试](https://github.com/fufufu11/Self-ReAct/issues/27)
>
> 本记录只描述 Day 14 的鲁棒性补全：模型超时/连接失败按原样传播、重复动作
> 在分派前被拦截并作为可恢复观察回写，以及解析失败、步数耗尽、工具异常等
> 既有边界的回归测试。持久化、暂停/恢复、流式、异步和并行调度仍不属于本期。

## 今天理解了什么

Day 12 的主循环已经能跑通"模型 -> 解析 -> 工具 -> Observation -> 模型"，
Day 13 能把终态渲染成人可读的中文文本。但有几类"运行时事故"还没有被
显式验证过：模型请求超时或网络断了怎么办？模型在同一轮里重复请求同一个
动作怎么办？这些分支一旦发生，主循环必须给出**可复述、可测试**的稳定行为，
而不是让异常随机泄漏到调用方。

第一个关键认识是**模型层错误和工具层错误是两个不同边界**。`LLM.complete`
抛出的适配器错误（超时、连接失败、认证失败等）不是一次"可以写回观察的
工具失败"：模型调用这一轮根本没有产出任何决策，主循环既没有可解析的输出，
也没有可回写的工具结果。Day 12 已经约定"适配器错误按原样向上传播、不私自
重试"，Day 14 用回归测试把这条边界钉死：`TIMEOUT`/`CONNECTION` 等稳定类别
从 DeepSeek 适配器一路传到 `Agent.run` 的调用方，类型与错误码都不变。

第二个关键认识是**重复动作应该由主循环在分派前拦截**。如果让模型再次调用
同一个工具，工具本身是"无状态、确定性的"，重复执行一次通常不会报错，但会
白白消耗步数和上下文；更糟的是同一 `call_id` 复用会破坏"一次调用对应一条
结果"的关联语义。Day 14 给主循环加了一道闸门：同一 `call_id` 在任意更早
步骤中使用过，或者同一工具紧挨着使用完全相同的参数，就在进入注册表之前
被识别，转成带 `REPEATED_ACTION` 错误码、`retryable=True` 的失败观察回写，
让模型在预算内换一种方式继续。

第三个关键认识是**鲁棒性不等于加功能**。解析失败默认不重试、步数耗尽兜底、
工具异常统一转 `ToolResult` 这些边界在 Day 7/11/12 已经实现；Day 14 的
价值是给它们补上回归测试，并确认"新增错误分支不改变接口"：渲染层只增加
一行标签映射，`render_trace(state) -> str` 的签名和输出格式完全不变。

## 今天交付了什么

- [`src/self_react/models.py`](../../src/self_react/models.py)：`ToolErrorCode`
  新增 `REPEATED_ACTION = "REPEATED_ACTION"`，与 `UNKNOWN_TOOL`、
  `TOOL_EXECUTION_ERROR` 一样是稳定错误类别。
- [`src/self_react/agent.py`](../../src/self_react/agent.py)：新增
  `_repeated_action_reason(decision, state)`，在 `registry.execute` 之前检查
  两种重复形态——同一 `call_id` 复用、同一工具连续使用相同参数；命中时用
  `ToolResult.failure(code=REPEATED_ACTION, retryable=True)` 直接构造失败
  结果，走与普通工具失败完全相同的观察回写路径，不执行工具。
- [`src/self_react/trace.py`](../../src/self_react/trace.py)：`_TOOL_ERROR_LABELS`
  增加 `REPEATED_ACTION: "重复动作"`；渲染接口与既有输出格式不变。
- 新增/扩展测试共 12 个：
  - `tests/test_agent.py`：模型超时/连接失败按原样传播（参数化 `TIMEOUT`、
    `CONNECTION`）；同一 `call_id` 复用先回写失败观察再恢复；同一工具连续
    相同参数被识别；重复动作不触达工具层（用带计数的计算器断言）；重复动作
    后预算耗尽兜底为 `MAX_STEPS_EXCEEDED`；中间隔了其他动作的相同参数调用
    不误判；非连续但复用 `call_id` 仍被拦截；
  - `tests/test_trace.py`：重复动作观察渲染出 `重复动作（REPEATED_ACTION）`
    中文标签与 `可重试：是`；
  - `tests/test_models.py`：`REPEATED_ACTION` 错误类别可构造失败结果并转成
    失败观察；
  - `tests/test_tools.py`：重复动作失败结果保持稳定错误码；
  - `tests/test_deepseek.py`：`TIMEOUT` 与 `CONNECTION` 是互不混淆的稳定类别。
- 新增本记录与 [Day 14 代码导读](../architecture/day-14-robustness-code-walkthrough.md)。
- 没有修改 `LLM.complete` 接口、Day 6 DeepSeek 适配器的错误传播语义、Day 10
  提示词、Day 11 解析器或三个已有业务工具；没有实现持久化、暂停/恢复、流式、
  异步或并行调度。

## 设计边界与不变量

- **错误分层**：`LLM.complete` 抛出的适配器错误（含 `TIMEOUT`/`CONNECTION`
  稳定类别）按原样向上传播，主循环不重试、不吞掉、不转成观察；工具层错误
  才统一转 `ToolResult` 并回写观察。
- **重复动作拦截**：主循环是唯一控制者，在分派前识别重复动作；`REPEATED_ACTION`
  永远 `retryable=True`，先回写观察，预算内继续，预算耗尽兜底。
- **接口稳定**：新增错误分支不改 `Agent.run`、`render_trace` 的签名；渲染层
  只需在标签映射表补一行。
- **确定性**：所有新测试使用 Fake LLM 与确定性工具（含带调用计数的计算器），
  不访问网络、不依赖真实 API。
- **不越界**：不实现自动纠错重试、持久化、暂停/恢复、流式、异步或并行调度。

## 遇到的问题与解决过程

### 问题一：重复动作应该算"可恢复"还是"不可恢复"

如果把重复动作直接判为不可恢复并终止，模型一旦手滑就会结束整次运行，太
严厉；如果完全不拦截，又会白耗步数并破坏 `call_id` 关联语义。参考 Day 12
对未知工具的处理：未知工具也是 `retryable=True`，先作为观察回写，让模型
纠正。重复动作同样属于"模型可以纠正的失误"，因此 `REPEATED_ACTION` 固定
`retryable=True`，循环预算负责兜底。

### 问题二：检测放在哪个边界才不会让工具层"多管闲事"

重复动作的语义是"这次调用不该发生"，不是"工具执行失败了"。如果放在注册表
里检测，注册表就得感知历史轨迹，违背 Day 7"注册表只做一次调用的统一转换"
的边界。解决：检测放在主循环的 `_repeated_action_reason`，在
`registry.execute` 之前完成；注册表和业务工具完全不知道重复动作的存在，
仍然只处理"单次调用"。

### 问题三：失败观察的消息要不要带具体参数值

连续相同参数的场景，把参数原样拼进消息会让模型更容易理解，但也可能把
敏感参数写进上下文。Day 13 的安全原则是"人类可读不等于全量输出"。解决：
消息只说明工具名和"已用相同参数调用过，请更换参数或使用新编号"，不拼参数
值；`call_id` 复用场景则只带编号本身。

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

- `uv sync`：成功，锁文件无变化。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：成功，290 个测试通过、3 个跳过（符号链接用例，与 Day 13
  相同），其中相比 Day 13 新增 12 个：Agent 主循环 30 个、轨迹渲染 19 个、
  领域模型 13 个、工具层 17 个、DeepSeek 17 个。
- `uv run ruff check .`：根目录唯一失败仍来自受保护的
  `tmp/day04_success_tool_call_demo.py` 导入排序问题；`src/` 与 `tests/`
  单独检查通过（`All checks passed!`）。
- `uv run ruff format --check .`：根目录两个失败仍来自受保护的 Day 4 导读和
  Day 6 导读示例格式；`src/` 与 `tests/` 单独检查通过（26 个文件均已格式化）。
- `git diff --check`：成功，无空白错误。

与 Day 6 至 Day 13 一致，全仓库检查的 Ruff 例外均来自开始前已存在且明确
受保护的文件，没有修改、暂存或删除它们。

Day 14 文件还在只包含仓库基线和本 Issue 文件的干净副本中复验：从基线提交
`87de91a` 创建临时工作树，只复制本 Issue 的变更文件（`src/self_react/models.py`、
`src/self_react/agent.py`、`src/self_react/trace.py`、五个测试文件与两份
Day 14 文档），再次执行完全相同的六条命令，结果如下：

- `uv sync`：成功创建隔离环境，解析并安装 24 个包，构建当前项目。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：290 个测试通过、3 个跳过（符号链接用例，与主工作区相同）。
- `uv run ruff check .`：成功，输出 `All checks passed!`。
- `uv run ruff format --check .`：成功，确认 61 个文件均已格式化。
- `git diff --check`：成功，无空白错误。

临时工作树在验证后已删除。这个结果验证的是仓库基线加 Day 14 Issue 文件，
不包含原工作区受保护的 Day 4/5/6/10 导读、交接文档和 `tmp/`。

## 明天要验证什么

- Day 15 命令行体验：把 `render_trace` 接入 CLI，让 `agent`/`run` 子命令
  支持任务输入、模型配置、最大步数与是否展示轨迹等参数。
- 端到端示例（Day 16）：用真实 DeepSeek 调用跑一次多轮工具任务，验证重复
  动作拦截和可恢复失败回写在真实模型下也成立。
