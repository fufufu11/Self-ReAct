# Day 13：状态与轨迹（人类可读输出）

> Issue：[25 feat: 实现人类可读的状态与轨迹输出](https://github.com/fufufu11/Self-ReAct/issues/25)
>
> 本记录只描述 Day 13 的展示层：把 Day 12 主循环产生的 `AgentState.trace`
> 渲染成稳定、人类可读的中文文本。持久化、暂停/恢复、流式、异步和并行调度
> 仍属于后续日期。

## 今天理解了什么

Day 12 的 `Agent.run` 把每一轮的输入摘要、决策、观察、错误和耗时都记进了
`AgentState.trace`，但那些还是 Pydantic 对象，只有程序能顺畅地读。Day 13
给这条流水线补上**展示层**：一个纯函数 `render_trace(state)`，把终态状态
变成一份能直接贴给同事看的中文"运行记录单"。

展示层最重要的设计是**只读不判**。它只消费 `AgentState` 里已经存在的字段，
不修改状态、不做任何决策、不调用模型和工具；同样一份状态，无论渲染多少次、
在什么顺序下构造参数，输出都必须完全一样。这份确定性来自三处固定约定：
枚举值到中文标签的固定映射、参数 JSON 按键排序、耗时固定三位小数并去掉
尾零。

第二个关键认识是**人类可读不等于全量输出**。渲染只展示五个核心字段（输入
摘要、决策、观察、错误、耗时），并刻意隐藏 `TraceError.details`、
`Observation.metadata` 这类调试细节；解析失败只展示稳定错误说明。这既让文本
干净，也顺带守住安全边界：领域对象里本来就不该有 API Key，渲染层更不会
把调试堆栈或模型原始输出打印出来。

## 今天交付了什么

- 新增 [`src/self_react/trace.py`](../../src/self_react/trace.py)：
  - `render_trace(state: AgentState) -> str`：把终态状态渲染成稳定中文文本；
  - 输出由头部（任务、终止原因、步数预算）和按顺序排列的步骤组成，每个步骤
    依次是输入摘要、决策、观察、错误、耗时；
  - 决策分为最终回答（`决策：最终回答` + 回答内容）与工具调用（工具名、调用
    编号、键排序 JSON 参数）两种形态；
  - 观察标注成功/失败，失败观察附带错误码中文标签与可重试标记；解析失败只
    渲染稳定错误说明；
  - 空轨迹只输出头部，不伪造步骤；`input_summary`/`duration_ms` 为 `None`
    时使用稳定占位符；
  - 不渲染 `TraceError.details`、`Observation.metadata`，不访问网络、不读
    环境变量、不修改状态。
- 新增 [`tests/test_trace.py`](../../tests/test_trace.py)：18 个用例，覆盖
  确定性、字段一一对应、四类轨迹、端到端分支（最终回答、单/多轮工具调用、
  解析失败、步数耗尽、未知工具、可恢复失败后恢复、不可恢复失败终止）与安全
  边界，全部使用 Fake LLM 与三个真实工具，不访问网络。
- 新增本记录与 [Day 13 代码导读](../architecture/day-13-trace-rendering-code-walkthrough.md)。
- 没有修改 `LLM.complete` 接口、Day 4 领域模型、Day 6 DeepSeek 适配器、
  Day 10 提示词、Day 11 解析器、Day 12 `Agent` 主循环或三个已有工具。

## 设计边界与不变量

- **纯展示层**：`render_trace` 只接受 `AgentState`，不修改状态、不调用模型
  与工具、不访问网络、不读取环境变量；其他模块不需要感知渲染器的存在。
- **确定性**：相同状态两次渲染结果完全一致；参数 JSON 使用
  `sort_keys=True` 排序，与字典插入顺序无关；耗时固定三位小数并去掉尾零。
- **字段一一对应**：每个 `TraceStep` 的输入摘要、决策、观察、错误、耗时都
  在文本中按顺序出现，与领域模型字段顺序一致。
- **四类轨迹覆盖**：最终回答、工具调用（成功/失败/未知工具）、解析失败和
  步数耗尽都有稳定的渲染形态；空轨迹只输出头部。
- **安全边界**：不渲染 `TraceError.details` 与 `Observation.metadata`；
  解析失败只展示稳定错误说明，不携带模型原始输出、调试堆栈或密钥。
- **不越界**：不修改任何已有模块，不实现持久化、暂停/恢复、流式、异步或
  并行调度，不接入 CLI（Day 15 再接命令参数）。

## 遇到的问题与解决过程

### 问题一：耗时的浮点怎么稳定又好看地展示

`duration_ms` 是 `perf_counter` 测出来的浮点数，直接 `str()` 会得到一长串
尾数，同样的运行每次打印都不同；保留全部小数又不符合"人类可读"。

解决：固定三位小数（`f"{duration_ms:.3f}"`），再统一去掉尾零和多余小数点。
`12.5` 显示为 `12.5 毫秒`，`10.0` 显示为 `10 毫秒`，`0.123456` 显示为
`0.123 毫秒`；`None` 显示为 `（未记录）`。格式化规则固定后，相同状态永远
得到相同文本。

### 问题二：参数字典插入顺序会让输出不稳定

同一个 `ToolCall.arguments` 只是键的插入顺序不同，就可能打印成
`{"b": 1, "a": "中文"}` 和 `{"a": "中文", "b": 1}` 两种文本，破坏确定性。

解决：`_format_json` 使用 `json.dumps(..., sort_keys=True, ensure_ascii=False)`，
参数始终按键名排序输出；测试用两个插入顺序相反的字典断言渲染结果一致。

### 问题三：展示到什么程度才算"人类可读"又不泄漏

如果无条件打印 `Observation.metadata` 和 `TraceError.details`，文本会混入
调试信息，也违背"解析失败只展示稳定错误说明"的要求。

解决：渲染只覆盖领域模型枚举的核心字段（输入摘要、决策、观察、错误、耗时），
`details` 与 `metadata` 一律不展示；枚举值同时给出英文代码与中文标签，例如
`终止原因：最终回答（FINAL_ANSWER）`。测试显式构造带调试内容的
`details`/`metadata`，断言它们不会出现在渲染文本里。

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
- `uv run pytest`：成功，278 个测试通过、3 个跳过（符号链接用例，与 Day 12
  相同），其中轨迹渲染 18 个、Agent 主循环 24 个、解析器 56 个、提示词 15 个、
  文件读取 47 个、检索 23 个、计算器 45 个、工具层 16 个、DeepSeek 16 个、
  LLM 8 个、领域模型 12 个、CLI 1 个。
- `uv run ruff check .`：根目录唯一失败仍来自受保护的
  `tmp/day04_success_tool_call_demo.py` 导入排序问题；Day 13 文件单独检查
  通过（`All checks passed!`）。
- `uv run ruff format --check .`：根目录两个失败仍来自受保护的 Day 4 导读和
  Day 6 导读示例格式；Day 13 文件单独检查通过（已格式化）。
- `git diff --check`：成功，无空白错误。

与 Day 6 至 Day 12 一致，全仓库检查的 Ruff 例外均来自开始前已存在且明确受
保护的文件，没有修改、暂存或删除它们。

Day 13 文件还在只包含仓库基线和本 Issue 文件的干净副本中复验：从基线提交
`843ee00` 创建临时工作树，只复制本 Issue 的 4 个变更文件
（`src/self_react/trace.py`、`tests/test_trace.py`、两份 Day 13 文档），再次
执行完全相同的六条命令。临时工作树在验证后已删除。

## 明天要验证什么

- Day 14 鲁棒性：模型超时、工具异常、重复动作等错误分支的回归测试，确认
  渲染层不需要为它们改变接口。
- Day 15 命令行体验：把 `render_trace` 接入 CLI，让 `--show-trace` 之类参数
  能直接打印本次运行的人类可读轨迹。
