# 定时任务系统：scheduled_tasks 模块

**日期**：2026-06-18
**档位**：设计卡（跨 ≥10 文件、新增基础依赖、新公开 API、改 ChatPipeline 入口语义、新增 DB 表、含前端模块）
**状态**：功能已落地；文中 `_ACTIVE_RUNS` 多 worker 限制见后续 `RunRegistry`（2026-08-04 重构收口），scheduler 多 worker 防双触发仍待统一

---

## 意图与边界

### Job-to-be-Done

当用户希望 AI 在指定时间或周期性自动执行某些行为（如每日新闻摘要、每周报表、定时提醒），
用户想要像 ChatGPT Tasks 一样让 AI 在后台自主跑一轮 agent loop，
以便在不在线 / 不主动发起对话的情况下，仍能拿到周期性的结果，且结果天然落入对话流便于追问。

### Goals

1. **对话内创建**：用户在对话里说"每天 8 点提醒我读 AI 新闻"，LLM 调 `schedule_task` 工具创建任务，返回任务 ID + 下次触发时间
2. **UI 管理**：单独 `/scheduled-tasks` 页面查看 / 编辑 / 暂停 / 删除 / 手动触发任务，列表分页 + 状态筛选
3. **触发即跑 agent loop**：到点后系统自动复用 `ChatPipeline`，把"任务原意图 + 执行上下文"作为消息注入，跑一轮（含 RAG / 工具 / 记忆）
4. **结果落入对话流**：每次触发新建一个 `ConversationRow`（绑定到任务），结果作为新对话出现在用户对话列表
5. **三种触发器**：`cron`（标准 5 字段表达式）/ `interval`（每 N 秒）/ `date`（一次性指定时刻）
6. **重启不丢任务**：任务定义 + 下次触发时间持久化到 SQLite，服务重启自动恢复
7. **失败可见可重试**：触发失败的任务在历史页有 error_message + 「立即重试」按钮

### Non-Goals（本次不做）

- **多 worker 横向扩展**：保持单 worker 部署。已记录的 `_ACTIVE_RUNS` multi-worker limitation 同样适用本模块；scheduler 单例 + SQLite datastore 在多 worker 下会出现重复触发，下版本统一迁移到 Redis/Postgres 时一并解决
- **推送通知（push / 邮件）**：项目无 push 基础设施。结果靠用户回到 web 时在「对话列表」+「定时任务历史页」自然看到；前端可加桌面通知 API 作为可选增强（首版不做）
- **跨任务依赖 / DAG 编排**：复杂工作流走 `agent` 模块的 NativeWorkflowOrchestrator；scheduling 只做单任务定时触发
- **任务级 RBAC**：仅按 `user_id` 隔离，不实现"管理员看所有任务"等高级权限
- **执行限流 QPS / 单用户并发上限**：首版用最朴素的 `asyncio.Semaphore(N)` 全局限流，不做per-user
- **历史归档清理 cron**：触发产生的 `ChatRunRow` 数据不自动清理，留给后续 milestone
- **MCP 工具调用 schedule_task**：仅在 NativeToolAdapter 注册，MCP 不接

### 成功标准

| Goal | 可验证现象 |
|---|---|
| G1 对话内创建 | 对话里发"每天上午 9 点给我推 AI 新闻"，LLM 触发 `schedule_task(prompt=..., trigger_type='cron', trigger_config={'expr': '0 9 * * *'})`；DB `scheduled_tasks` 多一行 |
| G2 UI 管理 | `GET /api/v1/scheduled-tasks` 返回分页列表；`POST /:id/pause` 后 `next_run_at` 变 NULL；前端列表正确刷新 |
| G3 触发跑 agent | 时间到 → `chat_runs` 表多一条 `status='running'` 记录，`request.trigger_source='schedule'` 字段非空；最终 `status='succeeded'` 且 `assistant_content` 非空 |
| G4 结果落对话流 | 触发后 `conversations` 表多一条新会话，`title=任务名`，`message_count >= 1`；前端对话列表自动出现 |
| G5 三种触发器 | `cron='*/5 * * * *'` / `interval=300` / `date='2026-06-20T14:00:00+08:00'` 三种都能创建并触发 |
| G6 重启不丢 | 创建一个 1 小时后触发的 `date` 任务 → 重启服务 → 1 小时后仍按时触发 |
| G7 失败可见 | runner 内 mock LLM 抛 RuntimeError → `chat_runs.status='failed'`，`error` 字段非空；UI 显示「重试」按钮，点击后新建一次 run |

