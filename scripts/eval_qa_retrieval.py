# -*- coding: utf-8 -*-
"""
T0 QA retrieval baseline evaluation.

Runs the PRODUCTION retrieval path (context_builder.build_qa_context) against
scripts/qa_golden_set.json and reports recall@3 / MRR / false-citation /
latency. This is the yardstick every later improvement (T1~T5) is compared to.

Usage:
    .venv/Scripts/python.exe scripts/eval_qa_retrieval.py [--contract-id N]

Outputs (UTF-8):
    outputs/eval_<ts>.md    human-readable report
    outputs/eval_<ts>.json  machine-readable results (for later diffs)

Notes:
  - Scorable types (LAW / COMPOUND): recall = |expected & returned| / |expected|,
    MRR = reciprocal rank of the FIRST expected hit among the returned hits.
    "Returned" is exactly what the user would see as citation cards (<=3 hits).
  - Non-scorable types (CLAUSE / GENERIC / META): the correct behavior is to
    produce NO law citations; we count false citations instead.
  - A warm-up query is issued before timing so BGE-M3 / reranker model loading
    does not pollute latency numbers.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

# Only backend/main.py loads .env.local in production; the eval script needs it
# too because the T3 HyDE retry builds an LLM client from these variables.
load_dotenv(ROOT / ".env.local")

import asyncpg  # noqa: E402

from backend.agents.contract_qa import context_builder as cb  # noqa: E402
from backend.agents.contract_qa.context_builder import (  # noqa: E402
    LAW_COLLECTION,
    LAW_RETRIEVE_THRESHOLD,
    MAX_LAW_HITS,
    build_qa_context,
)
from backend.agents.contract_review.rag_retriever import cn_to_int  # noqa: E402

DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres123@localhost:15432/eduagent")
GOLDEN_PATH = ROOT / "scripts" / "qa_golden_set.json"
OUT_DIR = ROOT / "outputs"

_ART_RE = re.compile(r"^第(.+)条$")
SCORABLE = {"LAW", "COMPOUND"}
TYPE_LABEL = {
    "LAW": "法条", "COMPOUND": "复合", "CLAUSE": "合同", "GENERIC": "库外", "META": "元问题",
}


def art_to_int(article_no: str) -> int:
    m = _ART_RE.match(str(article_no or "").strip())
    return cn_to_int(m.group(1)) if m else 0


def fmt_hits(returned: list[dict]) -> str:
    if not returned:
        return "（无命中）"
    return "、".join(f"{r['article_no']}@{r['conf']:.2f}" for r in returned)


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


async def pick_contract(db) -> int:
    row = await db.fetchrow(
        "SELECT r.id FROM contract_reviews r "
        "JOIN contract_review_cards c ON c.review_id = r.id "
        "GROUP BY r.id ORDER BY COUNT(c.id) DESC LIMIT 1"
    )
    if not row:
        raise SystemExit("No reviewed contract with cards found; run a review first.")
    return row["id"]


def score_item(item: dict, returned: list[dict]) -> dict:
    expected = [int(x) for x in item.get("expected", [])]
    returned_ns = [r["n"] for r in returned]
    if item["type"] in SCORABLE:
        hits = set(expected) & set(returned_ns)
        recall = len(hits) / len(expected) if expected else None
        rr = 0.0
        for i, n in enumerate(returned_ns, start=1):
            if n in expected:
                rr = 1.0 / i
                break
        return {"recall": recall, "mrr": rr if expected else None}
    return {"false_citations": len(returned)}


def mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def build_report(meta: dict, results: list[dict]) -> str:
    buf = io.StringIO()
    w = buf.write
    w("# QA 检索基线评测报告（T0）\n\n")
    w(f"- 生成时间：{meta['ts']}\n")
    w(f"- 检索集合：`{meta['collection']}`（阈值 {meta['threshold']}，top_k {meta['top_k']}）\n")
    w(f"- 评测合同：contract_id={meta['contract_id']}\n")
    hyde_txt = (
        f"T3 HyDE 重试（阈值 {meta['hyde_threshold']}）"
        if meta["hyde"] == "on" else "T3 HyDE 关闭（A/B 对照）"
    )
    w(f"- 流水线状态：元问题门控 + risk_type 查询改写 + T1 意图分类 + T2 多查询拆分 + "
      f"{hyde_txt} + 混合检索/重排 + 硬截断\n")
    w(f"- 黄金集：{meta['total']} 题（法条{meta['n_law']}、复合{meta['n_compound']}、"
      f"合同{meta['n_clause']}、库外{meta['n_generic']}、元问题{meta['n_meta']}）\n\n")

    w("## 逐题明细\n\n")
    w("| # | 类型 | 问题 | 期望条文 | 实际命中（条文@rerank分） | recall | MRR | 误引 | 耗时ms |\n")
    w("|---|---|---|---|---|---|---|---|---|\n")
    for r in results:
        exp = "、".join(str(x) for x in r.get("expected", [])) or "—"
        rec = pct(r.get("recall")) if "recall" in r else "—"
        mrr = pct(r.get("mrr")) if "mrr" in r else "—"
        fc = str(r["false_citations"]) if "false_citations" in r else "—"
        w(f"| {r['id']} | {TYPE_LABEL.get(r['type'], r['type'])}"
          f"{'(口语)' if r.get('style') == 'colloquial' else ''} "
          f"| {r['question']} | {exp} | {fmt_hits(r['returned'])} "
          f"| {rec} | {mrr} | {fc} | {r['latency_ms']} |\n")

    agg = meta["agg"]
    w("\n## 汇总指标\n\n")
    w(f"- 法条题（书面语，{agg['n_formal']}题）：平均 recall@3 = **{pct(agg['recall_formal'])}**，"
      f"平均 MRR = {pct(agg['mrr_formal'])}\n")
    w(f"- 法条题（口语化，{agg['n_colloquial']}题）：平均 recall@3 = **{pct(agg['recall_colloquial'])}**，"
      f"平均 MRR = {pct(agg['mrr_colloquial'])}\n")
    w(f"- 复合题（{agg['n_compound_scored']}题）：平均 recall@3 = **{pct(agg['recall_compound'])}**\n")
    w(f"- 可评分题总 recall@3 = **{pct(agg['recall_all'])}**，总 MRR = {pct(agg['mrr_all'])}\n")
    w(f"- 非检索题误引用：**{agg['false_cite_count']}/{agg['n_nonscorable']} 题**"
      f"（合同{agg['fc_clause']}、库外{agg['fc_generic']}、元问题{agg['fc_meta']}）\n")
    w(f"- 检索耗时：p50 = {agg['p50_ms']}ms，p95 = {agg['p95_ms']}ms\n")

    w("\n## 观察清单（自动生成）\n\n")
    misses = [r for r in results if r["type"] in SCORABLE and r.get("recall") is not None and r["recall"] < 1.0]
    if misses:
        w("**召回未满分（漏检/部分漏检）的可评分题**：\n\n")
        for r in misses:
            got = "、".join(str(x) for x in [h["n"] for h in r["returned"]]) or "无"
            w(f"- {r['id']} [{r['question']}] 期望 {'/'.join(map(str, r['expected']))}，实际 {got}\n")
    else:
        w("- 可评分题全部满分召回。\n")
    fc_items = [r for r in results if r.get("false_citations", 0) > 0]
    w("\n**产生了法条引用的非检索题（意图泄漏）**：\n\n")
    if fc_items:
        for r in fc_items:
            w(f"- {r['id']} [{TYPE_LABEL.get(r['type'], r['type'])}] {r['question']} → {fmt_hits(r['returned'])}\n")
    else:
        w("- 无。\n")
    w("\n> 本报告为 T0 基线。后续 T1~T5 每个任务完成后重跑本脚本，与本文件逐指标对比。\n")
    return buf.getvalue()


async def main() -> None:
    ap = argparse.ArgumentParser(description="QA retrieval baseline evaluation")
    ap.add_argument("--contract-id", type=int, default=None)
    ap.add_argument("--hyde-off", action="store_true",
                    help="disable the T3 HyDE retry for A/B comparison")
    args = ap.parse_args()

    if args.hyde_off:
        cb.HYDE_ENABLED = False

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    db = await asyncpg.connect(DSN)
    try:
        cid = args.contract_id or await pick_contract(db)
        print(f"[eval] contract_id={cid} collection={LAW_COLLECTION} "
              f"threshold={LAW_RETRIEVE_THRESHOLD} top_k={MAX_LAW_HITS} "
              f"hyde={'on' if cb.HYDE_ENABLED else 'off'} "
              f"(retry_threshold={cb.HYDE_RETRY_THRESHOLD}) questions={len(golden)}")

        print("[eval] warming up models (not timed) ...")
        await build_qa_context(db, cid, "违约责任")

        results: list[dict] = []
        for i, item in enumerate(golden, 1):
            t0 = time.perf_counter()
            ctx = await build_qa_context(db, cid, item["question"])
            dt_ms = int((time.perf_counter() - t0) * 1000)
            returned = []
            for h in ctx.get("law_hits", []):
                returned.append({
                    "n": art_to_int(h.get("article_no", "")),
                    "article_no": h.get("article_no", ""),
                    "conf": round(float(h.get("confidence", 0.0)), 4),
                })
            metrics = score_item(item, returned)
            results.append({**item, "returned": returned, "latency_ms": dt_ms, **metrics})
            tag = (f"recall={metrics['recall']:.2f}" if "recall" in metrics
                   else f"false_cites={metrics['false_citations']}")
            print(f"[eval] {i:02d}/{len(golden)} {item['id']} {tag} {dt_ms}ms")
    finally:
        await db.close()

    # ── Aggregates ──────────────────────────────────────────
    formal = [r for r in results if r["type"] == "LAW" and r.get("style") == "formal"]
    colloq = [r for r in results if r["type"] == "LAW" and r.get("style") == "colloquial"]
    compound = [r for r in results if r["type"] == "COMPOUND"]
    scorables = [r for r in results if r["type"] in SCORABLE]
    nonscorables = [r for r in results if r["type"] not in SCORABLE]
    lat = sorted(r["latency_ms"] for r in results)

    agg = {
        "n_formal": len(formal),
        "n_colloquial": len(colloq),
        "n_compound_scored": len(compound),
        "recall_formal": mean([r["recall"] for r in formal]),
        "mrr_formal": mean([r["mrr"] for r in formal]),
        "recall_colloquial": mean([r["recall"] for r in colloq]),
        "mrr_colloquial": mean([r["mrr"] for r in colloq]),
        "recall_compound": mean([r["recall"] for r in compound]),
        "recall_all": mean([r["recall"] for r in scorables]),
        "mrr_all": mean([r["mrr"] for r in scorables]),
        "false_cite_count": sum(1 for r in nonscorables if r["false_citations"] > 0),
        "n_nonscorable": len(nonscorables),
        "fc_clause": sum(1 for r in nonscorables if r["type"] == "CLAUSE" and r["false_citations"] > 0),
        "fc_generic": sum(1 for r in nonscorables if r["type"] == "GENERIC" and r["false_citations"] > 0),
        "fc_meta": sum(1 for r in nonscorables if r["type"] == "META" and r["false_citations"] > 0),
        "p50_ms": lat[len(lat) // 2] if lat else 0,
        "p95_ms": lat[max(0, int(len(lat) * 0.95) - 1)] if lat else 0,
    }
    meta = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "collection": LAW_COLLECTION,
        "threshold": LAW_RETRIEVE_THRESHOLD,
        "top_k": MAX_LAW_HITS,
        "hyde": "on" if cb.HYDE_ENABLED else "off",
        "hyde_threshold": cb.HYDE_RETRY_THRESHOLD,
        "contract_id": cid,
        "total": len(results),
        "n_law": len([r for r in results if r["type"] == "LAW"]),
        "n_compound": len(compound),
        "n_clause": len([r for r in results if r["type"] == "CLAUSE"]),
        "n_generic": len([r for r in results if r["type"] == "GENERIC"]),
        "n_meta": len([r for r in results if r["type"] == "META"]),
        "agg": agg,
    }

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = OUT_DIR / f"eval_{stamp}.md"
    json_path = OUT_DIR / f"eval_{stamp}.json"
    md_path.write_text(build_report(meta, results), encoding="utf-8")
    json_path.write_text(json.dumps({"meta": meta, "results": results},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[eval] report: {md_path}")
    print(f"[eval] json:   {json_path}")
    print(f"[eval] recall_all={pct(agg['recall_all'])} false_cites={agg['false_cite_count']}/{agg['n_nonscorable']}")


if __name__ == "__main__":
    asyncio.run(main())
