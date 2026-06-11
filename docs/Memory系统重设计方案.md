# Memory 系统重设计方案（Context Engineering 架构）

> 本文档描述对现有 Memory 系统的根本性重设计，替代原 `Memory系统设计方案.md` 中已过时的实现路线。核心变化：从"统一注入 system prompt"升级为"分层注入 + 语义检索 + LLM 晋升"的 Context Engineering 架构。

---

## 1. 现有系统的根本性缺陷

读完现有代码（`engine.py` 752 行），问题比文档描述的深：

| 缺陷 | 具体表现 |
|------|---------|
| **注入位置单一** | `build_memory_context()` 把所有 scope 的记忆统一拼成文本块追加到 system prompt 末尾，用户偏好和本轮会话状态竞争同一槽位 |
| **检索不懂语义** | `_rank_memories()` 用 `re.split(r"\W+")` 做关键词匹配，SQL 层用 `ILIKE`；问"游戏状态板"，记忆里写"游戏棋盘"，完全召不回来 |
| **每轮只提取一条** | `_extract_with_llm` 返回单个 `MemoryDecision`，同一轮对话可能包含多个可提取事实，全部丢弃 |
| **use_count 字段废弃** | 字段记录了访问次数，但从未驱动任何决策；高价值 session 记忆无法晋升，低价值记忆永不衰减 |

---

## 2. 设计目标

重设计的目标不是做加法，而是从架构层面正确解决以下问题：

1. **右记忆、右位置**：稳定的用户画像进 system prompt（Tier-1），动态的会话上下文进对话历史（Tier-2）
2. **语义检索**：用向量相似度替代关键词匹配，召回率从"词面相同"提升到"语义相近"
3. **多条提取**：一轮对话可产生 0-N 条结构化记忆，不再是"至多一条"
4. **生命周期闭环**：高价值 session 记忆经 LLM 判断后晋升到 user scope；低价值记忆自然衰减

---

## 3. 核心架构：两级注入

这是整个重设计的轴心，参考 Mem0、LangMem 的 Context Engineering 实践。

### 3.1 Tier-1：稳定用户画像（→ System Prompt）

```
┌─────────────────────────────────────┐
│          System Prompt              │
│  ├── AI Identity（角色定义）         │
│  ├── Skills（技能清单）              │
│  └── [Tier-1] User Profile          │ ← user + global scope
│      • preference（用户偏好）        │   全量加载，无需检索
│      • constraint（行为约束）        │
│      • decision（已确认决策）        │
│      • procedure（AI 行为规范）      │
└─────────────────────────────────────┘
```

**特征**：
- 来源：`scope = user | global`，`status = active`
- 加载方式：SQL 全量查询（条目少，通常 < 50 条）
- 字符预算：800 字符（超出按 importance 截断）
- 频率：每次对话的 `prepare()` 阶段执行一次，结果写入 `ChatContext.system_prompt`

### 3.2 Tier-2：动态会话上下文（→ role=assistant 合成消息）

```
[...对话历史，最后一条是 assistant]

user:      "[记忆同步]"              ← 合成桥接消息，不持久化
assistant: "【记忆快照】\n            ← Tier-2 内容，不持久化
            ### 当前会话状态
            - 正在实现 MemoryEngine 重设计
            ### 项目约束
            - 使用 Python 3.11+, FastAPI"

user:      <真实用户消息>             ← 持久化
```

**特征**：
- 来源：`scope = session | project`，`status = active`
- 检索方式：Chroma 向量相似度（按当前消息语义检索）
- 字符预算：1200 字符（top-8 相关记忆）
- 频率：每轮对话调用前实时执行

**合成消息规则**：
- `metadata = {"synthetic": True}`，在 `_prepare_for_save()` 中过滤，**绝不写入持久化历史**
- 对话历史结尾永远是 assistant turn，因此桥接消息序列（`user:[sync] → assistant:[recall]`）可以无缝接在历史末尾，满足 Anthropic API 的 user/assistant 交替约束
- 若对话历史为空（全新会话），则直接以 `assistant:[recall]` 开头（Anthropic API 支持首条为 assistant）

---

## 4. Memory Type 扩展

在原有 8 种类型基础上新增：

| type | 说明 | 天然归属 |
|------|------|---------|
| `fact` | 稳定事实 | Tier-1 / Tier-2 |
| `preference` | 用户偏好 | Tier-1 |
| `decision` | 已确认决策 | Tier-1 / Tier-2 |
| `constraint` | 约束或禁止事项 | Tier-1 |
| `state` | 当前状态 | Tier-2 |
| `plan` | 后续计划 | Tier-2 |
| `summary` | 阶段摘要 | Tier-2 |
| `lesson` | 经验教训 | Tier-1 / Tier-2 |
| **`procedure`** | **AI 行为规范**（新增）| **Tier-1** |