### 输入 / 输出

**输入（创建任务）**：
- `prompt: str` — 任务原意图（如"今日 AI 头条新闻摘要"），触发时作为 user 消息送入 ChatPipeline
- `trigger_type: 'cron' | 'interval' | 'date'`
- `trigger_config: dict` — `{expr: str}` / `{seconds: int}` / `{run_at: ISO8601}`
- `name: str | None` — 显示名（可选，缺省取 prompt 前 24 字）
- `timezone: str` — 默认 `Asia/Shanghai`
- `conversation_id: str | None` — 缺省为 None（每次触发新建对话）；若指定，触发结果追加到该对话尾部

**输出**：
- `ScheduledTaskRow` 持久化记录
- 触发时：`ChatRunRow` + 可能新建的 `ConversationRow`

### 约束

- 复用现有 SQLite + aiosqlite，不引入新数据库
- 复用 `ChatPipeline`，不另写一套执行器
- 复用 `NativeToolAdapter` 注册新工具，不改工具系统
- 保持 `chat/` 模块的 DDD 结构（`domain/` + `application/` + `api.py`）
- 前端用 React + Zustand + Ant Design，参照 `features/skills/` 模块结构
- 现有 190 个测试不退化

---

## 决策驱动变量

| 变量 | 类别 | 取值 | 来源 |
|---|---|---|---|
| 触发后行为 | driver | 自动跑一轮 ChatPipeline | 用户回答 |
| 创建入口 | driver | 对话（LLM 工具）+ UI 表单 都支持 | 用户回答 |
| 结果展示 | driver | 每次触发新建对话 + 单独历史页 | 用户回答 |
| 调度器选型 | driver | APScheduler（用户选 4.x，本设计因 alpha 状态降级到 3.11.2 stable，见 § 决策记录） | 用户回答 + 项目事实 |
| 多 worker 准备 | 边角 | 假设：本次单 worker（已记忆 `_ACTIVE_RUNS` 限制） | `memory: project_active_runs_multiworker.md` |
| 时区 | 边角 | 假设：默认 `Asia/Shanghai`，用户可在创建任务时指定 | `src/astracore/modules/skills/prompt_utils.py:12` 已有 `_BEIJING_TZ` |
| 单用户任务上限 | 边角 | 假设：默认 50（ChatGPT 限 10 偏严，本项目放宽，可在 `config.yaml` 调） | 无明显约束 |
| 通知方式 | 边角 | 假设：靠对话列表 + 历史页，无独立 push 系统 | 项目无 push 基础设施 |
| 失败重试策略 | 边角 | 假设：首次失败不自动重试；用户在历史页手动「重试」 | 自动重试需消息去重 + 退避策略，不在首版 |

---

## 项目事实

### 模块结构与启动流程

| 事实 | 路径 |
|---|---|
| FastAPI lifespan 钩子（注册 scheduler 的标准位置） | `src/astracore/app/factory.py:60-126` |
| 现有 lifespan 已有的扩展点：`init_db` / `seed_builtin_skills` / `seed_documents` / MCP `_start_mcp` | `src/astracore/app/factory.py:68-117` |
| `AstraCoreConfig` 配置入口 | `src/astracore/sdk/config.py` |
| `chat/` 模块结构（DDD 参照模板） | `src/astracore/modules/chat/{api.py,pipeline.py,application/,domain/}` |

### ChatPipeline 复用要点

| 事实 | 路径 |
|---|---|
| `ChatPipeline.__init__` 接收 6 个适配器，可复用 HTTP 层的实例 | `src/astracore/modules/chat/pipeline.py:204-223` |
| `ChatPipeline.prepare()` 单批 DB 查询；`stream()` 纯执行 | `src/astracore/modules/chat/pipeline.py:192-202` |
| `_execute_run` 函数：当前唯一驱动 stream() 的入口 | `src/astracore/modules/chat/api.py:428` |
| `_create_run_row` 函数：构造 ChatRunRow 的标准方式（可提取为 helper） | `src/astracore/modules/chat/api.py:357-376` |
| `_hitl_callback` 在 `_execute_run` 内闭包构造 | `src/astracore/modules/chat/api.py:442-444` |
| `ChatRequest` schema | `src/astracore/modules/chat/api.py`（顶部） |

