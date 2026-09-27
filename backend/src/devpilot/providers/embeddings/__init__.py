"""Embedding providers: the port and its adapters.

An embedding provider turns text into a fixed-dimension vector. The real
adapter wraps a sentence-transformers model and loads it lazily; the fake
adapter is deterministic and needs no model or network, and is what tests and
CI use. Both satisfy the same protocol, so the indexing pipeline and the search
service depend on the abstraction, never on a concrete model.
"""
