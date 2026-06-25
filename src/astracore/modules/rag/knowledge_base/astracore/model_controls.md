---
title: AstraCoreAI 模型控件与多供应商支持
category: astracore
tags: [模型控件, LLM, 多供应商, thinking, reasoning_effort, temperature, capabilities, controls]
related: [astracore/intro, astracore/chat_pipeline, astracore/attachment_system]
---

# AstraCoreAI 模型控件与多供应商支持

## 能力描述符（LLMCapabilities）

每个 LLM profile 的能力通过 `infer_model_capabilities()` 自动推断，生成 `LLMCapabilities` 对象：

| 字段 | 说明 |
|------|------|
| `thinking` | 支持 Extended Thinking（思考块）|
| `adaptive_thinking_only` | 仅支持自适应思考（Opus 4.7+，不接受 budget_tokens）|
| `temperature` | 支持 temperature 参数（GPT-5 等推理模型不支持）|
| `top_k` | 支持 top_k 采样（Anthropic 原生，thinking 开启时适配器自动忽略）|
| `vision` | 支持图片附件 |
| `documents` | 支持原生 PDF document block（Anthropic）|
| `anthropic_blocks` | 第三方 Anthropic 兼容端点（如 DeepSeek via /anthropic），不支持强制 tool_choice |
| `reasoning_effort_protocol` | 推理档位发送协议：`"responses"` / `"extra_body"` / `None` |
| `prompt_cache` | Anthropic 原生 prompt caching |
| `structured_output_via_tools` | 是否可以通过 tool_choice 强制结构化输出 |

## 各供应商能力速查

| 模型 | thinking | vision | reasoning_effort | 备注 |
|------|---------|--------|-----------------|------|
| Claude Sonnet 4.6 | ✅ off/on/adaptive | ✅ | — | top_k 支持，prompt_cache |
| Claude Opus 4.6 | ✅ off/on/adaptive | ✅ | — | 同 Sonnet 4.6 |
| Claude Opus 4.7 | ✅ 仅 adaptive | ✅ | — | 无 budget_tokens，temperature=False |
| GPT-5 | — | ✅ | ✅ minimal/low/medium/high | Responses API，无 temperature |
| DeepSeek-V4 (openai) | ✅ off/on | — | ✅ high/max | extra_body，无 adaptive |
| DeepSeek-V4 (anthropic) | ✅ off/on | — | — | 原生 anthropic_blocks |
| GLM-5/5.1/5-plus | ✅ off/on | — | — | 无 reasoning_effort |
| GLM-5.2+ | ✅ off/on | — | ✅ 7档(none~max) | extra_body |

## 动态控件描述符（controls）

`GET /api/v1/system/` 返回每个 profile 的 `controls` 列表，前端按 `kind` 字段渲染对应控件：

| kind | 渲染位置 | 出现条件 |
|------|---------|---------|
| `thinking` | 主工具栏（ThinkingModeSelector）| `caps.thinking == True` |
| `reasoning_effort` | 主工具栏（ReasoningEffortSelector）| `caps.reasoning_effort_protocol != None` |
| `temperature` | 折叠的高级设置面板 | `caps.temperature == True` |
| `top_p` | 折叠的高级设置面板 | `caps.temperature == True` |

前端切换 profile 时，控件列表自动刷新，无需硬编码任何模型信息。

## ThinkingControl 详细规格

`modes` 字段按型号不同：
- Opus 4.7+：`["off", "adaptive"]`（不含 "on"，发送 "on" 会触发 400）
- Sonnet/Opus 4.6：`["off", "on", "adaptive"]`
- DeepSeek/GLM：`["off", "on"]`（无自适应）

## ReasoningEffortControl 详细规格

`levels` 字段因供应商不同：
- GPT-5 (Responses API)：`["minimal", "low", "medium", "high"]`
- DeepSeek (extra_body)：`["high", "max"]`
- GLM-5.2+ (extra_body)：`["none", "minimal", "low", "medium", "high", "xhigh", "max"]`

`reasoning_effort_protocol` 字段控制适配器如何发送参数：
- `"responses"`：走 Responses API，发 `reasoning.effort` 字段
- `"extra_body"`：走 Chat Completions，发 `extra_body["reasoning_effort"]`

## per-turn 参数覆盖

对话中每轮都可以通过 `ChatOptions` 覆盖参数：

```python
from astracore.modules.chat.domain.chat_options import ChatOptions

opts = ChatOptions(
    thinking_mode="adaptive",
    temperature=0.5,
    top_p=0.9,
    reasoning_effort="high",
)
```

优先级：per-turn opts > profile 默认值。`top_p=0.0` 是有效覆盖，不会被 falsy 检查跳过。

## 参数互斥规则

- **temperature 与 top_p 互斥**：设了 `top_p` 就不发 `temperature`（GLM 强制要求，Anthropic 也支持）
- **thinking 开启时 top_k 不可用**：适配器在 thinking 模式下自动忽略 `top_k`
- **adaptive_thinking_only 模型**：不接受 `budget_tokens`，也不接受 `thinking_mode="on"`

## 新增模型

只需在 `src/astracore/sdk/model_capabilities.py` 的 `infer_model_capabilities()` 函数中加一条规则，前端 controls 自动适配，无需修改 UI 代码。
