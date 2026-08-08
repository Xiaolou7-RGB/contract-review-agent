"""
FastAPI application entry point.
Registers all routers with /api/v1 prefix.
"""
from __future__ import annotations

import os
import logging

# HF_ENDPOINT must be set before any HuggingFace imports (K-7)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Create logs directory
os.makedirs("logs", exist_ok=True)

# Configure logging
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.getenv("LOG_FILE", "logs/contract_review.log"),
            encoding="utf-8",
        ) if os.getenv("LOG_FILE") else logging.NullHandler(),
    ],
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Contract Review Assistant",
    description="AI-powered contract review with RAG and HITL",
    version="0.1.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routers ───────────────────────────────────────

from backend.api.v1.contract import router as contract_router
from backend.api.v1.contract_qa import router as contract_qa_router
from backend.api.v1.auth import router as auth_router
from backend.api.v1.admin_kb import router as admin_kb_router

app.include_router(contract_router, prefix="/api/v1")
app.include_router(contract_qa_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(admin_kb_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "contract-review"}


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8801"))
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run("backend.main:app", host=host, port=port, reload=True)
