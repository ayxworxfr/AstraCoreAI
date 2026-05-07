"""Automatic skill routing — matches user messages to relevant skills.

Supported modes
---------------
off    : disabled; callers receive an empty list.
vector : cosine similarity between the message embedding and per-skill
         description embeddings.  Requires sentence-transformers + numpy
         (already pulled in by the vector stack).
llm    : a lightweight LLM call returns a JSON list of matching skill IDs.
         Uses the configured llm_profile (defaults to llm.default_profile).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from astracore.adapters.db.models import SkillRow
from astracore.adapters.db.session import get_session
from astracore.runtime.observability.logger import get_logger

if TYPE_CHECKING:
    from astracore.sdk.config import AstraCoreConfig

logger = get_logger(__name__)


class SkillRouter:
    """Routes user messages to relevant skills.

    Call ``precompute()`` once at startup when mode is ``vector``.
    Thread-safe for concurrent reads after ``precompute()`` completes.
    """

    def __init__(self, config: "AstraCoreConfig", db_url: str) -> None:
        self._routing = config.skill_routing
        self._llm_cfg = config.llm
        self._retrieval = config.retrieval
        self._db_url = db_url
        # skill_id -> (SkillRow, np.ndarray) — populated by precompute()
        self._vectors: dict[str, tuple[SkillRow, Any]] = {}
        self._st_model: Any = None
        self._llm_adapter: Any = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def precompute(self) -> None:
        """Embed all skill name+description strings for vector mode.

        No-op when mode != "vector".  Logs a warning and degrades gracefully
        when sentence-transformers is not installed.
        """
        if self._routing.mode != "vector":
            return
        try:
            import asyncio  # noqa: PLC0415

            import numpy as np  # noqa: F401, PLC0415
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415

            skills = await self._load_all_skills()
            if not skills:
                return

            model = await asyncio.to_thread(SentenceTransformer, self._retrieval.embedding_model)
            self._st_model = model

            texts = [f"{s.name}：{s.description}" for s in skills]
            vectors = await asyncio.to_thread(model.encode, texts, normalize_embeddings=True)
            self._vectors = {s.id: (s, vectors[i]) for i, s in enumerate(skills)}
            logger.info("SkillRouter: precomputed embeddings for %d skills", len(skills))
        except ImportError:
            logger.warning(
                "SkillRouter: sentence-transformers / numpy not available; "
                "vector routing disabled — install them or switch to mode: llm"
            )
        except Exception:
            logger.exception("SkillRouter: precompute failed")

    async def route(self, message: str) -> list[SkillRow]:
        """Return matching skills for *message*, ordered by relevance.

        Returns an empty list when mode is ``off`` or when routing fails.
        """
        if self._routing.mode == "off":
            return []
        if self._routing.mode == "vector":
            return await self._route_vector(message)
        return await self._route_llm(message)

    # ------------------------------------------------------------------
    # Vector routing
    # ------------------------------------------------------------------

    async def _route_vector(self, message: str) -> list[SkillRow]:
        if not self._vectors or self._st_model is None:
            return []
        try:
            import asyncio  # noqa: PLC0415

            import numpy as np  # noqa: PLC0415

            msg_vec = await asyncio.to_thread(
                self._st_model.encode, [message], normalize_embeddings=True
            )
            msg_vec = msg_vec[0]

            scores: list[tuple[float, SkillRow]] = [
                (float(np.dot(msg_vec, vec)), row)
                for row, vec in self._vectors.values()
            ]
            scores.sort(key=lambda x: x[0], reverse=True)
            logger.info(
                "SkillRouter vector scores: %s",
                [(f"{score:.3f}", row.name) for score, row in scores],
            )

            cfg = self._routing
            result: list[SkillRow] = []
            for i, (score, row) in enumerate(scores):
                if i == 0:
                    if score >= cfg.threshold:
                        result.append(row)
                    else:
                        break  # sorted desc — primary miss means no matches
                else:
                    if (
                        result
                        and score >= cfg.secondary_threshold
                        and len(result) < cfg.max_skills
                    ):
                        result.append(row)
                    else:
                        break
            return result
        except Exception:
            logger.exception("SkillRouter: vector routing failed")
            return []

    # ------------------------------------------------------------------
    # LLM routing
    # ------------------------------------------------------------------

    def _get_llm_adapter(self) -> Any:
        if self._llm_adapter is None:
            from astracore.adapters.llm.anthropic import AnthropicAdapter  # noqa: PLC0415
            from astracore.adapters.llm.openai import OpenAIAdapter  # noqa: PLC0415

            profile = self._llm_cfg.get_profile(self._routing.llm_profile)
            if profile.provider == "anthropic":
                self._llm_adapter = AnthropicAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    max_tokens=256,
                    supports_temperature=False,
                    use_anthropic_blocks=False,
                )
            else:
                self._llm_adapter = OpenAIAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    max_tokens=256,
                )
        return self._llm_adapter

    async def _route_llm(self, message: str) -> list[SkillRow]:
        skills = await self._load_all_skills()
        if not skills:
            return []
        try:
            from astracore.core.domain.message import Message, MessageRole  # noqa: PLC0415

            skill_lines = "\n".join(
                f'- id: "{s.id}", name: "{s.name}", description: "{s.description}"'
                for s in skills
            )
            max_skills = self._routing.max_skills
            system = (
                "You are a skill router. Match the user message to relevant skills.\n\n"
                "Rules:\n"
                "1. Only include a skill if its name/description DIRECTLY maps to something "
                "the user EXPLICITLY asked — no implied or tangential needs.\n"
                "2. For messages with multiple distinct topics, include one matching skill per "
                f"topic (up to {max_skills} total).\n"
                "3. Do NOT include general-purpose or catch-all skills when specific ones match.\n"
                "4. Return an empty list if no skills clearly match.\n\n"
                'Respond ONLY with valid JSON: {"skill_ids": ["id1", "id2"]}'
            )
            user_text = (
                f"Available skills:\n{skill_lines}\n\n"
                f'User message: "{message}"'
            )
            adapter = self._get_llm_adapter()
            response = await adapter.generate(
                messages=[
                    Message(role=MessageRole.SYSTEM, content=system),
                    Message(role=MessageRole.USER, content=user_text),
                ],
            )
            raw = response.content.strip()
            # Strip markdown code fence if model wraps output
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:]).rstrip("`").strip()
            parsed = json.loads(raw)
            ids: list[str] = parsed.get("skill_ids", [])
            skill_map = {s.id: s for s in skills}
            return [skill_map[sid] for sid in ids if sid in skill_map][: self._routing.max_skills]
        except Exception:
            logger.exception("SkillRouter: LLM routing failed")
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_all_skills(self) -> list[SkillRow]:
        async with get_session(self._db_url) as db:
            result = await db.execute(
                select(SkillRow).order_by(SkillRow.sort_order, SkillRow.created_at)
            )
            return list(result.scalars().all())
