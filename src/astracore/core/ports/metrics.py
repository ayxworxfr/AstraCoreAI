"""Metrics reporter port interface."""

from abc import ABC, abstractmethod
from enum import StrEnum


class MetricType(StrEnum):
    """Metric types."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


class MetricsReporter(ABC):
    """Abstract metrics reporter interface."""

    @abstractmethod
    async def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Increment a counter metric."""
        pass

    @abstractmethod
    async def gauge(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Set a gauge metric."""
        pass

    @abstractmethod
    async def histogram(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Record a histogram value."""
        pass

    @abstractmethod
    async def timer(
        self,
        name: str,
        duration_ms: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Record a timing metric."""
        pass

    @abstractmethod
    async def flush(self) -> None:
        """Flush metrics."""
        pass
