<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2 style="text-align:center;margin-bottom:24px">管理员登录</h2>
      <el-form ref="formRef" :model="form">
        <el-form-item label="账号">
          <el-input v-model="form.username" placeholder="请输入管理员账号"></el-input>
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" placeholder="请输入密码"></el-input>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleLogin" style="width:100%">登录</el-button>
        </el-form-item>
      </el-form>
      <div class="tip">
        <el-link type="info" @click="$router.push('/student-login')">学生入口</el-link>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
const router = useRouter()

const form = ref({
  username: '',
  password: ''
})

const handleLogin = () => {
  // 模拟管理员登录校验，后续对接后端接口替换这里
  if (form.value.username && form.value.password) {
    localStorage.setItem('token', 'admin-token-123')
    localStorage.setItem('role', 'admin')
    router.push('/')
  } else {
    alert('请输入账号和密码')
  }
}
</script>

<style scoped>
.login-wrap {
  display: flex;
  justify-content: center;
  padding-top: 120px;
  background-color: #f5f7fa;
  height: 100vh;
  box-sizing: border-box;
}
.login-card {
  width: 400px;
  padding: 30px;
}
.tip {
  margin-top:16px;
  text-align:center;
}
</style>
