# Day 15：命令行体验（run 子命令与 CLI 参数）

> Issue：[29 feat: 实现命令行体验（run 子命令与 CLI 参数）](https://github.com/fufufu11/Self-ReAct/issues/29)
>
> 本记录只描述 Day 15 的命令行入口：`run` 子命令如何接收任务输入、模型
> 配置、最大步数与是否展示轨迹等参数，并把 Day 12 的 `Agent` 和 Day 13 的
> `render_trace` 串成一条可运行的命令行流水线。持久化、暂停/恢复、流式、
> 异步和并行调度仍不属于本期。

## 今天理解了什么

前十四天把"模型 -> 解析 -> 工具 -> 观察 -> 回答"的循环跑通了，但用户只能
通过测试和 Python 代码调用它。Day 15 给项目补上真正的**入口**：在命令行里
敲一行命令就能让智能体执行一次任务。这个入口是 `self-react run <task>`，
它把 Day 12 的 `Agent`（循环控制器）、Day 7 的工具注册表（三个真实工具）
和 Day 13 的 `render_trace`（人类可读轨迹）接到一起。

第一个关键认识是**CLI 只做三件事**：解析参数、组装依赖、打印结果。主循环
的步数计数、预算检查和终止判断全部仍然由 `Agent` 说了算；CLI 绝不复制
这些逻辑，否则同一套决策规则会散落在两个地方，改一处忘一处。命令行入口
的价值是把"参数怎么来、结果怎么展示"和"循环怎么跑"分开，让核心循环保持
单一控制者。

第二个关键认识是**参数校验的失败路径也要可测试、可复述**。未知子命令、
缺任务、非法最大步数、非法模型名，都应该在进入任何业务逻辑之前被明确拒绝，
并给出非零退出码。这样用户不会在参数写错时看到一堆与问题无关的堆栈；
自动化测试也不需要启动子进程，在同一 Python 进程里调用 `main(argv)` 就能
验证每条失败路径。

第三个关键认识是**默认不要求真实 API Key**。`--help`、参数错误路径和
`--model fake` 的确定性演示都不读取 `DEEPSEEK_API_KEY`；只有用户明确选择
`--model deepseek` 并真正运行时才构造 DeepSeek 适配器，密钥缺失时得到
一行稳定说明而不是堆栈。自动化测试通过 `build_llm` 参数注入返回 Fake LLM
的工厂，因此整条"任务 -> Agent.run -> 渲染轨迹 -> 打印"链路不访问网络、
不依赖真实模型。

## 今天交付了什么

- 更新 [`src/self_react/cli.py`](../../src/self_react/cli.py)：
  - 新增 `run` 子命令：位置参数 `task` 接收任务文本；`--model`（`deepseek`
    或 `fake`，默认 `deepseek`）选择模型适配器；`--max-steps`（正整数，
    默认 5）设置最大决策步数；`--show-trace` / `--no-show-trace` 控制是否
    打印人类可读轨迹（默认不打印）；
  - 新增 `build_llm(model, max_steps, task)` 默认模型工厂：`fake` 返回
    确定性离线演示 Fake LLM（固定走"计算器 -> 检索 -> 最终回答"三步），
    `deepseek` 构造 Day 6 的 `DeepSeekLLM`（密钥缺失时抛稳定配置错误）；
  - `main(argv, *, build_llm=build_llm)` 增加工厂注入点，测试传入返回
    Fake LLM 的工厂即可覆盖端到端路径，不需要网络或密钥；
  - `run` 只做参数解析与结果展示：把任务交给 `Agent.run`，有最终回答就
    打印 `最终回答：…`，没有就打印终止原因；`--show-trace` 时打印
    `render_trace(state)` 的输出；
  - `LLMConfigurationError`（配置缺失）与 `LLMProviderError`（供应商调用
    失败）转成一行稳定说明与非零退出码，不泄漏堆栈；
  - `hello` 命令行为保持不变。
- 扩展 [`tests/test_cli.py`](../../tests/test_cli.py) 至 16 个用例，全部
  只通过 `main(argv, build_llm=...)` 与 `build_llm` 公开入口出题：
  - 参数失败路径：未知子命令、`run` 缺任务、`--max-steps` 为 0/负数/
    浮点/非数字、未知 `--model`，全部返回退出码 2；
  - 端到端：不展示轨迹时只打印最终回答；`--show-trace` 时打印的轨迹行
    内容与 `render_trace` 的结构一致（耗时由 `perf_counter` 实测，不逐字
    比对亚毫秒差异）；`--model` 与 `--max-steps` 原样传给工厂；
  - 错误路径：配置缺失与供应商错误转成一行稳定说明，无 `Traceback`；
  - `build_llm("fake", ...)` 返回满足 `LLM` 协议的确定性适配器；
    `build_llm("deepseek", ...)` 在无密钥时抛 `LLMConfigurationError`。
- 新增本记录与 [Day 15 代码导读](../architecture/day-15-cli-experience-code-walkthrough.md)。
- 没有修改 `LLM.complete` 接口、Day 4 领域模型、Day 6 DeepSeek 适配器、
  Day 10 提示词、Day 11 解析器、Day 12 `Agent` 主循环、Day 13 `render_trace`
  或三个已有业务工具；`hello` 行为与 `pyproject.toml` 均未改动。

## 设计边界与不变量

- **CLI 只做三件事**：解析参数、组装依赖（模型工厂 + 默认注册表）、打印
  结果。步数计数、预算检查与终止判断仍由 `Agent` 唯一控制。
- **参数校验先行**：`task` 缺失、`--max-steps` 非法、`--model` 未知都在
  进入 `build_llm` 与 `Agent.run` 之前被 `argparse` 拒绝，返回退出码 2。
- **默认不要求 API Key**：`--help`、参数错误路径与 `--model fake` 不读取
  `DEEPSEEK_API_KEY`；只有 `--model deepseek` 真正运行时才构造适配器。
- **错误路径稳定**：`LLMConfigurationError` 与 `LLMProviderError` 转成一行
  中文说明与固定退出码（配置 2、供应商 3），不打印 `Traceback`、不泄漏
  密钥或原始异常文本。
- **展示与渲染一致**：`--show-trace` 打印的就是 `render_trace(终态状态)`
  的输出；默认不打印轨迹，只打印最终回答或终止原因。
- **确定性**：自动化测试使用 Fake LLM 与三个真实工具，不访问网络、不依赖
  真实 API；`--model fake` 的演示运行也是确定性的。
- **不越界**：不实现持久化、暂停/恢复、流式、异步或并行调度；不修改任何
  已有模块；`Agent` 仍是唯一循环控制者。

## 遇到的问题与解决过程

### 问题一：自动化测试怎么在不联网、无密钥的情况下覆盖端到端路径

CLI 的生产默认工厂会构造 `DeepSeekLLM`，没有密钥就抛配置错误；如果测试
直接调用 `main(["run", "任务"])`，就会卡在密钥上。解决：`main` 增加一个
`build_llm` 注入参数（默认是生产工厂），测试传入返回 Fake LLM 的工厂。
这样"任务 -> Agent.run -> 打印结果"的整条链路可以在同一 Python 进程里
跑通，Fake LLM 的预置响应保证运行确定性。`--model` 参数仍然原样传给工厂，
测试因此还能断言 CLI 是否正确传递用户配置。

### 问题二：`--show-trace` 的输出要不要与 `render_trace` 逐字比对

一开始的测试想断言 CLI 打印的轨迹与 `render_trace(state)` 完全一致。但
`Agent.run` 每轮的耗时用 `time.perf_counter()` 实测，两次运行会有亚毫秒
差异，逐字比对必然失败。解决：`render_trace` 的逐字确定性已经由 Day 13
用固定状态的测试锁定；Day 15 的测试改为断言轨迹的**结构**——最终回答行、
空行、头部三行、三步内容与顺序都和渲染层一致，同时单独断言"不展示轨迹时
这些行不出现"。

### 问题三：`BooleanOptionalAction` 生成了重复的 `--no-no-show-trace`

最初在 `add_argument` 里同时写了 `--show-trace` 和 `--no-show-trace` 两个
选项名，但 `argparse.BooleanOptionalAction` 会自动为 `--show-trace` 生成
对应的 `--no-show-trace`，结果帮助信息里出现了
`--no-show-trace | --no-no-show-trace` 的重复选项。解决：只声明
`--show-trace`，让 `BooleanOptionalAction` 自动生成反向开关，帮助信息恢复
为干净的 `--show-trace | --no-show-trace`。

## 验收结果

以下命令已在 Windows、CPython 3.13.5 环境中实际执行：

```powershell
uv sync
uv run self-react hello
uv run self-react run "计算 2 + 2" --model fake --show-trace
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

- `uv sync`：成功，锁文件无变化。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run self-react run "计算 2 + 2" --model fake --show-trace`：成功，
  打印 `最终回答：计算完成，并查到了 ReAct 的说明。`，随后打印三步骤轨迹
  （计算器 -> 检索 -> 最终回答）。
- `uv run pytest`：成功，305 个测试通过、3 个跳过（符号链接用例，与 Day 14
  相同），其中相比 Day 14 新增 15 个：CLI 16 个（原 1 个 `hello` 用例保留）。
- `uv run ruff check .`：根目录 4 个失败全部来自受保护的 `tmp/` 目录
  （`tmp/day04_success_tool_call_demo.py` 导入排序、`tmp/push_via_api.py`
  未用导入与超长行、`tmp/rebuild_remote_branch.py` 未用导入），与交接清单
  一致；`src/` 与 `tests/` 单独检查通过（`All checks passed!`）。
- `uv run ruff format --check src tests`：通过，26 个文件均已格式化。
- `git diff --check`：成功，无空白错误。

与 Day 6 至 Day 14 一致，全仓库检查的 Ruff 例外均来自开始前已存在且明确
受保护的文件，没有修改、暂存或删除它们。

Day 15 文件还会在只包含仓库基线和本 Issue 文件的干净副本中复验：从远端
`main` 对应的树创建临时工作树，只复制本 Issue 的变更文件
（`src/self_react/cli.py`、`tests/test_cli.py` 与两份 Day 15 文档），再次
执行完全相同的六条命令，确认 `uv run pytest`、全仓库 Ruff 检查与格式检查
全部通过。

## 明天要验证什么

- Day 16 端到端示例：用真实 DeepSeek 调用跑 2 至 3 个可复现示例（单工具、
  多工具、工具失败后恢复），验证 `self-react run "任务" --model deepseek`
  在真实模型下也能完成任务并展示轨迹。
- 确认 `--model fake` 的演示任务与真实任务在 CLI 参数层完全一致，只有模型
  适配器不同；真实模型运行不需要修改 CLI 代码。
- 为 Day 17 的对照研究记录真实运行中 CLI 层的边界是否仍然成立（CLI 只
  解析与展示，不参与循环控制）。
