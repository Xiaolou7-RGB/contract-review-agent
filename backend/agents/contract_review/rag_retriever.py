"""
RAG Retriever — Node ③ of the contract review pipeline.
Routes each risk card to the appropriate knowledge base collection(s),
performs hybrid search + rerank, and produces Evidence with mandatory source_id.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from backend.agents.contract_review.schemas import ContractReviewState
from backend.agents.contract_review.degradation import with_degradation
from backend.core.rag import get_kb_client

logger = logging.getLogger(__name__)

# ── Risk-type → Collection routing ─────────────────────────
# Currently all routed to civil_code_hybrid (dense+sparse BGE-M3 collection).
# When kb_case/kb_template corpora are ready, restore per-type routing.

_COLLECTION = "civil_code_hybrid"

RISK_TO_COLLECTION: dict[str, list[str]] = {
    "合同无效风险": [_COLLECTION],
    "违约风险": [_COLLECTION],
    "赔偿条款失衡": [_COLLECTION],
    "争议解决不利": [_COLLECTION],
    "知识产权归属不明": [_COLLECTION],
    "保密义务过宽": [_COLLECTION],
    "竞业限制无效": [_COLLECTION],
    "财务风险": [_COLLECTION],
    "价格条款不明": [_COLLECTION],
    "付款条件不合理": [_COLLECTION],
    "担保无效": [_COLLECTION],
    "合规风险": [_COLLECTION],
    "数据保护合规": [_COLLECTION],
    "劳动合规风险": [_COLLECTION],
    "权责不对等": [_COLLECTION],
    "免责条款过宽": [_COLLECTION],
}

# Dimension-based routing for unmatched risk types
DIM_TO_COLLECTION: dict[str, list[str]] = {
    "legal": [_COLLECTION],
    "compliance": [_COLLECTION],
    "financial": [_COLLECTION],
    "rights_obligations": [_COLLECTION],
}

DEFAULT_COLLECTIONS = [_COLLECTION]


def _route_collections(risk_type: str, dimension: str) -> list[str]:
    """Determine which collections to search based on risk type and dimension."""
    # Try exact risk type match
    if risk_type in RISK_TO_COLLECTION:
        return RISK_TO_COLLECTION[risk_type]

    # Try dimension-based routing
    if dimension in DIM_TO_COLLECTION:
        return DIM_TO_COLLECTION[dimension]

    # Fallback: search all
    return DEFAULT_COLLECTIONS


# ── Chinese numeral conversion (law article numbers) ───────

_CN_DIGITS = "零一二三四五六七八九"
_CN_DIGIT_VAL = {ch: i for i, ch in enumerate(_CN_DIGITS)}
_CN_UNIT_VAL = {"十": 10, "百": 100, "千": 1000}

# Article numbers observed in the corpus, e.g. "第四百七十四条"
_ARTICLE_NO_RE = re.compile(r"^第(.+)条$")
# Cross-reference pattern inside article text, e.g. "...依照本法第一百三十七条..."
_ARTICLE_REF_RE = re.compile(r"第[一二三四五六七八九十百千零]+条")


def cn_to_int(text: str) -> int:
    """Chinese numeral → int. Supports 1~9999 with 零 (e.g. 五百零八→508). Returns 0 on failure."""
    if not text:
        return 0
    total = 0
    num = 0
    for ch in text:
        if ch in _CN_DIGIT_VAL:
            num = _CN_DIGIT_VAL[ch]
        elif ch in _CN_UNIT_VAL:
            unit = _CN_UNIT_VAL[ch]
            if num == 0:
                num = 1  # bare "十" means 10
            total += num * unit
            num = 0
        else:
            return 0
    return total + num


def int_to_cn(n: int) -> str:
    """int → Chinese numeral for article numbers (1~1260).

    Handles 零 correctly: 101→一百零一, 508→五百零八, 1001→一千零一.
    Leading "一十" is shortened to "十" per legal convention (10→十, 15→十五).
    """
    if n < 1 or n > 9999:
        return ""
    units = ["", "十", "百", "千"]
    digits = str(n)
    out = ""
    zero_pending = False
    for i, ch in enumerate(digits):
        d = int(ch)
        unit = units[len(digits) - 1 - i]
        if d == 0:
            zero_pending = True
        else:
            if zero_pending and out:
                out += "零"
            out += _CN_DIGITS[d] + unit
            zero_pending = False
    if out.startswith("一十"):
        out = out[1:]
    return out


def _parse_article_no(article_no: str) -> int:
    """'第四百七十四条' → 474. Returns 0 if unparseable."""
    if not article_no:
        return 0
    m = _ARTICLE_NO_RE.match(str(article_no).strip())
    if not m:
        return 0
    return cn_to_int(m.group(1))


def _format_article_no(n: int) -> str:
    """474 → '第四百七十四条'. Returns '' if out of range."""
    cn = int_to_cn(n)
    return f"第{cn}条" if cn else ""


# ── Context expansion ──────────────────────────────────────

async def _build_expanded_context(
    search_results: list[dict[str, Any]],
    kb_client: Any,
    cache: dict[str, dict[str, Any]],
    collection: str,
    max_depth: int = 2,  # reserved; cross-refs are parsed one level only
) -> list[dict[str, Any]]:
    """
    Expand retrieval context around each hit:
      1. ±2 adjacent articles in the same chapter (chapter filter handles 编 boundaries);
      2. cross_refs parsed on the fly from hit text via 第X条 regex
         (one level, self-reference excluded, at most 4 refs per hit).
    Gates: global cache dedup; at most 8 expanded articles per call;
    expanded items carry expanded=True and confidence = 0.9 × source hit confidence.
    """
    if not search_results:
        return []

    def _ckey(a: str) -> str:
        return f"{collection}|{a}"

    # wanted: article_no_str → {"kind", "chapter" (required, '' = any), "base_conf"}
    wanted: dict[str, dict[str, Any]] = {}

    for r in search_results:
        art_no = _parse_article_no(r.get("article_no", ""))
        if art_no <= 0:
            continue
        chapter = r.get("chapter", "")
        base_conf = float(r.get("confidence", 0.0))

        # 1) ±2 adjacent articles (same chapter only)
        for delta in (-2, -1, 1, 2):
            a = _format_article_no(art_no + delta)
            if not a or _ckey(a) in cache or a in wanted:
                continue
            wanted[a] = {"kind": "adjacent", "chapter": chapter, "base_conf": base_conf}

        # 2) cross-references in text (one level, max 4 per hit)
        text = r.get("content", "")
        if not text:
            continue
        own = _format_article_no(art_no)
        seen_refs: set[str] = set()
        ref_count = 0
        for ref in _ARTICLE_REF_RE.findall(text):
            if ref == own or ref in seen_refs:
                continue
            seen_refs.add(ref)
            ref_no = _parse_article_no(ref)
            if ref_no <= 0 or _ckey(ref) in cache:
                continue
            # cross-refs outrank adjacents when both target the same article
            if ref in wanted and wanted[ref]["kind"] == "adjacent":
                wanted[ref]["kind"] = "cross_ref"
                wanted[ref]["chapter"] = ""
                wanted[ref]["base_conf"] = max(wanted[ref]["base_conf"], base_conf)
            elif ref not in wanted:
                wanted[ref] = {"kind": "cross_ref", "chapter": "", "base_conf": base_conf}
            ref_count += 1
            if ref_count >= 4:
                break

    if not wanted:
        return []

    # Gate: at most 8 expansions per call (cross_refs first, then adjacents)
    ordered = sorted(wanted.items(), key=lambda kv: 0 if kv[1]["kind"] == "cross_ref" else 1)
    selected = ordered[:8]

    try:
        fetched = kb_client.query_articles(collection, [a for a, _ in selected])
    except Exception:
        logger.exception("Context expansion query failed")
        return []

    by_no = {f.get("article_no", ""): f for f in fetched}

    expanded: list[dict[str, Any]] = []
    for a, meta in selected:
        if _ckey(a) in cache:  # may have been filled by a parallel path
            continue
        item = by_no.get(a)
        if not item:
            continue
        # adjacent articles must stay within the same chapter (编 boundary)
        if meta["kind"] == "adjacent" and meta["chapter"] and item.get("chapter", "") != meta["chapter"]:
            continue
        item = dict(item)
        item["confidence"] = round(meta["base_conf"] * 0.9, 4)
        item["rerank_score"] = item["confidence"]
        item["expanded"] = True
        item["expand_kind"] = meta["kind"]
        if item["confidence"] < 0.30:
            item["is_human_review"] = True
        cache[_ckey(a)] = item
        expanded.append(item)

    return expanded


# ── Evidence builder ────────────────────────────────────────

def _build_evidence(
    clause_id: str,
    search_results: list[dict[str, Any]],
    threshold: float = 0.30,
) -> list[dict[str, Any]]:
    """Build Evidence records from search results."""
    evidence_list: list[dict[str, Any]] = []
    for result in search_results:
        quote = result.get("content", "")
        if len(quote) > 300:
            quote = quote[:300] + "..."

        confidence = result.get("confidence", 0.0)
        is_human_review = confidence < threshold

        evidence_list.append({
            "source_id": str(result.get("id", "")),
            "source_collection": result.get("source_collection", ""),
            "quote": quote,
            "relevance": f"Score: {confidence:.2f}",
            "confidence": confidence,
            "is_human_review": is_human_review,
            "href": result.get("href", ""),
        })
    return evidence_list


# ── Search query builder ────────────────────────────────────

def _build_search_query(card: dict[str, Any], clause: dict[str, Any]) -> str:
    """Build a search query from a risk card and its clause."""
    parts = []
    if clause.get("title"):
        parts.append(clause["title"])
    parts.append(card.get("risk_type", ""))
    parts.append(card.get("suggestion", ""))
    return " ".join(parts)[:512]


# ── Main retrieval function ─────────────────────────────────

async def retrieve_evidence(
    review_cards: list[dict[str, Any]],
    clauses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    For each review card, search the appropriate collections and build evidence.
    Returns list of Evidence dicts.
    """
    logger.info(f"Starting RAG retrieval for {len(review_cards)} risk cards")

    # Build clause lookup
    clause_map: dict[str, dict[str, Any]] = {c["clause_id"]: c for c in clauses}

    all_evidence: list[dict[str, Any]] = []
    kb_client = get_kb_client()
    # Scope-wide cache so an article expanded for one card is not re-fetched for another
    expansion_cache: dict[str, dict[str, Any]] = {}

    for card in review_cards:
        clause_id = card.get("clause_id", "")
        clause = clause_map.get(clause_id, {})
        risk_type = card.get("risk_type", "")
        dimension = card.get("dimension", "")

        collections = _route_collections(risk_type, dimension)
        query = _build_search_query(card, clause)

        if not query.strip():
            continue

        for col in collections:
            try:
                results = await kb_client.hybrid_search(
                    query=query,
                    collection=col,
                    top_k=10,
                    rerank_top_k=3,
                )
                # Tag each result with collection
                for r in results:
                    r["source_collection"] = col

                # Context expansion: ±2 adjacent articles + cross-references
                try:
                    expanded = await _build_expanded_context(
                        results, kb_client, expansion_cache, col
                    )
                    for e in expanded:
                        e["source_collection"] = col
                    if expanded:
                        logger.info(
                            f"Context expansion added {len(expanded)} articles "
                            f"for clause={clause_id} from {col}"
                        )
                        results = results + expanded
                except Exception as e:
                    logger.warning(f"Context expansion failed for {col}: {e}")

                evidence = _build_evidence(clause_id, results)
                for ev in evidence:
                    ev["clause_id"] = clause_id
                all_evidence.extend(evidence)

                logger.debug(
                    f"Retrieved {len(evidence)} evidence for clause={clause_id} "
                    f"risk={risk_type} from {col}"
                )
            except Exception as e:
                logger.warning(f"Search failed for {col}: {e}")
                # Degradation: mark as human review needed
                all_evidence.append({
                    "clause_id": clause_id,
                    "source_id": "",
                    "source_collection": col,
                    "quote": "检索服务暂时不可用",
                    "relevance": "N/A",
                    "confidence": 0.0,
                    "is_human_review": True,
                    "href": "",
                })

    logger.info(f"RAG retrieval complete: {len(all_evidence)} evidence records")
    return all_evidence


