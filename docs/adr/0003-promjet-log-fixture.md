# 用 promjet 2021-12 真实 Apache 日志替换 NASA fixture（R-09）

## Status

accepted（supersedes 0002）

## Context

R-08 使用 NASA Kennedy Space Center WWW 服务器访问日志（1995-07）作为真实
日志 fixture。该数据年代久远（1995 年单服务器 CGI 场景），与现代互联网站点
排障的形态差距大；用户要求换成更接近现代站点的真实流量与错误事件。

## Decision

- 数据源改为 promjet.ru 2021-12 真实 Apache 访问日志（GitHub
  `vberkutovv/ApacheLog-Dataset`，MIT；`LICENSE` 版权声明为
  "Copyright (c) 2019 InterSystems Developer Community"）。
- 截取固定时间窗 `2021-12-17 03:00:00 ~ 03:59:59`（931 行，约 190KB），该
  窗口含真实事件：03:14-03:18 五分钟 733 条 HTTP 404（整站备份/源码文件
  探测，多 IP 并发，疑似外部扫描）。
- 示例叙事从"5xx 突增（服务端故障）"改为"404 突增（外部扫描探测）"；
  场景示例 `log-5xx-spike` 更名为 `log-404-spike`；runbook 改为
  RB-404/RB-403/RB-503；`deploys.ndjson` 改为演示数据 jet 1.2.0
  （2021-12-16 22:00）。
- 规范化与 PII 规则沿用 R-08：丢弃 IP/UA/Referer，只保留时间戳、请求行与
  状态码，schema 不变（`timestamp / service / level / error_code / message`）。
- 一次性转换脚本放 `tmp/`（未提交），提交的是固定 fixture；
  `PROVENANCE.md` 记录来源/许可/规范化规则与配套 fixture 边界。

## Considered Options

- 保留 NASA 1995-07：许可最干净（公共领域）且含真实 5xx 服务端故障，但年代
  久远、形态单一（1995 单服务器 CGI），不符合"更接近现代站点"的目标。
- zanbil.ir 电商日志（Harvard Dataverse，CC0）：最像"互联网公司"，但本环境
  无法下载（Dataverse WAF + Kaggle 需登录 + 3.3GB），错误码分布未知。
- 印尼公共机构日志（Zenodo，MIT）：满足"近几年"，但几乎全部为单一匿名 IP
  的攻击扫描，500 仅 20 条分散，无可演示的突增事件。
- ClarkNet（Internet Traffic Archive，1995）：许可干净但同样过旧，仅作兜底。

## Consequences

- 场景演示基于 2021 年真实站点的真实事件，可复现、可核查来源与许可。
- promjet 数据无 500/501：演示主线从"服务端 5xx 故障"变为"客户端 404 突增 +
  外部扫描甄别"，runbook 移除 RB-500/RB-501。
- fixture 从 14,130 行（2.2MB）减至 931 行（约 190KB），`log_query` 更快，
  pytest 仍秒级。
- 隐私处理沿用 NASA 规则（丢弃 IP/UA/Referer），`PROVENANCE.md` 记录 MIT
  署名要求。
- 真实 DeepSeek 验收结果非确定性，如实记录，不作为自动化前置条件。