### DB 模型参照

| 事实 | 路径 |
|---|---|
| `ChatRunRow` 表结构（含 status / error / assistant_content / model） | `src/astracore/infrastructure/db/models.py:213-246` |
| `ConversationRow` 表结构（user_id / title / message_count） | `src/astracore/infrastructure/db/models.py:249-273` |
| 现有的 ORM 风格：`Mapped[]` + `mapped_column()` + `__table_args__` Index | `src/astracore/infrastructure/db/models.py:213-246` |
| `init_db` 自动 `create_all`（首版无需 alembic 手写迁移） | `src/astracore/infrastructure/db/session.py` |

### 工具注册参照

| 事实 | 路径 |
|---|---|
| 工具注册模板：`build_tool_adapter` | `src/astracore/modules/tools/builtin.py` |
| `NativeToolAdapter.register_tool(name, fn, schema, description, requires_confirmation=False)` | `src/astracore/infrastructure/tools/native.py:24-39` |
| `_context` 注入：函数签名含 `_context` 时自动传入 user_id / session_id / db_url | `src/astracore/infrastructure/tools/native.py:61-64` |

### 调用方 grep（公开接口变更确认）

**`ChatPipeline` 调用方**（仅 3 处，新增 cron 触发不破坏现有路径）：
- `src/astracore/modules/chat/api.py:26,140,142` — HTTP `_get_chat_pipeline()`
- `src/astracore/sdk/client.py:25,230,303,305` — SDK `_build_pipeline()`
- `src/astracore/modules/chat/pipeline.py:188,192` — 自身定义

**`_create_run_row` 调用方**：
- `src/astracore/modules/chat/api.py` 内部仅 1 处调用（POST /chat 端点内）。**重构提议**：把 `_create_run_row` 移到 `chat/application/run_factory.py`，让 `chat/api.py` 和新增的 `scheduling/runner.py` 都可调用。属于 S 类（结构改）。

**`ChatRunRow` 引用**：仅 `chat/api.py` + `chat/conversations_api.py` + `infrastructure/db/models.py`。新增 scheduler runner 写 ChatRunRow 不影响现有引用。

---

## 档位

**设计卡**

选档理由：
- 跨 ≥10 文件（后端 + 前端）
- 新增基础依赖（APScheduler）
- 新公开 API（7 个端点 + 3 个工具）
- 改 ChatPipeline 入口语义（新增 cron 触发路径，需保证旧路径不退化）
- 新增 DB 表
- 命中 § 1.4 落盘触发条件

---

## diff 预算

| 类型 | 文件数 | 行数 |
|---|---|---|
| 后端新增 | 9 | ~700 |
| 后端修改 | 4 | ~150 |
| 前端新增 | 5 | ~500 |
| 测试新增 | 3 | ~250 |
| 配置文档 | 2 | ~50 |
| **总计** | **23** | **~1650** |

量级：5-10 文件 + 5-10 文件 / 1000-2000 行。

**触及未列文件 → building 必须停下报告。**

---

## 代码级约束（命中项）

| 维度 | 约束 | 检查方式 |
|---|---|---|
| **可靠** | scheduler 自身崩溃不能拖垮 FastAPI 主进程；任务回调内任何异常都要 try/except 包住后写 `chat_runs.status='failed'`，不向上抛 | RED 测试 F2：mock `ChatPipeline.stream()` 抛 `RuntimeError`，断言 scheduler 仍 running，task.last_run_id 对应的 run 状态是 failed |
| **可靠** | 重启后 misfire 处理：APScheduler 默认 misfire_grace_time 较短，需显式配置 → 错过的任务在窗口内补跑 1 次，超出窗口标 missed | RED 测试 F3：用 frozen_time 模拟错过 1 小时，重启后 5min cron 任务只触发 1 次（不是连续触发 12 次） |
| **并发** | 同一任务的两次触发不能重叠（前一次未完成，新触发跳过）；不同任务并发受全局 Semaphore 限制 | 单测：同一任务连续触发 2 次，第 2 次因 task_id 锁被跳过；3 个任务同时到点，Semaphore=2，第 3 个等队 |
| **性能** | scheduler 自身 tick 不阻塞事件循环；任务回调 await ChatPipeline 的 LLM 调用走 asyncio | 启动后 `htop` 观察主线程 CPU 不持续高占；scheduler tick 间隔 ≥ 5s |
| **安全** | cron 表达式用 APScheduler 自带 `CronTrigger.from_crontab()` 解析，不自己 eval；`prompt` 字段走 `SecurityValidator.validate()` 过 XSS / 长度 | 单测：尝试创建 `expr='__import__("os").system("rm")'` 任务被拒；prompt 含 `<script>` 被截断或拒绝 |
| **安全** | tool_adapter 注入的 `schedule_task` 工具用 `_context.user_id` 强绑定；不允许 LLM 通过参数指定 `user_id` 跨用户创建 | 单测：`_context = {'user_id': 'alice'}`，tool 创建的 task.user_id 必须是 'alice'，无视入参 |
| **可维护** | `scheduling/` 模块严格遵循 `chat/` 同款 DDD 结构（domain / application / api 分层） | 代码评审：domain 仅有数据类 + 类型；application 是 use case；api 仅是路由层；不允许跨层调用 |
| **兼容** | 现有 190 个测试不退化 | `make test` 通过 |
| **兼容** | 旧 chat 路径（POST /chat）行为不变 | 单测：POST /chat 仍走 `_create_run_row`（即使迁移到 helper），request 字段不变 |

