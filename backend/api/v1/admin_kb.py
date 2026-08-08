"""
Admin Knowledge Base API — CRUD for kb_law, kb_case, kb_template.
Requires admin role for all endpoints.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.dependencies import require_admin
from backend.core.rag import get_kb_client, COLLECTION_REGISTRY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/kb", tags=["admin-kb"])

# Supported collections
VALID_COLLECTIONS = {"law": "kb_law", "case": "kb_case", "template": "kb_template"}


# ── Pydantic schemas ────────────────────────────────────────

class KbItemCreate(BaseModel):
    id: str
    content: str
    metadata: dict[str, Any] = {}


class KbItemUpdate(BaseModel):
    content: str | None = None
    metadata: dict[str, Any] | None = None


# ── Routes ──────────────────────────────────────────────────

@router.get("/{collection}")
async def list_kb_items(
    collection: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict = Depends(require_admin),
):
    """List items in a knowledge base collection."""
    if collection not in VALID_COLLECTIONS:
        raise HTTPException(400, f"Invalid collection: {collection}. Must be one of {list(VALID_COLLECTIONS.keys())}")

    milvus_col = VALID_COLLECTIONS[collection]
    client = get_kb_client()

    try:
        client.connect()
        from pymilvus import Collection
        col = Collection(milvus_col)

        # Get total count
        col.load()
        total = col.num_entities

        # Query with pagination (simplified — in production use iterator)
        results = col.query(
            expr="id != ''",
            output_fields=COLLECTION_REGISTRY[milvus_col]["search_fields"] + ["id"],
            limit=page_size,
            offset=(page - 1) * page_size,
        )

        return {
            "collection": collection,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": results,
        }
    except Exception as e:
        logger.exception(f"Failed to list items from {milvus_col}")
        raise HTTPException(500, f"Failed to list items: {e}")


@router.post("/{collection}")
async def create_kb_item(
    collection: str,
    item: KbItemCreate,
    admin: dict = Depends(require_admin),
):
    """Add a new item to a knowledge base collection (with embedding)."""
    if collection not in VALID_COLLECTIONS:
        raise HTTPException(400, f"Invalid collection: {collection}")

    milvus_col = VALID_COLLECTIONS[collection]
    client = get_kb_client()

    # Build record with metadata fields
    record = {
        "id": item.id,
        "content": item.content[:12288],  # respect VARCHAR limit
        "embedding": [0.0] * 1024,  # placeholder — in production, compute real embedding
    }
    # Merge collection-specific metadata
    for k, v in item.metadata.items():
        record[k] = str(v)[:512]

    try:
        client.insert(milvus_col, [record])
        logger.info(f"Admin {admin['username']} created item {item.id} in {milvus_col}")
        return {"status": "created", "id": item.id, "collection": milvus_col}
    except Exception as e:
        logger.exception(f"Failed to create item in {milvus_col}")
        raise HTTPException(500, f"Failed to create: {e}")


@router.put("/{collection}/{item_id}")
async def update_kb_item(
    collection: str,
    item_id: str,
    item: KbItemUpdate,
    admin: dict = Depends(require_admin),
):
    """Update an existing knowledge base item."""
    if collection not in VALID_COLLECTIONS:
        raise HTTPException(400, f"Invalid collection: {collection}")

    milvus_col = VALID_COLLECTIONS[collection]
    client = get_kb_client()

    try:
        # Delete old, insert new (Milvus upsert via delete + insert)
        client.delete_by_ids(milvus_col, [item_id])

        # Build updated record
        updated_content = item.content[:12288] if item.content else ""
        record = {
            "id": item_id,
            "content": updated_content,
            "embedding": [0.0] * 1024,
        }
        if item.metadata:
            for k, v in item.metadata.items():
                record[k] = str(v)[:512]

        client.insert(milvus_col, [record])
        logger.info(f"Admin {admin['username']} updated item {item_id} in {milvus_col}")
        return {"status": "updated", "id": item_id, "collection": milvus_col}
    except Exception as e:
        logger.exception(f"Failed to update item {item_id} in {milvus_col}")
        raise HTTPException(500, f"Failed to update: {e}")


@router.delete("/{collection}/{item_id}")
async def delete_kb_item(
    collection: str,
    item_id: str,
    admin: dict = Depends(require_admin),
):
    """Delete an item from a knowledge base collection."""
    if collection not in VALID_COLLECTIONS:
        raise HTTPException(400, f"Invalid collection: {collection}")

    milvus_col = VALID_COLLECTIONS[collection]
    client = get_kb_client()

    try:
        client.delete_by_ids(milvus_col, [item_id])
        logger.info(f"Admin {admin['username']} deleted item {item_id} from {milvus_col}")
        return {"status": "deleted", "id": item_id, "collection": milvus_col}
    except Exception as e:
        logger.exception(f"Failed to delete item {item_id} from {milvus_col}")
        raise HTTPException(500, f"Failed to delete: {e}")


@router.post("/reindex")
async def reindex_kb(
    admin: dict = Depends(require_admin),
):
    """
    Full reindex: drop and recreate all collections, then re-ingest all data.
    Streams progress via SSE.
    """
    async def _reindex_stream():
        yield _sse("progress", {"message": "Starting full reindex..."})

        client = get_kb_client()

        # Drop existing collections
        for col_name in VALID_COLLECTIONS.values():
            yield _sse("progress", {"message": f"Dropping {col_name}..."})
            try:
                client.drop_collection(col_name)
            except Exception:
                pass  # collection may not exist

        yield _sse("progress", {"message": "Recreating collections..."})
        client.init_collections()

        # Re-run build scripts
        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scripts")

        yield _sse("progress", {"message": "Rebuilding kb_law..."})
        try:
            from scripts.build_kb_law import build_kb_law
            count = build_kb_law("sample/kb_data")
            yield _sse("progress", {"message": f"kb_law: {count} articles ingested"})
        except Exception as e:
            yield _sse("error", {"message": f"kb_law failed: {e}"})

        yield _sse("progress", {"message": "Rebuilding kb_case..."})
        try:
            from scripts.build_kb_case import build_kb_case
            count = build_kb_case("sample/kb_data")
            yield _sse("progress", {"message": f"kb_case: {count} cases ingested"})
        except Exception as e:
            yield _sse("error", {"message": f"kb_case failed: {e}"})

        yield _sse("progress", {"message": "Rebuilding kb_template..."})
        try:
            from scripts.build_kb_template import build_kb_template
            count = build_kb_template("sample/kb_data")
            yield _sse("progress", {"message": f"kb_template: {count} clauses ingested"})
        except Exception as e:
            yield _sse("error", {"message": f"kb_template failed: {e}"})

        yield _sse("complete", {"message": "Reindex complete"})

    return StreamingResponse(
        _reindex_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: Any) -> str:
    """Format an SSE event string."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
