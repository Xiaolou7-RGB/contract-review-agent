"""
Tests for revision_writer — diff generation, revision logic, degradation.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from backend.agents.contract_review.revision_writer import generate_diff_html


class TestDiffHtml:
    def test_identical_text_no_change(self):
        text = "甲方应支付货款人民币壹佰万元整。"
        result = generate_diff_html(text, text)
        assert "ffcdd2" not in result  # no deletion red
        assert "c8e6c9" not in result  # no insertion green

    def test_deletion_shown_in_red(self):
        before = "违约金为合同总额的50%。"
        after = "违约金为合同总额的30%。"
        result = generate_diff_html(before, after)
        assert "ffcdd2" in result  # deletion in red
        assert "c8e6c9" in result  # insertion in green

    def test_addition_shown_in_green(self):
        before = "甲方有权解除合同。"
        after = "甲方有权在书面通知乙方后30日解除合同。"
        result = generate_diff_html(before, after)
        assert "c8e6c9" in result  # new text in green

    def test_html_escapes_special_chars(self):
        before = "价格 < 市场价"
        after = "价格 > 市场价"
        result = generate_diff_html(before, after)
        assert "&lt;" in result
        assert "&gt;" in result
        # Should NOT contain raw < or > outside of span tags
        text_outside_tags = result.replace('<span style="background:#c8e6c9">&gt;</span>', '')
        text_outside_tags = text_outside_tags.replace('<span style="background:#ffcdd2;text-decoration:line-through">&lt;</span>', '')
        assert "<" not in text_outside_tags.replace("<span", "").replace("</span>", "").replace("<br>", "").replace("<del>", "").replace("</del>", "").replace("<ins>", "").replace("</ins>", "")

    def test_handles_empty_strings(self):
        result = generate_diff_html("", "新增内容")
        assert "新增内容" in result
