"""Skills CRUD API endpoints."""

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from astracore.infrastructure.db.models import SkillReferenceRow, SkillRow
from astracore.infrastructure.db.session import get_session
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _db_url() -> str:
    return AstraCoreConfig().storage.db_url


class SkillResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: str
    instructions: str
    category: str | None
    is_builtin: bool
    order: int
    has_references: bool
    has_scripts: bool
    created_at: datetime
    updated_at: datetime


class SkillCreate(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    instructions: str
    category: str | None = None


class SkillUpdate(BaseModel):
    name: str | None = None
    display_name: str | None = None
    description: str | None = None
    instructions: str | None = None
    category: str | None = None


def _has_scripts(row: SkillRow) -> bool:
    if not row.skill_dir:
        return False
    return (Path(row.skill_dir) / "scripts").exists()


def _to_response(row: SkillRow, *, has_references: bool = False) -> SkillResponse:
    return SkillResponse(
        id=row.id,
        name=row.name,
        display_name=row.display_name or "",
        description=row.description,
        instructions=row.instructions or "",
        category=row.category,
        is_builtin=row.is_builtin,
        order=row.sort_order,
        has_references=has_references,
        has_scripts=_has_scripts(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/", response_model=list[SkillResponse])
async def list_skills() -> list[SkillResponse]:
    """Return all skills ordered: built-ins first, then user-created by creation time."""
    async with get_session(_db_url()) as db:
        rows_result = await db.execute(
            select(SkillRow).order_by(
                SkillRow.is_builtin.desc(),
                SkillRow.sort_order,
                SkillRow.created_at,
            )
        )
        rows = rows_result.scalars().all()
        refs_result = await db.execute(select(SkillReferenceRow.skill_id).distinct())
        skills_with_refs: set[str] = set(refs_result.scalars().all())
        return [_to_response(row, has_references=row.id in skills_with_refs) for row in rows]


@router.post("/", response_model=SkillResponse, status_code=201)
async def create_skill(body: SkillCreate) -> SkillResponse:
    async with get_session(_db_url()) as db:
        now = datetime.now(UTC)
        row = SkillRow(
            id=str(uuid4()),
            name=body.name,
            display_name=body.display_name,
            description=body.description,
            instructions=body.instructions,
            category=body.category,
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
        if body.display_name is not None:
            row.display_name = body.display_name
        if body.description is not None:
            row.description = body.description
        if body.instructions is not None:
            row.instructions = body.instructions
        if body.category is not None:
            row.category = body.category
        row.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        refs_result = await db.execute(
            select(SkillReferenceRow.skill_id).where(SkillReferenceRow.skill_id == row.id).limit(1)
        )
        has_references = refs_result.first() is not None
        return _to_response(row, has_references=has_references)


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
