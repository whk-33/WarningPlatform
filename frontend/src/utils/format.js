export const RISK_TEXT = { 0: '正常', 1: '警惕', 2: '高危' }

/** risk_level -> el-tag type */
export function riskType(level) {
  if (Number(level) >= 2) return 'danger'
  if (Number(level) === 1) return 'warning'
  return 'success'
}

/** 分数：null / NaN 一律显示破折号，不能当 0 */
export function fmtScore(value, digits = 1) {
  const n = Number(value)
  return value === null || value === undefined || Number.isNaN(n) ? '—' : n.toFixed(digits)
}

/** 0.812 -> 81% */
export function fmtPercent(value) {
  const n = Number(value)
  return value === null || value === undefined || Number.isNaN(n) ? '—' : `${(n * 100).toFixed(0)}%`
}
