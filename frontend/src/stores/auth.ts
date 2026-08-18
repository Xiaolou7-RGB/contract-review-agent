import { defineStore } from 'pinia';
import { ref, computed } from 'vue';

interface UserInfo {
  userId: number;
  username: string;
  role: 'user' | 'admin';
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('token') || '');
  const user = ref<UserInfo | null>(null);

  const isLoggedIn = computed(() => !!token.value);
  const username = computed(() => user.value?.username || '');
  const role = computed(() => user.value?.role || 'user');

  function setAuth(newToken: string, userInfo: UserInfo) {
    token.value = newToken;
    user.value = userInfo;
    localStorage.setItem('token', newToken);
  }

  function logout() {
    token.value = '';
    user.value = null;
    localStorage.removeItem('token');
  }

  /**
   * Called when any API returns 401. The current token is invalid
   * (expired or revoked), so wipe local state and send the user to the
   * login page. We use location.href rather than router.push to avoid a
   * pinia ↔ router import cycle and to fully reset in-memory component state.
   */
  function handle401() {
    logout();
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login';
    }
  }

  async function fetchMe() {
    if (!token.value) return;
    try {
      const resp = await fetch('/api/v1/auth/me', {
        headers: { Authorization: `Bearer ${token.value}` },
      });
      if (resp.ok) {
        const data = await resp.json();
        user.value = {
          userId: data.user_id,
          username: data.username,
          role: data.role,
        };
      } else {
        logout();
      }
    } catch {
      // keep current state
    }
  }

  return { token, user, isLoggedIn, username, role, setAuth, logout, handle401, fetchMe };
});
