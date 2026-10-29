<template>
  <el-card shadow="hover">
    <div class="header">
      <h2>预警结果</h2>
      <el-date-picker v-model="dateRange" type="daterange" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期"/>
    </div>
    <el-divider />
    <el-table :data="warnList" border stripe>
      <el-table-column prop="warnId" label="预警编号" width="120"/>
      <el-table-column prop="stuId" label="学号" width="120"/>
      <el-table-column prop="stuName" label="姓名" width="100"/>
      <el-table-column prop="warnTime" label="预警时间"/>
      <el-table-column prop="riskLevel" label="风险等级">
        <template #default="scope">
          <el-tag :type="getTagType(scope.row.riskLevel)">
            {{ scope.row.riskLevel }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="reason" label="预警原因"/>
      <el-table-column label="操作">
        <template #default="scope">
          <el-button type="primary" link>查看分析报告</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
const dateRange = ref([])
const warnList = ref([
  { warnId: 'W2026001', stuId: '2023003', stuName: '王五', warnTime: '2026-10-20 14:22', riskLevel: '高危', reason: '多门课程连续低分，挂科风险高' },
  { warnId: 'W2026002', stuId: '2023002', stuName: '李四', warnTime: '2026-10-21 09:15', riskLevel: '警惕', reason: '近期作业提交率下降' },
  { warnId: 'W2026003', stuId: '2023005', stuName: '陈七', warnTime: '2026-10-22 16:40', riskLevel: '警惕', reason: '测验成绩下滑明显' },
])

const getTagType = (level) => {
  if (level === '高危') return 'danger'
  if (level === '警惕') return 'warning'
  return 'success'
}
</script>

<style scoped>
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
</style>
