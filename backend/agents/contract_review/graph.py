"""
LangGraph StateGraph — 4-node linear pipeline for contract review.
Nodes: parse → review → retrieve → revise → END.
Includes SSE progress callback and PG persistence after each node.
"""
from __future__ import annotations

import json as _json
import logging
import os
from typing import Any, Callable

import asyncpg
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from backend.agents.contract_review.schemas import ContractReviewState
from backend.agents.contract_review.clause_parser import clause_parser_node
from backend.agents.contract_review.multi_dim_review import multi_dim_review_node
from backend.agents.contract_review.rag_retriever import rag_retriever_node
from backend.agents.contract_review.revision_writer import revision_writer_node

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres123@localhost:15432/eduagent")

ProgressCallback = Callable[[str, str, dict[str, Any]], None]


async def _get_db() -> asyncpg.Connection:
    return await asyncpg.connect(DATABASE_URL)


# ── Persistence helpers ────────────────────────────────────

async def _persist_clauses(contract_id: int, clauses: list[dict[str, Any]]) -> None:
    """Save parsed clauses to contract_clauses table."""
    if not clauses:
        return
    db = await _get_db()
    try:
        for clause in clauses:
            await db.execute(
                """
                INSERT INTO contract_clauses (review_id, clause_id, seq_no, type, title, content, page, char_start, char_end, span)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT DO NOTHING
                """,
                contract_id,
                clause.get("clause_id", ""),
                clause.get("seq_no", 0),
                clause.get("type", ""),
                clause.get("title", ""),
                clause.get("content", "")[:5000],
                clause.get("page", 1),
                clause.get("char_start", 0),
                clause.get("char_end", 0),
                "{}",  # span as valid JSON
            )
        logger.info(f"Persisted {len(clauses)} clauses for contract {contract_id}")
    finally:
        await db.close()


async def _persist_review_cards(contract_id: int, review_cards: list[dict[str, Any]]) -> None:
    """Save review cards to contract_review_cards table."""
    if not review_cards:
        return
    db = await _get_db()
    try:
        for card in review_cards:
            await db.execute(
                """
                INSERT INTO contract_review_cards (review_id, clause_id, dimension, score, level, span, suggestion, risk_type)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (review_id, clause_id, dimension) DO NOTHING
                """,
                contract_id,
                card.get("clause_id", ""),
                card.get("dimension", ""),
                card.get("score", 0.0),
                card.get("level", "无"),
                str(card.get("span", "")),
                card.get("suggestion", ""),
                card.get("risk_type", ""),
            )
        logger.info(f"Persisted {len(review_cards)} review cards for contract {contract_id}")
    finally:
        await db.close()


