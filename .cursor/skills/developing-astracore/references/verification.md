# 验证与对账

> TOC：命令 · SDK/API 对等 · Agent 回归 · 交付断言

## 1. 标准命令

优先用 Makefile（环境已配 hatch）：

```bash
make test          # 全量 pytest
make check         # ruff + mypy
make lint
make type-check
make fmt
```

单测：

```bash
# 若 hatch 在 PATH
hatch run pytest tests/path/to/test_file.py -v

# Windows 本地 venv 回退（仓库常见布局）
PYTHONPATH=src .hatch/venvs/Scripts/pytest.exe tests/path/to/test_file.py -v
```

改工具循环时至少跑：

```bash
pytest tests/core/application/test_tool_loop.py \
       tests/core/application/test_tool_partition_loop.py \
       tests/modules/tools/test_partition.py \
       tests/modules/chat/test_soft_exec.py \
       tests/modules/chat/test_history_replay.py \
       tests/modules/chat/test_turn_budget.py -v
```

改 HTTP run：

```bash
pytest tests/service/test_stream_session_safety.py tests/infrastructure/chat/test_run_registry.py -v
```

## 2. SDK / API 对等检查表

对每个新能力勾选：

| # | 检查 | 通过标准 |
|---|---|---|
| 1 | Domain | `ChatOptions` 或 service 方法存在 |
| 2 | HTTP | Request 字段 + `to_options()` 或独立路由 |
| 3 | SDK | `chat`/`chat_stream`/`Conversation`/`子Client` 可达 |
| 4 | 行为 | 两边最终调用同一 module service / `ChatPipeline` |
| 5 | HITL | SDK `hitl_callback=None` 时行为明确（拒绝或跳过） |
| 6 | 测试 | 至少一侧自动化；对等字段有断言更佳 |

说「SDK 也能用」必须指出具体方法签名，禁止口头声称。

## 3. Agent 路径烟雾清单

| 场景 | 期望 |
|---|---|
| 无工具 normal stream | 单次 LLM，history 可保存 |
| tool_loop 多轮 | ROUND_START / TOOL_CALL / 结果回流 / 最终 DONE |
| compact 后下一轮 | 摘要仍在上下文（`compacted`） |
| short-term 清空 | transcript replay 恢复可见历史 |
| soft_exec + destructive | 不落盘，结果含 `[soft_exec]` |
| budget 极小 | `BudgetExceeded` → ERROR 事件或明确异常 |
| 并发 read+write | write 不与冲突 read 同批并行 |

## 4. 交付前断言（证据）

完成声明必须附：

1. 跑过的命令  
2. 通过/失败摘要（passed/failed 数或关键用例名）  
3. 若未跑全量：写明范围与原因  

禁止：「应该没问题」「基本对齐」「本地看看就行」。