---

## 子模式展开（design 候选表）

### 候选 1：调度器选型

| 维度 | A. APScheduler 3.11.2 (stable) | B. APScheduler 4.0.0a6 (alpha) | C. 自研 asyncio loop scanner |
|---|---|---|---|
| async 集成 | AsyncIOScheduler 配 asyncio loop | 原生 AsyncScheduler，更优雅 | 完全自控 |
| 持久化 | SQLAlchemyJobStore（同步引擎，需配独立 sync conn） | SQLAlchemyDataStore（async 原生） | 自己写 SQLite 表扫 |
| 稳定性 | 6+ 年 stable，30k+ stars，海量生产案例 | alpha 状态，2025-04 后无新版（1+ 年没更新） | 取决于自己 |
| cron 解析 | 自带 `CronTrigger.from_crontab` | 同 | 自己写或引 croniter |
| misfire | 内建 `misfire_grace_time` 参数 | 同 | 自己实现 |
| 引入开销 | 一个新依赖 | 一个新依赖（alpha） | 零依赖但 ~300 行自研 |
| 文档生态 | 齐全，stackoverflow 答案多 | 文档迁移期，例子少 | 无 |
| 风险 | sync engine 与 async 引擎共存有点别扭 | alpha API 可能变化 + 项目活跃度存疑 | 重复造轮子 |

**Decision Drivers 评分（1-5）**：

| Driver | 权重 | A 3.x | B 4.x | C 自研 |
|---|---|---|---|---|
| 稳定性 | 高 | 5 | 2 | 3 |
| async 优雅度 | 中 | 3 | 5 | 5 |
| 上手成本 | 中 | 5 | 3 | 1 |
| 长期维护 | 高 | 5 | 2 | 2 |
| 文档/社区 | 中 | 5 | 3 | 1 |
| **加权得分** | | **23** | **15** | **12** |

**推荐 A. APScheduler 3.11.2**（虽然用户初选 4.x，但 4.0.0a6 仍 alpha 且 2025-04 后无更新；3.x AsyncIOScheduler 集成 FastAPI 已是 stackoverflow / sentry / digon.io 等多篇 2025 年 FastAPI scheduler 教程的标准做法）。

> 取舍说明：3.x sync SQLAlchemyJobStore 需要建一个 sync engine（与现有 async engine 并存）。这点开销可控（3 行代码），换来稳定性 + 6 年生产验证，值得。详见 § 决策记录。

### 候选 2：触发后的执行模式

| 维度 | A. 直接 await ChatPipeline.stream | B. 注入消息到现有 chat_runs 队列 | C. 起子进程 / 独立 worker |
|---|---|---|---|
| 复用度 | 高（直接调 prepare + stream） | 高（复用 _execute_run） | 低（需序列化 ctx） |
| 隔离 | 任务跑在 scheduler 线程内 | 跑在 _execute_run 的 asyncio.Task | 完全进程隔离 |
| 实现难度 | 低 | 中（需 hack scheduler 与 chat_run 队列对接） | 高 |
| 失败影响 | 任务失败 ≠ scheduler 崩 | 同 | 进程崩不影响主进程 |

