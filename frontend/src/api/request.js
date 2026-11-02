import axios from 'axios'
import { ElMessage } from 'element-plus'
import { clearAuth, getToken, getRole, loginPath } from '../utils/auth'

/**
 * 统一请求封装（对应 docs/接口约定.md §2）
 *
 * - baseURL 默认 /api，开发环境由 vite proxy 转发到 http://127.0.0.1:8000
 * - 请求头自动带 Authorization: Bearer {token}
 * - 响应统一拆包：后端返回 { code, msg, data }，这里直接返回 data
 * - 401 清登录态并回登录页；其它错误提示 msg
 */
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 15000
})

service.interceptors.request.use(config => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

async function toLogin() {
  const role = getRole()
  clearAuth()
  const base = import.meta.env.BASE_URL.replace(/\/$/, '')
  window.location.replace(`${base}${loginPath(role)}`)
}

function messageOf(err) {
  const body = err.response?.data
  return (body && body.msg) || err.message || '请求失败'
}

service.interceptors.response.use(
  res => {
    const body = res.data
    // 非标准响应（如 Swagger / 静态文件）原样返回
    if (!body || typeof body !== 'object' || !('code' in body)) return body
    if (body.code !== 200) {
      ElMessage.error(body.msg || '请求失败')
      return Promise.reject(new Error(body.msg || `业务错误 ${body.code}`))
    }
    return body.data
  },
  async err => {
    const status = err.response?.status
    const msg = messageOf(err)

    if (status === 401) {
      ElMessage.error(msg === '请求失败' ? '登录已过期，请重新登录' : msg)
      await toLogin()
      return Promise.reject(err)
    }
    if (status === 403) {
      ElMessage.error(msg || '没有权限访问该资源')
      return Promise.reject(err)
    }
    if (status === 422) {
      // 参数校验失败：后端 data 里是 [{ field, msg }]
      const detail = err.response?.data?.data?.[0]
      ElMessage.error(detail ? `参数校验失败：${detail.field} ${detail.msg}` : msg)
      return Promise.reject(err)
    }
    ElMessage.error(status === 500 ? '服务异常，请稍后重试' : msg)
    return Promise.reject(err)
  }
)

export default service
