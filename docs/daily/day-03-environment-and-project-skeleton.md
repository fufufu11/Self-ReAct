# Day 3：环境与工程骨架

## 今天理解了什么

在实现 ReAct 循环之前，项目需要先具备可重复的工程基础：解释器版本、依赖
管理、包导入、命令行入口、测试和代码质量检查必须能够独立验证。这样后续接入
模型或工具时，问题可以被定位到具体能力，而不是混在环境配置中。

`pyproject.toml` 是 Python 项目的统一配置入口。本项目用它声明 Python 3.11+
兼容性、打包方式、`self-react` 命令入口、pytest 和 Ruff 配置；`uv` 读取这些
声明来创建受控环境并运行命令。

## 今天交付了什么

- 创建 `pyproject.toml`，确认项目使用 Python 3.11+ 与 `uv`。
- 采用 `src/self_react` 布局，建立可安装的最小 Python 包。
- 提供不访问模型、网络或 API Key 的 `self-react hello` 命令。
- 配置 pytest 与 Ruff，并为 `hello` 命令添加最小自动化测试。
- 新增项目骨架代码讲解，说明配置、调用链、测试边界和后续扩展位置。

## 遇到的问题与解决过程

### 问题

开始任务时 GitHub CLI 无法连接设备授权端点，且旧令牌已失效，无法满足“先
创建 Issue 再编码”的协作约定。

### 解决过程

确认 `github.com:443` 的连通性恢复后重新完成 GitHub CLI 认证，随后创建 Issue
#5。工程骨架只使用 Python 标准库的 `argparse`，避免在需要验证的最小阶段引入
不必要的运行时依赖。

## 明天要验证什么

- 定义 Message、ToolCall、ToolResult、AgentState 和 TraceStep 等领域模型。
- 为领域对象确定清晰的校验规则，并使用单元测试覆盖正常和异常输入。
- 保持 CLI、模型适配和工具执行之间的边界，避免在领域模型阶段提前实现循环。

## 本日验收

以下命令已在 Windows、CPython 3.13.5 环境中实际执行：

```powershell
uv sync
uv run self-react hello
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

结果如下：

- `uv sync` 成功创建 `.venv`、解析并安装 8 个包，同时生成 `uv.lock`。缓存与
  工作区位于不同文件系统时无法使用硬链接，uv 自动回退到文件复制；这是性能
  提示，不影响安装结果。
- `uv run self-react hello` 输出 `Hello from Self-ReAct!`。
- `uv run pytest` 收集 1 个测试，结果为 `1 passed`。
- `uv run ruff check .` 输出 `All checks passed!`。
- `uv run ruff format --check .` 确认 15 个文件均已格式化。
