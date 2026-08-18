"""
Clause parser — Node ① of the contract review pipeline.
Extracts structured clauses from contract text and identifies contract type.

Primary path: LLM structured extraction (get_structured_llm)
Fallback: pdfplumber page splitting + regex clause parsing
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from backend.agents.contract_review.schemas import Clause, ContractReviewState
from backend.core.llm import get_structured_llm

logger = logging.getLogger(__name__)

# ── Contract type identification ────────────────────────────

CONTRACT_TYPE_KEYWORDS: dict[str, list[str]] = {
    "买卖": ["买卖", "销售", "采购", "供货", "购货", "标的物", "交付", "验收", "质量"],
    "服务": ["服务", "委托", "咨询", "技术开发", "外包", "维护", "培训", "承揽", "受托", "技术服务", "运维", "开发合同"],
    "劳动": ["劳动", "劳动合", "用工", "薪酬", "工资", "社保", "解雇", "竞业限制", "劳动合同", "用人单位", "劳动者"],
    "借款": ["借款", "贷款", "利息", "本金", "抵押", "质押", "还款", "债权", "年利率", "连带责任保证", "担保人"],
    "保密": ["保密", "机密", "NDA", "非公开", "商业秘密", "保密信息", "披露", "保密协议", "confidential"],
    "租赁": ["租赁", "出租", "承租", "租金", "租期", "转租", "押金", "房租", "出租人", "承租人", "租用"],
}
OTHER_THRESHOLD = 2  # minimum keyword hits to classify, else "其他"


def identify_contract_type(text: str) -> str:
    """Classify contract type by keyword density. Returns 买卖/服务/劳动/借款/保密/其他."""
    text_short = text[:3000]
    scores: dict[str, int] = {}
    for ctype, keywords in CONTRACT_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_short)
        if score > 0:
            scores[ctype] = score
    if not scores:
        return "其他"
    best = max(scores, key=lambda k: scores[k])  # type: ignore[arg-type]
    if scores[best] < OTHER_THRESHOLD:
        return "其他"
    return best


# ── Stable clause ID ────────────────────────────────────────

def make_clause_id(content: str, page: int) -> str:
    """Generate a stable clause ID from content prefix + page number."""
    raw = (content[:80] + str(page)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# ── Regex fallback clause splitting ─────────────────────────

CLAUSE_PATTERNS = [
    # 第X条 / 第X章 / 第X节
    re.compile(r"(第[一二三四五六七八九十百千]+条[^\n]*)"),
    re.compile(r"(第[一二三四五六七八九十百千]+章[^\n]*)"),
    re.compile(r"(第[一二三四五六七八九十百千]+节[^\n]*)"),
    # Numbered sections: 1. / 1.1 / 1.1.1 / (1) / 一、
    re.compile(r"(^\d+[\.\、\)）]\s*[^\n]+)", re.MULTILINE),
    re.compile(r"(^\d+\.\d+[\.\s][^\n]+)", re.MULTILINE),
    re.compile(r"(^[\(（]\d+[\)）][^\n]+)", re.MULTILINE),
    re.compile(r"(^[一二三四五六七八九十]+[、\.,，。][^\n]+)", re.MULTILINE),
]


def _regex_split_clauses(text: str, page: int = 1) -> list[dict[str, Any]]:
    """Fallback: split text into clauses using Chinese legal regex patterns."""
    clauses: list[dict[str, Any]] = []

    # Try each pattern; find best one that yields most reasonable chunks
    best_matches: list[tuple[int, int, str]] = []  # (start, end, matched_text)
    best_pattern = None

    for pat in CLAUSE_PATTERNS:
        matches = list(pat.finditer(text))
        if 3 <= len(matches) <= 80:  # reasonable clause count
            if len(matches) > len(best_matches):
                best_matches = [(m.start(), m.end(), m.group()) for m in matches]
                best_pattern = pat

    if not best_matches:
        # Absolute fallback: split by double newline
        parts = text.split("\n\n")
        for i, part in enumerate(parts):
            part = part.strip()
            if len(part) < 10:
                continue
            clauses.append({
                "clause_id": make_clause_id(part, page),
                "seq_no": i + 1,
                "type": "",
                "title": "",
                "content": part,
                "page": page,
                "char_start": text.find(part),
                "char_end": text.find(part) + len(part),
                "span": {"page": page},
            })
        return clauses

    # Build clauses from matches + gap text
    for i, (start, end, matched) in enumerate(best_matches):
        # Include text after this match until next match (or end)
        next_start = best_matches[i + 1][0] if i + 1 < len(best_matches) else len(text)
        content = text[start:next_start].strip()
        clauses.append({
            "clause_id": make_clause_id(content, page),
            "seq_no": i + 1,
            "type": "",
            "title": matched.strip()[:120],
            "content": content,
            "page": page,
            "char_start": start,
            "char_end": next_start,
            "span": {"page": page},
        })

    return clauses


# ── Pydantic schema for LLM structured output ───────────────

from pydantic import BaseModel, Field


class _ParsedClause(BaseModel):
    seq_no: str = Field(default="1", description="条款序号，如 1, 2, 3")
    type: str = Field(default="", description="条款类型如 付款条款/违约责任/定义条款/保密条款")
    title: str = Field(default="")
    content: str = Field(...)


class _ParseResult(BaseModel):
    contract_type: str = Field(default="其他", description="买卖/服务/劳动/借款/保密/其他")
    clauses: list[_ParsedClause]


# ── Main parse function ─────────────────────────────────────

import json


PARSE_PROMPT = """你是一个JSON输出助手。请严格按照JSON Schema输出，不要输出任何其他内容。

