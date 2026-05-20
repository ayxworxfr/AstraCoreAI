# AstraCoreAI Memory 系统设计方案

## 1. 目标

Memory 系统的目标不是简单扩大上下文窗口，而是让 AstraCoreAI 具备长期工作能力：

- 记住当前对话中的目标、约束、阶段状态和未完成事项。
- 记住用户跨对话的稳定偏好和工作方式。
- 记住不同项目的结构、状态、决策和常见坑。
- 在新一轮对话前主动检索相关记忆，减少重复解释，降低长对话遗忘。
- 允许用户查看、修正、删除和锁定记忆，避免错误记忆长期污染。

该系统应成为框架级能力，同时服务 SDK、HTTP Service 和前端 SPA。

> **当前实现同步（2026-05）**：结构化 Memory 已落地，核心实现包括 `core/domain/memory.py`、`core/application/memory_engine.py`、`core/ports/memory_store.py`、`adapters/memory/store.py`、`service/api/memory.py` 和 `frontend/src/pages/MemoryPage.tsx`。默认持久化写入 SQLite `astracore.db`；Redis 仍用于短期会话记忆热路径，失败时降级到 SQLite。

## 2. 设计原则

1. **分层隔离**
   不同作用域的记忆必须隔离。用户偏好、项目状态、会话临时计划不能混在一起。

2. **结构化存储**
   Memory 不是纯文本日志，而是带 scope、type、importance、confidence、source 等字段的结构化对象。

3. **可追溯**
   每条自动生成的记忆都应能追踪到来源 run、会话和时间，便于解释“为什么 AI 记得这个”。

4. **可纠错**
   用户可以编辑、删除、锁定、归档记忆。被用户锁定的记忆不能被后台自动覆盖。

5. **谨慎注入**
   检索到的记忆必须经过预算控制和类型排序后注入 prompt，不能无脑塞满上下文。

6. **生命周期闭环**
   Memory 不是只增不减的日志。创建、更新、合并、压缩、归档、删除必须形成闭环，避免长对话和多轮协作导致记忆爆炸。

7. **先可靠，后智能**
   第一版优先保证记忆归属、读写、注入、管理闭环可靠；向量检索、冲突合并和质量评分可以逐步增强。

## 3. Memory Scope

Memory 按作用域分为四类。

### 3.1 Session Memory

绑定单个对话。

适合记录：

- 当前任务目标
- 当前阶段计划
- 本轮对话中确认的约束
- 用户对当前任务的临时要求
- 长对话阶段摘要

示例：

```text
当前对话正在讨论 AstraCoreAI 的 Memory Engine 设计，已确定 project 识别采用混合模式。
```

### 3.2 Project Memory

绑定具体项目。

适合记录：

- 项目目录
- 架构边界
- 运行命令
- 重要设计决策
- 文件结构
- 业务状态
- 常见故障和处理经验

示例：

```text
AstraCoreAI 的聊天历史分页以 chat_runs 为历史数据源，chat_sessions 只保留模型上下文短期记忆。
```

小说项目示例：

```text
星际破防指南卷一《观察期》已完结，卷二进入《贸易战》，核心冲突转向人类贸易网络被外部文明挑战。
```

### 3.3 User Memory

绑定用户。

适合记录：

- 长期工作偏好
- 沟通习惯
- 常用技术栈
- 常用项目路径
- 对 AI 行为的稳定要求

示例：

```text
用户偏好直接、务实的工程回答，不喜欢为了简单问题引入过度流程。
```

### 3.4 Global Memory

绑定系统或团队。

适合记录：

- 框架级稳定规则
- 通用工具经验
- 可复用排障经验
- 团队约定

示例：

```text
MCP edit_file 对长中文段落 exact match 脆弱，编辑 Markdown 状态文件时应优先按标题块替换。
```

## 4. Memory Type

每条 Memory 必须有明确类型。

