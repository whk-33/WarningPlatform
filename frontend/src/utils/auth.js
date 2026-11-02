/**
 * 登录态读写。
 *
 * 后端约定：JWT 无状态，token 有效期 12 小时，登出只清前端存储。
 * 存：token / role / name / username(或 student_id) / stu_id
 */
const TOKEN_KEY = 'token'
const ROLE_KEY = 'role'
const USER_KEY = 'user'

export function saveAuth(data = {}) {
  if (data.token) localStorage.setItem(TOKEN_KEY, data.token)
  if (data.role) localStorage.setItem(ROLE_KEY, data.role)
  localStorage.setItem(USER_KEY, JSON.stringify({
    name: data.name || '',
    username: data.username || '',
    student_id: data.student_id || data.student_no || '',
    stu_id: data.stu_id || '',
  }))
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function getRole() {
  return localStorage.getItem(ROLE_KEY) || ''
}

export function getUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || '{}')
  } catch {
    return {}
  }
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(ROLE_KEY)
  localStorage.removeItem(USER_KEY)
}

export function isLoggedIn() {
  return Boolean(getToken())
}

/** 按角色返回该去的首页 */
export function homePath(role = getRole()) {
  return role === 'admin' ? '/' : '/student-home'
}

/** 按角色返回该去的登录页 */
export function loginPath(role = getRole()) {
  return role === 'admin' ? '/admin-login' : '/student-login'
}
