<template>
  <div class="kb-admin">
    <el-card>
      <template #header>
        <div class="header-row">
          <span>知识库管理</span>
          <el-button type="primary" size="small" @click="reindex" :loading="reindexing">全量重建索引</el-button>
        </div>
      </template>

      <!-- Collection tabs -->
      <el-tabs v-model="activeCollection" @tab-change="loadItems">
        <el-tab-pane label="法律法规 (kb_law)" name="law" />
        <el-tab-pane label="判例 (kb_case)" name="case" />
        <el-tab-pane label="合同模板 (kb_template)" name="template" />
      </el-tabs>

      <!-- Add item -->
      <div class="add-row">
        <el-input v-model="newId" placeholder="ID" style="width:150px" size="small" />
        <el-input v-model="newContent" placeholder="内容" style="flex:1" size="small" />
        <el-button type="primary" size="small" @click="createItem" :disabled="!newId || !newContent">添加</el-button>
      </div>

      <!-- Items list -->
      <el-table :data="items" style="margin-top:1rem" max-height="500" stripe>
        <el-table-column prop="id" label="ID" width="200" />
        <el-table-column prop="content" label="内容" min-width="300">
          <template #default="{ row }">
            {{ truncate(row.content, 120) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" type="danger" @click="deleteItem(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- Pagination -->
      <el-pagination
        v-if="total > pageSize"
        style="margin-top:1rem;justify-content:flex-end"
        layout="prev, pager, next"
        :total="total"
        :page-size="pageSize"
        v-model:current-page="currentPage"
        @current-change="loadItems"
      />
    </el-card>

    <!-- Reindex progress dialog -->
    <el-dialog v-model="reindexDialog" title="重建索引进度" width="500px" :close-on-click-modal="false">
      <div v-for="(msg, i) in reindexLog" :key="i" class="reindex-msg">{{ msg }}</div>
      <el-progress v-if="reindexing" :percentage="100" :indeterminate="true" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';

const API = '/api/v1/admin/kb';
const activeCollection = ref('law');
const items = ref<any[]>([]);
const total = ref(0);
const currentPage = ref(1);
const pageSize = 20;
const newId = ref('');
const newContent = ref('');
const reindexing = ref(false);
const reindexDialog = ref(false);
const reindexLog = ref<string[]>([]);

function getToken(): string {
  return localStorage.getItem('token') || '';
}

async function loadItems() {
  try {
    const resp = await fetch(
      `${API}/${activeCollection.value}?page=${currentPage.value}&page_size=${pageSize}`,
      { headers: { Authorization: `Bearer ${getToken()}` } }
    );
    if (!resp.ok) throw new Error('Failed to load');
    const data = await resp.json();
    items.value = data.items || [];
    total.value = data.total || 0;
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败');
  }
}

async function createItem() {
  try {
    const resp = await fetch(`${API}/${activeCollection.value}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${getToken()}`,
      },
      body: JSON.stringify({ id: newId.value, content: newContent.value }),
    });
    if (!resp.ok) throw new Error('创建失败');
    ElMessage.success('已添加');
    newId.value = '';
    newContent.value = '';
    loadItems();
  } catch (e: any) {
    ElMessage.error(e.message || '创建失败');
  }
}

async function deleteItem(id: string) {
  try {
    await ElMessageBox.confirm(`确认删除 ${id}？`, '确认', { type: 'warning' });
    const resp = await fetch(`${API}/${activeCollection.value}/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    if (!resp.ok) throw new Error('删除失败');
    ElMessage.success('已删除');
    loadItems();
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败');
  }
}

async function reindex() {
  reindexing.value = true;
  reindexDialog.value = true;
  reindexLog.value = ['正在重建索引...'];

  try {
    const resp = await fetch(`${API}/reindex`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${getToken()}` },
    });

    const reader = resp.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data:')) {
          try {
            const data = JSON.parse(line.slice(5).trim());
            reindexLog.value.push(data.message || '处理中...');
          } catch { /* skip */ }
        }
      }
    }

    reindexLog.value.push('重建完成！');
    ElMessage.success('索引重建完成');
    loadItems();
  } catch (e: any) {
    ElMessage.error(e.message || '重建失败');
  } finally {
    reindexing.value = false;
  }
}

function truncate(text: string, max: number): string {
  if (!text) return '';
  return text.length > max ? text.slice(0, max) + '...' : text;
}

// Initial load
loadItems();
</script>

<style scoped>
.header-row { display: flex; align-items: center; justify-content: space-between; }
.add-row { display: flex; gap: 0.5rem; margin-top: 1rem; }
.reindex-msg { font-size: 0.85rem; color: #606266; margin-bottom: 0.25rem; }
</style>
