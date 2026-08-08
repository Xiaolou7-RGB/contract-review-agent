"""
Tests for the QA answer service.
Prompt composition tests are pure; the stream flow test uses a fake DB,
a fake retriever and FakeListChatModel (no network / no Milvus).
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.agents.contract_qa import context_builder, qa_service
from backend.agents.contract_qa.qa_service import build_prompt_messages


def _ctx(law_empty: bool = False) -> dict:
    hits = [] if law_empty else [
        {"id": "art_577", "content": "第五百七十七条 当事人一方不履行合同义务……",
         "article_no": "第五百七十七条", "chapter": "合同编", "confidence": 0.88},
    ]
    return {
        "review": {"original_filename": "t.docx", "contract_type": "采购合同"},
        "clauses": [{"clause_id": "C1", "seq_no": 1, "title": "付款", "content": "甲方付款"}],
        "cards": [{"clause_id": "C1", "dimension": "合规性", "score": 0.8, "level": "高",
                   "suggestion": "改", "risk_type": ""}],
        "evidence": [],
        "revisions": [],
        "law_hits": hits,
        "citations": context_builder.build_citations(hits),
        "law_empty": law_empty,
    }


class TestBuildPromptMessages:
    def test_system_contains_hard_rules(self):
        msgs = build_prompt_messages(_ctx(), [], "违约金怎么算？")
        system = msgs[0].content
        assert "范围守卫" in system
        assert "引用溯源" in system
        assert "严禁编造" in system
        assert "【合同信息】" in system and "t.docx" in system
        assert "【上下文】" in system
        assert "[1] 《中华人民共和国民法典》第五百七十七条" in system

    def test_law_empty_adds_no_law_note(self):
        msgs = build_prompt_messages(_ctx(law_empty=True), [], "依据是什么？")
        system = msgs[0].content
        assert "未检索到直接法律依据" in system
        assert "不得出现任何法条编号引用" in system

    def test_law_present_has_no_no_law_note(self):
        system = build_prompt_messages(_ctx(), [], "q")[0].content
        assert "未检索到直接法律依据" not in system

    def test_history_ordering_and_last_question(self):
        history = [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
        ]
        msgs = build_prompt_messages(_ctx(), history, "第二问")
        # [system, user, assistant, human(question)]
        assert msgs[1].content == "第一问" and msgs[1].type == "human"
        assert msgs[2].content == "第一答" and msgs[2].type == "ai"
        assert msgs[-1].content == "第二问" and msgs[-1].type == "human"


# ── Stream flow test (fake DB + fake retriever + fake LLM) ──────────────

class FakeDB:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.closed = False

    async def fetchrow(self, sql, *args):
        s = " ".join(sql.split())
        if "FROM contract_qa_messages m" in s:
            return {"session_id": 10, "contract_id": 9999}
        if "FROM contract_reviews" in s:
            return {"id": 9999, "user_id": 1, "original_filename": "t.docx",
                    "contract_type": "采购合同", "status": "completed"}
        return None

    async def fetch(self, sql, *args):
        s = " ".join(sql.split())
        if "contract_clauses" in s:
            return [{"clause_id": "C1", "seq_no": 1, "type": "付款",
                     "title": "付款条款", "content": "甲方付款"}]
        if "contract_review_cards" in s:
            return [{"clause_id": "C1", "dimension": "合规性", "score": 0.8,
                     "level": "高", "suggestion": "改", "risk_type": ""}]
        if "contract_evidence" in s:
            return [{"clause_id": "C1", "source_id": "s1",
                     "source_collection": "civil_code_hybrid",
                     "quote": "q", "confidence": 0.9}]
        if "revision_accepts" in s:
            return [{"clause_id": "C1", "before_text": "b",
                     "after_text": "a", "status": "pending"}]
        if "role, content FROM contract_qa_messages" in s:
            return [{"role": "user", "content": "上一问"},
                    {"role": "assistant", "content": "上一答"}]
        return []

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))

    async def fetchval(self, sql, *args):
        # Summary trigger check (_maybe_summarize): below threshold → no-op.
        return 0

    async def close(self):
        self.closed = True


class TestStreamAnswerFlow:
    def test_full_flow_with_fakes(self, monkeypatch):
        from langchain_community.chat_models import FakeListChatModel
        import asyncpg as asyncpg_mod

        fake_db = FakeDB()

        async def fake_connect(url):
            return fake_db

        async def fake_law_hits(question, hyde_question=None):
            return [{
                "id": "art_577", "content": "第五百七十七条 …",
                "article_no": "第五百七十七条", "chapter": "合同编", "confidence": 0.88,
            }]

        monkeypatch.setattr(asyncpg_mod, "connect", fake_connect)
        monkeypatch.setattr(context_builder, "retrieve_law_hits", fake_law_hits)
        monkeypatch.setattr(
            qa_service, "_build_qa_llm",
            lambda: FakeListChatModel(responses=["答案：依据 [1] 第五百七十七条。"]),
        )

        async def run():
            events = []
            async for ev in qa_service.stream_answer(123, "违约责任依据？"):
                events.append(ev)
            return events

        events = asyncio.run(run())

        types = [e["type"] for e in events]
        assert types[0] == "citations", "citations must be emitted before deltas"
        assert types[-1] == "done"
        assert "delta" in types

        cites = events[0]["items"]
        assert cites[0]["ref"] == "[1]" and cites[0]["source_id"] == "art_577"

        # Answer persisted with citations, status completed
        updates = [e for e in fake_db.executed if e[0].startswith("UPDATE contract_qa_messages SET content")]
        assert len(updates) == 1
        _, args = updates[0]
        assert args[0] == 123
        assert "第五百七十七条" in args[1]
        assert json.loads(args[2])[0]["ref"] == "[1]"

        assert fake_db.closed

    def test_missing_message_yields_error(self, monkeypatch):
        import asyncpg as asyncpg_mod

        class EmptyDB(FakeDB):
            async def fetchrow(self, sql, *args):
                return None

        async def fake_connect(url):
            return EmptyDB()

        monkeypatch.setattr(asyncpg_mod, "connect", fake_connect)

        async def run():
            return [ev async for ev in qa_service.stream_answer(999, "q")]

        events = asyncio.run(run())
        assert events == [{"type": "error", "message": "Message not found"}]
