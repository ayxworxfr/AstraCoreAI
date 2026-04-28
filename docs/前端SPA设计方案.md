# AstraCoreAI 前端 SPA 设计方案

## 1. 设计目标

交付专业、好看、好用的 AI 对话前端，具备以下特征：

- 视觉上达到现代 AI 产品水准（对标 Claude.ai / Perplexity）
- 双主题（深色 / 浅色）无缝切换，所有组件跟随主题
- AI 消息支持完整 Markdown 渲染（代码高亮、表格、列表等）
- 完整会话管理：创建、切换、重命名、删除、清空、持久化恢复
- 流式 SSE 输出稳定，支持工具调用进度、思考块展示和空响应兜底
- 支持后端多模型 Profile 注册表，前端自动读取并切换可用模型

## 2. 技术栈

| 包 | 版本 | 用途 |
|---|---|---|
| `antd` | 5.x | 基础 UI 系统（Layout、Button、Input、Card、Menu 等） |
| `@ant-design/x` | latest | AI 专用组件：`Bubble`、`Sender`、`Conversations`、`Welcome`、`Prompts` |
| `zustand` | 5.x | 全局状态管理 |
| `react-router-dom` | 6.x | SPA 路由 |
| `react-markdown` | 9.x | Bubble 内 Markdown 渲染 |
| `remark-gfm` | latest | GFM 扩展（表格、删除线等） |
| `rehype-highlight` | latest | 代码块语法高亮 |
| `axios` | 1.x | HTTP 请求 |

移除旧依赖：`@assistant-ui/react`（由 `@ant-design/x` 替代）、`use-effect-event`。

## 3. 整体布局

### 3.1 全局结构

```
┌─────────────────────────────────────────────────────────────┐
│  AstraCoreAI     [对话]  [RAG]  [Skill]  [系统]   [☀️/🌙]  │  ← Header 56px（固定）
├──────────────────┬──────────────────────────────────────────┤
│                  │                                          │
│  左侧面板        │           主内容区                       │
│  （仅 Chat 页）  │                                          │
│                  │                                          │
└──────────────────┴──────────────────────────────────────────┘
       300px                      剩余宽度
```

- **Chat 页**：左侧显示会话列表面板，右侧为完整聊天区
- **RAG / Skills / System 页**：无左侧面板，全宽内容区
- Header 固定，左侧 Logo，中间导航，右侧主题切换按钮

### 3.2 Chat 页布局

```
┌──────────────────┬──────────────────────────────────────────┐
│  AstraCoreAI     │  ┌──────────────────────────────────┐   │
│  [+ 新建会话]    │  │  会话标题                         │   │
│  [搜索会话...]   │  │  [模型 Profile] [Skill] [操作]    │   │
│  会话 1          │  └──────────────────────────────────┘   │
│  会话 2 ●        │                                          │
│  会话 3          │  ① 空态：Welcome + 推荐 Prompts          │
│  ...             │  ② 对话中：Bubble.List 消息流            │
│                  │                                          │
│                  │  ─────────────────────────────────────── │
│                  │  [Sender 输入框]   [流式] [发送]         │
└──────────────────┴──────────────────────────────────────────┘
```

## 4. 视觉设计

### 4.1 主题系统

使用 antd 5 `ConfigProvider` + `theme.darkAlgorithm` 实现，所有组件自动跟随，无需手写 CSS 变量。

**主色调：** `#1677ff`（antd 默认蓝，与 Ant Design X 组件配合最佳）

**深色主题 token 定制：**

```typescript
{
  colorBgBase: '#0d1117',       // GitHub 深色级别，比纯黑柔和
  colorBgContainer: '#161b22',
  colorBorderSecondary: '#30363d',
}
```

**浅色主题 token 定制：**

```typescript
{
  colorBgBase: '#f5f7fa',
  colorBgContainer: '#ffffff',
  colorBorderSecondary: '#e8edf2',
}
```

主题偏好持久化到 `localStorage["astracore.settings.v1"]`，刷新后恢复。

### 4.2 消息气泡设计

使用 Ant Design X `Bubble` 组件：