# ── Degradation fallback ────────────────────────────────────

async def _fallback_retrieve(
    review_cards: list[dict[str, Any]],
    clauses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fallback: mark all evidence as needing human review."""
    evidence: list[dict[str, Any]] = []
    for card in review_cards:
        evidence.append({
            "clause_id": card.get("clause_id", ""),
            "source_id": "",
            "source_collection": "",
            "quote": "检索服务不可用，需人工核查",
            "relevance": "N/A",
            "confidence": 0.0,
            "is_human_review": True,
            "href": "",
        })
    return evidence


# ── LangGraph node ──────────────────────────────────────────

async def rag_retriever_node(state: ContractReviewState) -> dict[str, Any]:
    """LangGraph node: retrieve legal evidence for each risk card."""
    review_cards = state.get("review_cards", [])
    clauses = state.get("clauses", [])

    async def _primary():
        return await retrieve_evidence(review_cards, clauses)

    all_evidence, degraded = await with_degradation(
        "rag_retriever",
        _primary,
        lambda: _fallback_retrieve(review_cards, clauses),
        max_retries=2,
    )

    # Build evidence_map: clause_id → [Evidence...]
    evidence_map: dict[str, list[dict[str, Any]]] = {}
    for ev in all_evidence:
        evidence_map.setdefault(ev["clause_id"], []).append(ev)

    return {
        "evidence_map": evidence_map,
        "status": "retrieved",
    }
