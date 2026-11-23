<script setup>
/**
 * 阶段四风险曲线（16 周）+ 选中周归因条形图。
 *
 * 契约要点（docs/前端接入说明.md §4）：
 * - week 是 0 起，横轴标签写 week + 1
 * - pred_event_week === -1：概率 <0.5 显示"本学期内无风险"，概率 >=0.5 显示"暂无预计周"
 * - risk_prob 是 0-1 小数，展示转百分数
 * - 等级文案用 risk_level_name（低/中/高），不与旧页面 risk_level_text 混用
 * - contrib 总和可能 < 1，条形长度按最大值缩放，数值照实显示
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { getRiskCurve } from '../api'
import { fmtPercent, predEventText, riskType } from '../utils/format'

const props = defineProps({
  stuId: { type: String, default: '' },
  semester: { type: String, default: '' },
  height: { type: Number, default: 240 }
})

const LEVEL_COLORS = ['#67c23a', '#e6a23c', '#f56c6c']

// 已发生事件的学生不渲染成"风险 100%"：risk_state=1 即已发生（契约必出字段，不再用 prob>=1 推断）
function isOccurred(p) {
  return Number(p?.risk_state) === 1
}

const loading = ref(false)
const error = ref('')
const weeks = ref([])
const active = ref(null)
const chartRef = ref(null)
let chart = null
let observer = null

const maxContrib = computed(() =>
  Math.max(...(active.value?.top_factors || []).map(f => Number(f.contrib) || 0), 0)
)

async function load() {
  if (!props.stuId) return
  loading.value = true
  error.value = ''
  weeks.value = []
  active.value = null
  chart?.clear()
  try {
    let data
    try {
      data = await getRiskCurve(props.stuId, { semester: props.semester || undefined }, { silent: true })
    } catch (e) {
      // 学生库学期（如 2025S）与风险数据源学期（如 2014B）不一致时，去掉学期重试一次
      if (!props.semester || e?.response?.status !== 400) throw e
      data = await getRiskCurve(props.stuId, {}, { silent: true })
    }
    weeks.value = (data?.weeks || []).slice().sort((a, b) => a.week - b.week)
    active.value = weeks.value.length ? weeks.value[weeks.value.length - 1] : null
    await nextTick()
    render()
  } catch (e) {
    error.value = e?.response?.data?.msg || e?.message || '暂无风险数据'
  } finally {
    loading.value = false
  }
}

function render() {
  if (!chartRef.value || !weeks.value.length) return
  if (!chart) chart = echarts.init(chartRef.value)
  chart.setOption(
    {
      grid: { left: 44, right: 18, top: 20, bottom: 30 },
      tooltip: {
        trigger: 'axis',
        formatter: params => {
          const p = weeks.value[params[0].dataIndex]
          if (!p) return ''
          const lines = [
            `第 ${p.week + 1} 周`,
            `风险概率：${isOccurred(p) ? '已发生' : fmtPercent(p.risk_prob)}`,
            `等级：${p.risk_level_name || '—'}`,
            `预测事件：${predEventText(p.pred_event_week, p.pred_event_week_source)}`
          ]
          if (p.top_factors?.length) {
            lines.push('主要因子：')
            p.top_factors.forEach(f => lines.push(`　${f.factor} ${fmtPercent(f.contrib)}`))
          }
          return lines.join('<br/>')
        }
      },
      xAxis: {
        type: 'category',
        data: weeks.value.map(w => `第${w.week + 1}周`),
        axisLine: { lineStyle: { color: '#dfe4ec' } },
        axisLabel: { color: '#8592a3' }
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: 1,
        axisLabel: { formatter: v => `${(v * 100).toFixed(0)}%`, color: '#8592a3' },
        splitLine: { lineStyle: { color: '#f0f3f7' } }
      },
      series: [
        {
          type: 'line',
          smooth: true,
          symbolSize: 7,
          data: weeks.value.map(w => ({
            value: w.risk_prob,
            itemStyle: { color: LEVEL_COLORS[Number(w.risk_level)] || '#2f6bff' }
          })),
          lineStyle: { width: 2, color: '#2f6bff' },
          areaStyle: { color: 'rgba(47,107,255,0.08)' }
        }
      ]
    },
    true
  )
  chart.off('click')
  chart.on('click', params => {
    active.value = weeks.value[params.dataIndex] || null
  })
}

function handleResize() {
  chart?.resize()
}

watch(() => [props.stuId, props.semester], load)

onMounted(() => {
  load()
  window.addEventListener('resize', handleResize)
  if (chartRef.value && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(handleResize)
    observer.observe(chartRef.value)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div v-loading="loading" class="risk-curve">
    <div v-show="weeks.length" ref="chartRef" :style="{ height: `${height}px` }" />

    <el-empty v-if="!loading && !weeks.length" :description="error || '暂无风险数据'" :image-size="60" />

    <div v-if="weeks.length" class="legend">
      <span v-for="(color, i) in LEVEL_COLORS" :key="i" class="legend-item">
        <i class="dot" :style="{ background: color }" />{{ ['低', '中', '高'][i] }}
      </span>
      <span class="muted legend-note">点击折线上的点查看该周归因</span>
    </div>

    <div v-if="active?.top_factors?.length" class="factors">
      <div class="factors-head">
        <span class="factors-title">第 {{ active.week + 1 }} 周归因</span>
        <el-tag :type="riskType(active.risk_level)" size="small" effect="light">
          {{ active.risk_level_name }}
        </el-tag>
        <span class="muted">{{ active.course_code }}</span>
      </div>
      <div v-for="f in active.top_factors" :key="f.factor" class="factor-row">
        <span class="factor-name">{{ f.factor }}</span>
        <div class="factor-track">
          <div
            class="factor-bar"
            :style="{ width: maxContrib ? `${(Number(f.contrib) / maxContrib) * 100}%` : '0%' }"
          />
        </div>
        <span class="factor-val">{{ fmtPercent(f.contrib) }}</span>
      </div>
      <p class="muted factor-note">条形长度按贡献度相对大小绘制，各项合计可能不足 100%</p>
    </div>
  </div>
</template>

<style scoped>
.risk-curve {
  min-height: 120px;
}

.legend {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 12px;
  color: #5b6675;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.legend-note {
  margin-left: auto;
  font-size: 12px;
}

.factors {
  margin-top: 12px;
  padding: 12px 14px;
  background: #f7f9fc;
  border-radius: 8px;
}

.factors-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.factors-title {
  font-size: 13px;
  font-weight: 600;
}

.factor-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
  font-size: 12px;
}

.factor-name {
  width: 92px;
  color: #3c4757;
  flex: none;
}

.factor-track {
  flex: 1;
  height: 8px;
  background: #e8eef8;
  border-radius: 4px;
  overflow: hidden;
}

.factor-bar {
  height: 100%;
  background: #2f6bff;
  border-radius: 4px;
}

.factor-val {
  width: 46px;
  text-align: right;
  color: #5b6675;
  flex: none;
}

.factor-note {
  margin: 6px 0 0;
  font-size: 12px;
}
</style>
