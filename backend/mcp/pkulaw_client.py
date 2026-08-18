"""
pkulaw_client.py — 北大法宝 MCP 客户端（真实司法案例检索）。

通过 Streamable HTTP + SSE 调用北大法宝案例检索服务 get_case_list，
为高风险条款补充带「真实案号」的判例，对抗 LLM 编造案例的幻觉问题。

服务端点：https://apim-gateway.pkulaw.com/mcp-case（SSE 流式）
工具：get_case_list(fulltext=..., title=..., court=...) → 返回前 20 条案例
鉴权：Authorization: Bearer <PKULAW_TOKEN>（从 .env.local 读，勿硬编码）

设计原则：search_cases 失败一律返回 []（不抛异常），由调用方做降级兜底，
保证北大法宝 MCP 不可用时审查流水线照常走本地知识库。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from backend.config import get_settings

logger = logging.getLogger(__name__)


def _extract_sse_json(text: str) -> list[dict[str, Any]]:
    """从 SSE 文本里提取所有 ``data:`` 行的 JSON 对象。"""
    msgs: list[dict[str, Any]] = []
    for line in text.split("\n"):
        if line.startswith("data:"):
            try:
                msgs.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                continue
    return msgs


async def search_cases(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """检索真实司法案例，返回标准化案例列表。

    返回每个案例的关键字段：title / case_no（真实案号）/ court / date /
    case_gist（裁判要旨）/ category。失败或未配置 token 时返回 []。
    """
    settings = get_settings()
    if not settings.pkulaw_token:
        logger.warning("PKULAW_TOKEN 未配置，跳过北大法宝案例检索")
        return []
    if not query or not query.strip():
        return []

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {settings.pkulaw_token}",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "get_case_list",
            "arguments": {"fulltext": query.strip()[:100]},
        },
    }

    try:
        async with httpx.AsyncClient(timeout=settings.pkulaw_timeout, trust_env=False) as client:
            resp = await client.post(settings.pkulaw_case_url, json=payload, headers=headers)
            resp.raise_for_status()
            msgs = _extract_sse_json(resp.text)
    except Exception as e:  # noqa: BLE001 — 降级兜底，绝不让 MCP 拖垮流水线
        logger.warning(f"北大法宝案例检索失败（降级兜底）: {type(e).__name__} {e}")
        return []

    for msg in msgs:
        if "result" not in msg:
            continue
        for content in msg["result"].get("content", []):
            if content.get("type") != "text":
                continue
            try:
                data = json.loads(content["text"])
            except json.JSONDecodeError:
                continue
            cases = data.get("Data", []) if isinstance(data, dict) else []
            out: list[dict[str, Any]] = []
            for c in cases[:top_k]:
                out.append({
                    "title": c.get("Title", ""),
                    "case_no": c.get("CaseFlag", ""),       # 真实案号，如 (2019)桂民申1358号
                    "court": c.get("Court", ""),
                    "date": c.get("LastInstanceDate", ""),
                    "case_grade": "、".join(c.get("CaseGrade", []) or []),
                    "case_gist": c.get("CaseGist", ""),      # 裁判要旨（可能为空）
                    "category": "、".join(c.get("Category", []) or []),
                })
            return out

    return []
