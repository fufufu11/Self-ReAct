# Day 4：领域模型

## 今天理解了什么

ReAct 中几个容易混淆的词承担不同职责：`Message` 是进入模型上下文的对话条目；`ToolCall` 是模型请求执行工具的动作意图；`ToolResult` 是工具执行边界返回的结构化成功或失败；`Observation` 是把结果写回模型上下文后的可读观察。前两者描述对话和意图，后两者描述执行结果及其反馈，不能互换。

`AgentState` 保存后续循环真正需要的任务、消息、工具名称、步数预算、轨迹和终止信息；`TraceStep` 只记录一次决策尝试的输入摘要、决策、观察、错误和耗时。`FinalAnswer` 与 `ToolCall` 是互斥的决策分支，`TerminationReason` 则说明整个运行最终为什么停止。可恢复的工具失败先作为带错误信息的 `Observation` 返回模型，只有控制器决定停止时才成为最终终止原因。

## 今天交付了什么

- 创建根目录 [`CONTEXT.md`](../../CONTEXT.md)，记录 Self-ReAct 的统一领域语言和概念边界。
- 在 [`src/self_react/models.py`](../../src/self_react/models.py) 使用 Pydantic v2 定义 `Message`、`ToolCall`、`ToolResult`、`Observation`、`AgentState`、`TraceStep`、`FinalAnswer`、错误类型和终止原因枚举。
- 在 [`pyproject.toml`](../../pyproject.toml) 增加运行时依赖 `pydantic>=2.7,<3`。
- 在 [`tests/test_models.py`](../../tests/test_models.py) 覆盖正常构造、缺失字段、错误类型、非法枚举、工具失败、调用关联、轨迹边界和 JSON 序列化往返。
- 保持 Day 3 的 `self-react hello` 实现不变。

## 设计边界与不变量

- `ToolResult` 用 `status` 区分成功和失败：成功必须有 `content` 且没有 `error`；失败必须有 `ToolError` 且不能把错误放进成功内容。
- `Observation.from_tool_result` 只做结果到模型反馈的转换，不执行工具；失败观察保留错误类别、面向模型的消息和 `retryable`。
- `Message` 的 `tool` 角色必须通过 `tool_call_id` 回指调用；助手消息可以携带多个唯一的 `ToolCall`，普通 system/user 消息不能携带工具调用。
- `AgentState` 不保存模型客户端、Python 函数、密钥或其他不可序列化资源；`steps_used` 必须等于 `trace` 长度且不超过 `max_steps`。
- `TraceStep` 允许只记录错误来表达模型输出解析失败，不伪造成功动作；工具观察若存在则必须回指同一 `call_id`。
- 完成状态必须同时有 `FinalAnswer` 和 `TerminationReason.FINAL_ANSWER`；异常终止只记录原因，不伪造最终回答。

本轮没有跨模块的运行时调用：CLI 仍只处理 `hello`，领域模型由后续 Agent、解析器和工具模块消费。因此不单独创建跨模块流程图，避免记录尚未实现的调用链。

## 遇到的问题与解决过程

### 问题

如果只把一组字典替换成 Pydantic 类，仍然可能把工具失败当成普通字符串、把工具消息关联到错误调用，或把不可序列化的运行时对象塞进状态。

### 解决过程

先在 `CONTEXT.md` 固定术语，再用判别字段和模型校验器表达边界：`ToolResultStatus` 保证成功/失败互斥，`ToolError` 和 `TraceError` 分别承载工具错误与轨迹错误，`Decision` 用 `kind` 区分 `ToolCall` 和 `FinalAnswer`，状态校验器保证步数、轨迹和终止信息一致。参数、元数据和错误详情在模型边界检查 JSON 可序列化性。

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

- `uv sync`：成功，解析并检查 13 个包。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：成功，13 个测试全部通过（CLI 1 个、领域模型 12 个）。
- `uv run ruff check .`：成功，输出 `All checks passed!`。
- `uv run ruff format --check .`：成功，确认 19 个文件已格式化。
- `git diff --check`：成功，无空白错误。

## 明天要验证什么

- 设计与测试不依赖具体供应商的最小 `LLM` 抽象和 Fake LLM。
- 确认模型请求、响应转换与领域模型之间的消费边界，不提前把工具执行放进 LLM 接口。