| type | 说明 | 示例 |
|------|------|------|
| `fact` | 稳定事实 | 项目使用 FastAPI + React |
| `preference` | 用户偏好 | 用户希望回答简洁直接 |
| `decision` | 已确认决策 | 项目识别采用混合模式 |
| `constraint` | 约束或禁止事项 | 不要覆盖用户锁定记忆 |
| `state` | 当前状态 | 某小说卷一已完结 |
| `plan` | 后续计划 | 下一步设计 Memory API |
| `summary` | 阶段摘要 | 一段长对话的压缩总结 |
| `lesson` | 经验教训 | 长 oldText 替换容易失败 |

注入优先级通常为：

```text
constraint > decision > state > preference > plan > fact > lesson > summary
```

实际排序还要结合相关性、重要度、更新时间和用户锁定状态。

## 5. Project 识别方案

采用混合模式：**默认自动识别，用户可手动修正和锁定**。

### 5.1 自动识别来源

系统可以从以下信号判断当前 project：

1. **当前 workspace**
   例如当前工作目录是 `D:\project\study\AstraCoreAI`，则默认 project 为 `AstraCoreAI`。

2. **文件路径**
   如果对话或工具调用涉及 `D:\project\StoryVault\星际破防指南\...`，则候选 project 为 `星际破防指南`。

3. **会话绑定**
   如果 conversation 已绑定 project，则优先使用已绑定 project。

4. **LLM 判断**
   当没有明确路径，但用户提到“秦落那本小说”“继续这个框架设计”等语义线索时，可由 LLM 判断候选 project。

### 5.2 用户修正

前端应允许用户在会话中看到当前 project，并手动切换：

```text
当前项目：AstraCoreAI
```

如果用户切换并锁定：

```text
当前对话绑定到：星际破防指南
```

后续该对话不再自动改 project，除非用户手动解除锁定。

### 5.3 冲突处理

如果一个对话同时出现多个 project 信号：

- 已锁定 project 优先。
- 用户手动选择优先。
- 文件路径信号优先于 LLM 判断。
- 多个路径项目同时出现时，不自动写入 project memory，只写入 session memory，并提示用户确认归属。

## 6. 数据模型

已引入新的结构化 Memory 表（`structured_memories`），不再只依赖早期 `memory_entries` 的简单 long-term 结构。

### 6.1 memory_entries

核心字段：

```text
id
scope               session | project | user | global
type                fact | preference | decision | constraint | state | plan | summary | lesson
subject             记忆主题，例如 chat-history、novel-state、coding-style
content             记忆正文
summary             可选短摘要
session_id          session scope 使用
conversation_id     来源对话
project_id          project scope 使用
user_id             user scope 使用，单用户部署可使用 default
source_run_id       来源 chat run
importance          1-5
confidence          0-1
status              active | stale | archived | rejected
locked              用户锁定后不允许自动覆盖
created_at
updated_at
last_used_at
use_count
metadata            JSON 扩展字段
```

### 6.2 projects

用于管理 project memory 的边界。

```text
id
name
root_paths          JSON list
description
created_at
updated_at
```

### 6.3 conversation_project_binding

记录会话和 project 的绑定关系。

```text
conversation_id
project_id
locked
source              manual | workspace | path | llm
created_at
updated_at
```

也可以直接把 `project_id/project_locked/project_source` 放入现有 `conversations` 表，第一版更简单。

## 7. 写入流程

Memory 写入分为手动写入和自动提取。自动提取不能采用“抽到就插入”的策略，必须升级为记忆决策流程。

### 7.1 手动写入

用户可以显式告诉 AI：

```text
记住：以后这个项目里的状态文件都用 UTF-8 保存。
```

系统应将其解析为候选 memory，并根据当前 project/session 归属写入。

### 7.2 自动提取

每次 chat run 完成后，后台执行 Memory Extractor：

```text
输入：
- user message
- assistant response
- tool activity
- active skill
- current project
- existing relevant memory

输出：
- candidate memories
- update operations
- conflict candidates
```

Extractor 不直接无脑写库，必须先经过 Consolidator。

### 7.3 记忆决策器

Memory Decision Engine 接收新候选记忆和已有候选记忆，输出明确动作：

