---
title: AstraCoreAI DAG 工作流引擎
category: astracore
tags: [Workflow, DAG, AgentTask, 多Agent, 并行任务, 条件跳过, 审批断点, SDK]
related: [astracore/intro, astracore/tool_system, astracore/chat_pipeline, ai-basics/agent_intro]
---

# AstraCoreAI DAG 工作流引擎

AstraCoreAI 内置基于有向无环图（DAG）的工作流引擎，允许将复杂目标分解为多个相互依赖的任务，并在满足依赖条件时并行执行。

## 核心数据结构

### AgentTask

`AgentTask`（`modules/agent/domain.py`）是工作流的基本单元：

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | UUID | 任务唯一标识，自动生成 |
| `role` | AgentRole | `PLANNER` / `EXECUTOR` / `REVIEWER` |
| `description` | str | 任务描述，也是发给 LLM 的提示语 |
| `depends_on` | list[UUID] | 前序任务 ID 列表，控制执行顺序 |
| `condition` | str \| None | Python 表达式，falsy → 任务跳过（SKIPPED） |
| `context` | dict | 任务私有上下文数据 |
| `metadata` | dict | SDK 层可读的扩展参数（如 `use_tools`、`model_profile`） |
| `status` | AgentTaskStatus | `PENDING / IN_PROGRESS / COMPLETED / SKIPPED / FAILED` |
| `result` | str \| None | 任务执行结果文本 |

### WorkflowState

`WorkflowState`（`modules/agent/ports/workflow.py`）保存整个工作流的运行状态：

| 字段 | 类型 | 说明 |
|------|------|------|
| `workflow_id` | UUID | 工作流唯一标识 |
| `name` | str | 工作流名称 |
| `status` | WorkflowStatus | `PENDING / RUNNING / PAUSED / COMPLETED / FAILED` |
| `tasks` | list[AgentTask] | 所有任务（含状态） |
| `task_results` | dict[str, str] | task_id → 结果文本的累积字典 |
| `context` | dict | 工作流级共享上下文 |
| `result` | Any | 最终结果（完成后为 `{completed_tasks, skipped_tasks}`） |
| `error` | str \| None | 失败原因 |

## 执行模型

`NativeWorkflowOrchestrator`（`infrastructure/workflow/native.py`）使用 Kahn 算法将任务按依赖关系分为若干"层"，同层任务并行执行：

```
create_workflow(name, tasks)
    ↓
_topo_layers(tasks)       ← Kahn 算法，检测环路，按 depends_on 分组
    ↓
for layer in layers:
    asyncio.gather(*[_run_task(t) for t in layer])   ← 同层并行
        ↓
        condition != None → eval(condition, {task_results, context}) → falsy → SKIPPED
        executor(task, task_results) → result
        task_results[str(task_id)] = result
    ↓
WorkflowStatus.COMPLETED / FAILED
```

**失败语义**：任意任务的 `executor` 抛出异常 → 该任务标记 `FAILED`，工作流立即标记 `FAILED`，后续所有层被跳过。

**已完成任务重入**：`resume_workflow` 复用同一 layer 结构，已 `COMPLETED` 的任务直接跳过，从上次中断点继续执行。

## 条件跳过（condition）

`AgentTask.condition` 是一段 Python 表达式，在受限命名空间（只有 `task_results` 和 `context`，无任何 builtins）中求值：

```python
from astracore.modules.agent.domain import AgentTask, AgentRole

qa_task = AgentTask(
    role=AgentRole.REVIEWER,
    description="对分析结果进行质量检查",
    depends_on=[analysis_task.task_id],
    # 只有当分析任务结果包含"风险"时才执行审查
    condition=f'"风险" in task_results.get("{analysis_task.task_id}", "")',
)
```

表达式求值异常时默认视为 `True`（任务继续执行），并记录 warning 日志。

## SDK 用法：client.workflow.run()

`WorkflowClient`（`sdk/client.py`）是最简便的使用方式，每个任务通过 `ChatPipeline.execute` 驱动：

```python
from astracore.sdk.client import AstraCoreClient
from astracore.modules.agent.domain import AgentTask, AgentRole

async with AstraCoreClient() as client:
    t1 = AgentTask(
        role=AgentRole.EXECUTOR,
        description="调研竞争对手 A 的产品特性，输出结构化列表",
    )
    t2 = AgentTask(
        role=AgentRole.EXECUTOR,
        description="调研竞争对手 B 的产品特性，输出结构化列表",
    )
    t3 = AgentTask(
        role=AgentRole.REVIEWER,
        description="综合以上调研结果，输出对比分析报告",
        depends_on=[t1.task_id, t2.task_id],   # t1、t2 都完成后才执行
    )

    state = await client.workflow.run(
        "竞品调研",
        [t1, t2, t3],
        use_tools=True,          # 所有任务默认启用工具
        model_profile="claude-opus",
    )
    print(state.result)          # {'completed_tasks': 3, 'skipped_tasks': 0}
    print(t3.result)             # 最终报告文本
```

