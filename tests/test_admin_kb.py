"""
Tests for admin KB API endpoints.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from backend.api.v1.admin_kb import router, VALID_COLLECTIONS


class TestAdminKBRouter:
    def test_router_has_prefix(self):
        assert router.prefix == "/admin/kb"

    def test_valid_collections(self):
        assert VALID_COLLECTIONS == {"law": "kb_law", "case": "kb_case", "template": "kb_template"}

    def test_has_crud_routes(self):
        route_paths = {r.path for r in router.routes}
        expected = {
            "/admin/kb/{collection}",
            "/admin/kb/{collection}/{item_id}",
            "/admin/kb/reindex",
        }
        assert expected.issubset(route_paths), f"Missing: {expected - route_paths}"
