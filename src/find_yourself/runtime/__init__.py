"""Runtime layer: model gateway, frozen middleware order and orchestration shim.

This package owns the cold-start model gateway (no fake answers without
credentials), the bounded LangGraph orchestration and the self-built local agent harness and the in-process
task event bus used by SSE. Durable task state is owned by the Workflow shard
(Temporal); this bus is the live, single-process fan-out for connected clients.
"""
