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
from astracore.sdk.config import AstraCoreConfig, LLMProfileConfig


class AstraCoreClient:
    """Main SDK client for AstraCore AI."""

    def __init__(self, config: AstraCoreConfig):
        self.config = config
        self._llm_adapters: dict[str, Any] = {}
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

        self._chat_use_cases: dict[str, ChatUseCase] = {}
        self._tool_loops: dict[str, ToolLoopUseCase] = {}

        self._memory_pipeline = MemoryPipeline(memory_adapter=self._memory)

        self._rag_pipeline = RAGPipeline(retriever=self._retriever)

        self._agent_orchestration = AgentOrchestrationUseCase(orchestrator=self._orchestrator)

    def _create_llm_adapter(self, profile: LLMProfileConfig) -> Any:
        """Create LLM adapter based on profile provider."""
        if profile.provider == "anthropic":
            return AnthropicAdapter(
                api_key=profile.api_key,
                default_model=profile.model,
                base_url=profile.base_url,
                max_tokens=profile.max_tokens,
                supports_temperature=profile.capabilities.temperature,
                use_anthropic_blocks=profile.capabilities.anthropic_blocks,
            )
        return OpenAIAdapter(
            api_key=profile.api_key,
            default_model=profile.model,
            base_url=profile.base_url,
            max_tokens=profile.max_tokens,
        )

    def _get_llm_adapter(self, profile_id: str | None = None) -> Any:
        profile = self.config.llm.get_profile(profile_id)
        if profile.id not in self._llm_adapters:
            self._llm_adapters[profile.id] = self._create_llm_adapter(profile)
        return self._llm_adapters[profile.id]

    def _get_chat_use_case(self, profile_id: str | None = None) -> ChatUseCase:
        profile = self.config.llm.get_profile(profile_id)
        if profile.id not in self._chat_use_cases:
            self._chat_use_cases[profile.id] = ChatUseCase(
                llm_adapter=self._get_llm_adapter(profile.id),
                memory_adapter=self._memory,
                policy_engine=self._policy,
            )
        return self._chat_use_cases[profile.id]

    def _get_tool_loop(self, profile_id: str | None = None) -> ToolLoopUseCase:
        profile = self.config.llm.get_profile(profile_id)
        if profile.id not in self._tool_loops:
            self._tool_loops[profile.id] = ToolLoopUseCase(
                llm_adapter=self._get_llm_adapter(profile.id),
                tool_adapter=self._tools,
                policy_engine=self._policy,
            )
        return self._tool_loops[profile.id]

    async def chat(
        self,
        message: str,
        session_id: UUID | None = None,
        model_profile: str | None = None,
    ) -> Message:
        """Send a chat message and get response."""
        session_id = session_id or uuid4()
        return await self._get_chat_use_case(model_profile).execute(
            session_id=session_id,
            user_message=message,
        )

    async def chat_stream(
        self,
        message: str,
        session_id: UUID | None = None,
        model_profile: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Send a chat message and get streaming response."""
        session_id = session_id or uuid4()
        async for event in self._get_chat_use_case(model_profile).execute_stream(
            session_id=session_id,
            user_message=message,
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
