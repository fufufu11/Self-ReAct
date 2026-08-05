# Day 16：端到端示例（单工具、多工具、工具失败后恢复）

> Issue：[35 feat: 编写端到端示例（单工具、多工具、工具失败后恢复）](https://github.com/fufufu11/Self-ReAct/issues/35)
>
> 本记录只描述 Day 16 的端到端示例：三个确定性、可复现的离线演示——单工具、
> 多工具、工具失败后恢复——通过 CLI 的 `example` 子命令一条命令跑完，并展示
> 完整人类可读轨迹。持久化、暂停/恢复、流式、异步和并行调度仍不属于本期。

## 今天理解了什么

前十五天把框架的每个零件都造好了：`Agent` 会跑主循环，注册表里有三个真实
工具，`render_trace` 能把轨迹翻译成中文，CLI 的 `run` 子命令能接住任务。
但"零件齐了"和"别人能复现整条流水线"是两回事。Day 16 的价值是把演示**固化成
数据**：每个示例的任务文本、工具序列和最终回答全部写死在代码里，用户敲一条
命令就能离线看到"任务 -> 工具 -> 观察 -> 回答"的完整过程。

第一个关键认识是**示例是"数据 + 组合"，不是新逻辑**。新增的
`src/self_react/examples.py` 只做两件事：定义三个场景（任务 + Fake LLM
预置响应），把 Day 5 的 `FakeLLM`、Day 7 的 `ToolRegistry`、Day 12 的
`Agent` 和 Day 13 的 `render_trace` 接起来。主循环的步数计数、预算检查和
终止判断仍然全部由 `Agent` 说了算，示例没有复制一行循环逻辑。

第二个关键认识是**可复现来自"固定输入 + 固定行为"**。Fake LLM 按顺序返回
预置响应，计算器与检索工具对相同输入永远返回相同输出，所以相同命令永远得到
相同决策与观察。唯一无法逐字复现的是耗时（`perf_counter` 实测），因此文档
和测试都把耗时排除在"完全一致"的断言之外。

第三个关键认识是**CLI 是示例的入口，不是示例本身**。`example` 子命令只是
把示例表、`run_example` 和 `render_trace` 接到一起，负责打印标题、最终回答
与轨迹；它不复制主循环逻辑，也不重新定义场景。这样"示例内容"和"示例怎么
展示"分离：改场景只动 `examples.py`，改展示只动 `cli.py`。

## 今天交付了什么

- 新增 [`src/self_react/examples.py`](../../src/self_react/examples.py)：
  - `ExampleScenario`：冻结数据类，保存示例名称、标题、任务与 Fake LLM
    预置响应；
  - `EXAMPLES`：三个固定场景——`single-tool`（只调计算器）、`multi-tool`
    （计算器 + 检索）、`failure-recovery`（检索未知主题失败后换正确主题
    继续）；
  - `build_example_llm(name)`：按名称构造确定性 Fake LLM；
  - `build_example_registry()`：构造与 CLI `run` 一致的四个工具注册表；
  - `run_example(name) -> AgentState`：组合 `Agent`、注册表与预置响应，
    固定 `max_steps` 等于响应数量，返回终态状态。
- 更新 [`src/self_react/cli.py`](../../src/self_react/cli.py)：
  - 新增 `example` 子命令：`self-react example single-tool|multi-tool|
    failure-recovery`，离线确定性运行，不读取 `DEEPSEEK_API_KEY`，始终
    打印示例标题、最终回答与 `render_trace` 的完整人类可读轨迹；
  - 新增 `_example_command`，只负责组装与打印，不复制主循环逻辑。
- 新增 [`tests/test_examples.py`](../../tests/test_examples.py)：11 个用例，
  全部使用 Fake LLM 与确定性工具，不访问网络、不依赖真实 API：
  - 三个场景的定义固定（名称、标题、任务）；
  - `build_example_llm` 返回满足 `LLM` 协议的适配器；
  - 单工具示例：calculator 观察 `4` 后最终回答；
  - 多工具示例：calculator -> retrieve 两条成功观察，注册表包含四个工具；
  - 失败恢复示例：首次 `TOOL_EXECUTION_ERROR` 且可重试，换主题后成功；
  - 确定性：相同示例两次运行，决策、观察、错误完全一致；
  - CLI：三个示例输出结构（标题行、最终回答行、空行、轨迹关键词）；
    未知示例名返回退出码 2；清空密钥后仍可离线运行。
- 新增本记录与 [Day 16 代码导读](../architecture/day-16-end-to-end-examples-code-walkthrough.md)。
- 没有修改 `Agent` 主循环、`LLM.complete` 接口、DeepSeek 适配器、提示词、
  解析器、领域模型或三个业务工具；`run --model fake` 的 Day 15 演示行为
  保持不变。

## 设计边界与不变量

- **只组合不复制**：示例只组合 `FakeLLM`、`ToolRegistry`、`Agent`、
  `render_trace` 与 CLI；不复制主循环逻辑，`Agent` 仍是唯一控制者。
- **场景即数据**：任务、工具序列与最终回答全部收在 `EXAMPLES` 中，测试和
  CLI 都从同一数据源读取，不会出现文档与代码各说一套。
- **确定性**：Fake LLM 预置响应 + 确定性工具；相同命令得到相同决策与观察，
  耗时除外。
- **必然最终回答**：`max_steps` 固定等于预置响应数量，最后一条响应是
  `final_answer`，因此示例总是以 `FINAL_ANSWER` 结束，不会出现步数耗尽。
- **离线**：`example` 子命令不访问网络、不读取 `DEEPSEEK_API_KEY`。
- **不越界**：不实现持久化、暂停/恢复、流式、异步或并行调度；不修改任何
  已有核心模块；`hello` 与 `run` 行为不变。

## 遇到的问题与解决过程

### 问题一：示例要不要复用 Day 15 的 `_demo_fake_llm`

`_demo_fake_llm` 是 Day 15 为 `run --model fake` 准备的固定三步演示
（计算器 -> 检索 -> 最终回答），它不关心任务文本。Day 16 的示例要求
"任务与响应一一对应"：单工具示例的任务只能触发计算器，多工具示例才触发
检索。直接复用会让"计算 2 + 2"的示例顺带检索 react，语义模糊，还会改变
Day 15 已记录的演示输出。

解决：`examples.py` 自持三份场景数据，`_demo_fake_llm` 保持原样。两个演示
各自独立：`run --model fake` 是 Day 15 的通用演示，`example` 是 Day 16
的三种固定场景。

### 问题二：`max_steps` 应该设多少

如果 `max_steps` 大于响应数量，示例仍然会在最后一条 `final_answer` 处正常
结束，但步数预算看起来"有多余"；如果小于响应数量，示例会在中途步数耗尽，
破坏可复现的最终回答。两者都不理想。

解决：`run_example` 固定 `max_steps = len(scenario.responses)`。预置响应
的最后一条总是最终回答，因此示例恰好消耗完全部预算并以 `FINAL_ANSWER`
结束，轨迹里"步数：3 / 3"这样的信息也一目了然。

### 问题三：CLI 测试怎么验证"确定性"

轨迹里包含 `perf_counter` 实测的耗时，两次运行会有亚毫秒差异，逐字比对
必然失败（Day 15 已经遇到过同样的问题）。

解决：分层验证。示例库测试直接比较两次运行的 `TraceStep` 决策、观察与错误
（这些是确定性的）；CLI 测试只断言输出结构——标题行、最终回答行、空行、
终止原因、步数与轨迹关键词，耗时不作为断言内容。

## 验收结果

以下命令已在 Windows、CPython 3.13.5 环境中实际执行：

```powershell
uv sync
uv run self-react hello
uv run self-react example single-tool
uv run self-react example multi-tool
uv run self-react example failure-recovery
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

- `uv sync`：成功，锁文件无变化。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run self-react example single-tool`：成功，两步骤轨迹
  （calculator -> 最终回答），最终回答 `2 + 2 = 4。`。
- `uv run self-react example multi-tool`：成功，三步骤轨迹
  （calculator -> retrieve -> 最终回答），最终回答
  `计算结果是 4；ReAct 是一种让模型推理与行动交错的智能体范式。`。
- `uv run self-react example failure-recovery`：成功，三步骤轨迹
  （retrieve unknown-topic 失败 -> retrieve react 成功 -> 最终回答），
  首次观察带 `TOOL_EXECUTION_ERROR` 与"可重试：是"，最终回答
  `第一次检索失败后改用 react，成功找到 ReAct 的说明。`。
- `uv run pytest`：成功（完整结果见下文复验）。
- `uv run ruff check .` 与 `uv run ruff format --check .`：`src/` 与
  `tests/` 单独检查通过（完整结果见下文复验）。
- `git diff --check`：成功，无空白错误。

Day 16 文件还会在只包含仓库基线和本 Issue 文件的干净副本中复验，确认
`uv run pytest`、全仓库 Ruff 检查与格式检查全部通过（保护文件例外除外）。

## 明天要验证什么

- Day 17 对照 LangChain/LangGraph：带着"示例只组合、不复制主循环"的边界，
  观察成熟框架如何组织端到端演示与编排层。
- 真实 DeepSeek 手动验收：显式设置 `DEEPSEEK_API_KEY` 后，用
  `self-react run "任务" --model deepseek --show-trace` 跑三个示例任务，
  验证单工具、多工具与失败恢复在真实模型下也成立（不作为自动化测试前置
  条件）。
