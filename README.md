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
- 与供应商解耦的 `LLM` 协议：Fake LLM 与 DeepSeekLLM 可互换，业务代码不依赖具体供应商。
- 四个确定性本地工具：计算器、受限文件读取、内置知识检索、`final_answer` 特殊工具。
- Pydantic v2 结构化领域模型与人类可读的中文执行轨迹。
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

运行时只需要一个配置：DeepSeek API Key。代码从进程环境变量
`DEEPSEEK_API_KEY` 读取密钥（见 [`deepseek.py`](src/self_react/deepseek.py)），
密钥不会写入领域状态、日志或仓库。

```powershell
# PowerShell：只在当前终端生效
$env:DEEPSEEK_API_KEY = "sk-你的密钥"
```

```bash
# Bash：只在当前终端生效
export DEEPSEEK_API_KEY="sk-你的密钥"
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
`run --model deepseek` 发起真实请求时才读取它。

## 运行

### 验证环境

```powershell
uv run self-react hello
```

固定输出 `Hello from Self-ReAct!`，用于验证 uv、打包安装与命令行入口整条链路。

### 执行一次任务（真实模型）

```powershell
uv run self-react run "计算 2 + 2" --model deepseek --show-trace
```

`run` 参数：

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `task` | 任务文本（必填） | — |
| `--model` | `deepseek`（真实 API）或 `fake`（离线确定性演示） | `deepseek` |
| `--max-steps` | 最大决策步数（正整数） | `5` |
| `--show-trace` / `--no-show-trace` | 是否打印人类可读执行轨迹 | 不打印 |

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
```

三条示例固定展示单工具、多工具、工具失败后恢复三条主线，使用 Fake LLM 与
确定性工具，不访问网络、不依赖 API Key，相同命令永远得到相同的决策与观察
（耗时除外）。详细输出见下文[演示记录](#演示记录)。

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
| `deepseek.py` | DeepSeek OpenAI 兼容 Chat Completions 适配器，只做请求/响应转换 |
| `prompts.py` | 最小系统提示词渲染：任务规则 + 工具清单 + 输出格式契约 |
| `parser.py` | 把模型 JSON 输出解析成 `FinalAnswer` 或 `ToolCall`，非法输出抛稳定 `ParseError` |
| `agent.py` | ReAct 主循环：唯一的步数计数与终止判断 |
| `trace.py` | 把终态渲染成稳定的人类可读中文轨迹 |
| `cli.py` | `hello` / `run` / `example` 命令入口 |
| `examples.py` | Day 16 三个确定性端到端示例（数据 + 组合） |
| `tools/` | `Tool` 协议、`ToolRegistry`，以及 calculator、file_reader、retrieve、final_answer |

领域上下文与概念边界见 [`CONTEXT.md`](CONTEXT.md)；核心循环的完整调研与状态图见
[`docs/architecture/react-loop.md`](docs/architecture/react-loop.md)；每个模块的
代码导读存放在 `docs/architecture/`。

## 局限性

这是最小 MVP，以下能力本期明确不做（详见
[项目计划](docs/project-plan.md)）：

- 单智能体、同步、每轮最多执行一个工具；供应商一次返回多个 `tool_calls` 时只执行
  第一个，其余以可恢复失败观察回写。
- 无持久化、暂停/恢复、流式、异步或并行工具调度。
- 知识检索是模块内固定的内置知识库，不是向量数据库或 RAG 平台。
- 文件读取被限制在构造时指定的根目录内（CLI 演示固定为 `C:/allowed`），只读
  UTF-8 文本并截断超长内容。
- 只接入 DeepSeek（OpenAI 兼容接口），默认禁用思考模式（`reasoning_content`），
  以保证多轮工具调用的请求历史稳定。
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
