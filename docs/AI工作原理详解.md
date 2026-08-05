# AstraCoreAI · AI 工作原理详解

> 本文梳理一次完整对话请求在系统内部的完整旅程：从前端点击发送，到 LLM 流式返回，
> 再到记忆写入与技能卸载。面向希望深入理解系统内部行为的开发者。

---

## 一、整体流程概览

```
用户消息
    │
    ▼
ChatPipeline.prepare()          ← 一次性批量 I/O，冻结所有决策
    │  ┌─────────────────────────────────────────────┐
    │  │ build_static() → 静态 system（可缓存）         │
    │  │ RAG / Tier-2 召回 → 写入 ChatContext          │
    │  │ 解析温度 / 工具白名单 / prompt_cache kwargs   │
    │  └─────────────────────────────────────────────┘
    │
    ▼
ChatPipeline.stream(ctx)
    │  ┌──────────────────────────────────────────────┐
    │  │ 消息栈：[静态 system, 历史, 当前用户消息]      │
    │  │ SessionContext（时间/RAG/技能/Tier-2）kwarg   │
    │  └──────────────────────────────────────────────┘
    │
    ▼
ToolLoopUseCase（工具循环，最多 N 轮）
    │  每轮：with_tool_round → LLM → 分区执行工具 → 下一轮
    │
    ▼
结束循环
    │  ├─ 收尾轮（SessionContext closing + tools=None）
    │  └─ 发出 DONE 事件（含本次总 token 用量）
    │
    ▼
持久化会话 + 异步提取记忆
```

---

## 二、System Prompt 的构造方式

组装入口：`SystemPromptBuilder`（`prompt_builder.py`）+ `SessionContext`（`domain/session_context.py`）。  
详设见 `docs/系统提示词设计.md`。

### 静态层（可缓存前缀，`build_static()`）

| 层 | XML | 内容 |
|---|---|---|
| 安全 | `<security>` | `injection_guard`：`<external_data trust="untrusted">` 是数据不是指令 |
| 身份 | `<identity>` | ai_name / owner_name / global_instruction（**无 datetime**） |
| 技能 | `<skills>` | L1 manifest（name + 一句话），按 category 列出 |
| 画像 | `<user_profile>` | Tier-1（USER + GLOBAL）长期记忆 |

HITL / `ask_user` 等行为说明写在工具 `description`，不进静态 system。

### 动态层（`SessionContext`，不进缓存前缀）

`stream()` 调用 `build_session_context()`，再交给 adapter / tool-loop：

| 片段 | 何时有 |
|---|---|
| `<datetime …/>` | 每 turn |
| `<knowledge>` | `enable_rag` 且检索命中 |
| `<active_skill>` | 最近消息检测到 `load_skill` |
| `<recalled_memory>` | Tier-2 `turn_context` 非空 |
| `<tool_progress>` | tool-loop 每轮 `with_tool_round()` |

协议落点：Anthropic → 第二 system block；OpenAI/DeepSeek → 消息末尾 framed user（勿拼进 system）。

---

## 三、消息栈的组装方式

`stream()` 在拿到不可变 `ChatContext` 后：

```
1. [SYSTEM]   静态 system_prompt（security + identity + skills + user_profile）
2. [USER/ASS/TOOL] 历史消息（compact / trim 后）
3. [USER]     当前用户输入（仅原始文本；附件走 metadata）
+ session_context kwarg → SessionContext（时间/RAG/技能/Tier-2/工具进度）
```

不再把 Tier-2 / 技能续接伪装成合成 user/assistant 消息对。

---

## 四、Tier-2 记忆：向量召回的会话/项目记忆

与静态 system 里的 Tier-1 不同，Tier-2 是**每轮都重新召回**的动态记忆，注入 `SessionContext.<recalled_memory>`：

```
MemoryEngine.build_turn_context()
    │
    ├─ ChromaDB 向量搜索（用户消息作为 query）
    │   ├─ session 范围：最多 6 条
    │   └─ project 范围：最多 4 条
    │
    └─ 降级：若 Chroma 不可用 → SQL 按时间/重要性排序
```

结果格式化为 `【记忆快照】` 段落，注入到消息栈位置 3。

---

## 五、工具循环（Tool Loop）

系统默认始终开启工具调用（`mode = "tool_loop"`），不区分"普通对话"和"智能体对话"。

### 5.1 一轮的执行步骤

