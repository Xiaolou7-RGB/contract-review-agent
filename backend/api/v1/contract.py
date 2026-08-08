"""
Contract review API routes — /contract/*
Upload, run, stream, report, revision accept, lawyer confirm.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.agents.contract_review.graph import run_contract_review, run_contract_review_direct

# ── Review cards store ──────────────────────────────────────

def _save_review_cards(contract_id: int, cards: list[dict[str, Any]]) -> None:
    pass  # now persisted to DB via _persist_review_cards


async def _load_review_cards_from_db(contract_id: int) -> list[dict[str, Any]]:
    """Load review cards from contract_review_cards table (survives restarts)."""
    db = await _get_db()
    try:
        rows = await db.fetch(
            "SELECT * FROM contract_review_cards WHERE review_id = $1",
            contract_id,
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contract", tags=["contract"])

UPLOAD_DIR = Path("uploads/contracts")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── Database helper ─────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres123@localhost:15432/eduagent")


async def _get_db() -> asyncpg.Connection:
    return await asyncpg.connect(DATABASE_URL)


# ── Pydantic schemas ────────────────────────────────────────

class ContractUploadResponse(BaseModel):
    contract_id: int
    filename: str


class RunResponse(BaseModel):
    run_id: str
    contract_id: int
    status: str


class RevisionAcceptRequest(BaseModel):
    status: str  # accepted / rejected / needs_lawyer
    idempotent_key: str


class LawyerConfirmRequest(BaseModel):
    confirmed: bool


# ── SSE helpers ─────────────────────────────────────────────

async def _sse_event(event: str, data: Any) -> str:
    """Format an SSE event string."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


# ── Routes ──────────────────────────────────────────────────

@router.post("/upload", response_model=ContractUploadResponse)
async def upload_contract(file: UploadFile = File(...)):
    """Upload a PDF/DOCX contract file. Returns contract_id."""
    if not file.filename:
        raise HTTPException(400, "Filename required")

    # Validate extension
    ext = Path(file.filename).suffix.lower()
    if ext not in (".pdf", ".docx", ".txt"):
        raise HTTPException(400, f"Unsupported file type: {ext}. Only PDF, DOCX, TXT allowed.")

    # Save file
    file_id = uuid.uuid4().hex[:12]
    saved_name = f"{file_id}_{file.filename}"
    saved_path = UPLOAD_DIR / saved_name

    content = await file.read()
    saved_path.write_bytes(content)

    # Extract text
    text = ""
    if ext == ".txt":
        text = content.decode("utf-8", errors="replace")
    elif ext == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(str(saved_path)) as pdf:
                parts = []
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        parts.append(page_text)
                text = "\n\n".join(parts)
        except ImportError:
            text = "[PDF parsing requires pdfplumber]"
    elif ext == ".docx":
        try:
            from docx import Document
            doc = Document(str(saved_path))
            parts = [p.text for p in doc.paragraphs if p.text.strip()]
            text = "\n\n".join(parts)
        except ImportError:
            text = "[DOCX parsing requires python-docx]"

    if not text.strip():
        raise HTTPException(400, "Could not extract text from the uploaded file")

    # Insert into database
    db = await _get_db()
    try:
        row = await db.fetchrow(
            """
            INSERT INTO contract_reviews (user_id, filename, original_filename, contract_type, status)
            VALUES ($1, $2, $3, '', 'pending')
            RETURNING id
            """,
            1, saved_name, file.filename,  # user_id=1 (default for now)
        )
        contract_id = row["id"]
        logger.info(f"Contract uploaded: id={contract_id}, file={file.filename}")
    finally:
        await db.close()

    return ContractUploadResponse(contract_id=contract_id, filename=file.filename)


