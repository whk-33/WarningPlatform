import request from './request'

// 获取学生列表
export function getStudentList(params) {
  return request({
    url: '/student/list',
    method: 'get',
    params
  })
}

// 获取预警学生列表
export function getWarnList() {
  return request({
    url: '/warn/list',
    method: 'get'
  })
}

// 获取GNN图数据（对接算法核心接口）
export function getGnnGraphData() {
  return request({
    url: '/gnn/graph',
    method: 'get'
  })
}
