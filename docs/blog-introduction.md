# 我用 Clean Architecture 造了个 AI 框架，130 项测试、78 个模块，生产可用

> 这篇文章介绍我做的开源项目 **AstraCore AI** —— 一个基于 Clean Architecture 的 Python AI 框架。不是玩具，不是 demo，是能实际用于生产的 AI 应用底座。

---

## 为什么要造这个轮子？

AI 应用开发有一个反复出现的困境：

你从一个 LangChain 教程开始，三天后拼出一个 demo，看起来很酷。然后真正的问题来了——

- 换个模型，要改 10 个地方
- 加个 RAG，和原来的记忆系统冲突
- 用 FastAPI 暴露接口，发现 SDK 和 HTTP 层逻辑开始分叉、重复
- 工具调用出 bug，根本不知道从哪里断点
- 用户刷新页面，生成到一半的回答就丢了

这些问题不是"换个更好的框架"能解决的，是架构层面的问题。

**AstraCore AI 就是为了彻底解决这些问题设计的。**

---

## 项目概览

```
GitHub: https://github.com/ayxworxfr/AstraCoreAI
语言: Python 3.11+
架构: Clean Architecture + Ports & Adapters
测试: pytest 当前收集 130 项（127 passed，3 failed 待修复）
Lint: ruff 0 error ✅
```

一张图说清楚整体结构：

```
┌─────────────────────────────────────────────┐
│               客户端 / 前端 SPA              │
└────────────┬───────────────────┬────────────┘
             │                   │
      ┌──────▼──────┐     ┌──────▼──────┐
      │  SDK 客户端  │     │ FastAPI 服务 │
      └──────┬──────┘     └──────┬──────┘
             └─────────┬─────────┘
                       │   ← 共享同一个 ChatPipeline
             ┌─────────▼─────────┐
             │   应用层 Use Cases  │
             └─────────┬─────────┘
                       │
        ┌──────────────┼──────────────┐
   ┌────▼────┐   ┌─────▼──────┐  ┌───▼──────┐
   │  LLM   │   │    记忆     │  │   RAG    │
   │ 适配器  │   │   适配器    │  │  适配器  │
   └────┬────┘   └─────┬──────┘  └───┬──────┘
        ▼               ▼              ▼
  Anthropic/      Redis + SQLite   ChromaDB
  OpenAI/DeepSeek  (默认本地持久化)
```

---

## 核心设计：两个关键决策

### 1. SDK 和 HTTP 服务共享同一个执行引擎

大多数框架有这个问题：SDK 版本和 HTTP 版本各自实现了一套 chat 逻辑，慢慢开始分叉，最后变成两套需要各自维护的东西。

AstraCore 当前用 `ChatPipeline` 彻底解决这个问题：

```python
# SDK 用法
async with AstraCoreClient() as client:
    async for event in client.chat_stream("你好"):
        print(event.content, end="")

# FastAPI 服务 —— 内部调用的是同一个 ChatPipeline
POST /api/v1/chat/runs
GET  /api/v1/chat/runs/{run_id}/stream  # SSE 订阅
```

两者功能完全一致：工具调用、RAG、Skill、extended thinking、记忆持久化——全部共享。

### 2. 后台 Chat Run：刷新页面不丢失生成结果

这是很多 AI 应用没有做好的地方。常见实现是 SSE 直接驱动生成，用户一刷新，生成就中断了。

AstraCore 的做法：

```
POST /api/v1/chat/runs      → 后台任务开始生成（生成不依赖 SSE 连接）
GET  /runs/{id}/stream      → SSE 订阅当前输出（可随时重连）
GET  /sessions/{id}/runs/active → 恢复连接后查询正在运行的任务
POST /runs/{id}/cancel      → 手动取消
```

浏览器刷新 = 断开 SSE 订阅，不等于中断生成。重新打开页面，会自动重连并恢复当前状态。

---

## 功能亮点

### 🔧 工具循环：健壮得出乎意料

工具调用是 AI 应用最容易出 bug 的地方。AstraCore 处理了所有边界情况：

- **悬空 tool_use 清理**：LLM 返回工具调用但没有结果时自动清理，避免 API 报错
- **单次工具超时隔离**：一个工具卡住，不影响整个循环
- **总结收尾兜底**：工具循环结束但没有文字输出时，自动触发总结回复
- **空响应引导续接**：模型输出为空时主动引导继续
- **中间旁白与最终答案自动分流**：工具执行过程中的思考文字和最终回答分开处理

### 🧠 Skill 系统：比 System Prompt 更结构化

每个 Skill 是一份 Markdown 文件，支持模板变量（`{{current_time_info}}`、`{{ai_name}}`）：

```yaml
---
name: 理财顾问
description: 黄金贵金属股票基金债券外汇期货行情分析
order: 25
---
# 角色
你是一名专业理财顾问...
```

**Skill 自动路由**有三种模式：

| 模式 | 实现方式 | 适合场景 |
|------|---------|---------|
| `off` | 禁用，手动指定 | 需要精确控制 |
| `vector` | sentence-transformers 余弦相似度 | 低延迟，无额外 API 消耗 |
| `llm` | 轻量 LLM 调用判断 | 语义理解要求高 |

主技能（📌）+ 自动路由的副技能（⚡）分层加载，系统 prompt 有序组合。

### 🗃️ 三层记忆架构

