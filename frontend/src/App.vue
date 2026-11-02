<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { logout } from './api'
import { clearAuth, getRole, getToken, getUser, loginPath } from './utils/auth'

const route = useRoute()
const router = useRouter()

const token = ref(getToken())
const role = ref(getRole())
const user = ref(getUser())

// 登录/退出都发生在同一个页面内，storage 事件不会触发，跟着路由刷新一次登录态
watch(
  () => route.fullPath,
  () => {
    token.value = getToken()
    role.value = getRole()
    user.value = getUser()
  }
)

const showMenu = computed(() => Boolean(token.value) && !route.meta.hideMenu)
const activePath = computed(() => route.path)
const displayName = computed(() => user.value.name || (role.value === 'admin' ? '管理员' : '同学'))

async function handleLogout() {
  const from = role.value
  try {
    await logout()
  } catch {
    // 登出失败（比如 token 已过期）也照样清本地
  } finally {
    clearAuth()
    token.value = ''
    ElMessage.success('已退出登录')
    router.replace(loginPath(from))
  }
}
</script>

<template>
  <el-container class="layout">
    <el-header v-if="showMenu" class="header">
      <div class="brand">
        <span class="logo">GNN</span>
        <span class="brand-name">学业预警平台</span>
      </div>

      <el-menu :default-active="activePath" mode="horizontal" router class="menu">
        <template v-if="role === 'admin'">
          <el-menu-item index="/">数据概览</el-menu-item>
          <el-menu-item index="/students">学生管理</el-menu-item>
          <el-menu-item index="/warn">预警结果</el-menu-item>
          <el-menu-item index="/gnn">GNN 分析</el-menu-item>
        </template>
        <template v-else>
          <el-menu-item index="/student-home">我的预警</el-menu-item>
        </template>
      </el-menu>

      <div class="user-box">
        <span class="user-name">{{ displayName }}</span>
        <el-button link type="primary" @click="handleLogout">退出登录</el-button>
      </div>
    </el-header>

    <el-main :class="['main', { 'main-plain': !showMenu }]">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.layout {
  min-height: 100vh;
}

.header {
  display: flex;
  align-items: center;
  gap: 24px;
  height: 60px;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid #e8ecf1;
  position: sticky;
  top: 0;
  z-index: 10;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: #2f6bff;
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.brand-name {
  font-size: 16px;
  font-weight: 600;
  color: #1f2a37;
}

.menu {
  flex: 1;
  border-bottom: none;
}

.user-box {
  display: flex;
  align-items: center;
  gap: 12px;
  white-space: nowrap;
}

.user-name {
  font-size: 13px;
  color: #5b6675;
}

.main {
  padding: 20px 24px 32px;
}

.main-plain {
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}
</style>
