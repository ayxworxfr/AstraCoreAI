# HITL：ask_user + 工具确认 + 记忆晋升审批

**日期**：2026-06-17  
**档位**：设计卡（设计卡级，跨 ≥10 文件，含新公开端点，改记忆引擎行为）  
**状态**：待实施

---

## 意图与边界

### Job-to-be-Done

当 AI 在处理任务时遇到分支决策、或即将执行不可逆动作，
用户希望能**像 Claude Code 一样**被 AI 主动询问并选择，
以便在保持对话流畅的同时，对关键决策保持控制权。

### Goals

1. LLM 可主动调用 `ask_user` 工具，向用户展示带选项的问题，选完再继续
2. 标记为 `requires_confirmation=True` 的工具执行前自动弹出确认框
3. Session → User/Global 记忆晋升前写入 pending 队列，用户在专属页面审批后才真正落库
4. 两类行为均可通过 `config.yaml` 的 `hitl` 节独立关闭

### Non-Goals（本次不做）

- 多 worker 部署下 `_ACTIVE_RUNS` 的 Redis 迁移（已知限制，HITL 加重了这个限制，文档注明但不修）
- MCP 工具的 `requires_confirmation` 拦截（只处理 NativeToolAdapter 路由的工具）
- before_tool Hook 内实现暂停（Hook 是同步拦截，不适合 await；暂停逻辑放在 tool_loop.py）
- 记忆审批的推送通知 / Websocket（用轮询即可）
- 移动端适配

### 成功标准

| Goal | 可验证现象 |
|---|---|
| ask_user | LLM 调用 `ask_user` → SSE 发 `user_input_required` → run 进入 `awaiting_input` → 前端渲染选项卡 → 提交 POST `/answer` → run 恢复 streaming |
| requires_confirmation | 调用 `save_memory(scope=user)` 前弹出确认框，用户选"取消" → LLM 收到 "user_denied" tool result |
| 记忆审批 | 晋升决策写入 `memory_pending_promotions` 表 → GET `/memory/pending-approvals` 返回列表 → POST 审批 → USER 级记忆落库 |
| 配置关闭 | `hitl.require_tool_approval: false` 时 `save_memory` 直接执行无弹框 |

---

## 决策驱动变量

| 变量 | 类别 | 取值 | 来源 |
|---|---|---|---|
| AI 主动询问范围 | driver | 全开（任何时候 AI 都能调 ask_user） | 用户回答 |
| 记忆审批触发时机 | driver | 晋升时审批（异步，不阻塞对话） | 用户回答 |
| 危险命令判定方式 | driver | 工具元数据声明 `requires_confirmation=True` | 用户回答 |
| 实施节奏 | driver | 完整功能一次性做完 | 用户回答 |
| 前端 SSE 解析 | driver（可推断） | `parseBlock` in `chatService.ts:98-169` | 项目事实 |
| `ToolDefinition.requires_confirmation` 已存在 | 项目事实 | 字段已在 `tool.py` + `native.py`，未使用 | `src/astracore/modules/tools/ports/tool.py` |
| `ShortCircuit` 不适合 await | 项目事实 | `before_tool` 返回 ShortCircuit 是同步拦截，不能 await Future | `src/astracore/shared/observability/hooks.py:64-74` |
| run 不携带 run_id 进工具函数 | 项目事实 | `_context` 含 `session_id`/`user_id`，不含 `run_id` | `src/astracore/modules/tools/builtin.py` 工具签名 |

---

## 项目事实

