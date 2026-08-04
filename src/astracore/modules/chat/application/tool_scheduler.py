"""声明式工具调度 —— 按 is_concurrency_safe 分批，流式/非流式共用。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from astracore.modules.chat.application.tool_executor import ToolExecutor
from astracore.modules.chat.domain.message import ToolCall, ToolResult
from astracore.modules.tools.application.partition import partition_tool_calls
from astracore.shared.ports.llm import StreamEvent, StreamEventType

_TOOL_DONE = object()


def tool_result_event(
    result: ToolResult,
    *,
    arguments: dict[str, Any] | None = None,
    duration_ms: int = 0,
) -> StreamEvent:
    """把 ToolResult 编成前端可消费的 TOOL_RESULT 事件。"""
    return StreamEvent(
        event_type=StreamEventType.TOOL_RESULT,
        content=result.name,
        metadata={
            "tool": result.name,
            "tool_call_id": result.tool_call_id,
            "input": arguments or {},
            "result": result.content,
            "is_error": result.is_error,
            "duration_ms": duration_ms,
        },
    )


class ToolScheduler:
    """分区调度器：并发安全工具 gather，其余串行独占。"""

    def __init__(self, executor: ToolExecutor) -> None:
        self._executor = executor

    async def run(self, calls: list[ToolCall]) -> list[ToolResult]:
        """非流式：返回与 ``calls`` 同序的结果列表。"""
        if not calls:
            return []
        id_to_idx = {tc.id: i for i, tc in enumerate(calls)}
        results: list[ToolResult | None] = [None] * len(calls)
        for batch in partition_tool_calls(calls, self._executor.definition_map()):
            if batch.concurrent:
                batch_results = await asyncio.gather(
                    *[self._executor.run(tc) for tc in batch.calls]
                )
                for tc, result in zip(batch.calls, batch_results, strict=True):
                    results[id_to_idx[tc.id]] = result
            else:
                for tc in batch.calls:
                    results[id_to_idx[tc.id]] = await self._executor.run(tc)
        return [r for r in results if r is not None]

    async def run_streaming(
        self,
        calls: list[ToolCall],
        parse_errors: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent | list[ToolResult]]:
        """流式调度：yield 中间事件，最后 yield ``list[ToolResult]``（同序）。

        ``parse_errors``: tool_call_id → 错误文案，跳过真实执行直接回流。
        """
        parse_errors = parse_errors or {}
        if not calls:
            yield []
            return

        id_to_idx = {tc.id: i for i, tc in enumerate(calls)}
        results_by_idx: dict[int, ToolResult] = {}

        for batch in partition_tool_calls(calls, self._executor.definition_map()):
            queue: asyncio.Queue[Any] = asyncio.Queue()
            indexed = [(id_to_idx[tc.id], tc) for tc in batch.calls]
            tasks = [
                asyncio.create_task(self._drive_one(tc, i, parse_errors, queue))
                for i, tc in indexed
            ]
            done_count = 0
            try:
                while done_count < len(tasks):
                    idx, item, result = await queue.get()
                    if item is _TOOL_DONE:
                        results_by_idx[idx] = result
                        done_count += 1
                    else:
                        yield item
            except (asyncio.CancelledError, GeneratorExit):
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        yield [results_by_idx[i] for i in range(len(calls))]

    async def _drive_one(
        self,
        tool_call: ToolCall,
        idx: int,
        parse_errors: dict[str, str],
        queue: asyncio.Queue[Any],
    ) -> None:
        if tool_call.id in parse_errors:
            result = ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=parse_errors[tool_call.id],
                is_error=True,
            )
            await self._executor.fire_after(result)
            await queue.put((idx, tool_result_event(result, arguments=tool_call.arguments), None))
            await queue.put((idx, _TOOL_DONE, result))
            return

        final: ToolResult | None = None
        async for item in self._executor.run_streaming(tool_call):
            if isinstance(item, ToolResult):
                final = item
            else:
                await queue.put((idx, item, None))

        assert final is not None
        await queue.put((idx, tool_result_event(final, arguments=tool_call.arguments), None))
        await queue.put((idx, _TOOL_DONE, final))
