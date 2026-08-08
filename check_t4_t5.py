"""T4+T5 verification: numeral round-trip, context expansion, dedup, chapter boundary, sparse probe."""
import asyncio
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from backend.core.rag import get_kb_client, _get_embedding_model
from backend.agents.contract_review.rag_retriever import (
    cn_to_int, int_to_cn, _parse_article_no, _format_article_no,
    _build_expanded_context,
)

OUT = r"C:\Users\小珅的雷神\.qoderworkcn\workspace\msiffglzwlehlccf\check_t4t5_out.txt"
lines = []
fails = []

def ok(cond, msg):
    lines.append(f"{'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        fails.append(msg)

COL = "civil_code_hybrid"

async def main():
    # ── [1] explicit conversion cases (零 handling) ──
    cases = [
        (1, "一"), (10, "十"), (15, "十五"), (101, "一百零一"),
        (110, "一百一十"), (508, "五百零八"), (988, "九百八十八"),
        (1001, "一千零一"), (1260, "一千二百六十"),
    ]
    all_pass = True
    for n, cn in cases:
        if int_to_cn(n) != cn or cn_to_int(cn) != n:
            all_pass = False
            lines.append(f"  conv mismatch: {n} <-> {cn} (got {int_to_cn(n)} / {cn_to_int(cn)})")
    ok(all_pass, "[1] explicit conversions incl. 零 (101/508/1001)")

    # ── [2] round-trip over ALL article_nos in the collection ──
    kb = get_kb_client()
    kb.connect()
    from pymilvus import MilvusClient
    client = MilvusClient(uri="http://localhost:19530")
    all_nos = []
    offset = 0
    while True:
        rows = client.query(collection_name=COL, filter="id != ''",
                            output_fields=["article_no"], limit=500, offset=offset)
        if not rows:
            break
        all_nos.extend(r["article_no"] for r in rows)
        offset += len(rows)
        if len(rows) < 500:
            break
    ok(len(all_nos) == 988, f"[2a] collection has 988 articles (got {len(all_nos)})")

    bad = []
    parsed_set = set()
    for a in all_nos:
        n = _parse_article_no(a)
        if n <= 0 or _format_article_no(n) != a:
            bad.append(a)
        else:
            parsed_set.add(n)
    ok(not bad, f"[2b] round-trip parse/format for all articles (bad={bad[:5]})")
    ok(parsed_set == set(range(1, 989)), "[2c] parsed numbers == 1..988")

    # ── [3] expansion on Article 474 (expects ref 137 + adjacents) ──
    rows = kb.query_articles(COL, ["第四百七十四条"])
    ok(bool(rows), "[3a] fetched Article 474 for expansion test")
    hit = dict(rows[0])
    hit["confidence"] = 0.8
    cache = {}
    exp = await _build_expanded_context([hit], kb, cache, COL)
    nos = sorted(_parse_article_no(e["article_no"]) for e in exp)
    lines.append(f"  expanded articles: {nos}")
    for e in exp:
        lines.append(f"    {e['article_no']}  kind={e.get('expand_kind')}  conf={e['confidence']}  chapter={e.get('chapter')}")
    ok(137 in nos, "[3b] cross-ref Article 137 expanded")
    ok(all(n in nos for n in (472, 473, 475, 476)), "[3c] adjacent 472/473/475/476 expanded")
    ok(all(e.get("expanded") is True for e in exp), "[3d] all expanded items flagged expanded=True")
    ok(all(abs(e["confidence"] - 0.72) < 1e-6 for e in exp), "[3e] expanded confidence = 0.8*0.9")
    ok(len(exp) <= 8, f"[3f] expansion capped at 8 (got {len(exp)})")

    # ── [4] cache dedup: second call adds nothing ──
    exp2 = await _build_expanded_context([hit], kb, cache, COL)
    ok(len(exp2) == 0, f"[4] cache dedup: second expansion empty (got {len(exp2)})")

    # ── [5] 编 boundary: Article 204 (总则 end) → 205/206 (物权) must be filtered ──
    b = kb.query_articles(COL, ["第二百零四条", "第二百零五条"])
    ch = {r["article_no"]: r.get("chapter", "") for r in b}
    lines.append(f"  chapters: {ch}")
    if len(ch) == 2 and ch.get("第二百零四条") != ch.get("第二百零五条"):
        hit204 = dict(kb.query_articles(COL, ["第二百零四条"])[0])
        hit204["confidence"] = 0.8
        cache2 = {}
        exp3 = await _build_expanded_context([hit204], kb, cache2, COL)
        nos3 = sorted(_parse_article_no(e["article_no"]) for e in exp3)
        lines.append(f"  expanded from Art.204: {nos3}")
        ok(202 in nos3 and 203 in nos3, "[5a] same-编 adjacents 202/203 kept")
        ok(205 not in nos3 and 206 not in nos3, "[5b] cross-编 adjacents 205/206 filtered")
    else:
        lines.append("  (skip boundary test: chapters identical or rows missing)")

    # ── [6] sparse probe: sparse-only ANN must return hits ──
    model = _get_embedding_model()
    query = "违约责任承担方式"
    encoded = model.encode([query], return_dense=True, return_sparse=True, return_colbert_vecs=False)
    dense_vec = encoded["dense_vecs"][0].tolist()
    sparse_dict = {int(k): float(v) for k, v in encoded["lexical_weights"][0].items()}
    from pymilvus import AnnSearchRequest
    req = AnnSearchRequest(data=[sparse_dict], anns_field="sparse_vector",
                           param={"metric_type": "IP", "params": {"drop_ratio_search": 0.0}}, limit=5)
    try:
        sres = client.hybrid_search(collection_name=COL, reqs=[req], output_fields=["article_no"], limit=5)
    except Exception as e:
        from pymilvus import WeightedRanker
        lines.append(f"  (sparse probe retry with ranker: {e})")
        sres = client.hybrid_search(collection_name=COL, reqs=[req], ranker=WeightedRanker(1.0),
                                    output_fields=["article_no"], limit=5)
    sparse_hits = [h["entity"].get("article_no") for h in sres[0]] if sres and sres[0] else []
    ok(len(sparse_hits) > 0, f"[6a] sparse-only ANN returns hits: {sparse_hits}")

    # full pipeline search
    full = await kb.hybrid_search(query=query, collection=COL, top_k=10, rerank_top_k=3)
    for r in full:
        lines.append(f"  hybrid+rerank: {r['article_no']}  conf={r['confidence']}  hybrid={r['hybrid_score']}")
    ok(len(full) == 3, f"[6b] hybrid_search returns rerank_top_k=3 (got {len(full)})")
    ok(all(r.get("article_no") for r in full), "[6c] results carry article_no")
    top_nos = [_parse_article_no(r["article_no"]) for r in full]
    ok(577 in top_nos, f"[6d] Art.577 (违约责任 general clause) in top-3: {top_nos}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.write("ALL_CHECKS_PASSED\n" if not fails else f"FAILED: {len(fails)}\n")
    print("done")

asyncio.run(main())