async def _persist_evidence(contract_id: int, evidence_map: dict[str, list[dict[str, Any]]]) -> None:
    """Save evidence records to contract_evidence table."""
    db = await _get_db()
    try:
        count = 0
        for clause_id, evidence_list in evidence_map.items():
            for ev in evidence_list:
                await db.execute(
                    """
                    INSERT INTO contract_evidence (review_id, clause_id, source_id, source_collection, quote, relevance, confidence, is_human_review, href)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    contract_id,
                    clause_id,
                    ev.get("source_id", ""),
                    ev.get("source_collection", ""),
                    ev.get("quote", ""),
                    ev.get("relevance", ""),
                    ev.get("confidence", 0.0),
                    ev.get("is_human_review", False),
                    ev.get("href", ""),
                )
                count += 1
        logger.info(f"Persisted {count} evidence records for contract {contract_id}")
    finally:
        await db.close()


async def _persist_revisions(contract_id: int, revisions: list[dict[str, Any]]) -> None:
    """Save revisions to revision_accepts table."""
    if not revisions:
        return
    db = await _get_db()
    try:
        for rev in revisions:
            await db.execute(
                """
                INSERT INTO revision_accepts (review_id, clause_id, before_text, after_text, diff_html, evidence_ids, status, idempotent_key)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7)
                ON CONFLICT DO NOTHING
                """,
                contract_id,
                rev.get("clause_id", ""),
                rev.get("before_text", ""),
                rev.get("after_text", ""),
                rev.get("diff_html", ""),
                _json.dumps(rev.get("evidence_ids", []), ensure_ascii=False),
                f"rev_{contract_id}_{rev.get('clause_id', '')}"[:128],
            )
        logger.info(f"Persisted {len(revisions)} revisions for contract {contract_id}")
    finally:
        await db.close()


# ── Wrapper nodes with progress + persistence ───────────────

def _make_node(
    name: str,
    fn,
    progress_callback: ProgressCallback | None = None,
) -> Callable:
    """Wrap a pipeline node with progress reporting and DB persistence."""

    async def wrapped(state: ContractReviewState) -> dict[str, Any]:
        logger.info(f"Node [{name}] started")
        if progress_callback:
            progress_callback(name, "started", {})

        try:
            result = await fn(state)
            logger.info(f"Node [{name}] completed")

            # Persist results to DB after each node
            contract_id = state.get("contract_id", 0)
            if contract_id:
                if name == "parse" and result.get("clauses"):
                    await _persist_clauses(contract_id, result["clauses"])
                elif name == "review" and result.get("review_cards"):
                    await _persist_review_cards(contract_id, result["review_cards"])
                elif name == "retrieve" and result.get("evidence_map"):
                    await _persist_evidence(contract_id, result["evidence_map"])
                elif name == "revise" and result.get("revisions"):
                    await _persist_revisions(contract_id, result["revisions"])

            if progress_callback:
                progress_callback(name, "completed", result)
            return result
        except Exception as e:
            logger.error(f"Node [{name}] failed: {e}")
            if progress_callback:
                progress_callback(name, "failed", {"error": str(e)})
            return {"error": str(e), "status": "error"}

    return wrapped


# ── Graph builder ───────────────────────────────────────────

def build_contract_review_graph(
    progress_callback: ProgressCallback | None = None,
) -> StateGraph:
    graph = StateGraph(ContractReviewState)

    graph.add_node("parse", _make_node("parse", clause_parser_node, progress_callback))
    graph.add_node("review", _make_node("review", multi_dim_review_node, progress_callback))
    graph.add_node("retrieve", _make_node("retrieve", rag_retriever_node, progress_callback))
    graph.add_node("revise", _make_node("revise", revision_writer_node, progress_callback))

    graph.set_entry_point("parse")
    graph.add_edge("parse", "review")
    graph.add_edge("review", "retrieve")
    graph.add_edge("retrieve", "revise")
    graph.add_edge("revise", END)

    return graph


def get_contract_review_app(
    progress_callback: ProgressCallback | None = None,
    checkpointer: Any = None,
) -> Any:
    graph = build_contract_review_graph(progress_callback)
    if checkpointer is None:
        checkpointer = get_memory_saver("contract_review")
    return graph.compile(checkpointer=checkpointer)


_memory_savers: dict[str, MemorySaver] = {}

def get_memory_saver(name: str = "contract_review") -> MemorySaver:
    if name not in _memory_savers:
        _memory_savers[name] = MemorySaver()
    return _memory_savers[name]


async def run_contract_review(
    contract_id: int,
    user_id: int,
    text: str,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    app = get_contract_review_app(progress_callback)

    initial_state: ContractReviewState = {
        "contract_id": contract_id,
        "user_id": user_id,
        "text": text,
        "contract_type": "",
        "clauses": [],
        "review_cards": [],
        "degraded_review": False,
        "evidence_map": {},
        "revisions": [],
        "iteration_count": 0,
        "max_iterations": 10,  # raise from 1 to avoid recursion limit
        "visited_clause_ids": [],
        "error": "",
        "status": "pending",
    }

    thread_id = f"contract_{contract_id}_run_1"
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

    logger.info(f"Starting contract review: contract_id={contract_id}, thread={thread_id}")
    final_state = await app.ainvoke(initial_state, config)
    logger.info(f"Contract review complete: contract_id={contract_id}, status={final_state.get('status', 'unknown')}")

    # Persist final results if not already persisted by node wrappers
    clauses = final_state.get("clauses", [])
    review_cards = final_state.get("review_cards", [])
    evidence_map = final_state.get("evidence_map", {})
    revisions = final_state.get("revisions", [])

    if clauses:
        await _persist_clauses(contract_id, clauses)
    if evidence_map:
        await _persist_evidence(contract_id, evidence_map)
    if revisions:
        await _persist_revisions(contract_id, revisions)

    # Also update contract_type from parsed result
    if final_state.get("contract_type"):
        db = await _get_db()
        try:
            await db.execute(
                "UPDATE contract_reviews SET contract_type = $2, updated_at = NOW() WHERE id = $1",
                contract_id, final_state["contract_type"],
            )
        finally:
            await db.close()

    logger.info(f"  clauses={len(clauses)}, review_cards={len(review_cards)}, evidence_map keys={len(evidence_map)}, revisions={len(revisions)}")
    return final_state


# ── Escape hatch: run pipeline without LangGraph serialization ───

async def run_contract_review_direct(
    contract_id: int,
    user_id: int,
    text: str,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run pipeline sequentially, bypassing LangGraph serialization issues.

    ``progress_callback`` receives ``(node_name, event, data)`` with event
    'started' / 'completed' / 'failed' around each node — the same contract
    as the LangGraph node wrappers. The API layer uses it to push the
    reviewing / retrieving / revising statuses that the SSE stream polls;
    without it the DB status sits on 'parsing' until it jumps to 'completed'
    and the frontend progress bar freezes then jumps. The callback may be
    sync or async; a failing callback never breaks the pipeline.
    """
    import inspect

    async def _fire(name: str, event: str, data: dict[str, Any]) -> None:
        if not progress_callback:
            return
        try:
            ret = progress_callback(name, event, data)
            if inspect.isawaitable(ret):
                await ret
        except Exception:
            logger.exception(
                f"progress callback failed at {name}/{event} (non-fatal)"
            )

    state: dict[str, Any] = {
        "contract_id": contract_id,
        "user_id": user_id,
        "text": text,
        "contract_type": "",
        "clauses": [],
        "review_cards": [],
        "degraded_review": False,
        "evidence_map": {},
        "revisions": [],
        "iteration_count": 0,
        "max_iterations": 10,
        "visited_clause_ids": [],
        "error": "",
        "status": "pending",
    }

    logger.info(f"Starting direct contract review: contract_id={contract_id}")

    # Node 1: parse
    logger.info("Node [parse] started")
    await _fire("parse", "started", {})
    try:
        r1 = await clause_parser_node(state)  # type: ignore[arg-type]
    except Exception as e:
        await _fire("parse", "failed", {"error": str(e)})
        raise
    state.update(r1)
    if state.get("clauses"):
        await _persist_clauses(contract_id, state["clauses"])
    await _fire("parse", "completed", r1)
    logger.info(f"Node [parse] done: {len(state.get('clauses', []))} clauses, type={state.get('contract_type', '')}")

    # Node 2: review
    logger.info("Node [review] started")
    await _fire("review", "started", {})
    try:
        r2 = await multi_dim_review_node(state)  # type: ignore[arg-type]
    except Exception as e:
        await _fire("review", "failed", {"error": str(e)})
        raise
    state.update(r2)
    if state.get("review_cards"):
        await _persist_review_cards(contract_id, state["review_cards"])
    await _fire("review", "completed", r2)
    logger.info(f"Node [review] done: {len(state.get('review_cards', []))} risk cards")

    # Node 3: retrieve
    logger.info("Node [retrieve] started")
    await _fire("retrieve", "started", {})
    try:
        r3 = await rag_retriever_node(state)  # type: ignore[arg-type]
    except Exception as e:
        await _fire("retrieve", "failed", {"error": str(e)})
        raise
    state.update(r3)
    evidence_count = sum(len(v) for v in state.get("evidence_map", {}).values())
    if state.get("evidence_map"):
        await _persist_evidence(contract_id, state["evidence_map"])
    await _fire("retrieve", "completed", r3)
    logger.info(f"Node [retrieve] done: {evidence_count} evidence records")

    # Node 4: revise
    logger.info("Node [revise] started")
    await _fire("revise", "started", {})
    try:
        r4 = await revision_writer_node(state)  # type: ignore[arg-type]
    except Exception as e:
        await _fire("revise", "failed", {"error": str(e)})
        raise
    state.update(r4)
    if state.get("revisions"):
        await _persist_revisions(contract_id, state["revisions"])
    await _fire("revise", "completed", r4)
    logger.info(f"Node [revise] done: {len(state.get('revisions', []))} revisions")

    if state.get("contract_type"):
        db = await _get_db()
        try:
            await db.execute(
                "UPDATE contract_reviews SET contract_type = $2, updated_at = NOW() WHERE id = $1",
                contract_id, state["contract_type"],
            )
        finally:
            await db.close()

    logger.info(f"Direct review complete: clauses={len(state.get('clauses',[]))}, cards={len(state.get('review_cards',[]))}, evidence_map keys={len(state.get('evidence_map',{}))}, revisions={len(state.get('revisions',[]))}")
    return state
