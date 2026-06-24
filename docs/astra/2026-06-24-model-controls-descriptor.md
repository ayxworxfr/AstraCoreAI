# model_controls 能力描述符设计方案

> 设计类型：**设计卡**（跨模块 + 公开接口变更）
> 参数事实依据：`2026-06-24-llm-api-params-reference.md`
> 最后更新：2026-06-24

---

## 意图与边界

**Job-to-be-Done**：
当用户切换 LLM profile 时，前端界面应自动调整可用的 per-turn 控件（思考模式、推理档位、temperature、top_p），而不需要前端代码硬编码哪些模型支持哪些参数。

**Goals**：
- `/system` 接口在每个 profile 中新增 `controls` 描述符列表，前端按 `kind` 字段动态渲染控件
- `top_p` 从 profile 级别提升为 per-turn 可覆盖（与 temperature 同等地位）
- 前端 chatStore 增加 temperature + topP 状态，发送消息时透传
- SDK `conversation()`、`chat_stream()`、`chat()` 增加 `top_p` 参数，保持 API + SDK parity

**Non-Goals**：
- `thinking_budget` 不做 per-turn UI（保留 profile 级别配置）
- `stop_sequences`、`frequency_penalty`、`presence_penalty`、`top_k`、`clear_thinking` 不在本次范围
- `LLMCapabilities` 内部布尔 flag 不变，仍供适配器内部使用
- DeepSeek `reasoning_effort`（high/max 两档）需独立设计，本次不覆盖
- GLM-5.2+ `reasoning_effort`（7 档）不在本次范围

**成功标准**：
- 切换到 Anthropic Claude Sonnet 时，前端渲染：ThinkingModeSelector（modes: off/on/adaptive）+ temperature slider（0–1）+ top-p slider（0–1）
- 切换到 Anthropic Opus 4.7+ 时，ThinkingModeSelector 的 modes 只有 off/adaptive（无 on）
- 切换到 GPT-5 时，只出现 ReasoningEffortSelector（无 temperature/top-p 控件）
- 切换到 GPT-4o 时，出现 temperature（0–2）+ top-p（0–1），无 thinking/reasoning 控件
- 发送消息时带 `top_p=0.8`，pipeline 优先使用此值而非 profile 配置的 `top_p`

---

## 决策驱动变量

| 变量 | 类别 | 取值 | 来源 |
|---|---|---|---|
| ChatOptions 当前是否有 top_p | driver | 无，缺口 | `chat_options.py` |
| Pipeline 读取 top_p 来源 | driver | settings > profile，未读 opts | `pipeline.py:413-417` |
| SDK 方法当前签名 | driver | 无 top_p 参数 | `client.py:392-396, 445-449, 489-493` |
| 系统 API 当前返回格式 | driver | 平铺布尔值，无 controls | `system/api.py:19-84` |
| ThinkingModeSelector 现有组件 | driver | 已有，需适配 controls | `ThinkingBlock.tsx` |
| Opus 4.7+ adaptive_thinking_only | driver | True，不能接受 "on" mode | `model_capabilities.py` |
| GPT-5 不支持 temperature/top_p | driver | reasoning_effort_capable=True, temperature=False | 参数参考文档 + `model_capabilities.py` |

---

## 项目事实

### ChatOptions 调用方（公开接口，加字段影响方）

| 文件:行 | 用途 |
|---|---|
| `src/astracore/modules/chat/api.py:271-278` | `to_options()` 映射 ChatRequest → ChatOptions |
| `src/astracore/modules/chat/api.py:551,818` | `request.to_options().apply(...)` |
| `src/astracore/sdk/client.py:392-427` | `conversation()` 逐字段构建 ChatOptions |
| `src/astracore/sdk/client.py:443-471` | `chat_stream()` 同上 |
| `src/astracore/sdk/client.py:489-511` | `chat()` 同上 |
| `src/astracore/eval/dataset.py:39` | `EvalCase.options: ChatOptions`（dataclass，向前兼容） |
| `src/astracore/eval/__main__.py:22` | `ChatOptions()` 无 top_p（有默认值，不受影响） |
| `src/astracore/modules/chat/application/run_executor.py:409` | `ChatOptions()` 同上 |
| `tests/sdk/test_register_tool.py` | `test_chat_options_fields()` subset 检查，需同步更新 expected set |

