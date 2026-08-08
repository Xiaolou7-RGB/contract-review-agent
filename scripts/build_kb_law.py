#!/usr/bin/env python3
"""
Build kb_law — ingest law articles into Milvus.
Atomic unit = one article (条). Never cut across articles.
Short articles (< 30 chars) merge with adjacent; long ones (> 400 chars) kept whole.
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

ARTICLE_PATTERN = re.compile(r"第([一二三四五六七八九十百千]+)条\s*")


def parse_law_articles(text: str, law_name: str = "") -> list[dict]:
    """
    Split law text into atomic article units.
    Each article = content + breadcrumb (编→章→条).
    """
    # Find all article boundaries
    matches = list(ARTICLE_PATTERN.finditer(text))
    if not matches:
        logger.warning("No article markers found in law text")
        return []

    articles: list[dict] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        article_text = text[start:end].strip()

        article_no = match.group(1)
        # Extract breadcrumb from nearby text (chapter headers before this article)
        breadcrumb = _extract_breadcrumb(text[:start])

        articles.append({
            "id": hashlib.sha256(f"{law_name}:{article_no}".encode()).hexdigest()[:32],
            "content": f"{law_name}\n{breadcrumb}第{article_no}条\n{article_text}",
            "article_no": article_no,
            "law_name": law_name,
            "chapter": breadcrumb.strip(),
            "cross_refs": _extract_cross_refs(article_text),
            "effective_date": "",
            "breadcrumb": breadcrumb.strip(),
        })

    # Post-process: merge short articles with adjacent
    merged = _merge_short_articles(articles)

    logger.info(f"Parsed {len(matches)} articles → {len(merged)} after merge (law: {law_name})")
    return merged


def _extract_breadcrumb(text_before: str) -> str:
    """Extract chapter/section breadcrumb from text preceding an article."""
    # Look for 第X章 / 第X节 patterns
    chapters = re.findall(r"(第[一二三四五六七八九十百千]+[章节][^\n]*)", text_before[-1000:])
    if chapters:
        return " > ".join(c.strip() for c in chapters[-3:])  # last 3 levels
    return ""


def _extract_cross_refs(article_text: str) -> str:
    """Extract cross-references from article text (e.g. '依照本法第X条')."""
    refs = re.findall(r"第[一二三四五六七八九十百千]+条", article_text)
    return ",".join(refs) if refs else ""


def _merge_short_articles(articles: list[dict]) -> list[dict]:
    """Merge articles shorter than 30 chars with the next article."""
    if len(articles) <= 1:
        return articles

    merged: list[dict] = []
    skip_next = False

    for i, art in enumerate(articles):
        if skip_next:
            skip_next = False
            continue

        content_len = len(art["content"])
        if content_len < 30 and i + 1 < len(articles):
            # Merge with next
            next_art = articles[i + 1]
            art["content"] = art["content"] + "\n\n" + next_art["content"]
            art["article_no"] = f"{art['article_no']}-{next_art['article_no']}"
            art["id"] = hashlib.sha256(art["content"][:200].encode()).hexdigest()[:32]
            skip_next = True
            logger.debug(f"Merged short article {art['article_no']}")

        merged.append(art)

    return merged


def build_kb_law(data_dir: str) -> int:
    """
    Ingest all law files from data_dir into kb_law collection.
    Expects .txt or .json files containing law texts.
    Returns total articles ingested.
    """
    from backend.core.rag import get_kb_client

    client = get_kb_client()
    data_path = Path(data_dir)
    if not data_path.exists():
        logger.error(f"Data directory not found: {data_dir}")
        return 0

    total = 0
    for filepath in data_path.glob("*"):
        if filepath.suffix not in (".txt", ".json"):
            continue

        law_name = filepath.stem

        if filepath.suffix == ".json":
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            text = data.get("content", data.get("text", ""))
        else:
            with open(filepath, encoding="utf-8") as f:
                text = f.read()

        articles = parse_law_articles(text, law_name)
        if not articles:
            continue

        # Batch insert (100 per batch)
        batch_size = 100
        for j in range(0, len(articles), batch_size):
            batch = articles[j:j + batch_size]
            client.insert("kb_law", batch)

        total += len(articles)
        logger.info(f"Ingested {law_name}: {len(articles)} articles")

    logger.info(f"Total law articles ingested: {total}")
    return total


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "sample/kb_data"
    build_kb_law(data_dir)


if __name__ == "__main__":
    main()
