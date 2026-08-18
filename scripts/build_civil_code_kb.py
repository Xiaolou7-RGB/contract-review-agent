#!/usr/bin/env python3
"""
build_civil_code_kb.py — 从《民法典》全文 HTML 解析 1260 条，用本地 BGE-M3 编码
（dense + sparse），直接灌入 civil_code_hybrid collection。

数据源：新华网/政府网站受权全文 HTML（每条一个 <p> 标签）。
RAG 检索（rag_retriever.py）硬编码使用 civil_code_hybrid，故直接灌目标 collection。

用法：
  python scripts/build_civil_code_kb.py <民法典HTML路径> [collection名]
  # 默认 collection = civil_code_hybrid

依赖：
  - Milvus 已在 localhost:19530 运行（deploy/docker-compose.yml）
  - BGE-M3 模型在 D:/contract/models/embedding/bge-m3
"""
from __future__ import annotations

import hashlib
import re
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

MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
BGE_M3_PATH = "D:/contract/models/embedding/bge-m3"
BATCH = 16
DEFAULT_COLLECTION = "civil_code_hybrid"

# 标题/条文标记：第X编 / 第X分编 / 第X章 / 第X节 / 第X条
_MARK = re.compile(r"第([一二三四五六七八九十百千零]+)(分编|编|章|节|条)")


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_civil_code(html_path: str) -> list[dict]:
    """解析 HTML 全文，返回 1260 条 {article_no, chapter, text}。

    article_no 存带「第」「条」的格式（如「第五百八十八条」），与
    rag_retriever.py 的 _format_article_no 输出一致，保证 query_articles
    的 filter 精确匹配。
    """
    html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)

    articles: list[dict] = []
    breadcrumb: list[str] = []  # [编, 分编, 章, 节]

    for block in re.split(r"</p>", html):
        t = _clean(block)
        if not t:
            continue
        m = _MARK.match(t)
        if not m:
            continue
        no, typ = m.group(1), m.group(2)
        rest = t[m.end():].strip()

        if typ == "分编":
            breadcrumb = breadcrumb[:1] + [f"第{no}分编 {rest}"]
        elif typ == "编":
            breadcrumb = [f"第{no}编 {rest}"]
        elif typ == "章":
            breadcrumb = breadcrumb[:2] + [f"第{no}章 {rest}"]
        elif typ == "节":
            breadcrumb = breadcrumb[:3] + [f"第{no}节 {rest}"]
        elif typ == "条":
            full_no = f"第{no}条"
            articles.append({
                "id": hashlib.md5(f"民法典_{full_no}".encode()).hexdigest()[:32],
                "article_no": full_no,                      # 带「第」「条」
                "chapter": " > ".join(breadcrumb),
                "text": f"{full_no} {rest}",
                "keywords": " > ".join(breadcrumb)[:1000],
            })

    return articles


def create_collection(collection: str) -> None:
    if utility.has_collection(collection):
        print(f"[skip] collection {collection} already exists")
        return
    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64, auto_id=False),
        FieldSchema(name="article_no", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="chapter", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=12288),
        FieldSchema(name="keywords", dtype=DataType.VARCHAR, max_length=1024),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024),
        FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
    ]
    schema = CollectionSchema(fields, description="Civil Code 1-1260 — BGE-M3 dense+sparse hybrid")
    col = Collection(collection, schema)
    col.create_index("embedding", {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 128}})
    col.create_index("sparse_vector", {"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP", "params": {"drop_ratio_build": 0.0}})
    print(f"[ok] created {collection} with dense + sparse indexes")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    html_path = sys.argv[1]
    collection = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_COLLECTION

    print(f"[1/4] parsing {html_path} ...")
    articles = parse_civil_code(html_path)
    print(f"      {len(articles)} articles parsed")
    if not articles:
        print("ERROR: no articles parsed, aborting")
        sys.exit(1)

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT, alias="default")
    print(f"[2/4] creating collection {collection} ...")
    create_collection(collection)

    print(f"[3/4] loading BGE-M3 from {BGE_M3_PATH} ...")
    from FlagEmbedding import BGEM3FlagModel
    model = BGEM3FlagModel(BGE_M3_PATH, use_fp16=False)

    print(f"[4/4] encoding + inserting in batches of {BATCH} ...")
    client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")
    total = 0
    for i in range(0, len(articles), BATCH):
        batch = articles[i:i + BATCH]
        texts = [r["text"] for r in batch]
        encoded = model.encode(texts, return_dense=True, return_sparse=True, return_colbert_vecs=False)
        insert_rows = []
        for j, r in enumerate(batch):
            dense = encoded["dense_vecs"][j].tolist()
            weights = encoded["lexical_weights"][j]
            sparse = {int(k): float(v) for k, v in weights.items()}
            insert_rows.append({
                "id": r["id"],
                "article_no": r["article_no"],
                "chapter": r["chapter"],
                "text": r["text"],
                "keywords": r["keywords"],
                "embedding": dense,
                "sparse_vector": sparse,
            })
        result = client.insert(collection_name=collection, data=insert_rows)
        total += result.get("insert_count", len(insert_rows))
        print(f"      {total}/{len(articles)} inserted")

    client.flush(collection_name=collection)
    col = Collection(collection)
    col.load()
    print(f"DONE: {collection} has {col.num_entities} entities")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
