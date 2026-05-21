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
    app/          # App 根组件、路由、主题
    features/     # 按产品能力组织页面、组件、状态、服务和类型
      chat/
        components/
        pages/
        services/
        store/
        types.ts
      memory/
      projects/
      rag/
      settings/
      skills/
      system/
    layouts/      # 跨页面布局
    shared/       # 跨 feature 复用组件、API 基础设施、类型和工具函数
      components/
      services/
      types/
      utils/
    global.css    # 全局样式
```

## 6. 说明

- 新功能优先放到 `src/features/<capability>/` 下，不再新增顶层 `pages/`、`components/`、`services/`、`stores/`、`types/` 目录。
- 只有多个 feature 复用的代码才放到 `src/shared/`；feature 私有组件和 API 调用不要提前抽到 shared。
- 跨 feature import 优先使用 `@/` 别名，例如 `@/features/chat/store/chatStore`。
- 前端默认通过 Vite 代理访问后端：
  - `/api` -> `http://127.0.0.1:8000`
  - `/health` -> `http://127.0.0.1:8000`
- Chat 页会从 `GET /api/v1/system/` 读取后端 `llm.profiles`，当存在多个模型 Profile 时展示模型下拉。
- 发送对话时使用 `model_profile` 字段选择后端 YAML 中的 profile，不直接传上游模型名。
- 会话元数据、消息内容、激活 Skill、模型 Profile 与主题设置会保存在浏览器 `localStorage` 中。
- 短期对话记忆同时持久化到后端 SQLite，重启后端后记忆不丢失。
- Chat 主区域使用主题适配的自定义滚动条，支持拖动和点击轨道平滑跳转。
- System 页包含系统状态、LLM 信息、运行参数三类视图，可查看 profile 能力推导结果与 API key 配置状态。
