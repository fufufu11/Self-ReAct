# Day 18：测试与质量收尾（DeepSeek 适配器边界测试）

> Issue：[39 test: 补齐 DeepSeek 适配器边界测试（配置校验、工具定义与畸形响应）](https://github.com/fufufu11/Self-ReAct/issues/39)
>
> 本记录只描述 Day 18 做的一件事：复盘 Day 4 至 Day 17 的测试覆盖，找出
> 仍缺测试的关键分支，并只补齐其中一个可独立验收的目标——DeepSeek 适配器
> 的三类边界。全部新测试使用注入客户端，不访问网络、不依赖真实 API；
> 不修改任何生产代码。

## 今天理解了什么

Day 18 的主题是"测试与质量"：执行完整测试与 ruff 检查；补齐关键分支的
测试；确保网络依赖可被 Fake LLM 替代。前一句（Fake LLM 替代网络）在
Day 5/12/15/16 已经做到了——`Agent` 主循环与 CLI 的自动化测试全部使用
Fake LLM，DeepSeek 适配器的测试也早已注入替身客户端。真正剩下的工作，
是**逐模块复盘，找出"代码写了但没有任何用例验证"的分支**。

### 第一个认识：测试覆盖的"边界"是分支，不是数量

`tests/test_deepseek.py` 已经有 19 个用例，覆盖了消息序列化、工具调用
反序列化、SDK 错误映射、思考模式开关等。但逐行对照
[`src/self_react/deepseek.py`](../../src/self_react/deepseek.py) 后发现，
三类关键分支一个用例都没有：

1. **构造参数校验**：`DeepSeekLLM.__init__` 对空/空白 `model`、空/空白
   `base_url`、非正 `timeout`（含布尔值）的 `LLMConfigurationError`
   拒绝逻辑完全未测；
2. **工具定义序列化边界**：工具名重复、工具名非字符串或空白、`tools`
   不是序列时的 `LLMInputError` 拒绝逻辑未测；
3. **畸形供应商响应**：tool_call 缺 `id`、`type` 非 `function`、缺
   `function`、缺 `name`、`content` 非字符串、`tool_calls` 非序列、
   重复 `call_id` 等 `LLMResponseError` 分支未测。

这些分支不是新功能，而是适配器早就写好的防御代码。测试的意义是把它
们**钉成契约**：谁在未来把这些错误路径改成"悄悄放过"或"泄漏 SDK
文本"，测试就会立刻报警。

### 第二个认识：注入客户端让"真实适配器"与网络彻底解耦

`DeepSeekLLM(client=...)` 的注入边界（Day 6 就设计好了）在 Day 18 派上
大用场：

- 测构造配置校验时，传入替身客户端，`__init__` 在创建客户端之前就抛
  `LLMConfigurationError`，根本不读 `DEEPSEEK_API_KEY`；
- 测工具定义与响应转换时，替身客户端记录请求、返回固定响应，全程没有
  网络、没有密钥。

这正好回答了 Day 18 目标里的"确保网络依赖可被 Fake LLM 替代"：Fake LLM
替代的是 `Agent` 主循环里的模型；注入客户端替代的是适配器里的 SDK
客户端。两层各自离线可测，合起来整条链路都不碰网络。

### 第三个认识：测试是"契约"，不是"凑数"

Day 18 只补一个目标，范围小到可以用一句话复述："为 DeepSeek 适配器补齐
构造配置、工具定义与畸形响应三类边界测试。"每个新用例都对应一段**真实
存在但没被验证**的分支，而不是复制既有用例换几个数字。25 个新用例跑完
没有改一行生产代码，说明现有实现行为正确，缺的只是验证。

## 今天交付了什么

- 新增 [`tests/test_deepseek_boundaries.py`](../../tests/test_deepseek_boundaries.py)：
  25 个确定性用例，全部使用注入客户端，不访问网络、不依赖真实 API Key：
  - 构造配置：空/空白 `model`、空/空白 `base_url`、`timeout` 为 0、
    -1、布尔值、字符串共 8 个参数化用例，均抛 `LLMConfigurationError`；
  - 合法边界：正数浮点 `timeout` 可构造，注入客户端无需密钥；
  - 工具定义：重复工具名、工具名非字符串/空白（3 例）、`tools` 非序列
    均在发请求前抛 `LLMInputError`，且 `client.calls == []`；
  - 工具描述：非字符串描述回退稳定占位符"（无描述）"，不把对象写进请求；
  - 畸形响应：tool_call 缺 `id`、`type` 非 `function`、缺 `function`、
    缺 `name`（共 5 例）、`content` 非字符串、`tool_calls` 非序列、重复
    `call_id`（共 4 例）均抛 `LLMResponseError`；
  - 错误稳定性：边界错误消息不含 `Traceback`、`ValidationError`、
    `File`、`Line`，不泄漏 SDK 原始文本。
- 新增本记录与 [Day 18 代码导读](../architecture/day-18-testing-and-quality-code-walkthrough.md)。
- **没有修改任何生产代码**：`DeepSeekLLM`、`_serialize_tools`、
  `_deserialize_response` 等全部原封不动；`Agent`、`LLM.complete` 接口、
  提示词、解析器、领域模型、工具注册表与 Day 16 示例均未触碰。

## 设计边界与不变量

- **只补测试，不改行为**：本 Issue 零生产代码改动。若测试真的发现缺陷，
  会单独记录并拆一个新 Issue，而不是在同一 PR 里混入实现。
- **全部确定性**：新测试只使用注入客户端与纯字典/字符串响应，不访问
  网络、不读取环境变量、不依赖真实密钥。
- **不复制既有用例**：三类分支在 `test_deepseek.py` 与
  `test_tool_schemas.py` 中均未覆盖；新用例逐条对应未测分支，不是既有
  用例的变体。
- **错误稳定**：断言错误消息不泄漏异常类名、堆栈或 SDK 文本，与 Day 6
  的"调用方不依赖供应商异常文本"承诺一致。
- **回归基准**：Day 16 的 `self-react example` 三条命令输出不变；
  `Agent` 仍是唯一控制者，测试改动不复制或削弱任何边界。
- **不越界**：不实现持久化、暂停/恢复、流式、异步或并行工具调度。

## 遇到的问题与解决过程

### 问题一：测试类里 `name = name` 抛 `NameError`

写"工具名非法"的测试工具时，一开始在参数化函数里写：

```python
class BadNameTool:
    name = name  # NameError: name 'name' is not defined
```

类体不是闭包：一旦 `name` 被类体赋值，右边的 `name` 就只在类命名空间里
查找，找不到也不会回退到外层函数的参数。

解决：把参数改名为 `tool_name`，类体写 `name = tool_name`，避开自引用。

### 问题二：怎么证明新用例"不是凑数"

补齐前先把 `test_deepseek.py` 的参数化列表逐条抄出来对照，确认三类分支
（构造配置、工具定义、畸形响应的缺字段/类型错误）确实没有覆盖。这个对照
清单写进了 Issue 与学习记录，让"缺什么、补什么"可复述、可验收。

### 问题三：配置错误发生在构造阶段，无法断言"没有发出请求"

工具定义错误在 `complete` 里被拒绝，可以用 `client.calls == []` 断言
"请求没发出去"；但构造配置错误发生在 `__init__`，客户端还没创建，没有
调用记录可查。

解决：配置错误只断言 `pytest.raises(LLMConfigurationError)`；"不发请求"
的断言只用于 `complete` 阶段的输入错误路径，两处语义不同，不强行统一。

## 验收结果

以下命令已在 Windows、CPython 3.13.5 环境中实际执行：

```powershell
uv sync
uv run self-react hello
uv run self-react example single-tool
uv run self-react example multi-tool
uv run self-react example failure-recovery
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

- `uv sync`：成功，解析并检查依赖，锁文件无变化。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run self-react example single-tool|multi-tool|failure-recovery`：
  三条命令均以退出码 0 结束，输出结构与最终回答与 Day 16 记录一致。
- `uv run pytest`：成功，357 个测试通过、3 个跳过（符号链接用例，与
  Day 16/17 相同），相比 Day 17 新增 25 个
  （`tests/test_deepseek_boundaries.py`）。
- `uv run ruff check src tests` 与
  `uv run ruff format --check src tests`：全部通过。
- `git diff --check`：成功，无空白错误。
- 全仓库 `ruff check .` 与 `ruff format --check .` 的例外与 Day 17 记录
  一致：均来自开始前已存在且受保护的 `tmp/` 临时脚本与 Day 4/6 导读，
  没有修改、暂存或删除它们；Day 18 文件会在只包含仓库基线和本 Issue
  文件的干净副本中复验。

## 不在范围内

- 修改 `DeepSeekLLM` 或适配器任何生产代码（未发现缺陷）。
- 复盘时发现但未在本 Issue 处理的其它候选缺口：CLI `run` 运行期模型
  错误路径（退出码 3）、工具 Schema 与工具校验的一致性交叉测试。它们
  各自独立、范围可控，留作后续单独 Issue，避免一次塞进多个目标。
- 真实 DeepSeek API 调用、网络依赖或密钥相关测试。
- 持久化、暂停/恢复、流式、异步、并行工具调度。

## 明天要验证什么

- Day 19 文档与演示前，如时间允许，可补 CLI 运行期模型错误（退出码 3）
  与工具 Schema-校验一致性两个候选缺口，让"测试与质量收尾"更完整；
- 真实 DeepSeek 多轮调用仍可作为手动验收（不作自动化前置条件），观察
  Day 17 的结构化 Schema 是否让非法参数轮次下降；
- Day 16 三条示例继续作为回归基准，任何后续改动都不改变它们的输出。
