<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { getAdminRiskStat, getHomeStat, getSemesters } from '../../api'
import { fmtPercent } from '../../utils/format'

const loading = ref(false)
const stat = ref({})
const riskStat = ref({})
const semesters = ref([])
const semester = ref('')
const chartRef = ref(null)
let chart = null

const riskCards = computed(() => [
  { label: '高危', value: riskStat.value.level_2, color: '#f56c6c' },
  { label: '中风险', value: riskStat.value.level_1, color: '#e6a23c' },
  { label: '低风险', value: riskStat.value.level_0, color: '#67c23a' }
])

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

async function loadRiskStat() {
  try {
    riskStat.value = (await getAdminRiskStat({ semester: semester.value || undefined })) || {}
  } catch {
    riskStat.value = {}
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
  loadRiskStat()
}

onMounted(async () => {
  await loadSemesters()
  await loadStat()
  await loadRiskStat()
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

    <el-row :gutter="16">
      <el-col :xs="24" :md="14">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>风险等级分布</span>
              <span class="muted">口径：学期末结论（正常 / 警惕 / 高危）</span>
            </div>
          </template>
          <div v-show="hasData" ref="chartRef" class="chart" />
          <el-empty v-show="!hasData" description="暂无数据" />
        </el-card>
      </el-col>

      <el-col :xs="24" :md="10">
        <el-card shadow="never" v-loading="loading" class="risk-card">
          <template #header>
            <div class="card-head">
              <span>本周风险（实时预测）</span>
              <span class="muted">
                {{ riskStat.semester || semester || '—' }}
                <template v-if="riskStat.week !== undefined && riskStat.week !== null">
                  · 第 {{ riskStat.week + 1 }} 周
                </template>
              </span>
            </div>
          </template>

          <template v-if="riskStat.total">
            <div class="risk-total">
              <span class="risk-total-value">{{ riskStat.total }}</span>
              <span class="muted">条学生-课程记录</span>
            </div>
            <div class="risk-grid">
              <div v-for="item in riskCards" :key="item.label" class="risk-item">
                <div class="stat-label">{{ item.label }}</div>
                <div class="risk-value" :style="{ color: item.color }">{{ valueOf(item.value) }}</div>
              </div>
            </div>
            <div class="risk-foot">
              <el-tag type="warning" effect="plain" size="small">
                预警率 {{ fmtPercent(riskStat.alert_rate) }}
              </el-tag>
              <span class="muted">等级口径：低 / 中 / 高</span>
            </div>
            <p v-if="riskStat.sample_based" class="muted risk-note">
              当前为 mock 样本：列表实际只能翻
              {{ riskStat.list_available ?? 0 }} 条，总额按全量口径展示
            </p>
          </template>
          <el-empty v-else description="暂无风险数据" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>
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

.risk-card .risk-total {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
}

.risk-total-value {
  font-size: 26px;
  font-weight: 600;
  color: #1f2a37;
}

.risk-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.risk-item {
  padding: 10px 12px;
  background: #f7f9fc;
  border-radius: 8px;
}

.risk-value {
  font-size: 20px;
  font-weight: 600;
}

.risk-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 12px;
  font-size: 12px;
}

.risk-note {
  margin: 8px 0 0;
  font-size: 12px;
}

@media (max-width: 768px) {
  .stat-card {
    margin-bottom: 12px;
  }

  .risk-card {
    margin-top: 12px;
  }
}
</style>
