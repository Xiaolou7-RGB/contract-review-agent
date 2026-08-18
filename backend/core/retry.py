"""
retry.py — 三层兜底与重试机制。

三层兜底，一层比一层更保底：
  第一层：自动重试（带退避 + 单次超时），消化网络抖动 / LLM 超时等短暂故障
  第二层：节点级降级（退化为更简单的实现）
  第三层：系统级兜底（返回安全空结果，保证调用方始终能拿到结果，绝不裸抛）

用法（函数式，向后兼容旧 with_degradation）：
    result, degraded = await with_degradation("review", primary, fallback)

用法（装饰器）：
    @with_retry(node_name="review", fallback=fallback)
    async def _primary():
        ...
    result, degraded = await _primary()
"""
from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any, Callable

from backend.core.exceptions import NON_RETRYABLE_ERRORS
from backend.core.logger import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 2                  # 最多重试 2 次（加上首次 = 共 3 次尝试）
RETRY_DELAYS = [1.0, 3.0]        # 第 1 次重试前等 1 秒，第 2 次前等 3 秒
TIMEOUT_PER_ATTEMPT = 300.0      # 单次调用超时（审查节点含多维度 fan-out + LLM，给足余量）


async def _run_with_fallback(
    node_name: str,
    primary_fn: Callable,
    fallback_fn: Callable | None,
    max_retries: int = MAX_RETRIES,
) -> tuple[Any, bool]:
    """三层兜底核心。返回 (result, degraded: bool)。"""
    last_error: Exception | None = None

    # ── 第一层：自动重试 ──
    for attempt in range(max_retries + 1):
        try:
            result = await asyncio.wait_for(
                primary_fn(),
                timeout=TIMEOUT_PER_ATTEMPT,
            )
            if attempt > 0:
                logger.info("retry.succeeded", node=node_name, attempt=attempt + 1)
            return result, False
        except NON_RETRYABLE_ERRORS as e:
            # 不可重试异常：重试也没用，直接进入降级
            logger.warning("retry.non_retryable_error", node=node_name, error=str(e))
            last_error = e
            break
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                logger.warning(
                    "retry.attempt_failed", node=node_name,
                    attempt=attempt + 1, max_retries=max_retries, delay=delay, error=str(e),
                )
                await asyncio.sleep(delay)
            else:
                logger.error("retry.all_attempts_failed", node=node_name, error=str(e))

    # ── 第二层：节点级降级 ──
    if fallback_fn is not None:
        try:
            result = await fallback_fn()
            logger.info("retry.fallback_succeeded", node=node_name)
            return result, True
        except Exception as e:
            logger.error("retry.fallback_failed", node=node_name, error=str(e))

    # ── 第三层：系统级兜底（永不失败）──
    logger.error("retry.system_fallback", node=node_name, original_error=str(last_error))
    return _system_fallback(node_name), True


def _system_fallback(node_name: str) -> Any:
    """第三层系统兜底：返回安全空结果。

    contract 审查流水线的三个节点结果类型都是 list（cards/evidence/revisions），
    空列表对下游 merge/extend 均安全，保证流水线绝不因单节点失败而崩。
    """
    return []


async def with_degradation(
    node_name: str,
    primary_fn: Callable,
    fallback_fn: Callable,
    *args,
    max_retries: int = MAX_RETRIES,
    **kwargs,
) -> tuple[Any, bool]:
    """执行 primary_fn，三层兜底。返回 (result, degraded)。

    向后兼容旧接口：primary_fn / fallback_fn 可接收 *args/**kwargs。
    """

    async def _primary():
        return await primary_fn(*args, **kwargs)

    async def _fallback():
        return await fallback_fn(*args, **kwargs)

    return await _run_with_fallback(node_name, _primary, _fallback, max_retries)


def with_retry(
    node_name: str,
    fallback: Callable | None = None,
    max_retries: int = MAX_RETRIES,
) -> Callable:
    """装饰器工厂：给异步函数套上「重试 → 降级 → 系统兜底」三层保护。

    被装饰函数被调用时返回 (result, degraded) 元组。
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> tuple[Any, bool]:
            async def _primary():
                return await func(*args, **kwargs)

            async def _fallback():
                return await fallback(*args, **kwargs)

            return await _run_with_fallback(
                node_name,
                _primary,
                _fallback if fallback is not None else None,
                max_retries,
            )

        return wrapper

    return decorator
