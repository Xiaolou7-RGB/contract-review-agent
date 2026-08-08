"""
Context manager — trims input text/cards to fit within a token budget.
Legal text must not be arbitrarily truncated; we trim by scope subsets.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

TOKEN_BUDGET = 8000
CHARS_PER_TOKEN_ESTIMATE = 1.5  # Chinese chars ≈ 1.5 tokens each


def estimate_tokens(text: str) -> int:
    """Rough token count estimate for Chinese text."""
    return int(len(text) / CHARS_PER_TOKEN_ESTIMATE)


def trim_clauses_for_budget(
    clauses: list[dict[str, Any]],
    max_tokens: int = TOKEN_BUDGET,
) -> list[dict[str, Any]]:
    """
    Trim clause list to fit token budget.
    Prioritizes: keep all clauses, but truncate longest content first.
    Returns the same list reference with potentially shortened content.
    """
    total_est = sum(estimate_tokens(c["content"]) for c in clauses)
    if total_est <= max_tokens:
        return clauses

    # Sort by content length descending, trim longest
    sorted_clauses = sorted(clauses, key=lambda c: len(c["content"]), reverse=True)
    budget_remaining = max_tokens

    for clause in sorted_clauses:
        content_len = estimate_tokens(clause["content"])
        if budget_remaining <= 0:
            clause["content"] = clause["content"][:200] + "...[truncated]"
        elif content_len > budget_remaining:
            max_chars = int(budget_remaining * CHARS_PER_TOKEN_ESTIMATE)
            clause["content"] = clause["content"][:max_chars] + "...[见原文]"
            budget_remaining = 0
        else:
            budget_remaining -= content_len

    return clauses


def trim_cards_for_budget(
    cards: list[dict[str, Any]],
    max_tokens: int = TOKEN_BUDGET,
) -> list[dict[str, Any]]:
    """Trim review cards to fit token budget, keeping high-risk ones."""
    total_est = sum(estimate_tokens(c.get("suggestion", "")) for c in cards)
    if total_est <= max_tokens:
        return cards

    # Priority: high risk > medium > low > none
    level_order = {"高": 0, "中": 1, "低": 2, "无": 3}
    sorted_cards = sorted(cards, key=lambda c: level_order.get(c.get("level", "无"), 3))

    kept: list[dict[str, Any]] = []
    budget_remaining = max_tokens
    for card in sorted_cards:
        card_est = estimate_tokens(card.get("suggestion", ""))
        if budget_remaining >= card_est:
            kept.append(card)
            budget_remaining -= card_est
        else:
            # Truncate suggestion to fit remaining budget
            max_chars = int(budget_remaining * CHARS_PER_TOKEN_ESTIMATE)
            card["suggestion"] = card.get("suggestion", "")[:max_chars] + "..."
            kept.append(card)
            break

    logger.info(f"Trimmed cards from {len(cards)} to {len(kept)} (budget={max_tokens})")
    return kept