| action | 含义 | 处理方式 |
|--------|------|----------|
| `ignore` | 不值得记忆 | 不写库 |
| `create` | 新主题 | 创建一条 active memory |
| `update` | 同一主题的新状态 | 更新已有 memory 的 content、summary、importance、confidence |
| `merge` | 同义重复或信息互补 | 合并 content，增加 use_count，刷新 last_used_at |
| `archive` | 新记忆取代旧记忆 | 新记忆 active，旧记忆 archived 或删除 |
| `conflict` | 与 locked 或高置信记忆冲突 | 不覆盖 locked 记忆，创建冲突候选或等待用户确认 |

决策器必须遵守硬规则：

- LLM 返回的目标 memory id 必须来自系统提供的候选列表，不能凭空生成。
- `locked=true` 的记忆不能被自动覆盖。
- `scope`、`project_id`、`session_id` 必须由系统校验，不能完全信任 LLM。
- 同 `scope + type + subject` 下默认优先 `update` 或 `merge`，不要重复 `create`。

### 7.4 候选记忆检索

写入前先检索已有记忆，避免重复创建：

```text
1. 根据候选 scope 选择过滤条件。
2. session memory 使用 session_id 精确过滤。
3. project memory 使用 project_id 精确过滤。
4. user memory 使用 user_id 精确过滤。
5. 在过滤结果内按 type、subject、content 相似度取候选。
6. 将候选交给 Decision Engine 做 create/update/merge/archive/ignore/conflict 决策。
```

第一版可以先使用规则相似度：

- `scope/type/subject` 完全一致：强候选。
- `subject` 近似或互为包含：候选。
- `content` 规范化后完全一致：直接 merge。
- `content` 高重叠但措辞不同：交给 LLM 决策。

第二版再加入向量相似度或 trigram 检索。

### 7.5 合并与去重

Memory Consolidator 负责：

- 删除低价值候选。
- 合并重复记忆。
- 更新已有记忆的 content、importance、confidence。
- 检测冲突。
- 尊重 locked memory。

示例：

旧记忆：

```text
用户偏好直接修复问题。
```

新候选：

```text
用户不喜欢简单问题被复杂流程拖慢。
```

合并为：

```text
用户偏好直接、务实地解决问题；简单问题不要引入过度流程。
```

### 7.6 长对话压缩

长对话不能无限积累 session memory。系统应设置 active 记忆上限，超过阈值后触发压缩：

```text
触发条件：
- 当前 session active memory 超过 12 条；或
- 当前 session memory 注入预算连续被打满；或
- 同一 subject 下出现 3 条以上相近记忆。

压缩动作：
1. 选取低优先级、旧状态、重复事实和阶段性计划。
2. 生成 1-3 条 summary/state/decision 记忆。
3. 将压缩后的摘要写为 active memory。
4. 被压缩的明细记忆从 DB 删除，或在需要审计时改为 archived。
```

当前产品目标是避免 DB 长期堆积，因此默认策略建议为：

```text
session scope: 压缩后硬删除被摘要覆盖的明细记忆
project/user scope: 默认归档，用户确认后可硬删除
locked memory: 不参与自动压缩删除
```

压缩后的 memory metadata 应记录：

```text
compressed_from_count
compressed_at
source_memory_ids
retention_action: deleted | archived
```

如果 `source_memory_ids` 对应明细已硬删除，该字段仅作为历史说明，不再要求可反查原文。

### 7.7 对话删除清理

删除 conversation 时必须同步清理关联记忆，避免 DB 孤儿数据：

```text
删除 conversation_id = X 时：
1. 删除 chat_runs 中 session_id = X 的运行记录。
2. 删除 chat_sessions 中 session_id = X 的短期上下文。
3. 删除 structured_memories 中 conversation_id = X 的记录。
4. 删除 structured_memories 中 scope=session AND session_id = X 的记录。
5. 删除 conversation_project_bindings 中 conversation_id = X 的绑定。
```

清空全部对话时执行同样的批量清理。project/user/global memory 不因单个对话删除而删除，除非它们的 `conversation_id` 明确指向该对话且策略配置为“删除来源对话时删除自动提取记忆”。

推荐默认策略：

```text
session memory: 对话删除时硬删除
project memory: 对话删除时保留，但清空 conversation_id/source_run_id 或标记 source_deleted=true
user memory: 对话删除时保留，但标记 source_deleted=true
manual memory: 不随对话删除
```

