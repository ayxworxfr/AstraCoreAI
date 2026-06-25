# model_controls 能力描述符设计方案

> 设计类型：**设计卡**（跨模块 + 公开接口变更）
> 参数事实依据：`2026-06-24-llm-api-params-reference.md`
> 最后更新：2026-06-24

---

## 意图与边界

**Job-to-be-Done**：
当用户切换 LLM profile 时，前端自动调整可用的 per-turn 控件，不需要前端代码硬编码任何模型信息。常用控件（思考/推理模式）直接展示；采样参数收进"高级设置"折叠面板，默认使用 profile 配置值，用户展开后才 override。

**Goals**：
- `/system` 接口每个 profile 新增 `controls` 描述符列表，前端按 `kind` 字段动态渲染
- UI 分两层：**主工具栏**（thinking/reasoning 控件，始终可见）+ **高级设置折叠面板**（temperature/top_p，默认折叠）
- 覆盖全部推理控件：Anthropic thinking（off/on/adaptive）、各家 reasoning_effort（levels 不同，但统一 kind="reasoning_effort"）
- `top_p` 从 profile 级别提升为 per-turn 可覆盖，在高级设置面板操作，默认显示 profile 值
- SDK 三个方法加 `top_p` 参数，保持 API + SDK parity

**Non-Goals**：
- `thinking_budget` 不做 per-turn UI（profile 级别）
- `stop_sequences`、`frequency_penalty`、`presence_penalty`、`top_k`、`clear_thinking` 本次不覆盖
- `LLMCapabilitiesInfo`（公开 API 类型）的 `reasoning_effort_capable` 字段用 `reasoning_effort_protocol` 替换，前端直接读 `controls` 判断能力

**成功标准**：
- 切换到 Anthropic Claude Sonnet → 工具栏出现 ThinkingModeSelector（off/on/adaptive）；高级设置可展开 temperature(0–1) + top-p(0–1)
- 切换到 Opus 4.7+ → ThinkingModeSelector 只有 off/adaptive
- 切换到 GPT-5 → 工具栏出现 ReasoningEffortSelector（minimal/low/medium/high）；无高级设置入口
- 切换到 DeepSeek-V4-Flash（openai 协议）→ 工具栏出现 ReasoningEffortSelector（high/max）
- 切换到 GLM-5.2+ → 工具栏出现 ReasoningEffortSelector（none/minimal/low/medium/high/xhigh/max）
- 高级设置面板 temperature/top-p 初始值来自 profile 配置（`profile_default`）

---

## 核心设计思想：controls 是数据，不是 flag

**旧模式（问题根源）**：

```
infer_model_capabilities() → bool flags → _build_controls() → frontend
```

每新增一个模型特性就加一个 flag，`LLMCapabilities` 无限膨胀。

**新模式（本次方案）**：

```
infer_model_capabilities() → protocol/capability 意图 → _build_controls() → controls[] → frontend
```

`LLMCapabilities` 只保留**适配器真正需要判断的行为 flag**（发 API 时用）。前端描述信息通过 `controls` 传递，不再用 flag 间接表达。

关键原则：**新增模型时只需在 `infer_model_capabilities()` 里加一条规则，不需要新增 flag。**

---

## 变更一：替换 `reasoning_effort_capable` → `reasoning_effort_protocol`

**旧**：`reasoning_effort_capable: bool = False`（只区分"有/无"，无法区分发送协议）

**新**：`reasoning_effort_protocol: Literal["responses", "extra_body"] | None = None`

| 值 | 含义 | 适用 |
|---|---|---|
| `"responses"` | 走 OpenAI Responses API，发 `reasoning.effort` 参数 | GPT-5 系列 |
| `"extra_body"` | 走 OpenAI Chat Completions，发 `extra_body["reasoning_effort"]` | DeepSeek openai 协议、GLM-5.2+ |
| `None` | 不支持 reasoning_effort | 其余所有模型 |

这一个字段取代了旧的 `reasoning_effort_capable` + 假设要加的 `deepseek_reasoning_effort` + `glm_reasoning_effort` 三个 flag。适配器（openai.py）读这个字段决定如何发参数；`_build_controls` 读这个字段决定是否生成 reasoning_effort 控件。

不需要新 flag，只需在 `infer_model_capabilities()` 里为 DeepSeek openai 协议和 GLM-5.2+ 设置 `reasoning_effort_protocol="extra_body"`。

---

## 变更二：controls 只有 4 种 kind

