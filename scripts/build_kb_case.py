#!/usr/bin/env python3
"""
Build kb_case — ingest court case precedents into Milvus.
Atomic unit = logical paragraph (基本案情/争议焦点/法院认为/判决结果).
The '法院认为' (reasoning) section is further split by paragraph.
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

SECTION_KEYWORDS = {
    "基本案情": ["基本案情", "案情", "事实", "经审理查明"],
    "争议焦点": ["争议焦点", "本案争议", "争议问题"],
    "法院认为": ["法院认为", "本院认为", "本院查明", "说理"],
    "判决结果": ["判决", "判决如下", "裁定", "裁判结果"],
}


def split_by_sections(text: str) -> list[dict]:
    """Split case text into logical sections."""
    # Find section boundaries
    boundaries: list[tuple[int, str]] = []
    for keyword, patterns in SECTION_KEYWORDS.items():
        for pat in patterns:
            for match in re.finditer(re.escape(pat), text):
                boundaries.append((match.start(), keyword))

    boundaries.sort(key=lambda x: x[0])

    if not boundaries:
        # No sections found — treat whole text as one chunk
        return [{"section": "全文", "content": text.strip()}]

    chunks: list[dict] = []
    for i, (pos, section) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
        chunk_text = text[pos:end].strip()

        if section == "法院认为":
            # Split reasoning section by paragraphs for finer granularity
            paragraphs = [p.strip() for p in chunk_text.split("\n\n") if len(p.strip()) > 20]
            for j, para in enumerate(paragraphs):
                chunks.append({
                    "section": f"法院认为-{j + 1}",
                    "content": para,
                })
        else:
            if len(chunk_text) > 20:
                chunks.append({"section": section, "content": chunk_text})

    return chunks


def build_kb_case(data_dir: str) -> int:
    """
    Ingest case precedents from data_dir into kb_case collection.
    Expects .json files with keys: case_id, case_name, court, judgment_date, content.
    Returns total case chunks ingested.
    """
    from backend.core.rag import get_kb_client

    client = get_kb_client()
    data_path = Path(data_dir)
    if not data_path.exists():
        logger.error(f"Data directory not found: {data_dir}")
        return 0

    total = 0
    for filepath in data_path.glob("*.json"):
        with open(filepath, encoding="utf-8") as f:
            case_data = json.load(f)

        case_id = case_data.get("case_id", filepath.stem)
        case_name = case_data.get("case_name", "")
        court = case_data.get("court", "")
        judgment_date = case_data.get("judgment_date", "")
        text = case_data.get("content", case_data.get("text", ""))

        if not text:
            logger.warning(f"Empty content in {filepath}")
            continue

        chunks = split_by_sections(text)
        records: list[dict] = []
        for chunk in chunks:
            chunk_id = hashlib.sha256(f"{case_id}:{chunk['section']}".encode()).hexdigest()[:32]
            records.append({
                "id": chunk_id,
                "content": f"{case_name}\n{chunk['section']}\n{chunk['content']}",
                "case_id": case_id,
                "case_name": case_name,
                "section": chunk["section"],
                "court": court,
                "judgment_date": judgment_date,
                "embedding": [0.0] * 1024,  # placeholder
            })

        # Batch insert
        batch_size = 100
        for j in range(0, len(records), batch_size):
            batch = records[j:j + batch_size]
            client.insert("kb_case", batch)

        total += len(records)
        logger.info(f"Ingested {case_name}: {len(records)} chunks")

    logger.info(f"Total case chunks ingested: {total}")
    return total


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "sample/kb_data"
    build_kb_case(data_dir)


if __name__ == "__main__":
    main()
