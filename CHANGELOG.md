# Changelog

本项目的所有重要改动都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

自 v1.0.0 发布后的改动（2026-08-16 至 2026-08-21，随项目收官冻结）。

### Added

- 规划/反思可选模式（R-06，#76）：`run --plan` 任务开始先输出结构化计划；
  `run --reflect` 工具失败后强制一步"总结原因 + 下一步方案"；默认关闭。
- 工具层 defense-in-depth 参数硬校验（#78）：`log_query` / `file_reader`
  路径白名单与全数字 keyword 拒绝，`service` 参数按数据校验。
- fixture 级 PII 守卫测试（#80）：对三个 NDJSON fixture 做邮箱/SSN/电话
  正则扫描，防止清洗后个人信息泄漏。
- 评估集与效果指标（#82）：离线 top-k 检索指标（recall / precision / F1@k）
  与真实模型评估集；真实 DeepSeek 24 次同协议评估，任务成功率
  66.7% → 95.8%。
- 重复查询硬停（#84）：查询类工具连调达到阈值后强制收尾，堵住步数耗尽漏洞。
- 查询护栏升级（#91/#92/#94）：查询次数按"有效查询"计数，新增证据收口
  清单与硬兜底——被拦后仍连续查询直接终止。
- DeepSeek 适配器默认开启思考模式，`reasoning_content` 非流式/流式两条
  路径完整往返（#89）。
- OpenAI 适配器支持低推理档（`reasoning_effort` 默认 `low`）与
  `OPENAI_BASE_URL` 中转环境变量（#97，收官提交）。

### Changed

- 解析失败重试边界微调（#86）：由全局计数改为每失败序列有界重试，
  连续失败达到上限以 `MODEL_OUTPUT_PARSE_ERROR` 终止。

## [1.0.0] - 2026-08-16

里程碑 M3 收尾：真实场景上线 + `--stream` 修复 + README 演示记录更新。

### Added

- 日志/故障排查场景（R-07，#64）：`log-troubleshooting` 场景工具包
  （calculator / file_reader / log_query / runbook_search / final_answer）、
  runbook RB-404/403/503、三个确定性示例。
- 真实日志场景化（R-08，#66）：用真实 Apache 访问日志替换合成数据。
- promjet.ru 2021-12 真实 Apache 访问日志（MIT）替换 NASA 数据（R-09，#68）：
  真实事件"03:14-03:18 五分钟 733 条 HTTP 404，疑似外部扫描"。
- 日志场景提示词引导（R-10，#70）：`render_system_prompt` 新增
  `extra_instructions` 注入缝 + 场景五条中文指引；真实 DeepSeek 开放排查
  任务 8 步预算内 7/8 收敛（修复前 5/5 步数耗尽）。
- `--stream` 真实模型最终回答实时逐字透出（R-11，#72）：原生
  `final_answer` 工具调用经 `StreamChunk.final_answer_content` 增量透出
  （真实验收：876 字符 / 482 个增量块 / 约 3.3 秒）。

## [0.3.0] - 2026-08-11

里程碑 M2：短程会话记忆与流式输出（R-04~R-05）。

### Added

- 短程会话记忆 / 上下文管理（R-04，#54）：`ContextPolicy` 字符窗口 +
  整轮原子裁剪 + 规则式摘要回填，CLI 增加 `--context-window`。
- 流式输出（R-05，#56）：`LLM.complete_stream` 增量协议 + FakeLLM
  确定性流 + DeepSeek/OpenAI 真流式，向后兼容原 `complete`。
- `--stream` 只输出最终回答并实时逐字打印（#58/#60）；
  完整决策轨迹由 `--show-trace` 提供。

## [0.2.0] - 2026-08-09

里程碑 M1（补打标签）：框架边界补齐（R-01~R-03）。

### Added

- OpenAI 原生后端 + 模型 provider 工厂（R-01，#46）：新增 `OpenAILLM`
  与可注册的 provider 工厂，DeepSeek/OpenAI 共享消息与响应转换逻辑。
- 解析失败有界重试（R-02，#48/#50）：解析失败时回写稳定错误、消耗一步
  预算、至多重试一次，杜绝无限子循环。
- 工具 Schema 自动生成 + 注册表 Schema 预校验（R-03，#52）：参数模型 /
  函数签名自动生成 JSON Schema，分派前按 Schema 拒绝非法参数。

## [0.1.0] - 2026-08-05

初始 MVP：最小可用、可测试、可演示的 ReAct 智能体框架。

### Added

- Pydantic v2 结构化领域模型：`Message`、`ToolCall`、`ToolResult`、
  `Observation`、`AgentState`、`TraceStep` 等统一数据契约。
- `LLM` 协议与确定性 Fake LLM；DeepSeek OpenAI 兼容适配器，
  支持原生 tool calls 多轮工具调用。
- 工具注册表与四个内置工具：calculator、受限 file_reader、retrieve、
  `final_answer` 特殊工具。
- 最小系统提示词渲染、模型 JSON 输出解析器（非法输出抛稳定 `ParseError`）。
- ReAct 主循环：步数预算、终止判断、观察回写。
- 人类可读的中文执行轨迹渲染；CLI 入口 `hello` / `run` / `example`。
- 三个离线确定性端到端示例：single-tool、multi-tool、failure-recovery。
- 自动化测试全部离线、确定性、可复现，不访问网络、不依赖真实 API Key。

[Unreleased]: https://github.com/fufufu11/Self-ReAct/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/fufufu11/Self-ReAct/compare/v0.3.0...v1.0.0
[0.3.0]: https://github.com/fufufu11/Self-ReAct/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/fufufu11/Self-ReAct/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/fufufu11/Self-ReAct/releases/tag/v0.1.0
