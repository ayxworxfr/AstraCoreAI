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
  provider: anthropic
  base_url: https://api.anthropic.com
  api_key_env: ANTHROPIC_API_KEY
  model: claude-sonnet-4-6
  max_tokens: 8192
```

字段说明：

- `id`：稳定的 profile 标识，前端下拉和聊天请求使用它。
- `label`：前端展示名称。
- `provider`：适配器类型，目前支持 `anthropic` 和 `deepseek`。其中 `deepseek` 走 OpenAI 兼容接口。
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

当前内置策略会根据 `provider`、`model` 和 `base_url` 共同判断。例如：

- Claude 系列默认支持工具调用，按模型差异决定是否发送 `thinking` 和 `temperature`。
- DeepSeek OpenAI 兼容接口默认不发送 Anthropic thinking 参数。
- DeepSeek Anthropic 兼容接口可通过 `anthropic_blocks` 控制历史 block 回放，避免代理协议不兼容。

如遇到代理或新模型能力与内置表不一致，可以在对应 profile 中手动覆盖：

```yaml
capabilities:
  thinking: false
  temperature: false
```

只需要写需要覆盖的字段，未写字段会继续使用内置推导值。

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
- `label` 用于下拉展示；未配置时使用 `id/provider/model` 兜底。

因此新增或删除模型只需要改 YAML 并重启后端，前端会自动跟随。