| kind | 何时出现 | 前端渲染 |
|---|---|---|
| `"thinking"` | `caps.thinking == True` | ThinkingModeSelector（胶囊下拉，主工具栏）|
| `"reasoning_effort"` | `caps.reasoning_effort_protocol != None` | ReasoningEffortSelector（胶囊下拉，主工具栏）|
| `"temperature"` | `caps.temperature == True` | 滑块（高级设置面板）|
| `"top_p"` | `caps.temperature == True` | 滑块（高级设置面板）|

`reasoning_effort` 是**统一 kind**，levels 字段编码各家的可选值——OpenAI 是 4 档，DeepSeek 是 2 档，GLM 是 7 档，前端不感知品牌，只渲染 levels。

**TypeScript 类型**：

```typescript
type ThinkingControl = {
  kind: 'thinking';
  modes: string[];   // e.g. ["off","on","adaptive"]
  default: string;
};

type ReasoningEffortControl = {
  kind: 'reasoning_effort';
  levels: string[];  // e.g. ["high","max"] or ["minimal","low","medium","high"]
  default: string;
};

type TemperatureControl = {
  kind: 'temperature';
  min: number; max: number; step: number;
  profile_default: number;
};

type TopPControl = {
  kind: 'top_p';
  min: number; max: number; step: number;
  profile_default: number | null;
};

type ModelControl = ThinkingControl | ReasoningEffortControl | TemperatureControl | TopPControl;
```

---

## 项目事实

### 调用方 grep（影响 ChatOptions + SDK 方法签名）

| 文件:行 | 用途 |
|---|---|
| `modules/chat/api.py:271-278` | `to_options()` 映射 ChatRequest → ChatOptions |
| `modules/chat/api.py:551,818` | `request.to_options().apply(...)` |
| `sdk/client.py:392-427` | `conversation()` 逐字段构建 ChatOptions |
| `sdk/client.py:443-471` | `chat_stream()` 同上 |
| `sdk/client.py:489-511` | `chat()` 同上 |
| `eval/dataset.py:39` | `EvalCase.options: ChatOptions`（dataclass，向前兼容）|
| `tests/sdk/test_register_tool.py:130-146` | `test_chat_options_fields()` subset 检查 |

### 调用方 grep（影响 LLMCapabilities.reasoning_effort_capable）

| 文件:行 | 用途 |
|---|---|
| `modules/chat/pipeline.py:404-411` | `if profile.capabilities.reasoning_effort_capable:` |
| `modules/system/api.py:70-76` | `LLMCapabilitiesInfo(reasoning_effort_capable=...)` 序列化 |
| `infrastructure/llm/openai.py` | responses API 分支判断 |
| `sdk/model_capabilities.py:18-19` | 字段定义 |

### Pipeline top_p 当前逻辑

```python
# pipeline.py:413-417  当前：settings > profile，未读 opts
saved_top_p = await self._get_setting("top_p", user_id)
effective_top_p: float | None = float(saved_top_p) if saved_top_p else profile.top_p
```
→ 改为三级：`opts.top_p`（is not None）> settings > profile

---

## 档位

**选定：设计卡**（跨模块 + 公开接口 + SDK 签名变更）

---

## diff 预算

| 文件 | 变更 | 行数 |
|---|---|---|
| `sdk/model_capabilities.py` | `reasoning_effort_capable` → `reasoning_effort_protocol` + GLM-5.2/DeepSeek 规则 | ~20 |
| `modules/chat/pipeline.py` | `reasoning_effort_capable` → `reasoning_effort_protocol` + top_p 三级读取 | ~10 |
| `infrastructure/llm/openai.py` | 按 `reasoning_effort_protocol` 分 responses/extra_body 路由 | ~20 |
| `modules/chat/domain/chat_options.py` | 加 `top_p` 字段 | +3 |
| `modules/chat/api.py` | ChatRequest 加 `top_p` + to_options | +6 |
| `sdk/client.py` | 3 个方法加 `top_p` | +15 |
| `modules/system/api.py` | `reasoning_effort_capable` → `reasoning_effort_protocol` + ModelControl 4-kind union + `_build_controls` | ~100 |
| `frontend/src/features/system/types.ts` | controls TS 类型（4 kind）| +45 |
| `frontend/src/features/chat/store/chatStore.ts` | temperature + topP 状态 | +25 |
| `frontend/src/shared/types/api.ts` | ChatRequest 加 `top_p` | +5 |
| `frontend/.../ChatInputArea.tsx` | 主工具栏 controls 驱动 + 高级设置面板 | +70 |
| `frontend/.../ThinkingBlock.tsx` | ThinkingModeSelector 接收 modes prop | +10 |
| 新增 `frontend/.../ReasoningEffortSelector.tsx` | 复用胶囊选择器 | +55 |
| `tests/sdk/test_register_tool.py` | expected 字段集加 `top_p` | +3 |
| `tests/` 新增测试 | controls 生成 + top_p 透传 + reasoning_effort 路由 | +90 |

