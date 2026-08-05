# Day 16：DeepSeek 原生工具调用（真实 API 多轮任务）

> Issue：[32 feat: DeepSeek 适配器支持原生工具调用（真实 API 多轮任务）](https://github.com/fufufu11/Self-ReAct/issues/32)
>
> 本记录只描述让真实 DeepSeek 调用跑通多轮工具任务所需的改动：适配器携带
> 工具定义并禁用思考模式、主循环消费原生 `tool_calls`、`final_answer`
> 特殊工具拦截。端到端示例、持久化、暂停/恢复、流式、异步和并行调度仍属于
> 后续日期。

## 今天理解了什么

Day 15 上线了 `self-react run "任务" --model deepseek`，但在真实 API 下跑
多轮工具任务会失败。用真实调用逐层诊断，发现两个 DeepSeek 的 API 硬约束：

1. **`tool` 消息前面必须有原生 `tool_calls`**。DeepSeek 要求
   `role: "tool"` 的消息必须是对一条带 `tool_calls` 的 assistant 消息的
   响应。而本项目 Day 10 的文本 JSON 契约把工具决策放在 `content` 里，
   请求历史中没有原生 `tool_calls`，第二轮就被拒绝（`Messages with role
   'tool' must be a response to a preceding message with 'tool_calls'`）。
2. **思考模式要求 `reasoning_content` 原样回传**。DeepSeek 思考模式会在
   响应里返回 `reasoning_content`，后续请求必须原样带上；文本 JSON 契约
   只保留 `content`，丢弃了它，导致下一轮被拒绝（`The reasoning_content
   in the thinking mode must be passed back to the API`）。

真实调用验证给出的可行方向是：**向请求提供工具定义（`tools`）+ 禁用思考
模式（`thinking: {"type": "disabled"}`）**。这样模型稳定返回原生
`tool_calls`，最终回答仍以 `content` JSON 返回。但工具模式激活后模型又
暴露了两个新行为：可能一次返回多个工具调用（并行），以及把提示词里的
`final_answer` 也当成可调用工具。

第二个关键认识是**真实模型的并行倾向需要框架显式约束**。领域模型
`Decision` 每轮只支持一个 `ToolCall`，Agent 无法把一轮里的多个调用都写成
轨迹步骤。解决：提示词明确"每轮只能输出一个 tool_call"；若模型仍一次返回
多个，Agent 只执行第一个，其余写成可恢复失败观察并写回消息，让 API 历史
中每个 `tool_call_id` 都有对应响应，同时提示模型把其余工具留到后续轮次。

第三个关键认识是**"结束对话"也应该是一个显式工具**。真实模型在原生工具
模式下会把 `final_answer` 当成工具调用。与其让注册表返回"未知工具"或让
解析器硬扛模型输出差异，不如把 `final_answer` 注册为特殊工具：Agent 在
分派前拦截，把调用转换为 `FinalAnswer` 决策并终止，同时写回一条 tool 消息
保持 API 历史完整。这样无论模型在 `content` 里给 JSON 最终回答，还是用
原生 `tool_calls` 调用 `final_answer`，两条路都能稳定结束。

## 今天交付了什么

- [`src/self_react/llm.py`](../../src/self_react/llm.py)：`LLM.complete`
  与 `FakeLLM.complete` 增加可选 `tools` 参数；Fake LLM 增加
  `calls_with_tools` 记录，供测试断言工具清单是否传给了适配器。
- [`src/self_react/deepseek.py`](../../src/self_react/deepseek.py)：
  - `complete` 接收 `tools` 并把工具序列化成供应商 function 定义（名称、
    描述、宽松参数形状），`tools=None` 时不发送；
  - 新增 `thinking_disabled=True` 构造参数，默认禁用思考模式（`extra_body`
    携带 `thinking: {"type": "disabled"}`），避免 `reasoning_content`
    回传问题；测试可显式关闭禁用配置。
- [`src/self_react/agent.py`](../../src/self_react/agent.py)：
  - 每轮把注册表工具清单传给 `LLM.complete`；
  - 消费原生 `tool_calls`：单工具调用直接作为决策，不经过文本 JSON 解析；
    多工具调用只执行第一个，其余写成可恢复失败观察并写回消息；
  - 拦截 `final_answer` 工具调用，转换为 `FinalAnswer` 决策并终止，同时
    写回 tool 消息保持历史完整。
- 新增 [`src/self_react/tools/final_answer.py`](../../src/self_react/tools/final_answer.py)：
  `FinalAnswerTool` 特殊工具，标记对话结束并交付最终回答；在
  [`tools/__init__.py`](../../src/self_react/tools/__init__.py) 与 CLI
  默认注册表（[`cli.py`](../../src/self_react/cli.py)）中登记。
- [`src/self_react/prompts.py`](../../src/self_react/prompts.py)：输出规则
  明确"每轮只能输出一个 tool_call，需要多个工具时依次请求"。
- 测试扩展 8 个用例（311 通过、3 跳过）：DeepSeek 请求携带工具定义与思考
  模式配置、思考模式可显式开启、Agent 消费原生 `tool_calls`、多工具调用
  处理、`final_answer` 拦截、工具清单透传、提示词单工具约束、Fake LLM
  工具记录。
- 新增本记录与 [Day 16 代码导读](../architecture/day-16-deepseek-native-tool-calls-code-walkthrough.md)。

## 设计边界与不变量

- **原生与文本双轨**：供应商原生 `tool_calls` 直接作为决策；`content`
  里的 JSON 仍走 Day 10/11 的 `parse_decision`。两条路径共用同一个终止
  分支与轨迹模型。
- **每轮一个决策**：领域模型 `Decision` 语义不变；多工具调用只执行第一个，
  其余作为可恢复失败观察写回，提示词同步约束模型单工具输出。
- **历史完整**：任何 `tool_call_id` 都有对应 tool 消息，满足 DeepSeek 的
  API 约束；`final_answer` 被拦截时也会写回 tool 消息再终止。
- **默认禁用思考模式**：避免 `reasoning_content` 往返问题；保留
  `thinking_disabled=False` 配置供未来启用思考模式时补齐字段往返。
- **确定性**：自动化测试使用 Fake LLM 与注入客户端，不访问网络、不依赖
  真实 API；真实调用只作为手动验收。
- **不越界**：不实现并行工具调度（多调用被折叠为失败观察）、持久化、
  暂停/恢复、流式或异步。

## 遇到的问题与解决过程

### 问题一：`tool` 消息被 DeepSeek 拒绝

第二轮请求报 `Messages with role 'tool' must be a response to a preceding
message with 'tool_calls'`。逐层诊断（SDK 裸调用 -> 适配器 -> Agent）确认：
文本 JSON 契约下 assistant 消息没有原生 `tool_calls`。解决：请求携带工具
定义并让模型使用原生工具调用，Agent 直接消费 `response.tool_calls`。

### 问题二：思考模式要求 `reasoning_content` 回传

转换成本地 `tool_calls` 后仍报 `The reasoning_content in the thinking
mode must be passed back to the API`。诊断发现首轮响应带 `reasoning_content`
字段，后续请求必须原样带上，而我们的契约只保留 `content`。解决：默认禁用
思考模式（`thinking: {"type": "disabled"}`），响应不再产生
`reasoning_content`，多轮工具调用即可通过。

### 问题三：模型一次返回多个工具调用

模型在工具模式下倾向并行调用（一次返回 calculator + retrieve）。领域模型
每轮只支持一个决策。解决：提示词明确单工具约束；Agent 防御处理——只执行
第一个，其余调用写成可恢复失败观察并写回消息，既满足 API 历史完整，又提示
模型后续轮次继续。

### 问题四：模型把 `final_answer` 当成工具

工具模式激活后，模型可能把提示词里的 `final_answer` 理解为可调用工具，导致
"未知工具"并最终解析失败。解决：把 `final_answer` 注册为特殊工具，Agent
在分派前拦截并转换为 `FinalAnswer` 决策，同时写回 tool 消息；模型无论用
content JSON 还是原生工具调用交付最终回答都能稳定结束。

## 验收结果

以下命令已在 Windows、CPython 3.13.5 环境中实际执行：

```powershell
uv sync
uv run self-react hello
uv run self-react run "计算 2 + 2，并检索 react 主题" --model deepseek --show-trace
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

- `uv sync`：成功，锁文件无变化。
- `uv run self-react hello`：成功。
- `uv run self-react run "计算 2 + 2，并检索 react 主题" --model deepseek
  --show-trace`：成功（真实 DeepSeek API），三步轨迹：calculator 返回 4 ->
  retrieve 返回 ReAct 说明 -> 最终回答；另验证了工具失败恢复场景
  （检索不存在的主题后继续检索 react）与 `--model fake` 离线演示。
- `uv run pytest`：成功，311 个测试通过、3 个跳过（符号链接用例），相比
  Day 15 新增 8 个（DeepSeek 适配器 3 个、Agent 3 个、提示词 1 个、Fake
  LLM 工具记录 1 个）。
- `uv run ruff check .`：根目录 4 个失败仍全部来自受保护的 `tmp/` 目录
  （与 Day 15 记录一致）；`src/` 与 `tests/` 单独检查通过。
- `uv run ruff format --check .`：根目录未格式化文件全部来自受保护的
  Day 4/6 导读与 `tmp/` 临时脚本（含本次诊断脚本）；`src/` 与 `tests/`
  单独检查通过（27 个文件均已格式化）。
- `git diff --check`：成功，无空白错误。

Day 16 文件还会在只包含仓库基线和本 Issue 文件的干净副本中复验，确认
`uv run pytest`、全仓库 Ruff 检查与格式检查全部通过（保护文件例外除外）。

## 明天要验证什么

- Day 17 端到端示例：用真实 DeepSeek 编写 2 至 3 个可复现示例（单工具、
  多工具、工具失败后恢复），验证 `self-react run "任务" --model deepseek`
  的稳定行为。
- 观察多轮真实调用中 `final_answer` 的两种交付形态（content JSON 与原生
  工具调用）是否都被稳定处理。
- 若需要启用思考模式，补齐 `reasoning_content` 的字段往返并新增对应测试。
