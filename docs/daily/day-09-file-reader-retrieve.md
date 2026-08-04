# Day 9：第二、三个确定性业务工具——受限文件读取与知识检索

> Issue：[17 feat: 实现受限文件读取与确定性检索工具](https://github.com/fufufu11/Self-ReAct/issues/17)
>
> 本记录只描述 Day 9 的两个业务工具：受限文件读取（`file_reader`）与确定性
> 知识检索（`retrieve`）。Agent 主循环、提示词、输出解析、重试、流式和异步
> 能力属于 Day 10 起。

## 今天理解了什么

Day 8 的计算器证明了"工具协议 + 注册表"能承载一个真实业务工具。今天要回答
两个新问题：**怎样安全地让模型读取本地文件**，以及**怎样提供一个不联网却
确定有用的检索工具**。

文件读取最危险的地方不是"读"本身，而是"读哪里"。模型传来的 `path` 只是一
个字符串，如果直接把它拼进文件路径，`../secret.txt`、`C:\Windows\win.ini`
甚至指向系统设备的 `CON` 都可能被打开。因此本日的文件读取工具把安全边界
做成两层：先做**语法安检**（只允许根目录内的相对路径，拒绝绝对路径、盘符
路径、`..` 组件和 Windows 保留设备名），再做**解析核对**（把路径解析成真实
位置后，确认它仍然落在根目录内）。符号链接逃逸就靠第二层拦截：`resolve()`
会跟着链接走到真实位置，越界就被拒绝。

知识检索的价值在"确定性"：相同输入必须得到相同输出，否则模型无法从重复
调用中学习规律。实现方式就是一张写死在代码里的主题说明表，查询先做规范化
（统一大小写、折叠空白），再精确查表；查不到就返回稳定错误，绝不编造答案。

两个工具继续沿用 Day 7/Day 8 的错误出口：参数**长什么样**不对走
`INVALID_ARGUMENTS`；输入合规但**执行时**失败（文件不存在、越界、未知主题）
走 `TOOL_EXECUTION_ERROR`，全部 `retryable=True`，让模型可以换个说法重试。

## 今天交付了什么

- 在 [`src/self_react/tools/file_reader.py`](../../src/self_react/tools/file_reader.py)
  实现 `FileReaderTool`：构造时固定允许的根目录，`execute({"path": "..."})`
  只读取根目录内的 UTF-8 文本文件。
  - 语法安检拒绝绝对路径、盘符路径、UNC 路径、包含 `..` 的路径、空字节和
    Windows 保留设备名（`CON`、`NUL`、`COM1` 等），全部走
    `INVALID_ARGUMENTS`。
  - 解析核对用 `resolve()` 把目标解析到真实位置，再用 `is_relative_to` 确认
    仍在根目录内；符号链接指向根目录之外返回 `TOOL_EXECUTION_ERROR`。
  - 根目录缺失、目标不存在、目标不是常规文件、无法按 UTF-8 解码、读取失败
    都返回稳定的 `TOOL_EXECUTION_ERROR`。
  - 资源上限：路径 ≤ 1000 字符；单次最多返回 10000 字符，超出部分截断并
    附加 `\n…（内容过长，已截断）` 标记，避免把超大文件塞进模型上下文。
- 在 [`src/self_react/tools/retrieve.py`](../../src/self_react/tools/retrieve.py)
  实现 `RetrieveTool`：内置 5 条确定性知识（`react`、`python`、`deepseek`、
  `uv`、`pydantic`），查询统一大小写并折叠空白后精确查表，相同输入返回相同
  结果；未知主题返回带可用主题列表的稳定错误。
  - 参数上限：查询 ≤ 200 字符；缺失、非字符串、空白、多余键走
    `INVALID_ARGUMENTS`，查不到条目走 `TOOL_EXECUTION_ERROR`。
- 更新 [`src/self_react/tools/__init__.py`](../../src/self_react/tools/__init__.py)，
  集中导出 `FileReaderTool` 与 `RetrieveTool`，调用方继续只依赖
  `self_react.tools` 一个入口。
- 新增 [`tests/test_file_reader.py`](../../tests/test_file_reader.py)（47 个
  用例）与 [`tests/test_retrieve.py`](../../tests/test_retrieve.py)（23 个
  用例），覆盖成功、越界、文件不存在、参数校验、截断、符号链接逃逸、调用
  编号关联和注册表集成；文件用例全部使用 pytest 临时目录，不访问网络。
- 新增本记录与 [Day 9 代码导读](../architecture/day-09-file-reader-retrieve-code-walkthrough.md)。

## 设计边界与不变量

- 安全边界是构造时固定的根目录，不是每次调用的参数：调用方只能传根目录内
  的相对路径，无法通过参数扩大允许范围。
- 路径检查分两层：语法问题（绝对路径、盘符、`..`、保留设备名）属于参数
  问题走 `INVALID_ARGUMENTS`；解析后越界属于执行期发现的安全问题走
  `TOOL_EXECUTION_ERROR`。两类都不会读取根目录外任何内容。
- 符号链接按"解析后的真实位置"判断：指向根目录内则合法，指向根目录外一律
  拒绝。这是有意取舍：边界是真实位置而不是字符串形状。
- 截断策略：内容超过 10000 字符时，只返回前 10000 字符并附加稳定标记，这是
  成功结果而非错误；读取时最多读"上限 + 标记长度 + 1"个字符，不会把超大
  文件整体载入内存。
- 检索工具只做精确匹配：规范化后的查询必须与知识库键完全一致，未知主题
  返回稳定执行错误并列出可用主题，不进行模糊猜测。
- 两个工具都只返回字符串或抛出稳定异常，不接触 `Message`、`AgentState`、
  注册表或密钥；注册进 `ToolRegistry` 后与计算器共享同一错误出口和注册纪律。

## 遇到的问题与解决过程

### 问题一：路径安全用"黑名单"还是"白名单"

最早的设想是只检查"路径里有没有 `..`"这种黑名单。但黑名单永远列不全：
Windows 的 `C:relative.txt` 不带盘符却按 C 盘当前目录解析，`CON` 是设备
而不是文件，`a/../../b` 藏在中间。最终采用"白名单 + 两层检查"：语法层只
放行"根目录内的普通相对路径"，把绝对路径、盘符路径、`..` 组件和保留设备名
全部挡在文件系统访问之前；解析层再把路径解析成真实位置做包含关系核对。
语法层负责"输入长什么样"，解析层负责"实际落在哪"，两层都通过才允许打开。

### 问题二：越界输入算参数错误还是执行错误

`../secret.txt` 和"符号链接指向根目录外"都越界，但错误类别不同。前者只看
字符串就能判断，是参数形状问题，走 `INVALID_ARGUMENTS`（模型改路径即可）；
后者需要访问文件系统才能发现，是执行期问题，走 `TOOL_EXECUTION_ERROR`。
这个分法与 Day 8"参数问题 vs 运行期问题"的哲学一致，测试和文档都明确记录，
避免调用方猜测。

### 问题三：Windows 的路径坑

`Path("C:relative.txt").is_absolute()` 在 Windows 上返回 `False`，但它仍会
按 C 盘当前目录解析；`Path("CON")` 打开的不是文件而是控制台设备。因此本日
除了 `is_absolute()` 还检查 `candidate.drive`（拦住盘符相对路径），并针对
Windows 增加保留设备名白名单检查（`CON`、`PRN`、`AUX`、`NUL`、`COM1-9`、
`LPT1-9`，按组件名去掉扩展名后比对）。这些检查只在 `os.name == "nt"` 时
生效，其他平台不受影响。

### 问题四：内容超长怎么办

直接把整个文件读进内存再截断，超大文件仍会拖垮进程。本日改为只读取
`MAX_OUTPUT_CHARS + 截断标记长度 + 1` 个字符：如果一次读到的内容超过上限，
说明文件更长，就截断并附加标记；否则原样返回。这样内存占用有明确上限，
模型也能从标记得知内容不完整，可以选择继续请求其他文件。

### 问题五：符号链接测试在当前环境被跳过

本机（Windows）创建符号链接需要管理员权限，三个真实符号链接用例按
`pytest.skip` 跳过。为了保证"解析后越界"这条安全分支在任何环境都有确定性
覆盖，额外增加一个 monkeypatch 用例：把 `Path.resolve` 临时替换成返回根目录
外路径，断言工具仍然返回"超出允许的根目录"。真实符号链接用例保留，在支持
符号链接的环境（如 Linux CI）中会自动执行。

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

- `uv sync`：成功，解析并检查 24 个包，锁文件无变化。
- `uv run self-react hello`：成功，输出 `Hello from Self-ReAct!`。
- `uv run pytest`：成功，165 个测试通过、3 个跳过（符号链接用例，原因见
  问题五），其中文件读取 47 个、检索 23 个、计算器 45 个、工具层 16 个、
  DeepSeek 16 个、LLM 8 个、领域模型 12 个、CLI 1 个。pytest 仅报告无法
  写入既有 `.pytest_cache` 的权限警告。
- `uv run ruff check .`：根目录唯一失败来自受保护的
  `tmp/day04_success_tool_call_demo.py` 导入排序问题；Day 9 文件单独检查
  通过。
- `uv run ruff format --check .`：根目录失败仍来自受保护的 Day 4 导读和
  Day 6 导读示例格式；Day 9 文件单独检查通过。
- `git diff --check`：成功，无空白错误。

与 Day 6/Day 7/Day 8 一致，全仓库检查的两个 Ruff 例外均来自开始前已存在且
明确受保护的文件，没有修改、暂存或删除它们。

Day 9 文件还在只包含仓库基线和本 Issue 文件的干净副本中复验：从基线提交
`095cf22` 创建临时仓库副本，只复制本 Issue 的 7 个变更文件
（`src/self_react/tools/` 三个文件、`tests/test_file_reader.py`、
`tests/test_retrieve.py`、两份 Day 9 文档），再次执行完全相同的六条命令，
结果如下：

- `uv sync`：成功创建隔离环境，解析并安装 24 个包，构建当前项目。
- `uv run self-react hello` 的等价已安装入口：成功输出
  `Hello from Self-ReAct!`。
- `uv run pytest` 的等价 `.venv` 命令：165 个测试通过、3 个跳过（符号链接
  用例）。
- `uv run ruff check .`：成功，输出 `All checks passed!`。
- `uv run ruff format --check .`：成功，确认 43 个文件均已格式化。
- `git diff --check`：成功，无空白错误。

临时副本在验证后已删除。这个结果验证的是仓库基线加 Day 9 Issue 文件，不
包含原工作区受保护的 Day 4/5/6 导读、交接文档和 `tmp/`。

## 不在范围内

- Agent 主循环、提示词、输出解析、重试、流式或异步能力（Day 10 起）。
- 修改 `LLM.complete` 接口、Day 4 领域模型或 Day 6 DeepSeek 适配器。
- 访问网络、真实 API、任意目录读取、模糊检索、同义词匹配或向量检索。
- 文件写入、删除、移动或任何修改文件系统的能力。

## 明天要验证什么

- 设计最小系统提示词（Day 10），把三个工具的 `description` 拼给模型，让
  模型知道何时调用 `calculator`、`file_reader` 和 `retrieve`。
- 确认工具描述与真实行为一致：`file_reader` 只能读根目录内文本、
  `retrieve` 只回答内置主题。
- 为 Day 11 的输出解析准备"合法动作、缺字段、未知工具"三类样例。