### LLMProfileInfo 调用方（controls 新字段）

| 文件:行 | 用途 |
|---|---|
| `src/astracore/modules/system/api.py:19-84` | LLMCapabilitiesInfo + LLMProfileInfo + 序列化 |
| `frontend/src/features/system/types.ts:1-27` | SystemInfo TypeScript 类型，需同步 |

### Pipeline top_p 当前逻辑

```python
# pipeline.py:413-417
saved_top_p = await self._get_setting("top_p", user_id)
effective_top_p: float | None = float(saved_top_p) if saved_top_p else profile.top_p
if effective_top_p is not None:
    llm_kwargs["top_p"] = effective_top_p
```
→ 改为：opts.top_p（per-turn）> settings > profile

---

## 档位

**选定：设计卡**

选档理由：涉及公开接口变更（ChatOptions dataclass、SDK 三个方法签名、HTTP `LLMProfileInfo` 响应），需调用方 grep、S/B 拆分、决策记录。

---

## diff 预算

| 文件 | 变更类型 | 估计行数 |
|---|---|---|
| `modules/chat/domain/chat_options.py` | 加 top_p 字段 | +3 |
| `modules/chat/api.py` | ChatRequest + to_options | +6 |
| `modules/chat/pipeline.py` | top_p 读取优先级 | +6 |
| `sdk/client.py` | 3 个方法各加 top_p | +15 |
| `modules/system/api.py` | ModelControl discriminated union + 构建函数 | +90 |
| `frontend/src/features/system/types.ts` | controls TS 类型 | +40 |
| `frontend/src/features/chat/store/chatStore.ts` | temperature + topP 状态 | +25 |
| `frontend/src/features/chat/services/chatService.ts` | ChatRequest TS 类型加 top_p/temperature | +6 |
| `frontend/src/features/chat/components/ChatInputArea.tsx` | controls 驱动渲染 | +50 |
| `frontend/src/features/chat/components/ThinkingBlock.tsx` | ThinkingModeSelector 接收 modes props | +10 |
| `tests/sdk/test_register_tool.py` | expected 字段集加 top_p | +3 |
| `tests/` 新增测试文件 | controls 生成逻辑 + top_p 透传测试 | +80 |

**合计**：~12 文件，~330 行

---

## 代码级约束

**可维护性**：`controls` 构建逻辑提取为纯函数 `_build_controls(profile, caps)`，不内联在 `get_system_info()` 中，便于单独测试。

**可靠性**：
- `ThinkingControl.modes` 列表由后端权威生成，前端不自行过滤/增减（防止 Opus 4.7+ 的 "on" 被前端误加）
- `TemperatureControl.max` 由 `profile.protocol` 决定，不由前端模型名推断
- pipeline 读取 `opts.top_p` 时必须显式判断 `is not None`（0.0 是有效值，不能用 falsy 判断）

**兼容性**：
- `ChatOptions` 是 dataclass，`top_p: float | None = None` 默认值向前兼容所有调用方
- `LLMProfileInfo.controls` 默认 `[]`，旧前端不读取时不报错

---

## 候选方案对比

### 候选 A：扁平化字段

```python
class LLMControlsInfo(BaseModel):
    thinking_modes: list[str] | None       # ["off", "on", "adaptive"] 或 None
    reasoning_effort_levels: list[str] | None
    temperature_range: tuple[float, float] | None
    top_p_range: tuple[float, float] | None
```

- ✅ 实现简单
- ❌ 新控件类型必须修改所有字段定义
- ❌ 前端仍需 `if thinking_modes` 逐字段判断
- ❌ 无法描述同一 profile 同时具有 thinking + reasoning 的混合情况

