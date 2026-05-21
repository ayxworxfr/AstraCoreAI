"""Security validation and content filtering."""

import re
from typing import Any

_SUSPICIOUS_PATTERNS = [
    re.compile(r"<script[^>]*>", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"onerror=", re.IGNORECASE),
    re.compile(r"onclick=", re.IGNORECASE),
]


class InputValidator:
    """Input validation for security."""

    def __init__(self, max_input_length: int = 100_000):
        self.max_input_length = max_input_length

    def validate_user_input(self, content: str) -> tuple[bool, str | None]:
        """Validate user input."""
        if len(content) > self.max_input_length:
            return False, f"Input exceeds maximum length of {self.max_input_length}"

        if self._contains_suspicious_patterns(content):
            return False, "Input contains suspicious patterns"

        return True, None

    def _contains_suspicious_patterns(self, content: str) -> bool:
        """Check for suspicious patterns using precompiled regex."""
        return any(p.search(content) for p in _SUSPICIOUS_PATTERNS)

    def sanitize_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Sanitize metadata by removing sensitive fields."""
        sensitive_fields = ["password", "api_key", "secret", "token", "credential"]
        return {
            k: "***REDACTED***" if any(f in k.lower() for f in sensitive_fields) else v
            for k, v in metadata.items()
        }


class ContentFilter:
    """Content filtering for safety."""

    def __init__(self) -> None:
        self.blocked_terms: set[str] = set()

    def add_blocked_term(self, term: str) -> None:
        """Add a term to the blocklist."""
        self.blocked_terms.add(term.lower())

    def filter_content(self, content: str) -> tuple[bool, str]:
        """Filter content for blocked terms."""
        content_lower = content.lower()

        for term in self.blocked_terms:
            if term in content_lower:
                return False, f"Content contains blocked term: {term}"

        return True, content