如果用户选择“删除对话并删除由该对话产生的记忆”，则删除所有 `conversation_id = X` 的自动提取 memory。

## 8. 检索流程

在 `ChatPipeline.prepare()` 中加入 MemoryRetriever。

流程：

```text
1. 解析当前 session_id、conversation_id、project_id。
2. 加载 session memory。
3. 加载当前 project memory。
4. 加载 user memory。
5. 根据当前消息做相关性排序。
6. 按 type、importance、confidence、recency 重新排序。
7. 控制总字符预算。
8. 生成 Memory Context 注入 system prompt。
```

第一版可以先做规则检索：

- session memory：取 active 状态，按 importance、updated_at 排序。
- project memory：取当前 project 下 active 状态，按 importance、updated_at 排序。
- user memory：取 active 状态下的 preference、constraint、decision。

第二版再加入向量检索。

### 8.1 读取预算

即使 DB 中保存了大量记忆，也不能全部注入给模型。读取时必须按 scope 和总字符预算截断。

推荐默认预算：

```text
session: 最多 6 条
project: 最多 6 条
user: 最多 4 条
global: 默认 0 条
total_chars: 3000-4000
```

读取优先级：

```text
1. locked constraint / decision
2. 当前 session 的 state / plan / decision
3. 当前 project 的 state / decision / constraint
4. user preference / constraint
5. 高相关 fact / lesson / summary
```

`archived`、`rejected`、被压缩删除的明细记忆不进入 prompt。

## 9. Prompt 注入格式

Memory Context 应结构化注入，避免模型误解。

示例：

```md
## Relevant Memory

以下记忆来自系统长期记忆。请优先遵守 Constraints 和 Decisions；如果用户明确纠正，以用户最新消息为准。

### Constraints
- 不要覆盖用户锁定的项目记忆。

### Current Project State
- AstraCoreAI 的聊天历史分页以 chat_runs 为历史数据源。

### User Preferences
- 用户偏好直接、务实的工程回答。

### Recent Decisions
- Memory 的 project 识别采用混合模式：自动识别，用户可手动修正和锁定。
```

注入规则：

- `constraint` 和 `decision` 优先。
- 低 confidence 的记忆要注明“可能”或不注入。
- `rejected` 和 `archived` 不注入。
- `locked` 记忆优先级提升。

## 10. 前端设计

第一版前端应提供基础可控性，不追求复杂管理后台。

### 10.1 Chat 页面

在 Chat 页面显示当前 project：

```text
当前项目：AstraCoreAI
```

用户可以：

- 切换 project。
- 锁定当前会话 project。
- 查看本会话提取出的 memory。

### 10.2 Memory 页面或抽屉

当前前端已提供独立 Memory 页面（`/memory`），不再只是抽屉候选方案。

提供 Memory 管理能力：

- 按 scope 筛选：Session / Project / User / Global。
- 按 type 筛选。
- 搜索 memory。
- 编辑 content。
- 删除 memory。
- 锁定 memory。
- 查看来源 run。

### 10.3 冲突提示

当系统检测到新旧记忆冲突时，前端显示：

```text
检测到记忆冲突：
旧：卷一仍在 ch059 阶段。
新：卷一《观察期》已完结。

保留旧记忆 / 使用新记忆 / 都保留 / 忽略
```

## 11. API 设计

建议新增 `service/api/memory.py`。

### 11.1 Memory CRUD

```text
GET    /api/v1/memory
POST   /api/v1/memory
PATCH  /api/v1/memory/{memory_id}
DELETE /api/v1/memory/{memory_id}
```

查询参数：

```text
scope
type
project_id
session_id
status
q
```

### 11.2 Project 管理

```text
GET    /api/v1/projects
POST   /api/v1/projects
PATCH  /api/v1/projects/{project_id}
DELETE /api/v1/projects/{project_id}
```

### 11.3 Conversation Project Binding

```text
GET   /api/v1/conversations/{conversation_id}/project
PUT   /api/v1/conversations/{conversation_id}/project
```

## 12. SDK 设计

SDK 应暴露 Memory 能力，供嵌入式使用。

