"""External data isolation for prompt injection defense."""

_CLOSING_TAG = "</external_data>"
_ESCAPED_CLOSING_TAG = "&lt;/external_data&gt;"


def wrap_external(content: str, source: str, trust: str = "untrusted") -> str:
    """Wrap external content in a trust-tagged block to prevent prompt injection.

    Escapes any closing tags inside content so they cannot break out of the wrapper.
    The system prompt must instruct the LLM to treat tagged content as data, not instructions.
    """
    escaped = content.replace(_CLOSING_TAG, _ESCAPED_CLOSING_TAG)
    return f'<external_data trust="{trust}" source="{source}">\n{escaped}\n{_CLOSING_TAG}'
