# Day 12：ReAct 主循环

> Issue：[23 feat: 实现 ReAct 主循环](https://github.com/fufufu11/Self-ReAct/issues/23)
>
> 本记录只描述 Day 12 的最小 Agent 主循环：把"模型 -> 解析 -> 工具 ->
> Observation -> 模型"串成一个有界闭环。重试、流式、异步、持久化和并行工具
> 调度属于后续日期。

## 今天理解了什么

前面十一天把每个零件都做好了：Day 4 定义了领域模型，Day 5 抽象了 `LLM`
接口，Day 7 建好了工具注册表，Day 10 规定了模型输出格式，Day 11 能解析模型
输出。但还没有人把它们**按顺序接起来**。Day 12 的 Agent 主循环就是那根
总线：每一轮先看还有没有步数预算，有就调用模型，把模型原始输出解析成决策，
是最终回答就结束，是工具调用就交给注册表执行，把结果转成观察写回消息上下文，
然后回到循环开头。

主循环最重要的设计是**只有一个控制者**。步数计数、预算检查和终止判断全部
收在 `Agent` 里，`AgentState` 只当"记账本"，其他模块（LLM、解析器、工具）
都无权私自开始重试循环。这样"为什么停"永远只有一个答案：给了最终回答、
解析失败、遇到不可恢复的工具失败，或者步数耗尽。

第二个关键认识是**可恢复错误不等于终止**。未知工具和大多数工具失败先作为
带错误码与 `retryable` 的 `Observation` 写回模型，让模型换一种方式继续；
只有预算耗尽或失败明确不可恢复时，才成为终止原因。解析失败则例外：MVP
默认策略是直接记录 `MODEL_OUTPUT_PARSE_ERROR` 终止，不猜、不补、不改写
模型输出。

## 今天交付了什么

- 新增 [`src/self_react/agent.py`](../../src/self_react/agent.py)：
  - `Agent` 类：持有 `LLM`、`ToolRegistry` 和 `max_steps`，构造时校验三者；
  - `Agent.run(task) -> AgentState`：执行一次完整运行并返回终态；
  - 每轮先检查预算，再调用 `LLM.complete`，用 Day 11 的 `parse_decision`
    解析 `response.content`；
  - `FinalAnswer` -> 记录轨迹、保存 `final_answer`、以 `FINAL_ANSWER` 终止；
  - `ToolCall` -> 交给 Day 7 注册表执行 -> `Observation.from_tool_result`
    转观察 -> 以 tool 消息写回上下文 -> 不可恢复失败按错误码映射终止原因，
    否则进入下一轮；
  - `ParseError` -> 记录 `TraceErrorCode.MODEL_OUTPUT_PARSE_ERROR` 轨迹步骤，
    以 `MODEL_OUTPUT_PARSE_ERROR` 终止；
  - 每轮至少一个 `TraceStep`，包含输入摘要、决策/观察/错误和耗时；输入摘要
    首轮取任务文本、后续取最近一条工具观察，截断到领域上限。
- 新增 [`tests/test_agent.py`](../../tests/test_agent.py)：24 个用例，全部
  使用 Fake LLM、三个真实工具与一个确定性失败工具，不访问网络、不调用真实
  API。
- 新增本记录与 [Day 12 代码导读](../architecture/day-12-agent-loop-code-walkthrough.md)。
- 没有修改 `LLM.complete` 接口、Day 4 领域模型、Day 6 DeepSeek 适配器、
  Day 10 提示词、Day 11 解析器或三个已有工具。

## 设计边界与不变量

- **唯一控制者**：`Agent` 拥有唯一的步数计数与终止判断；`AgentState` 只保存
  任务、消息、可用工具名称、`max_steps`/`steps_used`、轨迹和终止信息，不保存
  模型客户端、注册表、密钥或不可序列化运行时资源。
- **预算硬约束**：每轮在调用模型前检查 `steps_used >= max_steps`；耗尽时
  返回 `MAX_STEPS_EXCEEDED` 与已有轨迹，绝不发起第 `max_steps + 1` 次模型
  调用。
- **状态不变量**：每轮结束时 `steps_used == len(trace)` 且 `steps_used <=
  max_steps`；实现上每轮用完整字段重建 `AgentState`，而不是逐个字段原地赋值，
  保证任何时刻状态都满足 Day 4 校验器。
- **终止分支**：`FinalAnswer` -> `FINAL_ANSWER`；`ParseError` ->
  `MODEL_OUTPUT_PARSE_ERROR`；不可恢复工具失败 -> `UNKNOWN_TOOL` 或
  `TOOL_EXECUTION_ERROR`；预算耗尽 -> `MAX_STEPS_EXCEEDED`。
- **错误回写**：未知工具与可恢复失败先作为 `Observation`（含错误码与
  `retryable`）写回消息上下文，预算内继续；只有预算耗尽或不可恢复才终止。
- **轨迹安全**：轨迹不含 API Key；`TraceStep.input_summary` 只保存任务或最近
  观察的截断摘要，不保存完整隐藏推理。
- **确定性**：主循环完全由 Fake LLM 与确定性工具驱动，自动化测试不访问网络、
  不依赖真实 API；`LLM.complete` 抛出的适配器错误按原样向上传播，主循环不
  自行重试。

## 遇到的问题与解决过程

### 问题一：`AgentState` 开了 `validate_assignment`，能不能逐字段推进到终态

`AgentState` 设置了 `validate_assignment=True`，且校验器要求"只有 `FINAL_ANSWER`
才能提供 `final_answer`"。如果先赋 `final_answer`，再赋
`termination_reason=FINAL_ANSWER`，第一步就会因为"还没到 FINAL_ANSWER 却有
final_answer"被拒；反过来先赋终止原因也会因为缺少 final_answer 被拒。

解决：`Agent.run` 每一轮结束时用 `_rebuild_state` 把消息、轨迹、步数和终止
信息一次性传给构造器重建 `AgentState`。构造器会整体校验，因此每个中间状态和
终态都天然满足 Day 4 不变量，也避免了原地赋值触发的过渡态问题。

### 问题二：输入摘要应该记什么

`TraceStep.input_summary` 是可选的，但 react-loop 文档要求每轮记录输入摘要，
而且不能保存不必要的完整隐藏推理。模型每一轮真正"新增"的输入其实是最近一条
工具观察，首轮则是任务本身。解决：`_summarize_input` 在消息里从后往前找第一
条 tool 消息，找到就用它的内容，找不到就用任务文本，再统一截断到 2_000 字符
（与领域模型上限一致），避免构造轨迹时触发校验错误。

### 问题三：工具失败什么时候算终止

注册表对未知工具和绝大多数工具失败都标 `retryable=True`，直接终止会让模型
失去纠正机会。解决：主循环只对 `retryable=False` 的失败立即终止，并按错误码
映射终止原因（`UNKNOWN_TOOL` -> `UNKNOWN_TOOL`，其余 -> `TOOL_EXECUTION_ERROR`）；
可恢复失败先写回观察，下一轮循环开头的预算检查负责兜底"预算耗尽才终止"。

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

- `uv sync`：成功，解析并检查 24 个包，锁文件无变化。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：成功，260 个测试通过、3 个跳过（符号链接用例，与 Day 9/10/11
  相同），其中 Agent 主循环 24 个、解析器 56 个、提示词 15 个、文件读取 47 个、
  检索 23 个、计算器 45 个、工具层 16 个、DeepSeek 16 个、LLM 8 个、领域模型
  12 个、CLI 1 个。
- `uv run ruff check .`：根目录唯一失败仍来自受保护的
  `tmp/day04_success_tool_call_demo.py` 导入排序问题；Day 12 文件单独检查通过。
- `uv run ruff format --check .`：根目录两个失败仍来自受保护的 Day 4 导读和
  Day 6 导读示例格式；Day 12 文件单独检查通过。
- `git diff --check`：成功，无空白错误。

与 Day 6 至 Day 11 一致，全仓库检查的 Ruff 例外均来自开始前已存在且明确受保护
的文件，没有修改、暂存或删除它们。

Day 12 文件还在只包含仓库基线和本 Issue 文件的干净副本中复验：从基线提交
`3779dfa` 创建临时工作树，只复制本 Issue 的 4 个变更文件
（`src/self_react/agent.py`、`tests/test_agent.py`、两份 Day 12 文档），再次
执行完全相同的六条命令，结果如下：

- `uv sync`：成功创建隔离环境，解析并安装 24 个包，构建当前项目。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：260 个测试通过、3 个跳过（符号链接用例，与主工作区相同）。
- `uv run ruff check .`：成功，输出 `All checks passed!`。
- `uv run ruff format --check .`：成功，确认 55 个文件均已格式化。
- `git diff --check`：成功，无空白错误。

临时工作树在验证后已删除。这个结果验证的是仓库基线加 Day 12 Issue 文件，不
包含原工作区受保护的 Day 4/5/6/10 导读、交接文档和 `tmp/`。

## 不在范围内

- 重试、流式、异步、持久化或并行工具调度（Day 14 鲁棒性会继续补齐错误分支）。
- 修改 `LLM.complete` 接口、Day 4 领域模型、Day 6 DeepSeek 适配器、Day 10
  提示词、Day 11 解析器或三个已有工具。
- CLI 的 `agent`/`run` 子命令（Day 15 命令行体验）、人类可读 trace 输出
  （Day 13）、端到端演示（Day 16）。
- 真实模型调用、网络访问或工具参数 Schema 自动生成。

## 明天要验证什么

- Day 13 状态与轨迹：把主循环产生的 `TraceStep` 输出成人可读的 trace，并确认
  每次运行都能被复述。
- 是否需要用 `AgentState` 快照支持"暂停/继续"，以及轨迹输出应该隐藏哪些内容。
