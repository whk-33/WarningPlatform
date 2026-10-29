import { createRouter, createWebHistory } from 'vue-router'
// admin页面
import AdminHome from '../views/admin/Home.vue'
import StudentList from '../views/admin/StudentList.vue'
import GnnView from '../views/admin/GnnView.vue'
import WarnResult from '../views/admin/WarnResult.vue'
import AdminLogin from '../views/admin/Login.vue'
// student页面
import StudentLogin from '../views/student/Login.vue'
import StudentHome from '../views/student/Home.vue'

const routes = [
  {
    path: '/',
    name: 'adminHome',
    component: AdminHome,
    meta: { role: 'admin' }
  },
  {
    path: '/student-list',
    name: 'studentList',
    component: StudentList,
    meta: { role: 'admin' }
  },
  {
    path: '/gnn-view',
    name: 'gnnView',
    component: GnnView,
    meta: { role: 'admin' }
  },
  {
    path: '/warn-result',
    name: 'warnResult',
    component: WarnResult,
    meta: { role: 'admin' }
  },
  {
    path: '/admin-login',
    name: 'adminLogin',
    component: AdminLogin,
    meta: { hideMenu: true }
  },
  {
    path: '/student-login',
    name: 'studentLogin',
    component: StudentLogin,
    meta: { hideMenu: true }
  },
  {
    path: '/student-home',
    name: 'studentHome',
    component: StudentHome,
    meta: { role: 'student' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  const role = localStorage.getItem('role')
  // 两个登录页面直接放行
  if (to.name === 'adminLogin' || to.name === 'studentLogin') {
    next()
    return
  }
  // 无token，跳到学生登录页
  if (!token) {
    next('/student-login')
    return
  }
  // 角色校验
  if (to.meta.role && to.meta.role !== role) {
    alert('没有权限访问该页面！')
    if (role === 'admin') {
      next('/')
    } else {
      next('/student-home')
    }
    return
  }
  next()
})

export default router
