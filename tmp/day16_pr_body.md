Closes #32

## 背景

`self-react run "任务" --model deepseek` 在真实 DeepSeek API 下跑多轮工具
任务会失败。真实调用诊断确认两个 API 硬约束：`tool` 消息必须跟在原生
`tool_calls` 之后；思考模式要求 `reasoning_content` 原样回传。修复后真实
多轮任务完整跑通。

## 改动

- `src/self_react/llm.py`：`LLM.complete` / `FakeLLM.complete` 增加可选
  `tools` 参数；Fake LLM 新增 `calls_with_tools` 记录。
- `src/self_react/deepseek.py`：请求携带工具定义；默认禁用思考模式
  （`thinking: {"type": "disabled"}`），保留 `thinking_disabled=False`
  显式配置。
- `src/self_react/agent.py`：每轮传递工具清单；消费原生 `tool_calls`
  （单调用直接执行、多调用只执行第一个并写回失败观察）；分派前拦截
  `final_answer` 并转换为 `FinalAnswer` 决策。
- 新增 `src/self_react/tools/final_answer.py`：`FinalAnswerTool` 特殊工具，
  在 `tools/__init__.py` 与 CLI 注册表登记。
- `src/self_react/prompts.py`：输出规则明确"每轮只能输出一个 tool_call"。
- 测试扩展 8 个用例，覆盖请求字段、原生调用消费、多调用处理、
  `final_answer` 拦截、工具清单透传与提示词约束。

## 验证

- 真实 DeepSeek：`uv run self-react run "计算 2 + 2，并检索 react 主题"
  --model deepseek --show-trace` 完整跑通（calculator -> retrieve ->
  最终回答）；工具失败恢复场景与 `--model fake` 均正常。
- `uv run pytest`：311 通过、3 跳过（符号链接用例）。
- `uv run ruff check src tests` 与 `uv run ruff format --check src tests`：
  通过。
- `git diff --check`：通过。

## 风险与后续

- 每轮仍只支持一个决策：模型并行请求多个工具时只执行第一个，其余写回
  失败观察；并行调度不在本期范围。
- 默认禁用思考模式；未来启用时需补齐 `reasoning_content` 字段往返。
- Day 17 将基于本链路编写端到端示例。
