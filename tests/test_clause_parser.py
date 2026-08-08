"""
Tests for clause_parser — contract type identification and clause splitting.
"""
from __future__ import annotations

import pytest

from backend.agents.contract_review.clause_parser import (
    identify_contract_type,
    make_clause_id,
    _regex_split_clauses,
)

# ── Sample contract texts ──────────────────────────────────

SALE_CONTRACT = """
采购合同

第一条 标的物
甲方向乙方采购电子元器件一批，具体规格见附件一。

第二条 价格与付款
合同总价款为人民币壹佰万元整（¥1,000,000.00）。
甲方应于货物验收合格后30日内支付全部货款。

第三条 交付与验收
乙方应于合同生效后60日内完成交付。
甲方应在收货后15个工作日内完成验收。

第四条 违约责任
任何一方违反本合同约定，应向对方支付违约金。

第五条 争议解决
因本合同产生的争议，双方应友好协商解决。
协商不成的，提交北京仲裁委员会仲裁。
"""

SERVICE_CONTRACT = """
技术服务合同

第一条 服务内容
受托方为客户提供软件系统维护与技术支持服务。

第二条 服务期限
本合同服务期限为三年，自签订之日起计算。

第三条 服务费用
年度服务费为人民币伍拾万元整。

第四条 保密义务
受托方应对客户的技术资料和商业秘密予以保密。

第五条 知识产权
服务过程中产生的技术成果归客户所有。
"""

LABOR_CONTRACT = """
劳动合同

第一条 合同期限
本合同为有固定期限劳动合同，期限三年。

第二条 工作内容
乙方担任软件工程师岗位，负责程序开发工作。

第三条 劳动报酬
甲方每月支付乙方工资人民币贰万元整。

第四条 竞业限制
乙方离职后两年内不得从事与甲方有竞争关系的工作。

第五条 社会保险
甲方依法为乙方缴纳社会保险和住房公积金。
"""

NDA_CONTRACT = """
保密协议

第一条 保密信息定义
本协议所称保密信息是指一方（披露方）向另一方（接收方）披露的
任何非公开的商业、技术或财务信息。

第二条 保密义务
接收方承诺对保密信息予以严格保密，未经披露方书面同意，
不得向任何第三方披露。

第三条 知识产权
本协议不授予接收方任何知识产权许可。

第四条 违约责任
接收方违反保密义务的，应赔偿披露方因此遭受的全部损失。
"""

LOAN_CONTRACT = """
借款合同

第一条 借款金额
甲方向乙方借款人民币伍拾万元整。

第二条 借款期限
借款期限为12个月，自到账之日起计算。

第三条 利息
年利率为5%，按季付息。

第四条 还款方式
到期一次性还本。

第五条 担保
丙方为本次借款提供连带责任保证。
"""


class TestIdentifyContractType:
    def test_sale_contract(self):
        assert identify_contract_type(SALE_CONTRACT) == "买卖"

    def test_service_contract(self):
        assert identify_contract_type(SERVICE_CONTRACT) == "服务"

    def test_labor_contract(self):
        assert identify_contract_type(LABOR_CONTRACT) == "劳动"

    def test_nda_contract(self):
        assert identify_contract_type(NDA_CONTRACT) == "保密"

    def test_loan_contract(self):
        assert identify_contract_type(LOAN_CONTRACT) == "借款"

    def test_empty_text_returns_other(self):
        assert identify_contract_type("") == "其他"

    def test_unknown_text_returns_other(self):
        assert identify_contract_type("这是一份关于友好合作的备忘录。双方本着平等互利原则。") == "其他"


class TestMakeClauseId:
    def test_stable_across_calls(self):
        id1 = make_clause_id("甲方应支付乙方货款人民币壹佰万元整。", 1)
        id2 = make_clause_id("甲方应支付乙方货款人民币壹佰万元整。", 1)
        assert id1 == id2

    def test_different_page_gives_different_id(self):
        id1 = make_clause_id("付款条款内容", 1)
        id2 = make_clause_id("付款条款内容", 2)
        assert id1 != id2

    def test_different_content_gives_different_id(self):
        id1 = make_clause_id("违约责任条款", 1)
        id2 = make_clause_id("保密义务条款", 1)
        assert id1 != id2

    def test_returns_16_char_hex(self):
        cid = make_clause_id("测试内容", 1)
        assert len(cid) == 16
        int(cid, 16)  # should be valid hex


class TestRegexSplitClauses:
    def test_sale_contract(self):
        clauses = _regex_split_clauses(SALE_CONTRACT)
        assert len(clauses) >= 3
        cids = {c["clause_id"] for c in clauses}
        assert len(cids) == len(clauses), "all clause_ids should be unique"

    def test_nda_contract(self):
        clauses = _regex_split_clauses(NDA_CONTRACT)
        assert len(clauses) >= 2

    def test_each_clause_has_required_fields(self):
        clauses = _regex_split_clauses(SALE_CONTRACT)
        required = {"clause_id", "seq_no", "type", "title", "content", "page", "char_start", "char_end", "span"}
        for c in clauses:
            assert required.issubset(c.keys()), f"missing fields: {required - set(c.keys())}"

    def test_seq_no_monotonic(self):
        clauses = _regex_split_clauses(SALE_CONTRACT)
        seqs = [c["seq_no"] for c in clauses]
        assert seqs == list(range(1, len(clauses) + 1))

    def test_plain_text_fallback(self):
        """Text with no clause markers should fall back to newline split."""
        text = "段落一：本协议自签署之日起生效。\n\n段落二：双方应友好协商。\n\n段落三：未尽事宜另行签订补充协议。"
        clauses = _regex_split_clauses(text)
        assert len(clauses) >= 1
        for c in clauses:
            assert len(c["content"]) >= 1