| 事实 | 路径 |
|---|---|
| `_ActiveRun` 结构：`state dict + subscribers set + task` | `src/astracore/modules/chat/api.py:42-68` |
| `_broadcast_run_event(run_id, event, data)` 广播机制 | `src/astracore/modules/chat/api.py:90-96` |
| `StreamEventType(StrEnum)` 枚举定义位置 | `src/astracore/shared/ports/llm.py:17-38` |
| `ToolDefinition.requires_confirmation: bool = False` 已有字段 | `src/astracore/modules/tools/ports/tool.py` |
| `NativeToolAdapter.register_tool(requires_confirmation=)` 已有参数 | `src/astracore/infrastructure/tools/native.py:24-39` |
| `_promote_one()` 直接 create/archive，无 pending 表 | `src/astracore/modules/memory/application/engine.py:560-636` |
| `_evaluate_and_promote()` 在 `extract_and_store` 结尾异步调用 | `src/astracore/modules/memory/application/engine.py:424-451` |
| `before_tool` hook 可返回 `ShortCircuit(result=ToolExecutionResult)` | `src/astracore/shared/observability/hooks.py:83-157` |
| `_context` 注入机制：函数签名含 `_context` 时自动传入 | `src/astracore/infrastructure/tools/native.py:61-64` |
| 前端 SSE 解析入口：`parseBlock` 处理 event 类型 | `frontend/src/features/chat/services/chatService.ts:98-169` |
| `subscribedRunIds` 防重复 SSE 订阅 | `frontend/src/features/chat/store/chatStore.ts:89-140` |
| memory 路由文件位置（推断为同模块） | `src/astracore/modules/memory/api.py`（需确认是否已存在） |

### 调用方 grep（公开接口变更）

`_promote_one` 调用方：
- `src/astracore/modules/memory/application/engine.py:L518-558`（`_evaluate_and_promote` 内调用）
- 无其他调用方（内部私有函数）

`_execute_run` 中工具执行路径：
- `src/astracore/modules/chat/api.py` 调用 `ChatPipeline.stream(ctx)`
- `src/astracore/modules/chat/application/tool_loop.py` 内 `execute_stream_with_tools` 执行工具

新端点调用方（前端）：
- `frontend/src/features/chat/services/chatService.ts`（需新增 `submitAnswer` + `fetchPendingApprovals` + `submitApprovals`）

---

## 档位

**设计卡**（跨 ≥10 文件、新增公开 API 端点、改记忆晋升行为、含 DB schema 变更）

---

## diff 预算

| 类型 | 文件数 | 行数 |
|---|---|---|
| 后端新增/修改 | 8 | ~500 |
| 前端新增/修改 | 6 | ~450 |
| 配置 | 1 | ~15 |
| **合计** | **15** | **~965** |

触及 Non-Goals 或超出此预算 → building 停下报告。

---

## 代码级约束（命中项）

### 并发安全

**约束**：`ask_user` 工具内 `await asyncio.Future`；同一 run 可能多轮工具调用，需防止多个工具同时等待 Future（否则 answer 只解锁第一个）。  
**处理**：`_ActiveRun` 用 dict `_hitl_futures: dict[str, Future]` 按 `question_id` 隔离；`ask_user` 同时只允许一个活跃 question（第二次调用等待第一个完成或超时）。  
**检查方式**：pytest 并发测试：两个 `ask_user` 顺序调用，验证第二个排队等待而非同时挂起。

### 超时与资源泄漏

**约束**：`ask_user` `await asyncio.wait_for(future, timeout=300)` 超时后 Future 须清理，run 恢复 `running` 状态，不留僵尸挂起。  
**检查方式**：单测模拟超时：`asyncio.wait_for` 超时后检查 `_hitl_futures` 清空、run status 恢复 running。

### 多 worker 已知限制

**约束**：`_ACTIVE_RUNS` 是进程内 dict；`POST /answer` 落在不同 worker 时 Future 找不到。  
**处理**：文档注明，单 worker 部署有效，多 worker 部署前需先做 Redis 迁移（Non-Goal）。  
**检查方式**：在 `_answer_run` 端点加日志 warning：`run_id not in _ACTIVE_RUNS`。

### 记忆审批并发覆盖

**约束**：多条晋升候选并发写 `memory_pending_promotions`，主键冲突或重复审批。  
**处理**：以 `(user_id, source_memory_id)` 做 UNIQUE 约束，重复晋升决策幂等 INSERT OR IGNORE。  
**检查方式**：单测：同一 memory 两次晋升决策，只有一条 pending 记录。

### 前端 Sender 禁用一致性

**约束**：`awaiting_input` 期间用户切换会话再切回，Sender 应仍处于禁用态（从 run_state 恢复）。  
**检查方式**：手动路径：切换会话再切回，观察 Sender 禁用 + QuestionCard 显示。

---

## 子模式展开（large）

### Walking Skeleton（第一刀完成标志）

