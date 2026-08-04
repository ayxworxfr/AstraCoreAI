"""Chat run 注册表：本机持有 Task/HITL，Redis 做状态与 SSE 扇出。

多 worker 下：
- 生成 Task / Future 只在 owning worker 进程内
- Redis 保存 state 快照 + pub/sub 事件，其他 worker 可转发 SSE / 投递 HITL 答案
- Redis 不可用时静默退化为纯进程内（与 HybridMemoryAdapter 同策略）
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from astracore.infrastructure.db.models import ChatRunRow
from astracore.shared.observability.logger import get_logger

logger = get_logger(__name__)

_STATE_KEY = "astracore:run:{run_id}:state"
_EVENTS_CH = "astracore:run:{run_id}:events"
_HITL_CH = "astracore:run:{run_id}:hitl"
_CANCEL_CH = "astracore:run:{run_id}:cancel"
_STATE_TTL_SECONDS = 6 * 3600  # 状态快照 TTL；update_state 每次写入都会续期


def _utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


class ActiveRun:
    """本机 run 状态：热路径写内存；subscriber / HITL Future 不可跨进程。"""

    def __init__(self, row: ChatRunRow):
        self.task: asyncio.Task[None] | None = None
        self.subscribers: set[asyncio.Queue[tuple[str, str]]] = set()
        # HITL: 每 run 同时只挂起一个问题；由 POST /answer 或 Redis hitl 信道 resolve
        self._hitl_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._control_tasks: list[asyncio.Task[Any]] = []
        self.state: dict[str, Any] = {
            "run_id": row.id,
            "session_id": row.session_id,
            "status": row.status,
            "user_message": row.user_message,
            "assistant_content": row.assistant_content,
            "thinking_blocks": row.thinking_blocks or [],
            "tool_activity": row.tool_activity or [],
            "error": row.error,
            "created_at": _utc_iso(row.created_at),
            "updated_at": _utc_iso(row.updated_at),
            "completed_at": _utc_iso(row.completed_at) if row.completed_at else None,
            "pending_question": None,
        }

    def update(self, **patch: Any) -> None:
        self.state.update(patch)
        self.state["updated_at"] = datetime.now(UTC).isoformat()

    def payload(self) -> dict[str, Any]:
        return dict(self.state)


def _enqueue(queue: asyncio.Queue[tuple[str, str]], item: tuple[str, str]) -> None:
    """写订阅队列；满则丢最旧，保证最新状态可达。"""
    while True:
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return


class RunRegistry:
    """本机 ActiveRun 字典 + 可选 Redis 镜像。"""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url
        self._local: dict[str, ActiveRun] = {}
        self._redis: Any = None
        self._redis_disabled = not bool(redis_url)
        # 持有 fire-and-forget 任务的强引用，避免被 GC 提前回收（asyncio 官方告警）
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _get_redis(self) -> Any:
        if self._redis_disabled or not self._redis_url:
            return None
        if self._redis is None:
            try:
                from redis.asyncio import Redis

                self._redis = Redis.from_url(self._redis_url, decode_responses=True)
            except Exception as exc:
                logger.warning("RunRegistry Redis 不可用，退化为进程内: %s", exc)
                self._redis_disabled = True
                return None
        return self._redis

    def _disable_redis(self, action: str, error: Exception) -> None:
        logger.warning("RunRegistry Redis %s 失败，禁用 Redis: %s", action, error)
        self._redis_disabled = True
        self._redis = None

    def register(self, run_id: str, active: ActiveRun) -> None:
        self._local[run_id] = active
        self._persist_state(run_id, active.payload())
        self._ensure_control_listeners(run_id, active)

    def pop(self, run_id: str, default: ActiveRun | None = None) -> ActiveRun | None:
        active = self._local.pop(run_id, default)
        if active is not None:
            for t in active._control_tasks:
                t.cancel()
            active._control_tasks.clear()
        return active

    def get_local(self, run_id: str) -> ActiveRun | None:
        return self._local.get(run_id)

    def values_local(self) -> list[ActiveRun]:
        return list(self._local.values())

    def update_state(self, run_id: str, **patch: Any) -> None:
        active = self._local.get(run_id)
        if active is None:
            return
        active.update(**patch)
        self._persist_state(run_id, active.payload())

    def broadcast(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        active = self._local.get(run_id)
        if active is not None:
            for queue in active.subscribers:
                _enqueue(queue, (event, payload))
        self._publish_json(_EVENTS_CH.format(run_id=run_id), {"event": event, "data": data})

    async def load_state(self, run_id: str) -> dict[str, Any] | None:
        active = self._local.get(run_id)
        if active is not None:
            return active.payload()
        redis = self._get_redis()
        if redis is None:
            return None
        try:
            raw = await redis.get(_STATE_KEY.format(run_id=run_id))
        except Exception as exc:
            self._disable_redis("get_state", exc)
            return None
        if not raw:
            return None
        try:
            parsed: dict[str, Any] = json.loads(raw)
            return parsed
        except json.JSONDecodeError:
            return None

    async def publish_hitl_answer(
        self, run_id: str, question_id: str, answer: dict[str, Any]
    ) -> bool:
        """本机有 Future 则直接 resolve；否则 PUBLISH 给 owning worker 处理。

        返回值表示答案是否被实际处理：本机 resolve 成功，或 Redis 端确认至少有一个
        订阅者收到（PUBLISH 返回值为收到消息的客户端数）。Redis 不可用时返回 False，
        调用方应据此向用户报告「未找到活跃任务」。
        """
        active = self._local.get(run_id)
        if active is not None:
            fut = active._hitl_futures.get(question_id)
            if fut is not None and not fut.done():
                fut.set_result(answer)
                return True
            return False

        redis = self._get_redis()
        if redis is None:
            return False
        channel = _HITL_CH.format(run_id=run_id)
        message = json.dumps(
            {"question_id": question_id, "answer": answer}, ensure_ascii=False, default=str
        )
        try:
            subscribers: int = await redis.publish(channel, message)
        except Exception as exc:
            self._disable_redis("publish_hitl_answer", exc)
            return False
        return subscribers > 0

    def request_cancel(self, run_id: str) -> bool:
        """本机有 task 则 cancel；否则发 Redis cancel 信号。返回是否本机已处理。"""
        active = self._local.get(run_id)
        if active is not None:
            if active.task is not None:
                active.task.cancel()
            return True
        self._publish_json(_CANCEL_CH.format(run_id=run_id), {"run_id": run_id})
        return False

    async def subscribe_remote_events(self, run_id: str) -> AsyncIterator[tuple[str, str]]:
        """订阅其他 worker 广播的 SSE 事件。Redis 不可用则空迭代。"""
        redis = self._get_redis()
        if redis is None:
            return
        pubsub = redis.pubsub()
        channel = _EVENTS_CH.format(run_id=run_id)
        try:
            await pubsub.subscribe(channel)
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    await asyncio.sleep(0.05)
                    continue
                data = msg.get("data")
                if not isinstance(data, str):
                    continue
                try:
                    envelope = json.loads(data)
                except json.JSONDecodeError:
                    continue
                event = str(envelope.get("event") or "")
                inner = envelope.get("data")
                payload = (
                    inner
                    if isinstance(inner, str)
                    else json.dumps(inner or {}, ensure_ascii=False, default=str)
                )
                yield event, payload
                if event in {"done", "error"}:
                    break
        except Exception as exc:
            self._disable_redis("subscribe", exc)
        finally:
            closer = getattr(pubsub, "aclose", None) or pubsub.close
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    def _persist_state(self, run_id: str, state: dict[str, Any]) -> None:
        """尽力将状态快照写入 Redis；同步方法内部用 fire-and-forget task 承载异步写入。"""
        redis = self._get_redis()
        if redis is None:
            return
        key = _STATE_KEY.format(run_id=run_id)
        payload = json.dumps(state, ensure_ascii=False, default=str)

        async def _set() -> None:
            try:
                await redis.setex(key, _STATE_TTL_SECONDS, payload)
            except Exception as exc:
                self._disable_redis("set_state", exc)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._track(loop.create_task(_set()))

    def _publish_json(self, channel: str, payload: dict[str, Any]) -> None:
        redis = self._get_redis()
        if redis is None:
            return
        raw = json.dumps(payload, ensure_ascii=False, default=str)

        async def _pub() -> None:
            try:
                await redis.publish(channel, raw)
            except Exception as exc:
                self._disable_redis("publish", exc)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._track(loop.create_task(_pub()))

    def _ensure_control_listeners(self, run_id: str, active: ActiveRun) -> None:
        if self._redis_disabled or active._control_tasks:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        active._control_tasks.append(loop.create_task(self._listen_hitl(run_id, active)))
        active._control_tasks.append(loop.create_task(self._listen_cancel(run_id, active)))

    async def _listen_hitl(self, run_id: str, active: ActiveRun) -> None:
        redis = self._get_redis()
        if redis is None:
            return
        pubsub = redis.pubsub()
        channel = _HITL_CH.format(run_id=run_id)
        try:
            await pubsub.subscribe(channel)
            while run_id in self._local:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    await asyncio.sleep(0.05)
                    continue
                data = msg.get("data")
                if not isinstance(data, str):
                    continue
                try:
                    envelope = json.loads(data)
                except json.JSONDecodeError:
                    continue
                qid = str(envelope.get("question_id") or "")
                answer = envelope.get("answer") or {}
                fut = active._hitl_futures.get(qid)
                if fut is not None and not fut.done():
                    fut.set_result(answer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._disable_redis("hitl_listen", exc)
        finally:
            closer = getattr(pubsub, "aclose", None) or pubsub.close
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    async def _listen_cancel(self, run_id: str, active: ActiveRun) -> None:
        redis = self._get_redis()
        if redis is None:
            return
        pubsub = redis.pubsub()
        channel = _CANCEL_CH.format(run_id=run_id)
        try:
            await pubsub.subscribe(channel)
            while run_id in self._local:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    await asyncio.sleep(0.05)
                    continue
                if active.task is not None and not active.task.done():
                    active.task.cancel()
                break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._disable_redis("cancel_listen", exc)
        finally:
            closer = getattr(pubsub, "aclose", None) or pubsub.close
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