**合计**：~15 文件，~480 行

---

## 完整实现规格

### A. LLMCapabilities 改动

```python
# model_capabilities.py — 仅改一个字段

# 删除：
reasoning_effort_capable: bool = False

# 替换为：
reasoning_effort_protocol: Literal["responses", "extra_body"] | None = None
"""推理档位参数的发送协议。
'responses' = OpenAI Responses API (GPT-5)，发 reasoning.effort。
'extra_body' = Chat Completions extra_body，适用于 DeepSeek/GLM。
None = 不支持。
"""
```

`infer_model_capabilities` 对应改动：
- GPT-5 系列：`reasoning_effort_protocol="responses"`
- DeepSeek-V4-Flash (openai 协议)：`reasoning_effort_protocol="extra_body"`
- GLM-5.2+（新增规则）：`reasoning_effort_protocol="extra_body"`
- 其余：保持 `None`（默认值）

### B. _build_controls 函数

```python
def _build_controls(profile: LLMProfileConfig) -> list[ModelControl]:
    caps = profile.capabilities
    controls: list[ModelControl] = []

    # ── Thinking（主工具栏）────────────────────────────────────────────
    if caps.adaptive_thinking_only:
        controls.append(ThinkingControl(modes=["off", "adaptive"], default="off"))
    elif caps.thinking:
        controls.append(ThinkingControl(modes=["off", "on", "adaptive"], default="off"))

    # ── Reasoning effort（主工具栏）───────────────────────────────────
    # levels 由模型决定；kind 统一为 "reasoning_effort"，前端不感知品牌
    if caps.reasoning_effort_protocol == "responses":
        controls.append(ReasoningEffortControl(
            levels=["minimal", "low", "medium", "high"], default="medium"))
    elif caps.reasoning_effort_protocol == "extra_body":
        model = profile.model.lower()
        if "deepseek" in model:
            controls.append(ReasoningEffortControl(
                levels=["high", "max"], default="high"))
        elif "glm" in model:
            controls.append(ReasoningEffortControl(
                levels=["none", "minimal", "low", "medium", "high", "xhigh", "max"],
                default="max"))

    # ── 采样参数（高级设置面板）────────────────────────────────────────
    if caps.temperature:
        max_temp = 1.0 if profile.protocol == "anthropic" else 2.0
        controls.append(TemperatureControl(
            min=0.0, max=max_temp, step=0.01,
            profile_default=profile.temperature))
        controls.append(TopPControl(
            min=0.0, max=1.0, step=0.01,
            profile_default=profile.top_p))

    return controls
```

新增模型时：只需在 `infer_model_capabilities()` 里加一条规则，不需要碰 `_build_controls`。

### C. pipeline.py — reasoning_effort_capable → reasoning_effort_protocol

```python
# 旧：
if profile.capabilities.reasoning_effort_capable:

# 新：
if profile.capabilities.reasoning_effort_protocol == "responses":
```

DeepSeek/GLM 的 reasoning_effort 走适配器层（openai.py），pipeline 只需处理 responses 协议分支。

### D. openai.py — reasoning_effort 路由

```python
# 当前已有 responses 分支（GPT-5），新增 extra_body 分支：

if caps.reasoning_effort_protocol == "extra_body" and kwargs.get("reasoning_effort"):
    extra_body = extra_body or {}
    extra_body["reasoning_effort"] = kwargs["reasoning_effort"]
```

### E. ChatOptions + ChatRequest + SDK — 加 top_p

```python
# chat_options.py
top_p: float | None = None

# chat/api.py ChatRequest
top_p: float | None = Field(default=None, ge=0.0, le=1.0)
# to_options() 补 top_p=self.top_p

# pipeline.py top_p 三级读取
effective_top_p = (
    opts.top_p if opts.top_p is not None
    else (float(saved_top_p) if saved_top_p else profile.top_p)
)

# sdk/client.py 三个方法各加 top_p: float | None = None
```

### F. UI 架构

```
ChatInputArea
├─ 主工具栏（始终可见）
│  ├─ ThinkingModeSelector    ← controls.find(c => c.kind === 'thinking')
│  ├─ ReasoningEffortSelector ← controls.find(c => c.kind === 'reasoning_effort')
│  ├─ RAG / 工具 / 联网 / ModelSelector / 附件
│  └─ [高级] 入口按钮（仅当有 temperature/top_p 控件时显示）
└─ 高级设置折叠面板（默认折叠）
   ├─ Temperature 滑块（profile_default 为初始值，修改后写入 chatStore.temperature）
   └─ Top-P 滑块（profile_default 为初始值，修改后写入 chatStore.topP）
```

