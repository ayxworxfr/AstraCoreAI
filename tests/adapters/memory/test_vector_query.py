"""MemoryVectorAdapter query score floor."""

from astracore.infrastructure.memory.vector import MemoryVectorAdapter


class _FakeCollection:
    def query(self, **kwargs):
        return {
            "documents": [["relevant doc", "noise doc"]],
            "distances": [[0.2, 0.8]],
            "metadatas": [
                [
                    {"memory_id": "hit", "type": "fact", "scope": "session"},
                    {"memory_id": "miss", "type": "fact", "scope": "session"},
                ]
            ],
        }


def test_query_sync_drops_hits_below_min_score() -> None:
    adapter = MemoryVectorAdapter()
    adapter._available = True
    adapter._collection = _FakeCollection()

    hits = adapter._query_sync(
        "query",
        user_id="default",
        scope_filter=["session"],
        session_id="s1",
        project_id=None,
        n_results=8,
        min_score=0.5,
    )

    assert [hit.memory_id for hit in hits] == ["hit"]
    assert hits[0].score == 0.8