**推荐 A. 直接 await ChatPipeline.stream**：scheduler 回调是普通 async function，直接 `await pipeline.prepare(...)` 再 `async for event in pipeline.stream(ctx):`，写入 ChatRunRow 用复用的 `_create_run_row` helper。等价于"在 scheduler 触发时手动执行一次 _execute_run 的核心循环"，最少改动。

### 候选 3：结果对话归属

| 维度 | A. 每次触发新建 conversation（推荐） | B. 复用一个固定 task_conversation |
|---|---|---|
| 用户体验 | 每次结果独立可见，对话列表自然出现新对话 | 同一对话越来越长，难追溯 |
| 模拟 ChatGPT Tasks | 完全一致 | 不一致 |
| DB 开销 | 每次触发 +1 conversation | 0 |
| 追问体验 | 用户可在新对话里继续追问，结果脱离任务上下文 | 追问留在原对话，但混杂多次结果 |

**推荐 A**：每次触发新建独立对话，title 取 `任务名 (YYYY-MM-DD HH:mm)`；同时 `scheduled_tasks` 表记录 `last_run_conversation_id` 方便从历史页跳转。

---

## S/B 拆分（设计卡必填）

**顺序固定**：S → 跑测试 → B → 跑新测试

### S 类（结构，行为不变）

| ID | 改动 | 文件 |
|---|---|---|
| S1 | 把 `chat/api.py:_create_run_row` 提到 `chat/application/run_factory.py`，命名 `create_chat_run_row()`，类型签名改为接受 `prompt: str` + `trigger_source: str = "user"`（默认与现有行为一致） | `chat/application/run_factory.py`（新）+ `chat/api.py`（改 import） |
| S2 | `_execute_run` 提取核心循环到 `chat/application/run_executor.py:execute_run_loop()`，scheduler 和 HTTP 都能调 | `chat/application/run_executor.py`（新）+ `chat/api.py`（改 _execute_run 为薄壳） |

S 类完成后跑：`make check && make test` —— 必须 190 测试全过，证明行为零变化。

### B 类（行为新增）

| ID | 改动 | 文件 |
|---|---|---|
| B1 | 加 `apscheduler>=3.11.2,<4` 到 `pyproject.toml`，install | `pyproject.toml` |
| B2 | 新增 `ScheduledTaskRow` ORM；`init_db` 自动 create_all | `infrastructure/db/models.py` |
| B3 | 新增 `scheduling/domain/task.py`（dataclass + enum） | `scheduling/domain/task.py` |
| B4 | 新增 `scheduling/scheduler.py`：单例 AsyncIOScheduler + lifespan 集成（add_job / remove_job / start / stop） | `scheduling/scheduler.py` |
| B5 | 新增 `scheduling/runner.py`：scheduler 触发回调 → 加载 task → create_chat_run_row → execute_run_loop | `scheduling/runner.py` |
| B6 | 新增 `scheduling/api.py`：7 个 REST 端点（list/get/create/update/delete/pause/resume/run-now） | `scheduling/api.py` |
| B7 | 新增 `scheduling/application/task_service.py`：CRUD + scheduler 同步 | `scheduling/application/task_service.py` |
| B8 | 工具注册：`schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task` 加到 `tools/builtin.py` | `tools/builtin.py` |
| B9 | factory.py lifespan 注册 scheduler 启动 / 停止 | `app/factory.py` |
| B10 | 路由注册：`app.include_router(scheduling.router, prefix="/api/v1/scheduled-tasks")` | `app/factory.py` |
| B11 | 配置：`config.yaml` 加 `scheduling: { enabled, max_tasks_per_user, default_timezone, misfire_grace_seconds, max_concurrent_runs }` | `config/config.yaml` + `sdk/config.py` |
| B12 | 系统提示词补充（提示 LLM 何时建议用 schedule_task） | `chat/pipeline.py:_build_system_prompt` |
| B13 | 前端：`features/scheduling/` 模块（页面 + Zustand store + API service + 表单 modal） | `frontend/src/features/scheduling/*` |
| B14 | 前端：路由注册 `/scheduled-tasks` + 侧边栏入口 | `frontend/src/App.tsx` + `frontend/src/components/Sidebar.tsx` |
| B15 | 测试：scheduler 启停、任务 CRUD、cron 触发链路、失败处理、misfire | `tests/scheduling/` |

每条 B 改动单独 commit，message 用 `feat(scheduling):` 前缀。

---

## 失败模式与验证

