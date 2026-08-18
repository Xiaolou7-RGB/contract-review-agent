<template>
  <div class="contract-review">
    <!-- === Upload Phase === -->
    <div v-if="phase === 'upload'" class="upload-wrap">
      <div class="upload-title">
        <h2>新建合同审查</h2>
        <p>上传合同文件，AI 从法律、合规、财务、权责四个维度自动审查风险点</p>
      </div>
      <el-card class="upload-card" shadow="never">
        <el-upload
          ref="uploadRef"
          class="upload-zone"
          drag
          :auto-upload="false"
          :on-change="handleFileChange"
          :limit="1"
          accept=".pdf,.docx,.txt"
        >
          <el-icon :size="48"><UploadFilled /></el-icon>
          <div class="upload-text">
            <p>将合同文件拖到此处，或<em>点击选择</em></p>
            <p class="upload-hint">支持 PDF、DOCX、TXT 格式</p>
          </div>
        </el-upload>

        <div v-if="selectedFile" class="file-info">
          <el-tag>{{ selectedFile.name }}</el-tag>
          <span class="file-size">{{ formatSize(selectedFile.size) }}</span>
        </div>

        <div class="action-row">
          <el-button
            type="primary"
            :disabled="!selectedFile || uploading"
            :loading="uploading"
            @click="startReview"
          >
            {{ uploading ? '上传中...' : '开始审查' }}
          </el-button>
          <el-button :disabled="!selectedFile" @click="resetUpload">取消</el-button>
        </div>
      </el-card>
    </div>

    <!-- === Progress Phase === -->
    <el-card v-if="phase === 'progress'" class="progress-card" shadow="never">
      <template #header>
        <span>审查进度</span>
      </template>
      <div class="progress-status">
        <el-tag :type="statusTagType">{{ statusLabel }}</el-tag>
        <span class="status-text">{{ progressMessage }}</span>
      </div>
      <el-progress
        :percentage="progressPercent"
        :status="progressPercent === 100 ? 'success' : undefined"
        :stroke-width="8"
        :format="formatProgress"
      />
    </el-card>

    <!-- === Paused for Human Review === -->
    <HumanReviewPanel
      v-if="phase === 'paused_review'"
      :items="pausedItems"
      :contract-id="pausedContractId"
      :submitting="submittingDecision"
      @submit="submitHumanDecisions"
    />

    <!-- === Loading Existing Report === -->
    <el-card v-if="phase === 'loading'" class="progress-card" shadow="never">
      <div class="progress-status">
        <el-tag type="info">加载中</el-tag>
        <span class="status-text">正在读取审查报告…</span>
      </div>
    </el-card>

    <!-- === Report Phase (dashboard) === -->
    <template v-if="phase === 'report' && report">
      <!-- Document info header -->
      <div class="doc-header">
        <div class="doc-icon">
          <el-icon :size="26"><Document /></el-icon>
        </div>
        <div class="doc-info">
          <div class="doc-title-line">
            <h2 class="doc-title">{{ report.filename }}</h2>
            <el-tag effect="plain" round size="small">{{ report.contract_type || '未识别' }}</el-tag>
            <el-tag :type="report.status === 'completed' ? 'success' : 'warning'" size="small" effect="light">
              {{ report.status === 'completed' ? '已完成' : report.status }}
            </el-tag>
          </div>
          <p class="doc-meta">
            创建于 {{ formatTime(report.created_at) }}
            <template v-if="report.updated_at"> · 更新于 {{ formatTime(report.updated_at) }}</template>
          </p>
        </div>
        <div class="doc-actions">
          <el-button type="success" @click="exportFinalContract">
            <el-icon style="margin-right: 4px"><Download /></el-icon>导出修订后合同
          </el-button>
          <el-button type="primary" @click="exportReport">
            <el-icon style="margin-right: 4px"><Download /></el-icon>导出报告
          </el-button>
          <el-button @click="resetAll">
            <el-icon style="margin-right: 4px"><RefreshRight /></el-icon>重新审阅
          </el-button>
        </div>
      </div>

      <!-- Summary stats -->
      <div class="stats-row">
        <div class="stat-card accent">
          <div class="stat-value" :class="scoreClass(overallScore)">
            {{ overallScore }}<span class="stat-unit">分</span>
          </div>
          <div class="stat-label">综合合规评分</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ report.review_cards.length }}</div>
          <div class="stat-label">检出风险</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" :class="highRiskCount > 0 ? 'bad' : 'good'">{{ highRiskCount }}</div>
          <div class="stat-label">高风险项</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" :class="scoreClass(complianceRate)">{{ complianceRate }}<span class="stat-unit">%</span></div>
          <div class="stat-label">条款合规率</div>
        </div>
        <div class="stat-card">
          <div class="stat-value small">{{ reviewDuration }}</div>
          <div class="stat-label">审阅用时</div>
        </div>
      </div>

      <!-- Dimension risk cards -->
      <div class="section-title">风险维度概览</div>
      <div class="dim-grid">
        <div v-for="dim in dimensionSummaries" :key="dim.key" class="dim-card">
          <div class="dim-head">
            <span class="dim-name">{{ dim.label }}</span>
            <el-tag :type="riskTagType(dim.topLevel)" size="small" effect="light">
              {{ dim.topLevel === '无' ? '未发现风险' : dim.topLevel + '风险' }}
            </el-tag>
          </div>
          <div class="dim-score-line">
            <span class="dim-score" :class="scoreClass(dim.score)">{{ dim.score }}</span>
            <span class="dim-score-sub">/ 100 安全分</span>
            <span class="dim-count">{{ dim.count }} 项问题</span>
          </div>
          <ul class="dim-issues">
            <li v-for="(issue, i) in dim.issues" :key="i">{{ issue }}</li>
            <li v-if="dim.issues.length === 0" class="dim-ok">未发现实质性问题</li>
          </ul>
        </div>
      </div>

      <!-- Clauses with risk cards -->
      <el-card v-if="report.clauses && report.clauses.length" class="clauses-card" shadow="never">
        <template #header>
          <span>合同条款明细 ({{ report.clauses.length }} 条)</span>
        </template>
        <div
          v-for="clause in report.clauses"
          :key="clause.clause_id"
          class="clause-item"
          :class="'risk-' + (getRiskLevel(clause.clause_id) || 'none')"
        >
          <div class="clause-header">
            <span class="clause-seq">#{{ clause.seq_no }}</span>
            <span v-if="clause.title" class="clause-title">{{ clause.title }}</span>
            <span v-if="clause.type" class="clause-type">{{ clause.type }}</span>
            <el-tag
              v-if="getRiskLevel(clause.clause_id)"
              :type="riskTagType(getRiskLevel(clause.clause_id))"
              size="small"
            >
              {{ getRiskLevel(clause.clause_id) }}风险
            </el-tag>
          </div>
          <p class="clause-content">{{ truncateText(clause.content, 500) }}</p>

          <div v-if="getReviewCardsForClause(clause.clause_id).length" class="review-cards-section">
            <p class="section-label">多维评审结果：</p>
            <div
              v-for="card in getReviewCardsForClause(clause.clause_id)"
              :key="card.dimension"
              class="review-card-mini"
            >
              <el-tag size="small" effect="plain">{{ dimensionLabel(card.dimension) }}</el-tag>
              <el-tag :type="riskTagType(card.level)" size="small" effect="dark">
                {{ card.level }}风险 ({{ Math.round(card.score * 100) }}%)
              </el-tag>
              <span v-if="card.risk_type" class="risk-type">{{ card.risk_type }}</span>
              <p v-if="card.suggestion" class="card-suggestion">{{ card.suggestion }}</p>
            </div>
          </div>

          <div v-if="getEvidenceForClause(clause.clause_id).length" class="evidence-refs">
            <p class="evidence-label">法律依据：</p>
            <div
              v-for="ev in getEvidenceForClause(clause.clause_id).slice(0, 3)"
              :key="ev.id"
              class="evidence-item"
            >
              <el-tag size="small" type="info">{{ ev.source_collection }}</el-tag>
              <span>{{ truncateText(ev.quote, 200) }}</span>
              <el-tag v-if="ev.is_human_review" size="small" type="warning">需人工核实</el-tag>
            </div>
          </div>
        </div>
      </el-card>

      <!-- Revisions with diff -->
      <el-card v-if="report.revisions && report.revisions.length" class="revisions-card" shadow="never">
        <template #header>
          <span>修订建议 ({{ report.revisions.length }} 条)</span>
        </template>
        <div v-for="rev in report.revisions" :key="rev.id" class="revision-item" :class="{ 'revision-done': rev.status !== 'pending' }">
          <div class="diff-box" v-html="rev.diff_html || '暂无差异'"></div>
          <div class="revision-actions">
            <el-tag
              v-if="rev.status === 'accepted'"
              type="success"
              size="small"
              class="revision-status-tag"
            >已采纳</el-tag>
            <el-tag
              v-else-if="rev.status === 'rejected'"
              type="danger"
              size="small"
              class="revision-status-tag"
            >已驳回</el-tag>
            <el-tag
              v-else-if="rev.status === 'needs_lawyer'"
              type="warning"
              size="small"
              class="revision-status-tag"
            >需律师确认</el-tag>
            <el-tag
              v-else
              type="info"
              size="small"
              class="revision-status-tag"
            >待处理</el-tag>

            <el-button size="small" type="success" :disabled="rev.status === 'accepted'" @click="acceptRevision(rev, 'accepted')">采纳</el-button>
            <el-button size="small" type="danger" :disabled="rev.status === 'rejected'" @click="acceptRevision(rev, 'rejected')">驳回</el-button>
            <el-button size="small" type="warning" :disabled="rev.status === 'needs_lawyer'" @click="acceptRevision(rev, 'needs_lawyer')">需律师确认</el-button>
          </div>
        </div>
      </el-card>

      <!-- Disclaimer -->
      <el-card class="disclaimer-card" shadow="never">
        <div class="disclaimer">
          <el-icon><WarningFilled /></el-icon>
          <span>{{ report.disclaimer }}</span>
        </div>
      </el-card>

      <!-- Inline grounded Q&A chat -->
      <QaChatPanel :contract-id="report.contract_id" :token="qaToken" />
    </template>

    <!-- === Error Phase === -->
    <el-card v-if="phase === 'error'" class="error-card" shadow="never">
      <el-result icon="error" title="无法显示审查报告" :sub-title="errorMessage">
        <template #extra>
          <el-button type="primary" @click="resetAll">重新开始</el-button>
        </template>
      </el-result>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import {
  UploadFilled, WarningFilled, Document, Download, RefreshRight,
} from '@element-plus/icons-vue';
import {
  uploadContract,
  runContractReview,
  streamProgress,
  getReport,
  acceptRevision as acceptRevisionApi,
  downloadFinalContract,
  type ReviewReport,
  type Evidence,
} from '@/api/contract';
import QaChatPanel from '@/components/QaChatPanel.vue';
import HumanReviewPanel from '@/components/HumanReviewPanel.vue';

