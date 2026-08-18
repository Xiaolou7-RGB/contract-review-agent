"""
Rule engine — deterministic compliance checks before LLM review.
Layer 1 of the three-layer architecture: rule engine → agent scheduling → LLM review.

Each rule is a pure function: (clauses, ...) → RuleFinding | list[RuleFinding] | None.
Rules are composable, independently testable, and registered per contract type.
"""
from __future__ import annotations

import re
from typing import Any, Callable

import logging

logger = logging.getLogger(__name__)


# ── Finding model ───────────────────────────────────────────

class RuleFinding:
    """Deterministic finding from the rule engine."""

    def __init__(
        self,
        rule_id: str,
        category: str,
        level: str,
        description: str,
        related_clause_ids: list[str] | None = None,
        suggestion: str = "",
    ):
        self.rule_id = rule_id
        self.category = category  # missing_clause / invalid_term / penalty / privacy / labor / integrity
        self.level = level  # 高 / 中 / 低
        self.description = description
        self.related_clause_ids = related_clause_ids or []
        self.suggestion = suggestion

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "level": self.level,
            "description": self.description,
            "related_clause_ids": self.related_clause_ids,
            "suggestion": self.suggestion,
            "source": "rule_engine",
        }


# ── Category A: Missing Essential Clauses ────────────────────

def _check_essential_clause(
    clauses: list[dict],
    keywords: list[str],
    rule_id: str,
    description: str,
    suggestion: str = "",
    level: str = "中",
) -> RuleFinding | None:
    """Generic: check if any clause contains at least one keyword."""
    for c in clauses:
        text = (c.get("content", "") + " " + c.get("title", "")).lower()
        if any(kw in text for kw in keywords):
            return None
    return RuleFinding(rule_id, "missing_clause", level, description, suggestion=suggestion)


def check_dispute_resolution(clauses: list[dict]) -> RuleFinding | None:
    """争议解决条款：必须约定仲裁或诉讼管辖"""
    return _check_essential_clause(
        clauses,
        ["争议", "仲裁", "诉讼", "管辖", "法院", "纠纷解决"],
        "R001",
        "合同未约定争议解决方式，发生纠纷时无明确管辖依据",
        "建议增加争议解决条款，明确约定仲裁机构或有管辖权的人民法院",
        "高",
    )


def check_termination_clause(clauses: list[dict]) -> RuleFinding | None:
    """合同的解除条款：合同必须约定解除条件"""
    return _check_essential_clause(
        clauses,
        ["解除", "终止", "提前终止", "合同解除"],
        "R002",
        "合同未约定解除/终止条件",
        "建议增加合同解除条款，明确双方解除权及解除后果",
        "中",
    )


def check_liability_clause(clauses: list[dict]) -> RuleFinding | None:
    """违约责任条款"""
    return _check_essential_clause(
        clauses,
        ["违约", "违约责任", "赔偿", "损失"],
        "R003",
        "合同未约定违约责任条款",
        "建议增加违约责任条款，明确违约情形及责任承担方式",
        "高",
    )


def check_force_majeure(clauses: list[dict]) -> RuleFinding | None:
    """不可抗力条款"""
    return _check_essential_clause(
        clauses,
        ["不可抗力", "force majeure", "意外事件"],
        "R004",
        "合同未约定不可抗力条款",
        "建议增加不可抗力条款，明确不可抗力的定义、通知义务及后果",
        "低",
    )


def check_confidentiality(clauses: list[dict]) -> RuleFinding | None:
    """保密条款（非保密合同类型的检查）"""
    return _check_essential_clause(
        clauses,
        ["保密", "商业秘密", "保密义务", "confidential"],
        "R005",
        "合同未约定保密条款",
        "建议增加保密条款，明确保密信息范围、保密期限及违约责任",
        "低",
    )


def check_governing_law(clauses: list[dict]) -> RuleFinding | None:
    """适用法律条款"""
    return _check_essential_clause(
        clauses,
        ["适用法律", "管辖法律", "法律适用", "governing law"],
        "R006",
        "合同未明确约定适用法律",
        "建议增加法律适用条款，明确合同适用的法律及法规",
        "中",
    )