### 任务间数据传递

前序任务的结果通过 `task_results`（`dict[task_id_str → result_str]`）传递。`WorkflowClient.run` 会在下一层任务的 user message 末尾自动拼接：

```
[已完成任务的结果]
[Task <t1_id>]
<t1 的执行结果>

[Task <t2_id>]
<t2 的执行结果>
```

LLM 可以直接读取并在 `t3.description` 的指令下综合输出。

### 会话隔离

`WorkflowClient.run` 默认为整个工作流分配一个共享 `session_id`，前序任务的对话历史会通过 `HybridMemoryAdapter` 积累，后续任务能读取到完整上下文。传入 `session_id` 参数可恢复已有的工作流会话。

### 单任务覆盖（metadata）

可在 `task.metadata` 中为单个任务指定不同参数，优先级高于全局默认值：

```python
heavy_task = AgentTask(
    role=AgentRole.EXECUTOR,
    description="执行大规模数据分析",
    metadata={
        "use_tools": True,
        "model_profile": "claude-opus",   # 仅此任务用更强模型
        "temperature": 0.0,
    },
)
```

## 多 Agent 协作：AgentOrchestrationUseCase

`AgentOrchestrationUseCase`（`modules/agent/application/orchestration.py`）提供更高层的多 Agent 协作场景，预置 Planner → Executor → Reviewer 三角色模板：

```python
from astracore.infrastructure.workflow.native import NativeWorkflowOrchestrator
from astracore.modules.agent.application.orchestration import AgentOrchestrationUseCase

orchestrator = NativeWorkflowOrchestrator()
uc = AgentOrchestrationUseCase(orchestrator)

# 创建三任务工作流（不立即执行）
workflow = await uc.create_multi_agent_workflow(
    objective="分析 Q3 财报并输出摘要",
    context={"quarter": "Q3", "year": "2025"},
)

# 执行
state = await uc.execute_workflow(workflow.workflow_id)
```

注意：`create_multi_agent_workflow` 中 Executor 和 Reviewer 通过 `parent_task_id` 记录父子关系，但三个任务的 `depends_on` 均为空，即三者目前并行执行（无依赖约束）。如需顺序执行，需手动设置 `depends_on`。

## 暂停、审批与恢复

工作流支持人工审批断点，适合高风险操作的确认场景：

```python
# 暂停工作流等待审批
workflow = await uc.pause_for_approval(workflow_id, task_id=risky_task.task_id)
# workflow.status == WorkflowStatus.PAUSED

# 人工审核后批准并继续
workflow = await uc.approve_and_continue(workflow_id, task_id=risky_task.task_id)
# 已审批的任务被标记 COMPLETED，工作流从下一层继续执行
```

底层接口：
- `orchestrator.pause_workflow(workflow_id)` → 标记为 PAUSED，停止调度
- `orchestrator.resume_workflow(workflow_id)` → 从当前未完成层继续执行

## 直接使用 NativeWorkflowOrchestrator

不通过 SDK 时，可直接注入自定义 `executor`：

```python
from astracore.infrastructure.workflow.native import NativeWorkflowOrchestrator
from astracore.modules.agent.domain import AgentTask, AgentRole

orchestrator = NativeWorkflowOrchestrator()

async def my_executor(task: AgentTask, task_results: dict[str, str]) -> str:
    # task_results 包含所有已完成前序任务的结果
    return f"处理完成：{task.description}"

t1 = AgentTask(role=AgentRole.EXECUTOR, description="步骤一")
t2 = AgentTask(role=AgentRole.EXECUTOR, description="步骤二", depends_on=[t1.task_id])

wf = await orchestrator.create_workflow("my-flow", [t1, t2], executor=my_executor)
state = await orchestrator.execute_workflow(wf.workflow_id)
```

## 当前限制

| 限制 | 说明 |
|------|------|
| **内存态** | 工作流状态保存在 `NativeWorkflowOrchestrator._workflows` 字典中，进程重启后丢失 |
| **Checkpoint 为 no-op** | `save_checkpoint` / `load_checkpoint` 当前未接 Redis，调用不报错但不持久化 |
| **单进程** | 跨进程/多节点场景无法共享工作流状态 |
| **无调度器** | 没有定时触发或外部事件驱动，需由调用方主动发起 |

## 与 LangGraph 的兼容策略

`WorkflowOrchestrator`（`modules/agent/ports/workflow.py`）是唯一的编排抽象接口，当前实现为 `NativeWorkflowOrchestrator`。后续可新增 `LangGraphOrchestrator` 实现同一接口，通过配置切换，Domain 和 Application 层无需任何修改。
