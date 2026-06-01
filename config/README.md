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

## Retrieval / RAG 配置

`retrieval` 控制向量检索行为：

```yaml
retrieval:
  collection_name: astracore       # ChromaDB collection 名称
  persist_directory: ./chroma_db   # 持久化目录；不填则使用内存模式（重启丢失）
  embedding_model: all-MiniLM-L6-v2  # sentence-transformers 模型名
```

`embedding_model` 同时用于 RAG 文档向量化和技能路由（vector 模式）。两者共享同一个模型，保证语义空间一致。

可选模型：

| 模型 | 大小 | 适用场景 |
|------|------|---------|
| `all-MiniLM-L6-v2`（默认） | ~90MB | 英文 |
| `paraphrase-multilingual-MiniLM-L12-v2` | ~420MB | 中文 / 多语言 |

> ⚠️ 切换模型后，已有的向量数据与新模型不兼容，需要清空 ChromaDB 并重新索引：
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

- `filesystem`：通过 MCP 文件系统服务访问允许目录。
- `shell`：使用内置受控 shell server，在允许目录内执行命令。
- `custom`：自定义外部 MCP server，需要配置 `name`、`command`、`args`、`env`。

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
