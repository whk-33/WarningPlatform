<template>
  <el-card shadow="hover">
    <div class="header">
      <h2>学生列表</h2>
      <el-input v-model="searchKey" placeholder="输入学号/姓名搜索" style="width:260px"/>
    </div>
    <el-table :data="tableData.filter(item=>item.name.includes(searchKey)||item.id.includes(searchKey))" border stripe>
      <el-table-column prop="id" label="学号" width="120"/>
      <el-table-column prop="name" label="姓名" width="100"/>
      <el-table-column prop="major" label="专业"/>
      <el-table-column prop="score" label="平均分"/>
      <el-table-column label="风险等级">
        <template #default="scope">
          <el-tag :type="getTagType(scope.row.level)">
            {{ scope.row.level }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="scope">
          <el-button type="primary" link>查看详情</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
const searchKey = ref('')
const tableData = ref([
  { id: '2023001', name: '张三', major: '计算机科学', score: 82, level: '正常' },
  { id: '2023002', name: '李四', major: '软件工程', score: 70, level: '警惕' },
  { id: '2023003', name: '王五', major: '人工智能', score: 53, level: '高危' },
  { id: '2023004', name: '赵六', major: '计算机科学', score: 88, level: '正常' },
  { id: '2023005', name: '陈七', major: '软件工程', score: 65, level: '警惕' },
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
