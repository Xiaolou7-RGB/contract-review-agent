"""
Tests for the rule engine (backend/agents/contract_review/rule_engine.py).

Covers: rule functions return correct findings, rule registry routing,
and rule_check_node integration.
"""
import pytest
from unittest.mock import AsyncMock

from backend.agents.contract_review.rule_engine import (
    RuleFinding,
    check_dispute_resolution,
    check_penalty_cap,
    check_final_interpretation,
    check_exempt_personal_injury,
    check_unlimited_liability,
    check_probation_period,
    check_signing_integrity,
    check_double_penalty,
    check_excessive_liquidated_damages,
    check_non_compete_compensation,
    check_social_insurance,
    check_payment_terms,
    check_lease_term_limit,
    check_lease_rent,
    check_lease_sublease,
    check_lease_repair,
    check_lease_deposit,
    check_sale_delivery,
    check_sale_inspection,
    check_sale_risk_transfer,
    check_sale_quality,
    check_service_deliverable,
    check_service_ip_ownership,
    check_service_acceptance,
    check_service_warranty,
    rule_check_node,
)


# ── Helper ──────────────────────────────────────────────────

def _make_clause(clause_id="C001", title="", content=""):
    return {"clause_id": clause_id, "title": title, "content": content, "type": "general"}


# ── Category A: Missing Clauses ─────────────────────────────

class TestDisputeResolution:
    def test_missing_returns_finding(self):
        clauses = [_make_clause(content="本合同约定了付款方式")]
        result = check_dispute_resolution(clauses)
        assert result is not None
        assert result.rule_id == "R001"
        assert result.level == "高"

    def test_present_returns_none(self):
        clauses = [_make_clause(content="争议解决：提交北京仲裁委员会仲裁")]
        result = check_dispute_resolution(clauses)
        assert result is None


# ── Category B: Invalid Terms ───────────────────────────────

class TestFinalInterpretation:
    def test_detects_final_interpretation(self):
        clauses = [_make_clause(content="本合同最终解释权归甲方所有")]
        results = check_final_interpretation(clauses)
        assert len(results) == 1
        assert results[0].rule_id == "R101"
        assert "最终解释权" in results[0].description

    def test_no_match_returns_empty(self):
        clauses = [_make_clause(content="甲乙双方应友好协商")]
        results = check_final_interpretation(clauses)
        assert results == []


class TestExemptPersonalInjury:
    def test_detects_exemption(self):
        clauses = [_make_clause(content="甲方对乙方人身损害不承担任何责任")]
        results = check_exempt_personal_injury(clauses)
        assert len(results) >= 1
        assert "R102" in [r.rule_id for r in results]

    def test_normal_clause_ok(self):
        clauses = [_make_clause(content="甲方应保证产品质量安全")]
        results = check_exempt_personal_injury(clauses)
        assert results == []


class TestUnlimitedLiability:
    def test_detects_unlimited(self):
        clauses = [_make_clause(content="乙方须承担一切损失及赔偿，无上限")]
        results = check_unlimited_liability(clauses)
        assert len(results) >= 1
        assert "R104" in [r.rule_id for r in results]

    def test_normal_liability_ok(self):
        clauses = [_make_clause(content="违约金以合同金额的20%为上限")]
        results = check_unlimited_liability(clauses)
        assert results == []


# ── Category C: Penalty ─────────────────────────────────────

class TestPenaltyCap:
    def test_pct_over_limit(self):
        clauses = [_make_clause(content="违约金按合同金额的50%计算")]
        results = check_penalty_cap(clauses)
        assert len(results) >= 1
        assert any("50%" in r.description for r in results)

    def test_pct_within_limit(self):
        clauses = [_make_clause(content="违约金按合同金额的20%计算")]
        results = check_penalty_cap(clauses)
        assert len(results) == 0

    def test_no_penalty_ok(self):
        clauses = [_make_clause(content="双方应按时履行合同义务")]
        results = check_penalty_cap(clauses)
        assert results == []


class TestDoublePenalty:
    def test_both_deposit_and_penalty(self):
        clauses = [_make_clause(content="违约方应双倍返还定金，并支付违约金10万元")]
        results = check_double_penalty(clauses)
        assert len(results) >= 1
        assert results[0].rule_id == "R202"

    def test_only_penalty_ok(self):
        clauses = [_make_clause(content="违约金按合同金额的20%计算")]
        results = check_double_penalty(clauses)
        assert results == []


