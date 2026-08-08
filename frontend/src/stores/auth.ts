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

  return { token, user, isLoggedIn, username, role, setAuth, logout, fetchMe };
});
