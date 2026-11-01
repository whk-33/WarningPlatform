<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Graph } from '@antv/g6'
import { getGnnGraph, getSemesters } from '../../api'
import { fmtScore, riskType } from '../../utils/format'

const STUDENT_COLORS = ['#67c23a', '#e6a23c', '#f56c6c']

const loading = ref(false)
const meta = ref({})
const selected = ref(null)
const semester = ref('')
const semesters = ref([])
const limit = ref(50)
const courseCode = ref('')

const containerRef = ref(null)
let graph = null
let resizeObserver = null
const nodesById = new Map()

const metaText = computed(() => {
  const m = meta.value
  if (!m.node_count) return ''
  return `学期 ${m.semester} · 学生 ${m.student_count} · 节点 ${m.node_count} · 边 ${m.edge_count}`
})

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
  selected.value = null
  try {
    const data = await getGnnGraph({
      limit_students: limit.value,
      course_code: courseCode.value.trim() || undefined,
      semester: semester.value || undefined
    })
    meta.value = data.meta || {}
    nodesById.clear()
    ;(data.nodes || []).forEach(n => nodesById.set(n.id, n))
    await nextTick()
    render(data)
  } catch {
    meta.value = {}
  } finally {
    loading.value = false
  }
}

function colorOf(node) {
  if (node.type === 'course') return '#9aa7b8'
  return STUDENT_COLORS[Math.min(2, Number(node.risk_level) || 0)]
}

function render(data) {
  if (!containerRef.value) return
  if (graph) {
    graph.destroy()
    graph = null
  }

  graph = new Graph({
    container: containerRef.value,
    data: {
      nodes: (data.nodes || []).map(n => ({ id: n.id, data: n })),
      edges: (data.edges || []).map((e, i) => ({
        id: `e_${i}`,
        source: e.source,
        target: e.target,
        data: e
      }))
    },
    layout: { type: 'force', preventOverlap: true, nodeSize: 30 },
    node: {
      style: {
        size: d => (d.data?.type === 'student' ? 26 : 16),
        fill: d => colorOf(d.data || {}),
        stroke: '#ffffff',
        lineWidth: 1.5,
        labelText: d => d.data?.label || '',
        labelPlacement: 'bottom',
        labelFontSize: 10,
        labelFill: '#5b6675'
      }
    },
    edge: {
      style: {
        stroke: '#dbe2ec',
        lineWidth: 1,
        endArrow: false
      }
    },
    behaviors: ['zoom-canvas', 'drag-canvas', 'drag-element']
  })

  graph.on('node:click', event => {
    const id = event.target?.id
    selected.value = nodesById.get(id) || null
  })

  graph.render()
}

function handleResize() {
  graph?.resize()
}

onMounted(async () => {
  await loadSemesters()
  await load()
  resizeObserver = new ResizeObserver(handleResize)
  if (containerRef.value) resizeObserver.observe(containerRef.value)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  graph?.destroy()
  graph = null
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h2 class="page-title">GNN 分析</h2>
      <div class="toolbar">
        <el-select v-model="semester" placeholder="学期" style="width: 160px" @change="load">
          <el-option v-for="s in semesters" :key="s" :label="s" :value="s" />
        </el-select>
        <el-select v-model="limit" style="width: 150px" @change="load">
          <el-option :value="30" label="Top 30 高风险" />
          <el-option :value="50" label="Top 50 高风险" />
          <el-option :value="100" label="Top 100 高风险" />
        </el-select>
        <el-input
          v-model="courseCode"
          placeholder="课程代码（可选）"
          clearable
          style="width: 180px"
          @keyup.enter="load"
          @clear="load"
        />
        <el-button type="primary" :loading="loading" @click="load">加载图</el-button>
      </div>
    </div>

    <el-alert
      v-if="meta.truncated"
      type="info"
      :closable="false"
      show-icon
      title="学生数超过上限，当前只展示风险最高的部分学生。可调小范围或按课程筛选。"
      class="tip"
    />

    <el-row :gutter="16">
      <el-col :xs="24" :md="17">
        <el-card shadow="never" class="graph-card">
          <template #header>
            <div class="card-head">
              <span>学生-课程异质图</span>
              <span class="muted">{{ metaText }}</span>
            </div>
          </template>
          <div v-loading="loading" class="graph-wrap">
            <div ref="containerRef" class="graph-canvas" />
            <div class="legend">
              <span><i class="dot" style="background: #f56c6c" />高危</span>
              <span><i class="dot" style="background: #e6a23c" />警惕</span>
              <span><i class="dot" style="background: #67c23a" />正常</span>
              <span><i class="dot" style="background: #9aa7b8" />课程</span>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="7">
        <el-card shadow="never" class="side-card">
          <template #header>节点详情</template>
          <el-empty v-if="!selected" description="点击图中的节点查看信息" :image-size="70" />
          <el-descriptions v-else :column="1" size="small" border>
            <el-descriptions-item label="类型">
              {{ selected.type === 'student' ? '学生' : '课程' }}
            </el-descriptions-item>
            <el-descriptions-item label="名称">{{ selected.label }}</el-descriptions-item>
            <template v-if="selected.type === 'student'">
              <el-descriptions-item label="学号">{{ selected.student_no }}</el-descriptions-item>
              <el-descriptions-item label="风险等级">
                <el-tag :type="riskType(selected.risk_level)" size="small" effect="light">
                  {{ selected.risk_level_text }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="风险分">{{ fmtScore(selected.risk_score, 2) }}</el-descriptions-item>
            </template>
            <template v-else>
              <el-descriptions-item label="课程代码">{{ selected.course_code }}</el-descriptions-item>
              <el-descriptions-item label="学分">{{ selected.credit }}</el-descriptions-item>
              <el-descriptions-item v-if="selected.score !== undefined" label="成绩">
                {{ fmtScore(selected.score) }}
              </el-descriptions-item>
            </template>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.tip {
  margin-bottom: 4px;
}

.graph-card,
.side-card {
  border-radius: 10px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.graph-wrap {
  position: relative;
}

.graph-canvas {
  width: 100%;
  height: 560px;
}

.legend {
  position: absolute;
  left: 12px;
  bottom: 12px;
  display: flex;
  gap: 14px;
  padding: 6px 10px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid #eef1f6;
  border-radius: 8px;
  font-size: 12px;
  color: #5b6675;
}

.legend span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  display: inline-block;
}

.side-card {
  min-height: 560px;
}
</style>