const route = useRoute();
const router = useRouter();

// ── State ──────────────────────────────────────────────────

type Phase = 'upload' | 'progress' | 'loading' | 'paused_review' | 'report' | 'error';

const phase = ref<Phase>('upload');
const selectedFile = ref<File | null>(null);
const uploading = ref(false);
const progressPercent = ref(0);
const progressMessage = ref('');
const currentStatus = ref('');
const report = ref<ReviewReport | null>(null);
const errorMessage = ref('');
const qaToken = computed(() => localStorage.getItem('token') || 'anonymous');

// Human-in-the-Loop state
const pausedItems = ref<any[]>([]);
const pausedContractId = ref(0);
const submittingDecision = ref(false);
let abortController: AbortController | null = null;

// ── Dimension metadata ─────────────────────────────────────

const DIMENSION_LABELS: Record<string, string> = {
  legal: '法律风险',
  compliance: '合规风险',
  financial: '财务风险',
  rights_obligations: '权责风险',
};

function dimensionLabel(key: string): string {
  return DIMENSION_LABELS[key] || key;
}

// ── Dashboard computed ─────────────────────────────────────

const overallScore = computed(() => {
  const cards = report.value?.review_cards || [];
  if (cards.length === 0) return 100;
  const avg = cards.reduce((s, c) => s + c.score, 0) / cards.length;
  return Math.max(0, Math.min(100, Math.round(100 - avg * 100)));
});

