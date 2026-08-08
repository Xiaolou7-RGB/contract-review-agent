"""Verify RAG retrieval against civil_code_contract collection."""
import os
import sys
sys.path.insert(0, "D:/contract")
from dotenv import load_dotenv
load_dotenv("D:/contract/.env.local")

from backend.core.rag import get_kb_client, COLLECTION_REGISTRY

def main():
    client = get_kb_client()

    # Check registry has civil_code_contract
    assert "civil_code_contract" in COLLECTION_REGISTRY, "MISSING civil_code_contract"
    print(f"[1] civil_code_contract registered: YES (fields: {COLLECTION_REGISTRY['civil_code_contract']['search_fields']})")

    # Check collection exists in Milvus
    cols = client.list_collections()
    assert "civil_code_contract" in cols, f"civil_code_contract not found in Milvus. Existing: {cols}"
    print(f"[2] civil_code_contract exists in Milvus: YES")

    # Test search
    import asyncio
    results = asyncio.get_event_loop().run_until_complete(
        client.hybrid_search(query="违约金过高", collection="civil_code_contract", top_k=5, rerank_top_k=3)
    )

    assert len(results) > 0, "Search returned zero results"
    print(f"[3] Search '违约金过高' returned {len(results)} results")

    for r in results:
        assert "content" in r, f"Missing 'content' key in result: {list(r.keys())}"
        assert "id" in r, f"Missing 'id' key in result"
        assert isinstance(r["id"], str), f"id is not a string: {type(r['id'])}"
        assert "confidence" in r, f"Missing 'confidence' key"
        assert len(r["content"]) > 0, f"Empty content for id={r['id']}"
        print(f"  id={r['id'][:20]} conf={r['confidence']:.3f} content={r['content'][:60]}...")

    print("ALL_CHECKS_PASSED")


if __name__ == "__main__":
    main()