class TestExcessiveLiquidatedDamages:
    def test_daily_accumulation(self):
        clauses = [_make_clause(content="每延迟一日，支付合同金额1%的违约金")]
        results = check_excessive_liquidated_damages(clauses)
        assert len(results) >= 1
        assert results[0].rule_id == "R203"

    def test_normal_ok(self):
        clauses = [_make_clause(content="违约金10万元整")]
        results = check_excessive_liquidated_damages(clauses)
        assert results == []


# ── Category E: Labor ───────────────────────────────────────

class TestProbationPeriod:
    def test_probation_over_limit(self):
        clauses = [
            _make_clause(content="试用期为12个月"),
        ]
        results = check_probation_period(clauses, "劳动")
        assert any(r.rule_id == "R301" for r in results)

    def test_non_labor_skips(self):
        clauses = [_make_clause(content="试用期为12个月")]
        results = check_probation_period(clauses, "买卖")
        assert results == []

    def test_probation_within_limit(self):
        clauses = [_make_clause(content="试用期为3个月")]
        results = check_probation_period(clauses, "劳动")
        assert not any(r.rule_id == "R301" for r in results)

    def test_non_compete_no_compensation(self):
        clauses = [
            _make_clause(content="乙方离职后两年内不得从事竞争业务"),
        ]
        results = check_non_compete_compensation(clauses, "劳动")
        assert len(results) >= 1
        assert results[0].rule_id == "R303"

    def test_non_compete_with_compensation_ok(self):
        clauses = [
            _make_clause(content="乙方离职后两年内不得从事竞争业务，甲方每月支付补偿金5000元"),
        ]
        results = check_non_compete_compensation(clauses, "劳动")
        assert results == []

    def test_missing_social_insurance(self):
        clauses = [_make_clause(content="月薪8000元，试用期3个月")]
        results = check_social_insurance(clauses, "劳动")
        assert len(results) >= 1
        assert results[0].rule_id == "R304"

    def test_has_social_insurance_ok(self):
        clauses = [_make_clause(content="甲方依法为乙方缴纳社会保险")]
        results = check_social_insurance(clauses, "劳动")
        assert results == []


# ── Category F: Integrity ───────────────────────────────────

class TestSigningIntegrity:
    def test_missing_date(self):
        text = "甲方：某某公司\n乙方：某某个人\n（以下无正文）"
        results = check_signing_integrity(text)
        assert any(r.rule_id == "R601" for r in results)

    def test_has_date_and_parties(self):
        text = "甲方：某某公司\n2024年1月15日\n乙方：某某个人\n2024年1月15日"
        results = check_signing_integrity(text)
        assert not any(r.rule_id == "R601" for r in results)


# ── RuleFinding model ───────────────────────────────────────

class TestRuleFinding:
    def test_to_dict(self):
        f = RuleFinding("R001", "missing_clause", "高",
                        "test description",
                        related_clause_ids=["C001"],
                        suggestion="test suggestion")
        d = f.to_dict()
        assert d["rule_id"] == "R001"
        assert d["category"] == "missing_clause"
        assert d["level"] == "高"
        assert d["description"] == "test description"
        assert d["related_clause_ids"] == ["C001"]
        assert d["suggestion"] == "test suggestion"
        assert d["source"] == "rule_engine"


# ── Rule check node ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_rule_check_node_runs_all_rules():
    state = {
        "clauses": [
            _make_clause("C001", "付款", "买方应支付100万元"),
            _make_clause("C002", "争议", "发生争议由北京仲裁委仲裁"),
        ],
        "contract_type": "买卖",
        "text": "test contract text",
    }
    result = await rule_check_node(state)
    assert "rule_findings" in result
    findings = result["rule_findings"]
    # Should detect at least: no liability clause, no termination clause, no force majeure, etc.
    assert len(findings) > 0
    # All findings should have source='rule_engine'
    assert all(f.get("source") == "rule_engine" for f in findings)


@pytest.mark.asyncio
async def test_rule_check_node_empty_clauses():
    state = {
        "clauses": [],
        "contract_type": "其他",
        "text": "",
    }
    result = await rule_check_node(state)
    assert "rule_findings" in result
    # Even empty text should trigger some integrity/signing findings
    assert isinstance(result["rule_findings"], list)


