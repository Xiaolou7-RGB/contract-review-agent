"""
Smoke tests for the LangGraph pipeline.
Tests graph construction, node wiring, and basic state flow.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from backend.agents.contract_review.graph import (
    build_contract_review_graph,
    get_contract_review_app,
    get_memory_saver,
)
from backend.agents.contract_review.schemas import ContractReviewState


class TestGraphConstruction:
    def test_graph_builds_without_error(self):
        graph = build_contract_review_graph()
        assert graph is not None

    def test_graph_has_four_nodes(self):
        graph = build_contract_review_graph()
        nodes = list(graph._nodes.keys()) if hasattr(graph, '_nodes') else graph.nodes
        # Expected nodes: parse, review, retrieve, revise (+ __start__, __end__)
        node_names = set(nodes.keys()) if isinstance(nodes, dict) else set(nodes)
        expected = {"parse", "review", "retrieve", "revise"}
        assert expected.issubset(node_names) or expected == node_names, f"Nodes: {node_names}"

    def test_compiled_app_returns_runnable(self):
        app = get_contract_review_app()
        assert app is not None
        # Should have ainvoke method
        assert hasattr(app, "ainvoke")

    def test_memory_saver_is_reused(self):
        saver1 = get_memory_saver("test_graph")
        saver2 = get_memory_saver("test_graph")
        assert saver1 is saver2

    def test_memory_saver_different_name_is_separate(self):
        saver1 = get_memory_saver("test_a")
        saver2 = get_memory_saver("test_b")
        assert saver1 is not saver2


class TestStateSchema:
    def test_state_has_required_keys(self):
        """Verify state TypedDict contains all required keys."""
        required_keys = {
            "contract_id", "user_id", "text", "contract_type",
            "clauses", "review_cards", "degraded_review",
            "evidence_map", "revisions",
            "iteration_count", "max_iterations", "visited_clause_ids",
            "error", "status",
        }
        # ContractReviewState is a TypedDict — check its __annotations__
        annotations = ContractReviewState.__annotations__
        assert required_keys.issubset(set(annotations.keys())), \
            f"Missing keys: {required_keys - set(annotations.keys())}"


# ── Progress callback wiring in the direct pipeline ──────────
# Regression guard (2026-08-08): the API layer's _progress_callback was
# defined but never passed to run_contract_review_direct, so the DB status
# sat on 'parsing' until it jumped to 'completed' and the frontend progress
# bar froze then jumped. These tests pin the callback contract.

class TestDirectPipelineProgressCallback:
    def _stub_nodes(self, monkeypatch, fail_at: str | None = None):
        import backend.agents.contract_review.graph as g

        def make(name):
            async def stub(state):
                if fail_at == name:
                    raise RuntimeError(f"{name} boom")
                return {}
            return stub

        monkeypatch.setattr(g, "clause_parser_node", make("parse"))
        monkeypatch.setattr(g, "rule_check_node", make("rule_check"))
        monkeypatch.setattr(g, "multi_dim_review_node", make("review"))
        monkeypatch.setattr(g, "rag_retriever_node", make("retrieve"))
        monkeypatch.setattr(g, "revision_writer_node", make("revise"))

    def test_started_completed_order_for_all_nodes(self, monkeypatch):
        from backend.agents.contract_review.graph import run_contract_review_direct

        self._stub_nodes(monkeypatch)
        events: list[tuple[str, str]] = []

        async def cb(name, event, data):
            events.append((name, event))

        import asyncio
        asyncio.run(run_contract_review_direct(1, 1, "合同文本", progress_callback=cb))
        assert events == [
            ("parse", "started"), ("parse", "completed"),
            ("rule_check", "started"), ("rule_check", "completed"),
            ("review", "started"), ("review", "completed"),
            ("retrieve", "started"), ("retrieve", "completed"),
            ("revise", "started"), ("revise", "completed"),
        ]

    def test_sync_callback_also_supported(self, monkeypatch):
        from backend.agents.contract_review.graph import run_contract_review_direct

        self._stub_nodes(monkeypatch)
        events: list[str] = []

        def cb(name, event, data):
            events.append(f"{name}:{event}")

        import asyncio
        asyncio.run(run_contract_review_direct(1, 1, "合同文本", progress_callback=cb))
        assert len(events) == 10

    def test_node_failure_fires_failed_and_propagates(self, monkeypatch):
        from backend.agents.contract_review.graph import run_contract_review_direct

        self._stub_nodes(monkeypatch, fail_at="review")
        events: list[tuple[str, str]] = []

        async def cb(name, event, data):
            events.append((name, event))

        import asyncio
        with pytest.raises(RuntimeError, match="review boom"):
            asyncio.run(run_contract_review_direct(1, 1, "合同文本", progress_callback=cb))
        assert ("review", "started") in events
        assert ("review", "failed") in events
        assert ("retrieve", "started") not in events

    def test_raising_callback_does_not_break_pipeline(self, monkeypatch):
        from backend.agents.contract_review.graph import run_contract_review_direct

        self._stub_nodes(monkeypatch)

        async def cb(name, event, data):
            raise RuntimeError("callback down")

        import asyncio
        state = asyncio.run(
            run_contract_review_direct(1, 1, "合同文本", progress_callback=cb)
        )
        assert state["status"] == "pending"  # pipeline ran to the end

    def test_no_callback_still_works(self, monkeypatch):
        from backend.agents.contract_review.graph import run_contract_review_direct

        self._stub_nodes(monkeypatch)

        import asyncio
        state = asyncio.run(run_contract_review_direct(1, 1, "合同文本"))
        assert isinstance(state, dict)
