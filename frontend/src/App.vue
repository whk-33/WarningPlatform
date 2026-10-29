<template>
  <div class="layout">
    <el-menu v-if="$route.meta.hideMenu !== true" mode="horizontal" router class="menu">
      <!-- 管理员菜单 -->
      <template v-if="role === 'admin'">
        <el-menu-item index="/">首页</el-menu-item>
        <el-menu-item index="/student-list">学生列表</el-menu-item>
        <el-menu-item index="/gnn-view">GNN图可视化</el-menu-item>
        <el-menu-item index="/warn-result">预警结果</el-menu-item>
      </template>
      <!-- 学生菜单 -->
      <template v-if="role === 'student'">
        <el-menu-item index="/student-home">我的预警</el-menu-item>
      </template>
      <!-- 退出按钮，靠右 -->
      <div class="logout-wrap">
        <el-menu-item @click="handleLogout">退出登录</el-menu-item>
      </div>
    </el-menu>
    <div class="page">
      <router-view />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
const route = useRoute()
const router = useRouter()
const role = ref(localStorage.getItem('role'))

onMounted(() => {
  role.value = localStorage.getItem('role')
})
window.addEventListener('storage', () => {
  role.value = localStorage.getItem('role')
})

// 退出登录
const handleLogout = () => {
  localStorage.removeItem('token')
  localStorage.removeItem('role')
  router.push('/student-login')
}
</script>

<style scoped>
.layout {
  width: 100%;
}
.menu {
  margin-bottom: 20px;
}
.menu :deep(.el-menu) {
  display: flex;
}
.logout-wrap {
  margin-left: auto;
}
.page {
  padding: 0 20px;
}
</style>
