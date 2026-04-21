"""AstraCore SDK client implementation."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.adapters.llm.openai import OpenAIAdapter
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.adapters.retrieval.chroma import ChromaRetrieverAdapter
from astracore.adapters.tools.native import NativeToolAdapter
from astracore.adapters.workflow.native import NativeWorkflowOrchestrator
from astracore.core.application.agent import AgentOrchestrationUseCase
from astracore.core.application.chat import ChatUseCase
from astracore.core.application.memory import MemoryPipeline
from astracore.core.application.rag import RAGPipeline
from astracore.core.application.tool_loop import ToolLoopUseCase
from astracore.core.domain.agent import AgentTask
from astracore.core.domain.message import Message
from astracore.core.ports.llm import StreamEvent
from astracore.core.ports.tool import ToolParameter
from astracore.runtime.policy.engine import PolicyEngine
from astracore.sdk.config import AstraCoreConfig


class AstraCoreClient:
    """Main SDK client for AstraCore AI."""

    def __init__(self, config: AstraCoreConfig):
        self.config = config
        self._llm = self._create_llm_adapter()
        self._memory = HybridMemoryAdapter(
            redis_url=config.memory.redis_url,
            db_url=config.memory.db_url,
        )
        self._tools = NativeToolAdapter()
        self._retriever = ChromaRetrieverAdapter(
            collection_name=config.retrieval.collection_name,
            persist_directory=config.retrieval.persist_directory,
        )
        self._orchestrator = NativeWorkflowOrchestrator()
        self._policy = PolicyEngine()

        self._chat_use_case = ChatUseCase(
            llm_adapter=self._llm,
            memory_adapter=self._memory,
            policy_engine=self._policy,
        )

        self._tool_loop = ToolLoopUseCase(
            llm_adapter=self._llm,
            tool_adapter=self._tools,
            policy_engine=self._policy,
        )

        self._memory_pipeline = MemoryPipeline(memory_adapter=self._memory)

        self._rag_pipeline = RAGPipeline(retriever=self._retriever)

        self._agent_orchestration = AgentOrchestrationUseCase(orchestrator=self._orchestrator)

    def _create_llm_adapter(self) -> Any:
        """Create LLM adapter based on provider."""
        if self.config.llm.provider == "anthropic":
            return AnthropicAdapter(
                api_key=self.config.llm.api_key,
                default_model=self.config.llm.model,
                base_url=self.config.llm.base_url,
            )
        return OpenAIAdapter(
            api_key=self.config.llm.api_key,
            default_model=self.config.llm.model,
            base_url=self.config.llm.base_url,
        )

    async def chat(
        self,
        message: str,
        session_id: UUID | None = None,
        model: str | None = None,
    ) -> Message:
        """Send a chat message and get response."""
        session_id = session_id or uuid4()
        return await self._chat_use_case.execute(
            session_id=session_id,
            user_message=message,
            model=model,
        )

    async def chat_stream(
        self,
        message: str,
        session_id: UUID | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Send a chat message and get streaming response."""
        session_id = session_id or uuid4()
        async for event in self._chat_use_case.execute_stream(
            session_id=session_id,
            user_message=message,
            model=model,
        ):
            yield event

    def register_tool(
        self,
        name: str,
        func: Any,
        description: str,
        parameters: list[ToolParameter],
    ) -> None:
        """Register a custom tool."""
        self._tools.register_tool(
            name=name,
            func=func,
            description=description,
            parameters=parameters,
        )

    async def index_document(
        self,
        document_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Index a document for RAG."""
        return await self._rag_pipeline.index_document(
            document_id=document_id,
            text=text,
            metadata=metadata,
        )

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[Any]:
        """Retrieve relevant chunks."""
        return await self._rag_pipeline.retrieve_with_citations(
            query=query,
            top_k=top_k,
        )

    async def create_agent_workflow(
        self,
        objective: str,
        tasks: list[AgentTask] | None = None,
    ) -> Any:
        """Create a multi-agent workflow."""
        if tasks:
            return await self._orchestrator.create_workflow(
                name=objective,
                tasks=tasks,
            )
        else:
            return await self._agent_orchestration.create_multi_agent_workflow(objective=objective)

    async def execute_workflow(self, workflow_id: UUID) -> Any:
        """Execute an agent workflow."""
        return await self._agent_orchestration.execute_workflow(workflow_id)

    async def clear_session(self, session_id: UUID) -> None:
        """Clear session memory."""
        await self._memory_pipeline.clear_session(session_id)