`procedure` 与 `preference` 的区别：
- `preference`：描述用户特征（"用户喜欢简洁中文回答"）
- `procedure`：描述 AI 的行为规范（"询问代码时先确认语言版本"）

---

## 5. 向量检索层：MemoryVectorAdapter

### 5.1 设计原则

- Chroma 是索引，SQLite 是 source of truth
- 记忆的增删改必须保持两者同步
- 向量检索仅用于 Tier-2（session/project scope），Tier-1 全量加载无需向量

### 5.2 Collection 设计

```
collection: "astracore_memory"
model:      SentenceTransformerEmbeddingFunction("all-MiniLM-L6-v2")
distance:   cosine
```

每条记忆的 document：`f"{memory.subject}: {memory.content}"`（拼接主题和正文，提升召回）

metadata 字段：

```python
{
    "memory_id":   str,   # structured_memory.id
    "user_id":     str,
    "scope":       str,   # session | project | user | global
    "session_id":  str | None,
    "project_id":  str | None,
    "type":        str,
    "importance":  int,
    "locked":      bool,
    "status":      str,   # active | stale | archived | rejected
}
```

### 5.3 同步策略

```
create_memory()  →  写 SQLite  →  upsert Chroma
update_memory()  →  写 SQLite  →  upsert Chroma
delete_memory()  →  删 SQLite  →  delete Chroma
archive/stale    →  写 SQLite  →  upsert Chroma（元数据更新，不删除向量）
```

### 5.4 Tier-2 检索示例

```python
results = collection.query(
    query_texts=[current_message],
    n_results=8,
    where={
        "$and": [
            {"user_id":  {"$eq": user_id}},
            {"scope":    {"$in": ["session", "project"]}},
            {"status":   {"$eq": "active"}},
            {"session_id": {"$eq": str(session_id)}},  # 按会话隔离
        ]
    }
)
```

### 5.5 降级策略

若 chromadb 未安装或 Chroma 服务不可用：

```python
class MemoryVectorAdapter:
    _available: bool = False  # 初始化失败时置 False

    async def query(self, ...) -> list[str]:
        if not self._available:
            return []  # 调用方回退到 SQL ILIKE 检索
```

降级不影响系统正常运行，只是 Tier-2 召回质量下降。

---

## 6. 批量记忆提取

### 6.1 现有问题

`_extract_with_llm` 要求 LLM 输出单个 `_MemoryDecision`，一轮对话中"用户说自己是 Python 老手"和"决定用 FastAPI"是两条独立记忆，现在全部丢弃。

### 6.2 新 Schema

```python
class _MemoryDecision(BaseModel):
    action: Literal["create", "update", "ignore"]
    scope: MemoryScope
    type: MemoryType
    content: str
    subject: str
    summary: str
    importance: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    target_memory_id: str | None = None  # action=update 时填写

class _ExtractionBatch(BaseModel):
    memories: list[_MemoryDecision]  # 0-N 条
```

LLM 提示词要求输出 `_ExtractionBatch`，明确允许输出零条（`"memories": []`）。

### 6.3 写入流程

```
一轮对话结束
    └→ _extract_with_llm()          → _ExtractionBatch
         └→ for each _MemoryDecision:
               action=create  → _consolidate_candidate() → create_memory() → upsert Chroma
               action=update  → 校验 target_id 归属     → update_memory() → upsert Chroma
               action=ignore  → 跳过
```

---

## 7. LLM 记忆晋升（Session → User）

### 7.1 触发条件（启发式预过滤）

```python
should_evaluate = (
    memory.scope == MemoryScope.SESSION
    and memory.status == MemoryStatus.ACTIVE
    and (
        memory.use_count >= 5
        or (memory.importance >= 4 and memory.use_count >= 3)
        or memory.locked
    )
)
```

只有通过启发式过滤的记忆才进入 LLM 评估，避免每条记忆都调 LLM。

### 7.2 LLM 判断输出

```python
class _PromotionDecision(BaseModel):
    action: Literal["promote_user", "promote_project", "keep", "archive"]
    reason: str
    new_importance: int = Field(default=3, ge=1, le=5)  # 晋升时可能调整
```

给 LLM 的上下文包括：记忆内容/类型/use_count/创建时间，以及该会话最近几条对话（判断是否真的有跨会话价值）。

### 7.3 晋升动作

