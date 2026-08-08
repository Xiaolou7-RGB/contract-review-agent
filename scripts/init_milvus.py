#!/usr/bin/env python3
"""
Initialize Milvus collections for contract review RAG.
Creates three collections: kb_law, kb_case, kb_template.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    from backend.core.rag import KnowledgeBaseClient, COLLECTION_REGISTRY

    client = KnowledgeBaseClient()

    logger.info("Connecting to Milvus...")
    client.connect()

    logger.info("Initializing collections...")
    created = client.init_collections()

    for name in created:
        logger.info(f"  Collection '{name}' ready")

    # Verify
    all_collections = client.list_collections()
    expected = set(COLLECTION_REGISTRY.keys())
    missing = expected - set(all_collections)
    if missing:
        logger.error(f"Missing collections: {missing}")
        sys.exit(1)

    logger.info(f"All {len(expected)} collections verified: {sorted(expected)}")
    logger.info("Milvus initialization complete.")


if __name__ == "__main__":
    main()
