# /init 工作流

用于初始化项目上下文，生成 `.claude/astra/PROJECT.md`。新项目首次使用或项目结构变化后执行。

---

## 执行步骤

### 步骤 1：确保目录存在

检查 `.claude/astra/` 目录是否存在：
- 存在 → 继续
- 不存在 → 创建 `.claude/astra/` 和 `.claude/astra/plans/` 和 `.claude/astra/explore/`

### 步骤 2：探测项目根目录

列出项目根目录的一层文件结构（`ls -la` 或等价工具），识别项目类型信号：

| 文件 | 信号 |
|---|---|
| `Makefile` | 有构建脚本，优先读取 |
| `package.json` | Node.js / JS / TS 项目 |
| `pyproject.toml` / `setup.py` | Python 项目 |
| `requirements.txt` | Python 依赖 |
| `go.mod` | Go 项目 |
| `Cargo.toml` | Rust 项目 |
| `pom.xml` / `build.gradle` | Java 项目 |
| `docker-compose.yml` | 有容器化部署 |

### 步骤 3：读取配置文件（按优先级）

按以下顺序读取，每份文件提取对应字段：

1. **`Makefile`** → 提取所有 target 名称和注释，识别启动/测试/lint/格式化命令
2. **`package.json`** → 提取 `name`、`scripts`、主要 `dependencies`
3. **`pyproject.toml`** / **`setup.py`** → 提取项目名、Python 版本、依赖
4. **`README.md`** → 提取架构概述（前 60 行）
5. **`CLAUDE.md`** / **`.claude/CLAUDE.md`**（如存在）→ 提取已有约定，避免重复记录

### 步骤 4：向用户展示推断结果并确认

展示以下内容，逐字段请用户确认或修正：

```
检测到的项目信息：
- 项目名称: [推断值]
- 项目路径: [当前目录]
- 技术栈: [推断值]
- 启动命令: [推断值]
- 测试命令: [推断值]
- Lint 命令: [推断值]

是否正确？请直接回复确认，或指出需要修改的字段。
```

### 步骤 5：写入 PROJECT.md

确认后，按以下模板写入 `.claude/astra/PROJECT.md`：

---

## PROJECT.md 模板

```markdown
# 项目上下文

> 由 /init 生成。手动修改后在字段末尾加 `[已手动修改]` 标记。
> 最后更新: YYYY-MM-DD

## 基本信息

- **项目名称**: 
- **项目路径**: 
- **技术栈**: 
- **包管理器**: 

## 命令

| 用途 | 命令 |
|---|---|
| 启动服务 | `` |
| 运行测试 | `` |
| Lint / 类型检查 | `` |
| 格式化 | `` |
| 构建 | `` |

## 架构概述

[3 句话内：项目做什么 / 主要模块 / 核心数据流]

## 重要约定

[从 README / CLAUDE.md / 代码注释中提取的非显而易见约定]

- 
```

---

## 检查门

- PROJECT.md **已存在**时：展示现有内容，询问"是否重新扫描覆盖，或只更新某些字段？"，不得直接覆盖
- 配置文件**均不存在**时：直接向用户收集信息，手动填写模板
- 用户**提供了项目路径参数**（如 `/init /path/to/project`）：切换到该目录后执行
