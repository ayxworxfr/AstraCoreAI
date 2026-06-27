"""内置工具集合，注册到 NativeToolAdapter 供工具循环使用。"""

import ast
import asyncio
import math
import os
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, cast

from astracore.infrastructure.tools.composite import CompositeToolAdapter
from astracore.infrastructure.tools.native import NativeToolAdapter
from astracore.infrastructure.tools.parallel_agent import ParallelAgentTool
from astracore.modules.tools.ports.tool import ToolAdapter, ToolParameter, ToolParameterType
from astracore.sdk.config import AstraCoreConfig
from astracore.shared.domain.hitl import HITLOption, PendingQuestion
from astracore.shared.policy.engine import PolicyEngine

# 安全数学求值白名单
_SAFE_MATH: dict[str, Any] = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
_SAFE_MATH["abs"] = abs

_SAFE_AST_NODES = {
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.FloorDiv,
    ast.USub,
    ast.UAdd,
}


def _get_current_time(timezone_name: str = "Asia/Shanghai") -> str:
    now = datetime.now(UTC)
    return f"当前 UTC 时间：{now.strftime('%Y-%m-%d %H:%M:%S UTC')}（时区参数：{timezone_name}）"


def _calculate(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval")
        for node in ast.walk(tree):
            if type(node) not in _SAFE_AST_NODES:
                return f"不支持的表达式类型：{type(node).__name__}"
        result = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, _SAFE_MATH)  # noqa: S307
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算失败：{e}"


async def _tavily_search(query: str, max_results: int, api_key: str) -> str:
    import httpx  # noqa: PLC0415

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "include_answer": True,
            },
        )
        data = resp.json()
    parts: list[str] = []
    if data.get("answer"):
        parts.append(f"摘要：{data['answer']}")
    for r in data.get("results", []):
        parts.append(
            f"标题：{r.get('title', '无标题')}\n"
            f"内容：{r.get('content', '')}\n"
            f"URL：{r.get('url', '')}"
        )
    return "\n\n---\n\n".join(parts) if parts else "未找到相关搜索结果"


async def _duckduckgo_search(query: str, max_results: int) -> str:
    from ddgs import DDGS  # noqa: PLC0415

    def _sync() -> list[dict[str, Any]]:
        return list(DDGS().text(query, max_results=max_results))

    results = await asyncio.to_thread(_sync)
    if not results:
        return "未找到相关搜索结果"
    parts = [
        f"标题：{r.get('title', '无标题')}\n内容：{r.get('body', '')}\nURL：{r.get('href', '')}"
        for r in results
    ]
    return "\n\n---\n\n".join(parts)


async def _web_search(query: str, max_results: int = 5) -> str:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    try:
        if api_key:
            return await _tavily_search(query, max_results, api_key)
        return await _duckduckgo_search(query, max_results)
    except Exception as e:
        return f"搜索失败：{e}"