| action | 处理 |
|--------|------|
| `promote_user` | 创建新 user scope 记忆；原 session 记忆 status → ARCHIVED；Chroma 同步更新 |
| `promote_project` | 创建新 project scope 记忆；原 session 记忆 status → ARCHIVED |
| `keep` | 不操作 |
| `archive` | 原记忆 status → ARCHIVED（认为已过时） |

晋升本质是**复制 + 归档**，不删除原始记忆，保留溯源链路。

### 7.4 触发时机

晋升评估在 `_after_run()` 后台任务中执行（与提取并行），不阻塞对话响应。

---

## 8. 新组件架构

### 8.1 MemoryEngine 方法重组

| 方法 | 替代 / 新增 | 说明 |
|------|------------|------|
| `build_profile_context()` | 替代 `build_memory_context()` | Tier-1，SQL 全量，返回 str |
| `build_turn_context()` | **新增** | Tier-2，Chroma 检索，返回 str |
| `create_memory()` | 保留并增强 | 同步写 Chroma |
| `update_memory()` | 保留并增强 | 同步写 Chroma |
| `delete_memory()` | 保留并增强 | 同步删 Chroma |
| `_extract_with_llm()` | 改为批量 | 返回 `_ExtractionBatch` |
| `_consolidate_candidate()` | 保留 | 去重/合并逻辑 |
| `_evaluate_and_promote()` | **新增** | 启发式过滤 + LLM 晋升 |
| `compact_session_memories()` | 保留 | 压缩逻辑基本不变 |

### 8.2 MemoryVectorAdapter（新建）

```
src/astracore/infrastructure/memory/vector.py

class MemoryVectorAdapter:
    - __init__(db_path: str)
    - _get_collection() -> Collection    # lazy init + 降级
    - async upsert(memory: StructuredMemory) -> None
    - async delete(memory_id: str) -> None
    - async query(text, *, user_id, scope_filter, session_id, project_id, n_results) -> list[str]
```

所有同步 Chroma 调用通过 `asyncio.run_in_executor` 包裹，不阻塞事件循环。

### 8.3 ChatPipeline 变更

**新增方法**：

```python
def _build_turn_recall_messages(ctx: ChatContext) -> list[Message]:
    """构造 Tier-2 合成消息对（桥接 user + 记忆 assistant）"""
    if not ctx.turn_context:
        return []
    return [
        Message(role=MessageRole.USER,      content="[记忆同步]",    metadata={"synthetic": True}),
        Message(role=MessageRole.ASSISTANT,  content=ctx.turn_context, metadata={"synthetic": True}),
    ]
```

**`_prepare_for_save()` 增加过滤**：

```python
messages_to_save = [
    m for m in messages
    if not m.metadata.get("synthetic")  # 合成消息不持久化
]
```

**`stream()` 中插入 Tier-2**：

```python
recall_messages = _build_turn_recall_messages(ctx)
# 插入位置：存储历史末尾、真实用户消息之前
all_messages = stored_history + recall_messages + [user_message]
```

### 8.4 ChatContext 新增字段

```python
@dataclass(frozen=True)
class ChatContext:
    ...
    turn_context: str = field(default="")
    """Tier-2 记忆内容，由 MemoryEngine.build_turn_context() 生成；
    空字符串表示无相关 session/project 记忆。"""
```

---

## 9. 数据流（单轮对话）

```
用户消息到达
    │
    ▼
ChatPipeline.prepare()
    ├── MemoryEngine.build_profile_context()   → ChatContext.system_prompt (Tier-1)
    └── MemoryEngine.build_turn_context()      → ChatContext.turn_context  (Tier-2, Chroma)
    │
    ▼
ChatPipeline.stream()
    ├── _build_system_prompt(ctx)              → Tier-1 注入 system prompt
    ├── _build_turn_recall_messages(ctx)       → Tier-2 合成消息对
    └── 发送给 LLM
    │
    ▼
对话完成
    │
    ├── _prepare_for_save()                    → 过滤 synthetic 消息
    ├── MemoryEngine._extract_with_llm()       → _ExtractionBatch (0-N 条)
    │       └── 每条 → _consolidate_candidate() → create/update_memory() → upsert Chroma
    └── MemoryEngine._evaluate_and_promote()   → 启发式过滤 → LLM → 晋升/归档
```

---

