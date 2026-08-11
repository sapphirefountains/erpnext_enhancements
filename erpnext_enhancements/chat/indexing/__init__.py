# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat indexing — chunking, embeddings and rolling digests.

Everything here **writes** the index that :mod:`chat.retrieval` reads. The split matters: the
retrieval side runs as a real person and derives their room set from membership, while this
side runs on the scheduler with no session user at all and legitimately reads across rooms
with permissions ignored.

That asymmetry is exactly why the retrieval gate and the MCP denylist both exist. An indexer
that reads every room is fine; an indexer that could be *called* by a user, or whose output
could be read without the gate, is not. So nothing in this package is whitelisted, nothing
here is reachable from a document event, and every entry point is a scheduler job.

| module | what it is |
|---|---|
| ``chunker`` | **pure.** Where a chunk ends, and the tail that is deliberately not indexed |
| ``embed`` | the Vertex AI REST client. No SDK — the host cannot install one |
| ``digest`` | the scheduler-driven batch summariser and its invalidation |
"""
