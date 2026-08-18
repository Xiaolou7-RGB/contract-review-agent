"""
Tests for QA confidence re-routing (二次路由): CONFIDENT / WEAK / EMPTY.

Pure functions + prompt-wiring only (no network, no DB).
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.agents.contract_qa.context_builder import (
    LAW_CONFIDENT_THRESHOLD,
    classify_law_confidence,
)
from backend.agents.contract_qa.qa_service import (
    build_prompt_messages,
    NO_LAW_NOTE,
    WEAK_CONFIDENCE_NOTE,
)


def _hit(conf: float) -> dict:
    return {
        "id": f"law-{conf}",
        "article_no": "580",
        "chapter": "合同编",
        "confidence": conf,
        "content": "某条文内容",
    }


def _ctx(**overrides) -> dict:
    ctx = {
        "review": {"original_filename": "t.docx", "contract_type": "采购合同"},
        "clauses": [], "cards": [], "evidence": [], "revisions": [],
        "law_hits": [], "citations": [], "law_empty": True,
        "confidence_tier": "NA",
    }
    ctx.update(overrides)
    return ctx


class TestClassifyLawConfidence:
    def test_empty(self):
        assert classify_law_confidence([]) == "EMPTY"

    def test_confident_at_threshold(self):
        assert classify_law_confidence([_hit(LAW_CONFIDENT_THRESHOLD)]) == "CONFIDENT"

    def test_confident_above_threshold(self):
        assert classify_law_confidence([_hit(0.91)]) == "CONFIDENT"

    def test_weak_just_below_threshold(self):
        assert classify_law_confidence([_hit(0.69)]) == "WEAK"

    def test_top_hit_decides(self):
        # only the top confidence matters
        assert classify_law_confidence([_hit(0.95), _hit(0.31)]) == "CONFIDENT"
        assert classify_law_confidence([_hit(0.31), _hit(0.55)]) == "WEAK"


class TestWeakConfidenceNote:
    def test_weak_adds_note(self):
        ctx = _ctx(law_empty=False, confidence_tier="WEAK")
        msgs = build_prompt_messages(ctx, [], "违约金有效吗")
        system = msgs[0].content
        assert WEAK_CONFIDENCE_NOTE.strip() in system
        assert NO_LAW_NOTE.strip() not in system

    def test_confident_adds_no_note(self):
        ctx = _ctx(law_empty=False, confidence_tier="CONFIDENT")
        msgs = build_prompt_messages(ctx, [], "违约金有效吗")
        system = msgs[0].content
        assert WEAK_CONFIDENCE_NOTE.strip() not in system
        assert NO_LAW_NOTE.strip() not in system

    def test_empty_uses_no_law_note_not_weak(self):
        ctx = _ctx(law_empty=True, confidence_tier="EMPTY")
        msgs = build_prompt_messages(ctx, [], "违约金有效吗")
        system = msgs[0].content
        assert NO_LAW_NOTE.strip() in system
        assert WEAK_CONFIDENCE_NOTE.strip() not in system
