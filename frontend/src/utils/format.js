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

/**
 * 阶段四风险接口的等级文案（低/中/高）。
 * 与上面的 RISK_TEXT（正常/警惕/高危）是两套已冻结的契约，不要互相映射。
 */
export const RISK_NAME_TEXT = { 0: '低', 1: '中', 2: '高' }

/** 预测事件周：-1 是"本学期内无风险"，不是第 0 周；week 与 pred_event_week 都是 0 起 */
export function predEventText(week) {
  const n = Number(week)
  return n === -1 || Number.isNaN(n) ? '本学期内无风险' : `预计第 ${n + 1} 周`
}

/** 0.812 -> 81% */
export function fmtPercent(value) {
  const n = Number(value)
  return value === null || value === undefined || Number.isNaN(n) ? '—' : `${(n * 100).toFixed(0)}%`
}
