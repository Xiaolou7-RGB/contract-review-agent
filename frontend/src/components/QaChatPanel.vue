<template>
  <el-card class="qa-card" shadow="never">
    <template #header>
      <div class="qa-header">
        <div class="qa-heading">
          <span class="qa-title">智能对话</span>
          <span class="qa-subtitle">基于本合同条款与审查结果，法条实时检索，可追问任意风险点</span>
        </div>
        <div v-if="ready" class="qa-session-tools">
          <el-dropdown trigger="click" @command="onSessionCommand">
            <el-button size="small" text type="primary">
              历史会话<el-icon class="el-icon--right"><CaretBottom /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="s in sessions" :key="s.id" :command="s.id">
                  <span class="dd-item" :class="{ 'is-current': s.id === sessionId }">
                    <span class="dd-title">{{ s.title || '未命名对话' }}</span>
                    <span class="dd-meta">{{ formatTime(s.updated_at) }} · {{ s.message_count }} 条</span>
                  </span>
                </el-dropdown-item>
                <el-dropdown-item v-if="sessions.length === 0" disabled>暂无历史对话</el-dropdown-item>
                <el-dropdown-item divided command="__delete_current__">删除当前对话</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button size="small" type="primary" plain @click="newConversation">
            <el-icon><Plus /></el-icon>新对话
          </el-button>
        </div>
      </div>
    </template>

    <div class="qa-panel">
      <div class="qa-disclaimer">
        回答仅基于本合同条款与审查结果，法条引用均来自实时检索，不构成法律意见。
      </div>

      <div ref="listRef" class="qa-messages">
        <div v-if="!ready" class="qa-empty">
          <template v-if="initFailed">
            问答会话初始化失败，请确认后端服务已运行最新版本。<br /><br />
            <el-button size="small" type="primary" @click="retryInit">重试</el-button>
          </template>
          <template v-else>正在初始化会话…</template>
        </div>
        <template v-else>
          <div v-if="messages.length === 0" class="qa-empty">
            试试问本合同的问题，例如「第二条有什么风险？」「违约金的法律依据是什么？」
          </div>

          <div
            v-for="(m, idx) in messages"
            :key="idx"
            class="qa-msg"
            :class="'role-' + m.role"
          >
            <div class="bubble">
              {{ m.content }}<span v-if="m.status === 'streaming'" class="cursor">▍</span>
            </div>

            <div v-if="m.role === 'assistant' && m.citations.length" class="qa-citations">
              <div v-for="c in m.citations" :key="c.ref" class="cite">
                <el-tag size="small">{{ c.ref }} {{ c.article_no || '法条' }}</el-tag>
                <span class="cite-score">相关度 {{ Math.round(c.score * 100) }}%</span>
                <p class="cite-quote">{{ c.quote }}</p>
              </div>
            </div>

            <div v-if="m.status === 'failed'" class="qa-failed">回答生成失败，请重试</div>
          </div>
        </template>
      </div>

      <div class="qa-input">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          :disabled="sending"
          placeholder="输入问题，或选择审查维度深入追问…"
          @keydown.enter.exact.prevent="send"
        />
        <button class="qa-send" :disabled="!canSend" @click="send">
          <el-icon v-if="!sending"><Promotion /></el-icon>
          <span v-else class="qa-send-loading"></span>
        </button>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Promotion, CaretBottom, Plus } from '@element-plus/icons-vue';
import {
  createQaSession,
  resumeQaSession,
  listQaSessions,
  deleteQaSession,
  getQaMessages,
  askQuestion,
  streamAnswer,
  type QaCitation,
  type QaSessionInfo,
} from '@/api/qa';

const props = defineProps<{
  contractId: number;
  token: string;
}>();

interface ChatMessage {
  id: number | null;
  role: 'user' | 'assistant';
  content: string;
  citations: QaCitation[];
  status: 'completed' | 'streaming' | 'failed';
}

const ready = ref(false);
const initFailed = ref(false);
const sending = ref(false);
const input = ref('');
const messages = ref<ChatMessage[]>([]);
const sessions = ref<QaSessionInfo[]>([]);
const sessionId = ref<number | null>(null);
const listRef = ref<HTMLElement | null>(null);
let initialized = false;

