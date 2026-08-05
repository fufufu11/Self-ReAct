# 下一对话任务交接

> 用途：保存下一次 AI 对话需要执行的完整任务提示词。每个学习日结束后更新本文件，使它始终指向下一项尚未开始的任务。

## 使用方式

开始新的 AI 对话时，将下方"当前交接提示词"完整发送给 AI。交接文档只提供上下文，不替代对仓库、Git 和 GitHub 实际状态的核实。如果记录与命令结果不一致，以实际文件和命令结果为准；不要为了让状态看起来整洁而还原、删除或覆盖已有改动。

## Day 6b 已完成摘要（模型适配补全，不占用 Day 16）

Day 6b 完成了 DeepSeek 原生工具调用支持（真实 API 多轮任务），属 Day 6
（DeepSeek 适配器）主题的补全，Issue #32：

- `src/self_react/llm.py`：`LLM.complete` 与 `FakeLLM.complete` 增加可选
  `tools` 参数；Fake LLM 新增 `calls_with_tools` 记录。
- `src/self_react/deepseek.py`：请求携带工具定义（function 定义，宽松参数
  形状）；新增 `thinking_disabled=True` 默认禁用思考模式（`extra_body`
  携带 `thinking: {"type": "disabled"}`），避免 `reasoning_content` 回传
  问题。
- `src/self_react/agent.py`：每轮把注册表工具清单传给 `LLM.complete`；
  消费原生 `tool_calls`（单调用直接执行；多调用只执行第一个，其余写回可
  恢复失败观察；`final_answer` 调用在分派前拦截并转换为 `FinalAnswer`
  决策，同时写回 tool 消息保持历史完整）。
- 新增 `src/self_react/tools/final_answer.py`：`FinalAnswerTool` 特殊工具，
  在 `tools/__init__.py` 与 CLI 默认注册表登记。
- `src/self_react/prompts.py`：输出规则明确"每轮只能输出一个 tool_call，
  需要多个工具时依次请求"。
- 测试扩展 8 个用例：311 通过、3 跳过（符号链接用例）；DeepSeek 请求携带
  工具定义与思考模式配置、Agent 消费原生 `tool_calls`、多工具调用处理、
  `final_answer` 拦截、工具清单透传、提示词单工具约束。
- 真实 DeepSeek 验收通过：`uv run self-react run "计算 2 + 2，并检索 react
  主题" --model deepseek --show-trace` 完整跑通（calculator -> retrieve ->
  最终回答），工具失败恢复场景与 `--model fake` 离线演示均正常。
- 新增中文 Day 6b 学习记录和架构导读：
  `docs/daily/day-06b-deepseek-native-tool-calls.md`、
  `docs/architecture/day-06b-deepseek-native-tool-calls-code-walkthrough.md`。

## 当前交接提示词

开始 Day 16：端到端示例（单工具、多工具、工具失败后恢复的 2 至 3 个可复现
示例）。

### 开始前必须核实

先阅读 `README.md`、`CONTRIBUTING.md`、`AGENTS.md`（如果存在），以及：

- `CONTEXT.md`
- `docs/architecture/react-loop.md`
- `docs/daily/day-04-domain-model.md` 至 `docs/daily/day-15-cli-experience.md`，
  以及 `docs/daily/day-06b-deepseek-native-tool-calls.md`
- `docs/architecture/day-04-domain-model-code-walkthrough.md` 至
  `docs/architecture/day-15-cli-experience-code-walkthrough.md`，以及
  `docs/architecture/day-06b-deepseek-native-tool-calls-code-walkthrough.md`
- `src/self_react/models.py`、`src/self_react/llm.py`、`src/self_react/deepseek.py`、`src/self_react/prompts.py`、`src/self_react/parser.py`、`src/self_react/agent.py`、`src/self_react/trace.py`
- `src/self_react/tools/base.py`、`src/self_react/tools/__init__.py`、`src/self_react/tools/calculator.py`、`src/self_react/tools/file_reader.py`、`src/self_react/tools/retrieve.py`
- `tests/test_models.py`、`tests/test_llm.py`、`tests/test_deepseek.py`、`tests/test_tools.py`、`tests/test_calculator.py`、`tests/test_file_reader.py`、`tests/test_retrieve.py`、`tests/test_prompts.py`、`tests/test_parser.py`、`tests/test_agent.py`、`tests/test_trace.py`、`tests/test_cli.py`

开始任务前必须执行：

```powershell
git status --short --branch
git log --oneline --decorate -8
```

还要核实 GitHub 的实际 Issue、PR 和分支状态，不要假设下一条 Issue 编号。Day 16 应从最新远端 `main` 创建独立分支，并只处理一个 Issue。

### 当前已知状态（2026-08-05，仍需用命令复核）

- **网络限制（重要）**：本环境（沙箱）可以访问 `api.github.com`（`gh` 可用），但**不能访问 `github.com:443`、`ssh.github.com:443`，且本机代理 `127.0.0.1:7897` 未运行**。因此 `git push`/`git fetch`/`git pull` 不可用；跨网络操作（建 Issue/PR、合并、读远端对象）全部通过 `gh api` 完成。
- **远端 GitHub 实际状态**：`main` 在 `a712761`（Day 6b 的 PR #33 已合并，
  此前 Day 15 的 PR #30 + #31 也已合并）；Issue #32 已关闭；远端分支
  `feat/issue-32-native-tool-calls`、`feat/issue-29-cli-experience` 与旧
  分支 `feat/issue-27-robustness` 仍存在。
- **本地 git 状态**：本地 `main` 在 `d7bd255`（Day 14 内容镜像提交）。
  本地工作分支 `feat/issue-32-native-tool-calls` 基于本地 Day 15 工作分支
