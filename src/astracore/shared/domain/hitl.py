"""Human-in-the-loop (HITL) domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class HITLOption(BaseModel):
    """A single selectable option in a HITL question."""

    label: str
    description: str = ""


class PendingQuestion(BaseModel):
    """A question posed to the user mid-run, blocking further LLM execution until answered."""

    question_id: str = Field(default_factory=lambda: str(uuid4()))
    question: str
    header: str = ""
    options: list[HITLOption]
    multi_select: bool = False
    # When True, the frontend appends a free-text input field after the option list.
    allow_freeform: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HITLAnswer(BaseModel):
    """Answer submitted by the user for a pending question."""

    question_id: str
    selected: list[str]
    freeform: str | None = None
