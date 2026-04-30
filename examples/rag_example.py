"""RAG 示例：演示文档索引、向量检索，以及开启 RAG 的对话。

用法：
    python examples/rag_example.py
"""

import asyncio

from dotenv import load_dotenv

from astracore.sdk import AstraCoreClient

load_dotenv()

DOCUMENTS = [
    {
        "document_id": "astracore-overview",
        "text": (
            "AstraCore AI 是一个基于 Clean Architecture 的本地 AI 框架，"
            "支持 Anthropic Claude 和 DeepSeek 等多种 LLM，"
            "内置 RAG、工具调用、会话记忆和 MCP 协议支持。"
        ),
        "metadata": {"source": "docs", "category": "overview"},
    },
    {
        "document_id": "astracore-skills",
        "text": (
            "Skill 是 AstraCore 的系统提示管理机制，支持通过 YAML frontmatter 定义名称、"
            "描述和排序。内置 Skill 存储在 skills/ 目录，启动时自动同步到数据库。"
            "每个会话可以绑定独立的 Skill，也可以使用全局默认 Skill。"
        ),
        "metadata": {"source": "docs", "category": "skills"},
    },
    {
        "document_id": "astracore-mcp",
        "text": (
            "AstraCore 通过 MCP（Model Context Protocol）扩展工具能力。"
            "支持 filesystem、shell 等内置 MCP server，也可以通过配置接入第三方 MCP server。"
            "工具调用需要 LLM profile 开启 tools 能力。"
        ),
        "metadata": {"source": "docs", "category": "mcp"},
    },
]


async def main() -> None:
    async with AstraCoreClient() as client:
        # 1. 索引文档
        print("=== 索引文档 ===\n")
        for doc in DOCUMENTS:
            ok = await client.index_document(doc["document_id"], doc["text"], doc["metadata"])
            status = "✓" if ok else "✗"
            print(f"  {status} {doc['document_id']}")
        print()

        # 2. 纯向量检索
        print("=== 向量检索 ===\n")
        query = "AstraCore 支持哪些 LLM？"
        chunks = await client.retrieve(query, top_k=3)
        print(f"查询: {query}\n")
        for i, chunk in enumerate(chunks, 1):
            score = getattr(chunk, "score", 0)
            content = getattr(chunk, "content", str(chunk))
            print(f"  {i}. [score={score:.3f}] {content[:80]}...")
        print()

        # 3. RAG 增强对话
        print("=== RAG 对话 ===\n")
        question = "AstraCore 的 Skill 是什么，怎么使用？"
        print(f"问题: {question}\n")
        result = await client.chat(question, enable_rag=True, disable_skill=True)
        print(f"回复: {result.content}\n")
        print(f"模型: {result.model}")


def test_main():
    asyncio.run(main())


if __name__ == "__main__":
    test_main()