# Day 27：真实日志场景化（R-08）

## 目标

把 R-07 日志/故障排查场景的合成 `logs.ndjson` 替换为网上可下载的真实日志数据，
并同步更新 runbook、发布记录、示例、测试与文档。

## 数据源选择与许可核实

- 选定 NASA Kennedy Space Center WWW 服务器访问日志（1995-07，Internet
  Traffic Archive）：真实、公共领域、含真实 HTTP 500 状态码。
- 页面声明 “The traces may be freely redistributed”，允许公开入库与再分发；
  规范化丢弃 host/IP 列规避 PII。
- 未选 LogHub：许可为“研究/学术使用”，公开入库再分发存在边界；未选云厂商
  示例：多为合成数据。

## 截取窗口与真实事件

- 窗口：`1995-07-03 09:00:00 ~ 11:59:59`（14,130 行，约 2.2 MB）。
- 真实事件：1995-07-03 10:49:40 ~ 10:52:29，`GET /cgi-bin/geturlstats.pl`
  连续返回 53 次 HTTP 500；窗口内 500 全部来自该接口。
- 窗口状态码分布：200（12,789）、302（465）、304（729）、404（94）、
  500（53）；cgi-bin 487 行（geturlstats.pl 82 行：200 29 / 500 53）。

## 改动清单

- `data/logs.ndjson`：合成 80 行 -> 真实 14,130 行；
- `data/runbook.ndjson`：RB-500/404/501，服务改为 cgi-bin/images；
- `data/deploys.ndjson`：cgi-bin geturlstats 1.1.0 @ 10:00、images 1.0.0 @ 09:00；
- `data/PROVENANCE.md`：来源/许可/隐私/规范化规则（新增）；
- `examples.py`：三个示例对齐真实事件；
- `tests/test_log_troubleshooting_scenario.py`：计数与稳定回答断言；
- `docs/adr/0002-real-log-fixture.md`、本文档、README 同步更新。

## 验证

- 场景相关测试：`pytest tests/test_log_troubleshooting_scenario.py
  tests/test_log_query.py tests/test_runbook_search.py` 43 通过。
- 三个场景示例 `self-react example log-*` 均以 `FINAL_ANSWER` 结束，工具观察
  全部成功，输出包含真实计数（487 / 14130、53 / 14130、10.9%）。
- `log_query` 单次查询约 30 ms，fixture 增到 2.2 MB 不影响全量测试速度。

## 真实 DeepSeek 手动验收（2026-08-14，deepseek-v4-flash）

1. `run "找出错误码 500 集中出现的时间窗口" --model deepseek --scenario
   log-troubleshooting --show-trace`：`FINAL_ANSWER`（4/5 步）。模型先按小时
   聚合定位到 10:00 桶，再精确查询确认 **1995-07-03 10:49:40 ~ 10:50:26**
   为最小集中窗口，回答正确。
2. `run "排查 cgi-bin 服务的 500 错误突增" --model deepseek --scenario
   log-troubleshooting --show-trace`（默认 5 步）：`MAX_STEPS_EXCEEDED`。模型
   依次调用 `log_query(level=ERROR)`、`runbook_search`、`log_query(group_by=hour)`
   等工具，但在预算内未给出最终回答。
3. 同上任务提高预算到 8 步重试：仍 `MAX_STEPS_EXCEEDED`，模型持续探索
   （含一次 `group_by=source` 参数无效的可恢复失败），未在预算内收尾。

结论：框架的 `max_steps` 硬预算在真实模型上正确生效并明确终止；聚焦任务可
完成，开放排查任务易耗尽预算，符合“真实调用结果非确定性、不作为自动化前置
条件”的既有约定。后续可考虑在提示词中引导模型尽早给出最终回答（属 R-06 或
提示词优化范畴，不在本 Issue 范围）。
