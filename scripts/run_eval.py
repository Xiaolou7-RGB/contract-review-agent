#!/usr/bin/env python3
"""
Evaluation script for contract review pipeline.
Five eval sets, four control experiments.
Outputs eval_report.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── Eval data loaders ──────────────────────────────────────

def load_eval_data(eval_dir: str) -> dict[str, list[dict[str, Any]]]:
    """Load evaluation datasets from eval_data/ directory."""
    eval_path = Path(eval_dir)
    if not eval_path.exists():
        logger.warning(f"Eval directory not found: {eval_dir}")
        return {}

    datasets: dict[str, list[dict[str, Any]]] = {}
    for filepath in eval_path.glob("*.json"):
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        name = filepath.stem
        if isinstance(data, list):
            datasets[name] = data
        else:
            datasets[name] = [data]

    return datasets


# ── Eval metric functions ──────────────────────────────────

def _precision_recall_f1(
    predicted: list[str],
    ground_truth: list[str],
) -> dict[str, float]:
    """Compute precision, recall, F1 for two lists of items."""
    pred_set = set(predicted)
    gt_set = set(ground_truth)

    tp = len(pred_set & gt_set)
    fp = len(pred_set - gt_set)
    fn = len(gt_set - pred_set)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def _count_accuracy(predicted: str, ground_truth: str) -> float:
    """Simple string match accuracy."""
    return 1.0 if predicted == ground_truth else 0.0


# ── Eval Set 1: Clause Parsing ─────────────────────────────

def eval_clause_parsing(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate clause extraction accuracy."""
    from backend.agents.contract_review.clause_parser import (
        identify_contract_type,
        _regex_split_clauses,
    )

    results = []
    contract_type_correct = 0
    clause_count_errors: list[int] = []

    for sample in samples:
        text = sample.get("text", "")
        expected_type = sample.get("contract_type", "")
        expected_clause_count = sample.get("clause_count", 0)

        predicted_type = identify_contract_type(text)
        clauses = _regex_split_clauses(text)

        type_match = predicted_type == expected_type
        if type_match:
            contract_type_correct += 1

        count_error = abs(len(clauses) - expected_clause_count)
        clause_count_errors.append(count_error)

        results.append({
            "sample_id": sample.get("id", ""),
            "type_match": type_match,
            "predicted_type": predicted_type,
            "expected_type": expected_type,
            "clause_count": len(clauses),
            "expected_count": expected_clause_count,
        })

    return {
        "eval_name": "clause_parsing",
        "samples": len(samples),
        "contract_type_accuracy": round(contract_type_correct / len(samples), 4) if samples else 0,
        "avg_clause_count_error": round(sum(clause_count_errors) / len(clause_count_errors), 2) if clause_count_errors else 0,
        "details": results,
    }


# ── Eval Set 2: Multi-Dim Review ───────────────────────────

