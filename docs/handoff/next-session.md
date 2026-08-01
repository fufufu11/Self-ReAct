# 下一对话任务交接

> 用途：保存下一次 AI 对话需要执行的完整任务提示词。每个学习日结束后更新本文件，使其始终指向下一项尚未开始的任务。

## 使用方式

开始新的 AI 对话时，将下方“当前交接提示词”完整发送给 AI。开始任务前仍需以仓库的实际文件、Git 状态和 GitHub 状态为准；如果本文件记录的工作区状态已经变化，应先核实，不要擅自覆盖已有改动。

## 当前交接提示词

开始 Day 3：环境与骨架。

请先阅读仓库中的 `README.md`、`CONTRIBUTING.md`、`AGENTS.md`，以及：

- `docs/daily/day-01-project-scope-and-development-conventions.md`
- `docs/daily/day-02-understanding-react.md`
- `docs/architecture/react-loop.md`

开始前先执行 `git status`，识别并保护已有工作区改动。当前已知 `docs/daily/day-02-understanding-react.md` 可能存在未提交修改，`docs/daily/day-01-project-scope-and-development-conventions.md` 和 `docs/research/react-technical-route.md` 可能是未跟踪文件。不要还原、删除或把这些无关改动纳入 Day 3 的提交；如果实际状态与此不同，以命令结果为准。

本对话只处理一个 GitHub Issue。请创建下一条可用的 Issue，标题建议为：

```text
chore: 初始化 Python 工程环境与最小骨架
```

Issue 必须包含：目标、验收标准、不在范围内、验证方式。

### 任务目标

1. 确认并记录项目使用 Python 3.11+ 和 `uv`。
2. 创建最小 `pyproject.toml`，配置项目元数据、Python 版本和开发依赖。
3. 建立 `src` 布局和 `self_react` 包，但只创建 Day 3 所需的最小文件。
4. 配置 pytest 和 Ruff。
5. 提供一个不依赖模型、网络或 API Key 的最小 `hello` 命令。
6. 为 `hello` 命令补充最小自动化测试。
7. 确保以下命令可以运行；如果采用其他等价入口，需要说明原因：

   ```powershell
   uv sync
   uv run self-react hello
   uv run pytest
   uv run ruff check .
   uv run ruff format --check .
   ```

8. 更新 Day 3 每日记录，建议放在 `docs/daily/day-03-environment-and-project-skeleton.md`。

### 实现要求

- 遵循仓库现有命名、目录和 GitHub 协作约定。
- 保持工程骨架最小，只实现能够验证环境、包导入、CLI 入口和测试工具链的内容。
- 测试不得依赖真实模型、网络服务或环境密钥。
- 如需选择构建后端或 CLI 实现方式，优先选择简单、成熟且容易解释的方案，并在 PR 中说明。

### 学习性要求

- 代码需要包含足够详细的中文注释和 docstring，帮助初学者理解模块职责、函数输入输出、CLI 入口、测试组织和配置目的。
- 注释重点解释“为什么这样设计”“代码如何关联”和“调用从哪里流向哪里”，不要只重复代码表面含义。
- 对公共函数、CLI 入口和不直观的配置项进行说明，并保证注释与实际实现一致。
- 如果代码之间存在跨模块调用，请新增中文代码讲解文档，建议放在 `docs/architecture/day-03-project-skeleton-code-walkthrough.md`。
- 代码讲解文档至少说明：
  1. 项目目录及各文件职责。
  2. `pyproject.toml` 中构建、命令入口、pytest 和 Ruff 配置的作用。
  3. `uv run self-react hello` 从命令入口到最终输出的调用过程。
  4. 测试如何调用和验证代码。
  5. 后续模块应从哪些位置继续扩展。
- 代码讲解文档包含一个 Mermaid 调用流程图。如果实际只有单一函数且没有跨模块调用，可以不单独创建该文档，但需要在 Day 3 每日记录中说明原因。
- 完成后检查代码注释是否与实现一致，并验证讲解文档中的相对链接和 Mermaid 语法。

### 不在范围内

- 不接入 DeepSeek，不配置或调用任何真实模型。
- 不实现 LLM 抽象、领域模型、工具注册表、解析器或 ReAct 主循环。
- 不引入 LangChain、LangGraph 等运行时依赖。
- 不添加实际业务工具。
- 不处理下一条 Issue。
- 不修改或提交与 Day 3 无关的现有工作区改动。

### 完成流程

1. 执行全部验收命令并记录真实结果。
2. 检查 `git diff`、空白错误和敏感信息。
3. 创建符合仓库约定的独立分支。
4. 只暂存本 Issue 的文件。
5. 使用 Conventional Commits 创建提交。
6. 推送分支并创建关联 PR，PR 描述首行使用 `Closes #<实际Issue编号>`。
7. 确认 PR 合并、Issue 关闭，并把本地 `main` 同步到远端后结束本对话。
