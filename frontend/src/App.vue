<template>
  <div id="app-root">
    <!-- Logged-in: sidebar + content -->
    <div v-if="authStore.isLoggedIn" class="layout">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-logo">智审</div>
          <div class="brand-text">
            <span class="brand-name">智审 LawLens</span>
            <span class="brand-sub">AI 合同审查助手</span>
          </div>
        </div>

        <nav class="nav">
          <button class="nav-item primary" @click="goNewReview">
            <el-icon><Plus /></el-icon>
            <span>新建审查</span>
          </button>

          <div
            class="nav-item"
            :class="{ active: route.path === '/history' }"
            @click="router.push('/history')"
          >
            <el-icon><Clock /></el-icon>
            <span>审查历史</span>
          </div>

          <div
            v-if="authStore.role === 'admin'"
            class="nav-item"
            :class="{ active: route.path === '/admin/knowledge' }"
            @click="router.push('/admin/knowledge')"
          >
            <el-icon><Collection /></el-icon>
            <span>法律知识库</span>
          </div>

          <el-tooltip content="功能建设中，敬请期待" placement="right">
            <div class="nav-item disabled">
              <el-icon><FolderOpened /></el-icon>
              <span>合同模板库</span>
              <span class="soon-tag">待上线</span>
            </div>
          </el-tooltip>
        </nav>

        <div class="recent">
          <div class="recent-header">
            <span>最近审查</span>
            <span class="recent-count" v-if="totalReviews > 0">{{ totalReviews }}</span>
          </div>
          <div v-if="recentLoading" class="recent-empty">加载中…</div>
          <div v-else-if="recentReviews.length === 0" class="recent-empty">暂无审查记录</div>
          <div
            v-for="item in recentReviews"
            :key="item.id"
            class="recent-item"
            :class="{ active: currentReviewId === item.id }"
            @click="openReview(item.id)"
          >
            <span class="recent-dot" :class="'status-' + item.status"></span>
            <div class="recent-info">
              <span class="recent-name">{{ item.filename }}</span>
              <span class="recent-meta">
                {{ item.contract_type || '未识别' }} · {{ relTime(item.created_at) }}
              </span>
            </div>
          </div>
        </div>

        <div class="sidebar-footer">
          <div class="user-chip">
            <el-avatar :size="30" class="user-avatar">{{ avatarChar }}</el-avatar>
            <div class="user-meta">
              <span class="user-name">{{ authStore.username }}</span>
              <span class="user-role">{{ authStore.role === 'admin' ? '管理员' : '用户' }}</span>
            </div>
            <el-tooltip content="退出登录" placement="top">
              <el-button text class="logout-btn" @click="logout">
                <el-icon><SwitchButton /></el-icon>
              </el-button>
            </el-tooltip>
          </div>
        </div>
      </aside>

      <main class="content">
        <router-view />
      </main>
    </div>

    <!-- Not logged in: full-screen (login page) -->
    <router-view v-else />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Plus, Clock, Collection, FolderOpened, SwitchButton } from '@element-plus/icons-vue';
import { useAuthStore } from '@/stores/auth';
import { listReviews, type ReviewListItem } from '@/api/contract';

const authStore = useAuthStore();
const route = useRoute();
const router = useRouter();

const recentReviews = ref<ReviewListItem[]>([]);
const recentLoading = ref(false);
// True total of reviews (badge) — the sidebar only shows the latest 6 items.
const totalReviews = ref(0);

const avatarChar = computed(() => (authStore.username || 'U').slice(0, 1).toUpperCase());

const currentReviewId = computed(() => {
  const q = route.query.review;
  return q ? Number(q) : null;
});

function goNewReview() {
  // Clear any ?review= query so the view resets to upload phase
  router.push('/contract');
}

function openReview(id: number) {
  router.push(`/contract?review=${id}`);
}

function logout() {
  authStore.logout();
  router.push('/login');
}

async function loadRecent() {
  if (!authStore.isLoggedIn) return;
  recentLoading.value = true;
  try {
    const all = await listReviews();
    totalReviews.value = all.length;
    recentReviews.value = all.slice(0, 6);
  } catch {
    recentReviews.value = [];
    totalReviews.value = 0;
  } finally {
    recentLoading.value = false;
  }
}

function relTime(iso: string): string {
  const t = new Date(iso.replace(' ', 'T'));
  if (isNaN(t.getTime())) return iso;
  const diff = Date.now() - t.getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return '刚刚';
  if (m < 60) return m + ' 分钟前';
  const h = Math.floor(m / 60);
  if (h < 24) return h + ' 小时前';
  const d = Math.floor(h / 24);
  if (d < 30) return d + ' 天前';
  return iso.slice(0, 10);
}

// Refresh recent list on navigation (e.g., after a new review completes)
watch(
  () => route.fullPath,
  () => {
    void loadRecent();
  },
  { immediate: true }
);

