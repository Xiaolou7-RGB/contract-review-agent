"""
Tests for QA 北大法宝判例增强（二期）：WEAK 档补真实判例的渲染与注入。

Pure functions only — no network, no DB.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.agents.contract_qa.context_builder import _case_ref_block, render_context


def _case(**overrides) -> dict:
    c = {
        "title": "某公司买卖合同纠纷案",
        "case_no": "(2023)最高法民申123号",
        "court": "最高人民法院",
        "date": "2023-06-30",
        "case_gist": "违约金过高的，人民法院可以综合实际损失、合同履行情况等因素予以调整。",
    }
    c.update(overrides)
    return c


def _base_ctx() -> dict:
    return {
        "review": {"original_filename": "t.docx", "contract_type": "采购合同"},
        "clauses": [], "cards": [], "evidence": [], "revisions": [],
        "law_hits": [], "citations": [], "law_empty": True,
        "confidence_tier": "NA", "case_refs": [],
    }


class TestCaseRefBlock:
    def test_renders_all_fields_with_gist(self):
        block = _case_ref_block([_case()])
        assert "某公司买卖合同纠纷案" in block
        assert "(2023)最高法民申123号" in block
        assert "最高人民法院" in block
        assert "裁判要旨" in block
        assert "违约金过高的" in block

    def test_omits_gist_when_empty(self):
        block = _case_ref_block([_case(case_gist="")])
        assert "裁判要旨" not in block
        assert "某公司买卖合同纠纷案" in block

    def test_empty_list_returns_empty(self):
        assert _case_ref_block([]) == ""


class TestRenderContextInjectsCaseRefs:
    def test_injects_case_section(self):
        ctx = _base_ctx()
        ctx["case_refs"] = [_case()]
        text = render_context(ctx)
        assert "参考判例" in text
        assert "(2023)最高法民申123号" in text

    def test_no_case_section_when_absent(self):
        ctx = _base_ctx()
        text = render_context(ctx)
        assert "参考判例" not in text
