"""
FastAPI application entry point.
Registers all routers with /api/v1 prefix.
"""
from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager

# HF_ENDPOINT must be set before any HuggingFace imports (K-7)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.core.logger import configure_logging

_settings = get_settings()

# Create logs directory
os.makedirs("logs", exist_ok=True)

# Configure logging (结构化日志 + 压低第三方噪音)
configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时并行预热本地模型（embedding + reranker），避免首个请求卡顿。

    单个模型加载失败不阻断启动（warmup 内部已降级为首次请求 lazy-load）。
    """
    from backend.core.rag import warmup_models_async
    await warmup_models_async()
    yield


app = FastAPI(
    title="Contract Review Assistant",
    description="AI-powered contract review with RAG and HITL",
    version="0.1.0",
    lifespan=lifespan,
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

    host = _settings.server_host
    port = _settings.server_port
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run("backend.main:app", host=host, port=port, reload=True)
