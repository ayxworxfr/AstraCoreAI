"""Web search provider implementations for the web_search built-in tool."""

import asyncio
import os
from typing import Any

import httpx

from astracore.sdk.config import SearXNGSearchConfig, TavilySearchConfig, WebSearchConfig


async def _tavily(query: str, max_results: int, cfg: TavilySearchConfig) -> str:
    api_key = os.getenv(cfg.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"Tavily API key not found in environment variable '{cfg.api_key_env}'")

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
        resp.raise_for_status()
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


async def _searxng(
    query: str,
    max_results: int,
    cfg: SearXNGSearchConfig,
    *,
    categories: str = "",
    language: str = "",
) -> str:
    params: dict[str, Any] = {"q": query, "format": "json"}
    if cfg.engines:
        params["engines"] = cfg.engines
    if categories:
        params["categories"] = categories
    if language:
        params["language"] = language

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{cfg.base_url.rstrip('/')}/search",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])[:max_results]
    if not results:
        return "未找到相关搜索结果"
    parts = [
        f"标题：{r.get('title', '无标题')}\n内容：{r.get('content', '')}\nURL：{r.get('url', '')}"
        for r in results
    ]
    return "\n\n---\n\n".join(parts)


async def _duckduckgo(query: str, max_results: int) -> str:
    from ddgs import DDGS  # noqa: PLC0415

    def _sync() -> list[dict[str, Any]]:
        return list(DDGS().text(query, max_results=max_results))

    results = await asyncio.to_thread(_sync)
    if not results:
        return "未找到相关搜索结果"
    parts = [
        f"标题：{r.get('title', '无标题')}\n内容：{r.get('body', '')}\nURL：{r.get('href', '')}"
        for r in results
    ]
    return "\n\n---\n\n".join(parts)


_FETCH_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_FETCH_PAGE_MAX_CHARS = 15_000


async def fetch_page(url: str) -> str:
    """Fetch a web page and extract its main text content.

    Raises on network or HTTP errors — callers are responsible for
    formatting user-facing error messages.
    """
    import html2text  # noqa: PLC0415

    async with httpx.AsyncClient(
        timeout=15.0, follow_redirects=True, headers=_FETCH_PAGE_HEADERS
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    h = html2text.HTML2Text()
    h.ignore_links = True
    h.ignore_images = True
    h.body_width = 0
    text = h.handle(resp.text)

    lines = [line for line in text.splitlines() if line.strip()]
    cleaned = "\n".join(lines)
    if not cleaned:
        return "页面无可提取的文字内容"
    if len(cleaned) > _FETCH_PAGE_MAX_CHARS:
        return cleaned[:_FETCH_PAGE_MAX_CHARS] + f"\n\n[内容已截断，共 {len(cleaned)} 字符]"
    return cleaned


async def search(
    query: str,
    max_results: int,
    cfg: WebSearchConfig,
    *,
    categories: str = "",
    language: str = "",
) -> str:
    """Execute a web search using the configured provider.

    Raises on any provider error — callers are responsible for catching and
    formatting user-facing error messages.
    categories / language are SearXNG-specific; silently ignored by other providers.
    """
    match cfg.provider:
        case "tavily":
            return await _tavily(query, max_results, cfg.tavily)
        case "searxng":
            return await _searxng(
                query,
                max_results,
                cfg.searxng,
                categories=categories,
                language=language,
            )
        case "duckduckgo":
            return await _duckduckgo(query, max_results)
        case _:
            raise ValueError(f"Unknown web search provider: {cfg.provider!r}")
