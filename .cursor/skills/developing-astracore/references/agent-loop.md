# Agent 循环与工具运行时

> TOC：统一循环 · 执行管线 · 并发分区 · Toolset · Transcript · 预算/soft_exec · RunRegistry

## 1. 统一循环（禁止再写第二套）

`ToolLoopUseCase` 只有一条 `_run_loop()`：

| 公开 API | LLM 策略 | 工具调度 |
|---|---|---|
| `execute_with_tools` | `BlockingLLMRound` | `ToolScheduler`（分区） |
| `execute_stream_with_tools` | `StreamingLLMRound` | 同上 + 流式透出 |

流式 / 非流式差异 **只允许** 换 `LLMRoundStrategy`。发现复制粘贴两套 while → 立刻合并回 `_run_loop`。

相关文件：

- `modules/chat/application/tool_loop.py`
- `modules/chat/application/llm_round.py`
- `modules/chat/application/tool_scheduler.py`
- `modules/chat/application/tool_loop_config.py`（参数对象，别把构造器撑成 10+ 标量）

## 2. 单工具执行管线（固定顺序）

`ToolExecutor`：

```
Schema 校验 → before_tool Hook → HITL 确认 → Policy → soft_exec 门禁 → call → wrap_external → after_tool
```

要点：

- Schema 失败 → `ToolResult(is_error=True)` 回流模型，不崩 loop
- `requires_confirmation` 且无 `hitl_callback` → **拒绝执行**（fail-closed）
- `soft_exec=True` 且工具 `is_destructive=True` → 只返回参数预览，不调用真实工具
- 结果必须 `wrap_external(content, source=f"tool:{name}")`

## 3. 声明式并发分区

`modules/tools/application/partition.py` → `partition_tool_calls`：

1. `is_concurrency_safe=False`（默认）→ 独占串行 batch  
2. 同 path（参数里常见 path 键）冲突 → 强制拆批  
3. 连续无冲突的 safe 工具 → 同一并行 batch  

注册工具时必须显式标注：

```python
is_concurrency_safe=...
is_readonly=...
is_destructive=...
```

未知工具按不安全处理。写文件 / 改记忆类工具：`is_destructive=True`，`is_concurrency_safe=False`。

## 4. Toolset

`modules/tools/application/toolset.py`：命名子集（`default` / `readonly` / `memory_ops` / `worker`…）。

- `ChatOptions.toolset` → `prepare()` 用 `get_toolset` 裁剪  
- 新增场景优先加 Toolset，而不是在 prompt 里说「请少用工具」

## 5. Transcript 闭环

| 概念 | 实现 |
|---|---|
| 事件源 | `SQLTranscriptStore`（append-only，message.id 去重） |
| 领域模型 | `modules/chat/domain/transcript.py` |
| 物化视图 | Redis/SQL short-term（`prepare_for_save` 过滤后） |
| 崩溃恢复 | `load_history()`：short-term 空 → replay → 回填 short-term |

Compact 摘要：`USER` + `metadata.compacted=True`（禁止再用 SYSTEM+synthetic，会被过滤丢掉）。

## 6. 预算与 soft_exec

| 开关 | 位置 | 行为 |
|---|---|---|
| `max_input_tokens` / `max_output_tokens` | `ChatOptions` → `ChatContext` → `extra_context["budget"]` | `TurnBudget` 超限抛 `BudgetExceeded` → stream 出 `ERROR` |
| `soft_exec` | 同上 → `extra_context["soft_exec"]` | 破坏性工具预览跳过 |

`0` = 不限制。SDK/HTTP 字段已对等。

## 7. HTTP Run 与多 worker

`RunRegistry`（`infrastructure/chat/run_registry.py`）：

- **本机**：`ActiveRun` 持 Task、subscriber queues、HITL Future  
- **Redis**：state 快照 + events/hitl/cancel 信道；不可用则静默退化  
- SSE：本机有 run 订本地队列；否则尝试订 Redis 事件  

不要把 Task/Future 塞进 Redis；跨 worker 只传状态与信号。

## 8. Pipeline 职责边界

`ChatPipeline` 编排，不堆协议细节：

| 职责 | 归属 |
|---|---|
| 附件加载 | `application/attachment_loader.py` |
| LLM 适配器缓存 | `application/llm_factory.py` |
| 历史过滤 / replay | `application/history.py` |
| 静态提示拼装 | `application/prompt_builder.py` |
| prepare / stream 编排 | `pipeline.py` |

新逻辑优先进 `application/` 小模块，而不是继续撑大 `pipeline.py`。