**目标**：`ask_user` 工具端到端联通：LLM 调用 → run 暂停 → SSE `user_input_required` → 前端 QuestionCard → POST `/answer` → run 恢复 streaming → 最终 done。

**完成标志**：
- `make api` 启动后，在前端触发 `ask_user` 调用，能看到 QuestionCard 渲染
- 选项提交后 run 恢复并最终完成
- `make check` 通过

Walking Skeleton 不包含：requires_confirmation 拦截、记忆审批、配置开关（这三项在后续切片）。

---

### 切片清单

#### 切片 1：核心机制（Walking Skeleton）

**后端**：

**S 类（结构，不改行为）**：
- `StreamEventType` 新增 `USER_INPUT_REQUIRED = "user_input_required"` / `USER_INPUT_RESOLVED = "user_input_resolved"`（`shared/ports/llm.py`）
- `_ActiveRun` 新增字段：`pending_question: PendingQuestion | None = None`，`_hitl_futures: dict[str, asyncio.Future] = {}`（`api.py`）
- 新建 `src/astracore/shared/domain/hitl.py`：`PendingQuestion(BaseModel)` 数据类（question_id, question, header, options, multi_select, allow_freeform, created_at）

**B 类（行为）**：

1. **`ask_user` 工具**（`modules/tools/builtin.py`）

   ```python
   async def _ask_user(
       question: str,
       header: str,
       options: list[dict],           # [{label, description}]
       multi_select: bool = False,
       allow_freeform: bool = True,
       _context: dict | None = None,
   ) -> str:
   ```
   
   实现：
   - 从 `_context["hitl_callback"]` 获取回调（类型：`async (PendingQuestion) -> dict`）
   - 若无 callback（非 run 环境）返回 `{"selected": [], "freeform": null, "error": "no_run_context"}`
   - 构造 `PendingQuestion`，await callback，返回 JSON

2. **`hitl_callback` 注入**（`api.py` 的 `_execute_run` 函数）

   ```python
   async def _hitl_callback(q: PendingQuestion) -> dict:
       # 写入 _ActiveRun.pending_question
       # 创建 Future，写入 _ActiveRun._hitl_futures[q.question_id]
       # broadcast USER_INPUT_REQUIRED
       # update run status = awaiting_input
       # await asyncio.wait_for(future, timeout=cfg.hitl.inline_question_timeout)
       # 清理 pending_question 和 future
       # broadcast USER_INPUT_RESOLVED
       # update run status = running
       # return answer dict
   ```
   
   将 `hitl_callback` 注入到 tool context：
   ```python
   context = {
       "session_id": ...,
       "user_id": ...,
       "hitl_callback": _hitl_callback,
   }
   ```

3. **answer 端点**（`api.py`）

   ```
   POST /runs/{run_id}/answer
   Body: { question_id: str, selected: list[str], freeform: str | null }
   Response: 200 OK | 404 Not Found | 409 Conflict（无待答问题）| 410 Gone（超时/已答）
   ```
   
   实现：找到 `_hitl_futures[question_id]`，`future.set_result(answer)`

4. **SSE 广播**（`api.py` `_execute_run` 事件循环）

   - `USER_INPUT_REQUIRED` → SSE `event: user_input_required`，data 含完整 `PendingQuestion` + `run_state.status = "awaiting_input"`
   - `USER_INPUT_RESOLVED` → SSE `event: user_input_resolved`，data 含 `question_id`

5. **run_state 扩展**（`api.py` `_ActiveRun.update`）
   - `state["pending_question"]` 在 awaiting_input 时写入，resolved 后清空
   - `GET /sessions/{id}/runs/active` 返回的快照包含 `pending_question`（刷新后可恢复 QuestionCard）

**前端**：

1. **新增 SSE 事件处理**（`chatService.ts`）

   ```typescript
   // parseBlock 新增
   case 'user_input_required': handlers.onUserInputRequired?.(data); break;
   case 'user_input_resolved': handlers.onUserInputResolved?.(data.question_id); break;
   ```

2. **`submitAnswer`**（`chatService.ts`）

   ```typescript
   export async function submitAnswer(
     runId: string,
     answer: { question_id: string; selected: string[]; freeform: string | null }
   ): Promise<void>
   ```

