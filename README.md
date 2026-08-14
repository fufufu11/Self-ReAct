# Self-ReAct

> 一个最小可用、可测试、可演示的 ReAct（Reason + Act）智能体框架。

Self-ReAct 实现了一个单智能体 ReAct 闭环：模型基于当前状态做决策，
要么调用一个本地工具，要么直接给出最终回答；工具结果作为观察（Observation）
回写上下文，模型据此进入下一轮决策，直到给出最终回答或触发明确的终止条件。

项目不追求复刻成熟框架的全部功能，而是把每一条边界都做到能讲清楚、能测试：
`LLM` 接口与供应商解耦，工具注册与错误处理统一，领域模型和轨迹可序列化，
自动化测试全部离线、确定性、可复现。

## 特性

- 单智能体 ReAct 主循环：模型决策 -> 工具执行 -> 观察回写 -> 下一轮决策（或终止）。
- 与供应商解耦的 `LLM` 协议：Fake LLM、DeepSeekLLM 与 OpenAILLM 可互换，
  业务代码不依赖具体供应商。
- 确定性本地工具：计算器、受限文件读取、内置知识检索、`final_answer` 特殊工具，
  以及 `log_query`（NDJSON 日志过滤/聚合）与 `runbook_search`（BM25 知识检索）。
- Pydantic v2 结构化领域模型与人类可读的中文执行轨迹。
- 工具参数 Schema 自动生成（Pydantic 参数模型 / 函数签名）与注册表预校验，
  非法参数在分派前以稳定错误码被拒。
- 短程会话记忆：超过 `--context-window` 字符预算时，自动按整轮裁剪旧历史
  并回填规则式摘要（Claude auto-compact 风格），默认 20,000 字符。
- 流式输出：`LLM.complete_stream` 增量协议 + Fake LLM 确定性流 + DeepSeek/OpenAI
  真流式；CLI `--stream` 实时逐字输出最终回答，默认关闭。
- 命令行入口：`hello` / `run` / `example`。
- 全离线可测：自动化测试使用 Fake LLM 与注入客户端，不访问网络、不依赖真实 API Key。

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。在仓库根目录执行：

```powershell
uv sync
```

安装后即可用 `uv run self-react ...` 调用命令行入口，或通过
`pyproject.toml` 中的 `[project.scripts]` 使用已安装的 `self-react` 命令。

## 配置

运行时按所选模型需要 DeepSeek 或 OpenAI 的 API Key。`run --model deepseek`
从进程环境变量 `DEEPSEEK_API_KEY` 读取密钥（见
[`deepseek.py`](src/self_react/deepseek.py)）；`run --model openai` 从
`OPENAI_API_KEY` 读取密钥（见 [`openai.py`](src/self_react/openai.py)）。
密钥不会写入领域状态、日志或仓库。

```powershell
# PowerShell：只在当前终端生效
$env:DEEPSEEK_API_KEY = "sk-你的密钥"
$env:OPENAI_API_KEY = "sk-你的密钥"   # 使用 OpenAI 模型时设置
```

```bash
# Bash：只在当前终端生效
export DEEPSEEK_API_KEY="sk-你的密钥"
export OPENAI_API_KEY="sk-你的密钥"   # 使用 OpenAI 模型时设置
```

也可以把密钥放在本地 `.env` 文件（已被 `.gitignore` 忽略，不会提交）。项目
不会自动加载 `.env`，需要在当前终端手动加载后生效，例如：

```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
    }
}
```

模板见 [`.env.example`](.env.example)：复制为 `.env` 后填入密钥即可。注意
`hello`、`example` 与 `run --model fake` 都不需要密钥；只有
`run --model deepseek` / `run --model openai` 发起真实请求时才读取对应的
API Key。

## 运行

### 验证环境

```powershell
uv run self-react hello
```

固定输出 `Hello from Self-ReAct!`，用于验证 uv、打包安装与命令行入口整条链路。

### 执行一次任务（真实模型）

```powershell
uv run self-react run "计算 2 + 2" --model deepseek --show-trace
uv run self-react run "计算 2 + 2" --model openai --show-trace
uv run self-react run "计算 2 + 2" --model deepseek --show-trace --stream
```