const highRiskCount = computed(
  () => (report.value?.review_cards || []).filter((c) => c.level === '高').length
);

const complianceRate = computed(() => {
  const clauses = report.value?.clauses || [];
  if (clauses.length === 0) return 100;
  const risky = new Set((report.value?.review_cards || []).map((c) => c.clause_id));
  const clean = clauses.filter((c) => !risky.has(c.clause_id)).length;
  return Math.round((clean / clauses.length) * 100);
});

const reviewDuration = computed(() => {
  const r = report.value;
  if (!r || !r.updated_at) return '—';
  const start = new Date(r.created_at.replace(' ', 'T')).getTime();
  const end = new Date(r.updated_at.replace(' ', 'T')).getTime();
  if (isNaN(start) || isNaN(end) || end <= start) return '—';
  const sec = Math.round((end - start) / 1000);
  if (sec < 60) return sec + ' 秒';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m + ' 分 ' + s + ' 秒';
});

interface DimSummary {
  key: string;
  label: string;
  score: number;
  count: number;
  topLevel: string;
  issues: string[];
}

const dimensionSummaries = computed<DimSummary[]>(() => {
  const cards = report.value?.review_cards || [];
  const groups: Record<string, typeof cards> = {};
  for (const c of cards) {
    (groups[c.dimension] = groups[c.dimension] || []).push(c);
  }

  const levelRank: Record<string, number> = { '高': 3, '中': 2, '低': 1, '无': 0 };

  return Object.keys(groups).map((key) => {
    const list = groups[key];
    const avg = list.reduce((s, c) => s + c.score, 0) / list.length;
    const topLevel = list.reduce(
      (top, c) => (levelRank[c.level] > levelRank[top] ? c.level : top),
      '无'
    );
    // Unique issue labels: prefer risk_type, fall back to trimmed suggestion
    const seen = new Set<string>();
    const issues: string[] = [];
    for (const c of list) {
      const label = (c.risk_type || c.suggestion || '').trim().slice(0, 40);
      if (label && !seen.has(label)) {
        seen.add(label);
        issues.push(label);
      }
      if (issues.length >= 3) break;
    }
    return {
      key,
      label: dimensionLabel(key),
      score: Math.max(0, Math.min(100, Math.round(100 - avg * 100))),
      count: list.length,
      topLevel,
      issues,
    };
  }).sort((a, b) => a.score - b.score);
});

