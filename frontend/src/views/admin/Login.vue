<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { loginAdmin } from '../../api'
import { saveAuth } from '../../utils/auth'

const router = useRouter()
const loading = ref(false)
const form = reactive({ username: '', password: '' })

async function handleLogin() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning('请输入账号和密码')
    return
  }
  loading.value = true
  try {
    const data = await loginAdmin({
      username: form.username.trim(),
      password: form.password
    })
    saveAuth(data)
    ElMessage.success(`欢迎回来，${data.name}`)
    router.replace('/')
  } catch {
    // 401/422 已由请求拦截器提示
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <div class="head">
        <span class="logo">GNN</span>
        <h2>学业预警平台</h2>
        <p class="muted">管理员登录</p>
      </div>

      <el-form :model="form" label-position="top" @submit.prevent="handleLogin">
        <el-form-item label="账号">
          <el-input v-model="form.username" placeholder="admin" clearable @keyup.enter="handleLogin" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="wp123456"
            show-password
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-button type="primary" class="submit" :loading="loading" @click="handleLogin">登录</el-button>
      </el-form>

      <div class="foot">
        <span class="muted">演示账号 admin / wp123456</span>
        <router-link to="/student-login">学生入口 →</router-link>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  width: 100%;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #eef3ff 0%, #f7f9fc 60%, #eef7f4 100%);
  padding: 24px;
}

.login-card {
  width: 380px;
  max-width: 100%;
  border-radius: 12px;
}

.head {
  text-align: center;
  margin-bottom: 20px;
}

.logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: #2f6bff;
  color: #fff;
  font-size: 14px;
  font-weight: 700;
}

.head h2 {
  margin: 10px 0 4px;
  font-size: 19px;
  font-weight: 600;
}

.submit {
  width: 100%;
}

.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
}
</style>