3. **chatStore 扩展**（`chatStore.ts`）

   ```typescript
   pendingQuestionByConversation: Record<string, PendingQuestion | null>
   
   // sendMessage 的 handlers 新增：
   onUserInputRequired: (q) => setPendingQuestion(convId, q),
   onUserInputResolved: () => setPendingQuestion(convId, null),
   
   submitAnswer: async (convId, answer) => {
     const runId = state.runIdByConversation[convId]
     await submitAnswer(runId, answer)
   }
   ```

4. **`<QuestionCard>` 组件**（`features/chat/components/QuestionCard.tsx`）

   ```tsx
   // antd Radio.Group（单选）或 Checkbox.Group（多选）
   // allow_freeform=true 时末尾追加"其他..."带 Input.TextArea
   // 提交后 → disabled 状态展示选择结果
   // props: question, onSubmit, disabled
   ```

5. **ChatMain 渲染**（`features/chat/components/ChatMain.tsx`）
   - 消息列表底部追加 `QuestionCard`（在最后一条 assistant 气泡下方）
   - `isStreaming || pendingQuestion` 时 Sender disabled

6. **run_state 恢复**（`chatStore.ts` `resumeActiveRun`）
   - 若 `run_state.pending_question != null` → 恢复 `pendingQuestionByConversation`

---

#### 切片 2：requires_confirmation 工具拦截

**后端**：

**修改 `tool_loop.py`**（`modules/chat/application/tool_loop.py`）

在 `execute_stream_with_tools` 内，并行执行工具前，对每个 tool_call 检查：

```python
defn = self._adapter.get_definitions()  # 已缓存
requires_confirmation = next(
    (d.requires_confirmation for d in defns if d.name == tool_call.name),
    False,
)
if requires_confirmation and hitl_callback:
    confirmed = await _ask_tool_confirmation(tool_call, hitl_callback)
    if not confirmed:
        # 注入 tool_result error
        ...
        continue
```

`_ask_tool_confirmation` 构造问题（"是否执行 `{tool_name}`，参数：{args_preview}？"），调用 `hitl_callback`，返回 bool。

需要将 `hitl_callback` 从 context 传递到 tool_loop：

```python
# tool_loop.py execute_stream_with_tools 新增参数（或从 session context 取）
hitl_callback: Callable | None = context.get("hitl_callback")
```

**修改 `builtin.py`**：

`save_memory` 工具注册时，对 `scope=user` / `scope=global` 的情况标记 `requires_confirmation=True`：

```python
# 方案：register_tool 时不能按参数值动态标记
# 改为：在 before_tool hook 里检查 save_memory 的 scope 参数
```

实际上 `requires_confirmation` 是工具级别的（不能按参数值动态变化），对于 `save_memory`，scope 是运行时参数，所以不能在工具定义时标记。

**替代方案**：在 `_ask_tool_confirmation` 里特殊处理 `save_memory`（检查 `arguments.scope in ["user", "global"]`）。

或更干净的方案：在 `save_memory` 函数实现内部，若 scope 为 user/global 且 `_context["hitl_callback"]` 存在，直接调用 callback 询问确认：

```python
async def _save_memory(..., _context: dict | None = None) -> str:
    if scope in ("user", "global") and _context.get("hitl_callback"):
        answer = await _context["hitl_callback"](PendingQuestion(
            question=f"AI 想记住以下内容（{scope} 级）：\n{content}",
            header="记忆审批",
            options=[
                {"label": "允许", "description": "保存此记忆"},
                {"label": "拒绝", "description": "取消保存"},
            ],
        ))
        if "允许" not in answer["selected"]:
            return "用户拒绝保存此记忆。"
    # 继续执行原 save_memory 逻辑
```

这样 save_memory 自己包含确认逻辑，不依赖 tool_loop 的 `requires_confirmation` 字段（更精准，只拦截 user/global scope）。

**使用 `requires_confirmation` 字段的工具**（在 `builtin.py` 注册时标记）：
- `delete_memory`：直接标记 `requires_confirmation=True`
- `spawn_agents`：如果 `hitl.require_tool_approval=True`，标记确认

---

#### 切片 3：记忆晋升审批

**DB Schema 变更**：

新增表 `memory_pending_promotions`（`infrastructure/db/models.py`）：