function scoreClass(score: number): string {
  if (score >= 85) return 'good';
  if (score >= 70) return 'warn';
  return 'bad';
}

// ── Progress phase computed ────────────────────────────────

const statusLabel = computed(() => {
  const labels: Record<string, string> = {
    'pending': '等待中', 'parsing': '条款拆解中',
    'reviewing': '风险评审中', 'retrieving': '法条检索中',
    'revising': '修订建议中', 'paused_waiting': '等待人工审批',
    'completed': '已完成', 'failed': '失败',
  };
  return labels[currentStatus.value] || currentStatus.value || '准备中';
});

const statusTagType = computed(() => {
  const types: Record<string, string> = {
    'pending': 'info', 'parsing': '', 'reviewing': 'warning',
    'retrieving': '', 'revising': '', 'completed': 'success', 'failed': 'danger',
  };
  return types[currentStatus.value] || 'info';
});

// ── Smooth progress animator ──────────────────────────────
// Real stage events from SSE set the base percentage; between events a
// drift ticker eases the bar toward that stage's ceiling (next stage
// base − 2), so the bar keeps visibly moving during long LLM steps
// without ever overshooting the next real milestone.

const STAGE_PERCENT: Record<string, number> = {
  'pending': 15, 'parsing': 25, 'reviewing': 50,
  'retrieving': 75, 'revising': 90, 'completed': 100, 'failed': 100,
};

const STAGE_CEILING: Record<string, number> = {
  'pending': 23, 'parsing': 48, 'reviewing': 73,
  'retrieving': 88, 'revising': 98,
};

