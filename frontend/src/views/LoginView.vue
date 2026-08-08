<template>
  <div class="login-page">
    <el-card class="login-card">
      <template #header>
        <h2 style="text-align:center;margin:0">合同审查助手</h2>
      </template>

      <!-- Login Tab (default) -->
      <template v-if="!showRegister">
        <el-form :model="loginForm" label-position="top" @submit.prevent="handleLogin">
          <el-form-item label="用户名">
            <el-input v-model="loginForm.username" placeholder="请输入用户名" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="loginForm.password" type="password" placeholder="请输入密码" show-password />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" native-type="submit" style="width:100%">
              {{ loading ? '登录中...' : '登录' }}
            </el-button>
          </el-form-item>
          <p v-if="error" class="error-msg">{{ error }}</p>
        </el-form>

        <div class="switch-row">
          <span class="switch-hint">还没有账号？普通用户可自助注册</span>
          <el-button link type="primary" @click="showRegister = true; error = ''">
            注册新账号 &rarr;
          </el-button>
        </div>
      </template>

      <!-- Register Tab (public self-signup, always normal user) -->
      <template v-else>
        <el-divider content-position="center">注册新账号</el-divider>
        <el-form :model="regForm" label-position="top" @submit.prevent="handleRegister">
          <el-form-item label="用户名">
            <el-input v-model="regForm.username" placeholder="2-128 个字符" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="regForm.password" type="password" placeholder="至少 6 位" show-password />
          </el-form-item>
          <el-form-item label="确认密码">
            <el-input v-model="regForm.confirm" type="password" placeholder="再次输入密码" show-password />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="regLoading" native-type="submit" style="width:100%">
              {{ regLoading ? '注册中...' : '注册并登录' }}
            </el-button>
          </el-form-item>
          <p v-if="regError" class="error-msg">{{ regError }}</p>
        </el-form>

        <div class="switch-row">
          <el-button link type="primary" @click="showRegister = false; regError = ''">
            &larr; 返回登录
          </el-button>
        </div>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const router = useRouter();
const authStore = useAuthStore();

// ── Login state ────────────────────────────────────────────

const loginForm = reactive({ username: '', password: '' });
const loading = ref(false);
const error = ref('');

async function handleLogin() {
  if (!loginForm.username || !loginForm.password) {
    error.value = '请输入用户名和密码';
    return;
  }

  loading.value = true;
  error.value = '';

  try {
    const resp = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(loginForm),
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || '登录失败');
    }

    const data = await resp.json();
    authStore.setAuth(data.access_token, {
      userId: 0,
      username: data.username,
      role: data.role,
    });

    router.push(data.role === 'admin' ? '/admin/knowledge' : '/contract');
  } catch (e: any) {
    error.value = e.message || '登录失败';
  } finally {
    loading.value = false;
  }
}

// ── Register state (public self-signup) ─────────────────────

const showRegister = ref(false);
const regForm = reactive({ username: '', password: '', confirm: '' });
const regLoading = ref(false);
const regError = ref('');

async function handleRegister() {
  regError.value = '';

  const username = regForm.username.trim();
  if (username.length < 2 || username.length > 128) {
    regError.value = '用户名需要 2-128 个字符';
    return;
  }
  if (regForm.password.length < 6) {
    regError.value = '密码至少 6 位';
    return;
  }
  if (regForm.password !== regForm.confirm) {
    regError.value = '两次输入的密码不一致';
    return;
  }

  regLoading.value = true;
  try {
    const resp = await fetch('/api/v1/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password: regForm.password }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => null);
      throw new Error(err?.detail || '注册失败');
    }

    // Registration returns a token — sign in immediately.
    const data = await resp.json();
    authStore.setAuth(data.access_token, {
      userId: 0,
      username: data.username,
      role: data.role,
    });
    router.push(data.role === 'admin' ? '/admin/knowledge' : '/contract');
  } catch (e: any) {
    regError.value = e.message || '注册失败';
  } finally {
    regLoading.value = false;
  }
}
</script>

<style scoped>
.login-page { display: flex; justify-content: center; align-items: center; min-height: 80vh; }
.login-card { width: 400px; max-width: 90vw; }
.error-msg { color: var(--el-color-danger); text-align: center; font-size: 0.85rem; margin-top: -0.5rem; }
.success-msg { color: var(--el-color-success); text-align: center; font-size: 0.85rem; margin-top: -0.5rem; }
.switch-row { margin-top: 1rem; text-align: center; display: flex; flex-direction: column; align-items: center; gap: 0.25rem; }
.switch-hint { font-size: 0.8rem; color: var(--el-text-color-secondary); }
</style>
