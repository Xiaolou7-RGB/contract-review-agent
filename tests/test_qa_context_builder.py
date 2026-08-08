"""
Tests for the QA context builder (pure functions — no DB/Milvus access).
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.agents.contract_qa.context_builder import (
    build_citations,
    render_context,
    _review_block,
    _trunc,
    build_law_query,
    _referenced_clause_ids,
    is_meta_question,
    classify_question,
    retrieve_law_hits,
    split_sub_queries,
    LAW_RETRIEVE_THRESHOLD,
)
from backend.core.context_manager import estimate_tokens


def _fake_hits(n: int) -> list[dict]:
    return [
        {
            "id": f"art_{i}",
            "content": f"第{100+i}条内容" + "测" * 50,
            "article_no": f"第{100+i}条",
            "chapter": "合同编",
            "confidence": 0.9 - i * 0.1,
        }
        for i in range(n)
    ]


def _base_ctx(clauses=2, cards=1, evidence=1, revisions=1, law=3) -> dict:
    return {
        "review": {"original_filename": "test.docx", "contract_type": "采购合同"},
        "clauses": [
            {"clause_id": f"C{i}", "seq_no": i + 1, "title": f"条款{i+1}", "content": "内容" * 30}
            for i in range(clauses)
        ],
        "cards": [
            {"clause_id": "C1", "dimension": "合规性", "score": 0.8, "level": "高",
             "suggestion": "建议修改违约金比例" * 5, "risk_type": "违约金过高"}
            for _ in range(cards)
        ],
        "evidence": [
            {"clause_id": "C1", "source_id": f"src_{j}", "source_collection": "civil_code_hybrid",
             "quote": "当事人可以约定违约金" * 5, "confidence": 0.85 - j * 0.05}
            for j in range(evidence)
        ],
        "revisions": [
            {"clause_id": "C1", "before_text": "旧文本", "after_text": "新文本建议" * 5, "status": "pending"}
            for _ in range(revisions)
        ],
        "law_hits": _fake_hits(law),
        "citations": build_citations(_fake_hits(law)),
        "law_empty": law == 0,
    }


class TestBuildCitations:
    def test_numbering_is_sequential(self):
        cites = build_citations(_fake_hits(3))
        assert [c["ref"] for c in cites] == ["[1]", "[2]", "[3]"]

    def test_citation_fields_match_hits(self):
        hits = _fake_hits(2)
        cites = build_citations(hits)
        for cite, hit in zip(cites, hits):
            assert cite["source_id"] == hit["id"]
            assert cite["article_no"] == hit["article_no"]
            assert cite["collection"] == "civil_code_hybrid"
            assert cite["score"] == round(hit["confidence"], 4)
            assert len(cite["quote"]) <= 200

    def test_empty_hits_give_empty_citations(self):
        assert build_citations([]) == []


class TestRenderContext:
    def test_renders_all_sections(self):
        text = render_context(_base_ctx())
        assert "【合同条款】" in text
        assert "【审查结果（多维评审 + 法律依据）】" in text
        assert "【修订建议】" in text
        assert "【实时检索到的法律依据（引用时使用编号）】" in text

    def test_law_entries_are_numbered(self):
        text = render_context(_base_ctx())
        assert "[1] 《中华人民共和国民法典》" in text
        assert "[3] 《中华人民共和国民法典》" in text

    def test_law_empty_renders_no_numbered_refs(self):
        text = render_context(_base_ctx(law=0))
        assert "[1] 《中华人民共和国民法典》" not in text
        assert "【实时检索到的法律依据" not in text

    def test_within_budget(self):
        ctx = _base_ctx(clauses=20, cards=10, evidence=30)
        # make content large enough to force reduction
        for c in ctx["clauses"]:
            c["content"] = "长文本内容" * 200
        text = render_context(ctx, budget=8000)
        assert estimate_tokens(text) <= 8000

    def test_empty_context_renders_empty(self):
        ctx = {"clauses": [], "cards": [], "evidence": [], "revisions": [],
               "law_hits": [], "citations": [], "law_empty": True, "review": {}}
        assert render_context(ctx) == ""


class TestReviewBlock:
    def test_evidence_capped_per_clause(self):
        cards = [{"clause_id": "C1", "dimension": "合规性", "score": 0.8,
                  "level": "高", "suggestion": "改", "risk_type": ""}]
        evidence = [
            {"clause_id": "C1", "source_id": f"s{i}", "source_collection": "kb",
             "quote": f"quote{i}", "confidence": 0.9 - i * 0.1}
            for i in range(5)
        ]
        block = _review_block(cards, evidence, {}, 250, 150, 0)
        # only top-2 evidence lines should appear
        assert "quote0" in block and "quote1" in block
        assert "quote2" not in block

    def test_min_rank_filters_low_level_cards(self):
        cards = [
            {"clause_id": "C1", "dimension": "d", "score": 0.9, "level": "高",
             "suggestion": "keep", "risk_type": ""},
            {"clause_id": "C2", "dimension": "d", "score": 0.1, "level": "无",
             "suggestion": "drop-me", "risk_type": ""},
        ]
        block = _review_block(cards, [], {}, 250, 150, min_rank=1)
        assert "keep" in block
        assert "drop-me" not in block


class TestTrunc:
    def test_short_text_unchanged(self):
        assert _trunc("abc", 10) == "abc"

    def test_long_text_truncated(self):
        out = _trunc("a" * 100, 10)
        assert out.startswith("a" * 10) and out.endswith("…[略]")

    def test_none_safe(self):
        assert _trunc(None, 10) == ""


# ── Query rewrite fixtures ──────────────────────────────────

def _clauses():
    return [
        {"clause_id": "C4", "seq_no": 4, "title": "违约责任", "content": ""},
        {"clause_id": "C5", "seq_no": 5, "title": "保密条款", "content": ""},
        {"clause_id": "C14", "seq_no": 14, "title": "争议解决", "content": ""},
    ]


def _cards():
    return [
        {"clause_id": "C4", "dimension": "legal", "score": 0.9, "level": "高",
         "suggestion": "", "risk_type": "违约金过高"},
        {"clause_id": "C4", "dimension": "financial", "score": 0.85, "level": "高",
         "suggestion": "", "risk_type": "定金与违约金重复惩罚"},
        {"clause_id": "C5", "dimension": "compliance", "score": 0.5, "level": "中",
         "suggestion": "", "risk_type": "保密义务过宽"},
    ]


class TestReferencedClauseIds:
    def test_chinese_numeral_reference(self):
        ids = _referenced_clause_ids("第四条违约责任有什么高风险", _clauses())
        assert "C4" in ids

    def test_arabic_numeral_reference(self):
        ids = _referenced_clause_ids("第4条是不是有问题", _clauses())
        assert "C4" in ids

    def test_compound_chinese_numeral(self):
        ids = _referenced_clause_ids("第十四条怎么约定仲裁", _clauses())
        assert "C14" in ids

    def test_title_mention(self):
        ids = _referenced_clause_ids("保密条款是不是太宽了", _clauses())
        assert "C5" in ids

    def test_no_reference_returns_empty(self):
        assert _referenced_clause_ids("这份合同整体怎么样", _clauses()) == set()


class TestBuildLawQuery:
    def test_question_kept_first(self):
        q = build_law_query("第四条违约责任有什么高风险", _clauses(), _cards())
        assert q.startswith("第四条违约责任有什么高风险")

    def test_focuses_on_referenced_clause(self):
        q = build_law_query("第四条违约责任有什么高风险", _clauses(), _cards())
        # C4 terms included, C5 (confidentiality) term excluded
        assert "违约金过高" in q
        assert "定金与违约金重复惩罚" in q
        assert "保密义务过宽" not in q

    def test_no_reference_uses_all_cards_by_risk(self):
        q = build_law_query("这份合同有哪些风险", _clauses(), _cards())
        assert "违约金过高" in q
        assert "保密义务过宽" in q

    def test_no_cards_returns_question(self):
        assert build_law_query("有问题吗", _clauses(), []) == "有问题吗"

    def test_dedupes_terms(self):
        cards = [
            {"clause_id": "C4", "level": "高", "risk_type": "违约金过高"},
            {"clause_id": "C4", "level": "高", "risk_type": "违约金过高"},
        ]
        q = build_law_query("第四条", _clauses(), cards)
        assert q.count("违约金过高") == 1

    def test_terms_capped(self):
        cards = [
            {"clause_id": "C4", "level": "高", "risk_type": f"风险{i}"}
            for i in range(8)
        ]
        q = build_law_query("第四条", _clauses(), cards)
        added = [t for t in q.split() if t.startswith("风险")]
        assert len(added) == 4  # MAX_QUERY_TERMS


# ── Meta-question gate ─────────────────────────────────────

class TestIsMetaQuestion:
    @pytest.mark.parametrize("q", [
        "你可以干什么",
        "你能做什么？",
        "您可以帮我做些什么",
        "你会回答哪些问题",
        "你是谁",
        "你是什么助手",
        "介绍一下你自己",
        "你是干什么的",
        "你有什么功能",
        "怎么使用你",
        "你好",
        "您好！",
        "谢谢",
        "好的",
        "再见",
    ])
    def test_meta_questions_detected(self, q):
        assert is_meta_question(q) is True

    @pytest.mark.parametrize("q", [
        "第四条违约责任有什么高风险",
        "违约金比例是不是过高",
        "这份合同有哪些风险",
        "定金和违约金能同时主张吗",
        "保密条款是不是太宽了",
        "对方违约了我可以干什么",          # anchored to 我, not 你/您
        "这份合同能做哪些修改",             # about the contract, not the bot
        "违约金条款有什么用",               # no 你/您 anchor
    ])
    def test_legal_questions_not_gated(self, q):
        assert is_meta_question(q) is False

    def test_long_text_not_gated(self):
        long_q = "你可以干什么" + "请详细说明" * 10
        assert is_meta_question(long_q) is False

    def test_empty_is_meta(self):
        assert is_meta_question("") is True
        assert is_meta_question("   ") is True


class TestBuildQaContextMetaGate:
    def _fake_sources(self):
        return {
            "review": {"id": 1, "user_id": 1, "original_filename": "t.docx",
                       "contract_type": "采购合同", "status": "completed"},
            "clauses": _clauses(),
            "cards": _cards(),
            "evidence": [],
            "revisions": [],
        }

    def test_meta_question_skips_retrieval(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        async def fake_fetch_sources(db, contract_id):
            return self._fake_sources()

        async def must_not_be_called(question):
            raise AssertionError("retrieve_law_hits must not run for meta questions")

        monkeypatch.setattr(cb, "fetch_contract_sources", fake_fetch_sources)
        monkeypatch.setattr(cb, "retrieve_law_hits", must_not_be_called)

        import asyncio
        ctx = asyncio.run(cb.build_qa_context(None, 1, "你可以干什么"))
        assert ctx["law_hits"] == []
        assert ctx["citations"] == []
        assert ctx["law_empty"] is True
        # contract sources are still available for the scope-guard answer
        assert ctx["clauses"] == _clauses()

    def test_legal_question_still_retrieves(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        async def fake_fetch_sources(db, contract_id):
            return self._fake_sources()

        async def fake_retrieve(question, hyde_question=None):
            return _fake_hits(2)

        monkeypatch.setattr(cb, "fetch_contract_sources", fake_fetch_sources)
        monkeypatch.setattr(cb, "retrieve_law_hits", fake_retrieve)

        import asyncio
        ctx = asyncio.run(cb.build_qa_context(None, 1, "违约金比例是不是过高"))
        assert len(ctx["law_hits"]) == 2
        assert len(ctx["citations"]) == 2
        assert ctx["law_empty"] is False


# ── T1: intent classification ─────────────────────────────

class TestClassifyQuestion:
    @pytest.mark.parametrize("q", ["你可以干什么", "你好", "谢谢", "你是谁"])
    def test_meta(self, q):
        assert classify_question(q, _clauses()) == "META"

    @pytest.mark.parametrize("q", [
        "这份合同第四条写的是什么内容？",      # clause ref + content probe
        "第五条是怎么约定的",                  # clause ref + content probe
        "审查发现这份合同有哪些风险？哪个风险最高？",
        "审查结果里有什么问题",
        "对这份合同有什么修改建议？",
        "这份合同整体怎么样",
        "这份合同有哪些风险",                  # review cards answer it; no 分析词
    ])
    def test_clause(self, q):
        assert classify_question(q, _clauses()) == "CLAUSE"

    @pytest.mark.parametrize("q", [
        "离职后竞业限制的经济补偿金每月标准是多少？",
        "劳动争议申请仲裁的时效期间是多久？",
        "公司没给我交社保怎么办",
        "被公司违法辞退怎么申请劳动仲裁",
    ])
    def test_generic_off_domain(self, q):
        assert classify_question(q, _clauses()) == "GENERIC"

    @pytest.mark.parametrize("q", [
        # golden-set LAW / COMPOUND questions must all stay on retrieval
        "当事人一方不履行合同义务或者履行不符合约定，应当承担什么责任？",
        "违约金条款和定金条款能否同时适用？",
        "约定的违约金过分高于造成的损失的，应当如何处理？",
        "在哪些情形下当事人可以法定解除合同？",
        "标的物质量不符合约定的，违约责任如何承担？",
        "因不可抗力不能履行合同的，能否免除违约责任？",
        "对方收了钱一直不发货，我该怎么办？",
        "合同里的违约金太高了，我真的要全额赔吗？",
        "我交了定金后反悔不想买了，定金能要回来吗？",
        "仓库被雷劈了着火，要交的货全烧了，我还要赔吗？",
        "违约金和定金能同时适用吗？如果违约金约定过高该怎么处理？",
        "合同在什么情况下可以解除？解除之后还需要赔偿损失吗？",
        "交了定金的一方违约，定金怎么处理？定金不够弥补损失怎么办？",
        "什么是不可抗力",
    ])
    def test_law(self, q):
        assert classify_question(q, _clauses()) == "LAW"

    def test_clause_reference_with_analysis_stays_law(self):
        # The previously validated flow: clause pointer + risk analysis keeps
        # retrieval (review cards alone should not replace statute grounding).
        assert classify_question("第四条违约责任有什么高风险", _clauses()) == "LAW"

    def test_contract_self_reference_with_analysis_stays_law(self):
        assert classify_question("这份合同的违约金条款有效吗", _clauses()) == "LAW"

    def test_title_reference_with_analysis_stays_law(self):
        assert classify_question("保密条款是不是太宽了", _clauses()) == "LAW"

    def test_service_contract_not_off_domain(self):
        # 劳务合同 is a civil contract — must not match the 劳动合同 pattern.
        assert classify_question("劳务合同违约了怎么办", _clauses()) == "LAW"

    def test_no_clauses_available_still_routes(self):
        assert classify_question("这份合同整体怎么样", []) == "CLAUSE"
        assert classify_question("违约金过高怎么办", []) == "LAW"


class _FakeKB:
    """Stands in for KnowledgeBaseClient in retrieve_law_hits tests."""

    def __init__(self, hits):
        self._hits = hits

    async def hybrid_search(self, question, collection, top_k=10, rerank_top_k=3, threshold=0.30):
        return self._hits


class TestRetrieveLawHitsCutoff:
    def test_sub_threshold_hits_dropped(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        hits = [
            {"id": "a", "article_no": "第五百八十五条", "confidence": 0.72},
            {"id": "b", "article_no": "第五百八十八条", "confidence": 0.29},
            {"id": "c", "article_no": "第五百八十四条", "confidence": 0.04},
        ]
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKB(hits))

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["违约金过高"]))
        assert [h["id"] for h in kept] == ["a"]
        assert all(h["confidence"] >= LAW_RETRIEVE_THRESHOLD for h in kept)

    def test_kb_failure_degrades_to_empty(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        def broken():
            raise RuntimeError("milvus down")

        monkeypatch.setattr(cb, "get_kb_client", broken)

        import asyncio
        assert asyncio.run(retrieve_law_hits(["任意问题"])) == []


class TestBuildQaContextIntentRouting:
    def _fake_sources(self):
        return {
            "review": {"id": 1, "user_id": 1, "original_filename": "t.docx",
                       "contract_type": "采购合同", "status": "completed"},
            "clauses": _clauses(),
            "cards": _cards(),
            "evidence": [],
            "revisions": [],
        }

    def _run(self, monkeypatch, question, fake_hits=None):
        from backend.agents.contract_qa import context_builder as cb

        async def fake_fetch_sources(db, contract_id):
            return self._fake_sources()

        calls = []

        async def fake_retrieve(q, hyde_question=None):
            calls.append(q)
            return fake_hits or []

        monkeypatch.setattr(cb, "fetch_contract_sources", fake_fetch_sources)
        monkeypatch.setattr(cb, "retrieve_law_hits", fake_retrieve)

        import asyncio
        ctx = asyncio.run(cb.build_qa_context(None, 1, question))
        return ctx, calls

    def test_clause_question_skips_retrieval(self, monkeypatch):
        ctx, calls = self._run(monkeypatch, "这份合同第四条写的是什么内容？")
        assert ctx["intent"] == "CLAUSE"
        assert calls == []
        assert ctx["law_hits"] == [] and ctx["citations"] == []
        assert ctx["law_empty"] is True
        # contract context still fully available for the answer
        assert ctx["clauses"] == _clauses() and ctx["cards"] == _cards()

    def test_generic_question_skips_retrieval(self, monkeypatch):
        ctx, calls = self._run(monkeypatch, "劳动争议申请仲裁的时效期间是多久？")
        assert ctx["intent"] == "GENERIC"
        assert calls == []
        assert ctx["law_empty"] is True

    def test_law_question_retrieves(self, monkeypatch):
        ctx, calls = self._run(monkeypatch, "违约金比例是不是过高", fake_hits=_fake_hits(2))
        assert ctx["intent"] == "LAW"
        assert len(calls) == 1
        assert len(ctx["law_hits"]) == 2 and len(ctx["citations"]) == 2
        assert ctx["law_empty"] is False

    def test_review_question_skips_retrieval(self, monkeypatch):
        ctx, calls = self._run(monkeypatch, "审查发现这份合同有哪些风险？哪个风险最高？")
        assert ctx["intent"] == "CLAUSE"
        assert calls == []
        assert ctx["citations"] == []

    def test_compound_question_passes_split_queries(self, monkeypatch):
        ctx, calls = self._run(
            monkeypatch,
            "违约金和定金能同时适用吗？如果违约金约定过高该怎么处理？",
            fake_hits=_fake_hits(1),
        )
        assert ctx["intent"] == "LAW"
        assert len(calls) == 1          # one retrieve_law_hits call...
        assert len(calls[0]) == 2       # ...carrying two enriched sub-queries
        assert calls[0][0].startswith("违约金和定金能同时适用吗")
        assert calls[0][1].startswith("如果违约金约定过高该怎么处理")

    def test_single_intent_question_stays_one_query(self, monkeypatch):
        ctx, calls = self._run(monkeypatch, "违约金比例是不是过高", fake_hits=_fake_hits(1))
        assert len(calls[0]) == 1
        assert calls[0][0].startswith("违约金比例是不是过高")

    def test_law_question_passes_raw_question_for_hyde(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        async def fake_fetch_sources(db, contract_id):
            return self._fake_sources()

        seen = {}

        async def fake_retrieve(q, hyde_question=None):
            seen["hyde_question"] = hyde_question
            return _fake_hits(1)

        monkeypatch.setattr(cb, "fetch_contract_sources", fake_fetch_sources)
        monkeypatch.setattr(cb, "retrieve_law_hits", fake_retrieve)

        import asyncio
        asyncio.run(cb.build_qa_context(
            None, 1, "仓库被雷劈了着火，要交的货全烧了，我还要赔吗？"
        ))
        # The raw colloquial question (not the enriched query) feeds HyDE.
        assert seen["hyde_question"] == "仓库被雷劈了着火，要交的货全烧了，我还要赔吗？"


# ── T2: multi-query split + parallel merge ─────────────────

class TestSplitSubQuestions:
    def test_single_intent_unchanged(self):
        assert split_sub_queries("违约金比例是不是过高") == ["违约金比例是不是过高"]

    def test_single_intent_trailing_punct_kept_verbatim(self):
        # Regression guard (eval C1): stripping the trailing '？' from a
        # single-intent question flipped a knife-edge rerank (第580条@0.4515 vs
        # 第593条@0.4477). A question that does not actually split must come
        # back verbatim so it keeps exactly the pre-T2 retrieval behavior.
        assert split_sub_queries("对方收了钱一直不发货，我该怎么办？") == [
            "对方收了钱一直不发货，我该怎么办？"
        ]

    def test_split_on_fullwidth_question_marks(self):
        out = split_sub_queries("违约金和定金能同时适用吗？如果违约金约定过高该怎么处理？")
        assert out == ["违约金和定金能同时适用吗", "如果违约金约定过高该怎么处理"]

    def test_split_on_semicolon(self):
        out = split_sub_queries("交的定金能退吗；违约金怎么计算")
        assert out == ["交的定金能退吗", "违约金怎么计算"]

    def test_split_on_halfwidth_mark(self):
        out = split_sub_queries("交的定金能退吗?违约金怎么计算")
        assert len(out) == 2

    def test_split_on_connective(self):
        out = split_sub_queries("定金能要回来吗，另外违约金怎么算")
        assert out == ["定金能要回来吗", "违约金怎么算"]

    def test_short_fragment_dropped(self):
        out = split_sub_queries("违约金怎么算？还有定金")
        assert out == ["违约金怎么算"]

    def test_capped_at_three(self):
        out = split_sub_queries("第一个问题内容甲？第二个问题内容乙？第三个问题内容丙？第四个问题内容丁")
        assert len(out) == 3

    def test_duplicates_removed(self):
        assert split_sub_queries("违约金怎么算？违约金怎么算") == ["违约金怎么算"]

    def test_unsplittable_short_question_kept_whole(self):
        # 5 chars < MIN_SUB_QUERY_CHARS, but no split point → keep the original
        assert split_sub_queries("定金怎么办") == ["定金怎么办"]

    def test_empty(self):
        assert split_sub_queries("") == []
        assert split_sub_queries("   ") == []


class _FakeKBByQuery:
    """Per-query canned hits; an Exception value makes that path raise."""

    def __init__(self, by_query):
        self.by_query = by_query

    async def hybrid_search(self, question, collection, top_k=10, rerank_top_k=3, threshold=0.30):
        result = self.by_query[question]
        if isinstance(result, Exception):
            raise result
        return result


class TestRetrieveLawHitsMerge:
    def test_merge_dedupes_and_keeps_max_confidence(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        by_query = {
            "子问一": [{"id": "a", "confidence": 0.60}, {"id": "b", "confidence": 0.50}],
            "子问二": [{"id": "a", "confidence": 0.80}, {"id": "c", "confidence": 0.40}],
        }
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["子问一", "子问二"]))
        assert [h["id"] for h in kept] == ["a", "b", "c"]
        assert kept[0]["confidence"] == 0.80  # max wins, not path-1's 0.60

    def test_capped_at_max_law_hits(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        by_query = {
            f"子问{i}": [{"id": f"id_{i}_{j}", "confidence": 0.9 - j * 0.05} for j in range(3)]
            for i in range(3)
        }
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["子问0", "子问1", "子问2"]))
        assert len(kept) == 3
        assert kept[0]["confidence"] == 0.9

    def test_single_path_failure_degrades_only_itself(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        by_query = {
            "好路查询": [{"id": "a", "confidence": 0.7}],
            "坏路查询": RuntimeError("milvus timeout"),
        }
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["好路查询", "坏路查询"]))
        assert [h["id"] for h in kept] == ["a"]

    def test_cutoff_before_cap_backfills(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        # Cutting before capping lets an above-threshold hit from another path
        # take the slot a sub-threshold hit would have wasted.
        by_query = {
            "子问一": [
                {"id": "a", "confidence": 0.80},
                {"id": "b", "confidence": 0.20},
                {"id": "c", "confidence": 0.10},
            ],
            "子问二": [{"id": "d", "confidence": 0.45}],
        }
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["子问一", "子问二"]))
        assert [h["id"] for h in kept] == ["a", "d"]

    def test_round_robin_prevents_one_subquery_starving_another(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        # Regression guard (eval M2): "合同在什么情况下可以解除？解除之后还需要
        # 赔偿损失吗？" — sub-query 1 returned 563/562/565 and its 3rd-place
        # 565@0.84 barely beat sub-query 2's best 566@0.83, so a global
        # confidence sort filled all 3 slots from sub-query 1 and recall fell
        # 1.00 → 0.50. Every sub-question must get representation.
        by_query = {
            "合同在什么情况下可以解除": [
                {"id": "n563", "confidence": 0.98},
                {"id": "n562", "confidence": 0.91},
                {"id": "n565", "confidence": 0.84},
            ],
            "解除之后还需要赔偿损失吗": [
                {"id": "n566", "confidence": 0.83},
                {"id": "n584", "confidence": 0.75},
            ],
        }
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))

        import asyncio
        kept = asyncio.run(retrieve_law_hits(list(by_query)))
        assert [h["id"] for h in kept] == ["n563", "n562", "n566"]
        assert kept[0]["confidence"] == 0.98  # confidence-descending order

    def test_empty_queries_return_empty(self):
        import asyncio
        assert asyncio.run(retrieve_law_hits([])) == []
        assert asyncio.run(retrieve_law_hits(["", "   "])) == []


# ── T3: HyDE low-score retry ───────────────────────────────────

class _FakeQaLlm:
    """Stands in for the ChatOpenAI instance returned by _get_qa_llm."""

    def __init__(self, content="假设的民法典条文内容", delay=0.0, fail=False):
        self.content = content
        self.delay = delay
        self.fail = fail
        self.calls = []

    async def ainvoke(self, messages):
        import asyncio

        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("llm down")

        class _Resp:
            pass

        resp = _Resp()
        resp.content = self.content
        return resp


class TestRetrieveLawHitsHyDE:
    def test_hyde_triggers_below_threshold_and_merges_by_confidence(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        by_query = {
            "口语问题查询": [
                {"id": "a", "confidence": 0.50},
                {"id": "b", "confidence": 0.40},
            ],
            "假设条文": [
                {"id": "c", "confidence": 0.95},
                {"id": "a", "confidence": 0.92},  # duplicate → max wins
            ],
        }
        fake = _FakeQaLlm(content="假设条文")
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))
        monkeypatch.setattr(cb, "_get_qa_llm", lambda: fake)

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["口语问题查询"], hyde_question="口语问题"))
        assert len(fake.calls) == 1
        # Global confidence merge: hypothesis hits compete on score.
        assert [h["id"] for h in kept] == ["c", "a", "b"]
        assert kept[0]["confidence"] == 0.95
        assert kept[1]["confidence"] == 0.92  # max of 0.50 / 0.92

    def test_hyde_not_triggered_when_top_above_threshold(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        by_query = {
            "强命中查询": [{"id": "a", "confidence": 0.85}],
            "假设条文": [{"id": "x", "confidence": 0.99}],
        }
        fake = _FakeQaLlm(content="假设条文")
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))
        monkeypatch.setattr(cb, "_get_qa_llm", lambda: fake)

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["强命中查询"], hyde_question="问题"))
        assert fake.calls == []  # top 0.85 ≥ HYDE_RETRY_THRESHOLD → no LLM call
        assert [h["id"] for h in kept] == ["a"]

    def test_hyde_disabled_keeps_first_round(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        by_query = {"弱命中查询": [{"id": "a", "confidence": 0.50}]}
        fake = _FakeQaLlm(content="假设条文")
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))
        monkeypatch.setattr(cb, "_get_qa_llm", lambda: fake)
        monkeypatch.setattr(cb, "HYDE_ENABLED", False)

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["弱命中查询"], hyde_question="问题"))
        assert fake.calls == []
        assert [h["id"] for h in kept] == ["a"]

    def test_llm_failure_degrades_to_first_round(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        by_query = {"弱命中查询": [{"id": "a", "confidence": 0.50}]}
        fake = _FakeQaLlm(fail=True)
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))
        monkeypatch.setattr(cb, "_get_qa_llm", lambda: fake)

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["弱命中查询"], hyde_question="问题"))
        assert [h["id"] for h in kept] == ["a"]

    def test_empty_hypothesis_skips_second_search(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        by_query = {"弱命中查询": [{"id": "a", "confidence": 0.50}]}
        fake = _FakeQaLlm(content="   ")
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))
        monkeypatch.setattr(cb, "_get_qa_llm", lambda: fake)

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["弱命中查询"], hyde_question="问题"))
        assert len(fake.calls) == 1
        assert [h["id"] for h in kept] == ["a"]

    def test_hypothesis_search_failure_degrades_to_first_round(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        # No entry for the hypothesis text → KeyError → path degrades to [].
        by_query = {"弱命中查询": [{"id": "a", "confidence": 0.50}]}
        fake = _FakeQaLlm(content="假设条文")
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))
        monkeypatch.setattr(cb, "_get_qa_llm", lambda: fake)

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["弱命中查询"], hyde_question="问题"))
        assert [h["id"] for h in kept] == ["a"]

    def test_llm_timeout_degrades_to_first_round(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        by_query = {"弱命中查询": [{"id": "a", "confidence": 0.50}]}
        fake = _FakeQaLlm(content="假设条文", delay=0.2)
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))
        monkeypatch.setattr(cb, "_get_qa_llm", lambda: fake)
        monkeypatch.setattr(cb, "HYDE_TIMEOUT_SECONDS", 0.01)

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["弱命中查询"], hyde_question="问题"))
        assert [h["id"] for h in kept] == ["a"]

    def test_no_hyde_question_keeps_t2_behavior(self, monkeypatch):
        from backend.agents.contract_qa import context_builder as cb

        by_query = {"弱命中查询": [{"id": "a", "confidence": 0.50}]}
        fake = _FakeQaLlm(content="假设条文")
        monkeypatch.setattr(cb, "get_kb_client", lambda: _FakeKBByQuery(by_query))
        monkeypatch.setattr(cb, "_get_qa_llm", lambda: fake)

        import asyncio
        kept = asyncio.run(retrieve_law_hits(["弱命中查询"]))
        assert fake.calls == []  # existing callers (no kwarg) never touch HyDE
        assert [h["id"] for h in kept] == ["a"]