def check_contract_term(clauses: list[dict]) -> RuleFinding | None:
    """合同期限条款"""
    return _check_essential_clause(
        clauses,
        ["期限", "有效期", "合同期限", "生效", "到期"],
        "R007",
        "合同未明确约定期限/有效期",
        "建议明确约定合同生效日期、有效期及续期条件",
        "中",
    )


def check_payment_terms(clauses: list[dict]) -> RuleFinding | None:
    """付款条款（适用于买卖、服务类合同）"""
    return _check_essential_clause(
        clauses,
        ["付款", "支付", "价款", "费用", "报酬", "价格", "金额"],
        "R008",
        "合同未约定付款/价格条款",
        "建议明确约定价款金额、支付方式、支付时间及发票要求",
        "高",
    )


# ── Category B: Invalid / Unfair Terms ──────────────────────

def _check_blacklist(
    clauses: list[dict],
    patterns: list[str],
    rule_id: str,
    description_fn: Callable[[dict], str],
    level: str = "高",
) -> list[RuleFinding]:
    """Generic: check clauses against a blacklist of regex patterns."""
    findings = []
    for c in clauses:
        text = c.get("content", "")
        for pat in patterns:
            if re.search(pat, text):
                findings.append(
                    RuleFinding(
                        rule_id,
                        "invalid_term",
                        level,
                        description_fn(c),
                        related_clause_ids=[c.get("clause_id", "")],
                        suggestion="该条款可能因违反强制性规定而无效，建议删除或修改",
                    )
                )
                break  # one finding per clause per rule
    return findings


def check_final_interpretation(clauses: list[dict]) -> list[RuleFinding]:
    """「最终解释权归XX所有」属于无效格式条款"""
    return _check_blacklist(
        clauses,
        [r"最终解释权.*?归", r"解释权.*?所有"],
        "R101",
        lambda c: f"条款包含「最终解释权归{c.get('title', 'XX')}所有」，属于排除对方主要权利的无效格式条款",
    )


def check_exempt_personal_injury(clauses: list[dict]) -> list[RuleFinding]:
    """免除人身伤害责任的条款无效"""
    return _check_blacklist(
        clauses,
        [r"人身[伤损].*?[不免承][负责担]", r"[不免承][负责担].*?人身[伤损]"],
        "R102",
        lambda c: "条款试图免除人身伤害责任，违反《民法典》第506条，属无效条款",
    )


def check_unilateral_change(clauses: list[dict]) -> list[RuleFinding]:
    """单方任意变更/解除权"""
    return _check_blacklist(
        clauses,
        [r"甲方.*?有权.*?(?:随时|任意|单方).*?(?:变更|修改|解除|终止)"],
        "R103",
        lambda c: "条款赋予甲方单方任意变更/解除权，可能构成显失公平",
        "中",
    )


def check_unlimited_liability(clauses: list[dict]) -> list[RuleFinding]:
    """无限责任 / 一切损失"""
    return _check_blacklist(
        clauses,
        [r"(?:一切|全部|所有).*?损失.*?(?:赔偿|承担)", r"无上限.*?(?:赔偿|责任)"],
        "R104",
        lambda c: "条款涉及无限责任或无上限赔偿，违反《民法典》第585条违约金合理性原则",
    )


def check_wavier_of_rights(clauses: list[dict]) -> list[RuleFinding]:
    """放弃核心权利：放弃诉权、放弃抗辩权"""
    return _check_blacklist(
        clauses,
        [r"放弃.*?(?:诉讼|抗辩|异议|追索|索赔).*?权"],
        "R105",
        lambda c: "条款要求放弃法定权利（诉权/抗辩权），可能因违反法律强制性规定而无效",
    )


# ── Category C: Penalty Clauses ─────────────────────────────

def check_penalty_cap(clauses: list[dict]) -> list[RuleFinding]:
    """违约金超过法定上限（民法典585条 + 合同法司法解释二29条）"""
    findings = []
    pct_pattern = re.compile(r"(?:违约金|罚[金款]).*?(\d+)\s*%")
    fixed_pattern = re.compile(r"(?:违约金|罚[金款]).*?([\d.]+)\s*(?:万|元)")

    for c in clauses:
        text = c.get("content", "")
        # 百分比违约金
        for m in pct_pattern.finditer(text):
            pct = int(m.group(1))
            if pct > 30:
                findings.append(
                    RuleFinding(
                        "R201",
                        "penalty",
                        "中",
                        f"违约金比例{pct}%，超过《民法典》第585条及司法解释建议的30%上限",
                        related_clause_ids=[c.get("clause_id", "")],
                        suggestion=f"建议将违约金比例调至实际损失的20%-30%",
                    )
                )

    return findings


