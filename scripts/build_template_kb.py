#!/usr/bin/env python3
"""
build_template_kb.py — 从 sample/kb_data/templates/*.txt 解析合同示范文本，
按「第X条」切分成条款级 chunk，用本地 BGE-M3 编码 dense 向量，灌入 kb_template。

数据来源：
  - 市场监管总局全国合同示范文本库（买卖/租赁/承揽/服务，PDF→txt）
  - 人社部《劳动合同（通用）》示范文本（地方人社厅转载 HTML→txt）

每条 chunk 是「一条示范条款」，clause_type 用关键词识别（付款/违约/保密/交付…），
is_safe_clause 恒为 true（官方示范文本）。

用法：
  python scripts/build_template_kb.py [data_dir]
  默认 data_dir = sample/kb_data/templates

依赖：
  - Milvus 已在 localhost:19530 运行
  - BGE-M3 模型在 D:/contract/models/embedding/bge-m3
  - kb_template collection 已由 scripts/init_milvus.py 创建
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymilvus import Collection, MilvusClient, connections

MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
BGE_M3_PATH = "D:/contract/models/embedding/bge-m3"
BATCH = 16
COLLECTION = "kb_template"

# 条款标记：第X条（中文数字）
_ARTICLE = re.compile(r"第([一二三四五六七八九十百千零]+)条")

# 条款类型识别（与旧 build_kb_template.py 的 CLAUSE_TYPE_PATTERNS 一致）
CLAUSE_TYPE_PATTERNS: dict[str, list[str]] = {
    "付款条款": ["付款", "价款", "支付", "报酬", "费用", "价格", "工资", "劳务费"],
    "交付条款": ["交付", "交货", "验收", "交付期限", "工作内容", "工作地点"],
    "违约责任": ["违约", "违约金", "赔偿责任", "罚则", "赔偿"],
    "保密条款": ["保密", "机密", "商业秘密", "保密义务", "数据安全", "个人信息"],
    "知识产权": ["知识产权", "专利", "商标", "著作权", "技术成果", "版权"],
    "争议解决": ["争议", "仲裁", "诉讼", "管辖", "法律适用", "纠纷"],
    "不可抗力": ["不可抗力", "自然灾害", "意外事件", "政府行为"],
    "合同变更": ["变更", "修改", "补充协议"],
    "合同解除": ["解除", "终止", "合同终止", "合同解除"],
    "通知送达": ["通知", "送达", "地址变更", "联系方式"],
    "保证与承诺": ["保证", "承诺", "陈述", "担保", "声明"],
    "定义条款": ["定义", "下列用语", "本协议所称", "释义"],
}


def identify_clause_types(text: str) -> list[str]:
    """识别条款所属类型。"""
    types = []
    for ctype, keywords in CLAUSE_TYPE_PATTERNS.items():
        if any(kw in text for kw in keywords):
            types.append(ctype)
    return types if types else ["其他"]


def parse_template(txt_path: Path) -> list[dict]:
    """解析一份合同示范文本，按「第X条」切分成条款列表。"""
    name = txt_path.stem
    # 文件名形如「买卖合同_农副产品2025」→ template_name / category
    category = name.split("_")[0] if "_" in name else "其他"
    template_name = name

    text = txt_path.read_text(encoding="utf-8", errors="ignore")
    # 定位「第一条」作为正文起点，避免「使用说明」里的杂讯
    matches = list(_ARTICLE.finditer(text))
    if not matches:
        print(f"  [warn] {name}: 未找到「第X条」")
        return []

    articles = []
    for i, m in enumerate(matches):
        no = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        # 去掉换行/多余空白（合同正文里连续空白无意义）
        body = re.sub(r"\s+", " ", body)
        if len(body) < 5:
            continue
        full_no = f"第{no}条"
        ctypes = identify_clause_types(full_no + body)
        articles.append({
            "id": hashlib.md5(f"{name}_{full_no}".encode()).hexdigest()[:32],
            "content": f"{full_no} {body}",
            "clause_type": ",".join(ctypes),
            "template_name": template_name,
            "category": category,
            "is_safe_clause": "true",
        })
    return articles


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample/kb_data/templates")
    files = sorted(data_dir.glob("*.txt"))
    if not files:
        print(f"未找到 txt 文件：{data_dir}")
        sys.exit(1)

    print(f"待处理 {len(files)} 份模板：{[f.stem for f in files]}")

    all_chunks: list[dict] = []
    for f in files:
        chunks = parse_template(f)
        print(f"  {f.stem}: {len(chunks)} 条")
        all_chunks.extend(chunks)
    print(f"共 {len(all_chunks)} 条示范条款")

    if not all_chunks:
        print("无条款可灌入，终止")
        sys.exit(1)

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT, alias="default")
    print(f"加载 BGE-M3 from {BGE_M3_PATH} ...")
    from FlagEmbedding import BGEM3FlagModel
    model = BGEM3FlagModel(BGE_M3_PATH, use_fp16=False)

    client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")
    total = 0
    for i in range(0, len(all_chunks), BATCH):
        batch = all_chunks[i:i + BATCH]
        texts = [r["content"] for r in batch]
        encoded = model.encode(texts, return_dense=True, return_sparse=False, return_colbert_vecs=False)
        rows = []
        for j, r in enumerate(batch):
            row = dict(r)
            row["embedding"] = encoded["dense_vecs"][j].tolist()
            rows.append(row)
        result = client.insert(collection_name=COLLECTION, data=rows)
        total += result.get("insert_count", len(rows))
        print(f"  {total}/{len(all_chunks)} inserted")

    client.flush(collection_name=COLLECTION)
    col = Collection(COLLECTION)
    col.load()
    print(f"DONE: {COLLECTION} has {col.num_entities} entities")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
