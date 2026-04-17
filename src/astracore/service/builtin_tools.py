"""内置工具集合，注册到 NativeToolAdapter 供工具循环使用。"""

import ast
import math
import os
from datetime import datetime, timezone

from astracore.adapters.tools.native import NativeToolAdapter
from astracore.core.ports.tool import ToolParameter, ToolParameterType

# 安全数学求值白名单
_SAFE_MATH: dict = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
_SAFE_MATH["abs"] = abs

_SAFE_AST_NODES = {
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
    ast.FloorDiv, ast.USub, ast.UAdd,
}


def _get_current_time(timezone_name: str = "Asia/Shanghai") -> str:
    now = datetime.now(timezone.utc)
    return f"当前 UTC 时间：{now.strftime('%Y-%m-%d %H:%M:%S UTC')}（时区参数：{timezone_name}）"


def _calculate(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval")
        for node in ast.walk(tree):
            if type(node) not in _SAFE_AST_NODES:
                return f"不支持的表达式类型：{type(node).__name__}"
        result = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, _SAFE_MATH)  # noqa: S307
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算失败：{e}"


def build_tool_adapter() -> NativeToolAdapter:
    """构造并注册所有内置工具，返回 NativeToolAdapter。

    新增工具时只需在此函数中追加 register_tool 调用即可。
    """
    # 延迟导入避免循环依赖（rag_api 依赖 chat_api 的 lru_cache 工厂）
    from astracore.service.api import rag as rag_api  # noqa: PLC0415

    async def _search_knowledge_base(query: str, top_k: int = 3) -> str:
        try:
            pipeline = rag_api._get_rag_pipeline()
            chunks = await pipeline.retrieve_with_citations(query=query, top_k=top_k)
            if not chunks:
                return "知识库中未找到相关内容。"
            parts = [
                f"[{i + 1}] 来源：{c.citation.title or c.citation.source_id}\n{c.content}"
                for i, c in enumerate(chunks)
            ]
            return "\n\n".join(parts)
        except Exception as e:
            return f"知识库搜索失败：{e}"

    adapter = NativeToolAdapter()

    adapter.register_tool(
        name="get_current_time",
        func=_get_current_time,
        description="获取当前日期和时间。当用户询问现在几点、今天日期等问题时使用。",
        parameters=[
            ToolParameter(
                name="timezone_name",
                type=ToolParameterType.STRING,
                description="时区名称，例如 Asia/Shanghai、UTC、America/New_York",
                required=False,
            )
        ],
    )

    adapter.register_tool(
        name="calculate",
        func=_calculate,
        description="对数学表达式求值，支持加减乘除、幂运算、取模等基本运算。",
        parameters=[
            ToolParameter(
                name="expression",
                type=ToolParameterType.STRING,
                description="数学表达式，例如 '2 ** 10'、'(3 + 5) * 7'",
                required=True,
            )
        ],
    )

    adapter.register_tool(
        name="search_knowledge_base",
        func=_search_knowledge_base,
        description=(
            "在知识库（RAG）中搜索与查询相关的文档片段。"
            "当需要查找 AstraCoreAI 的功能、架构、使用方法等信息时使用。"
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ToolParameterType.STRING,
                description="搜索查询语句",
                required=True,
            ),
            ToolParameter(
                name="top_k",
                type=ToolParameterType.NUMBER,
                description="返回结果数量，默认 3",
                required=False,
            ),
        ],
    )

    # --- 工具 4: 联网搜索（Tavily）---
    async def _web_search(query: str, max_results: int = 5) -> str:
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        if not api_key:
            return (
                "未配置联网搜索 API Key。"
                "请在 .env 文件中设置 TAVILY_API_KEY（免费注册：https://tavily.com）"
            )
        try:
            import httpx  # noqa: PLC0415
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": max_results,
                        "include_answer": True,
                    },
                )
                data = resp.json()
            parts: list[str] = []
            if data.get("answer"):
                parts.append(f"摘要：{data['answer']}")
            for r in data.get("results", []):
                parts.append(
                    f"标题：{r.get('title', '无标题')}\n"
                    f"内容：{r.get('content', '')}\n"
                    f"URL：{r.get('url', '')}"
                )
            return "\n\n---\n\n".join(parts) if parts else "未找到相关搜索结果"
        except Exception as e:
            return f"搜索失败：{e}"

    adapter.register_tool(
        name="web_search",
        func=_web_search,
        description=(
            "在互联网上搜索实时信息。当需要查询最新新闻、当前事件、"
            "实时数据或训练数据截止日期之后的信息时使用。"
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ToolParameterType.STRING,
                description="搜索关键词或问题",
                required=True,
            ),
            ToolParameter(
                name="max_results",
                type=ToolParameterType.NUMBER,
                description="返回结果数量，默认 5",
                required=False,
            ),
        ],
    )

    return adapter
