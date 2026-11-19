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

/** 干预记录列表（同时返回可用的处理方式 actions） */
export function getWarnInterventions(params) {
  return request.get('/admin/warn/interventions', { params })
}

/** 登记一条干预记录 */
export function addWarnIntervene(data) {
  return request.post('/admin/warn/intervene', data)
}

/* ---------------- 学生端 ---------------- */

export function getStudentHomeInfo(params, config = {}) {
  return request.get('/student/home/info', { params, ...config })
}

export function getStudentSuggest(params, config = {}) {
  return request.get('/student/home/suggest', { params, ...config })
}

export function getStudentGraph(params) {
  return request.get('/student/gnn/graph', { params })
}

export function getStudentWarnList(params, config = {}) {
  return request.get('/student/warn/list', { params, ...config })
}

/* ---------------- 阶段四 · 风险（与上面 15 个并行，不替换） ---------------- */

/** 16 周曲线：一次拿全，别循环调 */
export function getRiskCurve(stuId, params, config = {}) {
  return request.get(`/risk/student/${stuId}`, { params, ...config })
}

/** 单周风险（含该周 top_factors） */
export function getRiskWeekly(params) {
  return request.get('/risk/weekly', { params })
}

/** 单周归因 top3，已按 contrib 降序 */
export function getRiskFactors(params) {
  return request.get('/risk/factors', { params })
}

/** 本周风险榜（管理员）：返回 { items, total, page, size } */
export function getAdminRiskList(params) {
  return request.get('/admin/risk/list', { params })
}

/** 等级分布 + 预警率（管理员）：mock 下用 list_available 当翻页上限 */
export function getAdminRiskStat(params) {
  return request.get('/admin/risk/stat', { params })
}

/* ---------------- 公共 ---------------- */

export function getSemesters() {
  return request.get('/meta/semesters')
}

export function getHealth() {
  return request.get('/health')
}
