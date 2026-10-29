import axios from 'axios'

const service = axios.create({
  baseURL: '/api',
  timeout: 10000
})

// 请求拦截器
service.interceptors.request.use(config => {
  // 后续对接登录时在这里携带token
  return config
})

// 响应拦截器
service.interceptors.response.use(
  res => res.data,
  err => {
    console.error('请求出错：', err)
    return Promise.reject(err)
  }
)

export default service
