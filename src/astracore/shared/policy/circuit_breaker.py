"""Circuit breaker for protecting LLM and external service calls.

状态机：
- closed  （关闭）：正常放行请求，记录失败次数。
- open    （开启）：拒绝所有请求，等待 recovery_time_s 后进入 half_open。
- half_open（半开）：放行一次探测请求，成功 → closed，失败 → open。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal


class CircuitBreakerOpenError(Exception):
    """熔断器处于 open 状态，拒绝请求。"""

    def __init__(self, name: str, remaining_s: float) -> None:
        self.remaining_s = remaining_s
        super().__init__(f"Circuit breaker '{name}' is open. Retry in {remaining_s:.1f}s.")


@dataclass
class CircuitBreaker:
    """三态熔断器。

    Parameters
    ----------
    name:
        标识名称，用于日志和异常信息。
    failure_threshold:
        连续失败多少次后进入 open 状态。
    recovery_time_s:
        open 状态持续多少秒后尝试进入 half_open。
    """

    name: str = "default"
    failure_threshold: int = 5
    recovery_time_s: float = 60.0

    _failures: int = field(default=0, init=False, repr=False)
    _state: Literal["closed", "open", "half_open"] = field(default="closed", init=False, repr=False)
    _opened_at: float | None = field(default=None, init=False, repr=False)

    @property
    def state(self) -> Literal["closed", "open", "half_open"]:
        return self._state

    def allow_request(self) -> bool:
        """返回 True 表示允许请求通过；返回 False 表示熔断拒绝。"""
        if self._state == "closed":
            return True
        if self._state == "open":
            elapsed = time.monotonic() - (self._opened_at or 0)
            if elapsed >= self.recovery_time_s:
                self._state = "half_open"
                return True
            return False
        # half_open：允许一次探测
        return True

    def check(self) -> None:
        """检查是否允许请求；不允许时抛出 CircuitBreakerOpenError。"""
        if not self.allow_request():
            elapsed = time.monotonic() - (self._opened_at or 0)
            remaining = max(0.0, self.recovery_time_s - elapsed)
            raise CircuitBreakerOpenError(self.name, remaining)

    def record_success(self) -> None:
        """记录一次成功；重置失败计数，恢复为 closed。"""
        self._failures = 0
        self._state = "closed"
        self._opened_at = None

    def record_failure(self) -> None:
        """记录一次失败；达到阈值后进入 open。"""
        self._failures += 1
        if self._state == "half_open" or self._failures >= self.failure_threshold:
            self._state = "open"
            self._opened_at = time.monotonic()
