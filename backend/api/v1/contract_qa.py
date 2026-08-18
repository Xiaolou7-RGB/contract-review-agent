"""
Contract QA API routes — /contract/qa/*
Session management, question submission, SSE answer streaming.

Auth model:
  - Optional JWT everywhere (demo-mode fallback): when no valid token is
    present, the acting user defaults to the contract/session owner —
    consistent with the app's existing auth-less endpoints (upload hardcodes
    user_id=1, report/stream do not enforce auth). When a VALID token for a
    different user is presented, ownership is still enforced (403).
  - SSE stream endpoint: GET + `token` query param (EventSource can't set headers, K-8)
"""
from __future__ import annotations

import json
import logging
import os

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.agents.contract_qa.qa_service import stream_answer
from backend.api.v1.contract import _sse_event  # reuse SSE frame formatting
from backend.config import get_settings
from backend.dependencies import decode_token, get_optional_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contract/qa", tags=["contract-qa"])

DATABASE_URL = get_settings().database_url


async def _get_db() -> asyncpg.Connection:
    return await asyncpg.connect(DATABASE_URL)


# ── Pydantic schemas ────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    contract_id: int


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


# ── Ownership helpers (scope guard) ─────────────────────────

async def _assert_session_ownership(db: asyncpg.Connection, session_id: int, user_id: int | None) -> dict:
    """Session must exist; if a user is authenticated, it (and its contract) must belong to them.

    user_id=None → demo mode (no valid token): skip ownership enforcement,
    matching the app's auth-less endpoints. Existence is still checked.
    """
    row = await db.fetchrow(
        "SELECT s.id, s.contract_id, s.user_id, r.user_id AS contract_owner "
        "FROM contract_qa_sessions s "
        "JOIN contract_reviews r ON r.id = s.contract_id "
        "WHERE s.id = $1",
        session_id,
    )
    if not row:
        raise HTTPException(404, "Session not found")
    if user_id is not None and (row["user_id"] != user_id or row["contract_owner"] != user_id):
        raise HTTPException(403, "No access to this session")
    return dict(row)


async def _assert_contract_access(db: asyncpg.Connection, contract_id: int, user_id: int | None) -> int:
    """Contract must exist; authenticated users must own it.

    Returns the acting user id (= contract owner in demo mode), mirroring
    the permission model of create_session.
    """
    review = await db.fetchrow(
        "SELECT id, user_id FROM contract_reviews WHERE id = $1", contract_id
    )
    if not review:
        raise HTTPException(404, "Contract not found")
    if user_id is not None and review["user_id"] != user_id:
        raise HTTPException(403, "No access to this contract")
    return review["user_id"]


# ── Routes ──────────────────────────────────────────────────

@router.post("/session")
async def create_session(req: CreateSessionRequest, user: dict | None = Depends(get_optional_user)):
    """Open a Q&A session bound to a contract.

    Demo mode (no valid token): acts as the contract owner.
    Authenticated: enforces contract ownership (403 otherwise).
    """
    db = await _get_db()
    try:
        review = await db.fetchrow(
            "SELECT id, user_id, original_filename FROM contract_reviews WHERE id = $1",
            req.contract_id,
        )
        if not review:
            raise HTTPException(404, "Contract not found")
        if user is not None and review["user_id"] != user["user_id"]:
            raise HTTPException(403, "No access to this contract")
        acting_user_id = user["user_id"] if user else review["user_id"]

        title = f"问答 - {review['original_filename']}"[:256]
        row = await db.fetchrow(
            "INSERT INTO contract_qa_sessions (contract_id, user_id, title) "
            "VALUES ($1, $2, $3) RETURNING id, created_at",
            req.contract_id, acting_user_id, title,
        )
        logger.info(f"QA session created: id={row['id']} contract={req.contract_id} user={acting_user_id}")
        return {"session_id": row["id"], "contract_id": req.contract_id, "title": title}
    finally:
        await db.close()


@router.get("/contract/{contract_id}/resume")
async def resume_session(contract_id: int, user: dict | None = Depends(get_optional_user)):
    """Resolve the most recently active QA session for a contract (thread_id resolution).

    Returns ``{"session_id": null}`` when the contract has no session yet —
    the frontend then falls back to POST /session.
    """
    db = await _get_db()
    try:
        acting_user_id = await _assert_contract_access(
            db, contract_id, user["user_id"] if user else None
        )
        row = await db.fetchrow(
            "SELECT id, title FROM contract_qa_sessions "
            "WHERE contract_id = $1 AND user_id = $2 "
            "ORDER BY updated_at DESC, id DESC LIMIT 1",
            contract_id, acting_user_id,
        )
        if not row:
            return {"session_id": None, "contract_id": contract_id, "title": None}
        return {"session_id": row["id"], "contract_id": contract_id, "title": row["title"]}
    finally:
        await db.close()


@router.get("/contract/{contract_id}/sessions")
async def list_sessions(contract_id: int, user: dict | None = Depends(get_optional_user)):
    """All QA sessions of a contract (newest activity first), with message counts."""
    db = await _get_db()
    try:
        acting_user_id = await _assert_contract_access(
            db, contract_id, user["user_id"] if user else None
        )
        rows = await db.fetch(
            "SELECT s.id, s.title, s.updated_at, COUNT(m.id) AS message_count "
            "FROM contract_qa_sessions s "
            "LEFT JOIN contract_qa_messages m ON m.session_id = s.id "
            "WHERE s.contract_id = $1 AND s.user_id = $2 "
            "GROUP BY s.id, s.title, s.updated_at "
            "ORDER BY s.updated_at DESC, s.id DESC",
            contract_id, acting_user_id,
        )
        sessions = [
            {
                "id": r["id"],
                "title": r["title"],
                "message_count": r["message_count"],
                "updated_at": str(r["updated_at"]),
            }
            for r in rows
        ]
        return {"contract_id": contract_id, "sessions": sessions}
    finally:
        await db.close()


