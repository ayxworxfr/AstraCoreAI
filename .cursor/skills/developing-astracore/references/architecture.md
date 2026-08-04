# AstraCore 架构速查（自包含）

> TOC：模块分层 · 双表面 · 关键路径 · 配置 · 前端镜像

## 1. 一句话定位

AstraCoreAI = **FastAPI 后端 + React SPA** 的 AI 助手平台。业务逻辑只活在 `modules/`；HTTP 与 SDK 是两扇门，共用同一扇门后的房间。

## 2. 分层（依赖方向只准向下）

```
modules/          ← 业务（domain + application + 本模块 ports）
  ↓ 只依赖抽象
shared/ports/     ← 跨模块接口（LLM / Tool / Memory / Attachment…）
  ↑ 由 infra 实现
infrastructure/   ← 适配器（Anthropic/OpenAI、Redis+SQL、Chroma、MCP…）
app/              ← FastAPI 装配 + 路由挂载
sdk/              ← AstraCoreClient 门面（薄封装，无重写业务）
```

**铁律**：`modules/` 禁止直接 import `infrastructure/` 具体类（装配在 `app/` / `sdk/` / pipeline 构造处完成）。新能力先想「端口在哪」，再写适配器。

## 3. 模块地图（改代码时先定位）

| 模块 | 职责 | 常改文件 |
|---|---|---|
| `chat` | 会话流水线、历史、compact、tool loop、HTTP runs/SSE | `pipeline.py`、`application/*`、`api.py` |
| `tools` | 工具定义、builtin、toolset、分区、Schema 校验 | `ports/tool.py`、`application/{toolset,partition,validate}.py`、`builtin.py` |
| `memory` | Tier-1/2、抽取、晋升 | `application/engine.py` |
| `rag` | 检索注入 | `application/pipeline.py` |
| `skills` | SKILL.md 加载与工具 | `builtin/`、skill tools |
| `agent` | 多 Agent DAG | `ports/workflow.py` + `infrastructure/workflow/` |
| `scheduling` | cron/interval/date 任务 | `task_service.py` |
| `auth` / `users` / `settings` / `system` / `tts` / `projects` / `attachments` | 各自边界 | 见目录名 |

横切：`shared/policy`（重试超时）、`shared/security/external_data`（`<external_data>` 包裹）、`shared/observability/hooks`。

## 4. 双表面（SDK ↔ API）

| 表面 | 入口 | 业务入口 |
|---|---|---|
| HTTP | `modules/*/api.py` + `app/factory.py` | 同一 `ChatPipeline` / 同一 service |
| SDK | `sdk/client.py`（及子 Client） | 同一 `ChatPipeline` / 同一 service |

**对等规则**：新字段进 `ChatOptions`（或领域模型）→ HTTP `Request.to_options()` → SDK `chat`/`chat_stream`/`Conversation` 参数或 `options=`。只做一边 = bug。

HITL：HTTP 注入 `hitl_callback`；SDK 可传 `None`（确认类工具 fail-closed 拒绝）。

## 5. 会话与持久化路径

```
用户消息
  → ChatPipeline.prepare()  → ChatContext（不可变决策快照）
  → ChatPipeline.stream()
       → load_history()          # short-term 优先；空则 transcript replay 回填
       → HistoryCompactor
       → normal | tool_loop
       → _save_session_safe()
            1) SQLTranscriptStore.append_messages  # append-only，按 message.id 去重
            2) prepare_for_save → memory.save_short_term  # 物化视图
```

HTTP 后台 run：`modules/chat/api.py` + `infrastructure/chat/run_registry.py`  
（本机 Task/HITL Future + Redis 状态/事件扇出；Redis 挂了退化为进程内）。

## 6. 系统提示分层（概念）

静态层（可缓存）：injection_guard → 身份 → skill 清单 → HITL 指南 → Tier-1 记忆  
动态层（每轮）：`<session_context>`（时间 / RAG / active-skill / Tier-2）

外部内容一律 `wrap_external(...)`，禁止当指令。

## 7. 配置

- `config/config.yaml`：结构化配置（LLM profiles、hitl、agent、policy…）
- `.env`：仅密钥
- 业务代码用 **profile id**（如 `claude-sonnet`），禁止散落裸 model 名

## 8. 前端镜像

`frontend/src/features/{chat,memory,skills,attachments,auth}/`  
Zustand + Ant Design；SSE 订 `run_id`；控件由 `/system` 的 `controls` 描述驱动。

## 9. 关键路径速查

| 意图 | 打开 |
|---|---|
| 改一轮对话编排 | `modules/chat/pipeline.py` |
| 改 Agent 循环 / 工具调度 | `modules/chat/application/tool_loop.py` + `tool_executor.py` + `tool_scheduler.py` + `llm_round.py` |
| 改工具协议 / 注册 | `modules/tools/ports/tool.py`、`builtin.py` |
| 改历史过滤 / replay | `modules/chat/application/history.py`、`domain/transcript.py` |
| 改 HTTP run/SSE | `modules/chat/api.py`、`infrastructure/chat/run_registry.py` |
| 改 SDK 门面 | `sdk/client.py` |
| 改 per-turn 选项 | `modules/chat/domain/chat_options.py` |