### 候选 B：discriminated union（推荐）

```python
class ThinkingControl(BaseModel):
    kind: Literal["anthropic_thinking"] = "anthropic_thinking"
    modes: list[str]       # ["off", "on", "adaptive"] 或 ["off", "adaptive"]
    default: str           # "off"

class ReasoningEffortControl(BaseModel):
    kind: Literal["openai_reasoning_effort"] = "openai_reasoning_effort"
    levels: list[str]      # ["minimal", "low", "medium", "high"]
    default: str           # "medium"

class TemperatureControl(BaseModel):
    kind: Literal["temperature"] = "temperature"
    min: float             # 0.0
    max: float             # 1.0（Anthropic 协议）或 2.0（OpenAI/DeepSeek/GLM 协议）
    step: float            # 0.01
    default: float | None  # None 表示无建议值

class TopPControl(BaseModel):
    kind: Literal["top_p"] = "top_p"
    min: float             # 0.0
    max: float             # 1.0
    step: float            # 0.01
    default: float | None

ModelControl = Annotated[
    ThinkingControl | ReasoningEffortControl | TemperatureControl | TopPControl,
    Field(discriminator="kind")
]
```

- ✅ 新控件只需加 union 成员，前端代码不动
- ✅ 前端 switch on `kind` 自动路由，每个 kind 有独立 TypeScript 类型
- ✅ FastAPI 序列化开箱即用
- ⚠️ Pydantic v2 discriminated union 语法略复杂（一次性学习成本）

### Decision Drivers 评分

| Driver | A（扁平化）| B（discriminated union）|
|---|---|---|
| 新控件类型扩展性 | ❌ 每次改 schema | ✅ 加 union 成员 |
| 前端渲染清晰度 | ⚠️ 需逐字段 if | ✅ switch on kind |
| 后端实现复杂度 | ✅ 简单 | ⚠️ 略复杂 |
| TypeScript 类型安全 | ⚠️ nullable 多 | ✅ 各 kind 独立类型 |
| 兼容现有代码 | ✅ | ✅ |

**推荐：候选 B（discriminated union）**

---

## 完整实现规格

### 后端：ModelControl 类型（`modules/system/api.py` 新增）

```python
from typing import Annotated, Literal
from pydantic import Field

class ThinkingControl(BaseModel):
    kind: Literal["anthropic_thinking"] = "anthropic_thinking"
    modes: list[str]   # 前端直接渲染，顺序固定：off 在首位
    default: str

class ReasoningEffortControl(BaseModel):
    kind: Literal["openai_reasoning_effort"] = "openai_reasoning_effort"
    levels: list[str]
    default: str

class TemperatureControl(BaseModel):
    kind: Literal["temperature"] = "temperature"
    min: float
    max: float
    step: float
    default: float | None

class TopPControl(BaseModel):
    kind: Literal["top_p"] = "top_p"
    min: float
    max: float
    step: float
    default: float | None

ModelControl = Annotated[
    ThinkingControl | ReasoningEffortControl | TemperatureControl | TopPControl,
    Field(discriminator="kind"),
]
```

### 后端：controls 构建函数

```python
def _build_controls(profile: LLMProfileConfig) -> list[ModelControl]:
    caps = profile.capabilities
    controls: list[ModelControl] = []

    # Thinking：adaptive_thinking_only（Opus 4.7+）优先于通用 thinking
    if caps.adaptive_thinking_only:
        controls.append(ThinkingControl(modes=["off", "adaptive"], default="off"))
    elif caps.thinking:
        controls.append(ThinkingControl(modes=["off", "on", "adaptive"], default="off"))

    # Reasoning effort：仅 OpenAI Responses API（GPT-5 系列）
    if caps.reasoning_effort_capable:
        controls.append(ReasoningEffortControl(
            levels=["minimal", "low", "medium", "high"],
            default="medium",
        ))

    # Temperature + top_p：仅支持 temperature 的 profile
    if caps.temperature:
        max_temp = 1.0 if profile.protocol == "anthropic" else 2.0
        controls.append(TemperatureControl(min=0.0, max=max_temp, step=0.01, default=None))
        controls.append(TopPControl(min=0.0, max=1.0, step=0.01, default=None))

    return controls
```

