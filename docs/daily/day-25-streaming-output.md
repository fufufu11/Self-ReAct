# Day 25：流式输出与中间过程展示（R-05）

## 今天理解了什么

- OpenAI 兼容 Chat Completions 的流式语义：`stream=True` 时响应变成
  `choices[0].delta` 的增量序列，`content` 按 token 切块、工具调用参数
  按 `index` 跨块拼接；DeepSeek 还会在思考模式下发 `reasoning_content`。
- "流式"和"结果等价"可以分离：协议层产出增量，`collect_stream` 统一组装，
  上层完全不知道底层是不是真流式。
- OpenAI 兼容 API 的历史约束：`tool` 角色消息必须紧跟带原生 `tool_calls`
  的 assistant 消息；文本 JSON 形式的工具调用在写回历史时必须归一化成
  原生 `tool_calls`，否则下一轮请求会被 400 拒绝。

## 今天交付了什么

- `LLM` 协议新增 `complete_stream`（`StreamChunk` 增量迭代器）与
  `collect_stream` 组装函数；`FakeLLM.complete_stream` 按固定 8 字符切块，
  确定性可断言。
- `deepseek.py` / `openai.py` 真流式：`stream=True` + `StreamAccumulator`
  增量组装 + 创建/中途错误的稳定映射；DeepSeek 思考模式增量被安全忽略。
- `Agent.run` 新增可选 `stream` / `on_chunk` / `on_step`，默认路径零变化；
  文本工具调用补原生 `tool_calls` 写回历史（真实 API 兼容性修复）。
- `trace.py` 公开 `render_step`，CLI `--stream` 逐步即时打印，可与
  `--show-trace` 共存。
- 全量 pytest 508 通过 / 3 跳过；ruff 检查与格式检查通过；Day 16 三条
  `example` 输出不变；`git diff --check` 干净。
- README 参数表/运行示例/流式验收记录、架构导读、本文档同步。

## 遇到的问题与解决过程

1. **流式首轮解析失败**：真实 DeepSeek 在流式下偶尔在 JSON 前输出英文散文
   （"This is a simple arithmetic calculation..."），`parse_decision` 按
   既有规则拒绝。R-02 的有界重试原样兜住，第二轮给出干净 JSON，无需改解析器。
2. **流式第三轮 BAD_REQUEST**：模型用 JSON 文本发出工具调用后，下一轮请求
   带 `tool` 消息，被 DeepSeek 拒绝：
   `Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`。
   用 `tmp/diag_r05_stream_chunks.py` 拿到原始错误体后确认是历史格式问题；
   修复为解析出 ToolCall 时把 assistant 消息补成原生 `tool_calls` 再写回
   历史。该修复对流式与非流式同样生效。
3. **OpenAI 验收**：`OPENAI_API_KEY` 无效，流式请求返回
   `AUTHENTICATION`，按交接约定如实记录，留待有效密钥。

## 真实 DeepSeek 手动验收（2026-08-11）

`uv run self-react run "计算 2 + 2" --model deepseek --show-trace --stream`

结果：calculator -> 观察 4 -> 最终回答，2 / 5 步，`FINAL_ANSWER`，
退出码 0，`最终回答：2 + 2 = 4`。

`uv run self-react run "计算 2 + 2，并检索 react 主题" --model deepseek --show-trace --stream`

结果：calculator -> retrieve -> 最终回答，3 / 5 步，`FINAL_ANSWER`，
退出码 0，汇总计算与 ReAct 说明。

两次任务中模型均使用原生 `tool_calls`（`call_00_...`），流式增量经
`StreamAccumulator` 正常组装；归一化修复针对的是模型偶尔走 JSON 文本的
分支，已由离线用例覆盖。

## 干净副本复验（2026-08-11）

从远端 main（`75d4b46`）用 `git worktree add` 建临时工作树，只复制本任务
涉及的 17 个文件后执行：

- `uv sync` 成功；`uv run pytest` 508 通过 / 3 跳过；
- `uv run ruff check .` 全绿；`ruff format --check .` 仅剩
  `docs/architecture/day-24-context-management-code-walkthrough.md` 的
  存量基线格式差异（远端 main 自带，与本次改动无关，未触碰）；
- 复验结束后删除临时工作树，`git worktree list` 只剩主工作区。

## 明天要验证什么

- 提交 PR（`Closes #55`）、合并后同步本地 main；
- 按真实结果重写 `docs/handoff/next-session.md`（保持未跟踪）；
- M2（v0.3.0）打标签发布作为待定事项留给后续对话。
