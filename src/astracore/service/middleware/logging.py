"""HTTP request logging middleware."""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from astracore.runtime.observability.logger import get_logger, request_id_var

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求注入 request_id 并记录访问日志。

    职责：
    1. 生成 12 位十六进制 request_id，写入 contextvars —— 同一请求内所有日志
       都会通过 _ContextFilter 自动附带这个 ID，无需手动传递。
    2. 将 request_id 写入响应头 X-Request-ID，方便前端/调试工具追踪。
    3. 记录 method、path、status code 和处理耗时。

    注意：ContextVar token 在 finally 块中重置，确保异步环境下不会污染其他请求。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)

        start = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "%s %s → %d  (%.1fms)",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "%s %s → ERR  (%.1fms)",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise
        finally:
            # 必须 reset，否则在 asyncio 任务复用时 request_id 会残留到下一个请求
            request_id_var.reset(token)
