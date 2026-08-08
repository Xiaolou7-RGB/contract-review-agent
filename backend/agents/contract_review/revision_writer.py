"""
Revision Writer — Node ④ of the contract review pipeline.
Generates safe clause revisions based on review cards and template evidence.
Uses diff-match-patch to produce inline diff (deletion red, insertion green).
"""
from __future__ import annotations

import logging
from typing import Any

from backend.agents.contract_review.schemas import ContractReviewState
from backend.agents.contract_review.degradation import with_degradation
from backend.core.llm import get_structured_llm

logger = logging.getLogger(__name__)

# ── Pydantic model for structured revision output ───────────

from pydantic import BaseModel, Field


class _RevEntry(BaseModel):
    clause_id: str = Field(...)
    before_text: str = Field(default="")
    after_text: str = Field(default="")
    revision_rationale: str = Field(default="")


class _RevResult(BaseModel):
    revisions: list[_RevEntry] = Field(default_factory=list)


# ── Diff generator (diff-match-patch) ──────────────────────

def generate_diff_html(before_text: str, after_text: str) -> str:
    """
    Generate inline diff HTML using diff-match-patch.
    Deletions in red, insertions in green.
    """
    try:
        import diff_match_patch as dmp_module
        dmp = dmp_module.diff_match_patch()
        diffs = dmp.diff_main(before_text, after_text)
        dmp.diff_cleanupSemantic(diffs)

        html_parts: list[str] = []
        for op, text in diffs:
            # Escape HTML entities
            escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            if op == -1:  # DELETE
                html_parts.append(f'<span style="background:#ffcdd2;text-decoration:line-through">{escaped}</span>')
            elif op == 1:  # INSERT
                html_parts.append(f'<span style="background:#c8e6c9">{escaped}</span>')
            else:  # EQUAL
                html_parts.append(f"<span>{escaped}</span>")

        return "".join(html_parts)
    except ImportError:
        logger.warning("diff-match-patch not available, returning plain text")
        return f"<del>{before_text}</del><ins>{after_text}</ins>"


# ── Build revision prompt ──────────────────────────────────

def _build_revision_prompt(
    clause: dict[str, Any],
    cards: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> str:
    """Build the revision generation prompt for one clause."""
    cards_text = "\n".join(
        f"- [{c.get('dimension', '')}] {c.get('level', '')}风险: {c.get('suggestion', '')}"
        for c in cards
    )
    evidence_text = "\n".join(
        f"- [{e.get('source_collection', '')}] {e.get('quote', '')[:200]}"
        for e in evidence[:3]
    )

    return f"""你是一位合同审查专家和律师。请根据审查意见和法律依据，为以下合同条款起草修订版本。

**原始条款**：
{clause.get('content', '')}

**审查意见**：
{cards_text if cards_text else '无特定审查意见'}

**法律/模板参考**：
{evidence_text if evidence_text else '无特定参考'}

请输出：
- before_text: 原始条款原文
- after_text: 修订后的安全版本（保持原条款结构和关键商业条款，仅修正法律风险点）
- revision_rationale: 修改理由和法律依据

如果不需要修改，after_text 与 before_text 相同。"""


# ── Single clause revision ─────────────────────────────────

async def _revise_single_clause(
    clause: dict[str, Any],
    cards: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Generate revision for a single clause."""
    # Only revise high or medium risk clauses
    levels = {c.get("level", "无") for c in cards}
    if "高" not in levels and "中" not in levels:
        return None

    prompt = _build_revision_prompt(clause, cards, evidence)

    try:
        llm = get_structured_llm(_RevResult)
        result: _RevResult = await llm.ainvoke(prompt)
        if result.revisions:
            entry = result.revisions[0]
            evidence_ids = [e.get("source_id", "") for e in evidence if e.get("source_id")]
            diff_html = generate_diff_html(entry.before_text, entry.after_text)
            return {
                "clause_id": entry.clause_id,
                "before_text": entry.before_text,
                "after_text": entry.after_text,
                "diff_html": diff_html,
                "evidence_ids": evidence_ids,
            }
    except Exception as e:
        logger.warning(f"LLM revision failed for clause {clause.get('clause_id')}: {e}")

    return None


# ── Main revision function ─────────────────────────────────

async def generate_revisions(
    clauses: list[dict[str, Any]],
    review_cards: list[dict[str, Any]],
    evidence_map: dict[str, list[dict[str, Any]]],
    on_revision: Any = None,
) -> list[dict[str, Any]]:
    """
    Generate revisions for high/medium-risk clauses.
    Returns list of Revision dicts.

    on_revision: optional async callback(rev_dict) fired after each clause
    revision is produced — used to persist/stream revisions incrementally.
    """
    logger.info(f"Starting revision generation for {len(clauses)} clauses")

    # Group review cards by clause
    cards_by_clause: dict[str, list[dict[str, Any]]] = {}
    for card in review_cards:
        cards_by_clause.setdefault(card.get("clause_id", ""), []).append(card)

    revisions: list[dict[str, Any]] = []

    for clause in clauses:
        clause_id = clause.get("clause_id", "")
        cards = cards_by_clause.get(clause_id, [])
        evidence = evidence_map.get(clause_id, [])

        if not cards:
            continue

        rev = await _revise_single_clause(clause, cards, evidence)
        if rev:
            revisions.append(rev)
            if on_revision is not None:
                try:
                    await on_revision(rev)
                except Exception as cb_err:  # callback must never break the node
                    logger.warning(f"on_revision callback failed for clause {clause_id}: {cb_err}")

    logger.info(f"Generated {len(revisions)} revisions")
    return revisions


# ── Degradation fallback ────────────────────────────────────

async def _fallback_generate_revisions(
    clauses: list[dict[str, Any]],
    review_cards: list[dict[str, Any]],
    evidence_map: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Fallback: return empty revisions with human-draft-needed flag."""
    cards_by_clause: dict[str, list[dict[str, Any]]] = {}
    for card in review_cards:
        cards_by_clause.setdefault(card.get("clause_id", ""), []).append(card)

    revisions: list[dict[str, Any]] = []
    for clause in clauses:
        clause_id = clause.get("clause_id", "")
        cards = cards_by_clause.get(clause_id, [])
        if not cards:
            continue
        levels = {c.get("level", "无") for c in cards}
        if "高" in levels or "中" in levels:
            revisions.append({
                "clause_id": clause_id,
                "before_text": clause.get("content", ""),
                "after_text": "",
                "diff_html": '<span style="background:#fff3cd">⚠ 需人工起草修订</span>',
                "evidence_ids": [],
            })

    return revisions


# ── LangGraph node ──────────────────────────────────────────

async def revision_writer_node(state: ContractReviewState) -> dict[str, Any]:
    """LangGraph node: generate revisions for risky clauses."""
    clauses = state.get("clauses", [])
    review_cards = state.get("review_cards", [])
    evidence_map = state.get("evidence_map", {})

    async def _primary():
        return await generate_revisions(clauses, review_cards, evidence_map)

    revisions, degraded = await with_degradation(
        "revision_writer",
        _primary,
        lambda: _fallback_generate_revisions(clauses, review_cards, evidence_map),
        max_retries=2,
    )

    return {
        "revisions": revisions,
        "status": "completed" if not degraded else "completed_degraded",
    }
