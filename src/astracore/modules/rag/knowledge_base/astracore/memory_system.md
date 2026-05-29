---
title: AstraCoreAI 记忆系统（Memory）
category: astracore
tags: [Memory, Redis, SQLite, 记忆抽取, 长期记忆, 三层存储]
related: [astracore/intro, astracore/chat_pipeline]
---

# AstraCoreAI 记忆系统（Memory）

AstraCoreAI 的记忆系统能在对话过程中自动抽取关键信息并持久化，让 AI 具备跨会话的长期记忆能力。

## 三层存储架构

| 层级 | 存储 | 用途 | 生命周期 |
|------|------|------|---------|
| 短期 | Redis | 会话消息缓存，加速读取 | 会话期间 |
| 中期 | SQLite | 会话历史持久化，重启恢复 | 永久 |
| 长期 | StructuredMemoryRow | LLM 抽取的结构化记忆 | 永久（可设过期）|

Redis 不可用时自动降级到 SQLite，服务不中断。

## 记忆类型

| 类型 | 说明 |
|------|------|
| `fact` | 客观事实 |
| `preference` | 用户偏好 |
| `decision` | 已做决策 |
| `constraint` | 约束条件 |
| `state` | 当前状态 |
| `plan` | 计划安排 |
| `summary` | 总结摘要 |
| `lesson` | 经验教训 |

## 记忆范围

| 范围 | 说明 |
|------|------|
| `session` | 本次会话内 |
| `project` | 项目范围（跨会话） |
| `user` | 用户级别（跨项目） |
| `global` | 全局共享 |

## 自动抽取流程

每轮对话结束后，`MemoryEngine` 会：

1. 将近期对话发送给 LLM
2. LLM 以结构化格式（`_MemoryDecision`）返回需要记录的信息
3. 去重、更新已有记忆、写入持久化存储
4. 超过容量阈值时自动紧凑化（合并旧记忆）

## 记忆注入

会话开始时，相关记忆自动注入系统提示，让 AI 具备上下文感知能力。

## API 访问

```
GET  /api/v1/memory/          查询记忆列表
POST /api/v1/memory/          写入记忆
DELETE /api/v1/memory/{id}    删除记忆
```
