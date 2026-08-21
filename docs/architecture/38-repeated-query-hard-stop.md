# 38：查询护栏硬兜底——被拦后仍连续查询直接终止（Issue #93）

> 这篇不写逐段代码导读，只简单说明这次改了什么、为什么改、效果如何。

## 用大白话说

排查日志的开放式任务之前还有一种顽固的死法：模型查日志查到被系统提醒「该收尾了」
（`REPEATED_QUERY`），但它不听，换一套参数继续查、就是不交最终答案，最后把 8 步预算
烧光（`MAX_STEPS_EXCEEDED`）。

上一版（37，Issue #90）的护栏是「软拦截」——只是提醒一句、允许模型重试。它能拦住
「查了等于没查」的无效查询（0 命中、参数重复），却拦不住「被提醒了还硬要查」的模型。

这次上了一层「硬兜底」：一次查询被 `REPEATED_QUERY` 软拦截后，**下一轮模型只要还发起
查询类工具，就直接终止**，不再给它反复换参数的机会；终止原因新增
`REPEATED_QUERY_STOP`，跟「步数耗尽」区隔开来，一眼就能看出是「被拦后还查」。

注意：**0 命中软拦截不触发硬兜底**（这是 Issue #93 合并后由 #95 修复的边界）。0 命中
代表「这个维度确实没有数据」，换维度继续查是正常排查（比如查 503 命中 0 条，模型接着
按状态码聚合、查 runbook 都是合理的），只有「参数重复」和「查够阈值」这两类真正
打转的信号才会触发硬兜底。

中间如果模型换套路——比如改用 `file_reader` 去读发布记录、或调 `calculator` 算个数——
就视为它换了策略，硬兜底解除，不会误伤正常排查流程。

## 为什么不是 plan-reflect

动手前先做了对照实验（default / plan / reflect / plan+reflect × 6 任务，各 1 次）：

| 模式 | 成功 | 失败 |
| --- | --- | --- |
| default | 6/6 | 无 |
| plan | 4/6 | t01 解析错误、t04 超时 |
| reflect | 6/6 | 无 |
| plan+reflect | 5/6 | t05 步数耗尽 |

结论：plan 模式反而新增解析错误、plan+reflect 把最简单的 t05 拖垮，没有收益还添乱。
所以不引入 plan-reflect，改用最小、最直接的硬兜底。

## 改了什么

- `src/self_react/models.py`：`TerminationReason` 新增 `REPEATED_QUERY_STOP`。
- `src/self_react/agent.py`：主循环新增 `pending_repeated_query_stop` 状态位；查询被
  `REPEATED_QUERY` 软拦截后置位，下一轮仍查询即构造不可恢复的 `REPEATED_QUERY` 终止
  （`_termination_reason_for` 映射到 `REPEATED_QUERY_STOP`）；任何非查询工具执行后复位。
  **0 命中软拦截不置位**（`zero_hit_intercept` 标志，Issue #95 修复）。
- `tests/test_repeated_query_guard.py`：+4 项（达阈值后仍查询硬止、0 命中后不硬止、
  calculator 解除、file_reader 解除）。

## 效果

- pytest **687 通过 / 3 跳过**（基线 683 + 4），ruff 全绿。
- 真实 DeepSeek 手动验收：t01（外部扫描）与 t02（窗口定位）正常收敛为 `FINAL_ANSWER`，
  结论正确，未被硬兜底误伤。
- 24 次同协议复测暴露并修复一处误伤：t05（查 503，0 命中）曾被硬兜底误杀（3 次里 2 次
  `REPEATED_QUERY_STOP`）；修复后 0 命中不再触发硬兜底，t05 复验恢复收敛。

## 没做的事

对照实验用每格 1 次的样本，只用于判断趋势；没有对 plan / plan+reflect 再做扩大样本
复测（结论已明确——无收益有副作用，不值得继续投入）。