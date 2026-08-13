# Day 26：R-07 日志/故障排查助手场景

## 目标

把 Self-ReAct 的固定字典检索升级为一个真实用户故事：模型通过多步工具调用完成
“读日志 -> 过滤/统计 -> 检索 runbook -> 给根因假设与下一步动作”的排障流程。

## 设计决策（grilling 结论，详见 ADR）

1. 场景代码放 `src/self_react/scenarios/log_troubleshooting/`（包内子模块）。
2. 新增通用工具 `log_query` 与 `runbook_search`，保留 `retrieve` 不动。
3. 日志用固定 NDJSON 语料；`log_query` 支持过滤/计数/分页与 `group_by` 聚合。
4. runbook 结构化条目 + 拼接正文 BM25；自实现 Okapi BM25 + 中文字符 bigram。
5. CLI：`example` 增加三个场景示例名，`run --scenario log-troubleshooting` 切换工具包。
6. 三个示例：`log-5xx-spike`、`log-error-window`、`log-release-correlation`。
7. 决策落库到 `docs/adr/0001-r07-log-troubleshooting-design.md`。

## 验证

- 离线测试：`pytest` 555 通过 / 3 跳过（新增 43 个用例）。
- `ruff check src tests` 与 `ruff format --check src tests` 均通过。
- `hello` 与 Day 16 三条 `example` 输出不变（回归基准保持）。
- 三个场景示例 `self-react example log-*` 均以 `FINAL_ANSWER` 结束，工具观察全部成功。

## 真实 DeepSeek 手动验收

- `run "计算 2 + 2" --model deepseek --show-trace --stream`：`FINAL_ANSWER`，
  轨迹 `calculator -> 最终回答`，最终回答 `2 + 2 = 4`。
- `run "排查 checkout 服务的 5xx 错误突增，结合 runbook 给出根因假设与下一步动作"
  --model deepseek --scenario log-troubleshooting --show-trace --max-steps 8`：
  `FINAL_ANSWER`，模型依次调用 `log_query(group_by=error_code)`、
  `runbook_search`、`log_query(group_by=hour)`、按错误码过滤、`keyword=数据库`，
  最终输出结构化排查报告，主根因判断为“数据库连接池耗尽”，并给出回滚/扩容/限流
  等下一步动作。
