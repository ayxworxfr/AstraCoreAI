---
title: AstraCoreAI 记忆系统（Context Engineering）
category: astracore
tags: [Memory, ContextEngineering, Tier1, Tier2, Chroma, 向量检索, 记忆抽取, 记忆晋升, MemoryEngine, HITL, Prompt注入防御]
related: [astracore/intro, astracore/chat_pipeline]
---

# AstraCoreAI 记忆系统（Context Engineering）

AstraCoreAI 记忆系统采用 **Context Engineering** 架构：按信息稳定性分层注入，让正确的记忆出现在正确的位置，而不是一股脑堆进 system prompt。

---

## 核心架构：两级注入

```
Tier-1  user/global scope  → System Prompt（稳定用户画像，每次请求注入一次）
Tier-2  session/project scope  → 合成消息对（动态会话上下文，每轮实时语义检索）
```

### Tier-1：用户画像注入 System Prompt

- **来源**：`scope = user | global`，`status = active`
- **加载**：SQL 全量查询（< 50 条），无需向量检索
- **字符预算**：800 字符，超出按 importance 降序截断
- **时机**：`ChatPipeline.prepare()` → `MemoryEngine.build_profile_context()`
- **注入位置**：system prompt 末尾

Tier-1 注入格式示例：

```
## 用户画像与行为规范

以下来自长期记忆，请严格遵守 Constraints 和 Procedures；如用户明确纠正，以最新消息为准。

### 行为规范
- 询问代码问题时，先确认用户的语言版本和运行环境

### 用户偏好
- 用户偏好直接、务实的工程回答
```

### Tier-2：动态上下文合成消息对

- **来源**：`scope = session | project`，`status = active`
- **检索**：Chroma 向量语义检索；Chroma 不可用或返回空时自动降级到 SQL 全量 + Python 关键词排序
- **字符预算**：1200 字符（top-6 session + top-4 project）
- **时机**：每轮对话 `prepare()` 时调用 `build_turn_context()`
- **注入位置**：存储历史末尾、真实用户消息之前，以合成消息对形式注入
- **Prompt 注入防御**：`turn_context` 内容经过 `wrap_external(source="memory")` 包裹，防止记忆内容被恶意利用于注入攻击

合成消息对格式（不持久化，metadata={"synthetic":True}）：

```
user:      "[记忆同步]"
assistant: "【记忆快照】
            ### 当前会话状态
            - 游戏：谁是卧底；平民词：薯片；卧底：小刚（虾条）
            ### 项目上下文
            - 使用 Python 3.11+, FastAPI"
```

---

## 记忆类型（MemoryType）

| type | 说明 | 天然层级 |
|------|------|---------|
| `fact` | 稳定事实 | Tier-1 / Tier-2 |
| `preference` | 用户偏好特征（描述用户） | Tier-1 |
| `decision` | 已确认决策 | Tier-1 / Tier-2 |
| `constraint` | 约束或禁止事项 | Tier-1 |
| `state` | 当前状态（会话内易变，如游戏状态） | Tier-2 |
| `plan` | 后续计划 | Tier-2 |
| `summary` | 阶段摘要 | Tier-2 |
| `lesson` | 经验教训 | Tier-1 / Tier-2 |
| `procedure` | AI 行为规范（描述 AI 应该怎么做） | Tier-1 |

---

## 记忆范围（MemoryScope）

| scope | 说明 | 注入层级 |
|-------|------|---------|
| `session` | 绑定到某个对话（conversation_id） | Tier-2 |
| `project` | 绑定到某个项目（project_id） | Tier-2 |
| `user` | 用户级别，跨所有对话 | Tier-1 |
| `global` | 全局共享 | Tier-1 |

---

## 向量检索层（MemoryVectorAdapter）

- **文件**：`src/astracore/infrastructure/memory/vector.py`
- **Collection**：`astracore_memory`
- **Embedding**：Chroma ONNX `all-MiniLM-L6-v2`
- **距离**：cosine

每条记忆文档内容：`f"{subject}: {content}"` — 主题与正文拼接，提升召回率

**同步策略**：

