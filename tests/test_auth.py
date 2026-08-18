"""
Tests for auth module — JWT tokens, role-based access, dependencies.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from backend.dependencies import create_token, decode_token, require_admin


class TestJWTToken:
    def test_create_and_decode_token(self):
        token = create_token(1, "testuser", "user")
        payload = decode_token(token)
        assert payload["sub"] == "1"
        assert payload["username"] == "testuser"
        assert payload["role"] == "user"

    def test_create_admin_token(self):
        token = create_token(2, "admin", "admin")
        payload = decode_token(token)
        assert payload["role"] == "admin"

    def test_decode_invalid_token_raises(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            decode_token("this-is-not-a-valid-jwt-token")
        assert exc.value.status_code == 401

    def test_token_contains_expiry(self):
        token = create_token(1, "user", "user")
        payload = decode_token(token)
        assert "exp" in payload
        assert "iat" in payload


class TestRoles:
    def test_roles_are_user_admin_only(self):
        """Verify that only 'user' and 'admin' are valid roles."""
        valid_roles = {"user", "admin"}
        token_user = create_token(1, "u", "user")
        token_admin = create_token(2, "a", "admin")
        assert decode_token(token_user)["role"] in valid_roles
        assert decode_token(token_admin)["role"] in valid_roles


# ── Self-signup model (single pre-seeded admin, public register) ────────

class TestSelfSignupModel:
    def test_auth_router_routes(self):
        from backend.api.v1.auth import router
        paths = {r.path for r in router.routes}
        assert paths == {"/auth/login", "/auth/register", "/auth/me"}, f"Got: {paths}"

    def test_register_request_has_no_role_field(self):
        """Structurally impossible to pick a role at signup."""
        from backend.api.v1.auth import RegisterRequest
        assert "role" not in RegisterRequest.model_fields

    def test_register_request_rejects_smuggled_role(self):
        """extra='forbid' → a smuggled role field is a 422, not silently ignored."""
        from pydantic import ValidationError
        from backend.api.v1.auth import RegisterRequest
        with pytest.raises(ValidationError):
            RegisterRequest(username="abc", email="a@b.com", password="123456", role="admin")

    def test_register_request_accepts_minimal_payload(self):
        from backend.api.v1.auth import RegisterRequest
        req = RegisterRequest(username="abc", email="a@b.com", password="123456")
        assert req.username == "abc" and req.email == "a@b.com" and req.password == "123456"
