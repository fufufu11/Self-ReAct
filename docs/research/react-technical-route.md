# Self-ReAct 技术路线调研

> 调研日期：2026-08-01  
> 范围：为 20 天、每日 2 至 4 小时的学习型单智能体 ReAct MVP 选择技术路线；不讨论生产级多智能体平台的完整能力。  
> 来源原则：仅采用论文原文、项目官方仓库/文档、DeepSeek 官方文档和依赖的官方文档。每个事实性结论均在相邻位置给出来源链接。

## 结论先行

当前建议的路线不是“成熟 ReAct 项目的唯一标准栈”，而是针对本仓库目标的最小工程组合：

```text
Python 3.11+
  + uv（管理项目、虚拟环境和锁文件）
  + Pydantic（领域模型、校验、JSON Schema）
  + LLMClient 协议
      + DeepSeekAdapter（首选 OpenAI Python SDK）
      + httpx（仅在需要直接控制 HTTP 时启用）
  + pytest（用 Fake LLM 与确定性工具测试循环）
  + ruff（格式化和静态检查）
  + 进程环境变量（本地由未提交的 .env 辅助）
```

原始 ReAct 只要求模型在任务过程中交错地产生推理轨迹和任务动作，并由动作从外部来源取得新信息；它**没有**规定 Python、某个 SDK、Pydantic 或某种工作流框架。[论文摘要](https://arxiv.org/abs/2210.03629)（访问：2026-08-01）因此，这些技术选型应以“能否让 ReAct 主循环易读、可替换、可测试”为标准，而不应被误解为论文复现的必要条件。

## 判断标准

本项目按以下优先级选择技术，而不是以框架知名度为标准：

1. **忠实保留 ReAct 的最小闭环。** 状态中要能看到 `Thought/Decision -> ToolCall -> Observation -> 下一轮或 Final`；动作应能接入外部信息，且轨迹可检查。[ReAct 论文](https://arxiv.org/abs/2210.03629)（访问：2026-08-01）
2. **提供商可替换。** DeepSeek 官方文档说明其 API 与 OpenAI/Anthropic API 格式兼容，修改配置后可使用兼容 SDK；因此业务层不应直接依赖某家客户端对象。[DeepSeek：首次 API 调用](https://api-docs.deepseek.com/quick_start/your_first_api_call)（访问：2026-08-01）
3. **边界可单测。** 模型网络调用必须能被 Fake LLM 替代，工具必须尽量确定性，这样可在不消耗 API 额度的情况下验证分支、最大步数和错误恢复。这是为本项目设定的工程约束，不是 ReAct 论文的原始要求。
4. **20 天内可验证和可解释。** 优先选择一套小而完整的工具链；不在 MVP 中引入多智能体、持久化执行、人工审批、向量库或部署平台。LangGraph、AutoGen、Semantic Kernel 已提供这些能力，恰好说明它们属于下一阶段，而不是本阶段的依赖。[LangGraph README](https://github.com/langchain-ai/langgraph#readme)；[AutoGen README](https://github.com/microsoft/autogen#readme)；[Semantic Kernel README](https://github.com/microsoft/semantic-kernel#readme)（均访问：2026-08-01）
5. **公开仓库可复现且不泄露密钥。** 依赖和锁文件应入库，虚拟环境与真实 API Key 不入库；DeepSeek 的官方示例通过 `DEEPSEEK_API_KEY` 环境变量传入密钥。[DeepSeek：首次 API 调用](https://api-docs.deepseek.com/quick_start/your_first_api_call)（访问：2026-08-01）

## 为什么建议这些技术

| 选择 | 为什么适合这个 MVP | 取舍与边界 |
| --- | --- | --- |
| Python 3.11+ | 原始 `ysymyth/ReAct` 以 Python/Jupyter Notebook 发布提示词实验代码；LangChain、LangGraph、AutoGen 和 Semantic Kernel 都提供 Python 路线。因此 Python 能直接阅读和对照大部分参考实现。[ysymyth/ReAct README](https://github.com/ysymyth/ReAct#readme)；[LangChain README](https://github.com/langchain-ai/langchain#readme)；[LangGraph README](https://github.com/langchain-ai/langgraph#readme)；[AutoGen README](https://github.com/microsoft/autogen#readme)；[Semantic Kernel README](https://github.com/microsoft/semantic-kernel#readme)（均访问：2026-08-01） | `3.11+` 是本项目统一的支持下限，不是 ReAct 的规定。uv 的项目初始化示例同样以 `requires-python = ">=3.11"` 为默认值；AutoGen 和 Semantic Kernel 当前 README 均要求 Python 3.10+，所以 3.11 能覆盖它们的 Python 基线。[uv 项目指南](https://docs.astral.sh/uv/guides/projects/)；[AutoGen README](https://github.com/microsoft/autogen#installation)；[Semantic Kernel README](https://github.com/microsoft/semantic-kernel#system-requirements)（均访问：2026-08-01） |
| `uv` | uv 官方定义其为 Python 包与项目管理器，能管理依赖、环境和 lockfile；项目同步机制会解析 lockfile 并安装受锁定依赖约束的环境。将 `pyproject.toml` 与 `uv.lock` 提交即可让面试官或贡献者复现同一套依赖。[uv 项目指南](https://docs.astral.sh/uv/guides/projects/)；[uv 项目同步](https://docs.astral.sh/uv/concepts/projects/sync/)（访问：2026-08-01） | 这里的 `uv / venv + pip` 是**二选一**，不是同时使用。默认选择 `uv`；若本地网络、镜像或学校环境导致 uv 不可用，再退回标准库 `venv + pip`，并在 README 说明。`.venv` 不提交。 |
| Pydantic | Pydantic `BaseModel` 可从不可信数据解析和校验，并保证生成模型的字段符合声明类型；其模型也支持序列化和 JSON Schema。它适合承载 `ToolCall`、`ToolResult`、`TraceStep` 和模型输出解析结果。[Pydantic 模型文档](https://docs.pydantic.dev/latest/concepts/models/)（访问：2026-08-01） | 它不是 ReAct 必需依赖。只用于跨模块边界和外部输入；不要把所有内部临时变量都包装成模型。DeepSeek 的工具调用接口以函数 JSON Schema 描述参数，正好使“工具规范 -> 校验模型”成为清晰的边界。[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)（访问：2026-08-01） |
| OpenAI Python SDK（首选） | DeepSeek 官方说明其 API 可用 OpenAI 兼容格式访问，并明确给出 OpenAI 兼容的 `base_url`；首个适配器使用该 SDK，代码量最少。[DeepSeek：首次 API 调用](https://api-docs.deepseek.com/quick_start/your_first_api_call)（访问：2026-08-01） | SDK 只能放在 `DeepSeekAdapter` 内，领域层只依赖自己定义的 `LLMClient` 协议。这样换供应商时不改 Agent、工具或测试。不要在 MVP 同时维护 SDK 与直接 HTTP 两个生产适配器。 |
| `httpx`（后备） | HTTPX 是同时提供同步/异步 API、支持 HTTP/1.1 与 HTTP/2 的 Python HTTP 客户端；当需要观察原始请求、处理供应商差异或 SDK 无法覆盖的端点时可作为直接适配器。[HTTPX 官方文档](https://www.python-httpx.org/)（访问：2026-08-01） | Day 1 不引入。只有出现一个明确、可验收的需求时才建第二个 `HttpxDeepSeekAdapter`；否则会把学习重点从 ReAct 循环转移到重复的传输层代码。 |
| `pytest` | pytest 采用普通 `assert`、自动发现测试，并提供模块化 fixture；适合用 fixture 注入 Fake LLM、工具注册表和临时文件目录，从而覆盖循环的成功与失败路径。[pytest 文档](https://docs.pytest.org/en/stable/)（访问：2026-08-01） | 真实 DeepSeek 调用只做手动 smoke test，不能成为单元测试前提。首批测试应覆盖输出解析、未知工具、工具异常和最大步数。 |
| `ruff` | Ruff 官方将其定位为 Python linter 与 formatter，并支持 `pyproject.toml` 配置；一个工具即可统一格式化与常见静态检查，适合小仓库的低维护成本。[Ruff 概览](https://docs.astral.sh/ruff/)（访问：2026-08-01） | 初期不再叠加 Black、isort、Flake8 等同类工具。类型检查器可在 MVP 稳定后再根据实际收益引入。 |
| 环境变量 + 本地 `.env` | DeepSeek 官方示例使用 `DEEPSEEK_API_KEY` 环境变量承载密钥；因此运行时代码只读取环境变量，符合公开仓库不提交真实密钥的目标。[DeepSeek：首次 API 调用](https://api-docs.deepseek.com/quick_start/your_first_api_call)（访问：2026-08-01） | `.env` 只是本地开发时设置环境变量的便利文件，不应被应用代码视为唯一配置来源，也绝不提交。仓库只保留不含值的 `.env.example`。若需要自动加载，再在一个单独配置模块引入 `python-dotenv`。 |

## 推荐的最小模块边界

下面是根据上述标准得到的实现边界，不是要求照搬成熟框架：

```text
CLI
  -> Agent.run(task, max_steps)
       -> LLMClient.complete(messages, tools)       # 可用 FakeLLM 替换
       -> parse_decision(...)                        # Pydantic 校验
       -> ToolRegistry.invoke(name, arguments)      # Pydantic 校验参数/结果
       -> append TraceStep / Observation
       -> stop: final | max_steps | unrecoverable error

DeepSeekAdapter
  -> OpenAI Python SDK + DeepSeek base_url
```

模型自身不会执行工具函数，官方工具调用示例也将具体函数的执行责任留给应用程序；因此工具注册、参数校验、异常转换和 observation 归 Agent/工具层，而不是 DeepSeek SDK 层。[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)（访问：2026-08-01）

DeepSeek 的 JSON 输出模式也不能替代本地解析与 Pydantic 校验：官方文档明确提示其输出可能偶发空内容。因此 Agent 仍需把“无法解析/字段缺失”作为可测试的终止或恢复分支，而不能把供应商格式保证当作系统正确性保证。[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode)（访问：2026-08-01）

## 成熟项目的技术路线对照

以下项目都可以参考，但它们解决的问题层级不同，不能把“用了什么”直接当作 Self-ReAct 的依赖清单。

| 项目 | 官方定位与技术路线 | 对本项目应学习什么 | 此阶段不应照搬什么 |
| --- | --- | --- | --- |
| [ysymyth/ReAct](https://github.com/ysymyth/ReAct) | 这是论文作者发布的 GPT-3 prompting 实验代码；README 给出 HotpotQA、FEVER、ALFWorld、WebShop 等 notebook 实验，并要求 OpenAI API Key 和 `openai`/`alfworld` 依赖。[README](https://github.com/ysymyth/ReAct#readme)（访问：2026-08-01） | 学习原始任务轨迹：推理与动作交错、每个任务拥有受限的动作空间、观察结果回写下一轮提示。论文说明动作能获取外部信息，推理可维护和修正行动计划。[论文](https://arxiv.org/abs/2210.03629)（访问：2026-08-01） | 它是研究复现实验，不是可扩展通用 Agent SDK；不要把 notebook、基准任务代码或旧模型调用方式当作工程架构。 |
| [LangChain](https://github.com/langchain-ai/langchain) | 当前官方 README 将其定位为构建 Agent 和 LLM 应用的框架，提供可互操作组件、第三方集成，并以 `uv add langchain` 展示 Python 安装；更复杂的编排指向 LangGraph。[README](https://github.com/langchain-ai/langchain#readme)（访问：2026-08-01） | 学习模型与工具的抽象边界、可替换集成和组合式 API。 | 不把 LangChain 作为本项目运行时依赖，否则核心 ReAct 循环会隐藏在框架内部，削弱“手搓并讲清楚”的作品目标。 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 当前官方 README 将其定位为低层、面向长运行有状态 Agent 的编排框架，列出持久执行、人工介入、短期/长期记忆、追踪和部署等能力；可独立于 LangChain 使用。[README](https://github.com/langchain-ai/langgraph#readme)（访问：2026-08-01） | 学习“显式状态 + 节点/边 + 可观测轨迹”的思想，把 `AgentState`、步数上限与终止分支设计清楚。 | 不实现持久化恢复、人工审批、跨会话记忆或图执行引擎；MVP 仅需一个清晰的线性循环。 |
| [Microsoft AutoGen](https://github.com/microsoft/autogen) | 当前官方 README 将其定位为多智能体 AI 应用框架，Python 路线以 `autogen-agentchat` 和 `autogen-ext[openai]` 分层安装，并用 `asyncio`、模型客户端、工具及最大工具迭代次数示例化运行。该 README 同时声明仓库已处于 maintenance mode，建议新用户转向 Microsoft Agent Framework。[README](https://github.com/microsoft/autogen#readme)（访问：2026-08-01） | 学习模型客户端与 AgentChat/扩展层的分离，以及“工具迭代上限”这一防无限循环的运行时保护。 | 不引入多智能体对话、MCP workbench、Studio 或事件运行时；这些会显著扩大 20 天 MVP 的范围。 |
| [Microsoft Semantic Kernel](https://github.com/microsoft/semantic-kernel) | 当前官方 README 将其定位为模型无关的 Agent/多智能体编排 SDK，支持 Python 3.10+、.NET 与 Java，并以 plugins、函数、结构化输出和 process framework 扩展能力。README 也说明其已迁移到 Microsoft Agent Framework 的方向。[README](https://github.com/microsoft/semantic-kernel#readme)（访问：2026-08-01） | 学习插件/函数元数据、模型适配和结构化输出的边界；其 Python 示例使用 Pydantic 模型作为结构化响应格式。[README 示例](https://github.com/microsoft/semantic-kernel#agent-with-plugins---python)（访问：2026-08-01） | 不引入跨语言 SDK、向量数据库、多智能体流程和企业部署能力；这些是通用平台目标，不是学习 ReAct 基础闭环的前置条件。 |

## 与计划文档一致的执行决策

1. Day 3 固定 `Python 3.11+ + uv`，创建 `pyproject.toml`、提交 `uv.lock`，不同时维护 `venv + pip` 教程。
2. Day 4 仅为跨边界数据建立 Pydantic 模型：`Message`、`ToolSpec`、`ToolCall`、`ToolResult`、`TraceStep`、`AgentState`。
3. Day 5 先定义很小的 `LLMClient` 协议和 `FakeLLM`；Day 6 再实现唯一的 `DeepSeekAdapter`，内部优先用 OpenAI SDK。
4. Day 7 至 Day 14 的测试全部依赖 Fake LLM 与本地确定性工具；真实 API 只保留一个显式手动验证脚本。
5. `ruff format`、`ruff check` 与 `pytest` 是每个功能 PR 的基础检查。真实 `.env`、`.venv` 和 API Key 始终忽略；只更新 `.env.example`。

## 不确定项与复查点

- **最终演示场景尚未确定。** 在 Day 9 完成计算器、受限文件读取和确定性检索/模拟工具后，再以已验证的工具选择一个场景；这比先为未验证场景引入 RAG 或浏览器工具更可控。
- **DeepSeek 模型名与功能会变化。** 官方文档应作为实现时的唯一准据，特别是模型名称、工具调用和严格 JSON Schema 的支持范围；不要把本文中的 API 示例视为永久配置。[DeepSeek 文档首页](https://api-docs.deepseek.com/)；[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)（访问：2026-08-01）
- **AutoGen 与 Semantic Kernel 的产品状态会变化。** 上表反映调研日期当天各自官方 README 的表述；开始具体对照实现前，应再次读取其官方文档和版本说明。[AutoGen README](https://github.com/microsoft/autogen#readme)；[Semantic Kernel README](https://github.com/microsoft/semantic-kernel#readme)（访问：2026-08-01）

## 参考来源

- [Yao et al., ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629)，访问：2026-08-01
- [ysymyth/ReAct 官方仓库](https://github.com/ysymyth/ReAct)，访问：2026-08-01
- [LangChain 官方仓库](https://github.com/langchain-ai/langchain)，访问：2026-08-01
- [LangGraph 官方仓库](https://github.com/langchain-ai/langgraph)，访问：2026-08-01
- [Microsoft AutoGen 官方仓库](https://github.com/microsoft/autogen)，访问：2026-08-01
- [Microsoft Semantic Kernel 官方仓库](https://github.com/microsoft/semantic-kernel)，访问：2026-08-01
- [DeepSeek API：首次 API 调用](https://api-docs.deepseek.com/quick_start/your_first_api_call)，访问：2026-08-01
- [DeepSeek API：Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)，访问：2026-08-01
- [DeepSeek API：JSON Output](https://api-docs.deepseek.com/guides/json_mode)，访问：2026-08-01
- [uv 项目指南](https://docs.astral.sh/uv/guides/projects/)，访问：2026-08-01
- [uv 项目同步](https://docs.astral.sh/uv/concepts/projects/sync/)，访问：2026-08-01
- [Pydantic 模型文档](https://docs.pydantic.dev/latest/concepts/models/)，访问：2026-08-01
- [HTTPX 官方文档](https://www.python-httpx.org/)，访问：2026-08-01
- [pytest 官方文档](https://docs.pytest.org/en/stable/)，访问：2026-08-01
- [Ruff 官方文档](https://docs.astral.sh/ruff/)，访问：2026-08-01
