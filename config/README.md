# 配置目录说明

本目录存放 AstraCoreAI 的结构化配置。`.env` 仍放在项目根目录，只用于保存密钥和少量环境差异变量。

## 文件说明

- `config.yaml`：本地开发实际使用的配置文件。
- `config.example.yaml`：示例配置，可复制后按需修改。
- `config.docker.yaml`：Docker 部署使用的配置文件。

默认读取路径是 `config/config.yaml`。如需切换配置文件，可在根目录 `.env` 中设置：

```env
ASTRACORE_CONFIG=config/config.local.yaml
```

## LLM Profiles

`llm.profiles` 中每一项代表一个可选择的模型 profile。常规情况下只需要填写连接信息：

```yaml
- id: claude-sonnet
  label: Claude Sonnet
  protocol: anthropic
  base_url: https://api.anthropic.com
  api_key_env: ANTHROPIC_API_KEY
  model: claude-sonnet-4-6
  max_tokens: 8192
```

字段说明：

- `id`：稳定的 profile 标识，前端下拉和聊天请求使用它。
- `label`：前端展示名称。
- `protocol`：接口协议，目前支持 `anthropic`、`openai`、`responses`。
- `base_url`：模型服务地址。
- `api_key_env`：密钥环境变量名，真实密钥写在根目录 `.env`。
- `model`：传给上游服务的真实模型名。
- `max_tokens`：单次响应最大 token 数。

### 采样参数（可选）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `top_p` | float \| null | null | 核采样概率截断（0.0–1.0）。与 `temperature` 二选一调整；null = 不发送，使用 provider 默认值。 |
| `stop_sequences` | list[str] | [] | 强终止序列，最多 4 条。遇到列表中任意字符串时强制停止输出。OpenAI 和 Anthropic 均支持。 |
| `enable_prompt_cache` | bool | true | 仅 `protocol: anthropic` 且 `capabilities.prompt_cache=true` 时生效。为**静态** system / 末 tool / messages 前缀注入 `cache_control: ephemeral`；动态内容走 `SessionContext`（datetime/RAG/工具进度），不进缓存前缀。OpenAI/Responses 走自动前缀缓存 + `prompt_cache_key`，不读此开关。 |

### 推理控制（可选）

| 字段 | 协议 | 默认值 | 说明 |
|------|------|--------|------|
| `thinking_mode` | anthropic | null | 思考模式：`'off'` 禁用 / `'on'` 启用扩展思考 / `'adaptive'` 自适应（Opus 4.7+ 支持，无需 budget_tokens）。null = 由 capabilities 推断。 |
| `thinking_budget` | anthropic | 8000 | `thinking_mode: on` 时的 token 预算（≥1000）。adaptive 模式忽略此字段。 |
| `reasoning_effort` | responses | null | GPT-5 Responses API 推理深度：`'minimal'` \| `'low'` \| `'medium'` \| `'high'`。null = 不发送，provider 默认 `medium`。 |
| `verbosity` | responses | null | GPT-5 回答长度控制：`'low'` \| `'medium'` \| `'high'`。null = 不发送，provider 默认 `medium`。 |

### 运维覆盖（可选）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `timeout_s` | float \| null | null | 覆盖全局 `policy.timeout.llm_timeout_s`（秒）。仅对该 profile 生效，不影响其他 profile。 |
| `max_retries` | int \| null | null | 覆盖全局 `policy.retry.max_retries`。仅对该 profile 生效。 |
| `service_tier` | str \| null | null | Anthropic：`'priority'` \| `'standard'` \| `'batch'`。OpenAI / Responses：`'auto'` \| `'default'` \| `'flex'`。null = 不发送，使用 provider 默认值。 |

## 模型能力

模型能力由 `src/astracore/sdk/model_capabilities.py` 的内置表自动推导，通常不需要在 YAML 中手写：

- `tools`：是否支持工具调用。
- `thinking`：是否支持深度思考参数。
- `temperature`：是否支持 `temperature` 参数。
- `anthropic_blocks`：是否回放 Anthropic 原始 `text/tool_use` blocks。

当前内置策略会根据 `protocol`、`model` 和 `base_url` 共同判断。例如：

- Claude 系列默认支持工具调用，按模型差异决定是否发送 `thinking` 和 `temperature`。
- OpenAI 兼容接口默认不发送 Anthropic thinking 参数。
- DeepSeek Anthropic 兼容接口可通过 `anthropic_blocks` 控制历史 block 回放，避免代理协议不兼容。

如遇到代理或新模型能力与内置表不一致，可以在对应 profile 中手动覆盖：

```yaml
capabilities:
  thinking: false
  temperature: false
```

只需要写需要覆盖的字段，未写字段会继续使用内置推导值。

## Storage 配置（数据库 / 缓存 / 向量库）

