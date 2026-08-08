# Day 22：解析失败有界重试（R-02）

> Issue：[47 feat: 解析失败有界重试（R-02）](https://github.com/fufufu11/Self-ReAct/issues/47)
>
> 这是 v0.2+ 迭代规划（`docs/project-roadmap.md`）Phase 1 的第二项工作：把
> `docs/architecture/react-loop.md` 预留的纠错策略落地——解析失败时把稳定
> 错误消息回写给模型、消耗一步预算、至多重试一次，杜绝无限子循环。改动
> 集中在 `Agent` 一个分支，不触碰 `LLM.complete` 接口、领域模型、解析器、
> 提示词和工具层。

## 今天理解了什么

### 第一个认识：纠错也要"有界"，错误回写本身就是一种观察

Day 12 的 MVP 对解析失败采用"直接终止"：记录 `MODEL_OUTPUT_PARSE_ERROR`
轨迹，不猜、不补、不改写模型输出。这只解决了一半问题——它安全，但没有给
模型第二次机会。Day 14 的工具失败已经证明"先作为可恢复错误回写、预算内
继续"是更好的模式；解析失败可以借用同样的思路，但要更保守：回写稳定错误
消息、消耗一步预算、**至多重试一次**。这样既给了模型纠错机会，又不会形成
解析重试的无限子循环。

### 第二个认识：稳定错误消息与轨迹错误是两个边界

轨迹里的 `TraceError` 是给调用方看的机器可读记录；回写给模型的反馈消息是
给模型看的下一条输入。两者都只复用 `ParseError` 的稳定中文说明（如
"模型输出不是合法 JSON""content 必须是字符串"），不携带模型原始输出、异常
对象或堆栈。`TraceError.retryable` 的语义与工具失败的 `retryable` 对齐：
它表示"这次失败之后控制器还会不会继续重试"——第一次解析失败且预算内为
`True`（下一轮就是重试），重试仍失败或预算耗尽为 `False`。

### 第三个认识：重试轮不需要新消息角色

反馈消息用已有的 `user` 角色，追加在失败的 `assistant` 消息之后，下一次
模型调用就能看到"你上一条输出无法解析：<稳定说明>，请重新输出"。这没有
改动 `Message` 领域模型，也保持了 API 消息历史的合法性。

## 今天交付了什么

- [`src/self_react/agent.py`](../../src/self_react/agent.py)：`Agent.run` 的
  `MODEL_OUTPUT_PARSE_ERROR` 分支改为有界重试——
  - 新增 `_parse_error_feedback(exc)`：把 `ParseError` 的稳定说明包装成
    user 角色反馈消息，引导模型按 Day 10 格式契约重新输出；
  - 解析失败时先记录 `TraceStep(error=TraceError(MODEL_OUTPUT_PARSE_ERROR,
    retryable=...))` 并消耗一步；若 `not parse_retried and
    step_number < max_steps` 则回写反馈并继续下一轮（重试），否则以
    `MODEL_OUTPUT_PARSE_ERROR` 终止；
  - `parse_retried` 局部标志保证一次运行内至多重试一次；
- [`tests/test_agent.py`](../../tests/test_agent.py)：新增 5 个用例——
  "重试一次成功后走工具分支"、"重试仍失败终止"、"预算恰好耗尽不重试"、
  "反馈消息稳定不泄漏原始输出"、"一次运行至多重试一次"；同步更新既有
  解析失败用例与状态不变量参数化；
- [`tests/test_trace.py`](../../tests/test_trace.py)：更新端到端解析失败
  渲染用例，适配有界重试语义；
- 文档同步：[ReAct 核心循环](../architecture/react-loop.md) 的状态图与
  "模型输出无法解析"章节、[Day 12 代码导读](../architecture/day-12-agent-loop-code-walkthrough.md)
  的解析失败相关章节、[Day 22 代码导读](../architecture/day-22-parse-error-bounded-retry-code-walkthrough.md)
  与本记录。

## 设计边界与不变量

- **至多一次**：一次运行内解析失败至多重试一次；重试仍失败直接终止，不回写
  第二次反馈；
- **消耗步数**：失败那轮与重试轮各消耗一步，`max_steps` 仍是硬预算；预算
  恰好耗尽时不再发起重试；
- **终止原因不变**：重试仍失败或预算不足以重试时，终止原因仍是
  `MODEL_OUTPUT_PARSE_ERROR`，不把解析失败伪装成 `MAX_STEPS_EXCEEDED`；
- **状态不变量**：每轮结束 `steps_used == len(trace)` 且
  `steps_used <= max_steps`；`FakeLLM` 调用次数不超过 `max_steps`；
- **错误安全**：轨迹错误与反馈消息都只含稳定中文说明，不泄漏原始输出、
  异常对象、堆栈或 API Key；
- **回归基准**：Day 16 三条 `example` 输出不变；`LLM.complete` 接口、
  解析器、提示词、领域模型与工具层零改动。

## 遇到的问题与解决过程

### 问题一：终止分支把同一个 TraceStep 追加了两次

第一版在 `if not retryable:` 分支里写 `trace=[*state.trace, step]`，但
`state` 刚被上一句重建并已经包含 `step`，导致 `steps_used=2` 而轨迹有 3
步，直接触发 Day 4 校验器"steps_used 必须等于 trace 的步骤数量"。
解决：先一次性构造 `trace = [*state.trace, step]`，再根据 `retryable`
决定是否带 `termination_reason` 重建状态，避免同一对象被追加两次。

### 问题二：既有解析失败用例全部建立在"直接终止"语义上

旧用例只预置一条非法响应，新语义下第一次失败后会自动重试，`FakeLLM`
随即耗尽并抛出 `LLMResponseExhaustedError`。解决：按新语义重写——"重试
仍失败"用两条非法响应、"预算恰好耗尽"用 `max_steps=1`，并新增"重试一次
成功"与"至多一次"用例，让三类路径都成为显式、确定的测试。

## 验收结果

以下命令在 Windows、本仓库环境中实际执行：

```powershell
uv sync
uv run self-react hello
uv run self-react example single-tool
uv run self-react example multi-tool
uv run self-react example failure-recovery
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

- `uv sync`：成功，解析并检查 24 个包，锁文件无变化（无新依赖）；
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`；
- 三条 `example`：退出码 0，最终回答与轨迹结构与 Day 16 记录一致
  （2/2、3/3、3/3 步）；
- `uv run pytest`：407 通过 / 3 跳过（基线 403 通过 / 3 跳过，净新增 4 个
  用例）；
- `uv run ruff check src tests` 与 `ruff format --check src tests`：通过；
- `git diff --check`：无空白错误；
- 全仓库 ruff 例外与交接记录一致：`ruff check .` 的 5 个失败全部来自受保护
  `tmp/`；`ruff format --check .` 的 11 个未格式化文件全部来自受保护
  Day 4/6 导读与 `tmp/`，未修改、未暂存。

真实 DeepSeek 手动验收（不作自动化测试前置条件）：

```powershell
uv run self-react run "计算 2 + 2" --model deepseek --show-trace
```

- 成功，退出码 0；真实 DeepSeek API，轨迹为
  calculator -> 观察 4 -> 最终回答，2 / 5 步，最终回答 `2 + 2 = 4`；
- 与本任务改动前的手动验收结果一致，确认有界重试没有改变正常路径行为。

干净副本复验：从远端 `main` 创建临时工作树，只复制本 Issue 的 6 个变更
文件（`agent.py`、`test_agent.py`、`test_trace.py`、两份架构文档与本记录），
再次执行同样的验收命令，结果见 PR 描述；临时工作树验证后已删除。

## 不在范围内

- R-03 工具 Schema 自动生成、R-07 日志/故障排查场景（按 roadmap 顺序在
  后续对话推进）；
- 修改 `LLM.complete` 接口、领域模型、解析器、提示词、工具层或 CLI 参数；
- 无限重试、流式、异步、持久化或并行工具调度；
- 修改受保护的 `tmp/`、历史导读、交接文档与 `docs/project-roadmap.md`。

## 明天要验证什么

- 继续按 roadmap 顺序推进 R-03（工具 Schema 自动生成 + 注册表 Schema 校验），
  保持"一个 Issue 一个 PR"的节奏；
- 若真实 DeepSeek 在长任务中偶发解析失败，可用 `--show-trace` 观察有界
  重试是否按预期消耗一步并恢复；
- Day 16 三条示例继续作为回归基准，任何后续改动都不改变它们的输出。
