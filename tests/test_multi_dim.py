"""
Tests for multi_dim_review — dimension routing, fan-out/fan-in, degradation.
"""
from __future__ import annotations

import pytest

from backend.agents.contract_review.multi_dim_review import (
    get_active_dimensions,
    merge_review_cards,
    DIMENSION_ROUTING,
)
from backend.core.context_manager import (
    estimate_tokens,
    trim_clauses_for_budget,
    trim_cards_for_budget,
)


class TestDimensionRouting:
    def test_sale_contract_uses_4_dims(self):
        dims = get_active_dimensions("买卖")
        keys = {d["key"] for d in dims}
        assert keys == {"legal", "compliance", "financial", "rights_obligations"}

    def test_service_contract_uses_4_dims(self):
        dims = get_active_dimensions("服务")
        keys = {d["key"] for d in dims}
        assert keys == {"legal", "compliance", "financial", "rights_obligations"}

    def test_labor_contract_uses_3_dims(self):
        dims = get_active_dimensions("劳动")
        keys = {d["key"] for d in dims}
        assert keys == {"legal", "compliance", "rights_obligations"}

    def test_loan_contract_uses_3_dims(self):
        dims = get_active_dimensions("借款")
        keys = {d["key"] for d in dims}
        assert keys == {"legal", "financial", "rights_obligations"}

    def test_nda_contract_uses_2_dims(self):
        dims = get_active_dimensions("保密")
        keys = {d["key"] for d in dims}
        assert keys == {"legal", "rights_obligations"}

    def test_unknown_contract_defaults_to_4_dims(self):
        dims = get_active_dimensions("其他")
        keys = {d["key"] for d in dims}
        assert keys == {"legal", "compliance", "financial", "rights_obligations"}

    def test_every_contract_type_has_routing(self):
        for ctype in DIMENSION_ROUTING:
            dims = get_active_dimensions(ctype)
            assert len(dims) >= 1


class TestMergeReviewCards:
    def test_same_clause_keeps_highest_level(self):
        cards = [
            {"clause_id": "abc", "dimension": "legal", "level": "低", "score": 0.2, "suggestion": "低风险", "risk_type": ""},
            {"clause_id": "abc", "dimension": "financial", "level": "高", "score": 0.9, "suggestion": "高风险", "risk_type": ""},
        ]
        merged = merge_review_cards(cards)
        assert len(merged) == 1
        assert merged[0]["level"] == "高"
        assert merged[0]["score"] == 0.9

    def test_different_clauses_kept_separate(self):
        cards = [
            {"clause_id": "abc", "dimension": "legal", "level": "中", "score": 0.5, "suggestion": "", "risk_type": ""},
            {"clause_id": "def", "dimension": "compliance", "level": "高", "score": 0.8, "suggestion": "", "risk_type": ""},
        ]
        merged = merge_review_cards(cards)
        assert len(merged) == 2
        ids = {m["clause_id"] for m in merged}
        assert ids == {"abc", "def"}

    def test_result_sorted_high_to_low(self):
        cards = [
            {"clause_id": "a", "dimension": "legal", "level": "低", "score": 0.1, "suggestion": "", "risk_type": ""},
            {"clause_id": "b", "dimension": "financial", "level": "高", "score": 0.9, "suggestion": "", "risk_type": ""},
            {"clause_id": "c", "dimension": "compliance", "level": "中", "score": 0.5, "suggestion": "", "risk_type": ""},
        ]
        merged = merge_review_cards(cards)
        assert merged[0]["level"] == "高"
        assert merged[1]["level"] == "中"
        assert merged[2]["level"] == "低"


class TestContextManager:
    def test_estimate_tokens(self):
        text = "这是一段中文测试文本，用于估算token数量。"
        tokens = estimate_tokens(text)
        assert 5 <= tokens <= 30

    def test_trim_clauses_within_budget(self):
        clauses = [
            {"content": "短条款", "seq_no": 1},
            {"content": "另一个短条款", "seq_no": 2},
        ]
        result = trim_clauses_for_budget(clauses, max_tokens=100)
        assert len(result) == 2
        # content should be unchanged
        assert "..." not in result[0]["content"]

    def test_trim_clauses_over_budget(self):
        clauses = [
            {"content": "X" * 20000, "seq_no": 1},
            {"content": "短内容", "seq_no": 2},
        ]
        result = trim_clauses_for_budget(clauses, max_tokens=100)
        assert len(result) == 2
        # The long one should have been trimmed
        assert "[见原文]" in result[0]["content"] or "[truncated]" in result[0]["content"]

    def test_trim_cards_prioritizes_high_risk(self):
        cards = [
            {"clause_id": "a", "level": "低", "suggestion": "低风险描述" * 100},
            {"clause_id": "b", "level": "高", "suggestion": "高风险描述" * 100},
            {"clause_id": "c", "level": "中", "suggestion": "中风险描述" * 100},
        ]
        result = trim_cards_for_budget(cards, max_tokens=30)
        # At least the high risk card should be kept
        kept_ids = {c["clause_id"] for c in result}
        assert "b" in kept_ids  # high risk should always be kept
