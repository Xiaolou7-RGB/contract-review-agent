"""
LLM factory — provides get_structured_llm() and get_llm() for all pipeline nodes.
Uses langchain-openai with DeepSeek API.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, TypeVar

from langchain_openai import ChatOpenAI
from langchain_core.runnables import Runnable

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Deterministic decoding for the review pipeline (parse / review / revise), so
# repeated reviews of the same contract yield stable risk cards and scores.
# QA intentionally keeps its own warmer LLM (qa_service.py, QA_TEMPERATURE=0.2)
# and is NOT affected by this value.
REVIEW_TEMPERATURE = 0.0


@lru_cache(maxsize=1)
def _cached_llm() -> ChatOpenAI:
    api_base = os.getenv("LLM_API_BASE", "http://localhost:11434/v1")
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "deepseek-chat")
    logger.info(f"Initializing LLM: base={api_base} model={model}")
    return ChatOpenAI(
        base_url=api_base,
        api_key=api_key,
        model=model,
        temperature=REVIEW_TEMPERATURE,
        max_tokens=4096,
    )


def get_llm() -> ChatOpenAI:
    """Return the raw ChatOpenAI instance."""
    return _cached_llm()


def get_structured_llm(output_schema: type[T]) -> Runnable[Any, T]:
    """Return a runnable that outputs structured data per the given Pydantic model.

    Uses function_calling method — DeepSeek does not support the
    default json_schema (beta parse) mode.
    """
    return _cached_llm().with_structured_output(output_schema, method="function_calling")
