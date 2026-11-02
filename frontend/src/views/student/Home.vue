<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { getSemesters, getStudentHomeInfo, getStudentSuggest, getStudentWarnList } from '../../api'
import { fmtPercent, fmtScore, riskType } from '../../utils/format'

const loading = ref(false)
const info = ref({})
const suggestions = ref([])
const warns = ref([])
const semesters = ref([])
const semester = ref('')
const chartRef = ref(null)
let chart = null

const trendSourceText = computed(() =>
  info.value.trend_source === 'platform_log' ? '来源：平台真实行为日志' : '来源：按当前指标估算的示意曲线'
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
  const params = { semester: semester.value || undefined }
  try {
    const [infoData, suggestData, warnData] = await Promise.all([
      getStudentHomeInfo(params),
      getStudentSuggest(params),
      getStudentWarnList(params)
    ])
    info.value = infoData || {}
    suggestions.value = suggestData?.items || []
    warns.value = warnData?.list || []
    await nextTick()
    renderChart()
  } catch {
    info.value = {}
  } finally {
    loading.value = false
  }
}

function renderChart() {
  const trend = info.value.attendance_trend || []
  if (!chartRef.value || !trend.length) return
  if (!chart) chart = echarts.init(chartRef.value)
  chart.setOption({
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

function handleResize() {
  chart?.resize()
}

onMounted(async () => {
  await loadSemesters()
  await load()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">我的学业预警</h2>
        <span class="muted">{{ info.semester || '—' }}</span>
      </div>
      <el-select v-model="semester" placeholder="学期" style="width: 160px" @change="load">
        <el-option v-for="s in semesters" :key="s" :label="s" :value="s" />
      </el-select>
    </div>

    <el-card shadow="never" v-loading="loading">
      <div class="profile">
        <div class="who">
          <div class="name">{{ info.name || '—' }}</div>
          <div class="muted">{{ info.student_no }} · {{ info.major }} · {{ info.class_name }}</div>
        </div>
        <div class="score-box">
          <el-tag :type="riskType(info.risk_level)" effect="dark" size="large">
            {{ info.risk_level_text || '—' }}
          </el-tag>
          <div class="avg">
            学期平均分 <strong>{{ fmtScore(info.avg_score) }}</strong>
          </div>
        </div>
      </div>

      <el-row :gutter="12" class="metrics">
        <el-col :xs="12" :sm="12" :md="6">
          <div class="metric">
            <span class="metric-label">出勤率</span>
            <span class="metric-value">{{ fmtPercent(info.metrics?.attendance_rate) }}</span>
          </div>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <div class="metric">
            <span class="metric-label">小测正确率</span>
            <span class="metric-value">{{ fmtPercent(info.metrics?.quiz_accuracy) }}</span>
          </div>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <div class="metric">
            <span class="metric-label">高危课程</span>
            <span class="metric-value">{{ info.high_course_count ?? 0 }}</span>
          </div>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <div class="metric">
            <span class="metric-label">不及格课程</span>
            <span class="metric-value">{{ info.fail_count ?? 0 }}</span>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <el-row :gutter="16">
      <el-col :xs="24" :md="12">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>出勤趋势</span>
              <span class="muted">每周</span>
            </div>
          </template>
          <div ref="chartRef" class="chart" />
          <p class="muted">{{ trendSourceText }}</p>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="suggest-card">
          <template #header>给你的建议</template>
          <ul v-if="suggestions.length" class="suggest">
            <li v-for="(item, i) in suggestions" :key="i">{{ item }}</li>
          </ul>
          <el-empty v-else description="暂无建议" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>我的预警记录</span>
          <span class="muted">共 {{ warns.length }} 条</span>
        </div>
      </template>
      <el-table :data="warns" stripe empty-text="当前学期没有预警记录">
        <el-table-column prop="course_name" label="课程" min-width="140" show-overflow-tooltip />
        <el-table-column label="周次" width="90" align="center">
          <template #default="{ row }">第{{ row.semester_week }}周</template>
        </el-table-column>
        <el-table-column label="等级" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="riskType(row.risk_level)" effect="light">{{ row.risk_level_text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="成绩" width="80" align="center">
          <template #default="{ row }">{{ fmtScore(row.final_score) }}</template>
        </el-table-column>
        <el-table-column prop="warn_time" label="时间" width="150" />
        <el-table-column prop="warn_reason" label="原因" min-width="260" show-overflow-tooltip />
      </el-table>
    </el-card>

    <el-card shadow="never">
      <template #header>当学期课程</template>
      <el-table :data="info.course_list || []" stripe empty-text="暂无课程数据">
        <el-table-column prop="course_code" label="课程代码" width="110" />
        <el-table-column prop="course_name" label="课程名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="credit" label="学分" width="70" align="center" />
        <el-table-column label="成绩" width="80" align="center">
          <template #default="{ row }">{{ fmtScore(row.score) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="riskType(row.risk_level)" effect="light" size="small">
              {{ row.risk_level_text }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.profile {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.name {
  font-size: 22px;
  font-weight: 600;
}

.score-box {
  display: flex;
  align-items: center;
  gap: 16px;
}

.avg {
  font-size: 13px;
  color: #5b6675;
}

.avg strong {
  color: #1f2a37;
  font-size: 16px;
}

.metrics {
  margin-top: 16px;
}

.metric {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  background: #f7f9fc;
  border-radius: 8px;
}

.metric-label {
  font-size: 13px;
  color: #5b6675;
}

.metric-value {
  font-size: 18px;
  font-weight: 600;
}

.chart {
  height: 200px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.suggest-card {
  min-height: 100%;
}

.suggest {
  margin: 0;
  padding-left: 18px;
  line-height: 1.9;
  color: #3c4757;
}

@media (max-width: 768px) {
  .metric {
    margin-bottom: 10px;
  }
}
</style>
