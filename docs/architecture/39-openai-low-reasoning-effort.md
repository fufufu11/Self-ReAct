# 39：OpenAI 适配器低推理档与中转支持（项目收官）

> 这篇不写逐段代码导读，只简单说明这次改了什么、为什么改、效果如何。
> 本篇也是项目的收官改动：到此为止 Self-ReAct 停止新增功能，进入维护状态。

## 用大白话说

评估后端想从 DeepSeek 切到 ChatGPT 的 API（gpt-5.6，低推理档省钱）。
GPT-5 系列按推理消耗的 token 计费，`reasoning_effort=low` 能把这部分成本压下来；
另外大陆直连 OpenAI 通常要走中转，而中转地址属于个人配置、不该写进公开仓库。

所以给 `OpenAILLM` 适配器补了两个小能力：

1. **低推理档**：构造参数 `reasoning_effort`，默认 `"low"`（`None` 表示不传、
   用供应商默认档），经 `extra_body` 随每次请求下发——和 DeepSeek 适配器传
   思考模式开关是同一个通道，测试替身不需要改签名；
2. **中转支持**：`base_url` 未显式传入时读 `OPENAI_BASE_URL` 环境变量，
   解析顺序为「显式参数 > 环境变量 > 官方默认地址」，环境变量为空白时回退
   默认。key 和中转地址都只存机器级环境变量，不进任何入库文件。

## 改了什么

- `src/self_react/openai.py`：`DEFAULT_REASONING_EFFORT = "low"` 常量；
  `reasoning_effort` 构造参数（构造时校验 `low/medium/high/None`）；
  `base_url` 参数改为可缺省并支持 `OPENAI_BASE_URL` 环境变量；两条请求路径
  （`complete` / `complete_stream`）统一经 `_extra_body()` 下发推理档。
- `tests/test_openai.py`：+12 项（默认低档随请求发送、`None` 不发送、显式
  `medium` 覆盖、非法档位构造拒绝、base_url 环境变量读取/显式覆盖/缺失回退/
  空白回退、真实客户端路径消费两个环境变量）。
- `README.md` / `.env.example`：配置说明补 `OPENAI_BASE_URL` 与低推理档。
- `tmp/eval_driver_108.py`（不入库）：评估驱动 provider 从 `deepseek` 切到
  `openai`，默认输出改为 `tmp/eval_openai_baseline_results.jsonl`。

## 效果

- pytest **699 通过 / 3 跳过**（基线 687 + 12），ruff 全绿，
  `git diff --check` 通过，八个 example 与 `hello` 全部 exit 0。

## 真实链路验证：未完成（如实记录）

真实 gpt-5.6 验收没有做成：所用的 OpenAI 兼容中转站 chat 接口持续返回
`503 Service temporarily unavailable`（`/v1/models` 列表正常、key 有效、
gpt-5.6 及其变体共 5 个模型名 × 流式/非流式 × 3 轮重试全部 503），属于
中转服务侧故障，与代码无关。离线测试已完整覆盖请求构造的正确性
（`reasoning_effort` 经 `extra_body` 传递、`base_url` 三级解析），
若日后中转恢复，直接跑 `README`「运行」一节的 `--model openai` 命令即可复验。

## 项目收官说明

原交接文档的「任务 C：硬兜底修复后的 24 次同协议复测」（DeepSeek 口径）与
本次派生的「OpenAI 基线 24 次复测」均不再执行——项目目标（手搓 ReAct 框架 +
真实场景验证）已由 v1.0.0 达成，历史评估结论以 33~38 导读为准（最终任务
成功率 / 收敛率 95.8%，23/24）。此后仓库进入维护状态：不新增功能，安全与
依赖问题照常处理。
