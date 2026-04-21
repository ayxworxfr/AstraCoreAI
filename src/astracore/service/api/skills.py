"""Skills CRUD API endpoints."""

from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from astracore.adapters.db.models import SkillRow
from astracore.adapters.db.session import get_session
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _db_url() -> str:
    return AstraCoreConfig().memory.db_url


class SkillResponse(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: str
    is_builtin: bool
    created_at: datetime
    updated_at: datetime


class SkillCreate(BaseModel):
    name: str
    description: str = ""
    system_prompt: str


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None


def _to_response(row: SkillRow) -> SkillResponse:
    return SkillResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        system_prompt=row.system_prompt,
        is_builtin=row.is_builtin,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/", response_model=list[SkillResponse])
async def list_skills() -> list[SkillResponse]:
    """Return all skills ordered: built-ins first, then user-created by creation time."""
    async with get_session(_db_url()) as db:
        result = await db.execute(
            select(SkillRow).order_by(SkillRow.is_builtin.desc(), SkillRow.created_at)
        )
        return [_to_response(row) for row in result.scalars().all()]


@router.post("/", response_model=SkillResponse, status_code=201)
async def create_skill(body: SkillCreate) -> SkillResponse:
    async with get_session(_db_url()) as db:
        now = datetime.now(UTC)
        row = SkillRow(
            id=str(uuid4()),
            name=body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            is_builtin=False,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _to_response(row)


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(skill_id: str, body: SkillUpdate) -> SkillResponse:
    async with get_session(_db_url()) as db:
        row = await db.get(SkillRow, skill_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        if row.is_builtin:
            raise HTTPException(status_code=403, detail="内置 Skill 不可修改")
        if body.name is not None:
            row.name = body.name
        if body.description is not None:
            row.description = body.description
        if body.system_prompt is not None:
            row.system_prompt = body.system_prompt
        row.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        return _to_response(row)


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(skill_id: str) -> None:
    async with get_session(_db_url()) as db:
        row = await db.get(SkillRow, skill_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        if row.is_builtin:
            raise HTTPException(status_code=403, detail="内置 Skill 不可删除")
        await db.delete(row)
        await db.commit()