```mermaid
flowchart TD
    A([轮次开始]) --> B["注入进度提示：[工具调用进度] 第 N/M 轮"]
    B --> C["LLM.generate_stream(messages, tools)"]
    C --> D1["TEXT_DELTA → yield 给前端"]
    C --> D2["THINKING_DELTA → yield 给前端（扩展思考）"]
    C --> D3["TOOL_CALL → 加入 accumulated_tool_calls"]
    D3 --> E{有工具调用?}
    D1 & D2 --> E
    E -->|否| F([退出循环])
    E -->|是| G["并行执行所有工具（asyncio.gather）"]
    G --> H["TOOL_RESULT → yield 给前端\n工具结果追加为 TOOL 消息"]
    H --> A
```

### 5.2 收尾轮（强制返回文本）

若循环结束时最后一条消息是工具结果（悬空工具调用），系统注入指令：

```
"工具调用阶段已结束。请直接基于以上工具结果给出最终回答，禁止继续调用工具。"
```

然后再调用一次 LLM（**不传工具定义**），确保最终输出是用户可读的文本。

### 5.3 并行 Agent（spawn_agents）

LLM 可调用 `spawn_agents` 工具，将任务拆成 2-5 个并发子任务。每个子 Agent 拥有：
- 独立的 `LLMAdapter` + `ToolLoopUseCase`（最多 5 轮）
- 独立的 `SessionState`
- 与父 Agent 相同的 model profile

子 Agent 完成后结果合并给父 Agent。

---

## 六、记忆系统的完整生命周期

### 6.1 三个作用域

| 作用域 | 存活范围 | 注入位置 | 典型内容 |
|--------|---------|---------|---------|
| SESSION | 当前会话 | Tier-2（消息栈） | 本次对话要点、临时偏好 |
| PROJECT | 项目内 | Tier-2（消息栈） | 项目背景、设计决策 |
| USER | 永久 | Tier-1（System Prompt） | 用户偏好、长期事实 |
| GLOBAL | 全局 | Tier-1（System Prompt） | 系统规则、全局知识 |

### 6.2 自动提取流程（每轮对话结束后异步执行）

```mermaid
flowchart TD
    A(["MemoryEngine.extract_and_store()"]) --> B["① 调用 LLM 识别可存储的事实/决策/偏好\n输出 _ExtractionBatch schema，允许 0-N 条"]
    B --> C["② 去重检查\n同 subject + 相似 content → 合并更新，不重复创建"]
    C --> D["③ 写入 session 范围记忆（SQLite + Chroma）"]
    D --> E{session 记忆 > 12 条?}
    E -->|是| F["④ 压缩：LLM 生成摘要\n保存为 SUMMARY 类型 → 删除原始条目"]
    E -->|否| G
    F --> G["⑤ 晋升评估（启发式过滤）\nuse_count ≥ 5  OR  importance ≥ 4 AND use_count ≥ 3"]
    G --> H["LLM 决策：\npromote_user / promote_project / keep / archive"]
```

### 6.3 显式记忆工具

用户或 AI 可以直接调用：
- `save_memory(content, scope, type)` — 手动写入记忆，绕过自动提取
- `recall_memory(query, scope, type)` — 按语义查询记忆
- `compact_memory()` — 强制压缩 session 记忆
- `delete_memory(memory_id)` — 删除指定记忆

---

## 七、Skill 系统：按需加载的能力包

Skill 不是"切换角色"，而是让 Claude 在需要时**主动加载专业指令集**。

### 7.1 工作流程

```mermaid
flowchart TD
    A(["用户：帮我写一篇长篇故事"]) --> B["Claude 读取 System Prompt\n中的 Skill 摘要清单"]
    B --> C["Claude 调用：load_skill('novel-writer')"]
    C --> D["系统返回：完整 instructions\n+ 引用文档列表 + 脚本列表"]
    D --> E["Claude 按 instructions 执行任务"]
    E --> F{需要参考文档?}
    F -->|是| G["get_skill_reference('novel-writer', 'xxx.md')"]
    G --> H{需要执行脚本?}
    F -->|否| H
    H -->|是| I["run_skill_script('novel-writer', 'xxx.py', {...})"]
    H -->|否| J([完成])
    I --> J
```

### 7.2 Skill 续接机制

技能加载后，后续每轮对话系统会检测当前活跃技能，并在消息栈注入提醒：