```python
await client.memory.list(scope="project", project_id="...")
await client.memory.create(...)
await client.memory.update(memory_id, ...)
await client.memory.delete(memory_id)
```

Conversation 门面可以支持：

```python
conv = client.conversation(project_id="astracore")
```

## 13. 运行时组件

建议新增模块：

```text
src/astracore/core/application/memory_engine.py
src/astracore/core/domain/memory.py
src/astracore/core/ports/memory_store.py
src/astracore/adapters/memory/store.py
src/astracore/service/api/memory.py
src/astracore/service/api/projects.py
```

职责划分：

- `domain/memory.py`：StructuredMemory、MemoryScope、MemoryType、MemoryStatus。
- `ports/memory_store.py`：MemoryStore 端口。
- `adapters/memory/store.py`：SQLAlchemy 实现。
- `application/memory_engine.py`：提取、合并、检索、注入上下文。
- `service/api/memory.py`：HTTP CRUD。
- `service/api/projects.py`：project 管理。

## 14. 第一阶段交付范围

第一阶段做完整闭环：

1. 新增结构化 memory 数据模型。
2. 新增 project 数据模型或 conversation project 字段。
3. Conversation 支持 project 自动识别、手动绑定和锁定。
4. ChatPipeline.prepare 注入相关 memory。
5. Chat run 完成后自动提取候选 memory。
6. Memory CRUD API（已落地：`/api/v1/memory`）。
7. 前端基础 Memory 管理视图（已落地：`MemoryPage`）。
8. 前端 Chat 页面显示并允许修改当前 project。

第一阶段不强制实现：

- 高级冲突 UI。
- 复杂向量记忆检索。
- Memory 质量评分后台报表。
- 多用户权限系统。

## 15. 后续增强

第二阶段：

- 向量检索 memory。
- 冲突检测和用户确认。
- Memory 合并质量优化。
- 项目自动识别更智能。
- “为什么使用这条记忆”的来源解释。

第三阶段：

- Memory 审计日志。
- Memory 导入导出。
- 团队共享 memory。
- Memory 质量评估。
- 可配置记忆策略。

## 16. 风险与约束

### 16.1 错误记忆污染

风险：AI 自动提取错误信息，后续持续影响回答。

控制：

- 所有记忆可查看、编辑、删除。
- 自动记忆带 confidence。
- 用户锁定记忆不可被自动覆盖。
- 冲突记忆不自动覆盖高置信旧记忆。

### 16.2 Project 归属错误

风险：小说项目记忆进入代码项目，或反过来。

控制：

- 混合识别。
- 文件路径优先于 LLM 判断。
- 用户可手动修正和锁定。
- 多项目信号冲突时降级为 session memory。

### 16.3 Prompt 过载

风险：注入太多 memory，反而干扰模型。

控制：

- Memory Context 有固定预算。
- 按 type 和 importance 排序。
- 低价值 summary 不优先注入。
- 按 session/project/user 分层限额读取，global 默认不注入。
- 长对话触发 session memory 压缩，压缩后删除或归档明细记忆。

### 16.4 记忆爆炸

风险：长对话或高频使用后，自动提取持续创建重复 memory，导致 DB 膨胀和检索质量下降。

控制：

- 写入前先按 session_id/project_id/user_id 精确过滤候选。
- 同 scope/type/subject 下优先 update 或 merge，不重复 create。
- session active memory 设置上限，超过阈值自动压缩。
- 压缩后的 session 明细默认从 DB 硬删除。
- 对话删除时同步删除 session memory 和相关绑定。

### 16.5 隐私和可控性

风险：用户不知道系统记住了什么。

控制：

- 前端显示 memory。
- 可删除、锁定、归档。
- 自动记忆保留来源。

## 17. 推荐路线

采用“产品级 Memory Engine”的路线，但分阶段落地。

第一版重点不是做最复杂的智能检索，而是把下面四件事做扎实：

1. Memory 结构化。
2. Project 归属明确。
3. ChatPipeline 自动注入。
4. 用户可管理。

这四点完成后，AstraCoreAI 就不再只是会话式框架，而是开始具备长期协作能力。
