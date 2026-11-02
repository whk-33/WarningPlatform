import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getRole, getToken, homePath, loginPath } from '../utils/auth'

const routes = [
  {
    path: '/',
    name: 'AdminHome',
    component: () => import('../views/admin/Home.vue'),
    meta: { requiresAuth: true, role: 'admin', title: '数据概览' }
  },
  {
    path: '/admin-login',
    name: 'AdminLogin',
    component: () => import('../views/admin/Login.vue'),
    meta: { hideMenu: true, title: '管理员登录' }
  },
  {
    path: '/students',
    name: 'StudentList',
    component: () => import('../views/admin/StudentList.vue'),
    meta: { requiresAuth: true, role: 'admin', title: '学生管理' }
  },
  {
    path: '/warn',
    name: 'WarnResult',
    component: () => import('../views/admin/WarnResult.vue'),
    meta: { requiresAuth: true, role: 'admin', title: '预警结果' }
  },
  {
    path: '/gnn',
    name: 'GnnView',
    component: () => import('../views/admin/GnnView.vue'),
    meta: { requiresAuth: true, role: 'admin', title: 'GNN 分析' }
  },
  {
    path: '/student-login',
    name: 'StudentLogin',
    component: () => import('../views/student/Login.vue'),
    meta: { hideMenu: true, title: '学生登录' }
  },
  {
    path: '/student-home',
    name: 'StudentHome',
    component: () => import('../views/student/Home.vue'),
    meta: { requiresAuth: true, role: 'student', title: '我的预警' }
  },
  { path: '/:pathMatch(.*)*', redirect: '/' }
]

const router = createRouter({
  // 部署在子路径时由 vite 的 base 决定，保证和静态资源路径一致
  history: createWebHistory(import.meta.env.BASE_URL),
  routes
})

router.beforeEach(to => {
  const token = getToken()
  const role = getRole()

  if (to.meta.requiresAuth && !token) {
    return loginPath(role || to.meta.role)
  }

  // 登录页已登录时直接回各自首页
  if (to.meta.hideMenu && token) {
    return homePath(role)
  }

  if (to.meta.requiresAuth && to.meta.role && role && to.meta.role !== role) {
    ElMessage.warning('没有权限访问该页面')
    return homePath(role)
  }
  return true
})

router.afterEach(to => {
  document.title = to.meta.title ? `${to.meta.title} · GNN 学业预警平台` : 'GNN 学业预警平台'
})

export default router
