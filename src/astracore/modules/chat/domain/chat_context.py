"""Immutable execution parameters for a single chat turn.

``ChatContext`` is produced by ``ChatPipeline.prepare()`` after all DB queries and
business-logic decisions are resolved.  ``stream()`` consumes it as pure data—zero
conditional branching on raw request fields, zero extra I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

if TYPE_CHECKING:
    from astracore.modules.tools.ports.tool import ToolAdapter
    from astracore.sdk.config import LLMProfileConfig


@dataclass(frozen=True)
class ChatContext:
    """Fully-resolved, immutable context for one chat turn.

    由 ``ChatPipeline.prepare()`` 在一次 DB 批量查询后构造，之后 ``stream()``
    纯执行阶段只读本对象，不再做任何 DB 访问或条件分支。

    Fields are set exactly once in ``prepare()`` and never mutated.
    Comparison and hashing ignore mutable/large objects (profile, tool_adapter,
    llm_kwargs) via ``compare=False, hash=False``.
    """

    # ------------------------------------------------------------------
    # 基础会话参数
    # ------------------------------------------------------------------

    session_id: UUID
    """当前对话的会话 ID，用于读写短期记忆（HybridMemoryAdapter）。"""

    user_id: str
    """当前登录用户的 ID，用于工具调用时的记忆隔离。"""

    message: str
    """用户本轮输入的原始消息文本。"""

    profile: LLMProfileConfig = field(compare=False, hash=False)
    """已解析的 LLM Profile 配置（含 protocol / model / api_key / 能力标志等）。
    不参与比较与哈希，因为它是可变的复合对象。"""

    temperature: float
    """采样温度，由"请求参数 → DB 用户设置 → profile 默认值"三级优先级解析得出。"""

    system_prompt: str | None
    """最终注入 LLM 的系统提示，由身份层 + 技能清单 + 记忆 + RAG 上下文四层拼接而成；
    无任何内容时为 ``None``。"""

    context_max_messages: int
    """传给 LLM 的历史消息条数上限，从用户设置或 profile 默认值中读取。"""

    mode: Literal["normal", "tool_loop"]
    """执行路由决策：
    - ``"normal"``：直接调用 ``llm.generate_stream()``，不启用工具循环。
    - ``"tool_loop"``：通过 ``ToolLoopUseCase.execute_stream_with_tools()`` 驱动多轮工具调用。
    """

    # ------------------------------------------------------------------
    # LLM 调用扩展参数
    # ------------------------------------------------------------------

    llm_kwargs: dict[str, Any] = field(default_factory=dict, hash=False)
    """透传给 LLM 适配器的额外关键字参数，例如 ``enable_thinking=True``、
    ``thinking_budget=8000``。不参与哈希，因其内容随请求变化。"""

    # ------------------------------------------------------------------
    # 工具相关
    # ------------------------------------------------------------------

    tool_adapter: ToolAdapter | None = field(default=None, compare=False, hash=False)
    """本轮使用的工具适配器实例（可能是 CompositeToolAdapter）。
    ``mode="normal"`` 时为 ``None``；不参与比较与哈希。"""

    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    """白名单工具名集合。非空时 ToolLoopUseCase 只向 LLM 暴露集合内的工具；
    空集合表示暴露全部可用工具。"""

    turn_context: str = field(default="")
    """Tier-2 动态会话上下文，由 ``MemoryEngine.build_turn_context()`` 生成；
    注入为 role=assistant 的合成消息对（桥接 user:[记忆同步] + assistant:[快照]），
    不持久化到会话历史。空字符串表示无相关 session/project 记忆。"""
