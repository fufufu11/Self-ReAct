# Day 23：工具 Schema 自动生成 + 注册表 Schema 预校验（R-03）

> Issue：[51 feat: 工具 Schema 自动生成 + 注册表 Schema 预校验（R-03）](https://github.com/fufufu11/Self-ReAct/issues/51)
>
> 这是 v0.2+ 迭代规划（`docs/project-roadmap.md`）Phase 1 的第三项工作：
> 把工具参数从"手写字典"升级为"声明即校验"——从 Pydantic 参数模型或函数
> 签名自动生成 JSON Schema，并在注册表分派前按 Schema 预校验参数。改动
> 集中在工具层（`tools/schema.py` 新增、`tools/base.py` 与四个业务工具
> 修改），不触碰 `LLM.complete` 接口、适配器、领域模型、解析器与主循环。

## 今天理解了什么

### 第一个认识：Schema 是"翻译产物"，声明才是唯一事实来源

Day 17 的工具自述参数是手写 `parameters` 字典，适配器把它下发给模型。
手写的问题不是"会写错"，而是**两处事实**：Schema 一份、工具业务校验一份，
改参数时容易只改一边，模型按 Schema 生成合法参数、工具却按另一套规则拒绝
——这正是 Day 18 记录的候选缺口"工具 Schema 与工具校验一致性交叉测试"
要防的分叉。R-03 把 Schema 变成参数模型声明的"翻译产物"：声明只有一份
（`CalculatorParameters` 等扁平 Pydantic 模型），Schema 由
`model_json_schema()` 自动生成，业务校验读取的键与 Schema 的
`properties`/`required` 从同一份声明出发，天然对齐。

### 第二个认识：注册表是"安检门"，不是业务规则的复制品

注册表预校验只做"结构体检"：缺必需参数、类型错、多余键，在调用工具之前
就以 `INVALID_ARGUMENTS` 被拒，工具根本不会被执行。业务语义（表达式语法、
路径越界、主题是否存在）仍然留在工具自己的 `execute` 里。这个分工让两处
各司其职：Schema 表达不了"表达式能不能算"，业务层也不该重复"参数必须是
字符串"。测试用 `calls == []` 证明"安检在业务之前"，用错误码稳定证明
"模型只看错误码就能决定重试"。

### 第三个认识：最小校验器要"够用且诚实"

不引入 `jsonschema` 依赖的前提下，校验器只覆盖本项目实际用到的关键字：
`type`、`properties`、`required`、`additionalProperties`、`enum`、
`minLength`/`maxLength`、`minimum`/`maximum`、`pattern`、`items`。两个
诚实点：一是无法表达的东西直接报错（函数签名里的 `Path`、可变参数、
嵌套模型 `$defs` 都显式拒绝），不产出残缺 Schema；二是错误消息只返回
第一个，按固定顺序检查，保证相同输入永远得到同一条稳定说明。

## 今天交付了什么

- [`src/self_react/tools/schema.py`](../../src/self_react/tools/schema.py)
  （新增）：`generate_parameters_schema` / `model_to_parameters_schema` /
  `signature_to_parameters_schema` 三条生成路径 +
  `validate_parameters` / `validate_parameters_schema` 最小校验器；
- [`src/self_react/tools/base.py`](../../src/self_react/tools/base.py)：
  `register` 增加声明 Schema 合法性检查（`ToolRegistrationError`）；
  `execute` 在业务校验之前按 Schema 预校验参数，非法参数以
  `INVALID_ARGUMENTS` 被拒且工具不被执行；
- [`src/self_react/tools/calculator.py`](../../src/self_react/tools/calculator.py)、
  [`file_reader.py`](../../src/self_react/tools/file_reader.py)、
  [`retrieve.py`](../../src/self_react/tools/retrieve.py)、
  [`final_answer.py`](../../src/self_react/tools/final_answer.py)：
  四个业务工具各自新增扁平参数模型，`parameters` 改为自动生成，生成结果
  与 Day 17 手写 Schema 等价；
- [`src/self_react/tools/__init__.py`](../../src/self_react/tools/__init__.py)：
  公共出口新增 `generate_parameters_schema`；
- [`tests/test_tool_schema_generation.py`](../../tests/test_tool_schema_generation.py)
  （新增，41 个用例）：生成等价、签名转换、最小校验器、注册表边界、
  一致性交叉五组；
- 文档同步：[Day 7 工具协议导读](../architecture/day-07-tool-registry-code-walkthrough.md)
  （第 6 节新增 Schema 预校验）、[Day 23 代码导读](../architecture/day-23-tool-schema-generation-code-walkthrough.md)
  与本记录；
- README 架构模块表同步更新工具层职责说明。

## 设计边界与不变量

- **声明即校验**：Schema 是参数模型声明的翻译产物，业务校验读取的键与
  Schema 的 `properties`/`required` 从同一份声明出发；
- **分工不重叠**：Schema 管结构（缺必需/类型/多余键），工具业务管语义
  （语法、路径、主题）；注册表预校验失败的工具不被执行；
- **错误码稳定**：结构非法与业务参数非法统一映射为 `INVALID_ARGUMENTS`，
  消息前缀 `参数校验失败：` 区分来源，`retryable=True`；
- **宽松回退不变**：未声明 `parameters` 的工具回退到 `DEFAULT_PARAMETERS_SCHEMA`，
  Day 7 以来的行为不变；
- **零新依赖**：生成用 Pydantic v2 内置 `model_json_schema()`，校验用项目
  内最小 JSON Schema 子集，不引入 `jsonschema`；
- **适配器零改动**：`openai_compat.py` 读取 `parameters` 的逻辑未触碰，
  生成结果与手写等价，下发给模型的内容不变；
- **回归基准**：Day 16 三条 `example` 输出不变；既有 407 个测试全绿
  （含 `tests/test_tool_schemas.py`、`test_deepseek.py`、`test_openai.py`）。

## 遇到的问题与解决过程

### 问题一：`from __future__ import annotations` 让函数注解变成字符串

测试文件开启了 `from __future__ import annotations`，`inspect.signature`
拿到的参数注解是 `'int'`、`'str | None'` 这样的字符串，直接查类型映射表
全部报"不支持的参数类型标注"。

解决：`signature_to_parameters_schema` 先用 `typing.get_type_hints(func)`
把字符串注解解析成真实类型再映射；解析失败（如无法解析的前向引用）时
回退到原始注解，由 `_annotation_to_json_type` 报出可读错误。

### 问题二：手写 Schema 与业务校验存在已知分叉（空白字符串）

手写 Schema 只约束 `type: string`，空白字符串能通过 Schema 但被业务校验
以"expression 不能为空"拒绝。要不要给 Schema 加 `minLength` 消除分叉？

解决：保持 Schema 与 Day 17 手写等价（等价性由测试钉死），把非空等业务
规则明确留给工具层兜底，并用一条显式测试记录这个分层：
`test_blank_string_passes_schema_but_is_still_rejected_by_business`。
这样"结构由 Schema、语义由业务"的边界可复述、可验收，而不是悄悄让 Schema
越权。

### 问题三：嵌套模型会生成 `$defs`，最小校验器接不住

参数模型一旦嵌套，`model_json_schema()` 会输出 `$defs` 与 `$ref`，最小
校验器不支持引用解析。静默丢弃 `$defs` 会产出残缺 Schema，比报错更糟。

解决：`_normalize_model_schema` 检测到 `$defs` 直接抛 `ValueError`，文档
明确"只支持扁平参数模型"；未来需要时再扩展 `$ref` 解析。

## 验收结果

以下命令在 Windows、CPython 3.13.5 环境中实际执行：

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
uv run self-react run "计算 2 + 2" --model deepseek --show-trace
```

- `uv sync`：成功，解析并检查 24 个包，锁文件无变化（无新依赖）；
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`；
- 三条 `example`：退出码 0，最终回答与轨迹结构与 Day 16 记录完全一致
  （2/2、3/3、3/3 步），输出逐字不变；
- `uv run pytest`：448 通过 / 3 跳过（基线 407 通过 / 3 跳过，净新增 41
  个用例）；
- `uv run ruff check src tests` 与 `ruff format --check src tests`：全部
  通过；
- `git diff --check`：成功，无空白错误；
- 全仓库 ruff 例外与交接记录一致：`ruff check .` 的失败与
  `ruff format --check .` 的未格式化文件全部来自受保护的 `tmp/` 与
  Day 4/6 导读，未修改、未暂存。

真实 DeepSeek 手动验收（不作自动化测试前置条件）：

```powershell
uv run self-react run "计算 2 + 2" --model deepseek --show-trace
uv run self-react run "计算 2 + 2，并检索 react 主题" --model deepseek --show-trace
```

- 单工具：真实 DeepSeek，轨迹为 calculator -> 观察 4 -> 最终回答，
  2 / 5 步，最终回答 `2 + 2 = 4`；
- 多工具：真实 DeepSeek，轨迹为 calculator -> retrieve -> 最终回答，
  3 / 5 步（首轮模型同时返回两个 tool_calls，第二个按既有"本轮只执行
  一个工具"策略在后续轮次执行），最终回答汇总计算与 ReAct 说明；
- 两次验收确认自动生成的 Schema 在真实模型下与手写 Schema 行为一致，
  注册表预校验没有改变正常路径。

干净副本复验：从远端 `main` 创建临时工作树，只复制本 Issue 的变更文件，
再次执行同样的验收命令，结果见 PR 描述；临时工作树验证后已删除。

## 不在范围内

- R-04 会话记忆、R-05 流式输出、R-07 日志/故障排查场景（按 roadmap 顺序
  在后续对话推进）；
- 修改 `LLM.complete` 接口、适配器、领域模型、解析器、提示词、主循环或
  CLI；
- 引入 `jsonschema` 等新依赖；完整 JSON Schema 语法校验；嵌套参数模型的
  `$ref` 解析；
- 修改受保护的 `tmp/`、历史导读、交接文档与 `docs/project-roadmap.md`。

## 明天要验证什么

- 继续按 roadmap 顺序推进 R-04（短程会话记忆 / 上下文管理），保持"一个
  Issue 一个 PR"的节奏；
- 若真实 DeepSeek 在长任务中生成多余参数或漏参数，可用 `--show-trace`
  观察注册表是否以 `INVALID_ARGUMENTS` 稳定拒绝并让模型下一轮纠正；
- Day 16 三条示例继续作为回归基准，任何后续改动都不改变它们的输出。
