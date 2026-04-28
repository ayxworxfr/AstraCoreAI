# AstraCoreAI Frontend

前端子工程，基于 `React + Vite + TypeScript`，提供会话式 Chat、模型 Profile 切换、Skill 管理、RAG 检索调试和系统配置页面。

## 1. 环境要求

- Node.js >= 18
- npm >= 9

## 2. 安装依赖

在项目根目录执行：

```bash
make fe-install
```

或在当前目录执行：

```bash
npm install
```

## 3. 启动开发服务

先确保后端已启动（根目录执行 `make api`），然后启动前端：

```bash
make fe-dev
```

默认访问地址：

- [http://127.0.0.1:5173](http://127.0.0.1:5173)

## 4. 构建与预览

```bash
make fe-build
make fe-preview
```

## 5. 目录结构

```text
frontend/
  src/
    app/          # 应用入口与路由
    pages/        # Chat / RAG / Skills / System 页面
    components/   # 页面组件（chat / rag / skills / system）
    services/     # API、SSE、Skill、系统信息与运行参数请求封装
    stores/       # Zustand（chatStore / skillStore / settingsStore）
    types/        # 类型定义
    styles/       # 全局样式
```

## 6. 说明

- 前端默认通过 Vite 代理访问后端：
  - `/api` -> `http://127.0.0.1:8000`
  - `/health` -> `http://127.0.0.1:8000`
- Chat 页会从 `GET /api/v1/system/` 读取后端 `llm.profiles`，当存在多个模型 Profile 时展示模型下拉。
- 发送对话时使用 `model_profile` 字段选择后端 YAML 中的 profile，不直接传上游模型名。
- 会话元数据、消息内容、激活 Skill、模型 Profile 与主题设置会保存在浏览器 `localStorage` 中。
- 短期对话记忆同时持久化到后端 SQLite，重启后端后记忆不丢失。
- Chat 主区域使用主题适配的自定义滚动条，支持拖动和点击轨道平滑跳转。
- System 页包含系统状态、LLM 信息、运行参数三类视图，可查看 profile 能力推导结果与 API key 配置状态。
