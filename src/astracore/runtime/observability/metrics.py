"""Metrics reporter implementation."""


from astracore.core.ports.metrics import MetricsReporter


class SimpleMetricsReporter(MetricsReporter):
    """Simple in-memory metrics reporter."""

    def __init__(self):
        self._metrics: dict[str, list[tuple[float, dict[str, str] | None]]] = {}

    async def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Increment a counter metric."""
        if name not in self._metrics:
            self._metrics[name] = []
        self._metrics[name].append((value, tags))

    async def gauge(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Set a gauge metric."""
        if name not in self._metrics:
            self._metrics[name] = []
        self._metrics[name].append((value, tags))

    async def histogram(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Record a histogram value."""
        if name not in self._metrics:
            self._metrics[name] = []
        self._metrics[name].append((value, tags))

    async def timer(
        self,
        name: str,
        duration_ms: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Record a timing metric."""
        if name not in self._metrics:
            self._metrics[name] = []
        self._metrics[name].append((duration_ms, tags))

    async def flush(self) -> None:
        """Flush metrics."""
        pass

    def get_metrics(self) -> dict[str, list[tuple[float, dict[str, str] | None]]]:
        """Get all collected metrics."""
        return self._metrics
