<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import RiskCurve from '../../components/RiskCurve.vue'
import {
  getAdminRiskList,
  getAdminRiskStat,
  getSemesters,
  getStudentDetail,
  getStudentList
} from '../../api'
import { fmtPercent, fmtScore, predEventText, riskType } from '../../utils/format'

// 已发生事件的行不展示成"风险 100%"：risk_state=1 即已发生（契约必出字段，不再用 prob>=1 推断）
function isOccurred(row) {
  return Number(row?.risk_state) === 1
}

const tab = ref('final')

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

/* ---------------- 本周风险榜（阶段四 /admin/risk/*） ---------------- */

const WEEK_OPTIONS = Array.from({ length: 16 }, (_, i) => ({ label: `第${i + 1}周`, value: i }))

const riskLoading = ref(false)
const riskRows = ref([])
const riskStat = ref({})
const riskTotal = ref(0)
const riskQuery = reactive({ week: '', level: '', page: 1, size: 15 })


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

function handleSemesterChange() {
  handleSearch()
  riskQuery.page = 1
  if (tab.value === 'risk') loadRisk()
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

async function loadRisk() {
  riskLoading.value = true
  const params = {
    semester: semester.value || undefined,
    week: riskQuery.week === '' ? undefined : Number(riskQuery.week),
    level: riskQuery.level === '' ? undefined : Number(riskQuery.level),
    page: riskQuery.page,
    size: riskQuery.size
  }
  try {
    const [listData, statData] = await Promise.all([
      getAdminRiskList(params).catch(() => null),
      getAdminRiskStat({ semester: params.semester, week: params.week }).catch(() => null)
    ])
    riskRows.value = listData?.items ?? listData?.list ?? []
    riskStat.value = statData || {}
    // mock 下 total 是全量（7804），实际只有 20 条，翻页上限必须用 list_available
    riskTotal.value = statData?.list_available ?? listData?.total ?? 0
  } finally {
    riskLoading.value = false
  }
}

function handleRiskSearch() {
  riskQuery.page = 1
  loadRisk()
}

function handleRiskReset() {
  riskQuery.week = ''
  riskQuery.level = ''
  riskQuery.page = 1
  loadRisk()
}

function handleRiskPageChange(page) {
  riskQuery.page = page
  loadRisk()
}

function handleRiskSizeChange(size) {
  riskQuery.size = size
  riskQuery.page = 1
  loadRisk()
}

watch(tab, value => {
  if (value === 'risk' && !riskRows.value.length) loadRisk()
})

</script>

<template>
  <div class="page">
    <div class="page-head">
      <h2 class="page-title">学生管理</h2>
      <div class="toolbar">
        <el-select v-model="semester" placeholder="学期" style="width: 160px" @change="handleSemesterChange">
          <el-option v-for="s in semesters" :key="s" :label="s" :value="s" />
        </el-select>
      </div>
    </div>

    <el-card shadow="never">
      <el-tabs v-model="tab">
        <el-tab-pane label="学期末结论" name="final">
          <div class="toolbar filter-bar">
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

          <el-table v-loading="loading" :data="list" stripe height="520" empty-text="暂无数据">
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
        </el-tab-pane>

        <el-tab-pane label="本周风险榜" name="risk">
          <div class="toolbar filter-bar">
            <el-select v-model="riskQuery.week" placeholder="周次" style="width: 120px" @change="handleRiskSearch">
              <el-option label="最新" value="" />
              <el-option v-for="w in WEEK_OPTIONS" :key="w.value" :label="w.label" :value="w.value" />
            </el-select>
            <el-select v-model="riskQuery.level" placeholder="等级" style="width: 110px" @change="handleRiskSearch">
              <el-option label="全部" value="" />
              <el-option label="高" :value="2" />
              <el-option label="中" :value="1" />
              <el-option label="低" :value="0" />
            </el-select>
            <el-button type="primary" @click="handleRiskSearch">查询</el-button>
            <el-button @click="handleRiskReset">重置</el-button>
            <span class="muted risk-hint">
              <template v-if="riskStat.sample_based">
                样本演示：仅 {{ riskTotal }} 条可翻（全量口径 {{ riskStat.total ?? '—' }} 条）
              </template>
              <template v-else>全量数据</template>
            </span>
          </div>

          <el-table v-loading="riskLoading" :data="riskRows" stripe height="520" empty-text="暂无风险数据">
            <el-table-column prop="stu_id" label="学生 ID" width="140" />
            <el-table-column prop="course_code" label="课程" width="100" />
            <el-table-column label="周次" width="90" align="center">
              <template #default="{ row }">第{{ row.week + 1 }}周</template>
            </el-table-column>
            <el-table-column label="风险概率" width="120" align="center">
              <template #default="{ row }">
                <el-tag v-if="isOccurred(row)" type="info" effect="plain" size="small">已发生</el-tag>
                <span v-else>{{ fmtScore(row.risk_prob * 100, 1) }}%</span>
              </template>
            </el-table-column>
            <el-table-column label="等级" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="riskType(row.risk_level)" effect="light">{{ row.risk_level_name }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="预测事件" min-width="160">
              <template #default="{ row }">{{ predEventText(row.pred_event_week, row.pred_event_week_source) }}</template>
            </el-table-column>
          </el-table>

          <div class="pager">
            <el-pagination
              :current-page="riskQuery.page"
              :page-size="riskQuery.size"
              :page-sizes="[10, 15, 20, 50]"
              :total="riskTotal"
              layout="total, sizes, prev, pager, next, jumper"
              background
              @current-change="handleRiskPageChange"
              @size-change="handleRiskSizeChange"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="detailVisible" title="学生详情" width="860px" destroy-on-close>
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

        <h4 class="sub-title">16 周风险曲线（实时预测）</h4>
        <RiskCurve v-if="detail.stu_id" :stu-id="detail.stu_id" :semester="detail.semester || semester" />
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

.filter-bar {
  margin-bottom: 12px;
}

.risk-hint {
  font-size: 12px;
}
</style>
