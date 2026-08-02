# Day 5：LLM 抽象与 Fake LLM

## 今天理解了什么

LLM 模块的职责是把“根据一组消息获得下一条助手消息”变成可替换的调用接缝。
它消费 Day 4 已定义的 `Sequence[Message]`，返回一条 assistant `Message`，不需要
再创造同义的请求或响应领域对象。助手消息可以只包含普通内容，也可以携带
`ToolCall`；后者仍然只是模型表达的动作意图，不代表工具已经执行。

接口的类型签名不是全部契约。调用方还需要知道输入不能为空、适配器不能修改
输入、返回角色必须是 assistant，以及会出现哪些稳定错误。`Protocol` 可以让
Fake 和未来供应商适配器在不共享父类实现的情况下满足同一结构，但
`runtime_checkable` 只能检查方法是否存在，不能替代返回角色和错误语义测试。

Fake LLM 的价值不只是“返回固定字符串”。按顺序消费响应、记录每次合法调用的
输入快照、明确报告耗尽，并隔离调用方对可变 Pydantic 模型的后续修改，才能让
未来 Agent 测试准确验证上下文、调用次数和循环分支。

## 今天交付了什么

- 创建 GitHub Issue #9：`feat: 抽象 LLM 接口与 Fake LLM`。
- 新增 [`src/self_react/llm.py`](../../src/self_react/llm.py)，定义最小 `LLM`
  Protocol、稳定错误类型和确定性 `FakeLLM`。
- 新增 [`tests/test_llm.py`](../../tests/test_llm.py)，覆盖普通回答、工具调用响应、
  元组输入、响应顺序、调用历史、耗尽、非法输入和接口替换。
- 新增 [Day 5 LLM 模块代码导读](../architecture/day-05-llm-code-walkthrough.md)，
  说明模块职责、接口接缝、数据流、测试组织和 Day 6 供应商适配位置。
- 更新顶层包说明，使它与当前领域模型和 LLM 模块的组织方式一致。
- 没有修改 Day 3 的 `self-react hello` 行为，也没有修改 Day 4 领域模型。

## 接口与职责边界

公开接口只有一个方法：

```python
def complete(self, messages: Sequence[Message]) -> Message: ...
```

- 输入接受列表、元组等 `Sequence`，但必须至少有一条消息且所有元素都是
  已校验的 `Message`。
- 输出必须是 assistant `Message`。普通内容和携带 `ToolCall` 的消息共用同一
  返回类型，避免引入第二套“模型响应”概念。
- LLM 模块不执行 `ToolCall`，不构造 `ToolResult` 或 `Observation`，不修改
  `AgentState`，也不决定重试、步数或循环终止。
- `LLMInputError`、`LLMResponseError` 和 `LLMResponseExhaustedError` 提供稳定
  控制流；测试不依赖具体中文异常文本。

`FakeLLM` 在构造时复制预置响应，每次合法调用先复制并记录输入，再消费下一条
响应。即使响应已经耗尽，该次合法调用仍计入历史；非法输入则在记录和消费前
失败。`calls` 返回新的深拷贝，测试读取或修改历史时不会破坏 Fake 内部状态。

这些响应、游标和调用历史是测试适配器的运行时状态，不属于可持久化领域状态，
因此不会放进 `AgentState`。后续 Agent 只依赖 `LLM.complete`，不能读取 Fake 的
`responses`、`calls` 或 `call_count`。

## 遇到的问题与解决过程

### 问题一：是否需要新的 LLM 请求和响应模型

如果新增 `LLMRequest` 和 `LLMResponse`，它们只会包裹已有 `Message`，调用方需要
在等价类型之间来回转换，接口会比当前实现更浅。Day 4 的 `Message` 已能表达模型
上下文和带工具请求的助手响应，因此直接复用它们。

### 问题二：Fake 的历史应保存引用还是快照

`Message` 当前允许赋值。若 Fake 保存调用方对象的引用，调用完成后对原消息的修改
会重写“历史”；若直接返回预置对象，接收方修改响应也会污染后续断言。因此输入、
预置响应、返回值和公开历史都使用深拷贝，测试只观察调用发生时的事实。

### 问题三：全工作区质量检查受到既有改动影响

工作区开始时已经有未提交的 Day 4 导读和 `tmp/`。根目录执行 Ruff 时，这两处
受保护的既有文件会被一并扫描并报告格式问题。Day 5 没有修改、暂存或删除它们；
最终验收会在只包含仓库基线和本 Issue 文件的临时干净副本中执行相同命令，同时
保留工作区检查的真实结果。

## 验收结果

以下命令已在 Windows、CPython 3.13.5 环境中实际执行：

```powershell
uv sync
uv run self-react hello
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

先在原工作区执行完整命令：`uv sync` 成功解析并检查 13 个包；`hello` 输出
`Hello from Self-ReAct!`；pytest 收集 21 个测试并全部通过；`git diff --check`
成功。Ruff 的根目录检查如实发现两个与本 Issue 无关的既有问题：未跟踪
`tmp/day04_success_tool_call_demo.py` 的导入格式，以及未提交
`day-04-domain-model-code-walkthrough.md` 中 Python 示例的空格格式。按照交接约定，
本 Issue 没有修改这些文件。

随后从 `ec7c787` 创建临时干净仓库副本，只复制本 Issue 的 5 个变更文件，并再次
执行完全相同的六条命令：

- `uv sync`：成功创建隔离环境、解析 13 个包并安装当前项目。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：成功，21 个测试全部通过，其中 LLM 8 个、领域模型 12 个、
  CLI 1 个。
- `uv run ruff check .`：成功，输出 `All checks passed!`。
- `uv run ruff format --check .`：成功，确认 23 个文件均已格式化。
- `git diff --check`：成功，无空白错误。

临时副本在验证后已删除。这个结果验证的是仓库基线加 Day 5 Issue 文件，不包含
原工作区受保护的 Day 4 导读和 `tmp/`。

## 明天要验证什么

- 实现 DeepSeek 的 OpenAI 兼容适配器，并确保它结构化满足同一个 `LLM` 接口。
- 明确配置和 API Key 只从运行时环境进入适配器，不进入 `Message`、`AgentState`
  或日志。
- 用 mock HTTP 或注入客户端验证请求转换、assistant 响应转换和供应商错误映射，
  自动化测试不依赖真实网络和密钥。
- 提供单独的手动真实调用脚本，验证真实服务时不把密钥或响应中的敏感信息提交到
  仓库。
