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

需要 PostgreSQL（本项目使用 15433 端口，database=contract）与 Milvus（19530 端口），可用 `deploy/docker-compose.yml` 一键启动。

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

## MCP 接入（北大法宝真实判例）

审查流水线在离线 RAG 检索之上，额外接入**北大法宝 MCP**，为「高」风险条款补充带**真实案号**的司法判例，对抗 LLM 编造案例的幻觉。

### 为什么接

- 本地 `kb_case` 判例库是 LLM 提炼的「裁判规则」（无真实案号）；北大法宝提供 **1.7 亿+ 真实司法案例**，补齐"有案可依"的真实性短板
- 在 Agent 流水线中真实落地 MCP 协议，形成「离线 RAG + 在线 MCP」混合检索架构

### 接入条件

- 注册 `https://mcp.pkulaw.com` → 控制台创建应用 → 领取 **900 次免费试用**（30 天有效）→ 获取 Access Token
- 案例检索服务：`https://apim-gateway.pkulaw.com/mcp-case`（Streamable HTTP + SSE 流式）
- 工具：`get_case_list(fulltext=...)` → 返回前 20 条真实案例（含案号/法院/日期/裁判要旨）

### 配置

在 `.env.local` 中追加（Token 不入库，`.env.local` 已在 .gitignore）：

```
PKULAW_TOKEN=<你的 Access Token>
PKULAW_CASE_URL=https://apim-gateway.pkulaw.com/mcp-case
```

### 架构：离线兜底 + 在线增强

- **仅 `level == "高"` 且命中语义路由映射表 `need_case=True` 才调 MCP**——在风险等级基础上按风险类型细分（违约/合同无效/免责/担保/竞业/争议解决/劳动/赔偿失衡等需补判例；财务/价格/付款/知产/保密/数据/权责/合规等不调），未知类型默认不调省额度；可由 `enable_case_semantic_route` 开关回滚为「仅按风险等级触发」旧逻辑
- **检索词经 LLM refine**：命中后把法条向查询改写为案由 + 争议焦点的判例向词，提升真实判例命中精度；refine 失败降级用原始词
- **MCP 失败/超时静默降级**：`search_cases` 返回空列表，审查流水线照常走本地知识库，绝不中断
- 证据链（修订建议的参考）：真实判例(pkulaw) → 示范条款(kb_template) → 裁判规则(kb_case) → 司法解释(kb_law) → 民法典(civil_code_hybrid)

相关代码：`backend/core/pkulaw_client.py`（MCP 客户端）、`backend/agents/contract_review/rag_retriever.py`（高风险触发 + 降级）、`revision_writer.py`（证据分组展示）。

## 面试备战材料

项目技术细节与面试表达可参考 [`outputs/合同审查项目-面试问答手册.md`](outputs/合同审查项目-面试问答手册.md)：覆盖高频面试题与标准回答、架构设计理由（6 节点 / 规则闸门 / 混合检索 / HITL / MCP）、项目亮点、团队叙事与数字口径表（技术评测指标可说、业务效果数字禁编）。
