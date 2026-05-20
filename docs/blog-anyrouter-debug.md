# Claude Code 能跑，curl 为什么不行：一次 AnyRouter 调试复盘

这次问题很典型：一个服务声称“兼容 Anthropic API”，但普通 curl 调不通；同样的 key 和 base URL，Claude Code 却能正常使用 `Opus 4.7 (1M context)`。

最后发现，真正的差异不是模型名，也不是 key，而是 **Claude Code 发出的请求并不是普通 Anthropic Messages 请求**。它带了一组 Claude Code 专用 beta header、1M context 配置、adaptive thinking、context management，以及一套带缓存标记的 system body。

这篇文章记录的是排查过程。重点不是给出某个工程的接入方案，而是：遇到这种黑盒代理服务时，怎么从现象一步步拆出真相。

## 先说结论

普通 Anthropic Messages 请求长这样：

```bash
curl -sS https://anyrouter.top/v1/messages \
  -H "Authorization: Bearer $ANTHROPIC_PROXY_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-opus-4-7",
    "max_tokens": 128,
    "messages": [
      {"role": "user", "content": "hi"}
    ]
  }'
```

它会失败。

Claude Code 能成功，是因为它实际发出的请求更接近这样：

```text
POST /v1/messages?beta=true
```

并且带上：

```http
anthropic-beta: claude-code-20250219,interleaved-thinking-2025-05-14,context-management-2025-06-27,prompt-caching-scope-2026-01-05,context-1m-2025-08-07,effort-2025-11-24
anthropic-dangerous-direct-browser-access: true
x-app: cli
user-agent: claude-cli/2.1.145 (external, sdk-cli)
```

body 里还有这些字段：

```json
{
  "model": "claude-opus-4-7",
  "max_tokens": 64000,
  "stream": true,
  "thinking": {"type": "adaptive"},
  "context_management": {
    "edits": [
      {"type": "clear_thinking_20251015", "keep": "all"}
    ]
  },
  "output_config": {"effort": "xhigh"}
}
```

更麻烦的是，简化这些字段以后仍然会 `503`。最终只有“Claude Code 原始 body + 替换用户问题”稳定可用。

## 现象

本地 Claude Code 这样启动：

```bash
export ANTHROPIC_AUTH_TOKEN=sk-xxx
export ANTHROPIC_BASE_URL=https://anyrouter.top
claude
```

界面显示：

```text
Opus 4.7 (1M context)
```

交互也能正常返回。

但手写 curl 调用 `claude-opus-4-7` 时，先遇到的是：

```json
{"error":"1m 上下文已经全量可用，请启用 1m 上下文后重试","type":"error"}
```

于是第一反应是：是不是少了 1M context beta？

## 第一个突破：错误从 400 变成 503

加上：

```http
anthropic-beta: context-1m-2025-08-07
```

错误确实变了。

原来是：

```text
请启用 1m 上下文
```

后来变成：

```json
{"error":{"message":"Service Unavailable","type":"error"},"type":"error"}
```

这个变化很有用。它说明：

- `context-1m-2025-08-07` 是必要条件。
- 但它不是充分条件。
- 请求已经进入了另一条服务端路径，只是那条路径仍然没被满足。

也就是说，问题不是“模型不存在”，而是“请求形态还不像 Claude Code”。

## 第二个突破：让 Claude Code 非交互跑起来

Claude Code 支持 `-p` 非交互模式，可以用来做对照实验：

```bash
ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_PROXY_API_KEY" \
ANTHROPIC_BASE_URL="https://anyrouter.top" \
claude -p --model opus --tools "" --betas context-1m-2025-08-07 -- "hi"
```

这个命令能成功返回：

```text
Hi! How can I help you today?
```

所以接下来要回答的问题就很明确了：

**Claude Code 到底发了什么，而我们的 curl 没发？**

## debug 日志还不够

Claude Code 有 `--debug-file`：

```bash
claude -p \
  --model opus \
  --tools "" \
  --betas context-1m-2025-08-07 \
  --debug api \
  --debug-file /tmp/claude-debug.log \
  -- "hi"
```

debug 里可以看到：

```text
[API REQUEST] /v1/messages source=sdk
model=claude-opus-4-7
```

但它不会把完整 headers 和 body 打出来。

这时继续猜 header 就很低效了。最好的办法是把 Claude Code 的请求抓下来。

## 用本地 HTTPS 服务抓包

这里没有用 mitmproxy，也没有做传统中间人代理。方法更简单：让 Claude Code 直接把 API 请求发到我们自己的本地 HTTPS server。

先生成一个临时自签证书：

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout /tmp/cc-capture-key.pem \
  -out /tmp/cc-capture-cert.pem \
  -subj "/CN=127.0.0.1" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost" \
  -days 1
```

然后起一个极简 HTTPS server：

```python
from http.server import BaseHTTPRequestHandler, HTTPServer
import ssl

captured = []

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        captured.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
        self.send_response(500)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(
            b'{"type":"error","error":{"type":"server_error","message":"captured"}}'
        )

    def log_message(self, *args):
        pass

