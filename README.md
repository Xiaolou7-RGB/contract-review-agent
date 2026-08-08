# 合同审查智能体（Contract Review Agent）

基于 LLM + RAG 的智能合同审查系统：上传合同 → 条款拆解 → 多维度风险评审 → 法条/判例检索佐证 → 修订建议，并支持基于已审查合同的法律问答对话。

## 核心功能

- **合同解析**：支持 PDF / DOCX / TXT 上传，自动拆解为结构化条款
- **多维风险评审**：法律、合规、财务、权责四个维度，LLM 逐条评分并给出风险等级（高/中/低）
- **RAG 法条检索**：Milvus 混合检索（向量 + 关键词）+ 交叉编码器重排，审查结论附法条依据
- **修订建议**：针对高风险条款生成可直接采纳/驳回的修订文本，支持幂等确认与律师复核标记
- **法律问答（QA）**：审查完成后可就合同内容对话提问；检索侧实现多查询拆分 + Round-Robin 合并、口语化问题 HyDE 假设文档增强，显著提升口语提问的法条召回
- **审查历史**：历史报告随时回看，修订状态持久化

## 审查流水线

```
上传 → 条款拆解(parse) → 多维评审(review) → 法条检索(retrieve) → 修订建议(revise) → 报告
```

后端通过 SSE 实时推送各阶段状态，前端进度条平滑展示审查进度。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI、LangGraph、asyncpg、aiohttp |
| 检索 | Milvus（hybrid search）+ 本地 reranker（cross-encoder） |
| LLM | DeepSeek（OpenAI 兼容接口） |
| 本地模型 | HuggingFace embedding + reranker（首次运行自动下载至 `models/`） |
| 数据库 | PostgreSQL |
| 前端 | Vue 3 + TypeScript + Vite + Element Plus |
| 测试 | pytest（235 个用例）+ 检索质量评测脚本（黄金集 recall/MRR） |

## 项目结构

```
backend/
  api/v1/          # 路由：合同上传/审查/SSE进度/报告/QA/认证
  agents/
    contract_review/   # 审查流水线（graph + 4 个节点）
    contract_qa/       # 法律问答（多查询拆分 + HyDE 检索增强）
  db/              # 迁移脚本
frontend/          # Vue3 前端
scripts/           # 评测与工具脚本（检索评测、种子数据、E2E）
tests/             # pytest 测试
eval_data/         # 评测黄金集（模拟合同 + 评分指南）
```

## 快速开始

### 1. 依赖服务

需要 PostgreSQL（本项目使用 15432 端口）与 Milvus（19530 端口），可用 Docker 启动。

### 2. 后端

```bash
cd 项目根目录
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt     # 依赖清单由 uv pip freeze 生成
copy .env.local.example .env.local                # 填入 LLM API Key 等配置
.venv\Scripts\python -m backend.main              # 注意用 -m 模块方式启动，默认 8801 端口
```

首次启动自动执行数据库迁移；法条知识库需按 `scripts/` 中脚本导入 Milvus。

### 3. 前端

```bash
cd frontend
npm install
npm run dev    # http://localhost:5173，/api 自动代理到 8801
```

### 4. 测试与评测

```bash
.venv\Scripts\python -m pytest tests/ -q                  # 全量单测
.venv\Scripts\python scripts/eval_qa_retrieval.py          # 检索质量评测（recall/MRR，支持 --hyde-off 对照）
```

## 检索增强要点（QA）

- **T1**：rerank 分数硬截断，杜绝低相关法条进入上下文
- **T2**：LLM 将口语问题拆分为多个检索查询，Round-Robin 合并保证每个子意图都有代表（避免全局排序饿死低分子意图）
- **T3 HyDE**：首轮最高分 < 0.80 时，由 LLM 生成假设法条文本二次检索，与首轮结果按置信度全局合并；LLM 失败/超时自动降级，不影响主流程

评测结果：口语化问题 recall@3 从 50% 提升至 75%，整体 recall 81% → 88%，详见 `outputs/` 中评测报告。
