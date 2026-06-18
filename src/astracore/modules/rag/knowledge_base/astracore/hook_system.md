---
title: AstraCoreAI Hook 系统
category: astracore
tags: [Hook, HookRegistry, ShortCircuit, Tracer, 中间件, 生命周期, 策略引擎]
related: [astracore/tool_system, astracore/chat_pipeline, astracore/workflow_system]
---

# AstraCoreAI Hook 系统

AstraCoreAI 内置完整的 Hook 体系，覆盖 LLM 调用、工具执行、HTTP 中间件、应用生命周期和策略引擎五个层面，全部支持异步。

---

## 一、核心 Hook 注册表（HookRegistry）

系统提供四种 Hook 类型，通过 `HookRegistry` 统一管理：

| Hook 类型 | 注册字段 | 触发时机 | 可短路（ShortCircuit） |
|----------|---------|---------|----------------------|
| `BeforeLLMHook` | `registry.before_llm` | LLM 调用前 | ✅ |
| `AfterLLMHook` | `registry.after_llm` | LLM 调用后 | ❌ |
| `BeforeToolHook` | `registry.before_tool` | 工具执行前 | ✅ |
| `AfterToolHook` | `registry.after_tool` | 工具执行后 | ❌ |

### Hook Payload 数据类

```python
@dataclass
class LLMCallInput:
    messages: list[Message]
    model: str
    tools: list[dict] | None
    kwargs: dict

@dataclass
class LLMCallOutput:
    content: str
    tool_calls: list[ToolCall]
    metadata: dict
    duration_ms: float

@dataclass
class ToolCallInput:
    tool_call_id: str
    tool_name: str
    arguments: dict

@dataclass
class ToolCallOutput:
    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool
    duration_ms: float
    metadata: dict
```

### ShortCircuit — 跳过后续执行

Before 类 Hook 返回 `ShortCircuit` 对象时，立即中断执行链，直接使用其中的 `result` 作为调用结果，不执行 LLM/工具：

```python
@dataclass
class ShortCircuit:
    result: LLMCallOutput | ToolCallOutput
```

典型用途：缓存命中直接返回、测试 Mock、Guardrail 拦截。

### Hook 执行规则

- Hook 返回 **非 None、非 ShortCircuit** → 替换 payload，继续执行后续 Hook
- Hook 返回 **ShortCircuit** → 立即终止整个链条
- Hook 返回 **None** → payload 透传，继续下一个 Hook
- Hook 抛出异常 → 记录日志，跳过该 Hook，不中断流程

---

## 二、Hook 注册方式

```python
from astracore.shared.observability.hooks import HookRegistry, LLMCallInput, ShortCircuit

registry = HookRegistry()

# 直接追加（顺序即执行顺序）
registry.before_llm.append(my_before_llm_hook)
registry.after_tool.append(my_after_tool_hook)
```

Hook 函数支持同步和异步混用：

```python
# 同步 Hook
def sync_hook(payload: LLMCallInput) -> None:
    print(f"Calling model: {payload.model}")

# 异步 Hook
async def async_hook(payload: LLMCallInput) -> LLMCallInput | None:
    # 修改 payload 并返回，或返回 None 透传
    return None

registry.before_llm.append(sync_hook)
registry.before_llm.append(async_hook)
```

---

## 三、Hook 集成点（tool_loop.py）

工具循环在每轮迭代中的触发顺序：

```
ROUND_START 事件
  └─ _fire_before_llm()        ← 可 ShortCircuit 跳过 LLM
       └─ LLM.generate_stream()
            ├─ TEXT_DELTA / THINKING_DELTA 事件
            └─ TOOL_CALL 事件
  └─ _fire_after_llm()
  
  for each tool_call:
    └─ _fire_before_tool()     ← 可 ShortCircuit 跳过工具执行
         └─ policy.check_security_policy()
         └─ tool.execute()
         └─ TOOL_RESULT 事件
    └─ _fire_after_tool()
```

非流式路径（`execute_with_tools`）触发相同的四个 Hook，时序一致。

---

## 四、SDK 注入（AstraCoreClient）

通过构造函数传入 `HookRegistry`，Hooks 会透传到 `ChatPipeline` → `ToolLoopUseCase`：

```python
from astracore.shared.observability.hooks import HookRegistry
from astracore.sdk.client import AstraCoreClient

registry = HookRegistry()
registry.before_llm.append(my_hook)

async with AstraCoreClient(hooks=registry) as client:
    async with client.conversation() as conv:
        result = await conv.send("你好")
```

---

## 五、内置 Tracer Hook

