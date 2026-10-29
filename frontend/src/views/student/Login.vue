<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2 style="text-align:center;margin-bottom:24px">学生登录</h2>
      <el-form ref="formRef" :model="form" label-width="80px">
        <el-form-item label="学号">
          <el-input v-model="form.username"></el-input>
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password"></el-input>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleLogin" style="width:100%">登录</el-button>
        </el-form-item>
        <div style="text-align:center">
          <el-link type="info" @click="$router.push('/admin-login')">管理员入口</el-link>
        </div>
      </el-form>
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
  // 模拟校验，后面对接后端接口替换这里
  if(form.value.username && form.value.password){
    localStorage.setItem('token', 'student-token-123')
    localStorage.setItem('role', 'student')
    router.push({ name: 'studentHome' })
  }else{
    alert('请输入学号和密码')
  }
}
</script>

<style scoped>
.login-wrap{
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #f5f7fa;
}
.login-card{
  width: 380px;
}
</style>