## 10. 改造文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `modules/memory/domain.py` | 修改 | 新增 `MemoryType.PROCEDURE`；`MemoryStatus` 新增 `PROMOTED`（可选，用于溯源） |
| `infrastructure/memory/vector.py` | **新建** | `MemoryVectorAdapter`，Chroma 适配层 |
| `infrastructure/memory/__init__.py` | 修改 | 导出 `MemoryVectorAdapter` |
| `modules/memory/application/engine.py` | 重构 | 拆 `build_memory_context` → `build_profile_context` + `build_turn_context`；提取改批量；新增晋升流程 |
| `modules/memory/api.py` | 修改 | Literal 枚举加入 `procedure`、`promoted` |
| `modules/chat/domain/chat_context.py` | 修改 | 新增 `turn_context: str = ""` 字段 |
| `modules/chat/pipeline.py` | 修改 | `_build_turn_recall_messages()`；`_prepare_for_save()` 加过滤；`stream()` 插入 recall 消息对 |
| `modules/chat/api.py` | 修改 | 修复 `user_id="default"` bug；传入 `MemoryVectorAdapter` |
| `modules/chat/conversations_api.py` | 修改 | 传入 `MemoryVectorAdapter`（记忆删除同步） |
| `sdk/client.py` | 修改 | 初始化 `MemoryVectorAdapter` |
| `features/memory/pages/MemoryPage.tsx` | 修改 | 类型下拉加入 `procedure` |
| `tests/service/test_memory_engine.py` | 修改 | 更新 API 调用，适配批量提取 |

---

## 11. 注入格式

### Tier-1（System Prompt 片段）

```markdown
## 用户画像与行为规范

以下来自长期记忆，请严格遵守 Constraints 和 Procedures；如用户明确纠正，以最新消息为准。

### 行为规范
- 询问代码问题时，先确认用户的语言版本和运行环境

### 用户偏好
- 用户偏好直接、务实的工程回答，不喜欢过度流程

### 已确认约束
- 不要在未确认的情况下修改用户锁定的项目记忆
```

### Tier-2（合成 Assistant 消息）

```markdown
【记忆快照】

### 当前会话状态
- 正在重设计 Memory Engine，已确认采用 Tier-1/Tier-2 分层注入架构
- 选型：Chroma 向量检索（astracore_memory collection）

### 项目约束
- 使用 Python 3.11+、FastAPI、SQLAlchemy async
- 不考虑向前兼容
```

---

## 12. 验证方案

### 12.1 单元测试

- `test_memory_engine.py`：批量提取（0/1/N 条）、晋升决策、build_profile_context/build_turn_context 输出格式
- `test_memory_vector_adapter.py`（新建）：upsert/query/delete/降级

### 12.2 集成验证

```bash
# 启动服务
hatch run serve

# 注册并登录
curl -X POST http://localhost:8000/api/v1/auth/register -d '{"username":"test","password":"test123"}'
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login -d 'username=test&password=test123' | jq -r .access_token)

# 对话（产生记忆）
curl -H "Authorization: Bearer $TOKEN" -X POST http://localhost:8000/api/v1/chat/...

# 验证 Tier-1 注入：user scope 记忆应出现在 system prompt
# 验证 Tier-2 检索：本轮消息语义相关的 session 记忆应出现在合成 assistant 消息

# 验证批量提取
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/memory/

# 验证晋升：构造 use_count >= 5 的 session 记忆，触发对话后应晋升为 user scope
```

### 12.3 语义检索验证

- 构造一条内容为"游戏棋盘状态"的 session 记忆
- 发送消息"游戏状态板现在是什么"
- 验证 Tier-2 检索命中该记忆（词面不同但语义相近）

---

## 13. 风险与降级

| 风险 | 降级策略 |
|------|---------|
| Chroma 不可用 | `MemoryVectorAdapter._available=False`，Tier-2 回退到 SQL ILIKE（现有逻辑），Tier-1 不受影响 |
| LLM 晋升调用超时 | 后台任务有独立超时，失败静默跳过，记忆仍保留 session scope |
| 批量提取 LLM 输出格式错误 | Pydantic 校验失败，记录 warning，跳过本轮提取 |
| 合成消息意外持久化 | `_prepare_for_save()` 中以 `metadata.synthetic=True` 过滤，双重保障 |

---

## 14. 与现有方案的兼容性

本方案**不追求向前兼容**：

- `build_memory_context()` 直接删除，调用方改为 `build_profile_context()` + `build_turn_context()`
- `_MemoryDecision` 改为批量输出，现有测试需更新
- `UserSettingsRow` 中与 memory 无关的变更（多用户支持）独立处理，不耦合本方案

SQLite 数据库结构无需 migration（`procedure`/`promoted` 只是 enum 值变化，存储为 TEXT 字段）。Chroma collection 首次启动时自动创建。