const canSend = computed(
  () => ready.value && !sending.value && input.value.trim().length > 0
);

// Inline panel: initialize as soon as it mounts (no visible/watch dance)
onMounted(() => {
  void ensureReady();
});

// Switching reviews (?review=<id>) must re-resolve the session for the NEW
// contract — otherwise the panel would keep the previous contract's session.
watch(
  () => props.contractId,
  async (newId, oldId) => {
    if (newId === oldId) return;
    initialized = false;
    sessionId.value = null;
    messages.value = [];
    sessions.value = [];
    ready.value = false;
    await ensureReady();
  }
);

function scrollToBottom() {
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight;
}

async function loadMessages(sid: number) {
  const hist = await getQaMessages(sid, props.token);
  messages.value = hist.messages.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    citations: m.citations || [],
    // Abandoned pending/streaming placeholders render as failed (no eternal spinner)
    status: m.status === 'completed' ? 'completed' : 'failed',
  }));
}

async function refreshSessionList() {
  try {
    const r = await listQaSessions(props.contractId, props.token);
    sessions.value = r.sessions;
  } catch {
    sessions.value = [];
  }
}

async function ensureReady() {
  if (initialized) return;
  initialized = true;
  try {
    // Thread resume: reuse the contract's most recently active session,
    // only create a fresh one when none exists yet.
    const resumed = await resumeQaSession(props.contractId, props.token);
    if (resumed.session_id != null) {
      sessionId.value = resumed.session_id;
    } else {
      const created = await createQaSession(props.contractId, props.token);
      sessionId.value = created.session_id;
    }
    await loadMessages(sessionId.value);
    await refreshSessionList();
    ready.value = true;
    await nextTick();
    scrollToBottom();
  } catch (err: any) {
    console.error('QA session init failed:', err);
    initialized = false;
    initFailed.value = true;
    ElMessage.error('问答会话初始化失败，请确认后端服务已运行最新版本后重试');
  }
}

function retryInit() {
  initFailed.value = false;
  void ensureReady();
}

// "2026-08-07 15:30:12.123456+08:00" → "08-07 15:30"
function formatTime(iso: string): string {
  const m = iso.match(/\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : iso.slice(0, 16);
}

function onSessionCommand(command: string | number) {
  if (command === '__delete_current__') {
    void deleteCurrentSession();
    return;
  }
  void switchSession(Number(command));
}

async function switchSession(sid: number) {
  if (sid === sessionId.value || sending.value || sid == null) return;
  try {
    sessionId.value = sid;
    messages.value = [];
    ready.value = true;
    await loadMessages(sid);
    await nextTick();
    scrollToBottom();
  } catch {
    ElMessage.error('加载对话失败');
  }
}

async function newConversation() {
  if (sending.value) return;
  try {
    const created = await createQaSession(props.contractId, props.token);
    sessionId.value = created.session_id;
    messages.value = [];
    await refreshSessionList();
    await nextTick();
    scrollToBottom();
  } catch (err: any) {
    ElMessage.error(err.message || '新建对话失败');
  }
}

async function deleteCurrentSession() {
  if (sessionId.value == null || sending.value) return;
  try {
    await ElMessageBox.confirm('将删除当前对话的全部消息，确定继续？', '删除对话', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    });
  } catch {
    return; // user cancelled
  }
  try {
    await deleteQaSession(sessionId.value, props.token);
    await refreshSessionList();
    if (sessions.value.length > 0) {
      sessionId.value = sessions.value[0].id;
      await loadMessages(sessions.value[0].id);
    } else {
      const created = await createQaSession(props.contractId, props.token);
      sessionId.value = created.session_id;
      messages.value = [];
      await refreshSessionList();
    }
    ElMessage.success('对话已删除');
    await nextTick();
    scrollToBottom();
  } catch (err: any) {
    ElMessage.error(err.message || '删除对话失败');
  }
}