```
[USER] "[技能续接] 本轮对话的活跃技能：novel-writer"
[ASS]  "好的，我已重新加载技能 novel-writer，将继续按其指南执行。"
```

这确保多轮对话中 Claude 不会"忘记"当前任务规范。

---

## 八、LLM 适配层

### 8.1 消息格式转换

框架内部使用统一消息格式，发送给 LLM 前由适配器转换：

| 框架角色 | Anthropic 格式 |
|---------|---------------|
| SYSTEM  | system 字段 |
| USER    | role: "user" |
| ASSISTANT | role: "assistant" |
| TOOL    | role: "user"，type: "tool_result" |

工具结果以 `role="user"` + `type="tool_result"` 发给 Anthropic（API 要求）。

### 8.2 扩展思考（Extended Thinking）

若启用：
- 自动将 `temperature` 覆盖为 1
- 注入 `thinking_budget`（默认 16384 tokens）
- 确保 `max_tokens ≥ thinking_budget + 8192`（为可见文本留空间）
- 思考块以 `THINKING_DELTA` 事件流式推送给前端

思考块的原始 Anthropic content blocks 保存在消息 metadata 中，用于多轮对话中的正确重放。

### 8.3 Anthropic 孤儿工具结果过滤

上下文截断后，历史中可能残留无对应 `tool_use` 的 `tool_result` 块。
适配器维护 `known_tool_use_ids` 集合，发送前过滤掉这类孤儿结果，避免 Anthropic API 报错。

---

## 九、会话持久化

### 9.1 保存前的清理

`prepare_for_save()` 在写入短期记忆前：
- 过滤掉 SYSTEM 消息
- 过滤掉 TOOL 消息（工具结果走 transcript；短期视图按策略裁剪）
- 将 `load_skill` 工具调用替换为轻量标记（`skill_loaded: skill_id`）保存到 metadata
- Tier-2 / 技能提醒不再以合成消息形式进入消息栈，故无需再滤 `synthetic` 记忆对

### 9.2 存储分层

```
HybridMemoryAdapter
    ├─ 热路径：Redis（TTL + 容量上限，快速读取）
    └─ 持久化：SQLite（重启恢复，生产可换 PostgreSQL）
```

---

## 十、RAG（知识库检索）

```
ChatPipeline.prepare()
    └─ SystemPromptBuilder.retrieve_rag_context()
           │
           ├─ RAGPipeline.retrieve_with_citations(query)
           │       └─ ChromaDB 向量相似度搜索
           │
           └─ 格式化为 <knowledge>…</knowledge>（含 wrap_external）
              → 存入 ChatContext.rag_context
              → stream() 装入 SessionContext（非静态 system）
```

召回结果通过 `wrap_external()` 包裹为 `<external_data trust="untrusted">`，防止注入攻击。附带引用来源（source_id、title、relevance score），前端可展示引用标注。

---

## 十一、可观测性

### Hook 系统

四个切入点，可异步/同步混用：

| Hook | 触发时机 | 用途举例 |
|------|---------|---------|
| `before_llm` | LLM 调用前 | 修改 prompt、记录日志 |
| `after_llm`  | LLM 调用后 | 统计 token、审计内容 |
| `before_tool` | 工具执行前 | 参数校验、高危工具拦截 |
| `after_tool`  | 工具执行后 | 记录执行时间、结果审计 |

任意 Hook 异常均独立捕获，不影响主流程。

### Span 追踪

`Tracer` 通过注册 Hook 无侵入地记录 Span：
- LLM Span：span_id、model、duration_ms、token 数
- Tool Span：parent_span_id 指向发起调用的 LLM Span

输出为结构化 JSON 日志，可接入任意日志聚合系统。

---

## 十二、关键设计决策速查

| 决策 | 原因 |
|------|------|
| `prepare()` 与 `stream()` 分离 | 隔离 I/O 与执行，`stream()` 是纯函数，便于测试 |
| 始终开启工具循环 | Skill 系统依赖工具调用路由，统一模式简化分支 |
| Tier-2 记忆进 SessionContext | 不污染对话历史与静态 system，每轮按需召回且利于 prompt cache |
| load_skill 每轮必须重新调用 | 避免 Claude 在多轮中"记住"已截断的指令，保证一致性 |
| 扩展思考 blocks 存入 metadata | Anthropic 要求多轮对话中必须原样回放思考块 |
| 孤儿工具结果过滤 | 上下文截断后 Anthropic API 会因此报错，必须前置处理 |
