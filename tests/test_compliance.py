"""
Tests for compliance module — disclaimer, idempotent ops, lawyer confirmation.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from backend.core.compliance import (
    DISCLAIMER_TEXT,
    DISCLAIMER_SHORT,
    validate_disclaimer_accepted,
    require_disclaimer,
    transition_lawyer_state,
    wrap_report_with_compliance,
)


class TestDisclaimer:
    def test_disclaimer_text_is_not_empty(self):
        assert len(DISCLAIMER_TEXT) > 50

    def test_disclaimer_mentions_ai_and_lawyer(self):
        assert "AI" in DISCLAIMER_TEXT
        assert "律师" in DISCLAIMER_TEXT

    def test_short_disclaimer(self):
        assert len(DISCLAIMER_SHORT) > 10

    def test_validate_disclaimer_accepted_true(self):
        review = {"disclaimer_accepted": True}
        assert validate_disclaimer_accepted(review) is True

    def test_validate_disclaimer_accepted_false(self):
        review = {"disclaimer_accepted": False}
        assert validate_disclaimer_accepted(review) is False

    def test_require_disclaimer_raises_when_not_accepted(self):
        review = {"disclaimer_accepted": False}
        with pytest.raises(PermissionError):
            require_disclaimer(review)

    def test_require_disclaimer_passes_when_accepted(self):
        review = {"disclaimer_accepted": True}
        require_disclaimer(review)  # should not raise


class TestLawyerConfirmation:
    def test_pending_to_reviewed(self):
        new_state = transition_lawyer_state("pending_lawyer_review", "lawyer_review")
        assert new_state == "lawyer_reviewed"

    def test_reviewed_to_accepted(self):
        new_state = transition_lawyer_state("lawyer_reviewed", "accept")
        assert new_state == "accepted"

    def test_pending_direct_to_accepted(self):
        new_state = transition_lawyer_state("pending_lawyer_review", "accept")
        assert new_state == "accepted"

    def test_invalid_transition_raises(self):
        with pytest.raises(ValueError):
            transition_lawyer_state("accepted", "lawyer_review")

    def test_unknown_state_raises(self):
        with pytest.raises(ValueError):
            transition_lawyer_state("unknown", "accept")


class TestReportWrapper:
    def test_adds_disclaimer_field(self):
        report = {"status": "completed", "clauses": []}
        wrapped = wrap_report_with_compliance(report)
        assert "disclaimer" in wrapped
        assert wrapped["disclaimer"] == DISCLAIMER_TEXT

    def test_adds_requires_lawyer_review(self):
        report = {"status": "completed"}
        wrapped = wrap_report_with_compliance(report)
        assert wrapped["requires_lawyer_review"] is True

    def test_preserves_original_fields(self):
        report = {"status": "completed", "contract_type": "买卖", "clauses": [1, 2, 3]}
        wrapped = wrap_report_with_compliance(report)
        assert wrapped["status"] == "completed"
        assert wrapped["contract_type"] == "买卖"
        assert wrapped["clauses"] == [1, 2, 3]