@pytest.mark.asyncio
async def test_rule_check_node_labor_contract():
    state = {
        "clauses": [
            _make_clause("C001", "试用期", "试用期为12个月"),
            _make_clause("C002", "竞业限制", "离职后2年内不得到竞争企业工作"),
        ],
        "contract_type": "劳动",
        "text": "劳动合同\n甲方：公司\n乙方：员工",
    }
    result = await rule_check_node(state)
    findings = result["rule_findings"]
    # Should detect labor-specific issues
    labor_ids = [f["rule_id"] for f in findings]
    assert "R301" in labor_ids or "R303" in labor_ids or "R304" in labor_ids


# ── Payment terms (signature fix regression) ─────────────────

class TestPaymentTerms:
    def test_accepts_contract_type_arg(self):
        """回归：check_payment_terms 必须能被 rule_check_node 的 (clauses, contract_type) 调用。"""
        clauses = [_make_clause(content="乙方交付货物")]
        result = check_payment_terms(clauses, "买卖")
        assert result is not None
        assert result.rule_id == "R008"


# ── Category G: Lease ───────────────────────────────────────

class TestLeaseRules:
    def test_term_over_20(self):
        clauses = [_make_clause(content="租赁期限为25年")]
        results = check_lease_term_limit(clauses, "租赁")
        assert any(r.rule_id == "R501" for r in results)

    def test_term_within_20_ok(self):
        clauses = [_make_clause(content="租赁期限为10年")]
        results = check_lease_term_limit(clauses, "租赁")
        assert results == []

    def test_term_cap_negative_filter(self):
        clauses = [_make_clause(content="租赁期限不超过25年")]
        results = check_lease_term_limit(clauses, "租赁")
        assert results == []

    def test_missing_rent(self):
        clauses = [_make_clause(content="租赁物为一套房屋")]
        result = check_lease_rent(clauses, "租赁")
        assert result is not None
        assert result.rule_id == "R502"

    def test_sublease_unclear(self):
        clauses = [_make_clause(content="承租人可以转租")]
        results = check_lease_sublease(clauses, "租赁")
        assert any(r.rule_id == "R503" for r in results)

    def test_sublease_with_consent_ok(self):
        clauses = [_make_clause(content="转租需经出租人书面同意")]
        results = check_lease_sublease(clauses, "租赁")
        assert results == []

    def test_missing_repair(self):
        clauses = [_make_clause(content="租金每月2000元")]
        result = check_lease_repair(clauses, "租赁")
        assert result is not None
        assert result.rule_id == "R504"

    def test_deposit_unclear(self):
        clauses = [_make_clause(content="押金3000元")]
        results = check_lease_deposit(clauses, "租赁")
        assert any(r.rule_id == "R505" for r in results)

    def test_non_lease_skips(self):
        clauses = [_make_clause(content="租赁期限为25年")]
        assert check_lease_term_limit(clauses, "买卖") == []


# ── Category H: Sale ────────────────────────────────────────

class TestSaleRules:
    def test_missing_delivery(self):
        clauses = [_make_clause(content="买方支付货款100万元")]
        result = check_sale_delivery(clauses, "买卖")
        assert result is not None
        assert result.rule_id == "R701"

    def test_missing_quality(self):
        clauses = [_make_clause(content="买方支付货款100万元")]
        result = check_sale_quality(clauses, "买卖")
        assert result is not None
        assert result.rule_id == "R704"


# ── Category I: Service ─────────────────────────────────────

class TestServiceRules:
    def test_missing_deliverable(self):
        clauses = [_make_clause(content="乙方提供服务，甲方支付报酬")]
        result = check_service_deliverable(clauses, "服务")
        assert result is not None
        assert result.rule_id == "R801"

    def test_ip_unclear(self):
        clauses = [_make_clause(content="乙方开发软件系统")]
        results = check_service_ip_ownership(clauses, "服务")
        assert any(r.rule_id == "R802" for r in results)

    def test_ip_with_ownership_ok(self):
        clauses = [_make_clause(content="乙方开发的软件知识产权归甲方所有")]
        results = check_service_ip_ownership(clauses, "服务")
        assert results == []

    def test_missing_acceptance(self):
        clauses = [_make_clause(content="乙方提供服务")]
        result = check_service_acceptance(clauses, "服务")
        assert result is not None
        assert result.rule_id == "R803"
