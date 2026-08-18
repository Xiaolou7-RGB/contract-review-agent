#!/usr/bin/env python3
"""
build_law_kb.py — 从 sample/kb_data/laws/*.html 解析多部法律/司法解释，
用本地 BGE-M3 编码 dense 向量，灌入 kb_law collection。

每部法律一个 HTML 文件，文件名即法律名（law_name）。条文按「第X条」切分，
article_no 存带「第」「条」的格式（与 civil_code_hybrid 一致）。

用法：
  python scripts/build_law_kb.py [data_dir]
  默认 data_dir = sample/kb_data/laws

依赖：
  - Milvus 已在 localhost:19530 运行
  - BGE-M3 模型在 D:/contract/models/embedding/bge-m3
  - kb_law collection 已由 scripts/init_milvus.py 创建
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymilvus import Collection, MilvusClient, connections

MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
BGE_M3_PATH = "D:/contract/models/embedding/bge-m3"
BATCH = 16
COLLECTION = "kb_law"

# 条文标记：第X条（中文数字）
_ARTICLE = re.compile(r"第([一二三四五六七八九十百千零]+)条")


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("\u3000", " ")
    s = re.sub(r"\s+", "", s)  # 法律正文里空格无意义，去掉
    return s.strip()


def parse_law(html_path: Path) -> list[dict]:
    """解析一部法律 HTML，返回条文列表。

    条文以「第X条」开头，独立成段（<p> 或 <br> 分隔）。用段落切分 + 开头
    匹配，避免条文正文内「民法典第X条」「本法第X条」引用被误当成新条文。
    """
    law_name = html_path.stem
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)

    articles = []
    # 每个 <p> / <br> 是一个独立块；条文号在块开头
    for block in re.split(r"</p>|<br\s*/?>", html):
        t = _clean(block)
        if not t:
            continue
        m = _ARTICLE.match(t)
        if not m:
            continue
        no = m.group(1)
        body = t[m.end():].strip()
        full_no = f"第{no}条"
        articles.append({
            "id": hashlib.md5(f"{law_name}_{full_no}".encode()).hexdigest()[:32],
            "article_no": full_no,                       # 带「第」「条」
            "law_name": law_name,
            "chapter": law_name,
            "content": f"{full_no} {body}",
            "cross_refs": ",".join(set(_ARTICLE.findall(body))),
            "effective_date": "",
            "breadcrumb": law_name,
        })
    return articles


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample/kb_data/laws")
    files = sorted(data_dir.glob("*.html"))
    if not files:
        print(f"未找到 HTML 文件：{data_dir}")
        sys.exit(1)

    print(f"待处理 {len(files)} 部法律：{[f.stem for f in files]}")

    # 解析全部
    all_articles: list[dict] = []
    for f in files:
        arts = parse_law(f)
        print(f"  {f.stem}: {len(arts)} 条")
        all_articles.extend(arts)
    print(f"共 {len(all_articles)} 条条文")

    if not all_articles:
        print("无条文可灌入，终止")
        sys.exit(1)

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT, alias="default")
    print(f"加载 BGE-M3 from {BGE_M3_PATH} ...")
    from FlagEmbedding import BGEM3FlagModel
    model = BGEM3FlagModel(BGE_M3_PATH, use_fp16=False)

    client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")
    total = 0
    for i in range(0, len(all_articles), BATCH):
        batch = all_articles[i:i + BATCH]
        texts = [r["content"] for r in batch]
        encoded = model.encode(texts, return_dense=True, return_sparse=False, return_colbert_vecs=False)
        rows = []
        for j, r in enumerate(batch):
            row = dict(r)
            row["embedding"] = encoded["dense_vecs"][j].tolist()
            rows.append(row)
        result = client.insert(collection_name=COLLECTION, data=rows)
        total += result.get("insert_count", len(rows))
        print(f"  {total}/{len(all_articles)} inserted")

    client.flush(collection_name=COLLECTION)
    col = Collection(COLLECTION)
    col.load()
    print(f"DONE: {COLLECTION} has {col.num_entities} entities")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
