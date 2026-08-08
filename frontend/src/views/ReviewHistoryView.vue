<template>
  <div class="history-page">
    <div class="page-header">
      <div>
        <h2>审查历史</h2>
        <p class="page-sub">共 {{ reviews.length }} 份合同审查记录</p>
      </div>
      <el-button type="primary" @click="router.push('/contract')">
        <el-icon style="margin-right: 4px"><Plus /></el-icon>新建审查
      </el-button>
    </div>

    <el-card v-if="loading" class="state-card">加载中…</el-card>
    <el-card v-else-if="loadError" class="state-card">
      加载失败：{{ loadError }}
      <el-button size="small" style="margin-left: 12px" @click="load">重试</el-button>
    </el-card>
    <el-card v-else-if="reviews.length === 0" class="state-card">
      暂无审查记录，点击「新建审查」上传第一份合同。
    </el-card>

    <div v-else class="history-list">
      <div
        v-for="item in reviews"
        :key="item.id"
        class="history-row"
        @click="openReport(item.id)"
      >
        <div class="doc-icon">
          <el-icon :size="20"><Document /></el-icon>
        </div>

        <div class="row-main">
          <span class="row-name">{{ item.filename }}</span>
          <span class="row-meta">
            <el-tag size="small" effect="plain" round>{{ item.contract_type || '未识别' }}</el-tag>
            <span class="meta-time">{{ formatTime(item.created_at) }}</span>
          </span>
        </div>

        <div class="row-risks">
          <span v-if="item.high_risk" class="risk-badge high">高 {{ item.high_risk }}</span>
          <span v-if="item.medium_risk" class="risk-badge medium">中 {{ item.medium_risk }}</span>
          <span v-if="item.low_risk" class="risk-badge low">低 {{ item.low_risk }}</span>
          <span v-if="!item.high_risk && !item.medium_risk && !item.low_risk" class="risk-badge none">无风险</span>
        </div>

        <div class="row-status">
          <el-tag :type="statusType(item.status)" size="small" effect="light">
            {{ statusLabel(item.status) }}
          </el-tag>
        </div>

        <div class="row-action">
          <el-button size="small" type="primary" plain @click.stop="openReport(item.id)">
            查看报告
          </el-button>
          <el-tooltip
            :content="isActiveStatus(item.status) ? '审查进行中，暂不能删除' : '删除该审查记录'"
            placement="top"
          >
            <span class="delete-wrap">
              <el-button
                size="small"
                type="danger"
                plain
                :disabled="isActiveStatus(item.status) || deletingId === item.id"
                :loading="deletingId === item.id"
                @click.stop="handleDelete(item)"
              >
                <el-icon style="margin-right: 4px"><Delete /></el-icon>删除
              </el-button>
            </span>
          </el-tooltip>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus, Document, Delete } from '@element-plus/icons-vue';
import { listReviews, deleteReview, type ReviewListItem } from '@/api/contract';

const router = useRouter();

const reviews = ref<ReviewListItem[]>([]);
const loading = ref(true);
const loadError = ref('');
const deletingId = ref<number | null>(null);

// Reviews still being processed cannot be deleted (backend enforces this too)
const ACTIVE_STATUSES = ['pending', 'parsing', 'reviewing', 'retrieving', 'revising'];
function isActiveStatus(status: string): boolean {
  return ACTIVE_STATUSES.includes(status);
}

async function handleDelete(item: ReviewListItem) {
  try {
    await ElMessageBox.confirm(
      `确定删除「${item.filename}」的审查记录吗？删除后条款、风险卡片、修订建议和问答记录将一并移除，且不可恢复。`,
      '删除审查记录',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
      }
    );
  } catch {
    return; // user cancelled
  }

  deletingId.value = item.id;
  try {
    await deleteReview(item.id);
    reviews.value = reviews.value.filter((r) => r.id !== item.id);
    window.dispatchEvent(new Event('reviews-changed'));
    ElMessage.success('已删除审查记录');
  } catch (err: any) {
    ElMessage.error(err.message || '删除失败');
  } finally {
    deletingId.value = null;
  }
}

async function load() {
  loading.value = true;
  loadError.value = '';
  try {
    reviews.value = await listReviews();
  } catch (err: any) {
    loadError.value = err.message || '加载失败';
  } finally {
    loading.value = false;
  }
}

function openReport(id: number) {
  router.push(`/contract?review=${id}`);
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '等待中', parsing: '拆解中', reviewing: '评审中',
    retrieving: '检索中', revising: '修订中', completed: '已完成', failed: '失败',
  };
  return labels[status] || status;
}

function statusType(status: string): string {
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'danger';
  return 'warning';
}

function formatTime(t: string): string {
  return t ? t.replace('T', ' ').slice(0, 16) : '';
}

onMounted(() => {
  void load();
});
</script>

<style scoped>
.history-page { max-width: 1000px; margin: 0 auto; }

.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1.25rem; }
.page-header h2 { margin: 0; font-size: 1.35rem; color: #1f2937; }
.page-sub { margin: 4px 0 0; font-size: 0.85rem; color: #9ca3af; }

.state-card { color: #6b7280; font-size: 0.9rem; }

.history-list { display: flex; flex-direction: column; gap: 10px; }

.history-row {
  display: flex; align-items: center; gap: 14px;
  background: #fff;
  border: 1px solid #ece8e1;
  border-radius: 12px;
  padding: 14px 18px;
  cursor: pointer;
  transition: box-shadow 0.15s, border-color 0.15s;
}
.history-row:hover { border-color: #b3e0c3; box-shadow: 0 2px 10px rgba(22, 163, 74, 0.08); }

.doc-icon {
  width: 42px; height: 42px;
  border-radius: 10px;
  background: #e6f5eb;
  color: #128a3e;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}

.row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.row-name { font-weight: 600; font-size: 0.95rem; color: #1f2937; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row-meta { display: flex; align-items: center; gap: 8px; }
.meta-time { font-size: 0.75rem; color: #a8a29e; }

.row-risks { display: flex; gap: 6px; flex-shrink: 0; }
.risk-badge { font-size: 0.72rem; padding: 2px 8px; border-radius: 10px; }
.risk-badge.high { background: #fee2e2; color: #b91c1c; }
.risk-badge.medium { background: #fef3c7; color: #b45309; }
.risk-badge.low { background: #e0f2fe; color: #0369a1; }
.risk-badge.none { background: #e6f5eb; color: #128a3e; }

.row-status { flex-shrink: 0; }
.row-action { flex-shrink: 0; display: flex; align-items: center; gap: 8px; }
.delete-wrap { display: inline-block; }

@media (max-width: 720px) {
  .history-row { flex-wrap: wrap; }
  .row-risks { order: 5; }
}
</style>