def check_double_penalty(clauses: list[dict]) -> list[RuleFinding]:
    """定金+违约金并存条款"""
    findings = []
    for c in clauses:
        text = c.get("content", "")
        if "定金" in text and "违约金" in text:
            findings.append(
                RuleFinding(
                    "R202",
                    "penalty",
                    "中",
                    f"条款同时约定了定金和违约金，根据《民法典》第588条，二者不能同时适用",
                    related_clause_ids=[c.get("clause_id", "")],
                    suggestion="建议明确约定：守约方可以选择适用定金罚则或违约金条款，但不可同时主张",
                )
            )
    return findings


def check_excessive_liquidated_damages(clauses: list[dict]) -> list[RuleFinding]:
    """按日累加违约金无上限"""
    findings = []
    for c in clauses:
        text = c.get("content", "")
        if re.search(r"(?:每|按).*?[日天].*?(\d+)\s*%", text) or re.search(
            r"按[日天].*?累[积加]", text
        ):
            findings.append(
                RuleFinding(
                    "R203",
                    "penalty",
                    "高",
                    "条款约定按日累加违约金且无上限，可能导致畸高违约金",
                    related_clause_ids=[c.get("clause_id", "")],
                    suggestion="建议设置违约金总额上限（如不超过合同总金额的20%）",
                )
            )
    return findings


# ── Category D: Personal Information / Privacy ──────────────

def check_personal_data_handling(clauses: list[dict]) -> list[RuleFinding]:
    """个人信息处理合规"""
    findings = []
    for c in clauses:
        text = c.get("content", "")
        if re.search(r"(?:个人(?:信息|数据))|(?:身份证|手机号|地址)", text):
            # 检查是否有删除/销毁条款
            if not re.search(r"(?:删除|销毁|清除|移除).*?(?:个人|数据|信息)", text):
                findings.append(
                    RuleFinding(
                        "R401",
                        "privacy",
                        "中",
                        "涉及个人信息处理但未约定数据删除/销毁条款，不符合《个人信息保护法》第47条",
                        related_clause_ids=[c.get("clause_id", "")],
                        suggestion="建议增加数据删除条款：合同终止后X日内删除/销毁所持有的个人信息",
                    )
                )
            # 检查是否有跨境传输说明
            if re.search(r"(?:跨境|境外|国外|海外)", text) and not re.search(
                r"(?:安全评估|标准合同|认证)", text
            ):
                findings.append(
                    RuleFinding(
                        "R402",
                        "privacy",
                        "高",
                        "涉及个人信息跨境传输但未提及安全评估或标准合同，违反《个人信息保护法》第38条",
                        related_clause_ids=[c.get("clause_id", "")],
                        suggestion="建议增加：个人信息跨境传输需通过国家网信部门的安全评估或签订标准合同",
                    )
                )
    return findings


def check_data_minimization(clauses: list[dict]) -> list[RuleFinding]:
    """数据最小化原则"""
    findings = []
    for c in clauses:
        text = c.get("content", "")
        if re.search(r"(?:收集.*?一切|全部.*?个人信息|所有.*?数据)", text):
            findings.append(
                RuleFinding(
                    "R403",
                    "privacy",
                    "中",
                    "条款范围过宽，要求收集'一切/所有'个人信息，违反《个人信息保护法》第6条最小必要原则",
                    related_clause_ids=[c.get("clause_id", "")],
                    suggestion="建议限定信息收集范围，遵循最小必要原则，仅收集与合同履行直接相关的个人信息",
                )
            )
    return findings


# ── Category E: Labor Law (仅劳动类合同) ─────────────────────

