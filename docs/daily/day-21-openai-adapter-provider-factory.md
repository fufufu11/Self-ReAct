# Day 21：OpenAI 原生后端 + 模型 provider 工厂（R-01）

> Issue：[45 feat: OpenAI 原生后端 + 模型 provider 工厂（R-01）](https://github.com/fufufu11/Self-ReAct/issues/45)
>
> 这是 v0.2+ 迭代规划（`docs/project-roadmap.md`）的第一项工作：把"可插拔
> 后端"从协议层面落到实现层面——新增 OpenAI 原生适配器，并把模型选择抽象成
> 可注册的 provider 工厂。本记录只描述这一个可独立验收的目标；R-02/R-03/
> R-07 等后续工作项按 roadmap 顺序在后续对话推进。

## 今天理解了什么

### 第一个认识：共享转换逻辑属于"供应商无关"的层

复盘 `deepseek.py` 后发现，它其实包含两层职责：**与供应商无关的转换**
（领域消息 -> OpenAI 兼容请求、工具定义序列化、响应反序列化、SDK 错误
分类）和**DeepSeek 特有的配置**（默认地址、模型名、思考模式开关、客户端
构造）。OpenAI 原生接口与 DeepSeek 走同一种 Chat Completions 消息结构，
前一层完全可复用；后一层才是两个适配器的真正差异。

因此 R-01 的第一步不是"复制 deepseek.py 再改几个常量"，而是把转换逻辑
抽到新的共享模块 `openai_compat.py`，让 DeepSeek 与 OpenAI 两个适配器都
只保留自己的配置与客户端构造。行为逐字节不变，代码量不膨胀，未来接入
第三个 OpenAI 兼容供应商时也不需要再复制。

### 第二个认识：provider 工厂把"模型选择"从 CLI 里拆出来

改之前 `cli.py` 的 `build_llm` 是一个两分支 if：`fake` 和 `deepseek`。
每新增一个供应商就要改 CLI 的模型分支，模型的扩展点被埋在命令入口里。
R-01 把它改成注册表：`providers.py` 维护"模型名 -> 工厂"映射，CLI 只做
两件事——把 `--model` 的可选值换成 `available_providers()`，把
`build_llm` 委托给 `create_provider(model)`。新增供应商只需要
`register_provider("名字", 工厂)`，CLI 一行不改。

惰性导入也被保留并集中化：deepseek/openai 的适配器只在真正创建时才导入，
所以 `--help`、参数校验错误路径和 `--model fake` 演示都不会读取任何
API Key。

### 第三个认识：OpenAI 适配器的"新"只在一个地方

`OpenAILLM` 与 `DeepSeekLLM` 的差异其实只有三点：默认地址
（`https://api.openai.com/v1`）、默认模型（当前默认别名 `gpt-5.6`）、
密钥环境变量（`OPENAI_API_KEY`）。DeepSeek 特有的思考模式开关（`extra_body`
里的 `thinking`）在 OpenAI 适配器中不存在，请求直接传 `extra_body=None`。
其余——消息序列化、工具定义、响应转换、错误分类——全部走共享模块，
这也解释了为什么 `test_openai.py` 几乎可以镜像 `test_deepseek.py`：测试的
是同一套契约在两个适配器下的行为。

## 今天交付了什么

- 新增 [`src/self_react/openai_compat.py`](../../src/self_react/openai_compat.py)：
  DeepSeek/OpenAI 共用的消息、工具定义与响应转换逻辑（从 `deepseek.py`
  原样迁入，行为不变）；
- 新增 [`src/self_react/openai.py`](../../src/self_react/openai.py)：
  `OpenAILLM`，默认读取 `OPENAI_API_KEY`，`base_url` / `model` / `timeout`
  可配置，支持注入客户端（离线测试）；
- 新增 [`src/self_react/providers.py`](../../src/self_react/providers.py)：
  provider 注册表与工厂，默认注册 `fake` / `deepseek` / `openai`，
  提供 `register_provider` 扩展点；
- [`src/self_react/deepseek.py`](../../src/self_react/deepseek.py)：改用共享
  转换模块，`DeepSeekLLM` 公开行为不变；
- [`src/self_react/cli.py`](../../src/self_react/cli.py)：`--model` 可选值来自
  注册表，`build_llm` 委托 `create_provider`，新增 `openai` 选项；
- 新增 [`tests/test_openai.py`](../../tests/test_openai.py)（27 个用例）与
  [`tests/test_providers.py`](../../tests/test_providers.py)（15 个用例），
  `test_cli.py` 补 4 个用例；全部离线、确定性；
- 文档同步：[README](../../README.md) 的配置/运行/架构简介/局限性、
  [`.env.example`](../../.env.example) 补充 OpenAI 密钥说明、本记录与
  [Day 21 代码导读](../architecture/day-21-openai-adapter-provider-factory-code-walkthrough.md)。

## 设计边界与不变量

- **DeepSeek 既有行为不变**：`DeepSeekLLM` 的公开接口、请求体（含思考模式
  禁用）、错误消息全部保持原样，既有 14 个测试文件无改动；
- **回归基准**：Day 16 的 `self-react example` 三条命令输出不变；
  `Agent` 仍是唯一循环控制者；
- **全部离线确定性**：`OpenAILLM` 单测全部注入客户端，不访问网络、不依赖
  真实密钥；
- **工厂只存名称与工厂**：注册表不保存客户端、密钥或其它运行时资源；
- **扩展点不覆盖**：重复注册同一模型名直接拒绝，避免注册表被意外改写；
- **不越界**：不实现流式（R-05）、解析失败重试（R-02）、工具 Schema 自动
  生成（R-03）或场景落地（R-07）。

## 遇到的问题与解决过程

### 问题一：把 250 行共享逻辑从 deepseek.py 迁走，如何保证行为不变

共享模块不是"重写"，而是把 `deepseek.py` 里的函数原样搬进
`openai_compat.py`，只把 `_serialize_messages` 等私有名改成公开的
`serialize_messages`。迁移后立刻跑 `test_deepseek.py`、
`test_deepseek_boundaries.py` 与 `test_tool_schemas.py`，116 个用例全部
通过，证明转换逻辑逐字节等价。

### 问题二：`providers.py` 里演示 Fake LLM 放哪

原来的 `_demo_fake_llm` 在 `cli.py`。如果 provider 工厂要注册 `fake`，
要么让 `providers` 反向导入 `cli`（循环依赖），要么把演示数据搬进
`providers`。选择后者：演示数据只是三条固定消息，搬到 `providers.py` 后，
`create_provider("fake")` 不需要导入 CLI 也能工作，CLI 的职责回到"参数解析
与组装"。

### 问题三：OpenAI 默认模型名怎么选

OpenAI 官方模型指南（developers.openai.com，2026-08-06 访问）显示当前
默认别名是 `gpt-5.6`；文档页面直接访问被 403 拦截，改用官方域名搜索
结果交叉确认。默认名始终可通过 `OpenAILLM(model=...)` 覆盖，不把模型名
写进领域状态。

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

- `uv sync`：成功，锁文件无变化（无新运行时依赖）；
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`；
- 三条 `example`：退出码 0，输出与 Day 16 记录一致；
- `uv run pytest`：全绿（既有 357 通过 / 3 跳过 + 新增 46 个用例）；
- `uv run ruff check src tests` 与 `ruff format --check src tests`：通过；
- `git diff --check`：无空白错误；
- 全仓库 ruff 例外与交接记录一致：均来自受保护的 `tmp/` 与 Day 4/6 导读，
  未修改；新文件在只包含本任务文件的干净副本中复验（见 PR 描述）。

## 不在范围内

- R-02 解析失败有界重试、R-03 工具 Schema 自动生成、R-07 日志/故障排查
  场景（按 roadmap 顺序在后续对话推进）；
- 流式接口（`complete_stream`，属 R-05）；
- 真实 OpenAI/DeepSeek API 调用自动化测试（只做手动验收记录，不作自动化
  前置条件）；
- 修改受保护的 `tmp/`、历史导读、交接文档与 `docs/project-roadmap.md`。

## 明天要验证什么

- 若配置真实 `OPENAI_API_KEY`，手动跑 `self-react run "计算 2 + 2" --model
  openai --show-trace`，验证 OpenAI 原生接口的多轮工具调用；
- 继续按 roadmap 顺序推进 R-02（解析失败有界重试），保持"一个 Issue 一个
  PR"的节奏；
- Day 16 三条示例继续作为回归基准，任何后续改动都不改变它们的输出。