```python
class MemoryPendingPromotionRow(Base):
    __tablename__ = "memory_pending_promotions"
    id: UUID PK
    user_id: str NOT NULL
    source_memory_id: UUID FK -> structured_memories.id
    target_scope: str  # "user" | "project"
    reason: str        # LLM 给出的晋升理由
    candidate_content: str  # 记忆内容快照
    candidate_subject: str
    status: str default "pending"  # pending | approved | rejected
    created_at: datetime
    reviewed_at: datetime | None
    UNIQUE (user_id, source_memory_id)  # 同一记忆不重复 pending
```

**修改 `_promote_one`**（`modules/memory/application/engine.py`）：

```python
async def _promote_one(...) -> None:
    # LLM 决策 promotion_decision ...
    if decision.action in ("promote_user", "promote_project"):
        if self._hitl_enabled:
            # 写入 pending 表，不立即晋升
            await self._store.create_pending_promotion(
                user_id=memory.user_id,
                source_memory_id=memory.id,
                target_scope="user" if decision.action == "promote_user" else "project",
                reason=decision.reason,
            )
            return
    # hitl 关闭时：原来的直接晋升逻辑
    ...
```

**新增 API 端点**（`modules/memory/api.py` 或 router）：

```
GET  /api/v1/memory/pending-approvals?limit=20&offset=0
     → { total: int, items: [{ id, candidate_subject, candidate_content, target_scope, reason, created_at }] }

POST /api/v1/memory/pending-approvals/batch-review
     Body: { decisions: [{ id: uuid, action: "approve" | "reject" }] }
     → { approved: int, rejected: int }
```

`approve` 时：执行原 `_promote_one` 的晋升逻辑 + 标记 `status=approved`  
`reject` 时：标记 `status=rejected` + 被审批记忆保持 SESSION scope 不动

**前端**：

1. **Header badge**（`layouts/AppShell.tsx`）：启动时 + 轮询（每 60s）拉 `pending-approvals` count，有未读则显示 antd `Badge`

2. **审批页**（`features/memory/pages/PendingApprovalsPage.tsx`）：路由 `/memory/approvals`，Table 展示 subject/content/target_scope/reason，批量勾选 + 审批/拒绝按钮

3. **Router 新增** `/memory/approvals` 路由，Header nav 中 Memory 下拉或独立入口

---

#### 切片 4：配置开关 + System Prompt 指南

**`config/config.yaml` 新增 `hitl` 节**：

```yaml
hitl:
  enabled: true                      # false = 关闭所有 HITL
  inline_question_timeout: 300       # ask_user 等待超时秒数
  require_tool_approval: true        # false = 忽略 requires_confirmation 字段
  require_memory_promotion_approval: true  # false = 直接晋升，不写 pending 表
```

**System Prompt 注入**（`modules/chat/pipeline.py` `_build_system_prompt` 或 `prompt_utils.py`）：

在 Skill 摘要清单层后追加（仅当 `hitl.enabled=True`）：

```
## ask_user 工具使用指南
当遇到以下情形时，调用 ask_user 工具向用户提问：
- 有 ≥2 个合理方案需用户选择（架构决策、代码风格、流程分支）
- 关键参数缺失且无法从上下文推断（删除哪个文件、目标环境）
- 即将执行不可逆操作（已由系统自动拦截，无需再手动询问）

不要问的场景：
- 用户已明确说过的事项
- 能从代码/历史/文档查到的信息
- 低代价的偏好选择（先做，做错再改更快）
```

---

## S/B 拆分顺序（设计卡）

### S 类（结构，不改行为）—— 先做

1. `StreamEventType` 新增两个枚举值（`shared/ports/llm.py`）
2. `_ActiveRun` 新增 `pending_question` 和 `_hitl_futures` 字段（`api.py`）
3. 新建 `src/astracore/shared/domain/hitl.py`：`PendingQuestion` 数据类
4. `MemoryPendingPromotionRow` 数据模型（`infrastructure/db/models.py`）
5. `config.yaml` 新增 `hitl` 节（仅结构，实际行为变更在 B 类）

运行 `make check`，所有现有测试仍绿。

### B 类（行为）—— S 之后