### 后端：LLMProfileInfo 更新

```python
class LLMProfileInfo(BaseModel):
    # ... 现有字段不变 ...
    controls: list[ModelControl] = []
```

`get_system_info()` 中填充：`controls=_build_controls(profile)`

### 后端：ChatOptions 加 top_p

```python
# chat_options.py
top_p: float | None = None
```

### 后端：ChatRequest 加 top_p

```python
# modules/chat/api.py
top_p: float | None = Field(default=None, ge=0.0, le=1.0)
```

`to_options()` 中加 `top_p=self.top_p`

### 后端：pipeline.py top_p 优先级

```python
# 改为 opts > settings > profile
effective_top_p: float | None = (
    opts.top_p
    if opts.top_p is not None
    else (float(saved_top_p) if saved_top_p else profile.top_p)
)
```

### 后端：SDK 三个方法加 top_p

```python
# client.py: conversation(), chat_stream(), chat() 各加：
top_p: float | None = None,
```

并传入 `ChatOptions(... top_p=top_p, ...)`

### 前端：SystemInfo TypeScript 类型

```typescript
// features/system/types.ts
export type ThinkingControl = {
  kind: 'anthropic_thinking';
  modes: string[];
  default: string;
};

export type ReasoningEffortControl = {
  kind: 'openai_reasoning_effort';
  levels: string[];
  default: string;
};

export type TemperatureControl = {
  kind: 'temperature';
  min: number; max: number; step: number; default: number | null;
};

export type TopPControl = {
  kind: 'top_p';
  min: number; max: number; step: number; default: number | null;
};

export type ModelControl =
  | ThinkingControl
  | ReasoningEffortControl
  | TemperatureControl
  | TopPControl;

// 在 SystemInfo.llm.profiles[] 中加：
controls: ModelControl[];
```

### 前端：chatStore 新增状态

```typescript
// chatStore.ts 新增
temperature: number | null;
topP: number | null;
setTemperature: (v: number | null) => void;
setTopP: (v: number | null) => void;
```

切换 profile 时重置 temperature + topP 为 null。

### 前端：ChatInputArea 重构

以 `controls` 驱动渲染，替代现有 capability 硬编码：

```tsx
{controls.map((control) => {
  switch (control.kind) {
    case 'anthropic_thinking':
      return <ThinkingModeSelector key="thinking" modes={control.modes} ... />;
    case 'openai_reasoning_effort':
      return <ReasoningEffortSelector key="effort" levels={control.levels} ... />;
    case 'temperature':
      return <TemperatureSlider key="temp" min={control.min} max={control.max} ... />;
    case 'top_p':
      return <TopPSlider key="top_p" min={control.min} max={control.max} ... />;
  }
})}
```

---

## S/B 拆分

本次无结构层重构需要（无独立 S commit）。行为变更按依赖顺序拆分：

| commit | prefix | 内容 |
|---|---|---|
| B1 | `feat(domain):` | ChatOptions + ChatRequest + to_options + pipeline top_p 优先级 |
| B2 | `feat(sdk):` | SDK 三个方法加 top_p |
| B3 | `feat(system):` | ModelControl discriminated union + _build_controls + LLMProfileInfo.controls |
| B4 | `feat(frontend):` | SystemInfo TS 类型 + chatStore 状态 + ChatInputArea 重构 |
| B5 | `test:` | controls 生成逻辑单测 + top_p 透传测试 + test_chat_options_fields 更新 |

---

## 失败模式与验证

