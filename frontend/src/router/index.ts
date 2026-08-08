import { createRouter, createWebHistory } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
    },
    {
      path: '/contract',
      name: 'contract-review',
      component: () => import('@/views/ContractReviewView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/history',
      name: 'review-history',
      component: () => import('@/views/ReviewHistoryView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/admin/knowledge',
      name: 'admin-knowledge',
      component: () => import('@/views/admin/KnowledgeAdminView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/',
      redirect: '/contract',
    },
  ],
});

router.beforeEach((to, _from, next) => {
  const authStore = useAuthStore();

  if (to.meta.requiresAuth && !authStore.isLoggedIn) {
    next('/login');
  } else if (to.meta.requiresAdmin && authStore.role !== 'admin') {
    next('/contract');
  } else {
    next();
  }
});

export default router;
