# LLM 参数配置审计与升级方案

> 状态：方案卡（settings 升级）
> 日期：2026-06-18
> 范围：`LLMProfileConfig` schema、Anthropic / OpenAI 适配器调用面、`policy.*` 配置加载、`config.yaml` 表结构

---

## 意图与边界

### Job-to-be-Done

> 当我（运维 / 应用开发者）配置一个新 LLM profile 时，
> 我希望能精细控制采样行为、推理深度、成本档位、超时与重试、失败降级，
> 以便对不同 provider / model 用最合适的参数，并在异常时不丢可用性。

### Goals

1. **profile 级开放采样参数**：`top_p` / `stop_sequences` / `seed` / `frequency_penalty` / `presence_penalty`
2. **接入 GPT-5 推理控制面**：`reasoning_effort` / `verbosity`
3. **接入 Anthropic prompt caching**：System Prompt + Tier-1 memory 命中缓存
4. **policy.\* 从 yaml 加载**：retry / timeout / budget / truncation 当前完全硬编码
5. **per-profile 覆盖**：每个 profile 可独立设 `timeout_ms` / `max_retries`
6. **service_tier**：Anthropic `priority|standard|batch`、OpenAI `flex|default|auto`
7. **fallback 链**：主 profile 429/5xx 时自动降级到备 profile
8. **stream_options.include_usage**：OpenAI 流式补齐 token 用量回报

### Non-Goals

- 不重写 provider SDK（继续用官方 `anthropic` / `openai`）
- 不实现自研 LLM gateway（不替代 LiteLLM / Portkey）
- 本期不接 1M context beta header（按需触发再做）
- 不做 `logit_bias` / `logprobs`（调试场景，不影响生产）
- 不实现日预算 / 月预算 / rpm-tpm 限速（运维基础设施层职责）
- **本期不做 fallback 链**（用户决策 2026-06-18，Q1=不需要；切片 D 暂搁置）
- **本期不做切片 E**（合规字段 / seed / penalties 留给后续按需）

### 成功标准

- 新增的 7 类参数在 `config.example.yaml` 有清晰示例与注释
- 每个参数有对应单测覆盖：`schema 校验 + provider 调用面正确透传`
- Anthropic prompt cache 启用后，连续两次相同 system prompt 的请求 `cache_read_input_tokens` > 0
- 主 profile 故意配错 api_key 时，自动降级到备用 profile 完成回复
- `make check` 全绿；`make test` 在新增 N 个用例下全过

---

## 决策驱动变量

