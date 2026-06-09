"""Project API for memory isolation."""

from functools import lru_cache
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from astracore.infrastructure.db.models import UserRow
from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.modules.auth.dependencies import get_current_user
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.modules.memory.domain import ConversationProjectBinding, Project
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _get_db_url() -> str:
    return AstraCoreConfig().memory.db_url


def _get_user_engine(user_id: str) -> MemoryEngine:
    return MemoryEngine(SQLMemoryStore(_get_db_url()), user_id=user_id)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    root_paths: list[str] = Field(default_factory=list)
    description: str = ""


class ProjectResponse(BaseModel):
    id: str
    name: str
    root_paths: list[str]
    description: str
    created_at: str
    updated_at: str


class ConversationProjectBind(BaseModel):
    project_id: str
    locked: bool = False
    source: Literal["manual", "workspace", "path", "llm"] = "manual"


class ConversationProjectResponse(BaseModel):
    conversation_id: str
    project_id: str
    locked: bool
    source: str
    created_at: str
    updated_at: str


def _project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        root_paths=project.root_paths,
        description=project.description,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
    )


def _binding_response(binding: ConversationProjectBinding) -> ConversationProjectResponse:
    return ConversationProjectResponse(
        conversation_id=str(binding.conversation_id),
        project_id=binding.project_id,
        locked=binding.locked,
        source=binding.source,
        created_at=binding.created_at.isoformat(),
        updated_at=binding.updated_at.isoformat(),
    )


@router.get("/", response_model=list[ProjectResponse])
async def list_projects(
    current_user: UserRow = Depends(get_current_user),
) -> list[ProjectResponse]:
    return [
        _project_response(project)
        for project in await _get_user_engine(current_user.id).list_projects()
    ]


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    current_user: UserRow = Depends(get_current_user),
) -> ProjectResponse:
    project = await _get_user_engine(current_user.id).create_project(
        name=body.name,
        root_paths=body.root_paths,
        description=body.description,
    )
    return _project_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    current_user: UserRow = Depends(get_current_user),
) -> ProjectResponse:
    project = await _get_user_engine(current_user.id).get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_response(project)


@router.get(
    "/conversations/{conversation_id}/project", response_model=ConversationProjectResponse | None
)
async def get_conversation_project(
    conversation_id: UUID,
    current_user: UserRow = Depends(get_current_user),
) -> ConversationProjectResponse | None:
    binding = await _get_user_engine(current_user.id).get_conversation_binding(conversation_id)
    return _binding_response(binding) if binding else None


@router.put("/conversations/{conversation_id}/project", response_model=ConversationProjectResponse)
async def bind_conversation_project(
    conversation_id: UUID,
    body: ConversationProjectBind,
    current_user: UserRow = Depends(get_current_user),
) -> ConversationProjectResponse:
    try:
        binding = await _get_user_engine(current_user.id).bind_conversation(
            conversation_id=conversation_id,
            project_id=body.project_id,
            locked=body.locked,
            source=body.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _binding_response(binding)