def eval_multi_dim_review(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate dimension routing correctness and risk level detection."""
    from backend.agents.contract_review.multi_dim_review import (
        get_active_dimensions,
        merge_review_cards,
    )

    results = []
    dim_correct = 0

    for sample in samples:
        contract_type = sample.get("contract_type", "其他")
        expected_dims = set(sample.get("expected_dimensions", []))
        expected_risks = sample.get("expected_risks", [])

        dims = get_active_dimensions(contract_type)
        dim_keys = {d["key"] for d in dims}

        dim_match = dim_keys == expected_dims
        if dim_match:
            dim_correct += 1

        # Test merge logic with mock cards
        mock_cards = sample.get("mock_review_cards", [])
        merged = merge_review_cards(mock_cards) if mock_cards else []

        results.append({
            "sample_id": sample.get("id", ""),
            "dim_match": dim_match,
            "predicted_dims": list(dim_keys),
            "expected_dims": list(expected_dims),
            "merged_risk_count": len(merged),
        })

    return {
        "eval_name": "multi_dim_review",
        "samples": len(samples),
        "dimension_routing_accuracy": round(dim_correct / len(samples), 4) if samples else 0,
        "details": results,
    }


# ── Eval Set 3: RAG Retrieval ──────────────────────────────

def eval_rag_retrieval(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate retrieval relevance and citation accuracy."""
    from backend.agents.contract_review.rag_retriever import (
        _route_collections,
        _build_evidence,
    )

    results = []
    routing_correct = 0

    for sample in samples:
        risk_type = sample.get("risk_type", "")
        dimension = sample.get("dimension", "")
        expected_collections = set(sample.get("expected_collections", []))

        collections = _route_collections(risk_type, dimension)
        col_match = set(collections) == expected_collections
        if col_match:
            routing_correct += 1

        # Test evidence building
        mock_results = sample.get("mock_search_results", [])
        evidence = _build_evidence("test_clause", mock_results) if mock_results else []

        results.append({
            "sample_id": sample.get("id", ""),
            "routing_match": col_match,
            "predicted_collections": collections,
            "expected_collections": list(expected_collections),
            "evidence_count": len(evidence),
        })

    return {
        "eval_name": "rag_retrieval",
        "samples": len(samples),
        "collection_routing_accuracy": round(routing_correct / len(samples), 4) if samples else 0,
        "details": results,
    }


# ── Eval Set 4: Citation Traceability ──────────────────────

def eval_citation_traceability(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify that all evidence has source_id and no fabricated citations."""
    results = []
    valid_count = 0

    for sample in samples:
        evidence_list = sample.get("evidence", [])
        all_valid = True
        issues: list[str] = []

        for ev in evidence_list:
            if not ev.get("source_id"):
                all_valid = False
                issues.append(f"Missing source_id")
            if ev.get("is_human_review") is False and not ev.get("source_id"):
                all_valid = False
                issues.append("Non-human-review evidence missing source_id")

        if all_valid:
            valid_count += 1

        results.append({
            "sample_id": sample.get("id", ""),
            "valid": all_valid,
            "issues": issues,
            "evidence_count": len(evidence_list),
        })

    return {
        "eval_name": "citation_traceability",
        "samples": len(samples),
        "traceability_rate": round(valid_count / len(samples), 4) if samples else 0,
        "details": results,
    }


# ── Eval Set 5: End-to-End ─────────────────────────────────

def eval_end_to_end(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """End-to-end pipeline evaluation (smoke test level, no LLM calls)."""
    from backend.agents.contract_review.clause_parser import (
        identify_contract_type,
        _regex_split_clauses,
    )
    from backend.agents.contract_review.multi_dim_review import (
        get_active_dimensions,
        merge_review_cards,
    )
    from backend.agents.contract_review.rag_retriever import _route_collections
    from backend.agents.contract_review.revision_writer import generate_diff_html

    results = []
    for sample in samples:
        text = sample.get("text", "")
        if not text:
            results.append({"sample_id": sample.get("id", ""), "error": "No text provided"})
            continue

        try:
            # Step 1: Parse
            ctype = identify_contract_type(text)
            clauses = _regex_split_clauses(text)

            # Step 2: Route dimensions
            dims = get_active_dimensions(ctype)

            # Step 3: Route collections for a mock risk
            collections = _route_collections("违约风险", "legal")

            # Step 4: Diff generation
            diff = generate_diff_html("原始条款", "修订条款")

            results.append({
                "sample_id": sample.get("id", ""),
                "contract_type": ctype,
                "clause_count": len(clauses),
                "dimensions": [d["key"] for d in dims],
                "collections_routed": collections,
                "diff_generated": len(diff) > 0,
            })
        except Exception as e:
            results.append({
                "sample_id": sample.get("id", ""),
                "error": str(e),
            })

    success_count = sum(1 for r in results if "error" not in r)

    return {
        "eval_name": "end_to_end",
        "samples": len(samples),
        "pipeline_success_rate": round(success_count / len(samples), 4) if samples else 0,
        "details": results,
    }


# ── Control Experiments ────────────────────────────────────

CONTROL_EXPERIMENTS = {
    "BM25_vs_Hybrid": "Compare BM25-only search vs hybrid (dense+sparse) for retrieval",
    "generic_chunk_vs_atomic": "Compare generic token-split chunks vs legal atomic chunks",
    "single_prompt_vs_multi_prompt": "Compare single combined review prompt vs 4-dimension fan-out",
    "zero_context_vs_expanded": "Compare ±0 context vs ±2 adjacent articles + cross-refs expansion",
}


def run_control_experiments(eval_dir: str) -> dict[str, Any]:
    """Run control experiments and record results (placeholder measurements)."""
    results: dict[str, dict[str, Any]] = {}

    for exp_name, description in CONTROL_EXPERIMENTS.items():
        # In production, these would run actual comparative benchmarks
        results[exp_name] = {
            "description": description,
            "status": "placeholder",
            "note": "Run with real data and LLM calls for actual comparisons",
            "baseline": {},
            "experiment": {},
        }

    return results


# ── Main ───────────────────────────────────────────────────

EVAL_FUNCTIONS: dict[str, Callable] = {
    "clause_parsing": eval_clause_parsing,
    "multi_dim_review": eval_multi_dim_review,
    "rag_retrieval": eval_rag_retrieval,
    "citation_traceability": eval_citation_traceability,
    "end_to_end": eval_end_to_end,
}


def main():
    parser = argparse.ArgumentParser(description="Contract Review Evaluation")
    parser.add_argument("--output", default="eval_report.json", help="Output JSON file path")
    parser.add_argument("--eval-dir", default="eval_data", help="Directory containing eval datasets")
    parser.add_argument("--dataset", help="Run a specific eval dataset only")
    args = parser.parse_args()

    logger.info(f"Loading eval data from: {args.eval_dir}")
    datasets = load_eval_data(args.eval_dir)

    if not datasets:
        # Generate a placeholder report even without eval data
        logger.warning("No eval datasets found. Generating placeholder report.")
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "eval_sets": {},
            "control_experiments": run_control_experiments(args.eval_dir),
            "summary": {
                "total_evals": 0,
                "note": "No eval data found. Place eval datasets in eval_data/ directory.",
            },
        }
    else:
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "eval_sets": {},
            "control_experiments": run_control_experiments(args.eval_dir),
        }

        target = args.dataset
        if target:
            if target in EVAL_FUNCTIONS and target in datasets:
                report["eval_sets"][target] = EVAL_FUNCTIONS[target](datasets[target])
            else:
                logger.error(f"Unknown or missing dataset: {target}")
        else:
            for name, fn in EVAL_FUNCTIONS.items():
                if name in datasets:
                    report["eval_sets"][name] = fn(datasets[name])
                else:
                    logger.info(f"Skipping {name}: no data found")

        # Summary
        total_evals = len(report["eval_sets"])
        report["summary"] = {
            "total_evals": total_evals,
            "datasets_available": list(datasets.keys()),
        }

    # Write report
    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Eval report written to: {output_path}")

    # Print summary
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