```
create_memory()  →  写 SQLite  →  upsert Chroma（仅 status=ACTIVE）
update_memory()  →  写 SQLite  →  upsert Chroma
delete_memory()  →  删 SQLite  →  delete Chroma
```

**降级**：chromadb 未安装或初始化失败时，`_available=False`，Tier-2 自动回退到 SQL 全量 + 关键词排序，系统正常运行不中断。

---

## 批量记忆提取

每轮对话完成后（`_after_run` 后台任务），调用 `extract_and_store()`：

1. LLM 分析本轮 user_message + assistant_content
2. 输出 `_ExtractionBatch(memories: list[_MemoryItemDecision])`，允许 0-N 条
3. 每条决策执行：
   - `action=create`：写入 SQLite + upsert Chroma
   - `action=update`：更新指定 `target_memory_id` 的记忆
   - `action=ignore`：跳过

---

## LLM 记忆晋升（Session → User/Project）

高价值 session 记忆经启发式过滤后由 LLM 判断是否晋升：

启发式条件（任一满足）：
- `use_count >= 5`
- `importance >= 4 AND use_count >= 3`
- `locked == True`

LLM 可输出：`promote_user`（升级为 user scope）、`promote_project`（升级为 project scope）、`keep`（保留）、`archive`（归档）

晋升 = 复制 + 归档原记忆，保留溯源链路。

### HITL 记忆晋升审批

当系统配置 `require_memory_promotion_approval=true` 时，LLM 决定晋升后不会立即执行，而是：

1. 创建 `pending_promotion` 状态的记忆记录，等待用户同意
2. 通过 SSE 发送审批请求，前端 QuestionCard 展示晋升详情（原始记忆、目标 scope、理由）
3. 用户确认 → 执行晋升复制；用户拒绝 → 删除 pending 记录
4. 超时（默认 60s）→ 自动取消晋升，记录保留为 session scope

---

## 手动管理记忆

通过前端 Memory 管理页（`/memory`）或 REST API 手动创建记忆：

```
GET    /api/v1/memory/           查询记忆列表（支持 scope/session_id/project_id/q 过滤）
POST   /api/v1/memory/           创建记忆
PATCH  /api/v1/memory/{id}       更新记忆
DELETE /api/v1/memory/{id}       删除记忆
```

**注意**：创建 `scope=session` 的记忆时，必须在 `session_id` 字段填写对应对话的 UUID，否则该记忆无法被 Tier-2 检索到。

---

## 单轮数据流

```
用户消息 → ChatPipeline.prepare()
    ├── build_profile_context()  → system prompt（Tier-1）
    └── build_turn_context()     → turn_context（Tier-2，Chroma + SQL fallback）
                                   ↳ wrap_external(source="memory") 包裹防注入

ChatPipeline.stream()
    ├── maybe_compact()          → 检测 token 用量，按需压缩历史
    ├── stored_history + [user:记忆同步, assistant:快照] + [user:真实消息] → LLM
    └── _prepare_for_save()      → 过滤 synthetic=True，仅持久化真实历史

_after_run()（后台）
    ├── extract_and_store()      → 批量提取 0-N 条 → SQLite + Chroma
    └── _evaluate_and_promote()  → 启发式 + LLM → 晋升/归档（可选 HITL 审批）
```

---

## 关键文件

| 文件 | 职责 |
|------|------|
| `modules/memory/domain.py` | MemoryType、MemoryScope、MemoryStatus、StructuredMemory |
| `modules/memory/application/engine.py` | MemoryEngine：两级注入、提取、晋升、压缩 |
| `modules/memory/api.py` | FastAPI CRUD，同步写 Chroma |
| `infrastructure/memory/store.py` | SQLMemoryStore（SQLAlchemy async） |
| `infrastructure/memory/vector.py` | MemoryVectorAdapter（Chroma，graceful degradation） |
| `modules/chat/pipeline.py` | 注入合成消息对，过滤 synthetic 消息，wrap_external 包裹 |

---

## 配置

```yaml
retrieval:
  persist_directory: ./chroma_db
  embedding_model: all-MiniLM-L6-v2       # Chroma ONNX 默认模型

memory:
  db_url: sqlite+aiosqlite:///./astracore.db
  require_memory_promotion_approval: false  # 设为 true 启用晋升 HITL 审批
```
