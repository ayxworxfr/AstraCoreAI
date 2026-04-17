# ============================================================
# AstraCoreAI Makefile
# Usage: make <command>
# ============================================================

.PHONY: help setup install deps rag-deps dev api sdk-chat fe-install fe-dev fe-build fe-preview test test-cov lint type-check check fmt clean clean-rag clean-old-hatch

.DEFAULT_GOAL := help

GREEN  := \033[0;32m
YELLOW := \033[0;33m
CYAN   := \033[0;36m
NC     := \033[0m

PYTHON := python3
HATCH_ENV_VARS := HATCH_DATA_DIR="$(CURDIR)/.hatch/data" HATCH_CACHE_DIR="$(CURDIR)/.hatch/cache" HATCH_ENV_TYPE_VIRTUAL_PATH="$(CURDIR)/.hatch/venvs" PIP_CACHE_DIR="$(CURDIR)/.cache/pip"
HATCH  := $(HATCH_ENV_VARS) $(PYTHON) -m hatch
ENV_RUN := set -a; [ -f .env ] && . ./.env; set +a;

##@ 帮助信息

help: ## 显示此帮助信息
	@echo "$(CYAN)AstraCoreAI$(NC) - Python AI Framework"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "$(YELLOW)用法:$(NC)\n  make $(GREEN)<target>$(NC)\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  $(GREEN)%-12s$(NC) %s\n", $$1, $$2 } /^##@/ { printf "\n$(YELLOW)%s$(NC)\n", substr($$0, 5) } ' $(MAKEFILE_LIST)
	@echo ""
	@echo "$(CYAN)快速开始:$(NC)"
	@echo "  1. make setup         # 安装项目依赖"
	@echo "  2. 配置 .env"
	@echo "  3. make api           # 启动 FastAPI 服务"
	@echo ""

##@ 环境准备

install: ## 安装 Hatch（如果未安装）
	@echo "$(GREEN)📦 检查并安装 Hatch...$(NC)"
	@$(PYTHON) -m pip install --user hatch
	@echo "$(GREEN)✅ Hatch 准备完成$(NC)"

deps: ## 安装项目依赖（默认不含重型向量模型包）
	@echo "$(GREEN)📦 安装项目依赖...$(NC)"
	@$(HATCH) run pip install -e ".[anthropic,openai,dev]"
	@echo "$(GREEN)✅ 依赖安装完成$(NC)"

rag-deps: ## 安装 RAG 轻量依赖（仅 chromadb）
	@echo "$(GREEN)📦 安装 RAG 依赖（chromadb）...$(NC)"
	@$(HATCH) run pip install chromadb
	@echo "$(GREEN)✅ RAG 依赖安装完成$(NC)"

setup: install deps rag-deps ## 一键初始化环境（含 RAG 轻量依赖）

##@ 运行

api: ## 启动 FastAPI 服务（http://127.0.0.1:8000）
	@echo "$(GREEN)🚀 启动 API 服务...$(NC)"
	@$(ENV_RUN) $(HATCH) run python examples/run_service.py

dev: api ## api 的别名

sdk-chat: ## 运行基础 SDK 对话示例
	@echo "$(GREEN)💬 运行基础对话示例...$(NC)"
	@$(ENV_RUN) $(HATCH) run python examples/basic_chat.py

fe-install: ## 安装前端依赖
	@echo "$(GREEN)📦 安装前端依赖...$(NC)"
	@npm --prefix frontend install

fe-dev: ## 启动前端开发服务（http://127.0.0.1:5173）
	@echo "$(GREEN)🖥️ 启动前端开发服务...$(NC)"
	@npm --prefix frontend run dev

fe-build: ## 构建前端产物
	@echo "$(GREEN)🔨 构建前端...$(NC)"
	@npm --prefix frontend run build

fe-preview: ## 预览前端构建产物
	@echo "$(GREEN)👀 预览前端构建...$(NC)"
	@npm --prefix frontend run preview

##@ 质量检查

test: ## 运行测试
	@$(HATCH) run test

test-cov: ## 运行测试覆盖率
	@$(HATCH) run test-cov

lint: ## 运行 ruff 检查
	@$(HATCH) run lint

type-check: ## 运行 mypy 类型检查
	@$(HATCH) run type-check

check: lint type-check ## 运行静态检查

fmt: ## 代码格式化
	@$(HATCH) run format

##@ 清理

clean: ## 清理 Python 缓存
	@echo "$(YELLOW)🧹 清理缓存...$(NC)"
	-@rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info 2>/dev/null || true
	-@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	-@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)✅ 清理完成$(NC)"

clean-rag: ## 清空 ChromaDB 向量数据库（需先停止 API 服务）
	@echo "$(YELLOW)🗑️  清空 ChromaDB 数据库...$(NC)"
	-@rm -rf chroma_db 2>/dev/null || true
	@echo "$(GREEN)✅ ChromaDB 已清空，重启 API 服务后将自动重建$(NC)"
