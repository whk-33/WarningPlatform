<template>
  <el-card shadow="hover">
    <template #header>
      <span>学生信息列表</span>
    </template>
    <el-table :data="tableData" border stripe>
      <el-table-column prop="id" label="学号" />
      <el-table-column prop="name" label="姓名" />
      <el-table-column prop="score" label="平均成绩" />
      <el-table-column prop="risk" label="风险等级">
        <template #default="scope">
          <el-tag :type="scope.row.risk === '高危' ? 'danger' : scope.row.risk === '警惕' ? 'warning' : 'success'">
            {{ scope.row.risk }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="scope">
          <el-button size="small" type="primary">查看详情</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
const tableData = [
  { id: '2024001', name: '张三', score: 52, risk: '高危' },
  { id: '2024002', name: '李四', score: 76, risk: '正常' },
  { id: '2024003', name: '王五', score: 61, risk: '警惕' },
  { id: '2024004', name: '赵六', score: 48, risk: '高危' },
  { id: '2024005', name: '陈七', score: 82, risk: '正常' },
]
</script>
