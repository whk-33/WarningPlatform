<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import * as echarts from 'echarts'
import { getSemesters, getWarnList, getWarnReport } from '../../api'
import { fmtPercent, fmtScore, riskType } from '../../utils/format'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const semesters = ref([])
const semester = ref('')
const range = ref([])

const query = reactive({
  risk_level: '',
  page: 1,
  page_size: 15
})

const reportDialog = ref(false)
const reportLoading = ref(false)
const report = ref({})
const trendRef = ref(null)
let trendChart = null

const trendSourceText = computed(() =>
  report.value.trend_source === 'platform_log' ? '来源：平台真实行为日志' : '来源：按当前指标估算的示意曲线'
)

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
    const data = await getWarnList({
      start_time: range.value?.[0] || undefined,
      end_time: range.value?.[1] || undefined,
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
  range.value = []
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

function renderTrend() {
  if (!trendRef.value) return
  const trend = report.value.trend || []
  if (!trend.length) return
  if (!trendChart) trendChart = echarts.init(trendRef.value)
  trendChart.setOption({
    grid: { left: 40, right: 16, top: 24, bottom: 28 },
    tooltip: { trigger: 'axis', valueFormatter: v => `${(v * 100).toFixed(0)}%` },
    xAxis: {
      type: 'category',
      data: trend.map(t => `第${t.week}周`),
      axisLine: { lineStyle: { color: '#dfe4ec' } }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 1,
      axisLabel: { formatter: v => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: '#f0f3f7' } }
    },
    series: [
      {
        type: 'line',
        smooth: true,
        symbolSize: 6,
        data: trend.map(t => t.attendance_rate),
        lineStyle: { width: 2, color: '#2f6bff' },
        itemStyle: { color: '#2f6bff' },
        areaStyle: { color: 'rgba(47,107,255,0.08)' }
      }
    ]
  })
}

async function openReport(row) {
  reportDialog.value = true
  reportLoading.value = true
  report.value = {}
  try {
    report.value = await getWarnReport({ warn_id: row.warn_id })
    await nextTick()
    renderTrend()
  } catch {
    reportDialog.value = false
  } finally {
    reportLoading.value = false
  }
}

function handleDialogClosed() {
  trendChart?.dispose()
  trendChart = null
}

onMounted(async () => {
  await loadSemesters()
  await load()
})

onBeforeUnmount(() => {
  trendChart?.dispose()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h2 class="page-title">预警结果</h2>
      <div class="toolbar">
        <el-select v-model="semester" placeholder="学期" style="width: 160px" @change="handleSearch">
          <el-option v-for="s in semesters" :key="s" :label="s" :value="s" />
        </el-select>
        <el-date-picker
          v-model="range"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          style="width: 260px"
          @change="handleSearch"
        />
        <el-select v-model="query.risk_level" placeholder="风险等级" style="width: 120px" @change="handleSearch">
          <el-option label="全部" value="" />
          <el-option label="高危" :value="2" />
          <el-option label="警惕" :value="1" />
        </el-select>
        <el-button type="primary" @click="handleSearch">查询</el-button>
        <el-button @click="handleReset">重置</el-button>
      </div>
    </div>

    <el-card shadow="never">
      <el-table v-loading="loading" :data="list" stripe height="560" empty-text="该条件下暂无预警记录">
        <el-table-column prop="warn_id" label="预警编号" width="160" show-overflow-tooltip />
        <el-table-column prop="student_no" label="学号" width="110" />
        <el-table-column prop="name" label="姓名" width="90" />
        <el-table-column prop="course_name" label="课程" min-width="130" show-overflow-tooltip />
        <el-table-column label="周次" width="80" align="center">
          <template #default="{ row }">第{{ row.semester_week }}周</template>
        </el-table-column>
        <el-table-column prop="warn_time" label="预警时间" width="150" />
        <el-table-column label="等级" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="riskType(row.risk_level)" effect="light">{{ row.risk_level_text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="成绩" width="80" align="center">
          <template #default="{ row }">{{ fmtScore(row.final_score) }}</template>
        </el-table-column>
        <el-table-column prop="warn_reason" label="预警原因" min-width="240" show-overflow-tooltip />
        <el-table-column label="操作" width="110" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openReport(row)">分析报告</el-button>
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

    <el-dialog
      v-model="reportDialog"
      title="预警分析报告"
      width="820px"
      destroy-on-close
      @closed="handleDialogClosed"
    >
      <div v-loading="reportLoading" class="report">
        <template v-if="report.warn_id">
          <el-alert :title="report.summary" type="warning" :closable="false" show-icon class="summary" />

          <el-descriptions :column="3" border size="small" class="report-desc">
            <el-descriptions-item label="学生">
              {{ report.student?.name }}（{{ report.student?.student_no }}）
            </el-descriptions-item>
            <el-descriptions-item label="课程">{{ report.course?.course_name }}</el-descriptions-item>
            <el-descriptions-item label="课程成绩">
              {{ fmtScore(report.course?.final_score) }}
            </el-descriptions-item>
            <el-descriptions-item label="预警时间">{{ report.course?.warn_time }}</el-descriptions-item>
            <el-descriptions-item label="出勤率">
              {{ fmtPercent(report.student?.metrics?.attendance_rate) }}
            </el-descriptions-item>
            <el-descriptions-item label="小测正确率">
              {{ fmtPercent(report.student?.metrics?.quiz_accuracy) }}
            </el-descriptions-item>
          </el-descriptions>

          <h4 class="sub-title">关键风险因子</h4>
          <el-table :data="report.factors || []" size="small" border empty-text="暂无因子">
            <el-table-column prop="label" label="因子" min-width="140" />
            <el-table-column prop="value" label="当前值" width="110" align="center" />
            <el-table-column prop="threshold" label="阈值" width="90" align="center" />
            <el-table-column label="贡献度" width="110" align="center">
              <template #default="{ row }">{{ fmtPercent(row.contribution) }}</template>
            </el-table-column>
            <el-table-column label="方向" width="110" align="center">
              <template #default="{ row }">
                {{ row.direction === 'higher_is_worse' ? '越高越差' : '越低越差' }}
              </template>
            </el-table-column>
          </el-table>
          <p class="muted">归因来源：{{ report.source === 'model' ? '模型解释' : '规则判定' }}</p>

          <h4 class="sub-title">出勤趋势</h4>
          <div ref="trendRef" class="trend" />
          <p class="muted">{{ trendSourceText }}</p>

          <h4 class="sub-title">学业建议</h4>
          <ul class="suggest">
            <li v-for="(item, i) in report.suggestions || []" :key="i">{{ item }}</li>
          </ul>
        </template>
        <el-empty v-else description="报告生成中" />
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

.summary {
  margin-bottom: 12px;
}

.report-desc {
  margin-bottom: 4px;
}

.trend {
  height: 180px;
}

.suggest {
  margin: 0;
  padding-left: 18px;
  line-height: 1.9;
  color: #3c4757;
}
</style>
