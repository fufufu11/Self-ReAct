# Day 17：对照优秀实现（LangChain/LangGraph 的工具定义与状态处理）

> Issue：[37 feat: 对照 LangChain/LangGraph 为工具补充结构化参数 Schema](https://github.com/fufufu11/Self-ReAct/issues/37)
>
> 本记录只描述 Day 17 吸收的一个改进：为每个工具声明结构化参数 JSON
> Schema，并让 DeepSeek 适配器随工具定义下发给模型。调研范围是
> LangChain/LangGraph 官方文档与源码中与本项目直接相关的部分（工具定义、
> 参数 Schema、工具错误处理与状态表示）；持久化、暂停/恢复、流式、异步和
> 并行调度仍不属于本期。

## 今天理解了什么

Day 16 交付了三个可复现的端到端示例，但真实的 DeepSeek 多轮调用还有一个
肉眼可见的浪费：**模型经常因为参数形状猜错而触发 `INVALID_ARGUMENTS`
失败轮次**。Day 17 的任务不是拍脑袋加功能，而是先读优秀实现，再只吸收
一个有明确价值的改进。

### 第一个认识：LangChain 把"工具"定义为三件套

LangChain 官方文档对工具的定义是：**"工具是有明确定义输入与输出的可调用
函数，会被传给聊天模型"**。一个工具最少包含三样东西：名称（name）、描述
（description）和**输入参数的 JSON Schema**。[LangChain Tools 官方文档](https://docs.langchain.com/oss/python/langchain/tools)

JSON Schema（读作"杰森 斯基玛"）是一种用 JSON 描述"数据长什么样"的通用
标准：字段叫什么、是什么类型、哪些必填、允不允许多余字段。LangChain 的
`@tool` 装饰器会**从函数的类型标注自动生成这个 Schema**（`query: str` 就
生成一个字符串字段），也可以显式传 `args_schema` 给更复杂的输入。[LangChain Tools 官方文档](https://docs.langchain.com/oss/python/langchain/tools)

对照本项目：Day 6b 的 `_serialize_tools` 发送给 DeepSeek 的工具定义里，
参数形状是**写死的宽松对象** `{"type": "object", "properties": {}}`。
模型只知道"这个工具要一个对象参数"，却不知道对象里到底要 `expression`
还是 `query`，只能靠 description 里的文字猜。猜错了就产生一次
`INVALID_ARGUMENTS`，白白消耗一轮步数。

### 第二个认识：LangGraph 的 ToolNode 在"调用前"和"调用后"各有一道关

LangGraph 预构建智能体的工具节点（ToolNode）源码展示了两个与本项目高度
相关的边界：

1. **调用前校验**：`ToolNode` 用 `tools_by_name`（名称到工具的字典）查找
   工具，查不到就返回一条 `status="error"` 的 ToolMessage，内容包含被
   请求的工具名和所有可用工具名；工具自身的参数 Schema（Pydantic 模型）
   会在 `tool.invoke` 时校验参数，`ValidationError` 被转换成稳定的
   `ToolInvocationError`。[LangGraph ToolNode 源码](https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/tool_node.py)
2. **调用后处理**：执行异常通过 `handle_tool_errors` 配置统一转成带
   `status="error"`、`name` 和 `tool_call_id` 的 ToolMessage，写回消息
   列表让模型继续。[LangGraph ToolNode 源码](https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/tool_node.py)

对照本项目：Day 7 注册表已经做了"调用前查名"（未知工具返回带可用清单的
`UNKNOWN_TOOL` 失败），Day 12/14 已经把失败统一转成带错误码与
`retryable` 的 Observation 回写。**这些边界我们都已经有了**，不需要再
抄一遍。真正缺的是"调用前给模型足够准确的参数形状"，也就是第一个认识
里的 JSON Schema。

### 第三个认识：只吸收一个改进，且要能讲清"借鉴了什么、代价是什么"

对比结论：

| 维度 | LangChain/LangGraph | Self-ReAct（Day 17 前） | Day 17 吸收 |
| --- | --- | --- | --- |
| 工具定义 | name + description + 输入 JSON Schema | name + description + 宽松空对象 | 补上输入 JSON Schema |
| 参数校验 | 工具 Schema 在调用前校验 | 工具自己在 `execute` 里校验 | 不重复校验（工具边界已有） |
| 工具失败 | 错误 ToolMessage（status/name/call_id） | ToolResult/Observation（code/retryable） | 不吸收（已有等价边界） |
| 状态表示 | 消息列表作为图状态 | `AgentState.messages` + trace | 不吸收（Day 4 已有） |

值得吸收的是**结构化参数 Schema**：它是"模型 -> 工具"这段接口的精度问题，
不是新功能。代价很明确：每个工具作者要多维护一份 `parameters` 声明，而且
声明必须与工具实际校验行为保持一致，否则会误导模型。本 Issue 用
`additionalProperties: False` 对齐工具"拒绝多余参数"的真实行为，并用
测试钉死四份 Schema，防止文档与代码分叉。

## 今天交付了什么

- [`src/self_react/tools/base.py`](../../src/self_react/tools/base.py)：
  - 新增公开常量 `DEFAULT_PARAMETERS_SCHEMA = {"type": "object",
    "properties": {}}`，作为工具未声明 schema 时的宽松回退，与 Day 6b
    的既有行为完全一致；
  - `Tool` 协议文档说明可选的 `parameters` 约定（JSON Schema 对象），并
    明确它**不是协议必需成员**：协议仍只要求 `name`、`description`、
    `execute`，适配器用 `getattr` 读取，不声明 schema 的简单工具照常可用。
- 四个业务工具各自声明 `parameters` 类属性：
  - [`calculator.py`](../../src/self_react/tools/calculator.py)：必填
    字符串 `expression`；
  - [`file_reader.py`](../../src/self_react/tools/file_reader.py)：必填
    字符串 `path`；
  - [`retrieve.py`](../../src/self_react/tools/retrieve.py)：必填字符串
    `query`；
  - [`final_answer.py`](../../src/self_react/tools/final_answer.py)：必填
    字符串 `content`；
  - 四份 Schema 都带参数中文描述并设置 `additionalProperties: False`，
    与工具"拒绝未声明参数"的实际行为一致。
- [`src/self_react/tools/__init__.py`](../../src/self_react/tools/__init__.py)：
  重导出 `DEFAULT_PARAMETERS_SCHEMA`。
- [`src/self_react/deepseek.py`](../../src/self_react/deepseek.py)：
  - 新增 `_tool_parameters(tool, name)`：读取工具声明的 `parameters`；
    未声明回退宽松对象；声明非法（非对象或不可 JSON 序列化）时抛
    `LLMInputError`，不发供应商请求；
  - `_serialize_tools` 使用 `_tool_parameters`，把结构化 Schema 放进
    function 定义下发给模型。
- 新增 [`tests/test_tool_schemas.py`](../../tests/test_tool_schemas.py)：
  10 个用例，全部使用注入客户端与真实工具类，不访问网络、不依赖真实 API：
  - 默认回退 Schema 形状；
  - 四个工具的参数 Schema（类型、必填项、`additionalProperties`、可
    JSON 序列化）；
  - 每个参数的 `type` 与中文 `description` 非空；
  - 适配器把声明的 Schema 原样放进 function 定义；
  - 未声明工具回退宽松对象；
  - 非法 Schema（非对象、不可序列化）在发请求前被拒绝。
- 新增本记录与 [Day 17 代码导读](../architecture/day-17-compare-excellent-implementations-code-walkthrough.md)。
- 没有修改 `Agent` 主循环、`LLM.complete` 接口、提示词、解析器、领域模型
  或 `Tool` 协议必需成员；`run --model fake` 与 `self-react example`
  的既有演示行为不变（Fake LLM 不消费工具定义）。

## 设计边界与不变量

- **只动"模型看到的工具定义"**：改动集中在工具层声明与 DeepSeek 适配器
  序列化，`Agent`、注册表执行路径、提示词与领域模型零改动。
- **向后兼容**：`parameters` 是可选约定，不在 `Tool` 协议必需成员里；
  未声明的工具（包括测试里的 `FakeTool`、`IndependentTool`）回退宽松
  对象，既有行为不变。
- **声明与行为一致**：四份 Schema 的必填字段与 `additionalProperties:
  False` 和工具实际校验一致（工具会拒绝多余键），由测试钉死。
- **非法声明在边界拒绝**：`parameters` 非 JSON 对象或不可 JSON 序列化时，
  适配器抛 `LLMInputError`，绝不把坏 Schema 悄悄发给供应商。
- **不复制校验逻辑**：Schema 只用于指导模型生成参数，不在注册表或 Agent
  再做一遍参数校验——工具自身已在 `execute` 校验（Day 7 边界），避免两处
  规则分叉。
- **确定性**：所有新测试使用注入客户端与确定性工具类，不访问网络、不依赖
  真实 API；Day 16 的三条示例命令输出保持为回归基准。
- **不越界**：不实现持久化、暂停/恢复、流式、异步或并行调度。

## 遇到的问题与解决过程

### 问题一：把 `parameters` 加进 `Tool` 协议会不会破坏已有工具

最初想把 `parameters` 直接写进 `Tool` 协议成员，但协议是
`runtime_checkable`（运行时检查），`isinstance(tool, Tool)` 会要求对象
必须有这个属性，测试里的 `FakeTool`、`IndependentTool` 等不声明 schema
的简单工具会全部注册失败。

解决：`parameters` 只作为**可选约定**写进协议文档，协议成员保持
`name`/`description`/`execute` 不变；适配器用 `getattr(tool, "parameters",
None)` 读取。这样新工具可以声明，老工具不用改，协议检查也不受影响。

### 问题二：宽松回退放在哪里才不会让"回退"散落多处

Day 6b 的 `_serialize_tools` 里内联了 `{"type": "object", "properties":
{}}`。如果只在适配器里定义一个私有常量，测试和工具层都看不到它，约定就
只有一处能读。

解决：把回退常量 `DEFAULT_PARAMETERS_SCHEMA` 放在工具层
`tools/base.py`（协议与约定的家），并在 `tools/__init__.py` 重导出；
适配器导入它并做浅拷贝 `dict(...)` 返回，避免调用方拿到后意外修改共享
常量。

### 问题三：schema 声明会不会与工具校验"各说一套"

如果 Schema 说"允许额外字段"而工具实际拒绝，模型会被误导；反之模型会
漏传字段。两者都不好。

解决：四份 Schema 的必填字段与 `additionalProperties: False` 直接对应
各工具 `_extract_*` 的"拒绝多余键、必填单参数"行为，并在测试里逐项断言
Schema 形状。若未来工具增加参数，测试会提醒同步更新 Schema。

## 验收结果

以下命令已在 Windows、CPython 3.13.5 环境中实际执行：

```powershell
uv sync
uv run self-react hello
uv run self-react example single-tool
uv run self-react example multi-tool
uv run self-react example failure-recovery
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

- `uv sync`：成功，解析并检查 24 个包，锁文件无变化。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run self-react example single-tool|multi-tool|failure-recovery`：
  三条命令均以退出码 0 结束，输出结构与最终回答与 Day 16 记录一致
  （单工具两步、多工具三步、失败恢复三步）。
- `uv run pytest`：成功，332 个测试通过、3 个跳过（符号链接用例，与
  Day 16 相同），相比 Day 16 新增 10 个（`tests/test_tool_schemas.py`）。
- `uv run ruff check .`：根目录 5 个失败全部来自受保护的 `tmp/`（与
  Day 16 记录一致：`day04_success_tool_call_demo.py` 导入排序、
  `fix_pr_encoding.py` 超长行、`push_via_api.py` 未用导入与超长行、
  `rebuild_remote_branch.py` 未用导入）；`src/` 与 `tests/` 单独检查
  通过（`All checks passed!`）。
- `uv run ruff format --check .`：11 个文件需要格式化，全部来自受保护的
  Day 4/6 导读与 `tmp/` 临时脚本；`src/` 与 `tests/` 单独检查通过
  （30 个文件均已格式化）。
- `git diff --check`：成功，无空白错误。

与 Day 6 至 Day 16 一致，全仓库检查的 Ruff 例外均来自开始前已存在且
明确受保护的文件，没有修改、暂存或删除它们。Day 17 文件还会在只包含
仓库基线和本 Issue 文件的干净副本中复验。

## 不在范围内

- 用 Schema 在注册表或 Agent 层再做一遍参数校验（工具已自行校验）。
- 修改 `Agent` 主循环、`LLM.complete` 接口、提示词、解析器、领域模型或
  `Tool` 协议必需成员。
- 持久化、暂停/恢复、流式、异步或并行工具调度。
- 为工具自动生成 Schema（LangChain 用类型标注自动生成，本项目四个工具
  手写声明即可，暂不引入反射机制）。

## 明天要验证什么

- Day 18 测试与质量收尾：跑一次真实 DeepSeek 多轮任务，观察结构化 Schema
  是否让模型生成非法参数的次数下降（真实行为不作自动化前置条件）。
- 确认 Day 16 三条示例继续作为回归基准，任何核心模块改动都不改变它们的
  输出。