def build_tool_adapter(db_url: str = "") -> ToolAdapter:
    """构造并注册所有内置工具（含技能工具），返回 CompositeToolAdapter。

    新增工具时只需在此函数中追加 register_tool 调用即可。
    spawn_agents 工具自动注入到 ParallelAgentTool；worker 工具集 = 内置工具 + 技能工具，
    防止子 Agent 递归调用 spawn_agents（深度限制 = 1）。
    """
    # 延迟导入避免循环依赖（rag_api 依赖 chat_api 的 lru_cache 工厂）
    from astracore.modules.rag import api as rag_api  # noqa: PLC0415

    async def _search_knowledge_base(query: str, top_k: int = 3) -> str:
        try:
            pipeline = rag_api._get_rag_pipeline()
            chunks = await pipeline.retrieve_with_citations(query=query, top_k=top_k)
            if not chunks:
                return "知识库中未找到相关内容。"
            parts = [
                f"[{i + 1}] 来源：{c.citation.title or c.citation.source_id}\n{c.content}"
                for i, c in enumerate(chunks)
            ]
            return "\n\n".join(parts)
        except Exception as e:
            return f"知识库搜索失败：{e}"

    async def _compact_memory(_context: dict[str, object] | None = None) -> str:
        from uuid import UUID  # noqa: PLC0415

        from astracore.infrastructure.memory.store import SQLMemoryStore  # noqa: PLC0415
        from astracore.modules.memory.application.engine import MemoryEngine  # noqa: PLC0415

        ctx = _context or {}
        session_id_str = ctx.get("session_id")
        if not session_id_str:
            return "无法获取当前会话 ID，记忆压缩失败。"

        session_id = UUID(str(session_id_str))
        from astracore.shared.ports.llm import LLMAdapter  # noqa: PLC0415

        raw_llm = ctx.get("llm_adapter")
        llm_adapter = raw_llm if isinstance(raw_llm, LLMAdapter) else None
        model_raw = ctx.get("model")
        model = str(model_raw) if model_raw is not None else None
        user_id = str(ctx.get("user_id") or "default")

        engine = MemoryEngine(SQLMemoryStore(db_url), user_id=user_id)
        result = await engine.compact_session_memories(
            session_id=session_id,
            llm_adapter=llm_adapter,
            model=model,
            force=True,
        )
        if result is None:
            return "当前会话记忆不足，无法压缩（至少需要 2 条可压缩记忆）。"
        compressed_count = result.metadata.get("compressed_from_count", "若干")
        return f"记忆压缩完成：已将 {compressed_count} 条会话记忆合并为一条摘要。"

    async def _save_memory(
        content: str,
        subject: str = "",
        memory_type: str = "fact",
        scope: str = "session",
        importance: int = 3,
        _context: dict[str, object] | None = None,
    ) -> str:
        from uuid import UUID  # noqa: PLC0415

        from astracore.infrastructure.memory.store import SQLMemoryStore  # noqa: PLC0415
        from astracore.modules.memory.application.engine import MemoryEngine  # noqa: PLC0415
        from astracore.modules.memory.domain import MemoryScope, MemoryType  # noqa: PLC0415

        _VALID_TYPES = {"fact", "preference", "decision", "constraint", "state", "plan", "lesson"}
        _VALID_SCOPES = {"user", "session", "global"}

        if memory_type not in _VALID_TYPES:
            memory_type = "fact"
        if scope not in _VALID_SCOPES:
            scope = "user"
        importance = max(1, min(5, importance))

        ctx = _context or {}

        # HITL: ask user before persisting user/global scope memories when enabled.
        if scope in ("user", "global"):
            callback = ctx.get("hitl_callback")
            if callback is not None:
                cfg = AstraCoreConfig()
                if cfg.hitl.enabled and cfg.hitl.require_tool_approval:
                    q = PendingQuestion(
                        question=(
                            f"AI 想保存以下记忆（{scope} 级，跨会话持久）：\n\n{content[:300]}"
                        ),
                        header="记忆审批",
                        options=[
                            HITLOption(label="允许", description="保存此记忆"),
                            HITLOption(label="拒绝", description="取消保存"),
                        ],
                        allow_freeform=False,
                    )
                    _cb = cast(Callable[..., Coroutine[Any, Any, dict[str, Any]]], callback)
                    answer = await _cb(q)
                    if "允许" not in answer.get("selected", []):
                        return "用户拒绝保存此记忆。"

        session_id_str = ctx.get("session_id")
        session_id = UUID(str(session_id_str)) if session_id_str else None
        user_id = str(ctx.get("user_id") or "default")

        engine = MemoryEngine(SQLMemoryStore(db_url), user_id=user_id)
        memory = await engine.create_memory(
            scope=MemoryScope(scope),
            memory_type=MemoryType(memory_type),
            content=content,
            subject=subject,
            summary=content[:120],
            session_id=session_id,
            importance=importance,
            confidence=1.0,
        )
        preview = content[:80] + ("…" if len(content) > 80 else "")
        return f"记忆已保存（ID: {memory.id}，范围: {scope}，类型: {memory_type}）：{preview}"

    async def _recall_memory(
        query: str,
        scope: str = "",
        memory_type: str = "",
        limit: int = 10,
        _context: dict[str, object] | None = None,
    ) -> str:
        from uuid import UUID  # noqa: PLC0415

        from astracore.infrastructure.memory.store import SQLMemoryStore  # noqa: PLC0415
        from astracore.modules.memory.application.engine import MemoryEngine  # noqa: PLC0415
        from astracore.modules.memory.domain import MemoryScope, MemoryType  # noqa: PLC0415

        ctx = _context or {}
        session_id_str = ctx.get("session_id")
        user_id = str(ctx.get("user_id") or "default")

        resolved_scope = MemoryScope(scope) if scope in {s.value for s in MemoryScope} else None
        resolved_type = (
            MemoryType(memory_type) if memory_type in {t.value for t in MemoryType} else None
        )
        limit = max(1, min(20, limit))

        session_id = (
            UUID(str(session_id_str))
            if (session_id_str and resolved_scope == MemoryScope.SESSION)
            else None
        )

        engine = MemoryEngine(SQLMemoryStore(db_url), user_id=user_id)
        memories = await engine.list_memories(
            scope=resolved_scope,
            memory_type=resolved_type,
            session_id=session_id,
            query=query,
            limit=limit,
        )
        if not memories:
            return "未找到相关记忆。"
        parts = [
            f"[{m.id}]\n范围: {m.scope}  类型: {m.type}  重要度: {m.importance}\n主题: {m.subject or '—'}\n{m.content}"
            for m in memories
        ]
        return "\n\n".join(parts)

    async def _delete_memory(memory_id: str, _context: dict[str, object] | None = None) -> str:
        from astracore.infrastructure.memory.store import SQLMemoryStore  # noqa: PLC0415
        from astracore.modules.memory.application.engine import MemoryEngine  # noqa: PLC0415

        ctx = _context or {}
        user_id = str(ctx.get("user_id") or "default")
        engine = MemoryEngine(SQLMemoryStore(db_url), user_id=user_id)
        existing = await engine.get_memory(memory_id)
        if existing is None:
            return f"未找到 ID 为 {memory_id!r} 的记忆。"
        await engine.delete_memory(memory_id)
        return f"记忆已删除（ID: {memory_id}）。"

    async def _ask_user(
        question: str,
        options: list[str] | None = None,
        header: str = "",
        allow_freeform: bool = True,
        _context: dict[str, object] | None = None,
    ) -> str:
        ctx = _context or {}
        callback = ctx.get("hitl_callback")
        if callback is None:
            return "当前环境不支持用户交互，请继续执行。"

        option_items = [HITLOption(label=o) for o in (options or [])]
        if not option_items and not allow_freeform:
            option_items = [HITLOption(label="好的")]

        q = PendingQuestion(
            question=question,
            header=header,
            options=option_items,
            allow_freeform=allow_freeform,
        )
        _cb = cast(Callable[..., Coroutine[Any, Any, dict[str, Any]]], callback)
        answer = await _cb(q)

        if answer.get("error"):
            return f"等待用户回复超时（{answer['error']}），请继续执行。"

        parts: list[str] = []
        selected = answer.get("selected", [])
        if selected:
            parts.append("用户选择：" + "、".join(selected))
        freeform = answer.get("freeform")
        if freeform:
            parts.append(f"用户补充：{freeform}")
        return "\n".join(parts) if parts else "用户未作选择，请继续执行。"

    native = NativeToolAdapter()

    native.register_tool(
        name="get_current_time",
        func=_get_current_time,
        description="获取当前日期和时间。当用户询问现在几点、今天日期等问题时使用。",
        parameters=[
            ToolParameter(
                name="timezone_name",
                type=ToolParameterType.STRING,
                description="时区名称，例如 Asia/Shanghai、UTC、America/New_York",
                required=False,
            )
        ],
    )

    native.register_tool(
        name="calculate",
        func=_calculate,
        description="对数学表达式求值，支持加减乘除、幂运算、取模等基本运算。",
        parameters=[
            ToolParameter(
                name="expression",
                type=ToolParameterType.STRING,
                description="数学表达式，例如 '2 ** 10'、'(3 + 5) * 7'",
                required=True,
            )
        ],
    )

    native.register_tool(
        name="search_knowledge_base",
        func=_search_knowledge_base,
        description=(
            "在知识库（RAG）中搜索与查询相关的文档片段。"
            "当需要查找 AstraCoreAI 的功能、架构、使用方法等信息时使用。"
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ToolParameterType.STRING,
                description="搜索查询语句",
                required=True,
            ),
            ToolParameter(
                name="top_k",
                type=ToolParameterType.NUMBER,
                description="返回结果数量，默认 3",
                required=False,
            ),
        ],
        # 知识库片段量级一般 5-10 条 × 1-2 KB，单次 8000 字符足够；
        # 防止极端长文档把 context 撑爆触发 LLM API 流式超时。
        metadata={"max_output_chars": 8_000},
    )

    native.register_tool(
        name="web_search",
        func=_web_search,
        description=(
            "在互联网上搜索实时信息。当需要查询最新新闻、当前事件、"
            "实时数据或训练数据截止日期之后的信息时使用。"
            "在进行网络搜索的时候检查当前年份是否正确。"
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ToolParameterType.STRING,
                description="搜索关键词或问题",
                required=True,
            ),
            ToolParameter(
                name="max_results",
                type=ToolParameterType.NUMBER,
                description="返回结果数量，默认 5",
                required=False,
            ),
        ],
        # WebSearch 单条 result 平均 500-1500 字符 × 5 条 ≈ 5 KB；
        # 8000 字符上限避免多轮联网搜索堆叠后 context 爆炸（10 次 × 8K = 80K，可控）。
        metadata={"max_output_chars": 8_000},
    )

    native.register_tool(
        name="compact_memory",
        func=_compact_memory,
        description=(
            "将当前会话中积累的多条短期记忆压缩合并为一条摘要，释放记忆槽位。"
            "当用户要求「压缩记忆」「整理记忆」或会话记忆条数较多时使用。"
        ),
        parameters=[],
    )

    native.register_tool(
        name="save_memory",
        func=_save_memory,
        description=(
            "将重要信息主动写入长期记忆，无需等待对话结束后的自动提取。"
            "当用户明确表达偏好、做出决策、提到项目状态、约束条件或有长期价值的背景信息时，"
            "应主动调用此工具保存，以便未来对话中复用。"
            "普通闲聊、一次性问答、临时指令无需保存。"
        ),
        parameters=[
            ToolParameter(
                name="content",
                type=ToolParameterType.STRING,
                description="要记住的具体内容，完整、准确",
                required=True,
            ),
            ToolParameter(
                name="subject",
                type=ToolParameterType.STRING,
                description="记忆主题或标题（简短，便于检索），选填",
                required=False,
            ),
            ToolParameter(
                name="memory_type",
                type=ToolParameterType.STRING,
                description=(
                    "记忆类型（选填，默认 fact）："
                    "fact（事实）/ preference（偏好）/ decision（决策）/ "
                    "constraint（约束）/ state（项目状态）/ plan（计划）/ lesson（经验教训）"
                ),
                required=False,
            ),
            ToolParameter(
                name="scope",
                type=ToolParameterType.STRING,
                description="范围（选填，默认 session）：session（仅本次会话）/ user（跨会话持久）/ global",
                required=False,
            ),
            ToolParameter(
                name="importance",
                type=ToolParameterType.NUMBER,
                description="重要程度 1-5（选填，默认 3），5 为最重要",
                required=False,
            ),
        ],
    )

    native.register_tool(
        name="recall_memory",
        func=_recall_memory,
        description=(
            "从长期记忆中检索与查询相关的信息。"
            "当需要回顾用户偏好、过往决策、项目状态或历史背景时使用。"
            "返回结果包含记忆 ID，可配合 delete_memory 删除。"
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ToolParameterType.STRING,
                description="搜索关键词或问题",
                required=True,
            ),
            ToolParameter(
                name="scope",
                type=ToolParameterType.STRING,
                description="范围筛选（选填）：user / session / global / project",
                required=False,
            ),
            ToolParameter(
                name="memory_type",
                type=ToolParameterType.STRING,
                description="类型筛选（选填）：fact / preference / decision / constraint / state / plan / lesson",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type=ToolParameterType.NUMBER,
                description="返回条数，默认 10，最多 20",
                required=False,
            ),
        ],
    )

    native.register_tool(
        name="delete_memory",
        func=_delete_memory,
        description=(
            "删除一条指定的记忆。"
            "当用户要求删除某条记忆、或发现记忆内容有误/过时时使用。"
            "需先通过 recall_memory 获取记忆 ID。"
        ),
        parameters=[
            ToolParameter(
                name="memory_id",
                type=ToolParameterType.STRING,
                description="记忆 ID（从 recall_memory 返回结果中获取）",
                required=True,
            ),
        ],
        requires_confirmation=True,
    )

    native.register_tool(
        name="ask_user",
        func=_ask_user,
        description=(
            "向用户提出一个问题并等待回复。"
            "调用后执行会暂停，直到用户作答后自动继续，作答内容作为工具结果返回。\n"
            "使用场景：\n"
            "- 需要用户确认是否执行某个有风险或不可逆的操作\n"
            "- 让用户在多个候选方案中做选择\n"
            "- 缺少关键信息且无法合理推断时\n"
            "不要为每一步都询问用户；可以推断或继续执行的场景不要使用。"
        ),
        parameters=[
            ToolParameter(
                name="question",
                type=ToolParameterType.STRING,
                description="向用户提出的问题内容",
                required=True,
            ),
            ToolParameter(
                name="options",
                type=ToolParameterType.ARRAY,
                description="可选项列表（字符串数组），留空则只显示自由文本输入框",
                required=False,
            ),
            ToolParameter(
                name="header",
                type=ToolParameterType.STRING,
                description="问题卡片标题（选填）",
                required=False,
            ),
            ToolParameter(
                name="allow_freeform",
                type=ToolParameterType.BOOLEAN,
                description="是否允许用户输入自由文本（默认 true）",
                required=False,
            ),
        ],
    )

    # ------------------------------------------------------------------
    # Scheduling tools: schedule_task / list_scheduled_tasks / cancel_scheduled_task
    # ------------------------------------------------------------------

    async def _schedule_task(
        prompt: str,
        trigger_type: str,
        trigger_config: dict[str, Any],
        name: str | None = None,
        timezone: str | None = None,
        model_profile: str | None = None,
        _context: dict[str, object] | None = None,
    ) -> str:
        from astracore.modules.scheduling.application.task_service import (  # noqa: PLC0415
            ScheduledTaskService,
        )
        from astracore.modules.scheduling.domain.task import (  # noqa: PLC0415
            CreateTaskRequest,
            TriggerType,
        )

        ctx = _context or {}
        user_id = str(ctx.get("user_id") or "default")
        session_id_raw = ctx.get("session_id")
        conversation_id = str(session_id_raw) if session_id_raw is not None else None
        # 未显式指定时，沿用当前对话的模型 profile；都没有则使用系统默认值
        _raw_profile = ctx.get("profile_id")
        effective_profile: str | None = model_profile or (
            str(_raw_profile) if _raw_profile is not None else None
        )
        cfg = AstraCoreConfig()

        try:
            svc = ScheduledTaskService(
                db_url or cfg.storage.db_url, cfg.scheduling.default_timezone
            )
            task = await svc.create_task(
                CreateTaskRequest(
                    user_id=user_id,
                    prompt=prompt,
                    trigger_type=TriggerType(trigger_type),
                    trigger_config=trigger_config,
                    name=name,
                    timezone=timezone,
                    model_profile=effective_profile,
                    conversation_id=conversation_id,
                ),
                max_per_user=cfg.scheduling.max_tasks_per_user,
            )
        except Exception as exc:
            return f"创建定时任务失败：{exc}"

        next_str = task.next_run_at.isoformat() if task.next_run_at else "未知"
        return (
            f"定时任务已创建（ID: {task.id}）\n"
            f"名称：{task.name}\n"
            f"触发器：{task.trigger_type.value} — {task.trigger_config}\n"
            f"下次执行：{next_str}"
        )

    async def _list_scheduled_tasks(
        status: str | None = None,
        _context: dict[str, object] | None = None,
    ) -> str:
        from astracore.modules.scheduling.application.task_service import (  # noqa: PLC0415
            ScheduledTaskService,
        )
        from astracore.modules.scheduling.domain.task import TaskFilter, TaskStatus  # noqa: PLC0415

        ctx = _context or {}
        user_id = str(ctx.get("user_id") or "default")
        cfg = AstraCoreConfig()
        svc = ScheduledTaskService(db_url or cfg.storage.db_url, cfg.scheduling.default_timezone)
        status_filter = (
            TaskStatus(status) if status and status in {s.value for s in TaskStatus} else None
        )
        tasks, total = await svc.list_tasks(
            TaskFilter(user_id=user_id, status=status_filter, page=1, page_size=20)
        )
        if not tasks:
            return "当前没有定时任务。"
        lines = [f"共 {total} 个定时任务："]
        for t in tasks:
            next_str = t.next_run_at.isoformat() if t.next_run_at else "—"
            lines.append(f"• [{t.status.value}] {t.name}（ID: {t.id}）  下次执行：{next_str}")
        return "\n".join(lines)

    async def _cancel_scheduled_task(
        task_id: str,
        _context: dict[str, object] | None = None,
    ) -> str:
        from astracore.modules.scheduling.application.task_service import (  # noqa: PLC0415
            ScheduledTaskService,
        )

        ctx = _context or {}
        user_id = str(ctx.get("user_id") or "default")
        cfg = AstraCoreConfig()
        svc = ScheduledTaskService(db_url or cfg.storage.db_url, cfg.scheduling.default_timezone)
        deleted = await svc.delete_task(task_id, user_id)
        if deleted:
            return f"定时任务（ID: {task_id}）已删除。"
        return f"未找到 ID 为 {task_id!r} 的定时任务，或无权操作。"

    native.register_tool(
        name="schedule_task",
        func=_schedule_task,
        description=(
            "创建一个定时任务，让 AI 在指定时间或周期性地自动执行某个操作。"
            "创建成功后返回任务 ID 与下次执行时间。\n"
            "触发信号：\n"
            "- 「每天…」「每周一…」「每小时…」→ trigger_type=cron 或 interval\n"
            "- 「下周三 9:00…」「在某具体时刻…」→ trigger_type=date\n"
            "- 「提醒我…」「自动发送…」「定期汇总…」→ 同上\n"
            "支持的触发器：\n"
            "  cron — 标准 5 字段 cron 表达式（如 '0 9 * * *' 每天 9 点）\n"
            "  interval — 每隔 N 秒执行一次\n"
            "  date — 一次性在指定时刻执行（ISO8601 格式）\n"
            "管理已有任务请用 list_scheduled_tasks 与 cancel_scheduled_task。"
        ),
        parameters=[
            ToolParameter(
                name="prompt",
                type=ToolParameterType.STRING,
                description="任务的意图描述，触发时作为用户消息送入 AI",
                required=True,
            ),
            ToolParameter(
                name="trigger_type",
                type=ToolParameterType.STRING,
                description="触发器类型：cron | interval | date",
                required=True,
            ),
            ToolParameter(
                name="trigger_config",
                type=ToolParameterType.OBJECT,
                description=(
                    "触发器参数：\n"
                    '  cron → {"expr": "0 9 * * *"}\n'
                    '  interval → {"seconds": 3600}\n'
                    '  date → {"run_at": "2026-06-20T14:00:00+08:00"}'
                ),
                required=True,
            ),
            ToolParameter(
                name="name",
                type=ToolParameterType.STRING,
                description="任务显示名称（选填，缺省取 prompt 前 24 字）",
                required=False,
            ),
            ToolParameter(
                name="timezone",
                type=ToolParameterType.STRING,
                description="时区（选填，默认 Asia/Shanghai）",
                required=False,
            ),
            ToolParameter(
                name="model_profile",
                type=ToolParameterType.STRING,
                description="触发时使用的模型 profile ID（选填）",
                required=False,
            ),
        ],
    )

    native.register_tool(
        name="list_scheduled_tasks",
        func=_list_scheduled_tasks,
        description="列出当前用户的定时任务。返回任务 ID、名称、状态和下次执行时间。",
        parameters=[
            ToolParameter(
                name="status",
                type=ToolParameterType.STRING,
                description="状态筛选（选填）：active | paused | finished",
                required=False,
            ),
        ],
    )

    native.register_tool(
        name="cancel_scheduled_task",
        func=_cancel_scheduled_task,
        description="删除（取消）一个定时任务。需先通过 list_scheduled_tasks 获取任务 ID。",
        parameters=[
            ToolParameter(
                name="task_id",
                type=ToolParameterType.STRING,
                description="要删除的任务 ID",
                required=True,
            ),
        ],
        requires_confirmation=True,
    )

    # 技能工具：load_skill / get_skill_reference / run_skill_script
    combined: ToolAdapter
    if db_url:
        from astracore.modules.skills.tools import build_skill_tools_adapter  # noqa: PLC0415

        skill_tools = build_skill_tools_adapter(db_url)
        combined = CompositeToolAdapter([skill_tools, native])
    else:
        combined = native

    config = AstraCoreConfig()
    if config.agent.enable_spawn_agents:
        parallel = ParallelAgentTool(config=config, worker_tools=combined, policy=PolicyEngine())
        return CompositeToolAdapter([parallel, combined])
    return combined