def check_probation_period(clauses: list[dict], contract_type: str) -> list[RuleFinding]:
    """试用期上限"""
    if contract_type != "劳动":
        return []
    findings = []
    full_text = "\n".join(c.get("content", "") for c in clauses)
    m = re.search(r"试用期\D*(\d+)\s*个?\s*月", full_text)
    if m and int(m.group(1)) > 6:
        findings.append(
            RuleFinding(
                "R301",
                "labor",
                "高",
                f"试用期{m.group(1)}个月，超过《劳动合同法》第19条规定的6个月上限",
                suggestion="试用期最长不得超过6个月，且合同期限三年以上方可约定6个月试用期",
            )
        )
    return findings


def check_non_compete_compensation(
    clauses: list[dict], contract_type: str
) -> list[RuleFinding]:
    """竞业限制无补偿"""
    if contract_type != "劳动":
        return []
    findings = []
    full_text = "\n".join(c.get("content", "") for c in clauses)
    if re.search(r"竞业[限禁]|竞业.*?义务|不.*?竞[争业]|非竞争", full_text) and not re.search(
        r"补偿|补偿金|经济补偿", full_text
    ):
        findings.append(
            RuleFinding(
                "R303",
                "labor",
                "高",
                "约定了竞业限制义务但未约定经济补偿，违反《劳动合同法》第23条",
                suggestion="需约定竞业限制期间按月支付不低于劳动合同终止前12个月平均工资30%的补偿",
            )
        )
    return findings


def check_social_insurance(clauses: list[dict], contract_type: str) -> list[RuleFinding]:
    """社保是否缴纳"""
    if contract_type != "劳动":
        return []
    findings = []
    full_text = "\n".join(c.get("content", "") for c in clauses)
    if not re.search(r"(?:社会保[险障]|社保|五险)", full_text):
        findings.append(
            RuleFinding(
                "R304",
                "labor",
                "高",
                "劳动合同未约定社会保险缴纳事项，违反《社会保险法》强制性规定",
                suggestion="建议增加：甲方依法为乙方缴纳社会保险（养老、医疗、失业、工伤、生育）",
            )
        )
    return findings


def check_training_bond(clauses: list[dict], contract_type: str) -> list[RuleFinding]:
    """培训服务期违约金超标"""
    if contract_type != "劳动":
        return []
    findings = []
    for c in clauses:
        text = c.get("content", "")
        if re.search(r"(?:培训|进修|学习)", text) and re.search(
            r"(?:违约金|赔偿).*?(\d+)", text
        ):
            findings.append(
                RuleFinding(
                    "R305",
                    "labor",
                    "中",
                    "培训服务期违约金需注意：《劳动合同法》第22条规定违约金不得超过用人单位实际培训费用，且需按服务期比例递减",
                    related_clause_ids=[c.get("clause_id", "")],
                    suggestion="建议核实培训费用金额，违约金不应超过实际培训费，并按未履行服务期比例折算",
                )
            )
    return findings


# ── Category F: Contract Integrity ──────────────────────────

def check_signing_integrity(text: str, _clauses=None) -> list[RuleFinding]:
    """合同完整性：签署日期、签章区域"""
    findings = []
    tail = text[-500:] if len(text) > 500 else text
    if not re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", tail):
        findings.append(
            RuleFinding(
                "R601", "integrity", "低",
                "合同末尾缺少签署日期",
                suggestion="建议在合同末尾添加签署日期",
            )
        )
    if "甲方" not in tail or "乙方" not in tail:
        findings.append(
            RuleFinding(
                "R602", "integrity", "低",
                "合同末尾缺少双方签章区域",
                suggestion="建议在合同末尾添加甲方、乙方的签章栏（单位名称、授权代表、日期）",
            )
        )
    return findings


def check_amount_consistency(text: str, _clauses=None) -> list[RuleFinding]:
    """金额大小写一致性"""
    findings = []
    # 简单检查：找到数字金额后检查是否有对应大写
    money_nums = re.findall(r"(\d[\d,.]*(?:万|元|美元|欧元))", text)
    upper_money = re.findall(r"[壹贰叁肆伍陆柒捌玖拾佰仟万亿圆]+", text)
    if len(money_nums) > 0 and len(upper_money) == 0 and len(money_nums) > 2:
        findings.append(
            RuleFinding(
                "R603", "integrity", "低",
                "合同涉及多笔金额但未见中文大写数字，建议关键金额同时使用大小写",
                suggestion="建议对关键金额（合同总价、违约金、定金等）同时标注小写和中文大写",
            )
        )
    return findings


