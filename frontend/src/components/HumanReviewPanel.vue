<template>
  <el-card class="human-review-panel" shadow="never">
    <template #header>
      <div class="panel-header">
        <el-icon :size="20" color="#d97706"><WarningFilled /></el-icon>
        <span class="panel-title">审查暂停 — 检测到 {{ items.length }} 项高风险，请确认</span>
        <el-tag type="danger" size="small">{{ items.length }} 项待确认</el-tag>
      </div>
    </template>

    <div class="panel-body">
      <div
        v-for="(item, idx) in items"
        :key="idx"
        class="risk-item"
        :class="{ decided: decisions[idx]?.action }"
      >
        <div class="risk-header">
          <div class="risk-badge" :class="'risk-' + (item.level || '中')">
            <template v-if="item.level === '高'">🔴 高风险</template>
            <template v-else-if="item.level === '中'">🟡 中风险</template>
            <template v-else>🟢 {{ item.level || '待评估' }}</template>
          </div>
          <span class="risk-source">{{ item.source }}</span>
        </div>

        <div class="risk-title">{{ item.title }}</div>
        <div class="risk-desc">{{ item.description }}</div>

        <div v-if="item.score !== undefined" class="risk-score">
          风险评分：<strong>{{ (item.score * 100).toFixed(0) }}%</strong>
        </div>

        <div class="decision-row">
          <el-radio-group v-model="decisions[idx].action" class="decision-group">
            <el-radio-button value="approve">
              <el-icon><Check /></el-icon> 确认风险
            </el-radio-button>
            <el-radio-button value="modify">
              <el-icon><EditPen /></el-icon> 调整等级
            </el-radio-button>
            <el-radio-button value="reject">
              <el-icon><Close /></el-icon> 忽略
            </el-radio-button>
          </el-radio-group>

          <template v-if="decisions[idx].action === 'modify'">
            <el-select
              v-model="decisions[idx].modified_level"
              placeholder="调整为..."
              size="small"
              style="width: 110px; margin-left: 8px"
            >
              <el-option label="中风险" value="中" />
              <el-option label="低风险" value="低" />
              <el-option label="无风险" value="无" />
            </el-select>
          </template>

          <el-checkbox
            v-model="decisions[idx].skip_revision"
            style="margin-left: 12px"
            size="small"
          >
            跳过修订
          </el-checkbox>
        </div>

        <el-input
          v-model="decisions[idx].comment"
          placeholder="备注（可选）"
          size="small"
          class="comment-input"
          clearable
        />
      </div>
    </div>

    <div class="panel-footer">
      <el-button @click="approveAll">
        <el-icon><Select /></el-icon> 全部确认，继续审查
      </el-button>
      <el-button type="primary" :loading="submitting" @click="submitDecisions">
        <el-icon><Promotion /></el-icon> 提交审批结果
      </el-button>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { WarningFilled, Check, Close, EditPen, Select, Promotion } from '@element-plus/icons-vue'

interface HumanReviewItem {
  id: string
  title: string
  level: string
  score: number
  description: string
  source: string
  type: string
}

interface Decision {
  clause_id: string
  action: string
  modified_level: string | null
  modified_score: number | null
  comment: string
  skip_revision: boolean
}

const props = defineProps<{
  items: HumanReviewItem[]
  contractId: number
  submitting: boolean
}>()

const emit = defineEmits<{
  submit: [decisions: Decision[]]
}>()

const decisions = ref<Decision[]>([])

// Initialize decisions when items change
watch(
  () => props.items,
  (newItems) => {
    decisions.value = newItems.map((item) => ({
      clause_id: item.id,
      action: 'approve',
      modified_level: null,
      modified_score: null,
      comment: '',
      skip_revision: false,
    }))
  },
  { immediate: true }
)

function approveAll() {
  decisions.value.forEach((d) => {
    d.action = 'approve'
    d.modified_level = null
  })
}

function submitDecisions() {
  emit('submit', decisions.value)
}
</script>

<style scoped>
.human-review-panel {
  margin-bottom: 24px;
  border: 2px solid #fbbf24;
  border-radius: 12px;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel-title {
  font-size: 1.05em;
  font-weight: 600;
  color: #92400e;
  flex: 1;
}

.panel-body {
  max-height: 600px;
  overflow-y: auto;
}

.risk-item {
  padding: 16px;
  margin-bottom: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fafafa;
  transition: all 0.2s;
}

.risk-item.decided {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.risk-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.risk-badge {
  font-size: 0.8em;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 4px;
}

.risk-badge.risk-高 { background: #fee2e2; color: #991b1b; }
.risk-badge.risk-中 { background: #fef3c7; color: #92400e; }
.risk-badge.risk-低 { background: #e0e7ff; color: #3730a3; }

.risk-source {
  font-size: 0.8em;
  color: #94a3b8;
}

.risk-title {
  font-weight: 600;
  font-size: 0.95em;
  margin-bottom: 4px;
}

.risk-desc {
  font-size: 0.85em;
  color: #64748b;
  margin-bottom: 8px;
}

.risk-score {
  font-size: 0.8em;
  color: #dc2626;
  margin-bottom: 10px;
}

.decision-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}

.decision-group {
  font-size: 0.85em;
}

.comment-input {
  margin-top: 4px;
}

.panel-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 16px;
  border-top: 1px solid #e2e8f0;
}
</style>
