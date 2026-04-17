# AstraCoreAI Frontend

前端子工程，基于 `React + Vite + TypeScript`，用于提供会话式 Chat、RAG 检索调试和系统状态检查页面。

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
    pages/        # Chat / RAG / System 页面
    components/   # 页面组件
    services/     # API 与 SSE 请求封装
    stores/       # Zustand 状态管理
    types/        # 类型定义
    styles/       # 全局样式
```

## 6. 说明

- 前端默认通过 Vite 代理访问后端：
  - `/api` -> `http://127.0.0.1:8000`
  - `/health` -> `http://127.0.0.1:8000`
- 会话元数据与消息内容会保存在浏览器 `localStorage` 中。
