"""
Auth API routes — login and user registration.

Account model:
  - ONE admin account, pre-seeded in the database (never created via API).
  - Normal users self-register via POST /auth/register (public, role always 'user').
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from backend.dependencies import create_token, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ── Schemas ────────────────────────────────────────────────

class LoginRequest(BaseModel):
    # 支持用户名或邮箱登录（字段名沿用 username，后端做 username OR email 匹配）
    username: str
    password: str


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    email: str
    password: str
    # No role field on purpose: self-signup always creates a normal user.
    # extra="forbid" rejects smuggled fields (e.g. "role": "admin") with 422.
    # The single admin account is pre-seeded and cannot be created via API.


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


# ── Routes ─────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """User login — returns JWT token."""
    import os
    from passlib.hash import bcrypt

    db = await _get_db()
    try:
        user = await db.fetchrow(
            "SELECT id, username, password_hash, role, is_active FROM users "
            "WHERE username = $1 OR email = $1",
            req.username,
        )
        if not user:
            raise HTTPException(401, "用户名或密码错误")

        if not user["is_active"]:
            raise HTTPException(403, "账号已被禁用，请联系管理员")

        if not bcrypt.verify(req.password, user["password_hash"]):
            raise HTTPException(401, "用户名或密码错误")

        token = create_token(user["id"], user["username"], user["role"])
        return TokenResponse(
            access_token=token,
            username=user["username"],
            role=user["role"],
        )
    finally:
        await db.close()


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest):
    """Public self-signup — always creates a normal user (role='user').

    The single admin account is pre-seeded; there is no API path to create one.
    """
    from passlib.hash import bcrypt

    if len(req.username) < 2 or len(req.username) > 128:
        raise HTTPException(400, "用户名需要 2-128 个字符")
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if not req.email or "@" not in req.email or len(req.email) > 128:
        raise HTTPException(400, "邮箱格式不正确")

    db = await _get_db()
    try:
        existing = await db.fetchrow("SELECT id FROM users WHERE username = $1", req.username)
        if existing:
            raise HTTPException(409, "用户名已存在")

        existing_email = await db.fetchrow("SELECT id FROM users WHERE email = $1", req.email)
        if existing_email:
            raise HTTPException(409, "邮箱已被注册")

        password_hash = bcrypt.hash(req.password)

        user = await db.fetchrow(
            """
            INSERT INTO users (username, email, password_hash, role)
            VALUES ($1, $2, $3, 'user')
            RETURNING id, username, role
            """,
            req.username, req.email, password_hash,
        )

        token = create_token(user["id"], user["username"], user["role"])
        logger.info(f"User self-registered: {req.username}")
        return TokenResponse(
            access_token=token,
            username=user["username"],
            role=user["role"],
        )
    finally:
        await db.close()


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    """Get current user info."""
    return {
        "user_id": current_user["user_id"],
        "username": current_user["username"],
        "role": current_user["role"],
    }


# ── DB helper ──────────────────────────────────────────────

from backend.config import get_settings


async def _get_db() -> asyncpg.Connection:
    return await asyncpg.connect(get_settings().database_url)
