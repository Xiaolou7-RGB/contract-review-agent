"""
Degradation strategies for each pipeline node.
Every node has a fallback path so the pipeline never blocks.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def with_degradation(
    node_name: str,
    primary_fn,
    fallback_fn,
    *args,
    max_retries: int = 2,
    **kwargs,
) -> Any:
    """
    Execute primary_fn with retries. On persistent failure, execute fallback_fn.
    Returns (result, degraded: bool).
    """
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 2):
        try:
            result = await primary_fn(*args, **kwargs)
            if attempt > 1:
                logger.info(f"{node_name} succeeded on attempt {attempt}")
            return result, False
        except Exception as e:
            last_error = e
            logger.warning(f"{node_name} attempt {attempt}/{max_retries + 1} failed: {e}")

    logger.error(f"{node_name} all {max_retries + 1} attempts failed, degrading. Last error: {last_error}")

    try:
        result = await fallback_fn(*args, **kwargs)
        return result, True
    except Exception as e:
        logger.error(f"{node_name} fallback also failed: {e}")
        raise
