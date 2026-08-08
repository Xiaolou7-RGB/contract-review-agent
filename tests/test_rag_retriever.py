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
        assert len(cols) == 1  # only civil_code_hybrid until kb_case/kb_template populated


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
    def test_combines_title_risk_suggestion(self):
        card = {"risk_type": "违约风险", "suggestion": "违约金过高"}
        clause = {"title": "违约责任条款"}
        query = _build_search_query(card, clause)
        assert "违约责任条款" in query
        assert "违约风险" in query
        assert "违约金过高" in query

    def test_truncates_long_query(self):
        card = {"risk_type": "风险", "suggestion": "X" * 1000}
        clause = {"title": "条款标题"}
        query = _build_search_query(card, clause)
        assert len(query) <= 512
