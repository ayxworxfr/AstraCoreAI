"""Per-turn chat execution options.

``ChatOptions`` is the single source of truth for all caller-supplied knobs that
control how a chat turn is executed.  It replaces the scattered 10-parameter lists
that previously appeared on ``ChatPipeline.prepare()``, ``AstraCoreClient.chat()``,
``chat_stream()``, and the HTTP ``ChatRequest``.

Callers construct a ``ChatOptions`` instance (or use the default) and pass it as a
single argument.  ``Conversation`` uses ``dataclasses.replace()`` to apply
per-turn overrides on top of per-conversation defaults without touching unset fields.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astracore.modules.attachments.domain import AttachmentRef


@dataclasses.dataclass
class ChatOptions:
    """Caller-supplied options for a single chat turn.

    All fields are optional and default to "disabled / unset".  The pipeline
    resolves final values by falling back to DB settings and profile defaults.

    Parameters
    ----------
    model_profile:
        LLM profile ID to use.  ``None`` falls back to the configured default.
    temperature:
        Sampling temperature override.  ``None`` defers to DB setting then profile.
    top_p:
        Nucleus sampling override.  ``None`` defers to DB setting then profile.
    top_k:
        Top-K sampling override (Anthropic only).  ``None`` means not sent to the API.
        Disabled automatically when thinking mode is active.
    use_tools:
        Enable the tool-loop for this turn.
    thinking_mode:
        Thinking mode override: ``'off'`` disables, ``'on'`` enables with budget_tokens
        (Anthropic standard), ``'adaptive'`` enables adaptive mode (Opus 4.7+).
        ``None`` falls back to the profile default, then capabilities-based inference.
    thinking_budget:
        Token budget when ``thinking_mode='on'``.  Ignored for adaptive mode.
    reasoning_effort:
        GPT-5/5.5 reasoning depth: ``'low'|'medium'|'high'|'xhigh'``.
        ``None`` defers to profile then provider default.
    verbosity:
        GPT-5 response length: ``'low'|'medium'|'high'``.
        ``None`` defers to profile then provider default.
    enable_rag:
        Expose the ``search_knowledge_base`` tool and inject RAG context.
    enable_web:
        Expose the ``web_search`` tool.
    """

    model_profile: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    use_tools: bool = False
    thinking_mode: str | None = None
    thinking_budget: int = 8000
    reasoning_effort: str | None = None
    verbosity: str | None = None
    enable_rag: bool = False
    enable_web: bool = False
    toolset: str | None = None
    """命名 Toolset（``default`` / ``readonly`` / ``memory_ops`` 等）。``None`` = default。"""
    max_input_tokens: int = 0
    """本轮输入 token 硬上限；0 = 不限制。"""
    max_output_tokens: int = 0
    """本轮输出 token 硬上限；0 = 不限制。"""
    soft_exec: bool = False
    """破坏性工具只预览不落盘（软执行）。"""
    attachments: list[AttachmentRef] = dataclasses.field(default_factory=list)
    """Attachment references to include in this turn. Pipeline loads bytes before LLM call."""

    def apply(self, **overrides: Any) -> ChatOptions:
        """Return a new ``ChatOptions`` with the given fields replaced.

        Convenience wrapper around ``dataclasses.replace`` so callers don't need
        to import the ``dataclasses`` module::

            effective = conv_defaults.apply(temperature=0.2, use_tools=True)
        """
        return dataclasses.replace(self, **overrides)
