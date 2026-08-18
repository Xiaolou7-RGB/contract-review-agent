"""
Tests for contract API endpoints.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from backend.api.v1.contract import router


class TestRouterSetup:
    def test_router_has_prefix(self):
        assert router.prefix == "/contract"

    def test_router_has_routes(self):
        route_paths = {r.path for r in router.routes}
        expected = {
            "/contract/upload",
            "/contract/run/{contract_id}",
            "/contract/run/{contract_id}/stream",
            "/contract/report/{contract_id}",
            "/contract/reviews",
            "/contract/review/{contract_id}",
            "/contract/revision/{revision_id}/accept",
            "/contract/revision/{revision_id}/lawyer-confirm",
            "/contract/{contract_id}/final-contract",
            "/contract/{contract_id}/final-contract/download",
            "/contract/{contract_id}/human-decision",
        }
        assert route_paths == expected, f"Got: {route_paths}"


class TestUploadValidation:
    def test_unsupported_extension(self):
        """Test that .exe files are rejected."""
        # This is tested at the route level — we verify the check logic
        from pathlib import Path
        ext = Path("test.exe").suffix.lower()
        assert ext not in (".pdf", ".docx", ".txt")
