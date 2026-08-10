# Day 24：短程会话记忆 / 上下文管理（R-04）

> Issue：[53 feat: 短程会话记忆 + 上下文管理（R-04）](https://github.com/fufufu11/Self-ReAct/issues/53)
>
> 这是 v0.2+ 迭代规划（`docs/project-roadmap.md`）Phase 2 的第一项工作：
> 让长任务在消息列表超过预算时自动压缩上下文，同时保持领域模型纯净。
> 方案在开工前经过逐项拷问确认：压缩只转换"发给模型的请求"
> （`AgentState.messages` 永远完整）、按整轮原子裁剪、规则式摘要回填
> （Claude auto-compact 风格）、CLI 默认 20,000 字符窗口。

## 今天理解了什么

### 第一个认识：会话记忆是"请求边界的横切组件"，不是领域状态

长任务溢出的本质是"发给模型的消息太多"，不是"状态记错了账"。因此压缩
可以完全发生在请求边界：`ContextPolicy.prepare(完整消息) -> 压缩请求`，
`AgentState` 继续保存完整历史。这样记忆组件无状态、纯函数，每次从完整
历史确定性重算，天然满足"记忆不进 `AgentState`"的约束，也避免"摘要消息
以什么角色写回状态"这一串连锁问题。成熟框架（LangChain 的 memory、
LangGraph 的独立节点）同样是"核心循环不写死记忆，记忆作为组件接入"。

### 第二个认识：裁剪的原子单位必须是"轮"，不是"条"

OpenAI 兼容 API 要求每条 tool 消息回指一条 assistant `tool_calls` 消息；
只删半轮（留下孤立工具结果，或删掉工具结果只留调用）会让 API 拒收请求。
所以裁剪以"整轮"为单位：一条 assistant 决策消息 + 紧随其后的工具结果
（或解析失败反馈）整体保留或整体移除，从旧到新裁，直到剩余轮次不超过
预算。宁可低于预算，也不拆对。

### 第三个认识：摘要必须"规则式、固定模板"，LLM 摘要在这里是负资产

LLM 摘要不可复现、要花 API、还让离线确定性测试失效。规则式摘要把每个
被裁轮次压成一行固定格式（工具名 + 参数 + 结果），行与总量都设上限，
超限用固定标记说明。相同历史永远得到相同摘要，测试可以直接断言字符串。
这也是 roadmap"摘要文本稳定可测（固定语料 + 固定摘要函数）"的落地。

### 第四个认识：默认"开"与"既有行为不变"可以同时成立

工业界（Claude auto-compact 等）默认开启、到阈值自动压缩。直接照搬
"默认开"会破坏既有 FakeLLM 断言；"默认关"又不符合工业界习惯。折中：
CLI 默认 20,000 字符窗口（开启），但该阈值远高于现有测试与 Day 16 示例
的真实消息规模（全库最长字符串字面量不足 2000 字符），因此既有 448 个
测试与三条示例的输出逐字节不变；只有真正超长任务才触发压缩。

## 今天交付了什么

- [`src/self_react/memory.py`](../../src/self_react/memory.py)（新增）：
  `ContextPolicy`（字符窗口 + 整轮原子裁剪 + 规则式摘要回填），纯函数、
  无状态；常量 `DEFAULT_CONTEXT_WINDOW = 20_000`、`SUMMARY_LINE_LIMIT`、
  `SUMMARY_TOTAL_LIMIT`；
- [`src/self_react/agent.py`](../../src/self_react/agent.py)：`Agent`
  构造新增可选 `context_policy`（默认 `None` 恒等），每轮请求模型前应用
  策略，终态消息保持完整；
- [`src/self_react/cli.py`](../../src/self_react/cli.py)：`run` 新增
  `--context-window`（正整数，默认 20,000），CLI 显式构造 `ContextPolicy`
  传入 Agent；`hello`/`example` 不触碰；
- [`tests/test_memory.py`](../../tests/test_memory.py)（新增，20 个用例）：
  窗口校验、恒等边界、整轮原子裁剪、system/任务保留、摘要模板与截断、
  总量上限、确定性、多工具轮、解析失败轮等；
- [`tests/test_agent.py`](../../tests/test_agent.py)（+3 个用例）：默认
  恒等、注入后请求被裁剪而终态完整、非法策略被拒；
- [`tests/test_cli.py`](../../tests/test_cli.py)（+3 个用例）：非法窗口
  拒绝、小窗口触发摘要、默认窗口下短任务请求不含摘要；
- 文档同步：[核心循环导读](../architecture/react-loop.md)（新增"上下文
  压缩"阶段与约束条目）、[Day 24 代码导读](../architecture/day-24-context-management-code-walkthrough.md)、
  README（特性/参数表/模块表）与本记录。

## 设计边界与不变量

- **请求边界**：`ContextPolicy.prepare` 只返回新的请求列表，不修改输入；
  `AgentState.messages` 始终完整，trace 与终态内容不受压缩影响；
- **整轮原子**：裁剪按"assistant + 其后 tool/user 反馈"成对进行，绝不
  拆开工具调用与结果；
- **保守保留**：system 提示词与首条 user 任务永远保留且不计入字符预算；
  最新一轮（触发超限的元凶）不参与裁剪；
- **确定性**：字符计数用 `json.dumps(sort_keys, 紧凑分隔符)`，摘要为固定
  模板 + 固定截断，相同输入永远得到相同请求；
- **摘要纪律**：单行上限 200 字符、总量上限 1000 字符、超限固定标记
  `（其余历史已省略）`；解析失败轮用稳定描述，不泄漏模型原始输出；
- **默认不破坏既有行为**：Agent 层 `context_policy=None` 恒等；CLI 默认
  20,000 字符，现有测试与示例远低于阈值，请求逐字节不变；
- **领域模型纯净**：`AgentState`（`extra="forbid"`）零新增字段，记忆状态
  不在状态里。

## 遇到的问题与解决过程

### 问题一：PowerShell 把多行 Issue 正文拆成多个参数

`gh issue create --body $body` 在 PowerShell 5.1 下把含双引号与换行的
正文拆散，报 `unknown arguments ["2" "+" ...]`。改为先把正文写入临时文件
（UTF-8 无 BOM），再用 `--body-file` 传入，问题解决。

### 问题二："多工具调用轮"测试构造错误

最初把两个 `ToolCall` 写成了两条 assistant 消息，导致轮次切分把"同一轮"
拆成两轮；且领域模型要求同一条 assistant 消息内 `call_id` 唯一。改为
一条 assistant 消息携带两个 `ToolCall` + 两条 tool 消息，既符合模型校验，
也真实还原供应商一次返回多个 `tool_calls` 的形态。

### 问题三：极小小窗口下真实模型反复试探工具

用 `--context-window 120` 做真实 API 压力测试时，模型每轮都继续请求工具，
5 步预算耗尽（`MAX_STEPS_EXCEEDED`）。根因是压缩过狠：模型只看到摘要与
任务，丢失细节后不断重复试探。这是极端配置的已知边界，不是默认行为问题
（默认 20,000 与 300 窗口均正常收敛）；文档如实记录，不粉饰。

## 真实 DeepSeek 手动验收（2026-08-10）

自动化测试全部离线；以下为真实 API 人工验收记录，不作为自动化前置条件。

1. **默认窗口**：`uv run self-react run "计算 2 + 2" --model deepseek --show-trace`
   → 3 步，最终回答 `2 + 2 = 4`。第 2 步 DeepSeek 以相同参数重复调用
   calculator，被 `REPEATED_ACTION` 拦截并回写观察，第 3 步给出最终回答；
2. **小窗口触发压缩**：`uv run self-react run "计算 2 + 2 并检索 react 是什么" --model deepseek --show-trace --context-window 300`
   → 3 步，最终回答同时包含计算与 ReAct 解释；第二轮后请求超限触发裁剪，
   带摘要的压缩请求被 DeepSeek 接受，任务正常完成；
3. **请求级验证**（临时脚本 `tmp/r04_diag_window_summary.py`，不交付）：
   包装 DeepSeekLLM 打印每轮实际请求，窗口 120 时第 2~5 轮请求均为
   `[system, system, user]` 且含摘要——证明裁剪确实发生在请求边界、终态
   消息保持完整、压缩请求被 API 接受；该窗口下 5 步内未收敛，记录为
   极端配置边界。

## 明天要验证什么

- 合并 PR 后同步本地 main，重写交接文档并保持未提交；
- M2 剩余工作项 R-05（流式输出）；M1 的 v0.2.0 打标签发布仍为待定事项，
  默认不在后续工作项内执行。
