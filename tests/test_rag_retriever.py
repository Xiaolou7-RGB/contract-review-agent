"""
Tests for rag_retriever — routing, evidence building, degradation.
"""
from __future__ import annotations

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.agents.contract_review.rag_retriever import (
    _route_collections,
    _build_evidence,
    _build_search_query,
    CASE_ROUTE_MAP,
    _case_route_for,
)


class TestRouteCollections:
    def test_contract_invalid_risk_goes_to_civil_code(self):
        cols = _route_collections("合同无效风险", "legal")
        assert "civil_code_hybrid" in cols

    def test_financial_risk_goes_to_civil_code(self):
        cols = _route_collections("付款条件不合理", "financial")
        assert "civil_code_hybrid" in cols

    def test_compliance_risk_goes_to_civil_code(self):
        cols = _route_collections("数据保护合规", "compliance")
        assert "civil_code_hybrid" in cols

    def test_unknown_risk_uses_dimension_routing(self):
        cols = _route_collections("", "financial")
        assert "civil_code_hybrid" in cols

    def test_unknown_risk_and_dim_returns_default(self):
        cols = _route_collections("", "")
        assert cols == ["civil_code_hybrid", "kb_law", "kb_case", "kb_template"]


class TestBuildEvidence:
    def test_high_confidence_not_human_review(self):
        results = [{"id": "law-001", "content": "违约金不得超过实际损失的30%", "confidence": 0.90}]
        evidence = _build_evidence("clause-1", results, threshold=0.75)
        assert len(evidence) == 1
        assert evidence[0]["is_human_review"] is False
        assert evidence[0]["source_id"] == "law-001"

    def test_low_confidence_is_human_review(self):
        results = [{"id": "law-002", "content": "相关法条内容...", "confidence": 0.50}]
        evidence = _build_evidence("clause-1", results, threshold=0.75)
        assert evidence[0]["is_human_review"] is True

    def test_long_quote_truncated(self):
        long_text = "法条内容" * 200  # ~800 chars
        results = [{"id": "law-003", "content": long_text, "confidence": 0.80}]
        evidence = _build_evidence("clause-1", results)
        assert len(evidence[0]["quote"]) <= 303  # 300 + "..."


class TestBuildSearchQuery:
    def test_prefers_search_query(self):
        card = {"risk_type": "违约风险", "suggestion": "违约金过高", "search_query": "违约金超过法定上限"}
        clause = {"title": "违约责任条款"}
        query = _build_search_query(card, clause)
        assert query == "违约金超过法定上限"

    def test_falls_back_to_suggestion_when_no_search_query(self):
        card = {"risk_type": "违约风险", "suggestion": "违约金过高"}
        clause = {"title": "违约责任条款"}
        query = _build_search_query(card, clause)
        assert query == "违约金过高"

    def test_truncates_long_query(self):
        card = {"risk_type": "风险", "suggestion": "X" * 1000}
        clause = {"title": "条款标题"}
        query = _build_search_query(card, clause)
        assert len(query) <= 512


class TestCaseRoute:
    def test_breach_risk_needs_case(self):
        assert CASE_ROUTE_MAP["违约风险"]["need_case"] is True

    def test_financial_risk_no_case(self):
        assert CASE_ROUTE_MAP["财务风险"]["need_case"] is False

    def test_all_true_routes_have_dimension_and_hint(self):
        for rt, route in CASE_ROUTE_MAP.items():
            if route["need_case"]:
                assert route.get("dimension"), rt
                assert route.get("hint"), rt

    def test_known_risk_type_returns_route(self):
        route = _case_route_for("担保无效")
        assert route is not None
        assert route["need_case"] is True

    def test_unknown_risk_type_returns_none(self):
        assert _case_route_for("不存在的风险类型") is None


class TestRefineCaseQuery:
    def test_refine_falls_back_on_llm_failure(self, monkeypatch):
        import asyncio
        from backend.agents.contract_review import rag_retriever as rr

        def boom():
            raise RuntimeError("LLM down")

        monkeypatch.setattr(rr, "_case_refine_llm", boom)
        route = {"need_case": True, "dimension": "合同违约责任纠纷", "hint": "违约金标准 违约认定"}
        card = {"risk_type": "违约风险", "search_query": "违约金超过30%", "suggestion": "违约金过高"}
        clause = {"content": "违约金为35%"}
        result = asyncio.run(rr._refine_case_query(route, card, clause))
        assert "违约金标准" in result  # fallback = hint + search_query

    def test_refine_falls_back_to_suggestion_when_no_hint_or_query(self, monkeypatch):
        import asyncio
        from backend.agents.contract_review import rag_retriever as rr

        def boom():
            raise RuntimeError("LLM down")

        monkeypatch.setattr(rr, "_case_refine_llm", boom)
        route = {"need_case": True, "dimension": "合同违约责任纠纷", "hint": ""}
        card = {"risk_type": "违约风险", "search_query": "", "suggestion": "违约金过高应调减"}
        clause = {"content": "违约金为35%"}
        result = asyncio.run(rr._refine_case_query(route, card, clause))
        assert "违约金过高应调减" in result
