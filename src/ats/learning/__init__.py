"""Episodic memory: per-close post-mortems stored as retrievable learnings.

On every closed paper trade a cheap LLM post-mortem writes one structured ``learning``
keyed by a numeric feature *fingerprint*. Future ``create_plan`` calls retrieve the
top-k most-similar learnings (cosine over the fingerprint) as non-binding context, so
the strategist sees how past trades in similar conditions actually played out.

The retrieval is a numeric-vector cosine (pgvector ``<=>``), not a text embedding: it
needs no embedding-model dependency and stays interpretable. The raw context is kept so
text embeddings can be backfilled later if trade density ever justifies them.
"""
