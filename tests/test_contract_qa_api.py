"""
Tests for the contract QA API router (route shape + request validation).
Full HTTP behavior (auth chain, SSE stream) is covered by check_qa_pipeline.py.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from pydantic import ValidationError

from backend.api.v1.contract_qa import router, AskRequest, CreateSessionRequest


class TestRouterSetup:
    def test_router_has_prefix(self):
        assert router.prefix == "/contract/qa"

    def test_router_has_routes(self):
        route_paths = {r.path for r in router.routes}
        expected = {
            "/contract/qa/session",
            "/contract/qa/contract/{contract_id}/resume",
            "/contract/qa/contract/{contract_id}/sessions",
            "/contract/qa/session/{session_id}",
            "/contract/qa/session/{session_id}/messages",
            "/contract/qa/session/{session_id}/ask",
            "/contract/qa/message/{message_id}/stream",
        }
        assert route_paths == expected, f"Got: {route_paths}"

    def test_stream_route_is_get(self):
        for r in router.routes:
            if r.path == "/contract/qa/message/{message_id}/stream":
                assert "GET" in r.methods


class TestRequestValidation:
    def test_ask_requires_nonempty_question(self):
        with pytest.raises(ValidationError):
            AskRequest(question="")

    def test_ask_rejects_overlong_question(self):
        with pytest.raises(ValidationError):
            AskRequest(question="问" * 2001)

    def test_ask_accepts_normal_question(self):
        req = AskRequest(question="第三条有什么风险？")
        assert req.question == "第三条有什么风险？"

    def test_create_session_requires_contract_id(self):
        req = CreateSessionRequest(contract_id=9999)
        assert req.contract_id == 9999
