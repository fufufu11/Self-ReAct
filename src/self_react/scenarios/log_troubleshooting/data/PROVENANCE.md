# 数据来源与许可说明（logs.ndjson）

`logs.ndjson` 是**真实 HTTP 服务器访问日志**的固定时间窗片段，不是合成数据。

## 数据源

- 数据集：promjet.ru 网站 2021-12 月 Apache 访问日志，来自 GitHub 仓库
  `vberkutovv/ApacheLog-Dataset`。
- 仓库地址：<https://github.com/vberkutovv/ApacheLog-Dataset>
- 原始文件：`src/data/promjetDec2021.log`（约 38.4 MB，137,510 行，
  2021-11-30 ~ 2021-12-31，时区 +0300，Apache combined 格式）。
- 本地副本：`tmp/promjetDec2021.log`（未提交，仅复现用）；下载命令：

  ```powershell
  curl.exe -L -o tmp/promjetDec2021.log `
    "https://raw.githubusercontent.com/vberkutovv/ApacheLog-Dataset/main/src/data/promjetDec2021.log"
  ```

## 许可与隐私

- 仓库以 **MIT 许可**发布；`LICENSE` 版权声明为
  **"Copyright (c) 2019 InterSystems Developer Community"**。再分发需保留
  该版权声明与本许可说明。
- 原始日志包含真实访客 IP、User-Agent 与 Referer（含 UptimeRobot 等监控
  探测与真实访客标识）；本 fixture 的规范化流程**丢弃了 IP、UA、Referer 列**，
  只保留时间戳、请求行与状态码，避免带入个人标识信息。

## PII 扫描说明（roadmap 10.7）

对原始整月文件（`tmp/promjetDec2021.log`，137,510 行）做 PII 模式正则扫描，
结果如下（2026-08-19 实测；模式口径与 `tests/test_fixture_pii.py` 一致）：

| 模式 | 匹配数 | 说明 |
| --- | --- | --- |
| 邮箱 | 41 | 6 个去重值，全部来自 User-Agent 列中的爬虫/扫描器标识（`scaninfo@expanseinc.com`、`+info@netcraft.com`、`bot@linkfluence.com`、`crawler@mixrank.com`、`help@moz.com`、`cargo@kontur.ru`），**非个人邮箱** |
| `mailto:` URL | 0 | promjet 原始数据无 mailto 链接（roadmap 10.7 原文基于旧 NASA fixture 的 618 处邮箱不适用于现状） |
| SSN（`\d{3}-\d{2}-\d{4}`） | 0 | — |
| 北美电话格式（`\d{3}-\d{3}-\d{4}`） | 3 | 全部为产品型号编码（`.../Вентиль-ВК-97-Джет-000-230-0001.jpg`，阀门商品编号），**非电话号码** |

上述内容所在列（IP / User-Agent / Referer）在规范化时全部丢弃，仅时间戳、
请求行与状态码进入 fixture；截取窗口（2021-12-17 03:00-03:59）内亦无上述
匹配。入库 fixture 的零 PII 由守卫测试（`tests/test_fixture_pii.py`）持续保证。

## 截取窗口

- 时间窗：`2021-12-17 03:00:00 ~ 2021-12-17 03:59:59`（固定 1 小时）。
- 该窗口包含一个真实且清晰的错误突增事件：**2021-12-17 03:14 ~ 03:18
  五分钟内 733 条 HTTP 404**，内容为整站备份/源码文件探测
  （`/promjet.ru.sql`、`/backup/root.rar`、`/tmp/root.tar.gz` 等 724 个不同
  路径），来源为多 IP 并发扫描，疑似外部扫描而非应用故障。
- 窗口内共 931 行；状态码分布：200（194）、304（1）、404（736）。

## 规范化规则（原始行 -> NDJSON 字段）

固定字段：`timestamp / service / level / error_code / message`。

| 字段 | 规则 |
| --- | --- |
| `timestamp` | 原始 `[30/Nov/2021:15:08:14 +0300]` 转为 `2021-11-30 15:08:14`（去时区） |
| `service` | 请求路径的第一个路径段（`/` 映射为 `root`），例如 `/jet/...` -> `jet` |
| `level` | 2xx/3xx -> `INFO`，4xx -> `WARN`，5xx -> `ERROR` |
| `error_code` | HTTP 状态码字符串（所有行保留真实状态码，如 `200`/`304`/`404`） |
| `message` | 原始请求行，例如 `GET /promjet.ru.sql HTTP/1.1` |

畸形行（无法解析出时间戳、请求或状态码的原始行）在转换时被跳过；本窗口内
无畸形行。

## 复现

一次性转换脚本在 `tmp/convert_promjet_logs.py`（未提交，仅本地复现用）：

```powershell
python tmp/convert_promjet_logs.py
```

提交到仓库的只有固定 fixture（`logs.ndjson` 约 190KB），运行时不下载任何数据。

## 配套 fixture 的边界

- `deploys.ndjson`（发布记录）与 `runbook.ndjson`（排障知识库）为配合真实日志
  窗口构造的**演示数据**：原始 promjet 日志不包含发布信息，发布记录按
  “jet 于 2021-12-16 22:00 发布 1.2.0”构造，用于演示发布关联场景
  （结论：与发布无关、疑似外部扫描），不属于日志数据本身。
- runbook 覆盖 404/403/503 三种错误码；其中 403/503 在所选窗口内不出现，
  仅作为诊断知识库条目存在。
