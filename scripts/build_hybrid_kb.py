#!/usr/bin/env python3
"""
build_hybrid_kb.py — build civil_code_hybrid (dense + sparse) from civil_code_contract.

Reads all 988 articles from the existing collection, encodes each with local BGE-M3
(dense + sparse in one pass), and inserts into a new collection that has both a
FLOAT_VECTOR and a SPARSE_FLOAT_VECTOR field, enabling true Milvus hybrid search.

The old collection civil_code_contract is NOT touched.

Run: D:\contract\.venv\Scripts\python.exe scripts/build_hybrid_kb.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    MilvusClient,
    connections,
    utility,
)

SOURCE = "civil_code_contract"
TARGET = "civil_code_hybrid"
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
BGE_M3_PATH = "D:/contract/models/embedding/bge-m3"
BATCH = 16


def read_source() -> list[dict]:
    """Read all rows from the source collection (paginated)."""
    col = Collection(SOURCE)
    col.load()
    rows: list[dict] = []
    offset = 0
    while True:
        batch = col.query(
            expr="id >= 0",
            output_fields=["id", "article_no", "chapter", "text", "keywords"],
            limit=500,
            offset=offset,
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    return rows


def create_target() -> None:
    """Create the target collection with dense + sparse vector fields."""
    if utility.has_collection(TARGET):
        print(f"[skip] {TARGET} already exists")
        return

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64, auto_id=False),
        FieldSchema(name="article_no", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="chapter", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=12288),
        FieldSchema(name="keywords", dtype=DataType.VARCHAR, max_length=1024),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024),
        FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
    ]
    schema = CollectionSchema(fields, description="Civil Code articles 1-988 — BGE-M3 dense+sparse hybrid")
    col = Collection(TARGET, schema)

    col.create_index(
        "embedding",
        {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 128}},
    )
    col.create_index(
        "sparse_vector",
        {"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP", "params": {"drop_ratio_build": 0.0}},
    )
    print(f"[ok] created {TARGET} with dense + sparse indexes")


def main() -> None:
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT, alias="default")
    print(f"[1/4] reading source collection {SOURCE} ...")
    rows = read_source()
    print(f"      {len(rows)} articles loaded")
    if not rows:
        print("ERROR: source is empty, aborting")
        sys.exit(1)

    print("[2/4] creating target collection ...")
    create_target()

    print(f"[3/4] loading BGE-M3 from {BGE_M3_PATH} ...")
    from FlagEmbedding import BGEM3FlagModel

    model = BGEM3FlagModel(BGE_M3_PATH, use_fp16=False)

    print(f"[4/4] encoding + inserting in batches of {BATCH} ...")
    client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")
    total = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        texts = [r["text"] for r in batch]
        encoded = model.encode(texts, return_dense=True, return_sparse=True, return_colbert_vecs=False)

        insert_rows = []
        for j, r in enumerate(batch):
            dense = encoded["dense_vecs"][j].tolist()
            weights = encoded["lexical_weights"][j]
            sparse = {int(k): float(v) for k, v in weights.items()}
            insert_rows.append({
                "id": str(r["id"]),
                "article_no": r.get("article_no", ""),
                "chapter": r.get("chapter", ""),
                "text": r["text"],
                "keywords": (r.get("keywords") or "")[:1000],
                "embedding": dense,
                "sparse_vector": sparse,
            })
        result = client.insert(collection_name=TARGET, data=insert_rows)
        total += result.get("insert_count", len(insert_rows))
        print(f"      {total}/{len(rows)} inserted")

    client.flush(collection_name=TARGET)
    col = Collection(TARGET)
    col.load()
    print(f"DONE: {TARGET} has {col.num_entities} entities")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