| # | 失败模式 | 级别 | 验证项（优先 RED 测试） |
|---|---|---|---|
| **F1** | scheduler 启动失败但 FastAPI 主服务已起，导致定时任务静默不工作（用户感知是"我的 task 怎么不跑"） | High | RED: `tests/scheduling/test_lifespan.py` mock `AsyncIOScheduler.start()` 抛错；断言 lifespan 把 error 写日志，主服务继续 yield；新增 `GET /api/v1/scheduled-tasks/_health` 端点返回 `{scheduler_running: bool}`，scheduler down 时返回 503 |
| **F2** | runner 触发时 ChatPipeline 抛 RuntimeError（如 LLM API 限流），导致 scheduler 自身崩或任务永远卡 running | High | RED: `tests/scheduling/test_runner.py` mock `ChatPipeline.stream()` 抛 `RuntimeError`；断言 `chat_runs.status='failed'`，error 字段非空；scheduler 仍能继续触发后续任务（ScheduleEvent 计数 +1） |
| **F3** | 进程重启后任务漂移：错过的任务全部连续补跑 N 次（如停了 1 小时，5min cron 任务连触 12 次） | High | RED: `tests/scheduling/test_misfire.py` 用 `freezegun` 模拟错过 1 小时；显式配 `misfire_grace_time=300`；断言重启后 5min cron 只触发 1 次（在 grace 窗口内的最后一次） |
| **F4** | 用户能创建无限多任务，磁盘 + scheduler 内存爆炸 | Med | 单测：连续 POST 创建 51 个任务，第 51 个返回 409 Conflict，error 含 "max_tasks_per_user" |
| **F5** | LLM 通过 `schedule_task` 工具传入非法 cron 表达式（如 `* * 30 2 *` 永远不触发；或 `'; DROP TABLE …`），创建后任务静默僵死 | High | 单测：tool 内调 `CronTrigger.from_crontab(expr)` 校验，非法时 raise ValueError；LLM 看到错误后能修正；防 SQL 注入靠 ORM 参数化（不裸拼） |
| **F6** | 多任务同一秒同时触发，N 路 LLM 调用串起队伍超时 | Med | 单测：3 任务同时到点，全局 `Semaphore=2`，第 3 个等队；总耗时 = 单 LLM 耗时 + 等队耗时 |
| **F7** | LLM 通过 schedule_task 创建任务时绕过 user_id 隔离（如传入别人的 conversation_id） | High | 单测：tool 函数从 `_context['user_id']` 取 user，不接受 user_id 入参；conversation_id 必须 owner == user_id（在 task_service 校验） |
| **F8** | 时区错（用户在上海，APScheduler 默认 UTC） | Med | 单测：创建 `cron='0 8 * * *', timezone='Asia/Shanghai'` → APScheduler `next_fire_time` 等于本地 8:00 而非 UTC 8:00；用 `ZoneInfo` 解析 |
| **F9** | scheduler 与 SQLAlchemyJobStore 的 sync engine 与现有 async engine 双写竞争（任务下次触发时间被覆盖） | Med | 单测：APScheduler 的 jobstore 用独立 sync engine（指向相同 SQLite 文件），不与 async session 同事务；高并发触发时 scheduler 状态正确 |
| **F10** | 前端创建任务时输入了 `Asia/Shanghai` 但后端默认用 UTC，next_run_at 显示偏 8 小时 | Med | E2E 测试：POST 创建 cron 任务，response 的 `next_run_at` 字段以 ISO8601 + 时区返回；前端正确按本地时区渲染 |

**High 级（F1, F2, F3, F5, F7）必须有针对性 RED 测试在 `tests/scheduling/` 下。**

---

## 推荐与决策

### 推荐方案

**调度器**：APScheduler **3.11.2 stable** + AsyncIOScheduler + SQLAlchemyJobStore（独立 sync engine 指向同一 SQLite 文件）

**触发模式**：scheduler 回调 → `runner.fire(task_id)` → 加载任务 → `create_chat_run_row(prompt=task.prompt, trigger_source='schedule')` → 复用 `execute_run_loop` 跑一轮

**结果对话归属**：每次触发新建独立 ConversationRow（task_id 写入 metadata），title = `任务名 (执行时间)`

**入口双通道**：
- LLM 工具（对话内创建）：`schedule_task(prompt, trigger_type, trigger_config, name=None, timezone=None)` 返回 task_id + next_run_at
- REST API（UI 管理）：7 个端点全 CRUD