@router.post("/run/{contract_id}", response_model=RunResponse)
async def run_contract_review_endpoint(contract_id: int):
    """Trigger async contract review. Returns run_id for tracking."""
    db = await _get_db()
    try:
        review = await db.fetchrow(
            "SELECT id, filename FROM contract_reviews WHERE id = $1", contract_id
        )
        if not review:
            raise HTTPException(404, "Contract not found")

        # Read the uploaded file
        saved_path = UPLOAD_DIR / review["filename"]
        if not saved_path.exists():
            raise HTTPException(404, "Uploaded file not found on disk")

        # Parse text (same logic as upload)
        ext = Path(review["filename"]).suffix.lower()
        text = ""
        if ext == ".txt":
            text = saved_path.read_text(encoding="utf-8", errors="replace")
        elif ext == ".pdf":
            try:
                import pdfplumber
                with pdfplumber.open(str(saved_path)) as pdf:
                    parts = []
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            parts.append(page_text)
                    text = "\n\n".join(parts)
            except ImportError:
                text = "[PDF parsing requires pdfplumber]"
        elif ext == ".docx":
            try:
                from docx import Document
                doc = Document(str(saved_path))
                parts = [p.text for p in doc.paragraphs if p.text.strip()]
                text = "\n\n".join(parts)
            except ImportError:
                text = "[DOCX parsing requires python-docx]"

        if not text.strip():
            raise HTTPException(400, "Could not extract text from file")

        # Update status
        await db.execute(
            "UPDATE contract_reviews SET status = 'parsing', updated_at = NOW() WHERE id = $1",
            contract_id,
        )
    finally:
        await db.close()

    run_id = uuid.uuid4().hex[:8]

    status_map = {
        "parse": "parsing",
        "review": "reviewing",
        "retrieve": "retrieving",
        "revise": "revising",
    }

    async def _progress_callback(node_name: str, event: str, data: dict[str, Any]):
        """Update DB status on each node start for SSE streaming."""
        if event == "started" and node_name in status_map:
            new_status = status_map[node_name]
            db2 = await _get_db()
            try:
                await db2.execute(
                    "UPDATE contract_reviews SET status = $2, updated_at = NOW() WHERE id = $1",
                    contract_id, new_status,
                )
            finally:
                await db2.close()

    async def _run_pipeline():
        review_cards = []
        try:
            result = await run_contract_review_direct(
                contract_id=contract_id,
                user_id=1,
                text=text,
                progress_callback=_progress_callback,
            )
            review_cards = result.get("review_cards", [])
            if review_cards:
                _save_review_cards(contract_id, review_cards)
            db2 = await _get_db()
            try:
                await db2.execute(
                    "UPDATE contract_reviews SET status = 'completed', updated_at = NOW() WHERE id = $1",
                    contract_id,
                )
            finally:
                await db2.close()
        except Exception as e:
            logger.exception(f"Pipeline failed for contract {contract_id}")
            db2 = await _get_db()
            try:
                await db2.execute(
                    "UPDATE contract_reviews SET status = 'failed', error_message = $2, updated_at = NOW() WHERE id = $1",
                    contract_id, str(e)[:500],
                )
            finally:
                await db2.close()

    asyncio.create_task(_run_pipeline())

    return RunResponse(run_id=run_id, contract_id=contract_id, status="started")


@router.get("/run/{contract_id}/stream")
async def stream_progress(contract_id: int, token: str = Query(...)):
    """SSE endpoint for real-time progress streaming. Requires Bearer token."""
    # Token validation (simplified — in production, validate JWT)
    if not token:
        raise HTTPException(401, "Authentication required")

    async def _event_stream():
        db = await _get_db()
        try:
            last_status = "pending"
            while True:
                row = await db.fetchrow(
                    "SELECT status, error_message FROM contract_reviews WHERE id = $1",
                    contract_id,
                )
                if not row:
                    yield await _sse_event("error", {"message": "Contract not found"})
                    return

                current_status = row["status"]
                if current_status != last_status:
                    yield await _sse_event("progress", {
                        "status": current_status,
                        "contract_id": contract_id,
                    })
                    last_status = current_status

                if current_status in ("completed", "failed"):
                    yield await _sse_event("complete", {
                        "status": current_status,
                        "contract_id": contract_id,
                        "error": row["error_message"],
                    })
                    return

                await asyncio.sleep(1)
        finally:
            await db.close()

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/report/{contract_id}")
async def get_report(contract_id: int):
    """Get complete review report including disclaimer."""
    db = await _get_db()
    try:
        review = await db.fetchrow(
            "SELECT * FROM contract_reviews WHERE id = $1", contract_id
        )
        if not review:
            raise HTTPException(404, "Contract not found")

        clauses = await db.fetch(
            "SELECT * FROM contract_clauses WHERE review_id = $1 ORDER BY seq_no",
            contract_id,
        )
        review_cards = await db.fetch(
            "SELECT * FROM contract_review_cards WHERE review_id = $1",
            contract_id,
        )
        evidence = await db.fetch(
            "SELECT * FROM contract_evidence WHERE review_id = $1",
            contract_id,
        )
        revisions = await db.fetch(
            "SELECT * FROM revision_accepts WHERE review_id = $1",
            contract_id,
        )

        return {
            "contract_id": contract_id,
            "status": review["status"],
            "contract_type": review["contract_type"],
            "filename": review["original_filename"],
            "disclaimer_accepted": review["disclaimer_accepted"],
            "lawyer_confirmed_at": str(review["lawyer_confirmed_at"]) if review["lawyer_confirmed_at"] else None,
            "clauses": [dict(c) for c in clauses],
            "review_cards": [dict(r) for r in review_cards],
            "evidence": [dict(e) for e in evidence],
            "revisions": [dict(r) for r in revisions],
            "disclaimer": (
                "⚠ 免责声明：本报告由AI自动生成，仅供参考，不构成法律意见。"
                "所有审查结果需经执业律师审核确认后方可作为决策依据。"
                "使用本系统即表示您已理解并同意上述声明。"
            ),
            "created_at": str(review["created_at"]),
            "updated_at": str(review["updated_at"]) if review["updated_at"] else None,
        }
    finally:
        await db.close()