```
┌─ 用户消息 ─────────────────────────────────────────────────┐
│                                         ┌───────────────┐ │
│                               你的问题  │  主色蓝背景   │ │
│                                         └───────────────┘ │
└────────────────────────────────────────────────────────────┘

┌─ AI 回复 ──────────────────────────────────────────────────┐
│  [A]  ┌─────────────────────────────────────────────────┐  │
│       │  Markdown 渲染区                                │  │
│       │  - 代码块：语法高亮 + 一键复制按钮              │  │
│       │  - 支持表格、有序/无序列表、粗体、引用块        │  │
│       │  - 流式输出时末尾有打字光标动效（内置）         │  │
│       └─────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

- **用户消息**：主色蓝背景，右对齐，无头像
- **AI 消息**：Surface 背景，左侧带 `[A]` 头像图标，内容区渲染 Markdown
- **思考与工具**：工具轮旁白展示为 thinking 区块，工具调用显示执行中/完成/失败状态
- **流式输出**：Bubble 内置 `loading` 状态与打字光标，SSE 结束时统一收敛状态
- **滚动条**：Chat 主区域使用简约自定义滚动条，按明暗主题适配，支持拖动和点击轨道平滑跳转

### 4.3 Welcome 空态

会话无消息时展示 Welcome 组件：

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                ✦  AstraCoreAI                               │
│           专业 AI 基础设施，开始你的对话                    │
│                                                             │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│   │ 你能做什么？ │  │ RAG 怎么用？ │  │ 工具调用示例 │    │
│   └──────────────┘  └──────────────┘  └──────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

点击推荐 Prompt 卡片直接填入 Sender 输入框。

## 5. 文件结构

```
frontend/src/
├── main.tsx                         ← 入口，挂载 ConfigProvider + Router
├── app/
│   ├── App.tsx                      ← RouterProvider
│   ├── router.tsx                   ← 路由定义（chat / rag / skills / system）
│   └── theme.ts                     ← antd light/dark token 配置
├── layouts/
│   ├── AppShell.tsx                 ← Header（Logo + Nav + 主题切换）+ Outlet
│   └── ChatLayout.tsx               ← 左侧会话栏 + 右侧聊天区双栏布局
├── pages/
│   ├── ChatPage.tsx                 ← 使用 ChatLayout，持有对话业务逻辑
│   ├── RagPage.tsx                  ← RAG 检索页
│   ├── SkillsPage.tsx               ← Skill CRUD 管理页（全局指令 + Skill 列表）
│   └── SystemPage.tsx               ← 系统状态 / LLM 信息 / 运行参数（Tabs）
├── components/
│   ├── chat/
│   │   ├── ConversationSidebar.tsx  ← @ant-design/x Conversations + 新建/搜索
│   │   ├── ChatMain.tsx             ← Bubble.List + Welcome + Sender + SkillSelector + 自定义滚动条
│   │   ├── ModelSelector.tsx        ← 后端 LLM profile 下拉选择
│   │   └── MarkdownContent.tsx      ← react-markdown 渲染器（代码高亮）
│   ├── rag/
│   │   ├── RagMarkdownEditor.tsx    ← Markdown 预览 + 编辑切换（复用于 Skill 编辑）
│   │   ├── RagQueryPanel.tsx        ← 查询输入 + top_k 参数
│   │   ├── RagIndexPanel.tsx        ← RAG 文档索引管理面板
│   │   └── RagResultList.tsx        ← 结果卡片列表（score badge + 引用内容）
│   ├── skills/
│   │   ├── SkillCard.tsx            ← Skill 卡片（查看/编辑/删除）
│   │   ├── SkillModal.tsx           ← Skill 创建/编辑弹窗
│   │   ├── SkillSelector.tsx        ← 对话工具栏 Skill 切换下拉
│   │   └── GlobalInstructionEditor.tsx  ← 默认 Skill 选择 + 全局指令编辑
│   └── system/
│       └── HealthStatusCard.tsx     ← 健康/就绪状态卡片
├── stores/
│   ├── chatStore.ts                 ← 会话 + 消息 + 流式状态 + activeSkillId + modelId
│   ├── skillStore.ts                ← Skills 列表 + 用户设置（持久化）
│   └── settingsStore.ts             ← theme: 'light' | 'dark'（持久化）
├── services/
│   ├── apiClient.ts                 ← axios 实例 + 错误规范化
│   ├── chatService.ts               ← POST /chat/ + SSE /chat/stream + DELETE /sessions/:id
│   ├── ragService.ts                ← POST /rag/retrieve
│   ├── skillService.ts              ← GET/POST/PUT/DELETE /skills/ + GET/PUT /settings/
│   ├── systemService.ts             ← GET /system/（LLM profiles + Tavily + MCP 状态）
│   └── healthService.ts             ← GET /health/ 和 /health/ready
└── types/
    ├── api.ts                       ← 后端接口类型
    ├── chat.ts                      ← 前端会话/消息类型
    ├── skill.ts                     ← Skill + UserSettings 类型
    └── system.ts                    ← SystemInfo 类型
```

### 各层职责边界

| 层 | 职责 |
|---|---|
| `layouts/` | 纯布局骨架，不含业务逻辑 |
| `pages/` | 页面级状态协调，连接 store 和 service |
| `components/` | 纯展示组件，props 驱动，可独立复用 |
| `stores/` | 全局共享状态，含 localStorage 持久化 |
| `services/` | 所有网络请求，不含 UI 逻辑 |

## 6. 状态管理设计

### 6.1 类型定义

```typescript
// types/chat.ts

