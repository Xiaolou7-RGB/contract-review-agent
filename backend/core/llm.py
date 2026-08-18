"""
LLM factory — provides get_structured_llm() and get_llm() for all pipeline nodes.
Uses langchain-openai with DeepSeek API.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, TypeVar

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.runnables import Runnable

from backend.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Deterministic decoding for the review pipeline (parse / review / revise), so
# repeated reviews of the same contract yield stable risk cards and scores.
# QA intentionally keeps its own warmer LLM (qa_service.py, QA_TEMPERATURE=0.2)
# and is NOT affected by this value.
REVIEW_TEMPERATURE = 0.0

# 绕过系统代理的 httpx 客户端（真实 Windows 坑：系统代理/HTTPS_PROXY 会被
# httpx 默认探测 trust_env=True，导致 DeepSeek 请求经代理后 TLS 握手失败）。
_HTTP_ASYNC_CLIENT = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(120.0, connect=15.0))
_HTTP_SYNC_CLIENT = httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0, connect=15.0))

# ── Agent 类型 → 模型 路由表 ─────────────────────────────────
# 默认全部走 settings.llm_model（换全局模型改 .env.local 的 LLM_MODEL 一行）。
# 想给某类业务单独换更强模型，改这里对应的值为具体模型名即可。
_AGENT_MODEL_ROUTING: dict[str, str] = {
    "review": get_settings().llm_model,   # 多维度风险评审
    "parse":  get_settings().llm_model,   # 条款拆解
    "revise": get_settings().llm_model,   # 修订建议
    "qa":     get_settings().llm_model,   # 法律问答（实际由 qa_service 独立构造，此处仅登记）
}


@lru_cache(maxsize=8)
def _cached_llm(model: str) -> ChatOpenAI:
    settings = get_settings()
    api_base = settings.llm_api_base
    api_key = settings.llm_api_key
    logger.info(f"Initializing LLM: base={api_base} model={model}")
    return ChatOpenAI(
        base_url=api_base,
        api_key=api_key,
        model=model,
        temperature=REVIEW_TEMPERATURE,
        max_tokens=4096,
        # max_retries=0：模型层不重试，重试统一由 core.retry 三层兜底接管，
        # 避免模型层 + 应用层双重重试。
        max_retries=0,
        http_async_client=_HTTP_ASYNC_CLIENT,
        http_client=_HTTP_SYNC_CLIENT,
    )


def get_llm(agent_type: str = "review") -> ChatOpenAI:
    """按 Agent 类型获取模型实例（带缓存）。

    相同模型只创建一次；agent_type 必须在路由表里，否则报错早暴露问题。
    """
    if agent_type not in _AGENT_MODEL_ROUTING:
        raise ValueError(
            f"未知 agent_type: '{agent_type}'，可用类型：{list(_AGENT_MODEL_ROUTING)}"
        )
    model = _AGENT_MODEL_ROUTING[agent_type]
    return _cached_llm(model)


def get_structured_llm(output_schema: type[T], agent_type: str = "review") -> Runnable[Any, T]:
    """Return a runnable that outputs structured data per the given Pydantic model.

    Uses function_calling method — DeepSeek does not support the
    default json_schema (beta parse) mode.
    """
    return get_llm(agent_type).with_structured_output(output_schema, method="function_calling")
