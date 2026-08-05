Closes #29

## 改动

- `src/self_react/cli.py`：新增 `run` 子命令（任务输入、`--model`、
  `--max-steps`、`--show-trace`/`--no-show-trace`），新增 `build_llm`
  模型工厂与 `main(argv, *, build_llm=...)` 注入点，接入 Day 13 的
  `render_trace`；`hello` 行为不变。
- `tests/test_cli.py`：16 个用例（原 1 个 hello 用例保留），覆盖参数失败
  路径、端到端运行、轨迹展示、参数透传与稳定错误路径，全部使用 Fake LLM，
  不访问网络、不依赖真实 API Key。
- 新增 `docs/daily/day-15-cli-experience.md` 与
  `docs/architecture/day-15-cli-experience-code-walkthrough.md`。

## 验证

- `uv sync`：成功，锁文件无变化。
- `uv run self-react hello`：成功。
- `uv run self-react run "计算 2 + 2" --model fake --show-trace`：成功，
  打印最终回答与三步骤轨迹。
- `uv run pytest`：305 通过、3 跳过（符号链接用例）。
- `uv run ruff check .`：仅受保护 `tmp/` 的既有例外；`src/` 与 `tests/`
  通过。
- `uv run ruff format --check src tests`：通过。
- `git diff --check`：通过。

## 风险与后续

- CLI 不实现持久化、暂停/恢复、流式、异步或并行调度。
- `--model deepseek` 的真实调用需要 `DEEPSEEK_API_KEY`；自动化测试通过
  `build_llm` 注入 Fake LLM，不依赖密钥。
- Day 16 端到端示例将使用真实 DeepSeek 调用验证 CLI。