@router.get("/reviews")
async def list_reviews():
    """List recent contract reviews with risk-level counts (审查历史)."""
    db = await _get_db()
    try:
        rows = await db.fetch(
            "SELECT id, original_filename, contract_type, status, created_at, updated_at "
            "FROM contract_reviews ORDER BY id DESC LIMIT 50"
        )
        result = []
        for r in rows:
            counts = await db.fetch(
                "SELECT level, COUNT(*) AS n FROM contract_review_cards "
                "WHERE review_id = $1 GROUP BY level",
                r["id"],
            )
            lv = {c["level"]: c["n"] for c in counts}
            result.append({
                "id": r["id"],
                "filename": r["original_filename"],
                "contract_type": r["contract_type"],
                "status": r["status"],
                "created_at": str(r["created_at"]),
                "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
                "high_risk": lv.get("高", 0),
                "medium_risk": lv.get("中", 0),
                "low_risk": lv.get("低", 0),
            })
        return {"reviews": result}
    finally:
        await db.close()


# Statuses that mean the pipeline is still running — deleting mid-run would
# leave the background task writing into a vanished review row.
_ACTIVE_STATUSES = ("pending", "parsing", "reviewing", "retrieving", "revising")


@router.delete("/review/{contract_id}")
async def delete_review(contract_id: int):
    """Delete a historical review and all of its dependent data.

    All child tables (contract_clauses, contract_review_cards, contract_evidence,
    revision_accepts, contract_qa_sessions → contract_qa_messages) are removed in
    a single transaction. The uploaded file itself is kept on disk.
    """
    db = await _get_db()
    try:
        review = await db.fetchrow(
            "SELECT id, status FROM contract_reviews WHERE id = $1", contract_id
        )
        if not review:
            raise HTTPException(404, "Contract not found")
        if review["status"] in _ACTIVE_STATUSES:
            raise HTTPException(409, "该审查正在进行中，请等待完成后再删除")

        async with db.transaction():
            await db.execute(
                "DELETE FROM contract_qa_messages WHERE session_id IN "
                "(SELECT id FROM contract_qa_sessions WHERE contract_id = $1)",
                contract_id,
            )
            await db.execute(
                "DELETE FROM contract_qa_sessions WHERE contract_id = $1", contract_id
            )
            await db.execute(
                "DELETE FROM contract_evidence WHERE review_id = $1", contract_id
            )
            await db.execute(
                "DELETE FROM revision_accepts WHERE review_id = $1", contract_id
            )
            await db.execute(
                "DELETE FROM contract_review_cards WHERE review_id = $1", contract_id
            )
            await db.execute(
                "DELETE FROM contract_clauses WHERE review_id = $1", contract_id
            )
            await db.execute(
                "DELETE FROM contract_reviews WHERE id = $1", contract_id
            )

        logger.info(f"Review deleted: id={contract_id}")
        return {"status": "deleted", "id": contract_id}
    finally:
        await db.close()


@router.post("/revision/{revision_id}/accept")
async def accept_revision(revision_id: int, req: RevisionAcceptRequest):
    """Accept/reject a revision suggestion with idempotent key."""
    if req.status not in ("accepted", "rejected", "needs_lawyer"):
        raise HTTPException(400, "Invalid status. Must be accepted, rejected, or needs_lawyer.")

    db = await _get_db()
    try:
        # Check idempotent key
        existing = await db.fetchrow(
            "SELECT id FROM idempotent_ops WHERE idempotent_key = $1",
            req.idempotent_key,
        )
        if existing:
            logger.info(f"Idempotent key already processed: {req.idempotent_key}")
            return {"status": "already_processed", "revision_id": revision_id}

        # Record idempotent op
        await db.execute(
            "INSERT INTO idempotent_ops (idempotent_key, operation_type, status) VALUES ($1, 'revision_accept', 'completed')",
            req.idempotent_key,
        )

        # Update revision
        await db.execute(
            "UPDATE revision_accepts SET status = $1, updated_at = NOW() WHERE id = $2",
            req.status, revision_id,
        )

        return {"status": "ok", "revision_id": revision_id, "new_status": req.status}
    finally:
        await db.close()


@router.post("/revision/{revision_id}/lawyer-confirm")
async def lawyer_confirm_revision(revision_id: int, req: LawyerConfirmRequest):
    """Lawyer confirms a revision (professional review step)."""
    db = await _get_db()
    try:
        rev = await db.fetchrow("SELECT review_id FROM revision_accepts WHERE id = $1", revision_id)
        if not rev:
            raise HTTPException(404, "Revision not found")

        if req.confirmed:
            await db.execute(
                "UPDATE contract_reviews SET lawyer_confirmed_at = NOW() WHERE id = $1",
                rev["review_id"],
            )

        return {"status": "ok", "revision_id": revision_id, "confirmed": req.confirmed}
    finally:
        await db.close()
