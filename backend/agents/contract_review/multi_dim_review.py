"""
Multi-dimension review — Node ② of the contract review pipeline.
Fans out N independent review prompts (one per dimension) and merges results.

Dimension routing is determined by contract_type.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.agents.contract_review.schemas import ContractReviewState
from backend.agents.contract_review.degradation import with_degradation
from backend.core.context_manager import trim_clauses_for_budget
from backend.core.llm import get_structured_llm

logger = logging.getLogger(__name__)

# ── Dimension definitions ───────────────────────────────────

DIMENSIONS = {
    "legal": {
        "key": "legal",
        "label": "法律风险",
        "prompt": "从法律合规角度审查，关注：合同是否违反法律强制性规定、是否存在无效条款、争议解决条款是否合理、适用法律是否明确。",
    },
    "compliance": {
        "key": "compliance",
        "label": "合规风险",
        "prompt": "从合规角度审查，关注：是否符合行业监管要求、数据保护与隐私合规、反商业贿赂、进出口管制、劳动用工合规。",
    },
    "financial": {
        "key": "financial",
        "label": "财务风险",
        "prompt": "从财务角度审查，关注：价格条款是否明确、付款条件是否合理、发票与税务安排、违约金是否过高、是否存在隐性财务风险。",
    },
    "rights_obligations": {
        "key": "rights_obligations",
        "label": "权责风险",
        "prompt": "从权利义务角度审查，关注：双方权利义务是否对等、免责条款是否合理、责任限制条款、知识产权归属、保密义务范围。",
    },
}

# ── Dimension routing ───────────────────────────────────────

DIMENSION_ROUTING: dict[str, list[str]] = {
    "买卖": ["legal", "compliance", "financial", "rights_obligations"],
    "服务": ["legal", "compliance", "financial", "rights_obligations"],
    "劳动": ["legal", "compliance", "rights_obligations"],
    "借款": ["legal", "financial", "rights_obligations"],
    "保密": ["legal", "rights_obligations"],
    "其他": ["legal", "compliance", "financial", "rights_obligations"],
}


def get_active_dimensions(contract_type: str) -> list[dict[str, str]]:
    """Return active dimension configs for a given contract type."""
    keys = DIMENSION_ROUTING.get(contract_type, DIMENSION_ROUTING["其他"])
    return [DIMENSIONS[k] for k in keys if k in DIMENSIONS]


# ── Pydantic model for structured review output ─────────────

from pydantic import BaseModel, Field


class _DimReviewCard(BaseModel):
    clause_index: int = Field(..., description="0-based index into the input clause list")
    clause_title: str = Field(default="")
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    level: str = Field(..., description="高 / 中 / 低 / 无")
    suggestion: str = Field(default="")
    risk_type: str = Field(default="")


class _DimReviewResult(BaseModel):
    cards: list[_DimReviewCard] = Field(default_factory=list)


# ── Single-dimension review ─────────────────────────────────

def _build_dimension_prompt(
    clause_texts: list[str],
    dim: dict[str, str],
) -> str:
    """Build the review prompt for one dimension."""
    clauses_block = "\n\n".join(
        f"[{i}] {text}" for i, text in enumerate(clause_texts)
    )
    return f"""你是一位合同审查专家。请从「{dim['label']}」维度审查以下合同条款。

审查维度说明：
{dim['prompt']}

合同条款（编号为 clause_index）：
{clauses_block}

对每个有风险的条款输出评审卡片：
- clause_index: 条款编号
- clause_title: 条款简要标题
- score: 风险分数 0.0~1.0（1.0=严重风险）
- level: 风险等级（高/中/低/无），score≥0.7→高，0.4~0.7→中，<0.4→低，0→无
- suggestion: 具体的修改建议或风险说明
- risk_type: 风险类别（如：合同无效风险、赔偿条款失衡、争议解决不利等）

仅输出有实质性风险的条款（level != 无），未发现问题的条款不需要输出卡片。"""


async def _review_single_dimension(
    clauses: list[dict[str, Any]],
    dim: dict[str, str],
) -> list[dict[str, Any]]:
    """Review all clauses against a single dimension."""
    clause_texts = [c["content"] for c in clauses]

    llm = get_structured_llm(_DimReviewResult)
    prompt = _build_dimension_prompt(clause_texts, dim)
    result: _DimReviewResult = await llm.ainvoke(prompt)

    cards: list[dict[str, Any]] = []
    seen_indices: set[int] = set()
    for card in result.cards:
        if card.clause_index in seen_indices:
            continue
        seen_indices.add(card.clause_index)
        if card.level == "无" or card.clause_index < 0:
            continue
        if card.clause_index >= len(clauses):
            continue

        clause = clauses[card.clause_index]
        cards.append({
            "clause_id": clause["clause_id"],
            "dimension": dim["key"],
            "score": max(0.0, min(1.0, card.score)),
            "level": card.level,
            "span": clause.get("span", {}).get("page", str(clause.get("page", 1))),
            "suggestion": card.suggestion,
            "risk_type": card.risk_type,
        })

    logger.info(f"Dimension {dim['key']}: found {len(cards)} risk cards out of {len(clauses)} clauses")
    return cards


# ── Fallback: single combined review ────────────────────────

async def _fallback_combined_review(
    clauses: list[dict[str, Any]],
    contract_type: str,
) -> list[dict[str, Any]]:
    """Fallback: review all dimensions in a single LLM call."""
    from pydantic import BaseModel, Field as PField

    class _FallbackCard(BaseModel):
        clause_index: str = PField(default="0", description="clause index as string")
        dimension: str = PField(...)
        score: float = PField(default=0.0)
        level: str = PField(...)
        suggestion: str = PField(default="")
        risk_type: str = PField(default="")

    class _FallbackResult(BaseModel):
        cards: list[_FallbackCard] = PField(default_factory=list)

    dims = get_active_dimensions(contract_type)
    dim_labels = "\n".join(f"- {d['key']}: {d['label']}（{d['prompt'][:60]}...）" for d in dims)
    clause_texts = [c["content"] for c in clauses]
    clauses_block = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(clause_texts))

    prompt = f"""你是合同审查专家。请从以下多维度综合审查合同条款：

