"""RED/GREEN tests for declarative tool-call partitioning."""

from astracore.modules.chat.domain.message import ToolCall
from astracore.modules.tools.application.partition import partition_tool_calls
from astracore.modules.tools.ports.tool import ToolDefinition


def _def(
    name: str,
    *,
    safe: bool = False,
    readonly: bool = False,
    destructive: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        is_concurrency_safe=safe,
        is_readonly=readonly,
        is_destructive=destructive,
    )


def _call(name: str) -> ToolCall:
    return ToolCall(name=name, arguments={})


def test_defaults_are_fail_closed():
    d = ToolDefinition(name="x", description="x")
    assert d.is_concurrency_safe is False
    assert d.is_readonly is False
    assert d.is_destructive is False


def test_partition_read_write_read_batches():
    """[Read(a), Read(b), Write(c), Read(d)] → parallel reads, serial write, serial read."""
    defs = {
        "read_a": _def("read_a", safe=True, readonly=True),
        "read_b": _def("read_b", safe=True, readonly=True),
        "write_c": _def("write_c", safe=False, destructive=True),
        "read_d": _def("read_d", safe=True, readonly=True),
    }
    calls = [_call("read_a"), _call("read_b"), _call("write_c"), _call("read_d")]
    batches = partition_tool_calls(calls, defs)

    assert len(batches) == 3
    assert [c.name for c in batches[0]] == ["read_a", "read_b"]
    assert batches[0].concurrent is True
    assert [c.name for c in batches[1]] == ["write_c"]
    assert batches[1].concurrent is False
    # 写后读落在独立 batch，靠顺序保证发生在 write 之后；
    # 自身仍是 concurrency_safe，concurrent=True（单元素 gather 无害）。
    assert [c.name for c in batches[2]] == ["read_d"]
    assert batches[2].concurrent is True


def test_unknown_tool_is_serial():
    """未注册工具按 fail-closed 串行处理。"""
    batches = partition_tool_calls([_call("mystery")], {})
    assert len(batches) == 1
    assert batches[0].concurrent is False


def test_all_safe_tools_one_parallel_batch():
    defs = {
        "a": _def("a", safe=True, readonly=True),
        "b": _def("b", safe=True, readonly=True),
    }
    batches = partition_tool_calls([_call("a"), _call("b")], defs)
    assert len(batches) == 1
    assert batches[0].concurrent is True
    assert len(batches[0]) == 2


def test_empty_calls():
    assert partition_tool_calls([], {}) == []


def test_path_conflict_splits_safe_reads():
    """同路径的两个 concurrency_safe 读也必须分批，避免写后读乱序语义。"""
    defs = {
        "read": ToolDefinition(
            name="read", description="r", is_concurrency_safe=True, is_readonly=True
        ),
    }
    calls = [
        ToolCall(name="read", arguments={"path": "src/a.ts"}),
        ToolCall(name="read", arguments={"path": "src/a.ts"}),
    ]
    batches = partition_tool_calls(calls, defs)
    assert len(batches) == 2
    assert all(len(b) == 1 for b in batches)


def test_different_paths_can_parallel():
    defs = {
        "read": ToolDefinition(
            name="read", description="r", is_concurrency_safe=True, is_readonly=True
        ),
    }
    calls = [
        ToolCall(name="read", arguments={"path": "a.ts"}),
        ToolCall(name="read", arguments={"path": "b.ts"}),
    ]
    batches = partition_tool_calls(calls, defs)
    assert len(batches) == 1
    assert batches[0].concurrent is True
