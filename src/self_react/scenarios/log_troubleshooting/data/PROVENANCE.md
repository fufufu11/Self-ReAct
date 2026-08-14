# 数据来源与许可说明（logs.ndjson）

`logs.ndjson` 是**真实 HTTP 服务器访问日志**的固定时间窗片段，不是合成数据。

## 数据源

- 数据集：NASA Kennedy Space Center WWW 服务器访问日志（1995-07 与 1995-08），
  由 Internet Traffic Archive 提供。
- 下载地址：<https://ita.ee.lbl.gov/html/contrib/NASA-HTTP.html>
- 原始文件：`NASA_access_log_Jul95.gz`（约 20.7 MB gzip / 205 MB 原始文本，
  含 1995-07-01 至 1995-07-31 共 1,891,715 行请求）。

## 许可与隐私

- 该档案页面声明 **"The traces may be freely redistributed"**（可自由再分发），
  属于公共领域数据，允许公开入库与再分发。
- 原始日志包含发起请求的主机名/IP 与完整请求；本 fixture 的规范化流程**丢弃了
  host/IP 列**，只保留时间戳、请求行与状态码，避免带入可能的个人标识信息。
- 1995 年数据为历史公开访问日志，请求内容为公共网页资源，无业务敏感信息。

## 截取窗口

- 时间窗：`1995-07-03 09:00:00 ~ 1995-07-03 11:59:59`（固定 3 小时）。
- 该窗口包含一个真实且清晰的 5xx 突增事件：**1995-07-03 10:49:40 ~ 10:52:29
  之间 `GET /cgi-bin/geturlstats.pl` 连续返回 53 次 HTTP 500**；窗口内 500
  全部来自该接口，其余时段 500 计数为 0。
- 窗口内共 14,130 行；状态码分布：200（12,789）、302（465）、304（729）、
  404（94）、500（53）。cgi-bin 服务共 487 行（geturlstats.pl 82 行，
  其中 200 为 29 行、500 为 53 行）。

## 规范化规则（原始行 -> NDJSON 字段）

固定字段：`timestamp / service / level / error_code / message`。

| 字段 | 规则 |
| --- | --- |
| `timestamp` | 原始 `01/Jul/1995:00:00:01 -0400` 转为 `1995-07-01 00:00:01` |
| `service` | 请求路径的第一个路径段（`/` 映射为 `root`），例如 `/cgi-bin/...` -> `cgi-bin` |
| `level` | 2xx/3xx -> `INFO`，4xx -> `WARN`，5xx -> `ERROR` |
| `error_code` | HTTP 状态码字符串（所有行保留真实状态码，如 `200`/`404`/`500`） |
| `message` | 原始请求行，例如 `GET /cgi-bin/geturlstats.pl HTTP/1.0` |

畸形行（无法解析出时间戳、请求或状态码的原始行）在转换时被跳过。

## 复现

一次性转换脚本在 `tmp/convert_nasa_logs.py`（未提交，仅本地复现用）：

```powershell
curl.exe -L -o tmp/NASA_access_log_Jul95.gz `
  "https://ita.ee.lbl.gov/traces/NASA_access_log_Jul95.gz"
python tmp/convert_nasa_logs.py
```

提交到仓库的只有固定 fixture（`logs.ndjson` 约 2.2 MB），运行时不下载任何数据。

## 配套 fixture 的边界

- `deploys.ndjson`（发布记录）与 `runbook.ndjson`（排障知识库）为配合真实日志
  窗口构造的演示数据：原始 NASA 日志不包含发布信息，发布记录按“cgi-bin 于
  1995-07-03 10:00 发布 geturlstats 1.1.0”与真实 500 起点（10:49）对齐构造，
  用于演示发布关联场景，不属于日志数据本身。