### 为什么选它（逐 Driver 解释）

- **稳定性**：3.11.2 是 6+ 年验证的 stable 版本，4.0.0a6 截至 2026-06-18 仍是 alpha 且 2025-04 后无新版（连续 14 个月无更新，活跃度存疑）。生产场景不应押注未发布 release 的 alpha 库
- **async 优雅度**：AsyncIOScheduler 配 asyncio loop 完全够用；4.x 的 AsyncScheduler 只在 jobstore 层面更 async，对回调函数来说没有区别（回调本来就可以是 async function）
- **触发模式 A**：`ChatPipeline` 是无状态的，scheduler 回调里直接 `await pipeline.prepare(...).stream(...)` 跟 HTTP 路径行为完全等价；C（独立进程）需要序列化 context，对一个共享 SQLite 的单进程项目没必要
- **结果归属 A**：复刻 ChatGPT Tasks 的成熟模式，用户回到 web 端在对话列表自然看到新结果，不污染历史对话；任务历史页提供集中视图

### 为什么不选其他

- **不选 4.0.0a6**：alpha 风险 + 14 个月无更新；如果 4.x 正式版发了再升级，迁移成本可控（API 类似）
- **不选自研调度**：cron 解析 / misfire / 持久化 / 时区 / 并发都是已被解决的问题，重写一份 ~300 行代码价值不大且引入 bug 风险
- **不选模式 B（注入消息到 chat_runs 队列）**：现有 chat_runs 队列是 HTTP request 触发的，hack 它会让"用户发起 vs scheduler 发起"逻辑混杂；模式 A 更清晰
- **不选模式 C（子进程）**：项目单进程 + SQLite，进程隔离价值低，序列化开销高
- **不选共用任务对话**：所有任务结果堆一个对话最终变成"任务噪音垃圾桶"，体验差

### 影响范围

**改公开接口**：
- ✅ ChatPipeline 调用方仅 3 处（HTTP / SDK / 自身），新增 cron 触发不破坏旧路径
- ✅ `_create_run_row` 仅 1 处调用，提取为 helper 不影响行为
- ⚠️ 数据库新增表，需要在已有数据库上重新 init_db 触发 create_all（生产环境需要 alembic 迁移，本项目目前用 init_db 自动建表机制）
- ⚠️ pyproject.toml 加新依赖，所有部署需要重新 `make setup`

**新增公开接口**：
- 7 个 REST 端点：`/api/v1/scheduled-tasks` GET/POST + `/{id}` GET/PUT/DELETE + `/{id}/pause` + `/{id}/resume` + `/{id}/run-now`
- 1 个健康端点：`/api/v1/scheduled-tasks/_health`
- 3 个 LLM 工具：`schedule_task` / `list_scheduled_tasks` / `cancel_scheduled_task`

**前端新增**：
- 1 个路由：`/scheduled-tasks`
- 1 个侧边栏入口

### 下一步实施边界（building 任务契约）

按 S → B 顺序执行：

**Phase S（结构整理，行为不变）**：
1. S1: 提取 `_create_run_row` 到 `chat/application/run_factory.py`
2. S2: 提取 `_execute_run` 核心循环到 `chat/application/run_executor.py`
3. 跑 `make check && make test` 必须 190 全过

**Phase B（行为新增，按 B1-B15 顺序）**：
- B1-B2: 依赖 + ORM
- B3-B5: scheduler 核心（domain + scheduler + runner）
- B6-B7: API + service
- B8: tool 注册
- B9-B10: lifespan + 路由挂载
- B11-B12: 配置 + 提示词
- B13-B14: 前端
- B15: 测试

每个 commit 跑测试通过再下一步。

### 验证计划

| 验证项 | 命令 / 现象 | 通过条件 |
|---|---|---|
| 单元测试 | `make test tests/scheduling/` | 全部 GREEN |
| 现有测试不退化 | `make test` | 190+N 全 GREEN（N = 新增数量） |
| Lint + Type | `make check` | 无 error |
| 启动验证 | `make api`，看日志 | 出现 "Scheduler started with N jobs" |
| F1 RED→GREEN | mock scheduler.start() 抛错 | lifespan 不阻塞，/_health 返回 503 |
| F2 RED→GREEN | mock pipeline 抛 RuntimeError | chat_runs.status=failed，scheduler 继续 |
| F3 RED→GREEN | freezegun 模拟错过 1h | 5min cron 重启后触发 1 次 |
| F5 RED→GREEN | 非法 cron expr | tool 返回 ValueError |
| F7 RED→GREEN | 跨用户访问任务 | 403 Forbidden |
| 端到端 | curl POST 创建 → 等到点 → 查 chat_runs | 自动有新 run，对话列表有新会话 |
| 前端 | 浏览器访问 /scheduled-tasks | 列表正常渲染，创建表单可用 |

