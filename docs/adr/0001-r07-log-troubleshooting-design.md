# R-07 日志/故障排查场景的架构决策

在已有 Self-ReAct 框架上落地日志/故障排查助手场景（R-07）。本次决定：场景代码放在包内子模块 `src/self_react/scenarios/log_troubleshooting/`，而不是顶层 `apps/` 或独立包；新增两个确定性通用工具 `log_query`（NDJSON 过滤/聚合）与 `runbook_search`（自实现 BM25 + 中文字符 bigram，不引入向量库或检索依赖）；日志与 runbook 用固定 NDJSON 语料保证离线可复现。CLI 通过 `example` 新增三个场景示例名，以及 `run --scenario log-troubleshooting` 切换场景工具包；默认 `run` 与 Day 16 三条 `example` 行为不变。

## Status

accepted

## Considered Options

- 场景放 `apps/log-troubleshooting/`：框架/应用边界更显式，但需要为单一场景引入新的打包与入口布局，改动面过大。
- 检索引入 `rank_bm25` / `jieba`：更贴近业界，但为固定小语料增加运行时依赖，而自实现可以逐字节离线断言、确定排序。
- 让模型直接靠 `file_reader` 加自身推理统计日志：不新增工具，但 LLM 计数/聚合不可靠，无法满足“相同输入相同输出”的确定性验收。

## Consequences

- 场景数据随包内 `data/` 目录发布；`log_query` 与 `runbook_search` 是通用框架工具，场景子模块只放数据、组装与示例。
- `run` 不带 `--scenario` 时注册表与 Day 16 行为完全不变，三条既有 `example` 仍是回归基准。
- 本决策解决了 roadmap §9 的“场景代码组织”待定项；受保护的 `docs/project-roadmap.md` 不在本次更新范围内。
