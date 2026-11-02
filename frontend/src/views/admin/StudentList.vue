<script setup>
import { onMounted, reactive, ref } from 'vue'
import { getSemesters, getStudentDetail, getStudentList } from '../../api'
import { fmtPercent, fmtScore, riskType } from '../../utils/format'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const semesters = ref([])
const semester = ref('')

const query = reactive({
  keyword: '',
  risk_level: '',
  page: 1,
  page_size: 15
})

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref({})

async function loadSemesters() {
  try {
    const data = await getSemesters()
    semesters.value = data.list || []
  } catch {
    semesters.value = []
  }
}

async function load() {
  loading.value = true
  try {
    const data = await getStudentList({
      keyword: query.keyword.trim() || undefined,
      risk_level: query.risk_level === '' ? undefined : Number(query.risk_level),
      page: query.page,
      page_size: query.page_size,
      semester: semester.value || undefined
    })
    list.value = data.list || []
    total.value = data.total || 0
  } catch {
    list.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  query.page = 1
  load()
}

function handleReset() {
  query.keyword = ''
  query.risk_level = ''
  query.page = 1
  load()
}

function handlePageChange(page) {
  query.page = page
  load()
}

function handleSizeChange(size) {
  query.page_size = size
  query.page = 1
  load()
}

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = {}
  try {
    detail.value = await getStudentDetail({
      student_id: row.student_id,
      semester: semester.value || undefined
    })
  } catch {
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

onMounted(async () => {
  await loadSemesters()
  await load()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h2 class="page-title">学生管理</h2>
      <div class="toolbar">
        <el-select v-model="semester" placeholder="学期" style="width: 160px" @change="handleSearch">
          <el-option v-for="s in semesters" :key="s" :label="s" :value="s" />
        </el-select>
        <el-select v-model="query.risk_level" placeholder="风险等级" style="width: 120px" @change="handleSearch">
          <el-option label="全部" value="" />
          <el-option label="高危" :value="2" />
          <el-option label="警惕" :value="1" />
          <el-option label="正常" :value="0" />
        </el-select>
        <el-input
          v-model="query.keyword"
          placeholder="学号 / 姓名"
          clearable
          style="width: 200px"
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        />
        <el-button type="primary" @click="handleSearch">搜索</el-button>
        <el-button @click="handleReset">重置</el-button>
      </div>
    </div>

    <el-card shadow="never">
      <el-table v-loading="loading" :data="list" stripe height="560" empty-text="暂无数据">
        <el-table-column prop="student_no" label="学号" width="120" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="major" label="专业" min-width="150" show-overflow-tooltip />
        <el-table-column prop="class_name" label="班级" min-width="120" show-overflow-tooltip />
        <el-table-column label="平均分" width="90" align="center">
          <template #default="{ row }">{{ fmtScore(row.avg_score) }}</template>
        </el-table-column>
        <el-table-column label="风险等级" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="riskType(row.risk_level)" effect="light">{{ row.risk_level_text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="high_course_count" label="高危课程" width="100" align="center" />
        <el-table-column prop="course_count" label="课程数" width="90" align="center" />
        <el-table-column label="操作" width="110" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">查看详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          :current-page="query.page"
          :page-size="query.page_size"
          :page-sizes="[10, 15, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>

    <el-dialog v-model="detailVisible" title="学生详情" width="760px" destroy-on-close>
      <div v-loading="detailLoading">
        <el-descriptions v-if="detail.student_no" :column="3" border size="small">
          <el-descriptions-item label="学号">{{ detail.student_no }}</el-descriptions-item>
          <el-descriptions-item label="姓名">{{ detail.name }}</el-descriptions-item>
          <el-descriptions-item label="学期">{{ detail.semester }}</el-descriptions-item>
          <el-descriptions-item label="专业">{{ detail.major }}</el-descriptions-item>
          <el-descriptions-item label="班级">{{ detail.class_name }}</el-descriptions-item>
          <el-descriptions-item label="年级">{{ detail.grade }}</el-descriptions-item>
          <el-descriptions-item label="平均分">{{ fmtScore(detail.avg_score) }}</el-descriptions-item>
          <el-descriptions-item label="风险等级">
            <el-tag :type="riskType(detail.risk_level)" effect="light">{{ detail.risk_level_text }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="不及格课程">{{ detail.fail_count ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="出勤率">
            {{ fmtPercent(detail.metrics?.attendance_rate) }}
          </el-descriptions-item>
          <el-descriptions-item label="小测正确率">
            {{ fmtPercent(detail.metrics?.quiz_accuracy) }}
          </el-descriptions-item>
          <el-descriptions-item label="风险分">{{ detail.risk_score ?? 0 }}</el-descriptions-item>
        </el-descriptions>

        <h4 class="sub-title">当学期课程</h4>
        <el-table :data="detail.course_list || []" size="small" border empty-text="暂无课程数据">
          <el-table-column prop="course_code" label="课程代码" width="110" />
          <el-table-column prop="course_name" label="课程名称" min-width="150" show-overflow-tooltip />
          <el-table-column prop="credit" label="学分" width="70" align="center" />
          <el-table-column label="成绩" width="80" align="center">
            <template #default="{ row }">{{ fmtScore(row.score) }}</template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="80" align="center" />
          <el-table-column label="风险" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="riskType(row.risk_level)" size="small" effect="light">
                {{ row.risk_level_text }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.sub-title {
  margin: 18px 0 8px;
  font-size: 14px;
  font-weight: 600;
}
</style>