type ConversationMeta = {
  id: string              // UUID，同时作为后端 session_id
  title: string
  updatedAt: string       // ISO string
  lastMessagePreview: string
  messageCount: number
  pinned: boolean
  skillId?: string | null // 会话独立 Skill：undefined = 默认，'none' = 禁用，uuid = 指定 Skill
  modelId?: string | null // 会话独立模型 Profile：null/undefined = 后端默认
}

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  thinkingBlocks?: string[]
  thinkingMode?: 'normal' | 'deep' | 'tool'
  toolActivity?: ToolActivity[]
  status: 'pending' | 'streaming' | 'done' | 'error'
  createdAt: string
}
```

### 6.2 chatStore

```typescript
// 状态
conversations: ConversationMeta[]
activeConversationId: string
messagesByConversation: Record<string, ChatMessage[]>
isStreaming: boolean
streamingConversationId: string | null
useStream: boolean              // 流式开关，持久化
activeSkillId: string | null    // 当前激活的 Skill ID（null = 使用默认）
activeModelId: string | null    // 当前激活的模型 profile id（null = 后端默认）

// 动作
createConversation()            // 新建，自动切换为当前
switchConversation(id)          // 切换（流式中禁止切换）
renameConversation(id, title)
deleteConversation(id)          // 删除后自动切到最近会话或新建，并清除后端 session 记忆
clearConversation(id)           // 清空消息，保留会话，并清除后端 session 记忆
setActiveSkillId(id)            // 切换激活 Skill
setActiveModelId(id)            // 切换激活模型 Profile
sendMessage(prompt)             // 非流式
sendStreamMessage(prompt)       // SSE 流式
cancelStream()                  // 中断流式请求
hydrateFromStorage()            // 启动时从 localStorage 恢复
```

### 6.3 skillStore

```typescript
// 状态（持久化到 localStorage）
skills: Skill[]
settings: UserSettings        // { default_skill_id, global_instruction, temperature, rag_top_k, context_max_messages }
isLoading: boolean

// 动作
fetchSkills()                 // GET /api/v1/skills/
createSkill(data)             // POST /api/v1/skills/
updateSkill(id, data)         // PUT /api/v1/skills/:id
deleteSkill(id)               // DELETE /api/v1/skills/:id
fetchSettings()               // GET /api/v1/settings/
updateSettings(patch)         // PUT /api/v1/settings/
```

### 6.4 settingsStore

```typescript
theme: 'light' | 'dark'
toggleTheme()
```

### 6.5 持久化键

| 键 | 内容 |
|---|---|
| `astracore.conversations.v1` | 会话元数据列表 |
| `astracore.messages.<id>.v1` | 各会话消息（按会话分桶） |
| `astracore.settings.v1` | 主题偏好 |
| `astracore.skill-store.v1` | Skills 列表与用户设置（Zustand persist） |

## 7. API 契约

### 7.1 Chat 非流式

```
POST /api/v1/chat/
Body: { message, session_id?, skill_id?, model_profile?, temperature?, enable_thinking?, thinking_budget?, enable_rag?, use_tools?, enable_web? }
Response: { session_id, message, model_profile, model?, metadata? }
```

### 7.2 Chat 流式（SSE）

```
POST /api/v1/chat/stream
Body: { message, session_id?, skill_id?, model_profile?, temperature?, enable_thinking?, thinking_budget?, enable_rag?, use_tools?, enable_web? }

SSE 事件：
  event: message  data: <文本增量>
  event: thinking_start / thinking / thinking_stop
  event: tool_use / tool_start / tool_result
  event: done     data: [DONE]
  event: error    data: <错误信息>