请从以下合同文本中：
1. 识别合同类型（只能是：买卖、服务、劳动、借款、保密、其他 之一）
2. 将合同拆分为独立的条款单元

要求：
- 每个条款保持完整，不要切断
- 条款按原文顺序排列。seq_no 使用数字字符串如 "1", "2", "3"
- 条款类型（type 字段）包括但不限于：定义条款、付款条款、交付条款、验收条款、违约责任、保密条款、知识产权、争议解决、不可抗力、合同变更、通知送达、其他
- 【关键】title 字段只填"真实条款标题"（如"第一条 租赁期限"、"第二条 违约责任"）。以下属于非实质内容，其 type 和 title 都必须设为空字符串 ""，正文只放入 content：
  · 合同标题（如"房屋租赁合同"）
  · 合同编号行
  · 当事人信息（甲乙双方基本信息）
  · 前言 / 声明 / 鉴于条款
  · 签署栏 / 落款
- 绝对不要自创"合同标题""当事人信息""前言声明""签署落款"之类的分类标签填入 title 字段

合同文本：
{text}"""


async def parse_clauses(text: str, page: int = 1) -> dict[str, Any]:
    """
    Extract clauses from contract text.
    Returns {"contract_type": str, "clauses": list[dict]}.
    Falls back to regex on LLM failure.
    """
    logger.info(f"Parsing contract text: {len(text)} chars, page={page}")

    # ── Try LLM structured extraction ──
    try:
        llm = get_structured_llm(_ParseResult)
        result: _ParseResult = await llm.ainvoke(
            PARSE_PROMPT.format(text=text[:12000])
        )
        contract_type = result.contract_type or identify_contract_type(text)
        clauses: list[dict[str, Any]] = []
        for c in result.clauses:
            content = c.content.strip()
            if not content:
                continue
            clauses.append({
                "clause_id": make_clause_id(content, page),
                "seq_no": int(c.seq_no) if c.seq_no.isdigit() else i + 1,
                "type": c.type,
                "title": c.title,
                "content": content,
                "page": page,
                "char_start": 0,
                "char_end": 0,
                "span": {"page": page},
            })
        # Try to fill char_start/char_end from original text
        for cl in clauses:
            pos = text.find(cl["content"])
            if pos >= 0:
                cl["char_start"] = pos
                cl["char_end"] = pos + len(cl["content"])

        logger.info(f"LLM extracted {len(clauses)} clauses, type={contract_type}")
        return {"contract_type": contract_type, "clauses": clauses}

    except Exception as e:
        logger.warning(f"LLM clause parsing failed ({type(e).__name__}: {e}), falling back to regex", exc_info=True)

    # ── Fallback: regex split + keyword contract_type ──
    contract_type = identify_contract_type(text)
    clauses = _regex_split_clauses(text, page)
    logger.info(f"Regex fallback extracted {len(clauses)} clauses, type={contract_type}")
    return {"contract_type": contract_type, "clauses": clauses}


# ── LangGraph node ──────────────────────────────────────────

async def clause_parser_node(state: ContractReviewState) -> dict[str, Any]:
    """LangGraph node: parse clauses and identify contract type."""
    text = state.get("text", "")
    page = 1  # Default page for single-document input

    result = await parse_clauses(text, page)

    return {
        "clauses": result["clauses"],
        "contract_type": result["contract_type"],
        "status": "parsed",
    }
