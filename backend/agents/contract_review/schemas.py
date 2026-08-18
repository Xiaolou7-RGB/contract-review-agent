"""
Data Transfer Objects for contract review pipeline.
Pydantic models for Clause, ReviewCard, Evidence, Revision
and TypedDict for LangGraph state.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict
from pydantic import BaseModel, Field


# ── Node ① output ──────────────────────────────────────────

class Clause(BaseModel):
    clause_id: str = Field(..., description="Stable hash of content[:80] + page")
    seq_no: int = Field(..., ge=1)
    type: str = Field(default="", description="e.g. 付款条款, 违约责任")
    title: str = Field(default="")
    content: str = Field(...)
    page: int = Field(default=1, ge=1)
    char_start: int = Field(default=0, ge=0)
    char_end: int = Field(default=0, ge=0)
    span: dict[str, Any] = Field(default_factory=dict, description="{page, x0, y0, x1, y1}")


# ── Node ② output ──────────────────────────────────────────

class ReviewCard(BaseModel):
    clause_id: str
    dimension: str = Field(..., description="legal / compliance / financial / rights_obligations")
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    level: str = Field(..., description="高 / 中 / 低 / 无")
    span: str = Field(default="")
    suggestion: str = Field(default="")
    risk_type: str = Field(default="")


# ── Node ③ output ──────────────────────────────────────────

class Evidence(BaseModel):
    source_id: str = Field(..., description="Unique ID in Milvus collection")
    source_collection: str = Field(..., description="kb_law / kb_case / kb_template")
    quote: str = Field(..., min_length=1, description="100-300 chars excerpt")
    relevance: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_human_review: bool = Field(default=False)
    href: str = Field(default="")


# ── Node ④ output ──────────────────────────────────────────

class Revision(BaseModel):
    clause_id: str
    before_text: str = Field(default="")
    after_text: str = Field(default="")
    diff_html: str = Field(default="")
    evidence_ids: list[str] = Field(default_factory=list)


# ── LangGraph state TypedDict ───────────────────────────────

class ContractReviewState(TypedDict, total=False):
    """State carried through the 6-node pipeline (parse → rule_check → review → retrieve → human_gate → revise)."""
    # Input
    contract_id: int
    user_id: int
    text: str
    contract_type: str

    # Node ①
    clauses: list[dict[str, Any]]

    # Node ①.5 (rule engine — Layer 1)
    rule_findings: list[dict[str, Any]]  # deterministic rule check results

    # Node ②
    review_cards: list[dict[str, Any]]
    degraded_review: bool
    # Node ② fan-out (LangGraph Send API — true graph-level parallelism)
    dimension_key: str                                       # which dimension a Send branch reviews
    dimension_cards: Annotated[list[dict[str, Any]], operator.add]  # reducer-merged per-dimension cards

    # Node ③
    evidence_map: dict[str, list[dict[str, Any]]]  # clause_id → [Evidence...]

    # Human-in-the-Loop gate (between retrieve and revise)
    needs_human_review: bool
    human_review_items: list[dict[str, Any]]
    human_review_status: str  # pending / skipped / completed
    human_review_decisions: list[dict[str, Any]]

    # Node ④
    revisions: list[dict[str, Any]]

    # Meta
    iteration_count: int
    max_iterations: int
    visited_clause_ids: list[str]
    error: str
    status: str
    # ── IMPORTANT: no LangGraph-managed keys that could contain unserializable objects ──
    messages: list[Any]  # placeholder for LangGraph message reducer (unused)