**切片 1 B**：
1. `ask_user` 工具注册 + 实现（`builtin.py`）
2. `hitl_callback` 注入到 context（`api.py` `_execute_run`）
3. `POST /answer` 端点（`api.py`）
4. SSE 事件广播（`api.py` `_broadcast_run_event` 已有，新增事件类型处理）
5. 前端 QuestionCard + chatStore + chatService（frontend）

**切片 2 B**：
6. `save_memory` 内部确认逻辑（`builtin.py`）
7. `delete_memory` 注册时标记 `requires_confirmation=True`（`builtin.py`）
8. `tool_loop.py` 读取 `requires_confirmation` 的通用拦截（对其余工具生效）

**切片 3 B**：
9. `_promote_one` 改写：写 pending 表而非直接晋升（`engine.py`）
10. `create_pending_promotion` + `list_pending_promotions` + `apply_promotion` 在 store 层（`infrastructure/memory/store.py`）
11. 审批端点（`modules/memory/api.py`）
12. 前端 Header badge + 审批页（frontend）

**切片 4 B**：
13. 配置读取 + 行为开关接入各切片
14. System Prompt 注入 ask_user 指南

---

## 失败模式与验证

| # | 失败模式 | 级别 | 原因 | 验证项 |
|---|---|---|---|---|
| F-1 | ask_user 超时后 Future 未清理，run 永久 awaiting_input | High | `asyncio.wait_for` 超时路径未在 finally 清理 `_hitl_futures` | RED test：mock Future 超时 → 检查 `_hitl_futures` 空、status 恢复 running |
| F-2 | 多 worker：`/answer` 落在不同进程，Future 找不到，run 永久挂 | High | `_ACTIVE_RUNS` 进程内 dict | 文档注明；`/answer` 404 时前端提示"服务器重启，请刷新" |
| F-3 | 同一 run 两次调 ask_user 同时挂起，第一个 answer 触发第二个 Future | Medium | 两个 Future 共用同一 answer endpoint | `question_id` 隔离 Future；answer 带 question_id 精确匹配；RED test：顺序两次 ask_user，验证第二次排队 |
| F-4 | 用户切换会话，前端仍发 QuestionCard 的 submitAnswer 到错误 run_id | Medium | 切换后 runIdByConversation 混淆 | 前端 submitAnswer 用当前 convId 的 runId；单测：切换 convId 后 submitAnswer 使用新 runId |
| F-5 | 记忆审批并发：同一 source_memory_id 两次进入 pending | Low | 两轮 extract_and_store 触发相同晋升候选 | DB UNIQUE(user_id, source_memory_id)；INSERT OR IGNORE；单测验证幂等 |
| F-6 | 刷新页面后 QuestionCard 消失，run 永远等待 | High | run_state 快照未包含 pending_question | `_ActiveRun.state["pending_question"]` 写入；`run_state` endpoint 返回；前端 resumeActiveRun 恢复 QuestionCard |
| F-7 | hitl_callback 未传入工具 context（SDK 模式无 run_id） | Medium | SDK 模式下 `_execute_run` 不存在 | `ask_user` 检查 `_context.get("hitl_callback")` 为 None 时返回 `{"error": "no_run_context"}` 而非抛异常 |

---

## 推荐与决策

### 推荐架构：Callback 注入模式

**核心选择**：`ask_user` 工具通过 `_context["hitl_callback"]` 获取暂停能力，而非直接导入 `_ACTIVE_RUNS`。

**为什么选它**：
- **可测试性**：单测可直接 mock `hitl_callback`，无需启动 FastAPI
- **无循环依赖**：`builtin.py` 不导入 `api.py`（否则 api.py → pipeline → builtin → api.py 成环）
- **SDK 兼容**：SDK 模式下 callback 为 None，工具降级返回 error 而非崩溃
- **与 `_context` 模式一致**：项目已有 `session_id`/`user_id` 通过 context 注入的模式

**为什么不直接用 `before_tool` hook 做暂停**：
- Hook 是同步拦截（`async def` Hook 在链式执行中需全部 await 完才能继续）
- `ShortCircuit` 返回值含义是"跳过工具执行"，不适合"暂停等用户回答后继续"
- 用 hook 实现暂停需要 hook 内 `await Future`，会阻塞整个 hook 链，难以超时控制