const STAGE_MESSAGES: Record<string, string> = {
  'parsing': '正在拆解合同条款...',
  'reviewing': '正在多维度评审风险...',
  'retrieving': '正在检索法律法规和判例...',
  'revising': '正在生成修订建议...',
  'completed': '审查完成！',
};

let driftTimer: ReturnType<typeof setInterval> | null = null;

function stopDrift() {
  if (driftTimer !== null) {
    clearInterval(driftTimer);
    driftTimer = null;
  }
}

function startDrift(ceiling: number) {
  stopDrift();
  driftTimer = setInterval(() => {
    const current = progressPercent.value;
    if (ceiling - current <= 0.5) {
      stopDrift();
      return;
    }
    const step = Math.max((ceiling - current) * 0.025, 0.2);
    progressPercent.value = Math.min(current + step, ceiling);
  }, 400);
}

function applyStage(status: string) {
  currentStatus.value = status;
  if (status === 'failed') {
    stopDrift();
    progressMessage.value = '审查失败';
    return;
  }
  if (status === 'completed') {
    stopDrift();
    progressPercent.value = 100;
    progressMessage.value = '审查完成！';
    return;
  }
  const base = STAGE_PERCENT[status];
  if (base === undefined) return;
  if (base > progressPercent.value) {
    progressPercent.value = base;
  }
  progressMessage.value = STAGE_MESSAGES[status] || progressMessage.value;
  startDrift(STAGE_CEILING[status] ?? base);
}

function formatProgress(percentage: number): string {
  return Math.floor(percentage) + '%';
}

onBeforeUnmount(() => {
  stopDrift();
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
});

// ── Load report from ?review=<id> ──────────────────────────

async function loadFromQuery() {
  const q = route.query.review;
  if (!q) {
    // Navigated to /contract without query → reset to upload
    if (phase.value === 'report' || phase.value === 'loading' || phase.value === 'error') {
      resetAll();
    }
    return;
  }
  const id = Number(q);
  if (!id || isNaN(id)) {
    phase.value = 'error';
    errorMessage.value = '无效的审查 ID';
    return;
  }
  phase.value = 'loading';
  try {
    report.value = await getReport(id);
    phase.value = 'report';
  } catch (err: any) {
    phase.value = 'error';
    errorMessage.value = err.message || '加载报告失败';
  }
}

onMounted(() => {
  void loadFromQuery();
});

watch(
  () => route.query.review,
  () => {
    void loadFromQuery();
  }
);

// ── Upload & review flow ───────────────────────────────────

function handleFileChange(file: any) {
  selectedFile.value = file.raw;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

function resetUpload() {
  selectedFile.value = null;
}

// ── Human-in-the-Loop helpers ──────────────────────────────

async function loadPausedItems(contractId: number) {
  try {
    const data = await getReport(contractId);
    // Merge rule_findings and high-level review_cards into paused items
    const cards = (data.review_cards || []).filter(
      (c: any) => c.level === '高' || c.score > 0.7
    );
    const rules = (data.rule_findings || []).filter(
      (f: any) => f.level === '高'
    );

    pausedItems.value = [
      ...cards.map((c: any) => ({
        id: c.clause_id || c.id,
        title: c.risk_type || c.dimension || '',
        level: c.level,
        score: c.score,
        description: c.suggestion || '',
        source: `LLM评审 · ${c.dimension || ''}维度`,
        type: 'review_card',
      })),
      ...rules.map((r: any) => ({
        id: r.rule_id || r.id,
        title: r.rule_id || '',
        level: r.level,
        score: 1.0,
        description: r.description || '',
        source: `规则引擎 · ${r.category || ''}`,
        type: 'rule_finding',
      })),
    ];

    if (pausedItems.value.length > 0) {
      phase.value = 'paused_review';
    } else {
      // No high-risk items after all — continue to report
      report.value = data;
      phase.value = 'report';
    }
  } catch (err) {
    console.error('Failed to load paused items:', err);
    phase.value = 'error';
    errorMessage.value = '加载待审批项失败';
  }
}

async function submitHumanDecisions(decisions: any[]) {
  if (!pausedContractId.value) return;
  submittingDecision.value = true;
  try {
    const token = localStorage.getItem('token') || 'anonymous';
    const resp = await fetch(
      `/api/v1/contract/${pausedContractId.value}/human-decision`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ decisions }),
      }
    );
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || '提交审批结果失败');
    }

    // Reload the completed report
    phase.value = 'progress';
    progressMessage.value = '正在完成审查...';
    progressPercent.value = 90;

    report.value = await getReport(pausedContractId.value);
    phase.value = 'report';
    currentStatus.value = 'completed';
    progressPercent.value = 100;
    progressMessage.value = '审查完成！';

    router.replace(`/contract?review=${pausedContractId.value}`);
  } catch (err: any) {
    console.error('Submit decisions error:', err);
    ElMessage.error(err.message || '提交审批失败');
  } finally {
    submittingDecision.value = false;
  }
}