| 变量 | 类别 | 取值 | 来源 |
|---|---|---|---|
| 当前主用 provider | driver | Anthropic Claude（主，sonnet-4-6 / opus-4-7）+ OpenAI 兼容（DeepSeek + GPT-5 via 代理）| 项目事实：`config/config.example.yaml:1-26` |
| Tier-1 memory 注入是否稳定前缀 | driver | 是；`pipeline.py::_build_system_prompt` 5-6 段固定顺序 | 项目事实：`src/astracore/modules/chat/pipeline.py` |
| 是否需要 fallback 链 | **已决策** | **否**（用户 2026-06-18：宁可整会话失败也不要降智体验）| Q1 答复 |
| service_tier 偏好 | **已决策** | **对话流 `priority` + 离线任务 `batch`** | Q2 答复 |
| GPT-5 reasoning 默认档 | **已决策** | **`medium`**（OpenAI 官方默认）| Q3 答复 |
| 实施顺序 | **已决策** | **先做 A+B+C；D 永久搁置；E 按需** | Q4 答复 |
| policy 配置切换层级 | inferred | 全局默认 + per-profile 覆盖（双层）| 行业方案：[LiteLLM model-level overrides](https://docs.litellm.ai/docs/proxy/configs) |

---

## 项目事实

### 当前已暴露参数（profile 级）

`src/astracore/sdk/config.py:15-30` `LLMProfileConfig`：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `temperature` | float, 0.0-2.0 | 0.7 | ✓ |
| `max_tokens` | int, ≥1 | 8192 | ✓ |
| `capabilities` | LLMCapabilities | inferred | tools / thinking / temperature / anthropic_blocks / structured_output_via_tools |

### 当前调用面参数（隐藏在 kwargs）

`src/astracore/infrastructure/llm/anthropic.py:248-265`：

- `enable_thinking: bool` — kwargs，默认 false
- `thinking_budget: int` — kwargs，默认 8000

OpenAI 适配器：**完全没有** reasoning_effort / verbosity / seed / frequency_penalty / parallel_tool_calls 接入。

### policy 配置（全局硬编码）

`src/astracore/shared/policy/rules.py:1-48`：

```python
class RetryRule(BaseModel):
    max_retries: int = 3
    initial_delay_ms: int = 1000
    max_delay_ms: int = 30_000
    retry_on_status_codes: list[int] = [429, 500, 502, 503, 504]

class TimeoutRule(BaseModel):
    llm_timeout_ms: int = 180_000  # 3 min
    tool_timeout_ms: int = 60_000
    retrieval_timeout_ms: int = 10_000

class BudgetRule(BaseModel):
    max_input_tokens: int = 100_000
    max_output_tokens: int = 4_096
    ...
```

⚠️ **核心问题**：这些 Rule 类**根本没有从 `config.yaml` 加载**，永远是 pydantic 默认值；`AstraCoreConfig` schema 里也没有 `policy` 字段。运维改不了。

### Capabilities 推断

`src/astracore/sdk/model_capabilities.py` 已能根据 `protocol + model + base_url` 自动推断 thinking / temperature 能力；新增的 `reasoning_effort` / `cache_control` / `service_tier` 也应纳入 capabilities flag，避免硬编码 if 分支。

---

## 缺失项 × 行业方案对照

> 参考：[LLM Temperature and Sampling Reference 2026](https://sureprompts.com/blog/llm-temperature-sampling-complete-guide-2026)、[GPT-5 New Params](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_new_params_and_tools)、[Claude Extended Thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)、[LiteLLM Input Params](https://docs.litellm.ai/docs/completion/input)、[Portkey Failover](https://portkey.ai/docs/guides/use-cases/enterprise-ready-unified-api)

### 🔴 高优先级（影响成本 / 可用性 / 监控）

| 缺失项 | 行业现状 | 我们的现状 | 影响 |
|---|---|---|---|
| **`top_p`** | OpenAI / Anthropic / 几乎所有 LLM 标配 | 没暴露 | 需要"既稳又有变化"的场景（如多样化生成、避免 mode collapse）调不了 |
| **`stop_sequences`** | 同上 | 没暴露 | 强终止规则（如 ReAct `Observation:`）只能靠 prompt 约束 |
| **Anthropic Prompt Caching** | 4 级 `cache_control` 标记，5 分钟 TTL，命中后输入 cost 降至 10% | **完全没接** | 我们 system prompt 5-6 段稳定前缀（injection_guard + identity + skill manifest + tier-1 memory），是 prompt cache 的最佳场景；不接等于每轮多花 ~90% 输入费用 |
| **`reasoning_effort` (GPT-5 / o-series)** | `minimal\|low\|medium\|high`，决定推理深度与工具召回意愿 | 完全没接 | `gpt-5` profile 等价于固定 medium，简单任务多花钱、复杂任务又不够深 |
| **`verbosity` (GPT-5)** | `low\|medium\|high`，控制回答长度独立于推理深度 | 完全没接 | 同上 |
| **`service_tier`** | Anthropic `priority\|standard\|batch`、OpenAI `flex\|default\|auto` | 完全没接 | 离线任务（如 memory 摘要、记忆晋升判断）可走 batch / flex 省 50% cost |
| **policy.\* yaml 化 + per-profile 覆盖** | LiteLLM 每个 model 独立 `timeout / num_retries / retry_policy` | 全局硬编码 | DeepSeek / Claude 速度差 3-5 倍，同一 timeout 不合理 |
| **`stream_options.include_usage`** | OpenAI 流式必须显式开启才有 usage | 没设 | 流式调用拿不到 token 用量，监控数据缺一半 |

### 🟡 中优先级（韧性 / 调试 / 合规）

| 缺失项 | 行业现状 | 我们的现状 | 影响 |
|---|---|---|---|
| **fallback 链** | LiteLLM `fallbacks=[...]` / Portkey 自动 failover | 主 profile 挂掉 → 整个会话挂 | 单点故障 |
| **`seed`** | OpenAI 支持，对 eval / debug 有用 | 没暴露 | eval 复现性弱 |
| **`parallel_tool_calls`** | OpenAI 默认 true | 没控制 | 工具有副作用时需要强制串行（如顺序写库） |
| **`safety_identifier` / `user`** | OpenAI 合规字段，传 user_id 利于滥用检测 | 没透传 | 走代理 / 自有账号被滥用时无追溯 |
| **adaptive thinking** | Opus 4.7+ **不再支持**手动 `budget_tokens`，必须切 `thinking: {type: adaptive}` | 还在用 `type: enabled, budget_tokens: 8000` | Opus 4.7/4.8 调用会报 400 |
| **`frequency_penalty` / `presence_penalty`** | OpenAI 标配，默认 0 | 没暴露 | 长开放生成场景调不了；默认 0 不影响主路径 |

### 🟢 低优先级（按需）

| 缺失项 | 说明 |
|---|---|
| `top_k` | Anthropic 支持但很少调；`top_p` 已能覆盖大部分需求 |
| `logit_bias` / `logprobs` / `top_logprobs` | 仅调试 / 概率探针场景 |
| 1M context beta header | Sonnet 4 长上下文，按需启用 |
| fine-grained tool streaming | Anthropic 已 GA，目前流式工具调用粒度足够，按需 |
| `n` 多采样 | 我们没这种场景 |

---

## 推荐方案：分 5 个切片

每个切片独立可上线、独立验证。**强烈建议按顺序实施**——切片 A/B 是用户可见的能力，切片 C 是运维基础，切片 D/E 增强韧性与合规。

### 切片 A — 采样基础 + Anthropic Prompt Cache（最高 ROI）

**改动文件（预计）**：

- `src/astracore/sdk/config.py` — `LLMProfileConfig` 新增字段
- `src/astracore/sdk/model_capabilities.py` — `LLMCapabilities` 新增 `prompt_cache: bool` flag
- `src/astracore/infrastructure/llm/anthropic.py` — 透传新参数、注入 `cache_control`
- `src/astracore/infrastructure/llm/openai.py` — 透传新参数
- `config/config.example.yaml` — 示例 + 注释
- 测试：4-5 个新单测

**新字段**：

```yaml
profiles:
  - id: claude-sonnet
    # 已有：temperature / max_tokens
    top_p: null              # null = 不发送，使用 provider 默认
    stop_sequences: []       # 列表，OpenAI 与 Anthropic 都接
    enable_prompt_cache: true   # 仅 anthropic 协议生效；自动给 system 加 cache_control
```

**Prompt cache 实施要点**：

- `pipeline.py::_build_system_prompt` 已经把 system prompt 拼成 string；需要重构为返回 `list[dict]`，对 Anthropic 协议拆成 4 段（injection_guard + identity + skill_manifest + tier1_memory），最后一段加 `cache_control: {type: ephemeral}`
- 第一次调用 cost 增加 25%（写缓存），第二次起降至 10%；按用户日均 50 轮估算，整体输入 cost 降 ~80%
- OpenAI 协议直接忽略 cache 字段（capabilities 推断为 false）

**预期收益**：输入 token 成本降低 ~80%（按当前 system prompt 约 3-5k tokens 估算）

**diff 预算**：~150 行

---

### 切片 B — 推理控制（GPT-5 + Adaptive Thinking）

**改动**：

- `LLMProfileConfig` 新增：`reasoning_effort` / `verbosity` / `thinking_mode`（替代 enable_thinking）
- `model_capabilities.py` 新增能力推断：GPT-5 系 → reasoning capable；Opus 4.7+ → adaptive_thinking only
- Anthropic adapter：根据 capabilities 决定发 `enabled` 还是 `adaptive`
- OpenAI adapter：透传 `reasoning_effort` 与 `verbosity`（仅 reasoning 模型）

**新字段**：

```yaml
profiles:
  - id: gpt-5-5
    reasoning_effort: medium     # minimal | low | medium | high
    verbosity: medium            # low | medium | high

  - id: claude-opus-4-7
    thinking_mode: adaptive      # adaptive (default for opus 4.7+) | enabled | off
    thinking_budget: 8000        # 仅 thinking_mode=enabled 生效
```

**预期收益**：

- GPT-5 复杂任务可设 `high` 拿满质量；简单 routing 可设 `minimal` 省 token
- Opus 4.7/4.8 切到 adaptive，避免 400 错误

**diff 预算**：~120 行

---

### 切片 C — Policy YAML 化 + Per-profile 覆盖 + service_tier + Stream Usage（运维基础）

**改动**：

- `AstraCoreConfig` schema 新增 `policy: PolicyConfig` 字段
- `PolicyConfig` 在 `shared/policy/rules.py` 已有，但要从 yaml 加载
- `LLMProfileConfig` 新增：`timeout_ms` / `max_retries` / `service_tier`（覆盖全局 policy）
- `PolicyEngine` 调用面接受 profile-level overrides
- OpenAI adapter 流式：`stream_options={"include_usage": true}`

**新字段**：

```yaml
policy:
  retry:
    max_retries: 3
    retry_on_status_codes: [429, 500, 502, 503, 504]
  timeout:
    llm_timeout_ms: 180000
    tool_timeout_ms: 60000
  budget:
    max_input_tokens: 200000
    max_output_tokens: 4096

profiles:
  - id: claude-sonnet
    timeout_ms: 240000           # Claude 慢，覆盖全局 180s
    max_retries: 5               # priority tier 配高重试
    service_tier: priority       # priority | standard | batch
```

**预期收益**：

- 慢 provider（Claude 长输出）独立放宽 timeout，不连累整个系统
- 离线任务（memory 摘要、晋升判断）走 batch / flex 省 50% cost
- 流式监控数据完整

**diff 预算**：~180 行

---

### ~~切片 D — Fallback 链~~（已搁置）

> 用户决策 2026-06-18：不做。宁可整会话失败也不要"降智"体验。
> 如未来运维角度需要可用性，重新激活此切片。

---

### ~~切片 E — 合规与可观测~~（按需，未排期）

> 用户决策 2026-06-18：本期不做。`seed` / `frequency_penalty` / `presence_penalty` / `parallel_tool_calls` / `safety_identifier` 留给后续按需补。

---

## S/B 拆分（设计卡级）

每个切片内部按 Tidy First 拆 commit：

| 切片 | S 类（结构） | B 类（行为） |
|---|---|---|
| A | `_build_system_prompt` 返回值从 `str` 改为 `list[block]`（行为不变） | 加 `top_p / stop_sequences / cache_control` 透传 |
| B | 抽 `_resolve_thinking_config()` helper（行为不变） | 加 reasoning_effort / verbosity / adaptive |
| C | `PolicyEngine` 改成接受 per-call overrides 参数（行为不变） | 加 yaml 加载 + profile 级字段 + service_tier |

---

## 失败模式与验证

| ID | 失败模式 | 级别 | 验证项（RED 测试优先） |
|---|---|---|---|
| F-1 | `top_p` 与 `temperature` 都设非默认时，模型分布塌缩成 mode collapse | Med | 单测：`top_p=0.1, temperature=2.0` 时回复仍多样（用 hash 检查 N 次输出 != 单点） |
| F-2 | Anthropic prompt cache 误用：cache_control 加在动态前缀上，导致每次都 miss | High | 单测：连续两次相同 system prompt，第二次 `cache_read_input_tokens > 0`（live API） + 单元测试 system 拆分顺序固定 |
| F-3 | Opus 4.7+ 仍发 `thinking: {type: enabled, budget_tokens: N}` → 400 错误 | High | 单测：`model=claude-opus-4-7` 时 capabilities.thinking_mode = adaptive，request 不含 budget_tokens |
| F-4 | OpenAI 非 reasoning 模型发 `reasoning_effort` → 400 | High | 单测：`gpt-4o` profile 设 reasoning_effort 时 schema 校验失败或被剥离 |
| F-5 | policy yaml 字段误写成全局生效但 profile 级覆盖被忽略 | High | 单测：profile.timeout_ms=10000 时实际 asyncio.wait_for 用 10s（mock asyncio） |
| F-6 | ~~fallback 链触发后 token 用量没归到正确 profile~~ | — | 切片 D 搁置，N/A |
| F-7 | stream 模式下 OpenAI 不发 usage（缺 stream_options） | Med | 集成测试：流式跑完后 usage 字段非零 |
| F-8 | Anthropic prompt cache 启用后，system prompt 因为时间戳变化导致每次 miss | High | 测试 `_build_system_prompt` 对同一 ChatContext 多次调用结果稳定（identity 段时间戳除外）；时间戳应放在**非 cache 段** |
| F-9 | service_tier=batch 但请求是流式 → 不兼容 | Low | 配置校验：batch tier 拒绝流式请求 |
| F-10 | 离线任务（memory 摘要/晋升）忘记切到 batch tier，仍走 priority | Med | 调用方 grep `MemoryEngine._summarize / _judge_promotion`，断言它们用的 profile/调用面带 `service_tier=batch` |

---

## 影响范围

- **局部**：切片 A/B/E（schema + adapter 内部）
- **跨模块**：切片 C（policy 加载链路触达 SDK / app 启动 / pipeline）
- **公开接口**：切片 D（fallback 链改变 LLMResponse.model 语义；调用方需要知道实际命中的 profile）

### 调用方 grep 结果（公开接口变更需关注）

切片 A 改 `_build_system_prompt` 返回类型：

- `pipeline.py:_stream_normal` 调用方一处
- `pipeline.py:_stream_tool_loop` 调用方一处

切片 D 改 `LLMResponse`：

- `usage` 字段消费方需 grep（已有几处 trace / metrics 写入）

---

## diff 预算汇总（本期）

| 切片 | 文件数 | 行数 | 状态 |
|---|---|---|---|
| A 后端 采样 + Anthropic prompt cache | 5-6 | 150 | 本期实施 |
| B 后端 推理控制（reasoning/verbosity/adaptive） | 4-5 | 120 | 本期实施 |
| C 后端 policy yaml + per-profile 覆盖 + service_tier + stream usage | 6-8 | 180 | 本期实施 |
| F 前端 思考模式按钮 + 运行参数页两层结构 | 7 | 600 | 本期实施 |
| ~~D fallback 链~~ | — | — | 搁置（用户决策） |
| ~~E 合规字段~~ | — | — | 按需 |
| **合计（本期）** | **~17 unique** | **~1050** | 分 4 次发布 |

切片关系：A → B → F（前端依赖 A/B 后端字段就绪）；C 独立可与 F 并行。

---

## 决策记录（2026-06-18）

| 问题 | 决策 |
|---|---|
| Q1 fallback 链 | **不做**。宁可整会话失败也不要"降智"体验；如未来需要可用性再激活切片 D |
| Q2 service_tier | **对话流 `priority` + 离线任务（memory 摘要 / 晋升判断）`batch`** |
| Q3 GPT-5 reasoning 默认 | **`medium`**（OpenAI 官方默认，平衡推理深度与成本） |
| Q4 实施顺序 | **A → B → C**，分 3 次提交；D 永久搁置；E 按需 |

### 切片 C 中 service_tier 落地细节

- `LLMProfileConfig` 新增 `service_tier: Literal["priority","standard","batch","flex","auto"] | None`
- 调用层根据**调用上下文**决定具体 tier 而不只看 profile：
  - 对话流（`pipeline.stream`）→ profile 设的默认 tier（建议 `priority`）
  - memory 摘要 / 晋升判断（`MemoryEngine._summarize` / `_judge_promotion`）→ 强制覆盖为 `batch`（Anthropic）/ `flex`（OpenAI）
  - HistoryCompactor 摘要 → 同上 batch
- 调用面新增 `service_tier_override` kwarg；离线消费方用此参数覆盖 profile 默认

---

## 切片 F — 前端 UI 配套（与 A/B/C 并行交付）

> 决策日期：2026-06-18
> 触发：用户提出"对话框深度思考模式选择"+"系统设置运行参数"两块需要优化
> 依赖：切片 A（top_p / stop_sequences 后端字段）、切片 B（reasoning_effort / verbosity / thinking_mode 后端字段）

### F.1 现状盘点

| 位置 | 文件:行 | 现状 |
|---|---|---|
| 对话框工具栏"深度思考"按钮 | `ChatMain.tsx:917-939` | 单 bool toggle，对所有 profile 一刀切；不区分 Sonnet/Opus/GPT-5 不同语义 |
| 系统设置"运行参数"Tab | `SystemPage.tsx:270-482` | 全局 5 字段平铺：`temperature` / `rag_top_k` / `context_max_messages` / `timezone` / `thinking_collapse_mode` |
| 参数存储 | `skillStore.ts:30-39` | zustand + 后端 `/api/v1/settings`（user-level） |

### F.2 决策记录（2026-06-18）

| 问题 | 决策 |
|---|---|
| Q5 思考模式按钮形态 | **智能感知按钮**：单按钮入口，根据当前 profile 切语义。Sonnet→开/关 toggle；Opus 4.7+→自适应/关 toggle；GPT-5→四档下拉（关闭/最小/中/高） |
| Q6 前端暴露的新参数 | **`top_p`** + **`stop_sequences`**（全局）+ **`verbosity`**（GPT-5 per-profile）；`timeout_ms` / `max_retries` 留 yaml；`enable_prompt_cache` 默认开不暴露 UI |
| Q7 参数页结构 | **全局默认 + Per-Profile 覆盖**两层结构 |

### F.3 思考模式按钮重塑（Q5 落地）

**组件契约**：

```text
ThinkingModeButton (新组件，替代 ChatMain.tsx:917-939 当前 Button)
├─ 触发器（按钮主体）
│  ├─ 图标：✦ (BulbOutlined / ThunderboltOutlined)
│  ├─ 文本：根据 profile 能力 + 当前值动态计算
│  └─ 状态指示：● 开启 | 文字档位 | 灰态 关闭
└─ 弹层（点击展开）
   ├─ Sonnet/Opus profile：单 Switch（开/关 / 自适应/关）
   └─ GPT-5 profile：Radio.Group 四档（关闭/最小/中/高）+ verbosity 二级 Radio
```

**State 模型**（chatStore 调整）：

```ts
// 旧
enableThinking: boolean              // 删除

// 新
thinkingMode: 'off' | 'on' | 'minimal' | 'low' | 'medium' | 'high'
verbosity: 'low' | 'medium' | 'high' | null    // 仅 GPT-5 生效
```

**Profile-按钮文本映射**（由 `LLMCapabilities` 推断）：

| capabilities | thinkingMode 取值范围 | 按钮文本示例 |
|---|---|---|
| `thinking: true, adaptive_only: false`（Sonnet 4.6） | `off` / `on` | "深度思考 ●" / "深度思考"（灰态）|
| `thinking: true, adaptive_only: true`（Opus 4.7+） | `off` / `on`（on=adaptive）| "自适应思考 ●" / "自适应思考" |
| `reasoning_effort_capable: true`（GPT-5）| `off` / `minimal` / `low` / `medium` / `high` | "深度思考: 中 ▾" |
| 都不支持（DeepSeek）| `off`（强制） | "深度思考"（disabled + tooltip 解释） |

**后端透传**：

- Sonnet：`thinkingMode='on'` → `thinking_mode: 'enabled'`，附 `thinking_budget` 走默认
- Opus：`thinkingMode='on'` → `thinking_mode: 'adaptive'`，无 budget
- GPT-5：`thinkingMode='medium'` → `reasoning_effort: 'medium'`；`'off'` → `reasoning_effort: 'minimal'`（不真禁用，省 token）
- 切换 profile 时 `thinkingMode` 自动钳到新 profile 支持的范围（如从 GPT-5 'high' 切到 Sonnet → 钳到 'on'）

**布局契约**：

- 按钮宽度自适应文本（深度思考 / 自适应思考 / 深度思考: 中 三种长度）
- 弹层 popover 锚定按钮，宽度 ≥ 200px，避免遮挡输入框
- disabled 状态（streaming / pending question / profile 不支持）：tooltip 解释原因
- 移动端窄屏：按钮文本退化为图标 + 状态点

### F.4 SystemPage 运行参数页重构（Q6 + Q7 落地）

**新结构**：RuntimeParamsTab 内部分两段，不拆 Tab。

```text
RuntimeParamsTab
├─ 全局默认（Card 1）
│  ├─ temperature   Slider 0-2
│  ├─ top_p         InputNumber 0-1（null = 不发送，提示框说明）
│  ├─ stop_sequences  Tags 输入（最多 4 条）
│  ├─ rag_top_k     InputNumber 1-20
│  ├─ context_max_messages  InputNumber 4-200
│  ├─ timezone     Select
│  └─ thinking_collapse_mode  Select
│
└─ Profile 覆盖（Card 2）
   └─ 每个 profile 一行 Collapse Panel
      ├─ Header：profile.label + 当前覆盖摘要（如"verbosity: low · 自适应思考"）
      └─ Body（展开后）：
         ├─ 默认思考模式（依 capabilities 显示开关 / 四档）
         ├─ verbosity（仅 GPT-5 显示）
         └─ "其他参数（timeout / max_retries / service_tier）请编辑 config/config.yaml"  ← 只读提示
```

**Per-Profile 覆盖 schema**（前端持久化到 user settings）：

```ts
// skillStore settings 新增字段
type ProfileOverride = {
  profile_id: string;
  thinking_mode_default?: 'off' | 'on' | 'minimal' | 'low' | 'medium' | 'high';
  verbosity_default?: 'low' | 'medium' | 'high';
};
settings.profile_overrides: ProfileOverride[]  // 默认空数组
```

**布局契约**：

- 全局 Card 与 Profile Card 之间 16px 间距
- Profile Collapse 默认全部折叠，节省垂直空间
- 每个 Profile Header 右侧显示"重置为默认"按钮（清除该 profile 的覆盖）
- 字段级"未设置"语义：用占位符灰字 `未设置（继承全局）`，与 0 值区分
- 空状态：用户未配置任何 profile 覆盖时，Profile Card 显示引导文案"在这里给特定模型配不同的思考默认值"

### F.5 Profile 切换时的状态同步

输入框工具栏的"思考模式"实际值优先级：

```
当前会话 thinkingMode（用户本轮调整）
    ↑ 覆盖
profile_overrides[current_profile_id].thinking_mode_default
    ↑ 覆盖
全局默认（按 profile 能力推断：thinking 模型 = 'on'，reasoning 模型 = 'medium'）
```

切 profile 时：

- 若用户本轮在工具栏调过 thinkingMode：保留旧值钳到新能力范围（如 high → on）
- 若用户没调过：读 `profile_overrides[new_profile].thinking_mode_default`，无则按能力推默认

### F.6 失败模式（前端专属）

| ID | 失败模式 | 级别 | 验证 |
|---|---|---|---|
| F-11 | profile 切换后 thinkingMode 没钳到新能力范围 → 后端报 400 | High | 单测：mock GPT-5 'high' → 切 Sonnet → 期望 chatStore.thinkingMode === 'on'；mock Sonnet 'on' → 切 DeepSeek → 期望 'off' |
| F-12 | DeepSeek 等不支持 thinking 的 profile，按钮没 disabled | Med | 渲染测试：profile.capabilities.thinking=false && reasoning_effort_capable=false → button.disabled=true |
| F-13 | profile_overrides 字段保存到后端但 schema 不匹配 | High | 后端 settings schema 校验 + 前端读取时 fallback 到默认值 |
| F-14 | Per-Profile Collapse 全部展开时移动端 layout 溢出 | Low | 响应式测试：< 768px 时 Collapse panel 内 form item 改为单列 |
| F-15 | top_p 与 temperature 都设非默认时用户没收到风险提示 | Low | UI：top_p 输入框旁边加 InfoIcon + tooltip "建议只调一个，避免分布塌缩" |

### F.7 切片 F 内部 S/B 拆分

| 类别 | commit | 内容 |
|---|---|---|
| S-1 | `tidy: extract ThinkingModeButton component` | 把 ChatMain.tsx:917-939 的 Button 抽成独立组件，行为不变（仍然 bool） |
| S-2 | `tidy: lift settings shape` | skillStore.ts settings 字段从 flat 改成 `{global, profile_overrides}` 嵌套（后端 settings API 需配合，行为不变） |
| B-1 | `feat: thinking mode profile-aware` | ThinkingModeButton 接 capabilities，分支 Sonnet/Opus/GPT-5 三种交互；chatStore.thinkingMode 改 string union |
| B-2 | `feat: runtime params page restructure` | SystemPage RuntimeParamsTab 加 Profile Card；新增 top_p / stop_sequences / verbosity 字段 |
| B-3 | `feat: thinking mode default override` | profile_overrides 写入路径，profile 切换时按优先级钳值 |

### F.8 切片 F diff 预算

| 文件 | 行数 | 性质 |
|---|---|---|
| `frontend/src/features/chat/components/ThinkingModeButton.tsx` | +180 | 新建 |
| `frontend/src/features/chat/components/ChatMain.tsx` | -25 / +5 | 替换 |
| `frontend/src/features/chat/store/chatStore.ts` | +30 | thinkingMode 改 string union |
| `frontend/src/features/system/pages/SystemPage.tsx` | +200 | RuntimeParamsTab 重构 |
| `frontend/src/features/system/components/ProfileOverrideCard.tsx` | +150 | 新建 |
| `frontend/src/features/skills/store/skillStore.ts` | +20 | profile_overrides 字段 |
| 后端 settings schema | +10 | profile_overrides 字段透传 |
| **合计** | **~600 行** / 7 文件 | 与 A+B+C 后端切片解耦，可并行 |

### F.9 切片 F 完成标志

- [ ] 切 profile 时按钮文本和形态正确变化（Sonnet 文字 / Opus 文字 / GPT-5 下拉）
- [ ] thinkingMode 跨 profile 切换有钳值，不会传超范围值给后端
- [ ] SystemPage 全局 Card + Profile Card 渲染正确，移动端不溢出
- [ ] profile_overrides 持久化往返一致（保存 → 刷新 → 读回）
- [ ] DeepSeek profile 选中时按钮 disabled + tooltip 提示
- [ ] `npm run typecheck` + `npm run build` 通过
- [ ] F-11 / F-13 RED 测试通过

### F.10 实施顺序建议

切片 F 与后端切片 A/B 强依赖，建议时序：

```
后端 切片 A 字段（top_p / stop_sequences） ─┐
后端 切片 B 字段（thinking_mode / verbosity）─┤→ 切片 F.S-1/S-2 → F.B-1/B-2/B-3
                                            ┘
后端 切片 C（policy yaml） ── 与切片 F 解耦，独立交付
```

切片 F 不依赖切片 C；切片 C 也不依赖切片 F。**A → B → F 串行；C 可与 F 并行。**

---

## 重评估条件

以下任一发生时回头改方案：

- 增加新 provider（如本地 vLLM、Gemini） → capabilities 推断面要扩
- 启用多 worker / 横向扩展 → fallback 状态要从内存改 Redis
- 接入 OpenTelemetry（M7） → service_tier / fallback 命中要打 trace span
- Anthropic 推出 1M context GA / 新 cache TTL 选项 → 切片 A 要补字段

---

## 不变的东西

- `temperature` 默认值 0.7 不动
- `max_tokens` 默认值 8192 不动
- 现有 `enable_thinking` / `thinking_budget` kwargs 在切片 B 之前保持向后行为；切片 B 后只对 Sonnet 4.6 等"手动 thinking"模型生效，Opus 4.7+ 自动切 adaptive
- `LLMCapabilities` 自动推断机制（`infer_model_capabilities`）保留，新参数也走推断
- 全局 `PolicyEngine` 不动，仅在 profile 级加 override 入口

---

## 引用资源

- [LLM Temperature & Sampling 2026 Reference](https://sureprompts.com/blog/llm-temperature-sampling-complete-guide-2026)
- [Claude Extended Thinking docs](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- [Claude Adaptive Thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking)
- [Claude Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [GPT-5 New Params](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_new_params_and_tools)
- [How to Get Better Outputs from GPT-5](https://www.prompthub.us/blog/how-to-get-better-outputs-from-gpt-5)
- [LiteLLM Input Params](https://docs.litellm.ai/docs/completion/input)
- [LiteLLM Production Best Practices](https://docs.litellm.ai/docs/proxy/prod)
- [Portkey Failover Guide](https://portkey.ai/docs/guides/use-cases/enterprise-ready-unified-api)
