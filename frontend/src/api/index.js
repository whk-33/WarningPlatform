import request from './request'

/* ---------------- 认证 ---------------- */

export function loginAdmin(data) {
  return request.post('/login/admin', data)
}

export function loginStudent(data) {
  return request.post('/login/student', data)
}

export function logout() {
  return request.post('/logout')
}

/* ---------------- 管理员端 ---------------- */

export function getHomeStat(params) {
  return request.get('/admin/home/stat', { params })
}

export function getStudentList(params) {
  return request.get('/admin/student/list', { params })
}

export function getStudentDetail(params) {
  return request.get('/admin/student/detail', { params })
}

export function getGnnGraph(params) {
  return request.get('/admin/gnn/graph', { params })
}

export function getWarnList(params) {
  return request.get('/admin/warn/list', { params })
}

export function getWarnReport(params) {
  return request.get('/admin/warn/report', { params })
}

/* ---------------- 学生端 ---------------- */

export function getStudentHomeInfo(params) {
  return request.get('/student/home/info', { params })
}

export function getStudentSuggest(params) {
  return request.get('/student/home/suggest', { params })
}

export function getStudentGraph(params) {
  return request.get('/student/gnn/graph', { params })
}

export function getStudentWarnList(params) {
  return request.get('/student/warn/list', { params })
}

/* ---------------- 公共 ---------------- */

export function getSemesters() {
  return request.get('/meta/semesters')
}

export function getHealth() {
  return request.get('/health')
}
