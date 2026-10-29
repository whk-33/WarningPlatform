<template>
  <div class="student-home">
    <el-card class="info-card" shadow="hover">
      <div class="header">
        <h2>个人学业预警中心</h2>
        <el-tag :type="riskTagType">{{ riskLevel }}</el-tag>
      </div>
      <el-divider />
      <div class="info-row">
        <span class="label">学号：</span>
        <span>{{ studentInfo.id }}</span>
      </div>
      <div class="info-row">
        <span class="label">姓名：</span>
        <span>{{ studentInfo.name }}</span>
      </div>
      <div class="info-row">
        <span class="label">专业：</span>
        <span>{{ studentInfo.major }}</span>
      </div>
      <div class="info-row">
        <span class="label">平均绩点：</span>
        <span>{{ studentInfo.gpa }}</span>
      </div>
    </el-card>

    <el-card class="course-card" shadow="hover">
      <h3>已修课程与成绩</h3>
      <el-table :data="courseList" border stripe>
        <el-table-column prop="courseName" label="课程名称"/>
        <el-table-column prop="score" label="成绩"/>
        <el-table-column prop="credit" label="学分"/>
        <el-table-column prop="status" label="学习状态">
          <template #default="scope">
            <el-tag :type="scope.row.status === '预警' ? 'danger' : 'success'">
              {{ scope.row.status }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="suggest-card" shadow="hover">
      <h3>学业建议</h3>
      <ul>
        <li v-for="(item,idx) in suggestList" :key="idx">
          {{ item }}
        </li>
      </ul>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

// 模拟学生数据，后续对接后端接口直接替换
const studentInfo = ref({
  id: '202301001',
  name: '王五',
  major: '计算机科学与技术',
  gpa: 2.3,
  riskLevel: '高危'
})

const courseList = ref([
  { courseName: '数据结构', score: 58, credit: 4, status: '预警' },
  { courseName: '机器学习', score: 62, credit: 3, status: '正常' },
  { courseName: '高等数学', score: 55, credit: 4, status: '预警' }
])

const suggestList = ref([
  '优先补习高等数学、数据结构薄弱知识点，这两门拉低学业风险评分',
  '每周固定时间刷题，及时向任课老师请教难点',
  '跟进课程作业提交，避免缺交作业进一步降低分数'
])

const riskLevel = computed(() => studentInfo.value.riskLevel)
const riskTagType = computed(() => {
  if(riskLevel.value === '高危') return 'danger'
  if(riskLevel.value === '警惕') return 'warning'
  return 'success'
})
</script>

<style scoped>
.student-home{
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
}
.info-card .header{
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.info-row{
  line-height: 2.2;
  font-size: 15px;
}
.label{
  color: #666;
  width: 80px;
  display: inline-block;
}
.suggest-card ul{
  padding-left: 20px;
  margin:0;
}
.suggest-card li{
  line-height: 2;
}
</style>