@router.get("/session/{session_id}/messages")
async def list_messages(session_id: int, user: dict | None = Depends(get_optional_user)):
    """Full message history of a session (including citations)."""
    db = await _get_db()
    try:
        await _assert_session_ownership(db, session_id, user["user_id"] if user else None)
        rows = await db.fetch(
            "SELECT id, role, content, citations, status, error_message, created_at "
            "FROM contract_qa_messages WHERE session_id = $1 ORDER BY id",
            session_id,
        )
        messages = []
        for r in rows:
            d = dict(r)
            d["citations"] = json.loads(d["citations"]) if d["citations"] else None
            d["created_at"] = str(d["created_at"])
            messages.append(d)
        return {"session_id": session_id, "messages": messages}
    finally:
        await db.close()


@router.delete("/session/{session_id}")
async def delete_session(session_id: int, user: dict | None = Depends(get_optional_user)):
    """Delete a QA session; its messages cascade via FK. Refuses while answers are in flight."""
    db = await _get_db()
    try:
        await _assert_session_ownership(db, session_id, user["user_id"] if user else None)
        active = await db.fetchval(
            "SELECT COUNT(*) FROM contract_qa_messages "
            "WHERE session_id = $1 AND status IN ('pending', 'streaming')",
            session_id,
        )
        if active:
            raise HTTPException(409, "该会话还有正在生成中的回答，请稍后再删除")
        await db.execute("DELETE FROM contract_qa_sessions WHERE id = $1", session_id)
        logger.info(f"QA session deleted: id={session_id}")
        return {"status": "deleted", "id": session_id}
    finally:
        await db.close()


@router.post("/session/{session_id}/ask")
async def ask_question(session_id: int, req: AskRequest, user: dict | None = Depends(get_optional_user)):
    """Submit a question. Persists user message + pending assistant placeholder.

    Returns the assistant placeholder message_id; the answer is then consumed
    via GET /message/{message_id}/stream (SSE).
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Question required")

    db = await _get_db()
    try:
        await _assert_session_ownership(db, session_id, user["user_id"] if user else None)

        await db.execute(
            "INSERT INTO contract_qa_messages (session_id, role, content, status) "
            "VALUES ($1, 'user', $2, 'completed')",
            session_id, question,
        )
        row = await db.fetchrow(
            "INSERT INTO contract_qa_messages (session_id, role, content, status) "
            "VALUES ($1, 'assistant', '', 'pending') RETURNING id",
            session_id,
        )
        # Auto-name the session from its first question (title starts as "问答 - <file>").
        # VARCHAR(256) counts bytes, so cap at 40 chars (≤120 bytes for CJK).
        sess = await db.fetchrow(
            "SELECT title FROM contract_qa_sessions WHERE id = $1", session_id
        )
        if sess and sess["title"].startswith("问答 - "):
            await db.execute(
                "UPDATE contract_qa_sessions SET title = $2, updated_at = NOW() WHERE id = $1",
                session_id, question[:40],
            )
        else:
            await db.execute(
                "UPDATE contract_qa_sessions SET updated_at = NOW() WHERE id = $1", session_id
            )
        return {"session_id": session_id, "message_id": row["id"]}
    finally:
        await db.close()


@router.get("/message/{message_id}/stream")
async def stream_message(message_id: int, token: str = Query(...)):
    """SSE stream of the grounded answer. Events: citations / delta / done / error.

    Token is optional in demo mode: an invalid/missing token falls back to
    acting as the session owner; a VALID token still enforces ownership.
    """
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub", 0)) or None
    except HTTPException:
        user_id = None  # demo mode

    db = await _get_db()
    try:
        msg = await db.fetchrow(
            "SELECT m.id, m.status, m.session_id, s.contract_id, s.user_id, "
            "r.user_id AS contract_owner "
            "FROM contract_qa_messages m "
            "JOIN contract_qa_sessions s ON s.id = m.session_id "
            "JOIN contract_reviews r ON r.id = s.contract_id "
            "WHERE m.id = $1",
            message_id,
        )
        if not msg:
            raise HTTPException(404, "Message not found")
        if user_id is not None and (msg["user_id"] != user_id or msg["contract_owner"] != user_id):
            raise HTTPException(403, "No access to this message")
        if msg["status"] in ("completed", "streaming"):
            raise HTTPException(409, "Answer already generated or in progress")

        # The question is the latest user message before this assistant placeholder
        q_row = await db.fetchrow(
            "SELECT content FROM contract_qa_messages "
            "WHERE session_id = $1 AND role = 'user' AND id < $2 "
            "ORDER BY id DESC LIMIT 1",
            msg["session_id"], message_id,
        )
        question = q_row["content"] if q_row else ""
        if not question:
            raise HTTPException(400, "No question found for this message")

        await db.execute(
            "UPDATE contract_qa_messages SET status = 'streaming' WHERE id = $1", message_id
        )
    finally:
        await db.close()

    async def _event_stream():
        try:
            async for event in stream_answer(message_id, question):
                yield await _sse_event(event["type"], event)
        except Exception as e:
            logger.exception(f"QA SSE stream failed for message {message_id}")
            yield await _sse_event("error", {"message": str(e)})

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