def check_blank_fields(text: str, _clauses=None) -> list[RuleFinding]:
    """合同空白待填项"""
    findings = []
    blanks = re.findall(r"_{3,}|（）|\(\s*\)|【\s*】", text)
    if len(blanks) > 2:
        findings.append(
            RuleFinding(
                "R604", "integrity", "中",
                f"检测到{len(blanks)}处疑似空白待填项（下划线/空括号），签署前请确保所有内容已填写完成",
                suggestion="请在签署前确认所有空白项已填写完整，避免签署内容不确定的合同",
            )
        )
    return findings


# ── Rule registry ────────────────────────────────────────────

# _all rules apply to every contract type
# Type-specific rules are keyed by contract type

RULE_REGISTRY: dict[str, list[Callable]] = {
    "_all": [
        # Missing essential clauses
        check_dispute_resolution,
        check_termination_clause,
        check_liability_clause,
        check_force_majeure,
        check_confidentiality,
        check_governing_law,
        check_contract_term,
        # Invalid / unfair terms
        check_final_interpretation,
        check_exempt_personal_injury,
        check_unilateral_change,
        check_unlimited_liability,
        check_wavier_of_rights,
        # Penalty
        check_penalty_cap,
        check_double_penalty,
        check_excessive_liquidated_damages,
        # Privacy
        check_personal_data_handling,
        check_data_minimization,
    ],
    # Contract-type-specific rules
    "买卖": [
        check_payment_terms,
    ],
    "服务": [
        check_payment_terms,
    ],
    "劳动": [
        check_probation_period,
        check_non_compete_compensation,
        check_social_insurance,
        check_training_bond,
    ],
    "借款": [
        check_payment_terms,
    ],
    "保密": [],
    "其他": [],
}

# Rules that take (text, clauses) signature instead of (clauses)
_TEXT_BASED_RULES = {
    check_signing_integrity,
    check_amount_consistency,
    check_blank_fields,
}


# ── Node entry point ────────────────────────────────────────

async def rule_check_node(state: dict[str, Any]) -> dict[str, Any]:
    """Run all applicable rules and return findings.

    Called as a LangGraph node between parse and review.
    """
    clauses = state.get("clauses", [])
    contract_type = state.get("contract_type", "其他")
    text = state.get("text", "")

    total_rules = 0
    all_findings: list[dict[str, Any]] = []

    # ── Run base rules (applied to all contract types) ──
    for rule_fn in RULE_REGISTRY.get("_all", []):
        total_rules += 1
        try:
            if rule_fn in _TEXT_BASED_RULES:
                result = rule_fn(text)
            else:
                result = rule_fn(clauses)
        except Exception:
            logger.warning(f"Rule {rule_fn.__name__} failed, skipping", exc_info=True)
            continue

        items = result if isinstance(result, list) else ([result] if result else [])
        all_findings.extend(i.to_dict() for i in items)

    # ── Run type-specific rules ──
    for rule_fn in RULE_REGISTRY.get(contract_type, []):
        total_rules += 1
        try:
            result = rule_fn(clauses, contract_type)
        except Exception:
            logger.warning(f"Rule {rule_fn.__name__} failed, skipping", exc_info=True)
            continue

        items = result if isinstance(result, list) else ([result] if result else [])
        all_findings.extend(i.to_dict() for i in items)

    # ── Integrity checks (text-based) ──
    for rule_fn in [check_signing_integrity, check_amount_consistency, check_blank_fields]:
        total_rules += 1
        try:
            result = rule_fn(text)
            if result:
                items = result if isinstance(result, list) else [result]
                all_findings.extend(i.to_dict() for i in items)
        except Exception:
            logger.warning(f"Rule {rule_fn.__name__} failed, skipping", exc_info=True)

    high = sum(1 for f in all_findings if f["level"] == "高")
    mid = sum(1 for f in all_findings if f["level"] == "中")
    low = sum(1 for f in all_findings if f["level"] == "低")
    logger.info(
        f"Rule engine complete: {len(all_findings)} findings "
        f"(高={high}, 中={mid}, 低={low}) "
        f"from {total_rules} rules, contract_type={contract_type}"
    )

    return {"rule_findings": all_findings}
