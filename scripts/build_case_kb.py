#!/usr/bin/env python3
"""
build_case_kb.py — 为合同审查的高频争议点生成「裁判规则」并灌入 kb_case。

数据源：LLM 基于法条 + 审判实务提炼的裁判规则（非特定真实判例，已在
content 中明确标注「裁判规则提炼」，不伪造案号）。

用法：python scripts/build_case_kb.py
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 加载 .env.local（LLM API Key 等），必须在 import backend.core.llm 之前
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")

from pydantic import BaseModel, Field
from pymilvus import Collection, MilvusClient, connections

from backend.core.llm import get_structured_llm

MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
BGE_M3_PATH = "D:/contract/models/embedding/bge-m3"
COLLECTION = "kb_case"

# ── 高频争议点清单（对应 rule_engine 规则 + 四维评审风险类型）──
DISPUTE_POINTS: list[dict] = [
    {"name": "格式条款效力", "law": "民法典第496条、第497条", "scene": "提供格式条款一方不合理地免除或减轻其责任、加重对方责任、限制或排除对方主要权利，且未履行提示说明义务"},
    {"name": "违约金过高调减", "law": "民法典第585条第2款", "scene": "约定的违约金过分高于造成的损失，违约方请求人民法院予以适当减少"},
    {"name": "定金与违约金并存", "law": "民法典第588条", "scene": "当事人既约定违约金又约定定金，一方违约时对方如何主张"},
    {"name": "复利条款效力", "law": "民间借贷司法解释第27条", "scene": "借贷双方对前期借款本息结算后将利息计入后期借款本金并重新出具债权凭证"},
    {"name": "民间借贷利率上限", "law": "民间借贷司法解释第25条", "scene": "出借人请求按约定利率支付利息，但约定利率超过合同成立时一年期LPR四倍"},
    {"name": "利息预先扣除", "law": "民法典第670条", "scene": "借款的利息预先在本金中扣除，实际借款数额如何认定"},
    {"name": "合同解除权行使", "law": "民法典第563条", "scene": "当事人一方迟延履行主要债务经催告后仍不履行，或致使不能实现合同目的"},
    {"name": "不可抗力免责", "law": "民法典第180条、第590条", "scene": "因不可抗力不能履行合同，部分或全部免除责任"},
    {"name": "争议解决条款缺失", "law": "民事诉讼法第24条、第35条", "scene": "合同未约定管辖法院或仲裁机构，发生纠纷时如何确定管辖"},
    {"name": "最终解释权条款", "law": "民法典第496条、第498条", "scene": "合同约定由一方保留最终解释权，该条款是否有效"},
    {"name": "单方变更合同条款", "law": "民法典第543条、第497条", "scene": "合同约定一方可单方变更条款，且未限制变更范围"},
    {"name": "保证方式约定不明", "law": "民法典第686条", "scene": "保证合同对保证方式没有约定或约定不明确，应承担一般保证还是连带责任保证"},
    {"name": "免责条款排除人身损害", "law": "民法典第506条", "scene": "合同中的免责条款造成对方人身损害，或因故意或重大过失造成对方财产损失"},
    {"name": "试用期超限", "law": "劳动合同法第19条", "scene": "劳动合同约定的试用期超过法定上限，或与同一劳动者多次约定试用期"},
    {"name": "竞业限制经济补偿", "law": "劳动合同法第23条、第24条", "scene": "竞业限制未约定经济补偿，或劳动者履行竞业限制义务后用人单位未按月支付补偿"},
    {"name": "培训服务期违约金", "law": "劳动合同法第22条", "scene": "用人单位提供专项培训并约定服务期，劳动者违约时的违约金限制"},
    {"name": "个人信息处理合规", "law": "个人信息保护法第13条、第15条", "scene": "处理个人信息缺乏合法性基础，或未取得个人同意"},
    {"name": "数据最小化原则", "law": "个人信息保护法第6条", "scene": "处理个人信息超出处理目的所必需的范围、收集与处理目的无直接关联的信息"},
    {"name": "无限责任条款", "law": "民法典第506条、第585条", "scene": "合同约定一方承担无限、全部责任，排除责任限制"},
    {"name": "放弃法定权利条款", "law": "民法典第497条", "scene": "合同约定一方预先放弃诉讼时效利益、抗辩权等法定权利"},
]


class _CaseRule(BaseModel):
    ruling: str = Field(..., description="裁判规则：法院在类似案件中确立的裁判要旨，100-200字，准确援引法条")
    practice: str = Field(..., description="实务要点：给合同拟定方的可操作建议，50-100字")


async def generate_rules() -> list[dict]:
    llm = get_structured_llm(_CaseRule)
    results: list[dict] = []
    for i, pt in enumerate(DISPUTE_POINTS):
        prompt = (
            f"你是资深民商事审判法官。请针对以下合同审查高频争议点，"
            f"提炼一条裁判规则和实务要点。\n\n"
            f"争议点：{pt['name']}\n"
            f"法条依据：{pt['law']}\n"
            f"适用场景：{pt['scene']}\n\n"
            f"要求：裁判规则必须准确表述法院的裁判立场并援引法条；"
            f"实务要点给出可操作的合规建议。"
        )
        try:
            r = await llm.ainvoke(prompt)
            results.append({
                "name": pt["name"],
                "law": pt["law"],
                "scene": pt["scene"],
                "ruling": r.ruling,
                "practice": r.practice,
            })
            print(f"  [{i+1}/{len(DISPUTE_POINTS)}] {pt['name']} 完成")
        except Exception as e:
            print(f"  [{i+1}/{len(DISPUTE_POINTS)}] {pt['name']} 失败: {e}")
    return results


def main() -> None:
    print(f"为 {len(DISPUTE_POINTS)} 个争议点生成裁判规则 ...")
    rules = asyncio.run(generate_rules())
    print(f"成功生成 {len(rules)} 条裁判规则")

    if not rules:
        print("无数据，终止")
        sys.exit(1)

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT, alias="default")
    print(f"加载 BGE-M3 ...")
    from FlagEmbedding import BGEM3FlagModel
    model = BGEM3FlagModel(BGE_M3_PATH, use_fp16=False)

    client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")
    total = 0
    for i, r in enumerate(rules):
        content = (
            f"【争议点】{r['name']}\n"
            f"【裁判规则】{r['ruling']}\n"
            f"【法条依据】{r['law']}\n"
            f"【实务要点】{r['practice']}\n"
            f"（本裁判规则为基于法条与审判实务的提炼，非特定真实判例）"
        )
        dense = model.encode([content], return_dense=True, return_sparse=False, return_colbert_vecs=False)["dense_vecs"][0].tolist()
        case_id = f"dispute_{i+1:02d}"
        row = {
            "id": hashlib.md5(f"{case_id}".encode()).hexdigest()[:32],
            "case_id": case_id,
            "case_name": r["name"],
            "section": "裁判规则",
            "court": "裁判规则提炼（基于法条与审判实务）",
            "judgment_date": "",
            "content": content,
            "embedding": dense,
        }
        client.insert(collection_name=COLLECTION, data=[row])
        total += 1

    client.flush(collection_name=COLLECTION)
    # 确保索引存在并加载
    col = Collection(COLLECTION)
    if not col.has_index():
        col.create_index("embedding", {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 32}})
    col.load()
    print(f"DONE: {COLLECTION} 灌入 {total} 条，实体数 {col.num_entities}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