| 失败模式 | 级别 | 验证项（优先 RED 测试）|
|---|---|---|
| Opus 4.7+ profile 的 ThinkingControl.modes 包含 "on" → 前端允许用户选 "on" → 后端 400 错误 | High | 单测：`_build_controls(opus47_profile)` → modes == ["off", "adaptive"]，不含 "on" |
| Pipeline 读取 opts.top_p 时用 `if opts.top_p:` 导致 0.0 被跳过，发出 profile 的旧 top_p | High | 单测：opts.top_p=0.0 时 llm_kwargs["top_p"] == 0.0 |
| GPT-5 profile 的 controls 中出现 TemperatureControl → 前端暴露 temperature 滑块 → 实际 API 报 400 | High | 单测：`_build_controls(gpt5_profile)` → controls 不含 kind=="temperature" |
| 前端切换 profile 后，旧 profile 的 temperature 值未重置，新 profile 发出意外 temperature | Med | 集成：切换 profile 后 chatStore.temperature === null |
| ThinkingModeSelector 收到 modes=["off","adaptive"] 时仍允许点击 "on"（前端未按 modes 过滤选项）| Med | 组件测试：modes=["off","adaptive"] 时渲染的选项数量 === 2 |

---

## 推荐与决策

**推荐**：discriminated union controls 描述符 + per-turn top_p，按 B1→B5 顺序实施。

**影响范围**：跨模块（chat domain + system + pipeline + SDK + frontend），涉及公开 HTTP 接口（LLMProfileInfo 响应新增字段）和 SDK 方法签名。

**下一步实施边界（building 起点）**：
1. 从 `ChatOptions` 加 `top_p` 开始（B1），验证现有测试通过
2. 再改 pipeline top_p 读取优先级，用 `is not None` 判断
3. SDK 三个方法同步，运行 `make check` 无类型错误
4. system API 新增 `_build_controls` 纯函数，配 B5 单测先写 RED
5. 前端 types → store → 组件，每步 `npm run typecheck` 通过

**越界检查**：触及 Non-Goals（stop_sequences、DeepSeek reasoning_effort、thinking_budget per-turn）→ 停下回 planning 重评估。

**重评估条件**：
- 需要 DeepSeek/GLM reasoning_effort 控件时，新增 `kind: "deepseek_reasoning_effort"` union 成员并独立设计
- 需要 thinking_budget per-turn 时，在 ThinkingControl 加 `budget_range?: [number, number]` 字段

---

## 决策记录

**日期**：2026-06-24

**上下文与问题**：前端在 `ChatInputArea` 中硬编码 `{capabilities.thinking && <ThinkingModeSelector/>}`。随着 GLM thinking、GPT-5 reasoning_effort 的接入，每新增一类模型都要修改前端判断逻辑。项目负责人认为这是"设计烂"的信号，要求后端返回结构化的 UI 描述符。

**Decision Drivers**：
1. 可扩展性：新控件不改前端代码
2. 类型安全：TypeScript discriminated union 编译期捕获遗漏的 kind
3. 后端权威：参数范围、可用 mode 由后端决定，防止前端失同步

**候选与权衡**：扁平化字段实现简单但扩展性差；discriminated union 扩展性好且类型安全，语法复杂度可接受。

**决策结果**：采用 discriminated union `controls: list[ModelControl]`，4 个初始 kind（anthropic_thinking / openai_reasoning_effort / temperature / top_p），同步将 top_p 提升为 per-turn 可覆盖参数。

**正面后果**：前端无需感知新模型的能力细节；controls 生成逻辑可独立单测；新增控件类型不影响现有前端代码。

**负面后果**：Pydantic v2 discriminated union 需要 `Annotated + Field(discriminator="kind")` 语法，初次实现需要注意。

**重评估条件**：若控件类型超过 8 种且 payload 增大明显，考虑将 controls 拆为独立的 `/system/profile/{id}/controls` 端点。

---

*依赖参数事实：`2026-06-24-llm-api-params-reference.md`*
