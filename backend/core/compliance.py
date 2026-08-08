"""
Compliance module — disclaimer, idempotent protection, lawyer confirmation flow.
Ensures the system meets legal compliance and risk management requirements.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Disclaimer text constants ───────────────────────────────

DISCLAIMER_TEXT = (
    "⚠ 免责声明：本报告由AI自动生成，仅供参考，不构成法律意见。"
    "所有审查结果需经执业律师审核确认后方可作为决策依据。"
    "使用本系统即表示您已理解并同意上述声明。"
)

DISCLAIMER_SHORT = (
    "本报告为AI生成内容，不构成法律意见，请咨询执业律师。"
)

LAWYER_REVIEW_REQUIRED_TEXT = (
    "⚠ 此审查结果需要律师复核确认后方可采纳。"
)


# ── Disclaimer validation ──────────────────────────────────

def validate_disclaimer_accepted(review: dict[str, Any]) -> bool:
    """Check if the disclaimer was accepted for this review."""
    return bool(review.get("disclaimer_accepted", False))


def require_disclaimer(review: dict[str, Any]) -> None:
    """Raise if disclaimer not accepted."""
    if not validate_disclaimer_accepted(review):
        raise PermissionError("Disclaimer must be accepted before proceeding.")


# ── Idempotent operation guard ──────────────────────────────

async def check_idempotent(
    db_conn,
    idempotent_key: str,
) -> bool:
    """
    Check if an idempotent operation has already been processed.
    Returns True if already processed (should skip), False if new.
    """
    row = await db_conn.fetchrow(
        "SELECT id, status, result FROM idempotent_ops WHERE idempotent_key = $1",
        idempotent_key,
    )
    if row:
        logger.info(f"Idempotent key already exists: {idempotent_key}, status={row['status']}")
        return True
    return False


async def record_idempotent_op(
    db_conn,
    idempotent_key: str,
    operation_type: str,
    result: dict[str, Any] | None = None,
) -> None:
    """Record a new idempotent operation."""
    import json
    await db_conn.execute(
        """
        INSERT INTO idempotent_ops (idempotent_key, operation_type, status, result)
        VALUES ($1, $2, 'completed', $3)
        """,
        idempotent_key,
        operation_type,
        json.dumps(result) if result else None,
    )
    logger.info(f"Recorded idempotent op: {idempotent_key}")


# ── Lawyer confirmation flow ────────────────────────────────

LAWYER_CONFIRMATION_STATES = {
    "pending_lawyer_review": "等待律师复核",
    "lawyer_reviewed": "律师已复核",
    "accepted": "已确认采纳",
}


def transition_lawyer_state(
    current_status: str,
    action: str,
) -> str:
    """
    Validate and transition lawyer confirmation state.
    Valid actions: 'lawyer_review' → 'lawyer_reviewed'
                   'accept' → 'accepted'
    """
    transitions = {
        ("pending_lawyer_review", "lawyer_review"): "lawyer_reviewed",
        ("lawyer_reviewed", "accept"): "accepted",
        ("pending_lawyer_review", "accept"): "accepted",  # skip review step
    }

    new_status = transitions.get((current_status, action))
    if new_status is None:
        raise ValueError(
            f"Invalid state transition: {current_status} → {action}. "
            f"Allowed: {[(k, v) for k, v in transitions.items() if k[0] == current_status]}"
        )

    logger.info(f"Lawyer confirmation: {current_status} → {new_status} (action={action})")
    return new_status


# ── Report wrapper ──────────────────────────────────────────

def wrap_report_with_compliance(
    report: dict[str, Any],
    disclaimer_accepted: bool = False,
) -> dict[str, Any]:
    """Add mandatory compliance fields to a report response."""
    return {
        **report,
        "disclaimer": DISCLAIMER_TEXT,
        "disclaimer_accepted": disclaimer_accepted,
        "requires_lawyer_review": True,
    }