**影响范围**：
- 跨模块（chat、memory、tools、shared）
- 新增公开 HTTP 端点（`/answer`、`/pending-approvals`）
- DB schema 新增一张表

**diff 预算**：15 文件 / ~965 行

**下一步实施边界（building 接下来做什么）**：

按 S → B 顺序实施，切片 1 优先（Walking Skeleton）。Building 在以下情况停下报告：
- 触及 Non-Goals（MCP 工具拦截、Redis 迁移）
- 超出 diff 预算
- 发现 `tool_loop.py` 工具执行路径与本设计不符

**重评估条件**：
- 如发现 `hitl_callback` 注入路径在 pipeline.py 中被截断（工具 context 不经过 api.py）
- 如 memory 模块已有独立 api.py（需确认路由注册位置）

---

## 决策记录

**日期**：2026-06-17

**上下文与问题**：  
AstraCoreAI 目前所有工具直接执行，无 approval gate。记忆晋升是后台自动行为，用户不知情。`专业度评估与优化路线.md` 的 P0 #3 指出这是"不补就称不上专业"的缺口。

**Decision Drivers**：
- AI 主动询问体验对标 Claude Code（全开）
- 记忆审批不阻塞对话（晋升时异步审批）
- 危险工具以元数据声明（而非正则/LLM 二次审查，延迟可控）
- 一次性完整交付

**决策结果**：  
Callback 注入模式 + PendingQuestion 数据类 + `asyncio.Future` 暂停 + DB pending 表异步审批。

**正面后果**：
- 用户对 AI 行为有可见控制权
- 记忆晋升不再是黑盒
- 为后续 "policy-as-code" guardrail 打地基

**负面后果**：
- 单 worker 限制被放大（awaiting_input 状态在多 worker 下不可路由）
- 实现量较大（~965 行），需完整联调

**重评估条件**：计划做多 worker 部署时，需先迁移到 Redis 再扩展 HITL。

---

## 附：文件影响清单（building 参考）

| 文件 | 改动类型 | 关键改动 |
|---|---|---|
| `src/astracore/shared/ports/llm.py` | S: 新增枚举值 | `USER_INPUT_REQUIRED`, `USER_INPUT_RESOLVED` |
| `src/astracore/shared/domain/hitl.py` | S: 新建文件 | `PendingQuestion(BaseModel)` |
| `src/astracore/modules/chat/api.py` | S+B: 扩展 `_ActiveRun`，新增端点 | `_hitl_futures`, `pending_question`, `POST /answer`, context 注入 |
| `src/astracore/modules/tools/builtin.py` | B: 新增工具，修改 save_memory/delete_memory | `ask_user` 工具，save_memory 确认逻辑 |
| `src/astracore/modules/chat/application/tool_loop.py` | B: 切片 2，requires_confirmation 拦截 | 工具执行前检查字段 |
| `src/astracore/modules/memory/application/engine.py` | B: 切片 3，_promote_one 改写 | 写 pending 表而非直接晋升 |
| `src/astracore/infrastructure/db/models.py` | S: 新增表 | `MemoryPendingPromotionRow` |
| `src/astracore/infrastructure/memory/store.py` | B: 切片 3 | `create_pending_promotion`, `list_pending`, `apply_promotion` |
| `src/astracore/modules/memory/api.py` | B: 切片 3（确认是否已存在） | `GET/POST /pending-approvals` |
| `src/astracore/modules/chat/pipeline.py` 或 `prompt_utils.py` | B: 切片 4 | System Prompt ask_user 指南注入 |
| `config/config.yaml` | S+B | `hitl` 节 |
| `frontend/src/features/chat/services/chatService.ts` | B | 新 SSE 事件 + `submitAnswer` |
| `frontend/src/features/chat/store/chatStore.ts` | B | `pendingQuestion` state + `submitAnswer` action |
| `frontend/src/features/chat/components/QuestionCard.tsx` | B: 新建 | 选项卡组件 |
| `frontend/src/features/chat/components/ChatMain.tsx` | B | 渲染 QuestionCard，Sender 禁用逻辑 |
| `frontend/src/layouts/AppShell.tsx` | B: 切片 3 | 审批 Badge |
| `frontend/src/features/memory/pages/PendingApprovalsPage.tsx` | B: 切片 3，新建 | 审批页 |