`storage` 统一管理三类持久化层：

```yaml
storage:
  db_url: sqlite+aiosqlite:///./astracore.db   # 全局 SQLite/PostgreSQL 连接串
  redis_url: redis://localhost:6379/0           # Redis 连接串（记忆缓存、调度队列）
  vector:
    collection_name: astracore       # ChromaDB collection 名称
    persist_directory: ./chroma_db   # 持久化目录；不填则使用内存模式（重启丢失）
    embedding_model: all-MiniLM-L6-v2  # Chroma ONNX 默认模型
```

`vector.embedding_model` 同时用于 RAG 文档向量化和记忆向量检索。当前默认镜像仅支持 Chroma ONNX `all-MiniLM-L6-v2`，避免拉取 PyTorch/CUDA 依赖。

内置模型：

| 模型 | 大小 | 适用场景 |
|------|------|---------|
| `all-MiniLM-L6-v2`（默认） | ~90MB | 通用轻量语义检索 |

> ⚠️ 切换 `vector.embedding_model` 后，已有的向量数据与新模型不兼容，需要清空 ChromaDB 并重新索引：
> ```bash
> make clean-rag
> ```

## Skills 目录扩展

`skills.extra_dirs` 支持在内置 `service/skills/` 之外扫描额外的 skill 目录：

```yaml
skills:
  extra_dirs:
    - D:/my-skills          # 绝对路径
    - ~/shared-skills       # 支持 ~ 展开
```

- 每个目录下的 `.md` 文件按相同规则解析（frontmatter `name/description/order/default` + 正文 system_prompt）
- 同名 `source_key`（文件名 stem）时，后配置的目录覆盖先配置的目录，并输出警告日志
- 可用于引入团队共享 skill 库或业务专属 skill，无需修改内置目录

## MCP 配置

`mcp.servers` 配置 Agent 可用的 MCP 工具服务器：

```yaml
mcp:
  servers:
    - type: filesystem
      paths:
        - D:/project

    - type: shell
      allow_dirs:
        - D:/project
      timeout: 30
```

支持类型：

- `filesystem`：内置 Python filesystem server（`mcp_servers/filesystem_server.py`），无需 Node.js；提供 `read_file`、`read_multiple_files`、`write_file`、`edit_file`、`list_directory`、`create_directory`、`move_file`、`delete_file`、`search_files`、`get_file_info` 共 10 个工具；`paths` 列表限定可访问目录。
- `shell`：使用内置受控 shell server，在允许目录内执行命令。
- `custom`：自定义外部 MCP server，需要配置 `name`、`command`、`args`、`env`。

## Policy 配置（全局重试 / 超时 / 上下文压缩）

`policy` 控制 LLM 调用的重试策略、超时阈值和历史压缩参数。所有字段均有内置默认值，不填时直接生效：

```yaml
policy:
  retry:
    max_retries: 3                              # 总重试次数（不含首次）
    retry_on_status_codes: [429, 500, 502, 503, 504]
    # initial_delay_ms: 1000                   # 首次重试等待（毫秒）
    # max_delay_ms: 30000                      # 指数退避上限（毫秒）
    # exponential_base: 2.0                    # 退避底数
  timeout:
    llm_timeout_s: 180                         # LLM 调用超时（秒）
    tool_timeout_s: 120                        # 工具执行超时（秒，0 = 不限制）
    retrieval_timeout_s: 10                    # RAG 检索超时（秒）
  compaction:
    context_window_tokens: 200000              # 估算的上下文窗口
    trigger_ratio: 0.5                         # 估算 token 超过 window*ratio 时压缩
    compact_batch_ratio: 0.6                   # 单次压缩最旧 60% 消息
    chars_per_token: 0.6                       # 字符到 token 的近似换算（中英混合）
    default_max_messages: 10                   # user_settings.context_max_messages 兜底
```

字段说明：

- `retry.max_retries`：失败后最多重试的次数（不含首次）。重试仅在 `retry_on_status_codes` 命中时触发，使用指数退避。
- `retry.retry_on_status_codes`：触发重试的 HTTP 状态码列表。429（限流）和 5xx（服务端错误）是最常见场景。
- `timeout.llm_timeout_s`：单次 LLM 流式调用的最长等待时间。Claude 长输出场景（reasoning / 长文）建议调高到 `240`（4 分钟）。
- `timeout.tool_timeout_s`：单次工具执行的最长等待时间，`0` 表示不限制。
- `timeout.retrieval_timeout_s`：RAG 向量检索超时。
- `compaction.context_window_tokens`：估算的上下文窗口 token 数（Claude Sonnet/Opus 实际为 200k，按 100k 触发以预留输出空间）。
- `compaction.trigger_ratio`：估算 token 超过 `context_window_tokens * trigger_ratio` 时触发摘要压缩。
- `compaction.compact_batch_ratio`：单次压缩最旧 N% 的非 system 消息为一段 LLM 摘要。
- `compaction.chars_per_token`：字符数到 token 的近似换算系数，中英混合保守值 0.6。
- `compaction.default_max_messages`：LLM 压缩失败回退尾部裁剪时的兜底消息数；当用户设置了 `context_max_messages` 时以用户值为准。