`run` 参数：

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `task` | 任务文本（必填） | — |
| `--model` | `deepseek`（真实 DeepSeek API）、`openai`（真实 OpenAI API）或 `fake`（离线确定性演示） | `deepseek` |
| `--max-steps` | 最大决策步数（正整数） | `5` |
| `--context-window` | 上下文窗口（字符数，正整数）；超过后自动按整轮裁剪旧历史并回填规则式摘要 | `20000` |
| `--show-trace` / `--no-show-trace` | 是否打印人类可读执行轨迹 | 不打印 |
| `--stream` | 实时逐字输出最终回答（从流式增量中提取，不打印逐步轨迹） | 不开启 |
| `--scenario` | 场景工具包：`log-troubleshooting`（日志/故障排查）；不指定时使用默认四个工具 | 不指定 |

没有 API Key 时，可以用 Fake LLM 离线看一遍完整流水线（固定走
计算器 -> 检索 -> 最终回答）：

```powershell
uv run self-react run "演示任务" --model fake --show-trace
```

### 端到端示例（离线、确定性）

```powershell
uv run self-react example single-tool
uv run self-react example multi-tool
uv run self-react example failure-recovery
uv run self-react example log-5xx-spike
uv run self-react example log-error-window
uv run self-react example log-release-correlation
```