维度说明：
{dim_labels}

合同条款：
{clauses_block}

对每个有风险的条款，从适当的维度提出评审意见。每个条款可涉及多个维度。
输出时对每个发现的风险，dimension 使用上述维度的 key 值。"""

    llm = get_structured_llm(_FallbackResult)
    result: _FallbackResult = await llm.ainvoke(prompt)

    cards: list[dict[str, Any]] = []
    for card in result.cards:
        try:
            ci = int(card.clause_index)
        except (ValueError, TypeError):
            continue
        if ci < 0 or ci >= len(clauses):
            continue
        clause = clauses[ci]
        cards.append({
            "clause_id": clause["clause_id"],
            "dimension": card.dimension,
            "score": max(0.0, min(1.0, card.score)),
            "level": card.level,
            "span": str(clause.get("page", 1)),
            "suggestion": card.suggestion,
            "risk_type": card.risk_type,
        })

    logger.info(f"Fallback combined review: {len(cards)} risk cards")
    return cards


# ── Merge (fan-in) ──────────────────────────────────────────

def merge_review_cards(all_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Merge cards from multiple dimensions.
    For the same clause_id, keep the highest risk level.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for card in all_cards:
        grouped.setdefault(card["clause_id"], []).append(card)

    merged: list[dict[str, Any]] = []
    level_order = {"高": 4, "中": 3, "低": 2, "无": 1}

    for clause_id, cards in grouped.items():
        # Take the card with highest level
        best = max(cards, key=lambda c: (level_order.get(c.get("level", "无"), 0), c.get("score", 0)))
        # Merge all dimensions
        dimensions = list({c["dimension"] for c in cards})
        best["dimensions"] = dimensions
        best["all_cards"] = cards
        merged.append(best)

    merged.sort(key=lambda c: level_order.get(c.get("level", "无"), 0), reverse=True)
    return merged


# ── Main review function ────────────────────────────────────

async def review_clauses(
    clauses: list[dict[str, Any]],
    contract_type: str,
    on_dimension_done: Any = None,
) -> dict[str, Any]:
    """
    Fan-out to N dimension reviewers, fan-in and merge results.
    Returns {"review_cards": list[dict], "degraded_review": bool}.

    on_dimension_done: optional async callback(dim_config, cards) fired as
    soon as each dimension finishes — used to persist/stream cards
    incrementally instead of waiting for the whole fan-in.
    """
    logger.info(f"Starting multi-dim review: {len(clauses)} clauses, type={contract_type}")

    trimmed = trim_clauses_for_budget(clauses)
    dims = get_active_dimensions(contract_type)
    logger.info(f"Active dimensions: {[d['key'] for d in dims]}")

    async def _primary():
        async def _dim_with_notify(dim: dict[str, str]) -> list[dict[str, Any]]:
            cards = await _review_single_dimension(trimmed, dim)
            if on_dimension_done is not None:
                try:
                    await on_dimension_done(dim, cards)
                except Exception as cb_err:  # callback must never break the review
                    logger.warning(f"on_dimension_done callback failed for {dim['key']}: {cb_err}")
            return cards

        tasks = [_dim_with_notify(dim) for dim in dims]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        all_cards: list[dict[str, Any]] = []
        for cards in results:
            all_cards.extend(cards)
        return all_cards

    async def _fallback():
        return await _fallback_combined_review(trimmed, contract_type)

    all_cards, degraded = await with_degradation(
        "multi_dim_review",
        _primary,
        _fallback,
        max_retries=2,
    )

    merged = merge_review_cards(all_cards)
    logger.info(f"Multi-dim review complete: {len(merged)} merged risk cards, degraded={degraded}")

    return {
        "review_cards": merged,
        "degraded_review": degraded,
    }


# ── LangGraph node ──────────────────────────────────────────

async def multi_dim_review_node(state: ContractReviewState, on_dimension_done: Any = None) -> dict[str, Any]:
    """LangGraph node: fan-out multi-dimension review.

    on_dimension_done is an optional passthrough for the direct (non-LangGraph)
    runner to persist/stream cards per dimension; LangGraph calls this with
    state only.
    """
    clauses = state.get("clauses", [])
    contract_type = state.get("contract_type", "其他")

    result = await review_clauses(clauses, contract_type, on_dimension_done=on_dimension_done)

    return {
        "review_cards": result["review_cards"],
        "degraded_review": result["degraded_review"],
        "status": "reviewed",
    }
