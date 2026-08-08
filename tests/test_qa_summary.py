"""
Tests for the QA rolling-summary (long-term memory) logic.
Pure functions + _maybe_summarize with fake DB / fake LLM (no network).
"""
from __future__ import annotations

import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.agents.contract_qa import qa_service
from backend.agents.contract_qa.qa_service import (
    SUMMARY_TRIGGER_MESSAGES,
    needs_summary,
    _trunc_text,
    format_summary_prompt,
    build_prompt_messages,
    _maybe_summarize,
)
from backend.agents.contract_qa.context_builder import build_citations


def _ctx() -> dict:
    return {
        "review": {"original_filename": "t.docx", "contract_type": "采购合同"},
        "clauses": [], "cards": [], "evidence": [], "revisions": [],
        "law_hits": [], "citations": build_citations([]), "law_empty": True,
    }


class TestNeedsSummary:
    def test_below_trigger(self):
        assert needs_summary(SUMMARY_TRIGGER_MESSAGES - 1) is False

    def test_at_trigger(self):
        assert needs_summary(SUMMARY_TRIGGER_MESSAGES) is True

    def test_zero(self):
        assert needs_summary(0) is False


class TestTruncText:
    def test_short_unchanged(self):
        assert _trunc_text("abc", 10) == "abc"

    def test_long_truncated(self):
        out = _trunc_text("字" * 100, 10)
        assert out.startswith("字" * 10) and out.endswith("…")

    def test_none_safe(self):
        assert _trunc_text(None, 5) == ""


class TestFormatSummaryPrompt:
    def test_contains_dialogue_and_old_summary(self):
        msgs = format_summary_prompt("旧摘要：关注第四条", "用户：问题一\n助手：回答一")
        assert msgs[0].type == "system" and msgs[1].type == "human"
        assert "旧摘要：关注第四条" in msgs[1].content
        assert "用户：问题一" in msgs[1].content

    def test_empty_old_summary_placeholder(self):
        msgs = format_summary_prompt("", "用户：q\n助手：a")
        assert "（暂无）" in msgs[1].content

    def test_requirements_listed(self):
        msgs = format_summary_prompt("", "d")
        assert "条款号与风险点" in msgs[0].content
        assert "尚未解决的追问" in msgs[0].content


class TestPromptSummaryInjection:
    def test_summary_in_system_message(self):
        msgs = build_prompt_messages(_ctx(), [], "q", summary="摘要：用户关注违约金。")
        system = msgs[0].content
        assert "【历史对话摘要】" in system
        assert "摘要：用户关注违约金。" in system
        # Summary sits after the contract meta block…
        assert system.index("【合同信息】") < system.index("【历史对话摘要】")
        # …and, with no rendered context in this fixture, it closes the system message
        assert system.rstrip().endswith("摘要：用户关注违约金。")

    def test_summary_before_rendered_context(self):
        ctx = _ctx()
        ctx["law_empty"] = False
        ctx["clauses"] = [{"clause_id": "C1", "seq_no": 1, "title": "付款",
                           "content": "甲方应在验收合格后付款"}]
        msgs = build_prompt_messages(ctx, [], "q", summary="摘要内容X")
        system = msgs[0].content
        assert system.index("【历史对话摘要】") < system.rfind("【上下文】")

    def test_empty_summary_not_injected(self):
        system = build_prompt_messages(_ctx(), [], "q", summary="")[0].content
        assert "【历史对话摘要】" not in system

    def test_whitespace_summary_not_injected(self):
        system = build_prompt_messages(_ctx(), [], "q", summary="   ")[0].content
        assert "【历史对话摘要】" not in system


# ── _maybe_summarize with fakes ──────────────────────────────────────────

class FakeSummaryDB:
    def __init__(self, count: int, summary: str = "", watermark: int = 0, rows=None):
        self.count = count
        self._summary = summary
        self._watermark = watermark
        self._rows = rows if rows is not None else []
        self.executed: list[tuple[str, tuple]] = []
        self.fetchrow_calls = 0

    async def fetchval(self, sql, *args):
        return self.count

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls += 1
        return {"summary": self._summary, "summarized_until": self._watermark}

    async def fetch(self, sql, *args):
        return self._rows

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))


class FakeSummaryLLM:
    def __init__(self, text: str = "摘要：用户关注第四条违约责任与违约金比例。"):
        self.text = text
        self.invoked_with = None

    async def ainvoke(self, messages):
        self.invoked_with = messages

        class R:
            content = self.text

        return R()


class TestMaybeSummarize:
    def test_below_trigger_does_nothing(self):
        db = FakeSummaryDB(count=5)

        async def run():
            await _maybe_summarize(db, 1)

        asyncio.run(run())
        assert db.fetchrow_calls == 0  # never even reads the session row
        assert db.executed == []

    def test_no_new_messages_does_nothing(self):
        db = FakeSummaryDB(count=30, rows=[])

        async def run():
            await _maybe_summarize(db, 1)

        asyncio.run(run())
        assert db.executed == []

    def test_writes_summary_and_watermark(self, monkeypatch):
        rows = [
            {"id": 12, "role": "assistant", "content": "回答B"},
            {"id": 11, "role": "user", "content": "问题B"},
            {"id": 10, "role": "assistant", "content": "回答A"},
            {"id": 9, "role": "user", "content": "问题A"},
        ]
        db = FakeSummaryDB(count=30, summary="旧摘要", watermark=5, rows=rows)
        fake_llm = FakeSummaryLLM()
        monkeypatch.setattr(qa_service, "_build_qa_llm", lambda max_tokens=None: fake_llm)

        async def run():
            await _maybe_summarize(db, 7)

        asyncio.run(run())

        # Dialogue fed to the LLM is chronological and includes the old summary
        dialogue = fake_llm.invoked_with[1].content
        assert "旧摘要" in dialogue
        assert dialogue.index("问题A") < dialogue.index("回答A") < dialogue.index("问题B")

        # Summary + watermark persisted
        updates = [e for e in db.executed if e[0].startswith("UPDATE contract_qa_sessions SET summary")]
        assert len(updates) == 1
        _, args = updates[0]
        assert args[0] == 7
        assert args[1] == "摘要：用户关注第四条违约责任与违约金比例。"
        assert args[2] == 12  # newest message id in the batch

    def test_empty_llm_reply_skips_write(self, monkeypatch):
        rows = [{"id": 21, "role": "user", "content": "q"}]
        db = FakeSummaryDB(count=25, rows=rows)
        monkeypatch.setattr(
            qa_service, "_build_qa_llm", lambda max_tokens=None: FakeSummaryLLM(text="   ")
        )

        async def run():
            await _maybe_summarize(db, 1)

        asyncio.run(run())
        assert db.executed == []