三条示例固定展示单工具、多工具、工具失败后恢复三条主线，使用 Fake LLM 与
确定性工具，不访问网络、不依赖 API Key，相同命令永远得到相同的决策与观察
（耗时除外）。后三条是日志/故障排查场景的确定性示例，使用 `log_query`、
`runbook_search` 等工具。详细输出见下文[演示记录](#演示记录)。

用真实模型跑日志/故障排查场景时加上 `--scenario`：

```powershell
uv run self-react run "排查 cgi-bin 服务的 500 错误突增" `
  --model deepseek --scenario log-troubleshooting --show-trace
```

## 架构简介

核心循环：

```text
任务输入
  -> 根据当前状态推理/决策
  -> 解析为"工具动作"或"最终回答"
  -> 校验并执行工具动作
  -> 将工具结果转换为观察并写回状态
  -> 进入下一轮决策，或以明确原因终止
```

源码模块（`src/self_react/`）：

| 模块 | 职责 |
| --- | --- |
| `models.py` | Pydantic 领域模型：`Message`、`ToolCall`、`ToolResult`、`Observation`、`AgentState`、`TraceStep` 等，所有跨边界数据走同一契约 |
| `llm.py` | `LLM` 协议与确定性 Fake LLM |
| `deepseek.py` | DeepSeek OpenAI 兼容 Chat Completions 适配器，只做请求/响应转换（与 OpenAI 共用转换逻辑） |
| `openai.py` | OpenAI 原生 Chat Completions 适配器，默认读取 `OPENAI_API_KEY`，`base_url`/`model`/`timeout` 可配置 |
| `openai_compat.py` | DeepSeek/OpenAI 共用的消息、工具定义与响应转换逻辑 |
| `providers.py` | 模型 provider 注册表与工厂：按 `--model` 选择适配器，提供注册扩展点 |
| `prompts.py` | 最小系统提示词渲染：任务规则 + 工具清单 + 输出格式契约 |
| `parser.py` | 把模型 JSON 输出解析成 `FinalAnswer` 或 `ToolCall`，非法输出抛稳定 `ParseError` |
| `agent.py` | ReAct 主循环：唯一的步数计数与终止判断 |
| `memory.py` | 短程会话记忆：`ContextPolicy` 按字符窗口整轮裁剪消息并回填规则式摘要，纯函数、离线确定 |
| `trace.py` | 把终态渲染成稳定的人类可读中文轨迹 |
| `cli.py` | `hello` / `run` / `example` 命令入口 |
| `examples.py` | Day 16 三个确定性端到端示例（数据 + 组合） |
| `tools/` | `Tool` 协议、`ToolRegistry`、参数 Schema 自动生成与预校验，以及 calculator、file_reader、retrieve、log_query、runbook_search、final_answer |
| `scenarios/` | 应用场景子包；`log_troubleshooting` 提供真实日志 fixture（NASA HTTP 日志片段）与 runbook/发布记录、工具组装与三个确定性示例 |

领域上下文与概念边界见 [`CONTEXT.md`](CONTEXT.md)；核心循环的完整调研与状态图见
[`docs/architecture/react-loop.md`](docs/architecture/react-loop.md)；每个模块的
代码导读存放在 `docs/architecture/`。

## 局限性

这是最小 MVP，以下能力本期明确不做（详见
[项目计划](docs/project-plan.md)）：

- 单智能体、同步、每轮最多执行一个工具；供应商一次返回多个 `tool_calls` 时只执行
  第一个，其余以可恢复失败观察回写。
- 无持久化、暂停/恢复、异步或并行工具调度。
- 默认知识检索是模块内固定字典；R-07/R-08 场景提供固定 NDJSON 语料的 BM25 检索
  （`runbook_search`），同样不是向量数据库或 RAG 平台；日志 fixture 是公共领域
  真实访问日志的固定时间窗片段（见 `docs/architecture/day-27-*` 与场景内
  `data/PROVENANCE.md`）。
- 文件读取被限制在构造时指定的根目录内（CLI 演示固定为 `C:/allowed`），只读
  UTF-8 文本并截断超长内容。
- 只接入 DeepSeek 与 OpenAI 两个供应商（均走 OpenAI 兼容 Chat Completions
  接口）；DeepSeek 默认禁用思考模式（`reasoning_content`），以保证多轮
  工具调用的请求历史稳定。
- 无 Web 前端、鉴权、限流、分布式执行与完整可观测性平台。

循环有界：`max_steps` 由 `Agent` 强制执行；解析失败、未知工具、不可恢复的工具
失败都会以明确的终止原因结束，不会静默吞掉错误。

## 演示记录

### 离线确定性示例（Day 16）

`uv run self-react example ...` 三条命令在 2026-08-05 实测全部以退出码 0 结束，
最终回答与轨迹结构如下：

| 示例 | 轨迹 | 最终回答 |
| --- | --- | --- |
| `single-tool` | calculator -> 最终回答（2 / 2 步） | `2 + 2 = 4。` |
| `multi-tool` | calculator -> retrieve -> 最终回答（3 / 3 步） | `计算结果是 4；ReAct 是一种让模型推理与行动交错的智能体范式。` |
| `failure-recovery` | retrieve(unknown-topic) 失败 -> retrieve(react) 成功 -> 最终回答（3 / 3 步） | `第一次检索失败后改用 react，成功找到 ReAct 的说明。` |

失败恢复示例的首次观察带 `TOOL_EXECUTION_ERROR` 错误码与"可重试：是"标记，
完整人类可读轨迹由 `render_trace` 输出。

### 真实 DeepSeek 手动验收（Day 16）

显式配置 `DEEPSEEK_API_KEY` 后，用真实 DeepSeek（`deepseek-v4-flash`）执行三个
与示例同构的任务，全部以 `FINAL_ANSWER` 结束：

| 任务 | 真实工具轨迹 | 步数 | 结果 |
| --- | --- | --- | --- |
| 计算 2 + 2 | calculator -> 观察 4 -> 最终回答 | 2 / 5 | `2 + 2 = 4` |
| 计算 2 + 2，并检索 react 主题 | calculator -> retrieve -> 最终回答 | 3 / 5 | 汇总计算与 ReAct 说明 |
| 检索 qwerty123，失败就换 react | retrieve(qwerty123) 失败 -> retrieve(react) 成功 -> 最终回答 | 3 / 5 | 说明失败后改用 react 成功 |

  真实模型调用结果是非确定性的，不作为自动化测试前置条件；离线确定性的
  `self-react example` 三条命令是可复现基准。

### 真实 DeepSeek 流式手动验收（Day 25）

`--stream` 走 `complete_stream` 真流式；2026-08-11 用真实 DeepSeek
（`deepseek-v4-flash`）验收两条任务，均以 `FINAL_ANSWER` 结束：

| 任务 | 真实流式工具轨迹 | 步数 | 结果 |
| --- | --- | --- | --- |
| 计算 2 + 2 | calculator -> 观察 4 -> 最终回答 | 2 / 5 | `2 + 2 = 4` |
| 计算 2 + 2，并检索 react 主题 | calculator -> retrieve -> 最终回答 | 3 / 5 | 汇总计算与 ReAct 说明 |

真实 OpenAI 流式因 `OPENAI_API_KEY` 无效在验收时返回 `AUTHENTICATION`，
留待有效密钥后补充（与 Day 16 的约定一致：真实调用不作为自动化前置条件）。

> 收尾调整（2026-08-11）：按用户要求，`--stream` 只输出最终回答，且从
> 流式增量中实时逐字打印（不再打印逐步决策/工具调用/观察）；完整轨迹仍由
> `--show-trace` 提供。

### 日志/故障排查场景（Day 26）

`self-react example log-*` 三条场景示例离线确定，最终回答要点：

| 示例 | 轨迹主线 | 最终回答要点 |
| --- | --- | --- |
| `log-5xx-spike` | log_query 总量/过滤 -> calculator 占比 -> 聚合错误码 -> runbook_search -> 最终回答 | geturlstats.pl 53 条 500、占 cgi-bin 日志约 10.9%，集中在 10:49-10:52 |
| `log-error-window` | log_query(group_by=hour) -> 最终回答 | 500 集中在 1995-07-03 10:00 整点桶（53 条），09:00/11:00 桶为 0 |
| `log-release-correlation` | 时间窗过滤 + file_reader 发布记录 -> 最终回答 | 500 起点 10:49 与 cgi-bin 10:00 发布 geturlstats 1.1.0 重合，判断相关 |

真实 DeepSeek（`deepseek-v4-flash`）用 `--scenario log-troubleshooting` 执行
“排查 cgi-bin 500 突增”任务，以 `FINAL_ANSWER` 结束：模型依次调用
`log_query(group_by=error_code)`、`runbook_search`、`log_query(group_by=hour)`、
按错误码过滤与 `keyword` 过滤，最终给出根因假设与回滚/修复等下一步动作。
真实调用结果非确定性，不作为自动化测试前置条件。

### 真实日志场景（Day 27）

R-08 把合成日志替换为真实数据：NASA Kennedy Space Center WWW 服务器 1995-07
访问日志（公共领域、可自由再分发）的固定 3 小时窗口（`1995-07-03 09:00-11:59`，
14,130 行），包含真实故障“`GET /cgi-bin/geturlstats.pl` 在 10:49-10:52 连续
返回 53 次 HTTP 500”。来源、许可与规范化规则见
[`data/PROVENANCE.md`](src/self_react/scenarios/log_troubleshooting/data/PROVENANCE.md)
与 [`ADR 0002`](docs/adr/0002-real-log-fixture.md)。

2026-08-14 用真实 DeepSeek（`deepseek-v4-flash`）验收两条任务：

| 任务 | 真实轨迹 | 步数 | 结果 |
| --- | --- | --- | --- |
| 找出错误码 500 集中出现的时间窗口 | log_query(group_by=hour) -> log_query 精确查询 -> 最终回答 | 4 / 5 | 锁定 1995-07-03 10:49:40 ~ 10:50:26 窗口 |
| 排查 cgi-bin 服务的 500 错误突增 | log_query / runbook_search 多次调用 | 5 / 5（8/8） | 步数耗尽（MAX_STEPS_EXCEEDED），未在预算内给出最终回答 |

真实调用结果非确定性：聚焦任务可完成，开放排查任务可能耗尽 `max_steps` 预算
并被框架明确终止，不作为自动化测试前置条件。

### 3分钟讲解

3 分钟讲解稿见 [`docs/demo/3-minute-talk.md`](docs/demo/3-minute-talk.md)。

## 文档导航

- [项目计划](docs/project-plan.md)：目标、边界、开发约定与 Issue/PR 索引。
- [CONTEXT.md](CONTEXT.md)：领域上下文与统一术语。
- [ReAct 核心循环](docs/architecture/react-loop.md)：论文调研与状态图。
- [每日记录](docs/daily/)：每日开发过程、遇到的问题与验证结果。
- [架构导读](docs/architecture/)：每个模块的代码导读。
- [贡献指南](CONTRIBUTING.md)：开发流程、提交信息与分支规范。
- [LICENSE](LICENSE)：MIT License。
