# Day 19+20：文档与演示 + 发布前复盘（项目收尾）

> Issue：[41 docs: README 拆分、3分钟讲解与发布前复盘（Day 19+20 收尾）](https://github.com/fufufu11/Self-ReAct/issues/41)
>
> 本记录描述 Day 19 与 Day 20 合并执行的一件收尾工作：把 README 从"项目
> 规划书"改写成真正面向使用者的 README，产出一份 3 分钟讲解稿，并从空环境
> 复验安装与示例、清理敏感信息、整理 20 天 Issue/PR 索引并发布 v0.1.0。
> 全程零生产代码改动，所有验收可复现。

## 今天理解了什么

### 第一个认识：README 的读者决定了它该写什么

Day 19 之前，`README.md` 更像"项目规划书"：20 天计划、建议技术路线、GitHub
开发约定、每日记录模板和参考项目清单，全部面向"做这个项目的人"。但 README
的默认读者是"要用这个项目的人"：他要在一分钟内知道这是什么、怎么装、怎么跑、
有哪些限制。两类读者需要不同的文档，混在一起会让两边都找不到重点。

因此 Day 19 把规划内容完整抽到 `docs/project-plan.md`，README 只保留使用者
关心的部分：项目简介、安装、配置、运行、架构简介、局限性与演示记录。抽离不是
删除——规划内容全部保留，并额外补了一张"完成情况与 Issue/PR 索引"表，把 20
天的学习记录和 Issue/PR 链接对齐，让项目规划书同时成为可追溯的开发史。

### 第二个认识：演示要"跑得出来"才算数

Day 20 的发布前复盘让我重新理解了"演示记录"：它不能是口头承诺，而应该是可以
重新执行的命令和结果。Day 16 的三个 `example` 示例本身就是数据加组合，命令
重跑就能复现；Day 19+20 把它们的实测输出、最终回答和真实 DeepSeek 手动验收表
写进 README，让"演示记录"与代码行为一一对应，不出现文档与实现分叉。

### 第三个认识：发布的最后一道闸是"敏感信息与可复现性"

发布前复盘的三件事——空环境复验、清理密钥与临时文件、整理链接并打标签——本质
上是在回答三个问题：别人从零能不能跑起来？仓库里有没有不该出现的东西？这段
开发历史能不能被完整追述？三者都通过后，"打标签或发布版本"才成为有意义的动作，
而不是形式上的仪式。

## 今天交付了什么

- 拆分 `README.md` 与新增 [`docs/project-plan.md`](../../docs/project-plan.md)：
  - `README.md` 改写成真正 README：项目简介、特性、安装（`uv sync`）、配置
    （`DEEPSEEK_API_KEY` / `.env`）、运行（`hello` / `run` / `example`）、
    架构简介、局限性、演示记录与文档导航，全部与代码实际行为一致；
  - `docs/project-plan.md` 承接原 README 的规划内容：项目目标、目标边界、
    建议技术路线、20 天计划、GitHub 开发约定、参考项目与阅读方法、每日记录
    模板、已确定前提与待决事项，并新增"完成情况与 Issue/PR 索引"附录；
- 新增 [`docs/demo/3-minute-talk.md`](../../docs/demo/3-minute-talk.md)：
  面向面试的口语化 3 分钟讲解稿，按时间线覆盖项目是什么、核心循环、工具与
  错误处理、测试与质量、局限与收获；
- 新增 [`.env.example`](../../.env.example)：密钥配置模板，只含占位说明，
  不含真实密钥；README 补充了 `.env` 的手动加载方式（代码不自动加载 `.env`，
  文档如实说明，不与行为分叉）；
- 更新 [`CONTRIBUTING.md`](../../CONTRIBUTING.md) 中"项目计划"链接指向
  `docs/project-plan.md`；
- 新增本学习记录（Day 19+20）；
- **零生产代码改动**：`src/` 与 `tests/` 全部原封不动，Day 16 三条示例输出
  保持不变，357 个测试继续通过；
- 发布前复盘：
  - 从空环境（临时工作树 + `uv sync`）复验 `hello`、三个 `example`、pytest
    与 ruff（结果见下文"验收结果"）；
  - 扫描仓库确认无真实 API Key、无 `.env`、无临时文件被提交；
  - 整理 20 天 Issue/PR 索引（见 `docs/project-plan.md` 附录）；
  - 创建 v0.1.0 标签与 GitHub Release，发布说明汇总 20 天成果与链接。

## 设计边界与不变量

- **文档与代码不分叉**：README 只描述真实行为——`hello` 固定输出、`example`
  三条命令的最终回答、`run` 的参数与默认值、`.env` 需要手动加载、思考模式
  默认禁用等，均以源码与实测为准；
- **零生产代码改动**：本 Issue 只改文档与配置模板；若发现实现问题，单独开
  Issue，不在文档 PR 里混入代码修改；
- **可复现验收**：验收命令全部可重新执行；干净副本验证只复制本 Issue 的变更
  文件，保护文件（历史导读与 `tmp/`）原样保留；
- **敏感信息零残留**：扫描 `sk-`、`DEEPSEEK_API_KEY=` 等模式，确认没有真实
  密钥进入仓库；`.env`、`tmp/`、`.obsidian/` 均未暂存；
- **不越界**：不实现持久化、暂停/恢复、流式、异步或并行调度；不修改受保护的
  历史文档与 `tmp/`。

## 遇到的问题与解决过程

### 问题一：README 要不要写"加载 .env"的教程

代码从环境变量读取 `DEEPSEEK_API_KEY`，并不会自动加载 `.env`。如果 README
只写"把密钥放进 `.env`"，读者照做后 `run --model deepseek` 仍会报缺少密钥，
文档就与行为分叉了。

解决：README 如实说明两层关系——代码只读环境变量；`.env` 只是本地存放文件，
需要先在当前终端手动加载。同时给出 PowerShell 的加载片段和 `.env.example`
模板，读者照做即可跑通，文档与实现保持一致。

### 问题二：规划内容搬走后，README 会不会太薄

规划内容（20 天计划、技术路线、开发约定）对使用者是噪音，但对面试复盘是有
价值的材料。直接删掉会损失信息，全留会回到老问题。

解决：搬到 `docs/project-plan.md`，并在 README 的"文档导航"里保留入口；
project-plan 的附录把每日学习记录与 Issue/PR 一一对应，让"规划 + 过程"成为
一个完整的可追述文档。README 变薄是目的，不是副作用。

### 问题三：全仓库 ruff 检查仍然受保护文件影响

`ruff check .` 的 5 个失败与 `ruff format --check .` 的 11 个未格式化文件
全部来自开始前已存在且受保护的 `tmp/` 临时脚本与 Day 4/6 导读，与 Day 18
记录完全一致。本 Issue 不修改这些保护文件。

解决：如实记录例外，并在只包含本 Issue 变更文件的干净副本中复验，确认干净
副本内 `src/`、`tests/`、README 与新增文档全部通过 ruff。

## 验收结果

以下命令已在 Windows、CPython 3.13.5 环境中实际执行（完整复验见下文"干净副本
复验"）：

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

- `uv sync`：成功，依赖无变化；
- `uv run self-react hello`：输出 `Hello from Self-ReAct!`；
- 三个 `example` 命令：均以退出码 0 结束，最终回答与 Day 16 记录一致；
- `uv run pytest`：357 通过 / 3 跳过；
- `uv run ruff check src tests` 与 `uv run ruff format --check src tests`：
  全部通过；
- `git diff --check`：无空白错误；
- 全仓库 ruff 例外：与 Day 18 记录一致，均来自受保护的 `tmp/` 与 Day 4/6
  导读，未修改、未暂存；
- 敏感信息扫描：未发现真实 `sk-` 密钥、`.env` 或凭据进入暂存区；
- 发布：创建 `v0.1.0` 标签与 GitHub Release，说明含 20 天 Issue/PR 索引。

### 干净副本复验

从远端 `main` 创建临时工作树，只复制本 Issue 的变更文件（README.md、
docs/project-plan.md、docs/demo/3-minute-talk.md、docs/daily/day-19-20-docs-and-demo.md、
.env.example、CONTRIBUTING.md），然后执行：

- `uv sync`：成功；
- `uv run self-react hello`：成功；
- 三个 `example` 命令：退出码 0；
- `uv run pytest`：357 通过 / 3 跳过；
- `uv run ruff check src tests` 与 `uv run ruff format --check src tests`：
  通过；
- `git diff --check`：无空白错误；
- 临时工作树验证后已删除。

## 不在范围内

- 修改任何生产代码或核心模块（LLM、Agent、工具、解析器、领域模型等）；
- 持久化、暂停/恢复、流式、异步或并行调度；
- 修改受保护的历史文档（Day 4/5/6/10/16 导读）与 `tmp/` 临时文件；
- 把真实 DeepSeek API 调用纳入自动化测试前置条件；
- 复盘时发现但未处理的其它候选缺口（CLI `run` 运行期模型错误退出码 3 路径、
  工具 Schema 与工具校验一致性交叉测试），留作后续独立 Issue。

## 明天要验证什么

20 天计划到此收尾。后续可验证方向：

- 在全新机器上按 README 从零安装并运行三个示例，确认发布版本可复现；
- 如继续迭代，优先处理两个候选缺口（CLI 退出码 3 路径、Schema-校验一致性
  交叉测试），各自保持一个 Issue 一个 PR 的节奏；
- 真实 DeepSeek 多轮调用可作为手动验收持续观察，但不作为自动化前置条件。