async function startReview() {
  if (!selectedFile.value) return;

  uploading.value = true;
  phase.value = 'progress';
  progressPercent.value = 0;
  currentStatus.value = 'pending';
  progressMessage.value = '正在上传文件...';

  try {
    const uploadResult = await uploadContract(selectedFile.value);
    const contractId = uploadResult.contract_id;

    progressPercent.value = 10;
    progressMessage.value = '已上传，正在启动审查...';

    await runContractReview(contractId);
    progressPercent.value = 15;

    abortController = new AbortController();
    const token = localStorage.getItem('token') || 'anonymous';

    await streamProgress(
      contractId,
      token,
      (status) => {
        if (status === 'paused_waiting') {
          // Switch to paused review phase — load report for review items
          stopDrift();
          pausedContractId.value = contractId;
          currentStatus.value = 'paused_waiting';
          progressMessage.value = '检测到高风险项，需要人工审批';
          loadPausedItems(contractId);
          return;
        }
        applyStage(status);
      },
      abortController.signal
    );

    stopDrift();
    progressMessage.value = '正在生成报告...';
    report.value = await getReport(contractId);
    phase.value = 'report';
    currentStatus.value = 'completed';
    progressPercent.value = 100;

    // Put the new review id into the URL so sidebar/history stay consistent
    router.replace(`/contract?review=${contractId}`);
  } catch (err: any) {
    stopDrift();
    if (err.name === 'AbortError') return;
    console.error(err);
    phase.value = 'error';
    errorMessage.value = err.message || '未知错误';
  } finally {
    uploading.value = false;
  }
}

function exportReport() {
  window.print();
}

async function exportFinalContract() {
  if (!report.value) return;
  const contractId = report.value.contract_id;

  // 前端先校验：还有未决策的修订则提示，不请求后端
  const pending = (report.value.revisions || []).filter(
    (r) => r.status === 'pending'
  ).length;
  if (pending > 0) {
    ElMessage.warning(`还有 ${pending} 条修订未决策，请先全部决策后再导出`);
    return;
  }

  try {
    const result = await downloadFinalContract(contractId);
    if (result.ok) {
      ElMessage.success('已导出修订后合同');
    } else {
      ElMessage.error(result.message || '导出失败');
    }
  } catch (err: any) {
    ElMessage.error(err.message || '导出失败');
  }
}

// ── Report helpers ─────────────────────────────────────────

function getRiskLevel(clauseId: string): string {
  const cards = report.value?.review_cards || [];
  const clauseCards = cards.filter((c: any) => c.clause_id === clauseId);
  if (clauseCards.length === 0) return '';
  const levels = clauseCards.map((c: any) => c.level);
  if (levels.includes('高')) return '高';
  if (levels.includes('中')) return '中';
  return '低';
}

function getReviewCardsForClause(clauseId: string): any[] {
  const cards = report.value?.review_cards || [];
  return cards.filter((c: any) => c.clause_id === clauseId);
}

function riskTagType(level: string): string {
  const types: Record<string, string> = { '高': 'danger', '中': 'warning', '低': 'info', '无': 'success' };
  return types[level] || 'info';
}

function getEvidenceForClause(clauseId: string): Evidence[] {
  if (!report.value?.evidence) return [];
  return report.value.evidence.filter((e) => e.clause_id === clauseId);
}