的树（与远端 main `4f5ccc0` 的树完全一致：`576016b1`）创建；Day 6b 改动
  已提交为本地 `9868d66`（与远端合成提交 `91ea99c` 内容相同但 SHA 不同）。
  本地 `origin/main` 引用仍是旧的 `87de91a`（无法 fetch）。
- **下次有机会联网时必须先同步**：
  ```powershell
  git fetch origin
  git checkout main
  git reset --hard origin/main
  git branch -D feat/issue-29-cli-experience   # 远端分支已合并，如需清理
git branch -D feat/issue-32-native-tool-calls # Day 6b 合并后，如需清理
  git branch -D feat/issue-27-robustness       # 远端分支已合并，如需清理
  ```
  本地 Day 15/16 工作分支的提交与远端合成提交内容相同但 SHA 不同，同步前
  不要基于它们直接 push。
- 工作区未提交内容（保护清单）：`docs/daily/day-04-domain-model.md`、
  `docs/architecture/day-04-domain-model-code-walkthrough.md`、
  `docs/architecture/day-05-llm-code-walkthrough.md`（交接前已有"按代码顺序
  走一遍"章节）、`docs/architecture/day-06-deepseek-adapter-code-walkthrough.md`
  （已按用户要求重写为初学友好版）、`docs/architecture/day-10-system-prompt-code-walkthrough.md`
  （工作区已有未提交修改）、本交接文档、未跟踪的 `.obsidian/` 目录和 `tmp/`
  （含 Day 14/15/16 临时脚本：`tmp/rebuild_remote_branch.py`、
  `tmp/push_via_api.py`、`tmp/push_day15_via_api.py`、
  `tmp/push_day15_doc_fix_via_api.py`、`tmp/rebase_day15_doc_fix_via_api.py`、
  `tmp/push_day16_via_api.py`、`tmp/fix_pr_encoding.py`、`tmp/diag_*.py`、
  `tmp/day14_issue_body.md`、`tmp/day14_pr_body.md`、
  `tmp/day15_pr_body.md`、`tmp/day16_issue_body.md`、
  `tmp/day16_pr_body.md` 等）。这些文件必须保护，不得暂存、删除、还原、
  格式化或覆盖。

Day 6b 干净副本复验结果：从本地提交 `9868d66`（树与远端 main 合并后一致）
创建临时工作树，六项验收全部通过——`uv sync` 成功、`hello` 成功、pytest
311 通过 / 3 跳过、`ruff check .` 全部通过、`ruff format --check .` 确认
68 个文件均已格式化、`git diff --check` 无空白错误。临时工作树验证后已删除。

## Day 16 任务目标

README 的 Day 16 主题是"端到端示例"：编写 2 至 3 个可复现示例——单工具、
多工具、工具失败后恢复。以下是建议目标，具体范围以你创建的 Issue 为准：

1. 编写 2 至 3 个可复现的端到端示例：单工具（如计算器）、多工具（如
   计算器 + 检索）、工具失败后恢复（如未知主题检索后换一种方式继续）。
2. 示例可以使用 `self-react run "任务" --model fake --show-trace`
   作为离线确定性演示，也可以在显式设置 `DEEPSEEK_API_KEY` 后使用
   `--model deepseek` 做真实模型验证；真实模型调用不作为自动化测试前置条件。
3. 示例应可复现：相同输入得到相同行为，文档记录完整命令与预期输出。
4. 不实现持久化、暂停/恢复、流式、异步或并行调度。
5. 学习文档写作要求与 Day 6 至 Day 15 架构导读保持一致：避免初学者看不懂
   的专有名词（确需使用时在首次出现处用大白话解释）、整体架构配 Mermaid
   图、带初学者按顺序过一遍实际代码、采用"先见森林、再见树木"结构。

## 领域压力测试

- 每个示例都是一条可复现的命令或脚本：任务固定、工具固定、预期输出可核对。
- 确定性：自动化测试使用 Fake LLM 与确定性工具，不访问网络、不依赖真实 API。
- 边界：示例只组合已有模块（`Agent`、工具注册表、`render_trace`、CLI），
  不复制主循环逻辑；`Agent` 仍是唯一控制者。
- 端到端：示例覆盖单工具、多工具、工具失败后恢复三条主线，并展示轨迹输出。

## 建议验收命令

```powershell
uv sync
uv run self-react hello
uv run self-react run "计算 2 + 2" --model fake --show-trace
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

如遇到保护文件导致的全仓库检查失败，记录真实结果，并在只包含 Day 16 文件的干净副本中复验；不要修改无关保护文件来制造全绿结果。

## 完成流程

1. 核实 GitHub 状态，创建并确认 Day 16 Issue，再从最新远端 `main` 创建独立分支。注意：在沙箱内 `git push` 不可用，参考 Day 14/15/6b 的做法用 `gh api` 的 Git Data 端点重建远端分支（上传 blob -> 建树 -> 建提交 -> 建/更新引用），再 `gh pr create`；或者先向用户说明网络限制。
2. 先写示例与公开行为测试，再实现最小边界（示例通常只是组合现有模块）。
3. 执行完整验收，检查敏感信息、相对链接、Mermaid 和暂存清单。
4. 只暂存 Day 16 Issue 文件，不暂存本交接文档、Day 4/5/6/10 导读或 `tmp/`。
5. 使用 Conventional Commits 提交；推送与 PR 按第 1 条的网络方案处理；PR 首行使用 `Closes #<实际 Issue 编号>`。
6. 确认 PR 合并、Issue 关闭并同步本地 `main`（联网后执行上方同步命令）。
7. 根据真实结果重写本文件，继续保持它为未提交工作区修改。
