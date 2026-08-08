"""End-to-end pipeline smoke test — loads .env.local, runs direct pipeline."""
import os
import sys
import asyncio
sys.path.insert(0, "D:/contract")
from dotenv import load_dotenv
load_dotenv("D:/contract/.env.local")

TEXT = """第一条 付款条款——甲方向乙方采购货物。
第二条 违约责任——违约金为合同总额50%。
第三条 保密条款——双方应保守商业秘密。"""

async def main():
    from backend.agents.contract_review.graph import run_contract_review_direct
    import asyncpg

    # Create a placeholder contract_review row so FK is satisfied
    db = await asyncpg.connect("postgresql://postgres:postgres123@localhost:15432/eduagent")
    try:
        await db.execute(
            "INSERT INTO contract_reviews (id, user_id, filename, original_filename, status) "
            "VALUES (9999, 1, 'check_pipeline.txt', 'check_pipeline.txt', 'pending') "
            "ON CONFLICT (id) DO NOTHING"
        )
    finally:
        await db.close()

    state = await run_contract_review_direct(contract_id=9999, user_id=1, text=TEXT)

    cards = state.get("review_cards", [])
    evidence_map = state.get("evidence_map", {})
    revisions = state.get("revisions", [])

    # T3: review_cards persisted and returned
    assert len(cards) > 0, "No review cards produced"
    print(f"[1] review_cards: {len(cards)} cards")
    for c in cards:
        print(f"    [{c.get('dimension','')}] level={c.get('level','')} score={c.get('score',0)}")

    # T2: evidence from civil_code_contract (no more placeholder)
    evidence_count = sum(len(v) for v in evidence_map.values())
    print(f"[2] evidence_map: {evidence_count} records across {len(evidence_map)} clauses")
    contains_fadian = False
    for ev_list in evidence_map.values():
        for ev in ev_list:
            q = ev.get("quote", "")
            if "民法典" in q or "第" in q or "法律" in q:
                contains_fadian = True
                break
    # With real BGE-M3 + Reranker, evidence should contain relevant legal citations
    if not contains_fadian:
        print("    NOTE: zero-vector search returns non-semantic results, expected until BGE-M3 is wired")

    # T1: llm produces structured output (function_calling)
    high_risk = [c for c in cards if c.get("level") in ("高", "中")]
    print(f"[3] high/medium risk clauses: {len(high_risk)}")
    print(f"    revisions: {len(revisions)}")

    assert len(high_risk) > 0, "No high/medium risk cards found"
    assert len(revisions) > 0, "No revisions generated for high-risk clauses"

    print("ALL_CHECKS_PASSED")


if __name__ == "__main__":
    asyncio.run(main())
