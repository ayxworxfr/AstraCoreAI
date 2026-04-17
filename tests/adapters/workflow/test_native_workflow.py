"""Tests for NativeWorkflowOrchestrator — create, execute, pause, resume, checkpoint."""
import pytest
from uuid import uuid4

from astracore.adapters.workflow.native import NativeWorkflowOrchestrator
from astracore.core.domain.agent import AgentRole, AgentTask, AgentTaskStatus
from astracore.core.ports.workflow import WorkflowStatus


def _task(description: str = "do something") -> AgentTask:
    return AgentTask(role=AgentRole.EXECUTOR, description=description)


# ---------- create_workflow ----------

async def test_create_workflow_returns_workflow_state():
    oc = NativeWorkflowOrchestrator()
    tasks = [_task("step1"), _task("step2")]
    wf = await oc.create_workflow("test-wf", tasks)
    assert wf.name == "test-wf"
    assert len(wf.tasks) == 2
    assert wf.status == WorkflowStatus.PENDING


async def test_create_workflow_with_context():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()], context={"key": "val"})
    assert wf.context == {"key": "val"}


# ---------- execute_workflow ----------

async def test_execute_workflow_completes_all_tasks():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task("a"), _task("b")])
    result = await oc.execute_workflow(wf.workflow_id)
    assert result.status == WorkflowStatus.COMPLETED
    assert all(t.status == AgentTaskStatus.COMPLETED for t in result.tasks)


async def test_execute_workflow_raises_for_unknown_id():
    oc = NativeWorkflowOrchestrator()
    with pytest.raises(ValueError, match="not found"):
        await oc.execute_workflow(uuid4())


async def test_execute_workflow_skips_already_completed_tasks():
    oc = NativeWorkflowOrchestrator()
    t1 = _task("pre-completed")
    t1.mark_completed("done already")
    t2 = _task("fresh")
    wf = await oc.create_workflow("wf", [t1, t2])
    result = await oc.execute_workflow(wf.workflow_id)
    assert result.status == WorkflowStatus.COMPLETED


# ---------- get_workflow_state ----------

async def test_get_workflow_state_returns_current_state():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()])
    state = await oc.get_workflow_state(wf.workflow_id)
    assert state.workflow_id == wf.workflow_id


async def test_get_workflow_state_raises_for_unknown_id():
    oc = NativeWorkflowOrchestrator()
    with pytest.raises(ValueError, match="not found"):
        await oc.get_workflow_state(uuid4())


# ---------- pause_workflow ----------

async def test_pause_workflow_sets_paused_status():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()])
    await oc.pause_workflow(wf.workflow_id)
    state = await oc.get_workflow_state(wf.workflow_id)
    assert state.status == WorkflowStatus.PAUSED


# ---------- resume_workflow ----------

async def test_resume_workflow_completes_after_pause():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()])
    await oc.pause_workflow(wf.workflow_id)
    result = await oc.resume_workflow(wf.workflow_id)
    assert result.status == WorkflowStatus.COMPLETED


async def test_resume_workflow_raises_when_not_paused():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()])
    await oc.execute_workflow(wf.workflow_id)
    with pytest.raises(ValueError, match="not paused"):
        await oc.resume_workflow(wf.workflow_id)


# ---------- save_checkpoint — no-op without Redis ----------

async def test_save_checkpoint_is_noop_without_redis():
    oc = NativeWorkflowOrchestrator()  # no redis_url
    wf = await oc.create_workflow("wf", [_task()])
    # Must not raise even though Redis is not configured
    await oc.save_checkpoint(wf.workflow_id)


async def test_save_checkpoint_unknown_id_is_noop():
    oc = NativeWorkflowOrchestrator()
    await oc.save_checkpoint(uuid4())  # should not raise


# ---------- load_checkpoint — in-memory fallback ----------

async def test_load_checkpoint_falls_back_to_in_memory():
    oc = NativeWorkflowOrchestrator()
    wf = await oc.create_workflow("wf", [_task()])
    loaded = await oc.load_checkpoint(wf.workflow_id)
    assert loaded.workflow_id == wf.workflow_id


async def test_load_checkpoint_raises_when_not_found():
    oc = NativeWorkflowOrchestrator()
    with pytest.raises(ValueError, match="checkpoint not found"):
        await oc.load_checkpoint(uuid4())