```
Redis          → 热数据，TTL 淘汰，毫秒级读写
SQLite         → Redis 不可用时自动降级，重启可恢复
StructuredMemory → SQLite 持久化（默认），后续可扩展到 PostgreSQL
```

Redis 挂掉？自动降级到 SQLite，应用无感知继续运行。

### 📡 MCP 工具集成（内置 + 自定义）

```yaml
mcp:
  servers:
    - type: filesystem          # 文件系统访问
      paths: [/your/project]
    - type: shell               # 受控 shell 执行
      allow_dirs: [/your/project]
    - type: custom              # 任意外部 MCP 进程
      name: my-tool
      command: python
      args: [my_mcp_server.py]
```

### 🤖 并行多 Agent：任务分解并发执行

遇到需要同时从多个来源收集信息、对比分析、或可拆分为相互独立子问题的任务时，`spawn_agents` 工具让 LLM 主动将任务分解为 2–5 个子任务，由 Worker Agent 并发执行：

```
主 Agent 决策 → spawn_agents(tasks=[
    {"task": "分析 A 公司财报"},
    {"task": "分析 B 公司财报"},
    {"task": "查询行业均值数据"}
])
       ↓  asyncio.Queue 并发驱动
Worker A     Worker B     Worker C   ← 各自独立 LLM + 工具循环
       ↓
   汇总结果 → 主 Agent 综合回答
```

- 每个 Worker 拥有完整工具访问权限，使用用户当前选择的模型 profile
- 前端实时展示各 Worker 进度（可折叠卡片）
- 通过 `agent.enable_spawn_agents: false` 可关闭该工具

### 🔍 RAG 管道

```python
# 索引文档
await client.index_document(
    document_id="doc-001",
    text="你的文档内容...",
    metadata={"source": "handbook", "version": "2.0"}
)

# 带引用的检索（幂等 upsert，重复索引不会重复）
async for event in client.chat_stream(
    "根据文档，我们的退款政策是什么？",
    enable_rag=True
):
    print(event.content, end="")
```

---

## 实际跑起来长什么样

前端是一个 React + Vite + Zustand 的 SPA，这是功能列表：

- 多模型 Profile 切换（同一对话可切换 Claude / DeepSeek / GLM）
- Skill 管理（CRUD，实时切换，路由结果在消息旁显示）
- RAG 调试面板（索引 / 检索 / 查看命中 chunk）
- 系统配置（全局指令、记忆上下文长度、temperature）
- Extended Thinking 展开/收起
- 工具调用状态实时显示（正在执行哪个工具、耗时）
- 并行 Agent 实时进度（折叠卡片，各 Worker 独立展示）
- 页面刷新不丢失正在生成的回答

---

## 代码质量基线

```
pytest collected 130 items: 127 passed, 3 failed
ruff: 0 errors             ✅
```

覆盖：SessionState、PolicyEngine、SecurityValidator、RAGPipeline、ChatUseCase、ToolLoopUseCase、LLM 适配器、HybridMemoryAdapter、MCP、Skill、流式会话安全等核心链路。

---

## 5 分钟跑起来

```bash
git clone https://github.com/ayxworxfr/AstraCoreAI
cd AstraCoreAI

# 安装（推荐 Hatch）
make setup

# 复制配置
cp config/config.example.yaml config/config.yaml
# 编辑 config.yaml，填入你的 API Key 对应的环境变量名

# .env 里放 key
echo "ANTHROPIC_API_KEY=sk-ant-xxx" >> .env

# 启动后端
make api          # http://127.0.0.1:8000

# 启动前端（另一个终端）
make fe-install
make fe-dev       # http://127.0.0.1:5173
```

不想启动服务？直接用 SDK：

```python
import asyncio
from astracore.sdk import AstraCoreClient

async def main():
    async with AstraCoreClient() as client:
        # 流式对话
        async for event in client.chat_stream("用博弈论解释一下为什么大家都选内卷"):
            if event.content:
                print(event.content, end="", flush=True)

asyncio.run(main())
```

---

## 项目现状与路线图

| 里程碑 | 状态 |
|--------|------|
| M1-M5：核心功能全闭环 | ✅ 已完成 |
| M6：熔断器、API Key 鉴权、限流 | 🔜 规划中 |
| M7：SLO/指标/压测基线 | 🔜 规划中 |
| M8：版本策略、回滚预案、运维文档 | 🔜 规划中 |

当前 78 个 Python 模块、pytest 收集 130 项测试，核心链路持续演进中。

---

## 适合哪些人

- **想从零搭 AI 应用**，不想重新踩工具调用、记忆管理、流式输出的坑
- **已有 LangChain/LlamaIndex 项目**，感觉架构越来越乱，想参考一个 Clean Architecture 的实现
- **做 AI 中台 / AI 网关**，需要 Skill 管理、多模型路由、多租户隔离的能力
- **学习 Clean Architecture 在 AI 领域的落地**，想看完整的 Ports & Adapters 实现

---

## 最后

如果这个项目对你有帮助，或者你在 AI 应用开发中也踩过同样的坑，欢迎：

- ⭐ **Star** 支持一下
- 🐛 发现 bug 提 Issue
- 💬 有想法开 Discussion 聊

> **GitHub**: https://github.com/ayxworxfr/AstraCoreAI

---

*AstraCore AI — 让 AI 应用开发有架构可依*
