"""
Dependency injection — JWT auth and role-based access control.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

security = HTTPBearer(auto_error=False)


def create_token(user_id: int, username: str, role: str) -> str:
    """Create a JWT access token."""
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(401, f"Invalid token: {e}")


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """FastAPI dependency: extract current user from Bearer token."""
    token = None
    if credentials:
        token = credentials.credentials
    else:
        # Fallback: check Authorization header directly
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]

    if not token:
        raise HTTPException(401, "Authentication required")

    payload = decode_token(token)
    return {
        "user_id": int(payload.get("sub", 0)),
        "username": payload.get("username", ""),
        "role": payload.get("role", "user"),
    }


async def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict | None:
    """FastAPI dependency: extract user if authenticated, else None."""
    try:
        return await get_current_user(request, credentials)
    except HTTPException:
        return None


async def require_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """FastAPI dependency: require admin role."""
    if current_user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return current_user