// Refresh when another view mutates the review list (e.g. delete on history page)
function onReviewsChanged() {
  void loadRecent();
}
onMounted(() => window.addEventListener('reviews-changed', onReviewsChanged));
onUnmounted(() => window.removeEventListener('reviews-changed', onReviewsChanged));
</script>

<style>
/* ── Global theme: beige background + green primary (智审 LawLens style) ── */
:root {
  --el-color-primary: #16a34a;
  --el-color-primary-light-3: #4db876;
  --el-color-primary-light-5: #83cd9f;
  --el-color-primary-light-7: #b3e0c3;
  --el-color-primary-light-8: #cbead6;
  --el-color-primary-light-9: #e6f5eb;
  --el-color-primary-dark-2: #128a3e;
}
html, body { margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans SC", sans-serif; }
#app-root { min-height: 100vh; background: #f9f7f4; }
</style>

<style scoped>
.layout { display: flex; min-height: 100vh; }

/* ── Sidebar ── */
.sidebar {
  width: 236px;
  flex-shrink: 0;
  background: #ffffff;
  border-right: 1px solid #ece8e1;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}

.brand { display: flex; align-items: center; gap: 0.6rem; padding: 1.1rem 1rem 0.9rem; }
.brand-logo {
  width: 40px; height: 40px;
  background: linear-gradient(135deg, #16a34a, #0f7a37);
  color: #fff;
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 0.9rem; letter-spacing: 1px;
}
.brand-text { display: flex; flex-direction: column; }
.brand-name { font-weight: 700; font-size: 0.98rem; color: #1f2937; }
.brand-sub { font-size: 0.72rem; color: #9ca3af; }

.nav { padding: 0.25rem 0.75rem; display: flex; flex-direction: column; gap: 2px; }
.nav-item {
  display: flex; align-items: center; gap: 0.55rem;
  padding: 0.55rem 0.75rem;
  border-radius: 8px;
  font-size: 0.9rem;
  color: #4b5563;
  cursor: pointer;
  border: none;
  background: transparent;
  width: 100%;
  text-align: left;
  transition: background 0.15s;
}
.nav-item:hover { background: #f4f2ee; }
.nav-item.active { background: #e6f5eb; color: #128a3e; font-weight: 600; }
.nav-item.primary {
  background: #16a34a; color: #fff; font-weight: 600;
  margin-bottom: 0.5rem;
  justify-content: center;
}
.nav-item.primary:hover { background: #128a3e; }
.nav-item.disabled { color: #b6b0a7; cursor: not-allowed; }
.nav-item.disabled:hover { background: transparent; }
.soon-tag {
  margin-left: auto;
  font-size: 0.66rem;
  background: #f3f1ed;
  color: #a8a29e;
  padding: 1px 6px;
  border-radius: 8px;
}

/* ── Recent reviews ── */
.recent { flex: 1; padding: 0.75rem; min-height: 120px; }
.recent-header {
  display: flex; align-items: center; gap: 0.4rem;
  font-size: 0.75rem; font-weight: 600; color: #9ca3af;
  text-transform: none;
  padding: 0 0.4rem 0.4rem;
}
.recent-count {
  background: #f3f1ed; color: #78716c;
  border-radius: 8px; padding: 0 6px; font-size: 0.7rem;
}
.recent-empty { font-size: 0.8rem; color: #b6b0a7; padding: 0.5rem 0.4rem; }
.recent-item {
  display: flex; align-items: flex-start; gap: 0.5rem;
  padding: 0.45rem 0.5rem;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}
.recent-item:hover { background: #f4f2ee; }
.recent-item.active { background: #e6f5eb; }
.recent-dot { width: 7px; height: 7px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; background: #d1d5db; }
.recent-dot.status-completed { background: #16a34a; }
.recent-dot.status-failed { background: #ef4444; }
.recent-dot.status-parsing, .recent-dot.status-reviewing,
.recent-dot.status-retrieving, .recent-dot.status-revising { background: #f59e0b; }
.recent-info { display: flex; flex-direction: column; min-width: 0; }
.recent-name {
  font-size: 0.82rem; color: #374151;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.recent-meta { font-size: 0.7rem; color: #a8a29e; }

/* ── Footer / user ── */
.sidebar-footer { border-top: 1px solid #f0ede7; padding: 0.75rem; }
.user-chip { display: flex; align-items: center; gap: 0.5rem; }
.user-avatar { background: #e6f5eb; color: #128a3e; font-weight: 600; flex-shrink: 0; }
.user-meta { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.user-name { font-size: 0.85rem; color: #1f2937; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.user-role { font-size: 0.7rem; color: #a8a29e; }
.logout-btn { color: #a8a29e; }

/* ── Content ── */
.content { flex: 1; min-width: 0; padding: 1.5rem 2rem; }

@media (max-width: 860px) {
  .layout { flex-direction: column; }
  .sidebar { width: 100%; height: auto; position: static; }
  .recent { display: none; }
  .content { padding: 1rem; }
}
</style>
