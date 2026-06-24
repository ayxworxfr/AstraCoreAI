# LLM API 参数参考

> 梳理各家 LLM 的实际 API 参数，作为模型能力描述符设计的基础。
> 信息来源：各厂商官方文档 + 本项目适配器代码印证。
> 最后更新：2026-06-24

---

## 目录

1. [Anthropic Claude](#1-anthropic-claude)
2. [OpenAI GPT（Chat Completions + Responses API）](#2-openai-gpt)
3. [DeepSeek](#3-deepseek)
4. [GLM（智谱 BigModel）](#4-glm智谱-bigmodel)
5. [横向对比速查表](#5-横向对比速查表)
6. [对本项目设计的影响](#6-对本项目设计的影响)

---

## 1. Anthropic Claude

**文档来源**：https://docs.anthropic.com/en/api/messages

### 1.1 采样参数

| 参数 | 类型 | 范围 | 默认值 | 说明 |
|------|------|------|--------|------|
| `temperature` | float | 0–1 | — | 控制随机性。**与 `top_p` 互斥**（只设其一）。Thinking 模式开启时强制为 1.0，不可自定义 |
| `top_p` | float | 0–1 | — | 核采样。**与 `temperature` 互斥**。Thinking 开启时若值 < 0.95 会被忽略 |
| `top_k` | int | ≥0 | — | 限制候选 token 数。Thinking 模式开启时不可用 |
| `stop_sequences` | list[str] | 最多 8191 条 | [] | 命中时停止生成，stop_reason = "stop_sequence" |
| `max_tokens` | int | ≥1 | 必填 | 最大输出 token 数（包含 thinking block） |

### 1.2 Thinking / 推理控制

#### Extended Thinking（标准思考，`thinking.type = "enabled"`）

```json
{
  "thinking": {
    "type": "enabled",
    "budget_tokens": 8000
  }
}
```

- `budget_tokens`：最小 1024，必须 < `max_tokens`（interleaved thinking beta 除外）
- 开启后：temperature 强制 1.0，top_p / top_k 被忽略
- 开启后：不支持强制 tool_choice、不支持 response pre-fill

#### Adaptive Thinking（自适应思考，Opus 4.7+）

```json
{
  "thinking": {
    "type": "enabled"
  }
}
```

- Opus 4.7+ 仅支持此形式，**不接受 `budget_tokens`**（发送会报 400）
- 模型自动决定思考深度，不受 temperature / top_p 控制
- 本项目用 `adaptive_thinking_only=True` flag 区分

### 1.3 各模型能力矩阵

| 模型 | temperature/top_p | Extended Thinking | Adaptive Thinking |
|------|-------------------|-------------------|-------------------|
| claude-sonnet-4-6 | ✅ | ✅（budget_tokens 可设） | — |
| claude-opus-4-6 | ✅ | ✅ | — |
| claude-opus-4-7 | ❌ | ❌ | ✅（adaptive only）|

> **注**：claude-opus-4-7 发送 temperature / top_p 会收到 400 错误。
> 本项目 `model_capabilities.py` 已正确处理：Opus 4.7 的 `temperature=False`，`adaptive_thinking_only=True`。

### 1.4 本项目当前适配状态

- `anthropic.py:generate_stream()` — thinking 开启时自动将 temperature 设为 1.0，top_p < 0.95 时置 None ✅
- `model_capabilities.py` — `adaptive_thinking_only` flag 控制是否发送 `budget_tokens` ✅
- **缺口**：top_k 目前未透传（profile config 无此字段，暂不影响主流用例）

---

## 2. OpenAI GPT

OpenAI 目前有两套 API，参数体系不同：

### 2.1 Chat Completions API（gpt-4o、gpt-4-turbo 等）

**文档来源**：https://platform.openai.com/docs/api-reference/chat

| 参数 | 类型 | 范围 | 默认值 | 说明 |
|------|------|------|--------|------|
| `temperature` | float | 0–2 | 1.0 | 控制随机性 |
| `top_p` | float | 0–1 | 1.0 | 核采样，与 temperature 二选一调整 |
| `frequency_penalty` | float | -2.0–2.0 | 0 | 基于已出现频率惩罚 token |
| `presence_penalty` | float | -2.0–2.0 | 0 | 惩罚已出现过的 token，鼓励新话题 |
| `max_tokens` | int | ≥1 | — | 最大输出 token 数 |
| `stop` | str / list[str] | 最多 4 条 | — | 命中时停止生成 |
| `n` | int | ≥1 | 1 | 生成候选数量 |

> **注**：OpenAI **不支持 `top_k`**，此参数只在开源兼容服务（vLLM、Ollama）中存在。

### 2.2 Responses API（GPT-5 / o-series）

**文档来源**：https://platform.openai.com/docs/api-reference/responses

| 参数 | 类型 | 说明 |
|------|------|------|
| `reasoning.effort` | str | `"minimal"` / `"low"` / `"medium"` / `"high"`（default: `"medium"`） |
| `reasoning.summary` | str | `"auto"` / `"detailed"`，推理链摘要 |
| `max_output_tokens` | int | 最大 token 数（**包含内部推理 token**） |
| `temperature` | — | ❌ **不支持**（发送报错）|
| `top_p` | — | ❌ **不支持** |
| `frequency_penalty` | — | ❌ **不支持** |
| `presence_penalty` | — | ❌ **不支持** |

#### reasoning.effort 含义

| 值 | 含义 |
|----|------|
| `minimal` | 最低推理深度，速度最快，适合简单问题 |
| `low` | 低推理深度 |
| `medium` | 标准（默认） |
| `high` | 深度推理，更耗时 |

> `"minimal"` 仍会产生少量推理 token，与 "无推理" 不同。

### 2.3 本项目当前适配状态

- `openai.py` — Chat Completions：temperature / top_p / stop 已透传 ✅
- `openai.py(_generate_responses_stream)` — Responses API：reasoning.effort / verbosity 已透传 ✅
- `model_capabilities.py` — `reasoning_effort_capable=True` 仅对 GPT-5 系列生效 ✅
- **缺口**：frequency_penalty / presence_penalty 未在 profile config 暴露（当前需求不明确，暂不做）

---

## 3. DeepSeek

**文档来源**：https://api-docs.deepseek.com

### 3.1 标准采样参数（Chat Completions 兼容）

| 参数 | 类型 | 范围 | 默认值 | 说明 |
|------|------|------|--------|------|
| `temperature` | float | 0–2 | 1.0 | 控制随机性。**Thinking 模式开启时无效** |
| `top_p` | float | 0–1 | 1.0 | 核采样。**Thinking 模式开启时无效** |
| `max_tokens` | int | ≥1 | — | 最大输出 token 数 |
| `stop` | str / list[str] | 最多 16 条 | — | 停止序列（比 OpenAI 多） |

### 3.2 Thinking 控制

```json
{
  "thinking": {
    "type": "enabled"
  }
}
```

- DeepSeek-V4-Flash 支持此参数（通过 `extra_body` 发送，因为标准 OpenAI SDK 不识别）
- **Thinking 开启时**：temperature、top_p、presence_penalty、frequency_penalty **全部无效**

### 3.3 reasoning_effort（DeepSeek 特有）

| 值 | 说明 |
|----|------|
| `"high"` | 默认值，标准推理深度 |
| `"max"` | 最深推理，更耗时 |

- **只有两档**，与 OpenAI 的 4 档（minimal/low/medium/high）不兼容
- 必须配合 thinking 开启才生效
- **本项目当前状态**：`openai.py` 适配器未实现此参数，`reasoning_effort_capable=False` 对 DeepSeek ✅（正确）

### 3.4 DeepSeek 通过 Anthropic 协议接入（api.deepseek.com/anthropic）

- 当 base_url 含 `/anthropic` 时，走 AnthropicAdapter
- 支持 thinking 但不支持强制 tool_choice（`structured_output_via_tools=False`）
- 本项目 `model_capabilities.py:100-117` 已处理 ✅

### 3.5 本项目当前适配状态

- openai 协议：`openai.py` 的 `generate_stream()` 读取 `thinking_mode` kwargs → 写入 `extra_body` ✅
- anthropic 协议：`anthropic.py` 正常处理 ✅
- **缺口**：reasoning_effort 的两档（high/max）未透传，需要未来单独做

---

## 4. GLM（智谱 BigModel）

**文档来源**：https://docs.bigmodel.cn

### 4.1 标准采样参数

| 参数 | 类型 | 范围 / 建议值 | 说明 |
|------|------|--------------|------|
| `temperature` | float | 0.2（保守）–0.8（创意） | **与 `top_p` 互斥，不可同时使用** |
| `top_p` | float | 0.2–0.9，推荐 0.8–0.95 | 与 temperature 互斥 |
| `max_tokens` | int | 最小建议 1024 | GLM-4.5 最高 98304；GLM-5.2 最高 131072 |

### 4.2 Thinking 控制

```json
{
  "thinking": {
    "type": "enabled"
  }
}
```

- GLM-5 / 5.1 / 5-plus / GLM-4.7 系列支持
- 走 OpenAI Chat Completions 协议（非 Anthropic）
- `thinking` 通过 `extra_body` 传入（本项目 `openai.py:506-510` 已实现）
- **`clear_thinking`**（GLM 特有）：boolean，false = 保留推理上下文，true = 每轮清空思考链

### 4.3 reasoning_effort（GLM-5.2+ 特有，当前项目不支持）

GLM-5.2+ 支持更细粒度的 reasoning_effort：

| 值 | 说明 |
|----|------|
| `"none"` | 不推理 |
| `"minimal"` | 极低 |
| `"low"` | 低 |
| `"medium"` | 中 |
| `"high"` | 高 |
| `"xhigh"` | 极高 |
| `"max"` | 最高（默认） |

> **注意**：GLM-5.2 是未来型号，当前项目主要支持 GLM-5 / 5.1，这些型号**无 `reasoning_effort` 参数**。
> 本项目 `reasoning_effort_capable=False` 对 GLM-5/5.1 是正确的。

### 4.4 本项目当前适配状态

- `openai.py:generate_stream()` — thinking_mode 不为 off 且 model 含 "glm" 时写入 `extra_body` ✅
- GLM thinking 响应以 `delta.reasoning_content` 形式返回，已处理 ✅
- **缺口**：`clear_thinking` 参数未暴露（当前不影响基本功能）

---

## 5. 横向对比速查表

### 5.1 采样参数支持

| 参数 | Anthropic Claude | OpenAI Chat Completions | OpenAI Responses (GPT-5) | DeepSeek | GLM |
|------|:---:|:---:|:---:|:---:|:---:|
| temperature | ✅（0–1）| ✅（0–2）| ❌ | ✅（0–2）| ✅ |
| top_p | ✅（0–1）| ✅（0–1）| ❌ | ✅（0–1）| ✅（0.2–0.9）|
| top_k | ✅（0–∞）| ❌ | ❌ | ❌ | ❌ |
| frequency_penalty | ❌ | ✅（-2–2）| ❌ | ❌（文档未提）| ❌ |
| presence_penalty | ❌ | ✅（-2–2）| ❌ | ❌（文档未提）| ❌ |
| stop_sequences | ✅（≤8191）| ✅（≤4）| ❌ | ✅（≤16）| 未明确 |

### 5.2 Thinking / 推理控制

| 特性 | Anthropic | OpenAI Responses | DeepSeek | GLM |
|------|-----------|-----------------|----------|-----|
| 推理开关 | `thinking.type=enabled`<br>（有 budget_tokens） | 通过 `reasoning.effort` 隐式开启 | `thinking.type=enabled`<br>（extra_body） | `thinking.type=enabled`<br>（extra_body） |
| 深度控制 | `budget_tokens`（token 数量） | `reasoning.effort`（4档）| `reasoning_effort`（2档：high/max）| 无（GLM-5.2+ 有 7 档）|
| 自适应模式 | Opus 4.7+：adaptive | 全部默认自适应 | 无 | 无 |
| 开启时 temperature/top_p | ❌ 禁止（400 报错）| ❌ 禁止 | 可发但无效 | 可发但行为未定义 |

### 5.3 参数互斥规则

| 规则 | 提供方 |
|------|--------|
| temperature 和 top_p 只能用其一 | Anthropic（推荐）、GLM（强制）、OpenAI（推荐）|
| Thinking 开启时 temperature/top_p 被禁或无效 | 全部提供方 |
| top_k 只有 Anthropic 支持 | Anthropic |

---

## 6. 对本项目设计的影响

### 6.1 当前代码已正确处理的

1. **Opus 4.7 的 adaptive_thinking_only**：pipeline 不发送 budget_tokens，model_capabilities.py 有 flag ✅
2. **thinking 开启时 temperature 处理**：anthropic.py 强制 1.0，top_p 置 None ✅
3. **GLM thinking**：通过 extra_body 发送，reasoning_content 作为 THINKING_DELTA ✅
4. **GPT-5 reasoning_effort**：走 responses.py 通道，参数名映射正确 ✅
5. **DeepSeek anthropic 协议**：base_url 含 /anthropic 时走 AnthropicAdapter ✅

### 6.2 当前代码的缺口（为后续设计留存）

| 缺口 | 影响 | 优先级 |
|------|------|--------|
| top_p 仅 profile 级别，无法 per-turn 覆盖 | 用户无法在单次对话中调整核采样 | 中 |
| temperature 在 ChatOptions 中存在但前端未暴露 UI | 功能可用但用户看不到 | 中 |
| DeepSeek reasoning_effort (high/max) 未透传 | DeepSeek 无法控制推理深度 | 低 |
| GLM clear_thinking 未暴露 | 多轮推理上下文不可控 | 低 |
| GLM-5.2+ reasoning_effort (7档) 未实现 | 该模型型号暂未在本项目支持 | 低（待需求）|
| frequency_penalty / presence_penalty 未暴露 | OpenAI Chat Completions 少两个参数 | 低 |

### 6.3 per-turn 可调参数建议范围

基于各家约束，以下参数可以安全地做成 per-turn 用户可调：

| 参数 | 可调范围 | 约束 |
|------|----------|------|
| temperature | 0.0–2.0（Anthropic 0–1）| thinking 开启时不可用 |
| top_p | 0.0–1.0 | 与 temperature 互斥；thinking 开启时不可用 |
| thinking_mode | off / on / adaptive | 按 capability 决定可选项 |
| reasoning_effort | minimal / low / medium / high | 仅 OpenAI Responses API |

以下参数建议保留为 **profile 级别**（不做 per-turn UI），原因是用户价值低或语义复杂：

- `stop_sequences`：高度场景化，不适合在 toolbar 暴露
- `frequency_penalty` / `presence_penalty`：OpenAI 专有，语义复杂，普通用户难以理解
- `top_k`：仅 Anthropic 支持，与 top_p 语义相近
- `thinking_budget`：技术性太强，可考虑未来在高级设置中暴露
- `clear_thinking`：GLM 专有且边缘功能

---

*此文档作为 `model_controls` 能力描述符设计（`2026-06-24-model-controls-descriptor.md`）的参数事实基础。*