**Per-Profile 覆盖**：在 `llm.profiles[]` 中设置 `timeout_s` / `max_retries` 可以只覆盖该 profile 的值，不影响其他 profile：

```yaml
profiles:
  - id: claude-opus
    timeout_s: 240        # 只有 Opus 放宽到 4 分钟
    max_retries: 5        # Opus priority tier 多重试几次
```

## 认证配置（auth）

`auth` 控制 JWT 认证行为：

```yaml
auth:
  secret_key: change-me-in-production   # 必须替换为强随机字符串（生产环境）
  token_expire_days: 30                 # JWT token 有效期（天）
  allow_registration: true              # false 时关闭 /register 接口
```

- 首个注册用户自动成为 `admin`，后续用户默认为 `user` 角色。
- 完成初始用户创建后建议将 `allow_registration` 改为 `false`，防止未授权注册。
- `secret_key` 不要写进 YAML 明文，推荐通过环境变量覆盖：

```env
ASTRACORE__AUTH__SECRET_KEY=your-strong-random-secret
```

## HITL 配置（Human-in-the-Loop）

`hitl` 控制人机协作的审批行为：

```yaml
hitl:
  enabled: true                            # 总开关；false 时禁用所有 HITL 交互
  inline_question_timeout: 300             # 等待用户回复的超时秒数，超时后自动继续
  require_tool_approval: true              # true 时带 requires_confirmation 标记的工具暂停等待审批
  require_memory_promotion_approval: true  # true 时记忆晋升（session→user/project）需用户确认
```

- `require_tool_approval`：目前 `delete_memory` 已标记为需审批。工具调用前前端会弹出 `QuestionCard` 等待用户选择"允许/拒绝"。
- `require_memory_promotion_approval`：AI 判断某条 session 记忆值得长期保留时，会先创建 pending 状态等待用户在记忆管理页确认，而非直接晋升。
- `inline_question_timeout`：仅对 `ask_user` 工具的主动询问生效；工具审批和记忆晋升使用前端异步确认，不受此超时约束。

## 网络搜索配置（web_search）

`web_search` 控制 `web_search` 内置工具使用的搜索 provider：

```yaml
web_search:
  provider: duckduckgo           # tavily | searxng | duckduckgo
  tavily:
    api_key_env: TAVILY_API_KEY
  searxng:
    base_url: http://localhost:8080
    engines: ""
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | str | `duckduckgo` | 激活的搜索 provider，三选一 |
| `tavily.api_key_env` | str | `TAVILY_API_KEY` | Tavily API key 所在的环境变量名，真实 key 写在 `.env` |
| `searxng.base_url` | str | `http://localhost:8080` | SearXNG 实例地址（自托管或公共实例） |
| `searxng.engines` | str | `""` | 传给 SearXNG 的引擎列表（逗号分隔），留空使用实例默认配置 |

**Provider 选择建议：**

- `duckduckgo`：无需任何 key，开箱即用，搜索质量一般。
- `tavily`：需 `TAVILY_API_KEY`，AI 优化结果，质量最高。
- `searxng`：需自托管或使用公共实例，聚合 70+ 搜索引擎，质量好，完全免费。

配置的 provider 发生错误时快速失败，直接返回错误消息，不自动切换到其他 provider。

## 调试配置（debug）

```yaml
debug:
  log_prompts: false   # true 时在每次 LLM 调用前把完整提示词打印到 stdout
```

`log_prompts` 开启后会在终端输出完整的 system prompt 和消息列表（含 Tier-1/Tier-2 记忆注入内容），方便排查提示词组装问题。**不要在生产环境开启**，会泄露用户数据。

## 密钥管理

不要把真实密钥写进 YAML。推荐在根目录 `.env` 中保存：

```env
ANTHROPIC_API_KEY=sk-ant-xxx
DEEPSEEK_API_KEY=sk-xxx
ANTHROPIC_PROXY_API_KEY=app-key-xxx
```

YAML 中通过 `api_key_env` 引用这些变量。

## 前端模型选择

前端不会直接维护模型列表，而是读取后端 `GET /api/v1/system/` 返回的 `llm.profiles`：

- `default_profile` 决定默认选中项。
- 每个 profile 的 `id` 会作为聊天请求中的 `model_profile`。
- `label` 用于下拉展示；未配置时使用 `id/protocol/model` 兜底。

因此新增或删除模型只需要改 YAML 并重启后端，前端会自动跟随。