```

前端处理流程：
1. 发起请求前插入 `status: 'streaming'` 的 assistant 占位消息
2. 每个 `message` 事件追加到占位消息内容
3. thinking 事件写入 `thinkingBlocks`，空 thinking block 不渲染
4. tool 事件写入 `toolActivity`，让用户知道用了哪些工具
5. 收到 `done` → 置 `status: 'done'`
6. 收到 `error` 或网络异常 → 置 `status: 'error'`，展示重试入口

### 7.3 Session 记忆清理

```
DELETE /api/v1/chat/sessions/:session_id   → 204 No Content
```

删除或清空对话时 fire-and-forget 调用，清除后端 SQLite 中的短期记忆。

### 7.4 RAG 检索

```
POST /api/v1/rag/retrieve
Body: { query, top_k? }
Response: { results: [{ content, score, citation? }] }
```

### 7.5 Skill 管理

```
GET    /api/v1/skills/        → Skill[]
POST   /api/v1/skills/        Body: SkillCreate  → Skill
PUT    /api/v1/skills/:id     Body: SkillUpdate  → Skill
DELETE /api/v1/skills/:id     → 204 No Content
```

### 7.6 用户设置

```
GET /api/v1/settings/         → { default_skill_id, global_instruction, temperature, rag_top_k, context_max_messages }
PUT /api/v1/settings/         Body: 以上字段的子集  → 同上
```

运行时参数（temperature、rag_top_k、context_max_messages）按请求从 DB 读取，修改后立即生效，无需重启。

### 7.7 系统信息

```
GET /api/v1/system/   → {
  llm: {
    default_profile,
    profiles: [{
      id, label, provider, model, base_url, api_key_configured, max_tokens,
      capabilities: { tools, thinking, temperature, anthropic_blocks }
    }]
  },
  tavily_configured,
  mcp_servers: [{ name, type }]
}
```

### 7.8 健康检查

```
GET /health/        → { status }
GET /health/ready   → { status }
```

## 8. 交互规则

### 8.1 会话管理

- 新建会话后自动切换为当前会话
- 首条用户消息自动设为会话标题（截断到 24 字）
- 重命名通过 Conversations 组件内联编辑（不用 `window.prompt`）
- 删除当前会话后自动切换到最近活跃会话；若无则新建空白会话
- 流式输出过程中禁止切换会话，顶部提示"生成中，请稍候"
- 会话列表支持置顶和搜索过滤

### 8.2 流式输出

- Bubble 组件 `loading` 状态展示打字光标
- 中断按钮（Sender 内置 Stop 按钮）可取消流式请求
- 流异常结束时消息显示错误状态，提供重试按钮
- 工具调用中的中间轮文字只进入 thinking 区，不冒充最终答案
- 结尾空响应时后端会返回续接提示，前端不再只显示"（空响应）"

### 8.3 一致性保障

- 每条消息写入必须携带 `conversationId`
- 严禁将流式增量写入非当前激活会话
- 任何异常结束都将 assistant 消息状态收敛到 `error` 或 `done`

## 9. 错误处理

| 场景 | 处理方式 |
|---|---|
| 网络请求失败 | 消息气泡显示错误态，提供重试 |
| SSE 流中断 | 消息置 `error` 状态，不静默失败 |
| localStorage 损坏 | 检测异常时自动清空并重置，提示用户 |
| 后端 5xx | 统一错误提示，展示 `detail` 字段内容 |

## 10. 里程碑拆解

### FE-M1：工程初始化

- 安装 antd、@ant-design/x、zustand 等依赖
- 搭建路由骨架（AppShell + 三页路由）
- 配置 antd ConfigProvider，深/浅主题切换可用
- 搭建 API Client

验收：`npm run dev` 可访问三个页面，主题切换生效。

### FE-M2：完整会话系统

- ConversationSidebar（新建、搜索、列表、置顶）
- chatStore 完整实现（含 localStorage 持久化）
- 会话 CRUD 全部可用

验收：刷新后会话与消息可恢复，无状态错乱。

### FE-M3：Chat 链路

- ChatMain（Welcome + Bubble.List + Sender）
- MarkdownContent（react-markdown + 代码高亮）
- 非流式 + 流式 SSE 发送
- 流式取消与错误重试

验收：多会话不串消息，流式稳定，Markdown 正确渲染。

### FE-M4：RAG + System + 收尾 ✅

- RagPage（查询 + 结果卡片 + 索引管理）
- SystemPage（健康状态卡片 + 自动刷新）
- 统一空态、加载态、错误态

验收：三个页面均可正常使用，无 TODO 残留。

### FE-M5：Skill 系统 + 系统配置扩展 ✅

- SkillsPage（GlobalInstructionEditor + SkillCard 列表 CRUD）
- SkillSelector（Chat 工具栏 Skill 切换下拉，流式时禁用）
- SystemPage 重构为三 Tab（系统状态 / LLM 信息 / 运行参数）
- 运行参数（Temperature Slider、RAG top_k、Context 长度）在线修改
- 删除/清空对话时 fire-and-forget 清除后端 session 记忆

验收：Skill CRUD 可用，切换后对话系统提示正确应用；System 页面三 Tab 可用；运行参数修改后立即生效。

## 11. 验收标准

- 双主题切换流畅，无组件主题不跟随问题
- 会话能力完整：新建、切换、重命名、删除、清空、刷新恢复
- 会话一致性：多会话下不串消息
- Chat 普通与流式链路稳定可用
- AI 消息 Markdown 正确渲染，代码块有高亮
- RAG 与 System 页面可用
- 无 TODO 占位，无残缺功能入口
