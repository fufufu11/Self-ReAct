# 用公共领域真实日志替换合成日志 fixture（R-08）

## Status

accepted（superseded by 0003）

## Context

R-07 的日志/故障排查场景使用手写合成 `logs.ndjson`（80 行、虚构服务
`auth/payment/checkout`）。用户要求把场景优化到“真实日志场景”：用网上可下载的
真实日志数据集替换合成数据，让演示与验收基于真实事件。

## Decision

- 数据源选 NASA Kennedy Space Center WWW 服务器访问日志（1995-07，
  Internet Traffic Archive）。原因：**真实**、页面明确声明
  “The traces may be freely redistributed”（可自由再分发，公共领域）、
  含真实 HTTP 5xx 状态码（500/501）、无业务敏感内容。
- 截取固定时间窗 `1995-07-03 09:00:00 ~ 11:59:59`（14,130 行，约 2.2 MB），
  该窗口含真实事件：`GET /cgi-bin/geturlstats.pl` 在 10:49-10:52 连续返回
  53 次 HTTP 500。
- 一次性转换脚本放 `tmp/`（未提交），提交的是固定 fixture；规范化丢弃 host/IP
  列以规避 PII，映射规则记录在场景内 `data/PROVENANCE.md`。
- runbook 与发布记录改为与真实错误码（500/404/501）和服务（cgi-bin/images）
  匹配的内容；发布记录明确标注为配合真实日志窗口构造的演示数据。
- 三个确定性示例的任务、工具序列与最终回答随真实数据更新；Day 16 三条既有
  `example` 与默认 `run` 行为不变。

## Considered Options

- LogHub（logpai）日志集：真实但许可为“供研究/学术使用”，公开入库再分发
  存在边界问题，且多为系统日志、缺少 HTTP 状态码维度，弃用。
- 云厂商示例日志（AWS/阿里等）：多为合成示例，不满足“真实日志”要求。
- 保留合成数据：离线确定但不符合用户“真实日志场景化”的指定目标。

## Consequences

- 场景演示基于真实历史事件，可复现、可核查来源与许可。
- fixture 从 80 行增至 14,130 行（约 2.2 MB），`log_query` 单次查询约 30 ms，
  全量 pytest 仍在秒级，可接受。
- 示例任务从“checkout”改为“cgi-bin（geturlstats.pl）”，README/架构导读/当日
  记录同步更新；Day 16 三条既有示例零回归。
- 真实 DeepSeek 验收结果非确定性：聚焦任务（定位错误窗口）可完成，开放排查
  任务在 5/8 步预算内可能步数耗尽，如实记录，不作为自动化前置条件。