`Tracer` 是 Hook 系统的标准使用示例，通过 `register_hooks()` 将自身挂入 Registry：

```python
from astracore.shared.observability.tracing import Tracer

tracer = Tracer(session_id="session-123")
tracer.register_hooks(registry)
# 等价于：
# registry.before_llm.append(tracer._before_llm)
# registry.after_llm.append(tracer._after_llm)
# registry.before_tool.append(tracer._before_tool)
# registry.after_tool.append(tracer._after_tool)
```

Tracer 记录每次 LLM 调用和工具执行的 Span（含 duration_ms、token 数、tool_name 等属性）。

---

## 六、策略引擎（PolicyEngine）

策略引擎是工具循环中的另一类"Hook"，在固定时机自动生效：

| 策略 | 方法 | 触发时机 |
|-----|------|---------|
| 安全检查 | `check_security_policy(tool_name, arguments)` | before_tool Hook 内 |
| 重试 | `apply_retry_policy(func, *args)` | 包装任意可重试调用 |
| 上下文压缩 | `HistoryCompactor.maybe_compact(...)` | `ChatPipeline.stream()` 入口处 |
| 工具超时 | `policy.timeout.tool_timeout_s` | `ToolLoopUseCase` 单工具执行 wait_for |

安全策略拦截时，工具不执行，直接触发 `after_tool` Hook 并返回错误结果。

---

## 七、HTTP 中间件 Hook

`RequestLoggingMiddleware` 基于 Starlette `BaseHTTPMiddleware`，为每个请求注入 `request_id` 并记录访问日志：

```python
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(CORSMiddleware, ...)
```

可通过同样方式追加自定义中间件（认证、限流、数据脱敏等）。

---

## 八、应用生命周期 Hook

通过 FastAPI `lifespan` 实现启动/关闭回调：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 启动阶段 ──
    await init_db(...)
    await seed_builtin_skills(...)
    asyncio.create_task(seed_documents(...))
    asyncio.create_task(_start_mcp())
    
    yield  # 应用运行
    
    # ── 关闭阶段 ──
    await mcp_adapter.stop()
```

---

## 九、典型自定义 Hook 示例

### 示例 1：LLM 结果缓存

```python
class LLMCacheHook:
    def __init__(self):
        self._cache: dict[str, LLMCallOutput] = {}

    def before_llm(self, payload: LLMCallInput) -> LLMCallInput | ShortCircuit | None:
        key = str([(m.role, m.content) for m in payload.messages])
        if key in self._cache:
            return ShortCircuit(result=self._cache[key])
        return None

    def after_llm(self, payload: LLMCallOutput) -> None:
        # 存入缓存（key 由外部协调，此处仅演示）
        pass

hook = LLMCacheHook()
registry.before_llm.append(hook.before_llm)
registry.after_llm.append(hook.after_llm)
```

### 示例 2：工具执行 Guardrail

```python
async def production_guardrail(payload: ToolCallInput) -> ToolCallInput | ShortCircuit | None:
    if "production" in payload.arguments.get("path", "").lower():
        return ShortCircuit(
            result=ToolCallOutput(
                tool_call_id=payload.tool_call_id,
                tool_name=payload.tool_name,
                content="操作被拦截：禁止在生产环境执行此工具",
                is_error=True,
                duration_ms=0,
                metadata={"blocked": True},
            )
        )
    return None

registry.before_tool.append(production_guardrail)
```

### 示例 3：工具执行耗时监控

```python
import time

async def tool_latency_monitor(payload: ToolCallOutput) -> None:
    if payload.duration_ms > 3000:
        print(f"[SLOW TOOL] {payload.tool_name} took {payload.duration_ms:.0f}ms")
    # 返回 None，不修改输出

registry.after_tool.append(tool_latency_monitor)
```

---

## 十、Hook 能力总览

| 层次 | Hook 点 | 可扩展 | 短路支持 |
|-----|---------|--------|---------|
| LLM 调用前 | `HookRegistry.before_llm` | ✅ | ✅ |
| LLM 调用后 | `HookRegistry.after_llm` | ✅ | ❌ |
| 工具执行前 | `HookRegistry.before_tool` | ✅ | ✅ |
| 工具执行后 | `HookRegistry.after_tool` | ✅ | ❌ |
| HTTP 请求 | `BaseHTTPMiddleware` | ✅ | ✅ |
| 应用启动/关闭 | `lifespan` | ✅ | ❌ |
| 安全策略 | `PolicyEngine.check_security_policy` | 配置驱动 | ✅（阻断工具） |
| 追踪观测 | `Tracer.register_hooks()` | ✅ | ❌ |