### 重评估条件

- APScheduler 4.x 出 stable release 时 → 评估升级（接口类似，迁移成本小）
- 项目准备多 worker 部署时 → 同时迁移 `_ACTIVE_RUNS` 和 scheduler 到 Redis（Redlock + RedisJobStore）
- 单用户任务突破 50 个成为常态 → 重新评估限额或加分页 jobstore 加载
- 出现"必须秒级触发的高频任务"需求 → APScheduler 不适合，需引入 Celery Beat 或专用调度
- 出现跨任务依赖（A 完成后触发 B）需求 → 走 `agent` 模块的 NativeWorkflowOrchestrator，scheduling 仅做时间触发

---

## 决策记录

**标题**：定时任务系统选型 — APScheduler 3.x stable vs 4.x alpha

**日期**：2026-06-18

**上下文与问题**：
用户希望 AI 助手支持定时任务（对标 ChatGPT Tasks）。AskUserQuestion 中用户选了 "APScheduler 4.x"。但实际查证后 APScheduler 4.0.0a6 仍是 pre-release alpha 且自 2025-04-27 后未发布新版，连续 14 个月无更新。用户的选择基于"async 一等公民更现代"的直觉，但忽视了项目阶段（alpha）。

**Decision Drivers**：
- 稳定性（高权重）：生产可用性
- async 集成质量（中）：与 FastAPI lifespan 配合
- 长期维护（高）：库本身的活跃度与未来 release
- 上手成本（中）：文档完整度

**候选与权衡**：
- A. 3.11.2 stable：成熟、文档齐、6 年验证；缺点是 jobstore 用 sync engine（多 3 行代码）
- B. 4.0.0a6 alpha：async 原生；缺点是 alpha 状态 + 1 年多无新版 + 文档迁移期
- C. 自研：~300 行代码自己实现 cron + misfire + 持久化，成本高且无收益

**决策结果**：选 A（APScheduler 3.11.2 stable）。

**正面后果**：
- 6 年验证的稳定库，stackoverflow 答案多
- 文档完整，新人上手快
- 失败模式被社区充分讨论，bug 已知

**负面后果**：
- 引入一个 sync SQLAlchemy engine（与现有 async engine 共存）
- 配 jobstore 时多 3 行模板代码
- 未来如果 4.x 出 stable 需要迁移（迁移成本可控，API 相似）

**重评估条件**：APScheduler 4.x 出 stable release（不再是 alpha）→ 重新评估升级；或项目转多 worker → 同步迁移到 Redis-based jobstore。

---

## Open Questions

| 问题 | 默认假设 | 何时需要用户拍板 |
|---|---|---|
| 是否接受 3.x 替代用户初选的 4.x？ | 接受（见决策记录理由） | building 开始前需用户最终确认 |
| `scheduling.max_tasks_per_user` 默认 50 是否合适？ | 是（ChatGPT 限 10 偏严） | 上线后看用户反馈 |
| 是否需要前端"任务模板库"（预设的常见任务模板）？ | 首版不做 | M+1 milestone |
| 前端是否需要桌面通知 API（`Notification.requestPermission()`）？ | 首版不做 | M+1 milestone |
| 是否需要 `enabled: bool` 配置开关，方便整体禁用 scheduling？ | 是（`config.yaml: scheduling.enabled` 默认 true） | 已纳入 B11 |

---

## 落盘说明

本文件路径：`docs/astra/2026-06-18-scheduled-tasks.md`

building skill 开始实施时请以此文件为契约：
- 触及 Non-Goals → 停下回 planning 重评估
- 超 diff 预算（>2000 行 / >25 文件）→ 停下回 planning 重评估
- 越 S/B 切片顺序（B 类先于 S 类）→ 停下回 planning 重评估
- 出现新的失败模式（不在 F1-F10 内）→ 添加到表里，对应 RED 测试补齐