server = HTTPServer(("127.0.0.1", 0), Handler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain("/tmp/cc-capture-cert.pem", "/tmp/cc-capture-key.pem")
server.socket = ctx.wrap_socket(server.socket, server_side=True)
print(server.server_port)
server.handle_request()
```

再把 Claude Code 指到这个本地服务：

```bash
ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_PROXY_API_KEY" \
ANTHROPIC_BASE_URL="https://127.0.0.1:<port>" \
NODE_TLS_REJECT_UNAUTHORIZED=0 \
BUN_TLS_REJECT_UNAUTHORIZED=0 \
claude -p --model opus --tools "" --betas context-1m-2025-08-07 -- "hi"
```

这里有几个坑：

- 必须是 HTTPS。直接用 `http://127.0.0.1:<port>` 时，Claude Code 没有把 API 请求打到本地服务。
- 自签证书需要临时关闭 TLS 校验，所以设置 `NODE_TLS_REJECT_UNAUTHORIZED=0` 和 `BUN_TLS_REJECT_UNAUTHORIZED=0`。
- `--betas` 后面要用 `-- "hi"` 分隔 prompt，否则 `hi` 会被当成 beta 名称。
- 输出前要把 token 替换成 `[redacted]`，不然很容易把 key 写进日志。

这个办法的好处是：不猜。直接拿到 Claude Code 实际发出的 HTTP 请求。

## 抓到的真实请求

抓到后才发现，Claude Code 请求的是：

```text
POST /v1/messages?beta=true
```

关键 headers：

```http
Authorization: Bearer sk-xxx
anthropic-version: 2023-06-01
anthropic-dangerous-direct-browser-access: true
x-app: cli
user-agent: claude-cli/2.1.145 (external, sdk-cli)
anthropic-beta: claude-code-20250219,interleaved-thinking-2025-05-14,context-management-2025-06-27,prompt-caching-scope-2026-01-05,context-1m-2025-08-07,effort-2025-11-24
```

顶层 body 字段：

```text
model
messages
system
tools
metadata
max_tokens
thinking
context_management
output_config
stream
```

其中几个非常不像普通 Anthropic Messages 请求：

```json
{
  "max_tokens": 64000,
  "stream": true,
  "thinking": {"type": "adaptive"},
  "context_management": {
    "edits": [
      {"type": "clear_thinking_20251015", "keep": "all"}
    ]
  },
  "output_config": {"effort": "xhigh"}
}
```

另外，`system` 不是一个普通字符串，而是一个 block 列表；里面还有 Claude Code 自己的长 system prompt 和 `cache_control`。

## replay 实验

把 Claude Code 原始 body 保存成：

```text
/tmp/cc-body.json
```

只替换最后一个用户输入：

```python
import json
from pathlib import Path

body = json.loads(Path("/tmp/cc-body.json").read_text(encoding="utf-8"))
body["messages"][0]["content"][-1]["text"] = "你是谁？"
Path("/tmp/cc-body-who.json").write_text(
    json.dumps(body, ensure_ascii=False),
    encoding="utf-8",
)
```

再发出去：

```bash
curl -sS -N "https://anyrouter.top/v1/messages?beta=true" \
  -H "Authorization: Bearer $ANTHROPIC_PROXY_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: claude-code-20250219,interleaved-thinking-2025-05-14,context-management-2025-06-27,prompt-caching-scope-2026-01-05,context-1m-2025-08-07,effort-2025-11-24" \
  -H "anthropic-dangerous-direct-browser-access: true" \
  -H "x-app: cli" \
  -H "user-agent: claude-cli/2.1.145 (external, sdk-cli)" \
  -H "content-type: application/json" \
  --data-binary @/tmp/cc-body-who.json
```

这次成功返回 SSE：

```text
event: message_start
event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"我"}}
...
```

这说明抓包方向是对的：**只要请求足够像 Claude Code，它就能通。**

## 最小化为什么失败

接下来尝试把请求一点点变小：

- 只保留 `context-1m-2025-08-07`
- 加完整 `anthropic-beta`
- 加 `/v1/messages?beta=true`
- 加 `stream: true`
- 加 `thinking: adaptive`
- 加 `context_management`
- 加 `output_config.effort`
- 加 Claude Code headers
- 构造一个很长的合成 system prompt

这些都还是 `503`。

最后只有“原始 Claude Code body + 替换用户问题”稳定可用。

这说明 AnyRouter 的这条路由可能不是简单校验几个字段，而是对 Claude Code 风格请求有更细的识别条件。也可能是某些 system/cache 结构刚好触发了正确的上游路由。

## 这次学到什么

第一，**兼容 API 不等于完全同一个协议**。模型列表里有 `claude-opus-4-7`，不代表它能被普通 Anthropic Messages body 调通。

第二，**错误变化比错误内容更重要**。从“请启用 1m context”变成 `503`，说明我们确实触发了不同路径。

第三，**debug 日志不够时就抓真实请求**。与其猜 header，不如让 SDK 请求打到自己的本地 HTTPS server。

第四，**不要过早抽象结论**。一开始以为只是 `anthropic-beta`，后来发现还涉及 query string、多个 beta、thinking、context management、output config、system block 和 cache control。

## 最后的判断

Claude Code 能用 `Opus 4.7 (1M context)`，不是因为 AnyRouter 对外暴露了一个普通 Anthropic Messages API，而是因为 Claude Code 发了一种更复杂的、带 SDK 特征的请求。

所以，如果只是想验证服务是否可用，最可靠的方式是：

```bash
ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_PROXY_API_KEY" \
ANTHROPIC_BASE_URL="https://anyrouter.top" \
claude -p --model opus --tools "" --betas context-1m-2025-08-07 -- "hi"
```

如果想用 curl 复现，就需要先抓 Claude Code 的 body，再基于原始 body 替换用户输入。这个结果不优雅，但它是真实的。