交互规则：
- 折叠时 chatStore.temperature / topP 为 null → 发送时不透传（pipeline 用 settings/profile 值）
- 用户移动滑块 → chatStore 记录值 → 下次发送透传
- 切换 profile → 重置 temperature/topP 为 null

---

## S/B 拆分

| commit | prefix | 内容 |
|---|---|---|
| B1 | `feat(capabilities):` | `reasoning_effort_capable` → `reasoning_effort_protocol` + GLM-5.2/DeepSeek 规则 |
| B2 | `feat(domain):` | ChatOptions + ChatRequest + to_options + pipeline top_p 三级读取 + pipeline reasoning_effort_protocol |
| B3 | `feat(sdk):` | SDK 三个方法加 top_p |
| B4 | `feat(llm):` | openai.py extra_body reasoning_effort 路由 |
| B5 | `feat(system):` | ModelControl 4-kind union + `_build_controls` + LLMProfileInfo.controls |
| B6 | `feat(frontend):` | TS 类型 + chatStore + ReasoningEffortSelector + ChatInputArea 重构 |
| B7 | `test:` | controls 生成单测 + top_p 透传 + reasoning_effort 路由 |

---

## 失败模式与验证

| 失败模式 | 级别 | 验证项 |
|---|---|---|
| Opus 4.7+ ThinkingControl.modes 含 "on" → 用户选中 → 后端 400 | High | 单测：`_build_controls(opus47_profile)` modes 不含 "on" |
| Pipeline opts.top_p=0.0 被 falsy 跳过，用 profile 旧值 | High | 单测：opts.top_p=0.0 → llm_kwargs["top_p"] == 0.0 |
| GPT-5 profile controls 出现 TemperatureControl → 前端暴露滑块 → API 报 400 | High | 单测：`_build_controls(gpt5_profile)` 不含 kind=="temperature" |
| DeepSeek extra_body 路径 reasoning_effort 未透传 | High | 单测：DeepSeek openai profile + reasoning_effort="max" → extra_body 含该字段 |
| 切换至 GPT-5 后 temperature override 未重置，下次发送仍带 temperature | Med | 集成：切换 profile → chatStore.temperature === null |
| GLM-5/5.1（无 reasoning_effort）误生成 ReasoningEffortControl | Med | 单测：`_build_controls(glm5_profile)` controls 不含 kind=="reasoning_effort" |

---

## 推荐与决策

**推荐**：单一 `reasoning_effort_protocol` 字段 + 4-kind controls 统一描述符 + 主工具栏/高级设置 UI 分层。

**核心权衡**：
- 不选"每家一个 flag"：flag 数量随模型数量线性增长，维护成本高，调用方到处写 `if reasoning_effort_capable or deepseek_reasoning_effort or ...`
- 不选"把 levels 放进 LLMCapabilities"：capabilities 是适配器内部协议，不应承担 UI 描述职责
- 选"protocol + controls 分离"：capabilities 只说发送协议（HOW），controls 说 UI 内容（WHAT），各司其职

**影响范围**：跨模块（capabilities + chat domain + system + openai adapter + SDK + frontend），涉及公开接口（LLMProfileInfo.controls 新字段、ChatOptions + SDK 方法签名加 top_p）。

**越界检查**：触及 Non-Goals → 停下回 planning。

**重评估条件**：
- 若某家 provider 同时支持 thinking + reasoning_effort（如 Claude 未来版本），`_build_controls` 同时输出两个控件，无需改结构
- 若 reasoning_effort levels 超过 10 档或需要多语言 label，在 `ReasoningEffortControl` 加 `labels: list[str]` 字段
- 若控件种类超过 8 种，考虑单独的 `/system/controls/{profile_id}` 端点

---

## 决策记录

**日期**：2026-06-24

**问题**：最初设计为每个 provider 变体加一个 bool flag（`deepseek_reasoning_effort`, `glm_reasoning_effort` 等），用户指出这是 flag 爆炸模式，不可维护。

**决策**：用 `reasoning_effort_protocol: Literal["responses", "extra_body"] | None` 单字段替换所有 reasoning_effort 相关 flag，同时统一 controls 的 `reasoning_effort` kind——levels 由 profile 模型名决定，前端只渲染 levels 列表，不感知品牌。

**后果**：
- 正面：新增支持 reasoning_effort 的模型只需在 `infer_model_capabilities()` 里加一行；`_build_controls` 的 extra_body 分支按 model 名查 levels，增加模型只需加一条 if-elif
- 负面：`_build_controls` 的 extra_body 分支用 model 名字符串匹配 levels，如果 DeepSeek 未来增加档位需要同步更新

---

*依赖参数事实：`2026-06-24-llm-api-params-reference.md`*
