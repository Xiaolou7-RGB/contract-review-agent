"""
RAG abstraction layer — hybrid search (BGE-M3 dense + sparse) with BGE-Reranker-v2-M3.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from pymilvus import (
    AnnSearchRequest,
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    MilvusClient,
    WeightedRanker,
    connections,
    utility,
)

logger = logging.getLogger(__name__)

from backend.config import get_settings

# ── Lazy model singletons (thread-safe) ─────────────────────

_embedding_model: Any = None
_reranker_model: Any = None
_model_lock = threading.Lock()


def _get_embedding_model() -> Any:
    """Lazy-load BGE-M3 FlagEmbedding model (dense + sparse)."""
    global _embedding_model
    if _embedding_model is None:
        with _model_lock:
            if _embedding_model is None:
                path = get_settings().bge_m3_model_path
                logger.info(f"Loading BGE-M3 from {path} ...")
                from FlagEmbedding import BGEM3FlagModel
                _embedding_model = BGEM3FlagModel(path, use_fp16=False)
                logger.info("BGE-M3 loaded")
    return _embedding_model


def _get_reranker_model() -> Any:
    """Lazy-load BGE-Reranker-v2-M3."""
    global _reranker_model
    if _reranker_model is None:
        with _model_lock:
            if _reranker_model is None:
                path = get_settings().bge_reranker_model_path
                logger.info(f"Loading BGE-Reranker-v2-M3 from {path} ...")
                from FlagEmbedding import FlagReranker
                _reranker_model = FlagReranker(path, use_fp16=False)
                logger.info("BGE-Reranker-v2-M3 loaded")
    return _reranker_model


async def warmup_models_async() -> None:
    """并行预热本地模型（embedding + reranker），供 lifespan 启动时调用。

    单个模型加载失败不阻断启动——降级为首个请求时再 lazy-load。
    """

    async def _safe(name: str, loader: Callable[[], Any]) -> None:
        try:
            await asyncio.to_thread(loader)
            logger.info(f"{name} warmed up")
        except Exception:
            logger.exception(f"{name} warmup failed, will lazy-load on first request")

    await asyncio.gather(
        _safe("BGE-M3", _get_embedding_model),
        _safe("BGE-Reranker", _get_reranker_model),
    )


# ── Collection schema definitions ───────────────────────────

EMBEDDING_DIM = 1024  # BGE-M3 output dim

# Reranker candidate pool size (T6 fix, 2026-08-07).
# Previously the pool was max(rerank_top_k*2, 6): a relevant statute ranked 7th
# or lower after Milvus fusion was silently dropped before reranking. The QA
# query "第四条违约责任有什么高风险" lost 第五百八十八条 that way (ranked #10).
# The pool must be fed by Milvus too, so ANN requests fetch max(top_k, RERANK_POOL).
RERANK_POOL = 15

_COMMON_FIELDS = [
    FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=128, auto_id=False),
    FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=12288),
    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
]


def _create_collection(client: MilvusClient, name: str, schema: CollectionSchema) -> None:
    """Create a collection if it does not exist."""
    if utility.has_collection(name):
        logger.info(f"Collection '{name}' already exists, skipping creation")
        return
    client.create_collection(collection_name=name, schema=schema)
    logger.info(f"Collection '{name}' created")


def _schema_law() -> CollectionSchema:
    fields = _COMMON_FIELDS + [
        FieldSchema(name="article_no", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="law_name", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="chapter", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="cross_refs", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="effective_date", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="breadcrumb", dtype=DataType.VARCHAR, max_length=512),
    ]
    return CollectionSchema(fields, description="Law articles — atomic article-level chunks")


def _schema_case() -> CollectionSchema:
    fields = _COMMON_FIELDS + [
        FieldSchema(name="case_id", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="case_name", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="court", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="judgment_date", dtype=DataType.VARCHAR, max_length=64),
    ]
    return CollectionSchema(fields, description="Case precedents — logical paragraph chunks")


def _schema_template() -> CollectionSchema:
    fields = _COMMON_FIELDS + [
        FieldSchema(name="clause_type", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="template_name", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="is_safe_clause", dtype=DataType.VARCHAR, max_length=8),
    ]
    return CollectionSchema(fields, description="Contract template clauses — atomic clause-type chunks")


COLLECTION_REGISTRY: dict[str, dict[str, Any]] = {
    "kb_law": {
        "schema_fn": _schema_law,
        "description": "Law articles — atomic article-level chunks",
        "search_fields": ["article_no", "law_name", "chapter", "content"],
    },
    "kb_case": {
        "schema_fn": _schema_case,
        "description": "Case precedents — logical paragraph chunks",
        "search_fields": ["case_name", "section", "content"],
    },
    "kb_template": {
        "schema_fn": _schema_template,
        "description": "Contract template clauses — atomic clause-type chunks",
        "search_fields": ["clause_type", "template_name", "content"],
    },
    "civil_code_contract": {
        # Pre-existing dense-only collection — kept for reference, no schema_fn (do not drop/recreate)
        # Actual fields: id(Int64), article_no, chapter, text, keywords, embedding
        "description": "Civil Code articles 1-988 — dense only (legacy)",
        "search_fields": ["article_no", "chapter", "text"],
    },
    "civil_code_hybrid": {
        # Dense + sparse hybrid collection built by scripts/build_hybrid_kb.py
        # Fields: id(VARCHAR), article_no, chapter, text, keywords, embedding, sparse_vector
        "description": "Civil Code articles 1-988 — BGE-M3 dense+sparse hybrid",
        "search_fields": ["article_no", "chapter", "text"],
        "hybrid": True,
    },
}


# ── Client ──────────────────────────────────────────────────

class KnowledgeBaseClient:
    """Knowledge base client with BGE-M3 hybrid search + BGE-Reranker."""

    def __init__(self, host: str | None = None, port: int | None = None):
        settings = get_settings()
        self._host = host or settings.milvus_host
        self._port = port or settings.milvus_port
        self._connected = False

    def connect(self) -> None:
        if self._connected:
            return
        connections.connect(host=self._host, port=self._port, alias="default")
        self._connected = True
        logger.info(f"Connected to Milvus at {self._host}:{self._port}")

    def init_collections(self) -> list[str]:
        """Create collections that have schema_fn defined. Skip pre-existing ones."""
        self.connect()
        client = MilvusClient(uri=f"http://{self._host}:{self._port}")
        created: list[str] = []
        for name, cfg in COLLECTION_REGISTRY.items():
            if "schema_fn" not in cfg:
                logger.info(f"Collection '{name}' has no schema_fn, skipping creation")
                continue
            schema = cfg["schema_fn"]()
            _create_collection(client, name, schema)
            # 为 embedding 字段建索引（否则后续 load/search 报 index not found）
            self._ensure_embedding_index(name)
            created.append(name)
        return created

    def _ensure_embedding_index(self, name: str) -> None:
        """Ensure an IVF_FLAT index exists on the dense embedding field."""
        from pymilvus import Collection

        try:
            col = Collection(name)
            if col.has_index():
                return
            col.create_index(
                "embedding",
                {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 32}},
            )
            logger.info(f"Created embedding index for '{name}'")
        except Exception as e:  # non-fatal: collection may be empty / already indexed
            logger.warning(f"Failed to ensure index for '{name}': {e}")

    def list_collections(self) -> list[str]:
        self.connect()
        return utility.list_collections()

    def insert(self, collection: str, data: list[dict[str, Any]]) -> int:
        """Insert data into a collection. Returns insert count."""
        self.connect()
        client = MilvusClient(uri=f"http://{self._host}:{self._port}")
        result = client.insert(collection_name=collection, data=data)
        count = result.get("insert_count", 0)
        logger.info(f"Inserted {count} records into {collection}")
        return count

    def delete_by_ids(self, collection: str, ids: list[str]) -> int:
        """Delete records by IDs."""
        self.connect()
        client = MilvusClient(uri=f"http://{self._host}:{self._port}")
        result = client.delete(collection_name=collection, filter=f"id in {ids}")
        return result.get("delete_count", 0)

    def drop_collection(self, collection: str) -> None:
        self.connect()
        if utility.has_collection(collection):
            utility.drop_collection(collection)
            logger.info(f"Dropped collection '{collection}'")

    async def hybrid_search(
        self,
        query: str,
        collection: str,
        top_k: int = 10,
        rerank_top_k: int = 3,
        threshold: float = 0.30,
    ) -> list[dict[str, Any]]:
        """
        Hybrid search: Milvus native dual-path retrieval (dense + sparse) fused by
        WeightedRanker(0.7, 0.3) for collections flagged hybrid=True, followed by
        BGE-Reranker-v2-M3 cross-encoder reranking.
        Legacy collections without sparse vectors fall back to dense-only ANN.

        threshold is calibrated against normalized reranker scores (empirically 0.1~0.4).

        Milvus fetches max(top_k, RERANK_POOL) candidates so the reranker pool is
        filled even when callers ask for a small top_k.
        """
        self.connect()
        client = MilvusClient(uri=f"http://{self._host}:{self._port}")
        cfg = COLLECTION_REGISTRY.get(collection, {})

        # ── Step 1: encode query with BGE-M3 (dense + sparse) ──
        sparse_dict: dict[int, float] = {}
        try:
            model = _get_embedding_model()
            encoded = model.encode(
                [query],
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
            dense_vec = encoded["dense_vecs"][0].tolist()
            sparse_dict = {int(k): float(v) for k, v in encoded["lexical_weights"][0].items()}
        except Exception:
            logger.exception("BGE-M3 encoding failed, falling back to zero vector")
            dense_vec = [0.0] * EMBEDDING_DIM

        # ── Step 2: Milvus retrieval (native hybrid or dense-only) ──
        search_fields = cfg.get("search_fields", ["content"]) + ["id"]
        use_hybrid = bool(cfg.get("hybrid")) and bool(sparse_dict)
        # Fetch enough candidates to fill the rerank pool (T6 fix)
        fetch_k = max(top_k, RERANK_POOL)
        try:
            if use_hybrid:
                dense_req = AnnSearchRequest(
                    data=[dense_vec],
                    anns_field="embedding",
                    param={"metric_type": "IP", "params": {"nprobe": 64}},
                    limit=fetch_k,
                )
                sparse_req = AnnSearchRequest(
                    data=[sparse_dict],
                    anns_field="sparse_vector",
                    param={"metric_type": "IP", "params": {"drop_ratio_search": 0.0}},
                    limit=fetch_k,
                )
                results = client.hybrid_search(
                    collection_name=collection,
                    reqs=[dense_req, sparse_req],
                    ranker=WeightedRanker(0.7, 0.3),
                    output_fields=search_fields,
                    limit=fetch_k,
                )
            else:
                results = client.search(
                    collection_name=collection,
                    data=[dense_vec],
                    anns_field="embedding",
                    search_params={"metric_type": "IP", "params": {"nprobe": 64}},
                    limit=fetch_k,
                    output_fields=search_fields,
                )
        except Exception:
            logger.exception(f"Search failed for collection {collection}")
            return []

        if not results or not results[0]:
            return []

        # ── Step 3: normalize fields + dedupe ──
        hits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in results[0]:
            entity = hit.get("entity", {})
            source_id = str(entity.get("id", ""))
            if source_id in seen:
                continue
            seen.add(source_id)
            # 透传所有自定义字段（clause_type/template_name/law_name/case_name…），
            # 否则 kb_template/kb_law/kb_case 的元数据会在返回时丢失。
            item = dict(entity)
            item["id"] = source_id
            item["content"] = entity.get("text") or entity.get("content", "")
            item["hybrid_score"] = round(float(hit.get("distance", 0.0)), 4)
            hits.append(item)

        # Candidate pool for the reranker (widened by T6 fix: was max(rerank_top_k*2, 6))
        candidates = hits[:RERANK_POOL]

        # ── Step 4: BGE-Reranker-v2-M3 ──
        try:
            reranker = _get_reranker_model()
            pairs = [[query, h["content"]] for h in candidates]
            rerank_scores = reranker.compute_score(pairs, normalize=True)
            if isinstance(rerank_scores, float):
                rerank_scores = [rerank_scores]
            for i, h in enumerate(candidates):
                h["rerank_score"] = float(rerank_scores[i]) if i < len(rerank_scores) else 0.0
            candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
        except Exception:
            logger.exception("Reranker failed, using hybrid scores")
            for h in candidates:
                h["rerank_score"] = h["hybrid_score"]

        # ── Step 5: final top_k + threshold ──
        out: list[dict[str, Any]] = []
        for h in candidates[:rerank_top_k]:
            item = dict(h)  # 保留透传的自定义字段（clause_type/template_name/law_name…）
            item["confidence"] = h["rerank_score"]
            item["rerank_score"] = h["rerank_score"]
            if item["confidence"] < threshold:
                item["is_human_review"] = True
            out.append(item)

        return out

    def query_articles(self, collection: str, article_nos: list[str]) -> list[dict[str, Any]]:
        """
        Exact-match lookup of law articles by article_no (no vector search).
        Used by context expansion (adjacent articles / cross-references).
        Returns items with the same normalized keys as hybrid_search hits.
        """
        if not article_nos:
            return []
        self.connect()
        client = MilvusClient(uri=f"http://{self._host}:{self._port}")
        cfg = COLLECTION_REGISTRY.get(collection, {})
        output_fields = cfg.get("search_fields", ["content"]) + ["id"]
        try:
            quoted = ", ".join(f'"{a}"' for a in article_nos)
            rows = client.query(
                collection_name=collection,
                filter=f"article_no in [{quoted}]",
                output_fields=output_fields,
                limit=len(article_nos) * 2,
            )
        except Exception:
            logger.exception(f"query_articles failed for {collection}")
            return []

        out: list[dict[str, Any]] = []
        for entity in rows:
            out.append({
                "id": str(entity.get("id", "")),
                "content": entity.get("text") or entity.get("content", ""),
                "article_no": entity.get("article_no", ""),
                "chapter": entity.get("chapter", ""),
                "confidence": 0.0,
                "hybrid_score": 0.0,
                "rerank_score": 0.0,
            })
        return out


# Singleton
_kb_client: KnowledgeBaseClient | None = None


def get_kb_client() -> KnowledgeBaseClient:
    global _kb_client
    if _kb_client is None:
        _kb_client = KnowledgeBaseClient()
    return _kb_client
