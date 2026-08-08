"""Verify rag_retriever node wiring end-to-end WITHOUT LLM (retrieval is LLM-free)."""
import asyncio
import sys

sys.path.insert(0, "D:/contract")
sys.stdout.reconfigure(encoding="utf-8")

OUT = r"C:\Users\小珅的雷神\.qoderworkcn\workspace\msiffglzwlehlccf\retrieve_node_out.txt"
lines = []
fails = []

def ok(cond, msg):
    lines.append(f"{'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        fails.append(msg)

async def main():
    from backend.agents.contract_review.rag_retriever import retrieve_evidence

    cards = [
        {"clause_id": "C2", "risk_type": "违约风险", "dimension": "legal",
         "suggestion": "违约金为合同总额50%，过高，建议调整"},
        {"clause_id": "C1", "risk_type": "付款条件不合理", "dimension": "financial",
         "suggestion": "付款节点不明确"},
    ]
    clauses = [
        {"clause_id": "C1", "title": "付款条款", "text": "甲方向乙方采购货物，分期付款。"},
        {"clause_id": "C2", "title": "违约责任", "text": "违约金为合同总额50%。"},
    ]

    evidence = await retrieve_evidence(cards, clauses)
    ok(len(evidence) > 0, f"[1] evidence produced: {len(evidence)} records")

    by_clause = {}
    for ev in evidence:
        by_clause.setdefault(ev["clause_id"], []).append(ev)
    ok("C1" in by_clause and "C2" in by_clause, f"[2] both clauses covered: {sorted(by_clause)}")

    # evidence quality: source_id must be non-empty (real citations)
    empty_src = [ev for ev in evidence if not ev.get("source_id")]
    ok(not empty_src, f"[3] all evidence have source_id (empty={len(empty_src)})")

    # evidence quotes should be real civil-code text (contain 当事人/合同/义务 etc.)
    legal_hits = sum(1 for ev in evidence if any(k in ev.get("quote", "") for k in ("当事人", "合同", "义务", "违约", "约定")))
    ok(legal_hits >= len(evidence) // 2, f"[4] legal-text quotes: {legal_hits}/{len(evidence)}")

    # expansion evidence present (expanded items also become evidence)
    for cid, evs in by_clause.items():
        lines.append(f"  clause {cid}: {len(evs)} evidence")
        for ev in evs:
            q = ev["quote"][:40].replace("\n", " ")
            lines.append(f"    src={ev['source_id'][:12]}.. conf={ev['confidence']:.3f} review={ev['is_human_review']}  {q}")

    # human-review flags sane: not ALL flagged (threshold 0.30 calibration)
    flagged = sum(1 for ev in evidence if ev["is_human_review"])
    ok(flagged < len(evidence), f"[5] threshold calibration: {flagged}/{len(evidence)} flagged human-review")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.write("ALL_CHECKS_PASSED\n" if not fails else f"FAILED: {len(fails)}\n")
    print("done")

asyncio.run(main())
