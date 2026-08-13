# R-07 日志/故障排查场景代码导读（Day 26）

本导读说明 R-07 新增的两个通用工具、场景组装层、示例与 CLI 接线，以及固定
合成数据的设计。领域术语与主循环契约见 [CONTEXT](../../CONTEXT.md) 与
[react-loop](react-loop.md)，工具协议见 [工具注册表导读](day-07-tool-registry-code-walkthrough.md)。

## 1. 新增通用工具

### `log_query`（`src/self_react/tools/log_query.py`）

`LogQueryTool` 在构造时固定的根目录内读取 JSON Lines（NDJSON）日志，支持按
`service` / `level` / `error_code` 精确过滤、`keyword` 对 `message` 做大小写不敏感
子串过滤、`time_start` / `time_end` 闭区间时间过滤，以及 `group_by ∈
{error_code, service, level, hour}` 的简单聚合。返回稳定文本：非聚合返回
`匹配 N 条 / 共 M 条` + 命中行（`limit` 截断）；聚合返回按计数降序、键升序的
`key: count` 列表。

设计要点：

- `total` 始终是文件总行数，`matched` 是过滤后的行数；聚合时只统计该维度非空的
  条目，`matched_count` 等于被聚合的条目数，保证分组计数之和等于“匹配”数。
- 路径安全边界与 `file_reader` 一致：绝对路径、盘符、`..` 越界与 Windows 保留
  设备名在访问文件系统前被拒绝。
- 时间格式固定为 `YYYY-MM-DD HH:MM:SS`，用字符串字典序比较实现闭区间；命中行
  按 `(timestamp, service, message)` 排序并重新序列化，输出确定。

### `runbook_search`（`src/self_react/tools/runbook_search.py`）

`RunbookSearchTool` 对构造时注入的结构化 `RunbookEntry` 条目做 BM25 检索。每个
条目把 `title + error_code + service + causes + checks + actions` 拼接成检索正文；
tokenizer 把 ASCII/数字连续段、中文字符 bigram 与中文单字都切成 token。BM25 采用
标准 Okapi 参数 `k1=1.5`、`b=0.75`，分数相同时按条目 `id` 字典序兜底，保证
“相同查询相同结果”。

设计要点：

- `RunbookEntry` 是 Pydantic 模型（`extra="forbid"`），`id` 唯一性在构造索引时校验。
- 检索不引入 `rank_bm25`、`jieba` 或向量数据库，只依赖标准库，便于离线精确测试。
- 空结果返回稳定成功文本“命中 0 条”，而不是执行错误，让模型可以换说法重试或收尾。

## 2. 场景组装层（`src/self_react/scenarios/log_troubleshooting/`）

- `scenario.py`：`build_registry()` 组装五个工具
  `calculator / file_reader / log_query / runbook_search / final_answer`，
  `file_reader` 与 `log_query` 的根目录都指向包内 `data/`；`runbook_search`
  从 `runbook.ndjson` 读取条目。
- `examples.py`：三个确定性示例，只组合 `FakeLLM`、`Agent` 与场景注册表，
  `Agent` 仍是唯一循环控制器。
- `data/`：固定 fixture，见第 4 节。

## 3. 三个示例与 CLI 接线

三个场景示例分别覆盖一条排障主线：

- `log-5xx-spike`：读 checkout 总量 -> 过滤 500 -> 计算占比 -> 聚合错误码分布 ->
  检索 runbook -> 根因假设。
- `log-error-window`：`log_query(error_code=503, group_by=hour)` 一次定位峰值窗口。
- `log-release-correlation`：对比错误起点时间窗 + 读取 `deploys.ndjson` 发布记录，
  判断故障是否与发布相关。

CLI（`src/self_react/cli.py`）：

- `example` 的 `name` 选项合并 Day 16 三条与上述三条，按名称分发到
  `run_example` 或 `run_scenario_example`。
- `run` 新增可选 `--scenario log-troubleshooting`；不指定时仍用默认四工具，
  指定后调用 `build_registry()`，`Agent` 与主循环完全不变。

## 4. 固定合成数据

`data/logs.ndjson` 共 80 行：`checkout` 40 行、`payment` 20 行、`auth` 20 行；
错误码分布为 `503: 7`、`500: 5`、`502: 5`、`504: 3`。checkout 的 500/502 集中
在 `10:20` 之后（对应 `10:00` 发布），503 集中在 `11:20`，与示例叙述一致。
`data/runbook.ndjson` 有 4 条 runbook（RB-500/502/503/504）；
`data/deploys.ndjson` 有 2 条发布记录（auth 09:00、checkout 10:00）。

这些数字被离线测试锁定（见 `tests/test_log_troubleshooting_scenario.py`），
不包含真实业务敏感信息。