async function send() {
  if (!canSend.value || sessionId.value == null) return;

  const question = input.value.trim();
  input.value = '';
  messages.value.push({
    id: null, role: 'user', content: question, citations: [], status: 'completed',
  });
  messages.value.push({
    id: null, role: 'assistant', content: '', citations: [], status: 'streaming',
  });
  // Grab the reactive proxies (mutating a stale plain object would not render)
  const assistant = messages.value[messages.value.length - 1];
  sending.value = true;
  await nextTick();
  scrollToBottom();

  try {
    const asked = await askQuestion(sessionId.value, question, props.token);
    assistant.id = asked.message_id;

    await streamAnswer(
      asked.message_id,
      props.token,
      {
        onDelta: (t) => { assistant.content += t; scrollToBottom(); },
        onCitations: (items) => { assistant.citations = items; },
        onDone: () => { assistant.status = 'completed'; },
        onError: (msg) => { assistant.status = 'failed'; ElMessage.error(msg); },
      }
    );

    // Stream ended without explicit done/error event
    if (assistant.status === 'streaming') {
      assistant.status = assistant.content ? 'completed' : 'failed';
    }
  } catch (err: any) {
    assistant.status = 'failed';
    ElMessage.error(err.message || '问答请求失败');
  } finally {
    sending.value = false;
    scrollToBottom();
    // First question auto-names the session and bumps updated_at — reload list
    void refreshSessionList();
  }
}
</script>

<style scoped>
.qa-card { margin-top: 14px; border-radius: 12px; }
.qa-header { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; }
.qa-heading { display: flex; flex-direction: column; gap: 0.15rem; }
.qa-title { font-size: 1.02rem; font-weight: 700; color: #1f2937; }
.qa-subtitle { font-size: 0.76rem; color: var(--el-text-color-secondary); font-weight: normal; }
.qa-session-tools { display: flex; align-items: center; gap: 0.4rem; flex-shrink: 0; }

.dd-item { display: flex; flex-direction: column; gap: 0.1rem; max-width: 260px; }
.dd-title { font-size: 0.82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dd-meta { font-size: 0.72rem; color: var(--el-text-color-secondary); }
.dd-item.is-current .dd-title { color: #16a34a; font-weight: 600; }

.qa-panel { display: flex; flex-direction: column; }

.qa-disclaimer {
  font-size: 0.75rem;
  color: #92400e;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 4px;
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.75rem;
  line-height: 1.5;
}

.qa-messages {
  height: 420px;
  overflow-y: auto;
  padding: 0.75rem 4px 0.75rem 0;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  background: #fbfaf8;
}
.qa-empty { color: var(--el-text-color-secondary); font-size: 0.85rem; padding: 2rem 1rem; text-align: center; }

.qa-msg { margin-bottom: 0.9rem; display: flex; flex-direction: column; padding: 0 0.75rem; }
.qa-msg.role-user { align-items: flex-end; }
.qa-msg.role-assistant { align-items: flex-start; }

.bubble {
  max-width: 88%;
  padding: 0.55rem 0.8rem;
  border-radius: 12px;
  font-size: 0.88rem;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}
.role-user .bubble { background: #e6f5eb; color: #14532d; border-bottom-right-radius: 4px; }
.role-assistant .bubble { background: #fff; color: var(--el-text-color-primary); border: 1px solid #eeeae3; border-bottom-left-radius: 4px; }
.cursor { animation: blink 1s step-end infinite; }
@keyframes blink { 50% { opacity: 0; } }

.qa-citations { max-width: 88%; margin-top: 0.4rem; display: flex; flex-direction: column; gap: 0.4rem; }
.cite { background: #fff; border: 1px solid #eeeae3; border-radius: 6px; padding: 0.4rem 0.6rem; }
.cite-score { font-size: 0.75rem; color: var(--el-text-color-secondary); margin-left: 0.5rem; }
.cite-quote {
  margin: 0.3rem 0 0 0;
  font-size: 0.78rem;
  color: var(--el-text-color-regular);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.qa-failed { font-size: 0.78rem; color: var(--el-color-danger); margin-top: 0.25rem; }

.qa-input { display: flex; gap: 0.6rem; align-items: flex-end; margin-top: 0.75rem; }
.qa-input :deep(.el-textarea__inner) { border-radius: 10px; }

.qa-send {
  width: 42px; height: 42px;
  border-radius: 50%;
  border: none;
  background: #16a34a;
  color: #fff;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s, opacity 0.15s;
}
.qa-send:hover:not(:disabled) { background: #128a3e; }
.qa-send:disabled { opacity: 0.45; cursor: not-allowed; }
.qa-send-loading {
  width: 16px; height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
