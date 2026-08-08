#!/usr/bin/env python3
"""
Build kb_template — ingest safe contract template clauses into Milvus.
Atomic unit = single clause type (付款条款/违约条款/保密条款/交付条款 etc.).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CLAUSE_TYPE_PATTERNS: dict[str, list[str]] = {
    "付款条款": ["付款", "价款", "支付", "报酬", "费用", "价格"],
    "交付条款": ["交付", "交货", "验收", "验收标准", "交付期限"],
    "违约责任": ["违约", "违约金", "赔偿责任", "罚则", "违约条款"],
    "保密条款": ["保密", "机密", "非公开", "商业秘密", "保密义务"],
    "知识产权": ["知识产权", "专利", "商标", "著作权", "技术成果", "版权"],
    "争议解决": ["争议", "仲裁", "诉讼", "管辖", "法律适用"],
    "不可抗力": ["不可抗力", "自然灾害", "意外事件", "政府行为"],
    "合同变更": ["变更", "修改", "补充协议", "合同变更"],
    "合同解除": ["解除", "终止", "合同终止", "合同解除"],
    "通知送达": ["通知", "送达", "地址变更", "联系方式"],
    "保证与承诺": ["保证", "承诺", "陈述", "担保", "声明与保证"],
    "定义条款": ["定义", "下列用语", "本协议所称", "释义"],
}


def identify_clause_types(text: str) -> list[str]:
    """Identify which clause types a given text segment belongs to."""
    types: list[str] = []
    for ctype, keywords in CLAUSE_TYPE_PATTERNS.items():
        if any(kw in text for kw in keywords):
            types.append(ctype)
    return types if types else ["其他"]


def parse_template_clauses(text: str, template_name: str, category: str) -> list[dict]:
    """
    Split template text into clause-type-level chunks.
    Each chunk is one atomic clause (e.g. a single 付款条款 block).
    """
    # Split by numbered headings: 第X条 / X. / X、/ 一、etc.
    headings = list(re.finditer(
        r"(?:第[一二三四五六七八九十百千]+条|^\d+[\.\、\)）]|^[一二三四五六七八九十]+[、\.,，。])",
        text, re.MULTILINE,
    ))

    if not headings:
        # Try splitting by double newline
        parts = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 20]
        return _build_clause_records(parts, template_name, category)

    records: list[dict] = []
    for i, match in enumerate(headings):
        start = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        clause_text = text[start:end].strip()
        if len(clause_text) < 10:
            continue

        ctypes = identify_clause_types(clause_text)
        for ctype in ctypes:
            records.append(_make_record(clause_text, ctype, template_name, category))

    return records


def _build_clause_records(parts: list[str], template_name: str, category: str) -> list[dict]:
    records: list[dict] = []
    for part in parts:
        ctypes = identify_clause_types(part)
        for ctype in ctypes:
            records.append(_make_record(part, ctype, template_name, category))
    return records


def _make_record(clause_text: str, clause_type: str, template_name: str, category: str) -> dict:
    rec_id = hashlib.sha256(f"{template_name}:{clause_type}:{clause_text[:80]}".encode()).hexdigest()[:32]
    return {
        "id": rec_id,
        "content": f"[{clause_type}] {clause_text[:12000]}",
        "clause_type": clause_type,
        "template_name": template_name,
        "category": category,
        "is_safe_clause": "true",
        "embedding": [0.0] * 1024,  # placeholder
    }


def build_kb_template(data_dir: str) -> int:
    """
    Ingest template clause files from data_dir into kb_template collection.
    Expects .json files with keys: template_name, category, content.
    Returns total template clauses ingested.
    """
    from backend.core.rag import get_kb_client

    client = get_kb_client()
    data_path = Path(data_dir)
    if not data_path.exists():
        logger.error(f"Data directory not found: {data_dir}")
        return 0

    total = 0
    for filepath in data_path.glob("*"):
        if filepath.suffix == ".json":
            with open(filepath, encoding="utf-8") as f:
                tmpl = json.load(f)
            template_name = tmpl.get("template_name", filepath.stem)
            category = tmpl.get("category", "其他")
            text = tmpl.get("content", tmpl.get("text", ""))
        elif filepath.suffix == ".txt":
            with open(filepath, encoding="utf-8") as f:
                text = f.read()
            template_name = filepath.stem
            category = "其他"
        else:
            continue

        if not text:
            continue

        records = parse_template_clauses(text, template_name, category)

        batch_size = 100
        for j in range(0, len(records), batch_size):
            batch = records[j:j + batch_size]
            client.insert("kb_template", batch)

        total += len(records)
        logger.info(f"Ingested {template_name}: {len(records)} clauses")

    logger.info(f"Total template clauses ingested: {total}")
    return total


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "sample/kb_data"
    build_kb_template(data_dir)


if __name__ == "__main__":
    main()