function truncateText(text: string, maxLen: number): string {
  if (!text) return '';
  return text.length > maxLen ? text.slice(0, maxLen) + '...' : text;
}

function formatTime(t: string): string {
  return t ? t.replace('T', ' ').slice(0, 16) : '';
}

async function acceptRevision(rev: any, status: 'accepted' | 'rejected' | 'needs_lawyer') {
  try {
    const key = `rev_${rev.id}_${Date.now()}`;
    await acceptRevisionApi(rev.id, status, key);
    rev.status = status;
    ElMessage.success(status === 'accepted' ? '已采纳修订' : status === 'rejected' ? '已驳回修订' : '已标记需律师确认');
  } catch (err: any) {
    ElMessage.error(err.message || '操作失败');
  }
}

function resetAll() {
  stopDrift();
  phase.value = 'upload';
  selectedFile.value = null;
  report.value = null;
  progressPercent.value = 0;
  progressMessage.value = '';
  currentStatus.value = '';
  errorMessage.value = '';
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
  if (route.query.review) {
    router.replace('/contract');
  }
}

function getCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
}
</script>

<style scoped>
.contract-review { max-width: 1080px; margin: 0 auto; }

/* ── Upload ── */
.upload-wrap { max-width: 640px; margin: 3rem auto 0; }
.upload-title { text-align: center; margin-bottom: 1.5rem; }
.upload-title h2 { margin: 0 0 6px; font-size: 1.5rem; color: #1f2937; }
.upload-title p { margin: 0; font-size: 0.88rem; color: #9ca3af; }
.upload-zone { width: 100%; }
.upload-text p { margin: 0.5rem 0; }
.upload-hint { font-size: 0.8rem; color: var(--el-text-color-secondary); }
.file-info { margin-top: 1rem; display: flex; align-items: center; gap: 0.75rem; }
.file-size { color: var(--el-text-color-secondary); font-size: 0.85rem; }
.action-row { margin-top: 1.25rem; display: flex; gap: 0.75rem; }

/* ── Progress / loading ── */
.progress-card { margin-bottom: 1rem; }
.progress-card :deep(.el-progress-bar__inner) { transition: width 0.45s ease; }
.progress-status { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem; }
.status-text { color: var(--el-text-color-secondary); font-size: 0.9rem; }

/* ── Document header ── */
.doc-header {
  display: flex; align-items: center; gap: 14px;
  background: #fff; border: 1px solid #ece8e1; border-radius: 14px;
  padding: 18px 22px;
  margin-bottom: 14px;
}
.doc-icon {
  width: 52px; height: 52px; flex-shrink: 0;
  border-radius: 12px;
  background: #e6f5eb; color: #128a3e;
  display: flex; align-items: center; justify-content: center;
}
.doc-info { flex: 1; min-width: 0; }
.doc-title-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.doc-title { margin: 0; font-size: 1.15rem; color: #1f2937; }
.doc-meta { margin: 4px 0 0; font-size: 0.78rem; color: #a8a29e; }
.doc-actions { display: flex; gap: 8px; flex-shrink: 0; }

/* ── Stats row ── */
.stats-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 20px;
}
.stat-card {
  background: #fff; border: 1px solid #ece8e1; border-radius: 12px;
  padding: 14px 16px;
  display: flex; flex-direction: column; gap: 4px;
}
.stat-card.accent { border-color: #b3e0c3; background: linear-gradient(160deg, #ffffff, #f0faf3); }
.stat-value { font-size: 1.55rem; font-weight: 700; color: #1f2937; line-height: 1.2; }
.stat-value.small { font-size: 1.05rem; padding-top: 6px; }
.stat-unit { font-size: 0.8rem; font-weight: 500; color: #9ca3af; margin-left: 2px; }
.stat-label { font-size: 0.78rem; color: #9ca3af; }
.stat-value.good { color: #16a34a; }
.stat-value.warn { color: #d97706; }
.stat-value.bad { color: #dc2626; }

/* ── Dimension grid ── */
.section-title { font-size: 1rem; font-weight: 700; color: #1f2937; margin: 0 0 10px; }
.dim-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}
.dim-card {
  background: #fff; border: 1px solid #ece8e1; border-radius: 12px;
  padding: 14px 16px;
}
.dim-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.dim-name { font-weight: 600; font-size: 0.92rem; color: #1f2937; }
.dim-score-line { display: flex; align-items: baseline; gap: 6px; margin-bottom: 8px; }
.dim-score { font-size: 1.6rem; font-weight: 700; }
.dim-score.good { color: #16a34a; }
.dim-score.warn { color: #d97706; }
.dim-score.bad { color: #dc2626; }
.dim-score-sub { font-size: 0.72rem; color: #a8a29e; }
.dim-count { margin-left: auto; font-size: 0.75rem; color: #78716c; }
.dim-issues { margin: 0; padding-left: 16px; display: flex; flex-direction: column; gap: 3px; }
.dim-issues li { font-size: 0.78rem; color: #4b5563; line-height: 1.5; }
.dim-issues li.dim-ok { color: #16a34a; list-style: none; margin-left: -16px; }

/* ── Clause details ── */
.clauses-card { margin-bottom: 14px; }
.clause-item {
  border-left: 3px solid var(--el-border-color);
  padding: 0.75rem 1rem;
  margin-bottom: 0.75rem;
  background: var(--el-fill-color-light);
  border-radius: 0 8px 8px 0;
}
.clause-item.risk-高 { border-color: var(--el-color-danger); }
.clause-item.risk-中 { border-color: var(--el-color-warning); }
.clause-item.risk-低 { border-color: var(--el-color-primary); }
.clause-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
.clause-seq { font-weight: 600; color: var(--el-text-color-secondary); }
.clause-title { font-weight: 500; }
.clause-type { font-size: 0.8rem; color: var(--el-text-color-secondary); background: var(--el-fill-color); padding: 0 6px; border-radius: 3px; }
.clause-content { margin: 0.5rem 0; font-size: 0.9rem; line-height: 1.7; white-space: pre-wrap; }
.review-cards-section { margin-top: 0.75rem; padding-top: 0.5rem; border-top: 1px dashed var(--el-border-color-lighter); }
.section-label { font-size: 0.8rem; font-weight: 600; color: var(--el-text-color-secondary); margin-bottom: 0.5rem; }
.review-card-mini { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.5rem; padding: 0.5rem; background: var(--el-fill-color-lighter); border-radius: 4px; }
.risk-type { font-size: 0.8rem; color: var(--el-text-color-regular); }
.card-suggestion { width: 100%; margin: 0.25rem 0 0 0; font-size: 0.85rem; color: var(--el-text-color-primary); line-height: 1.5; }
.evidence-refs { margin-top: 0.75rem; padding-top: 0.5rem; border-top: 1px solid var(--el-border-color-lighter); }
.evidence-label { font-size: 0.8rem; color: var(--el-text-color-secondary); margin-bottom: 0.25rem; }
.evidence-item { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem; font-size: 0.85rem; }

/* ── Revisions ── */
.revisions-card { margin-bottom: 14px; }
.revision-item { margin-bottom: 1.25rem; padding-bottom: 1rem; border-bottom: 1px solid var(--el-border-color-lighter); }
.revision-item.revision-done { opacity: 0.72; }
.diff-box { font-size: 0.9rem; line-height: 1.7; padding: 0.75rem; background: #fafaf8; border-radius: 6px; margin-bottom: 0.5rem; }
.revision-actions { display: flex; align-items: center; gap: 0.5rem; }
.revision-status-tag { flex-shrink: 0; }

/* ── Disclaimer ── */
.disclaimer-card { margin-bottom: 14px; }
.disclaimer { display: flex; align-items: flex-start; gap: 0.5rem; font-size: 0.85rem; color: #92400e; background: #fffbeb; padding: 1rem; border-radius: 8px; border: 1px solid #fde68a; }

.error-card { margin-bottom: 1rem; }

@media (max-width: 900px) {
  .stats-row { grid-template-columns: repeat(2, 1fr); }
  .doc-header { flex-wrap: wrap; }
  .doc-actions { width: 100%; }
}
</style>
