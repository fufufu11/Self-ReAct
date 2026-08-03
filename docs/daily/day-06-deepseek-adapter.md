# Day 6：DeepSeek LLM 适配器

> Issue：[11 feat: 接入 DeepSeek LLM 适配器](https://github.com/fufufu11/Self-ReAct/issues/11)
>
> 本记录只描述 Day 6 的供应商适配边界，不扩展 Agent 主循环、工具注册或异步能力。

## 今天理解了什么

DeepSeek 提供与 OpenAI Chat Completions 兼容的接口。适配器不应把 OpenAI SDK 的请求对象或响应对象扩散到其他模块，而是把 Day 4 的 Message 序列转换为供应商消息，再把一次响应转换回 assistant Message。这样 Day 5 的同步 complete(Sequence[Message]) -> Message 接缝保持不变，Fake LLM、未来 Agent 和真实适配器共享同一接口。

DeepSeek 的工具调用是消息上下文中的数据，不是适配器要执行的动作。assistant 响应中的 tool_calls[].id 成为 ToolCall.call_id；下一次请求中的 tool 消息通过 tool_call_id 回指这个编号。适配器只完成转换，工具执行与下一轮控制属于后续 Agent 模块。

## 今天交付了什么

- 在 src/self_react/deepseek.py 实现同步 DeepSeekLLM，默认使用 https://api.deepseek.com 和 deepseek-v4-flash。
- 复用 src/self_react/llm.py 的 LLM 接缝，补充 LLMConfigurationError、LLMProviderError 和稳定的 LLMProviderErrorCode。
- 将 OpenAI Python SDK 作为唯一新增运行时依赖写入 pyproject.toml 与 uv.lock。
- 在 tests/test_deepseek.py 使用注入的最小客户端覆盖消息转换、普通回答、工具调用、非法响应、配置缺失和供应商错误映射。
- 提供 examples/deepseek_chat.py 作为显式手动真实调用入口。它只输出成功/失败类别，不输出密钥、请求头或完整响应。

## 官方依据

以下资料在 2026-08-03 访问，均为 DeepSeek 或 OpenAI 官方一手来源：

1. [DeepSeek Quick Start：Your First API Call](https://api-docs.deepseek.com/quick_start) 说明 API 兼容 OpenAI 格式，OpenAI base_url 为 https://api.deepseek.com，并给出 DEEPSEEK_API_KEY、OpenAI 客户端和 chat.completions.create 的 Python 示例。
2. [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing) 列出当前 deepseek-v4-flash 与 deepseek-v4-pro；本 Issue 采用前者作为默认模型，避免把模型名交给领域消息或状态对象。
3. [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls) 展示 assistant 的 tool_calls 如何被追加回消息历史，以及 tool 消息如何使用 tool_call_id 关联调用；文档也明确说明模型不会替用户执行函数。
4. [DeepSeek Error Codes](https://api-docs.deepseek.com/quick_start/error_codes) 说明 400、401、402、422、429、500、503 等错误的含义。适配器将供应商状态映射到项目自己的稳定类别，不把原始错误文本传播给调用方。
5. [OpenAI Python SDK README](https://github.com/openai/openai-python/blob/main/README.md) 说明同步 OpenAI 客户端、chat.completions.create 和环境变量配置方式。
6. [OpenAI SDK exception source](https://github.com/openai/openai-python/blob/main/src/openai/_exceptions.py) 说明 AuthenticationError、APITimeoutError、APIConnectionError、RateLimitError、BadRequestError 与 APIStatusError 的异常层次和状态字段。

## 配置边界

DeepSeekLLM 的构造参数属于适配器配置，不进入 Message 或 AgentState：

| 配置 | 默认值 | 进入位置 |
| --- | --- | --- |
| API Key | 运行时 DEEPSEEK_API_KEY | 构造 OpenAI 客户端时读取 |
| base_url | https://api.deepseek.com | 适配器配置 |
| model | deepseek-v4-flash | 适配器配置 |
| timeout | 30.0 秒 | 适配器配置 |
| max_retries | 0 | 固定传给 SDK，重试不属于本 Issue |

注入测试客户端时可以不提供 API Key。未注入客户端且环境变量缺失会抛出 LLMConfigurationError；默认 CLI、pytest 和 Fake LLM 都不会读取该变量，也不会访问网络。

## 请求转换

serialize_messages 先拒绝空序列、字符串和非 Message 元素，再为每条消息创建独立字典：

- system、user、assistant、tool 的 role 使用领域枚举值；
- assistant 的每个 ToolCall 转为 type=function，调用编号进入 id，参数用 JSON 字符串进入 function.arguments；
- tool 消息保留 content，并把 tool_call_id 原样传给供应商；
- 适配器不添加工具定义，不执行动作，也不修改调用方的 Pydantic 对象。

一次调用固定传递 stream=False，因此本 Issue 不引入流式解析或异步接口。

## 响应转换

适配器只读取第一个 choice，并要求其 message.role 为 assistant。content 为 null 时转换为空字符串，这覆盖只有工具调用的正常响应；如果既没有内容也没有工具调用，Day 4 的 Message 校验器会拒绝它，适配器对外抛出 LLMResponseError。

每个供应商工具调用必须包含字符串形式的 id、函数名和 JSON 对象参数。参数是数组、标量、非法 JSON 或缺字段时，适配器抛出 LLMResponseError，不会把半合法数据伪装成 ToolCall。响应中的工具调用只返回给调用方，绝不触发工具执行。

## 错误映射

适配器不把 SDK 的完整错误文本、请求头、响应体或 API Key 放进稳定错误；只暴露 LLMProviderError.code：

| SDK/HTTP 情况 | 稳定类别 |
| --- | --- |
| 401、403、AuthenticationError | AUTHENTICATION |
| APITimeoutError、408 | TIMEOUT |
| APIConnectionError | CONNECTION |
| 429、RateLimitError | RATE_LIMIT |
| 其他 4xx、BadRequestError | BAD_REQUEST |
| 5xx、其他 APIError | SERVICE |
| 未知异常 | UNKNOWN |

DeepSeekLLM 不自行重试；调用方后续可以依据稳定类别决定控制流。响应结构错误属于 LLMResponseError，输入上下文错误属于 LLMInputError，启动配置错误属于 LLMConfigurationError。

## 验收结果

本 Issue 自动化验证使用注入客户端，不需要网络或 API Key：

```text
tests/test_deepseek.py：16 passed
```

实际执行结果如下：

- `uv sync`：使用 uv 绝对路径执行成功，解析并检查 24 个包；`openai==2.52.0` 已写入 `pyproject.toml` 和 `uv.lock`。
- `uv run self-react hello` 的等价已安装入口：成功输出 `Hello from Self-ReAct!`。
- `uv run pytest` 的等价 `.venv` 命令：37 passed；pytest 仅报告无法写入既有 `.pytest_cache` 的权限警告。
- `uv run ruff check .` 的等价 `.venv` 命令：根目录唯一失败来自受保护的 `tmp/day04_success_tool_call_demo.py` 导入排序问题；Day 6 文件单独检查通过。
- `uv run ruff format --check .` 的等价 `.venv` 命令：根目录唯一失败来自受保护的 Day 4 导读示例格式；Day 6 文件单独检查通过。
- `git diff --check`：通过，无空白错误。
- `DEEPSEEK_API_KEY` 显式手动验证：成功输出 `manual verification succeeded: assistant_message`，退出码 0；未记录密钥、请求头或响应正文。

手动入口在未设置密钥时也已验证：输出 `manual verification skipped: DEEPSEEK_API_KEY is not set`，退出码 2。

完整仓库检查的两个 Ruff 例外均来自 Day 6 开始前已存在且明确受保护的文件；没有修改、暂存或删除它们。

## 不在范围内

- Agent 主循环、工具注册、工具执行、ToolResult 生成和重试策略；
- 流式输出、异步调用、Responses API、思考模式或供应商特有扩展字段；
- 自动化测试和默认 CLI 中的真实网络请求；
- 第二个真实供应商或 LangChain/LangGraph 运行时依赖。

## 明天要验证什么

- 在不改变 LLM.complete 接缝的前提下设计工具协议和注册表；
- 用 Day 6 返回的 ToolCall 驱动确定性工具调用，并把 ToolResult 转回 Observation；
- 保持真实模型调用只发生在显式入口，自动化测试继续使用 Fake LLM 或注入客户端。
