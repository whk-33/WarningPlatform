<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { getHomeStat, getSemesters } from '../../api'
import { fmtPercent } from '../../utils/format'

const loading = ref(false)
const stat = ref({})
const semesters = ref([])
const semester = ref('')
const chartRef = ref(null)
let chart = null

const cards = computed(() => [
  { label: '学生总数', value: stat.value.total_student, color: '#2f6bff', sub: '' },
  { label: '预警学生', value: stat.value.warn_count, color: '#e6a23c', sub: `占比 ${fmtPercent(stat.value.warn_rate)}` },
  { label: '高危学生', value: stat.value.high_count, color: '#f56c6c', sub: '需优先介入' },
  { label: '正常学生', value: stat.value.normal_count, color: '#67c23a', sub: '保持现有节奏' }
])

const hasData = computed(() => {
  const d = stat.value.risk_distribute || {}
  return Object.values(d).some(v => Number(v) > 0)
})

function valueOf(v) {
  return v === null || v === undefined ? '—' : v
}

async function loadSemesters() {
  try {
    const data = await getSemesters()
    semesters.value = data.list || []
    semester.value = data.current || data.list?.[0] || ''
  } catch {
    // 其它页面还会兜底显示
  }
}

async function loadStat() {
  loading.value = true
  try {
    stat.value = await getHomeStat({ semester: semester.value || undefined })
    await nextTick()
    renderChart()
  } catch {
    stat.value = {}
  } finally {
    loading.value = false
  }
}

function renderChart() {
  if (!chartRef.value) return
  if (!chart) chart = echarts.init(chartRef.value)
  const d = stat.value.risk_distribute || {}
  chart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} 人（{d}%）' },
    legend: { bottom: 0, itemWidth: 10, itemHeight: 10 },
    color: ['#f56c6c', '#e6a23c', '#67c23a'],
    series: [
      {
        type: 'pie',
        radius: ['45%', '68%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: true,
        label: { formatter: '{b}\n{c} 人' },
        data: [
          { name: '高危', value: d['高危'] || 0 },
          { name: '警惕', value: d['警惕'] || 0 },
          { name: '正常', value: d['正常'] || 0 }
        ]
      }
    ]
  })
}

function handleResize() {
  chart?.resize()
}

function onSemesterChange() {
  loadStat()
}

onMounted(async () => {
  await loadSemesters()
  await loadStat()
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
        <h2 class="page-title">数据概览</h2>
        <span class="muted">当前学期：{{ stat.semester || semester || '—' }}</span>
      </div>
      <div class="toolbar">
        <el-select v-model="semester" placeholder="选择学期" style="width: 170px" @change="onSemesterChange">
          <el-option v-for="s in semesters" :key="s" :label="s" :value="s" />
        </el-select>
        <el-button :loading="loading" @click="loadStat">刷新</el-button>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col v-for="card in cards" :key="card.label" :xs="24" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card" v-loading="loading">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value" :style="{ color: card.color }">{{ valueOf(card.value) }}</div>
          <div class="stat-sub">{{ card.sub }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>风险等级分布</span>
          <span class="muted">口径：学生当学期最高课程风险</span>
        </div>
      </template>
      <div v-show="hasData" ref="chartRef" class="chart" />
      <el-empty v-show="!hasData" description="暂无数据" />
    </el-card>
  </div>
</template>

<style scoped>
.stat-card {
  border-radius: 10px;
}

.stat-label {
  font-size: 13px;
  color: #5b6675;
}

.stat-value {
  font-size: 30px;
  font-weight: 600;
  line-height: 1.4;
}

.stat-sub {
  font-size: 12px;
  color: #8592a3;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.chart {
  height: 320px;
}

@media (max-width: 768px) {
  .stat-card {
    margin-bottom: 12px;
  }
}
</style>
