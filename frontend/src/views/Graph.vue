<template>
  <div class="graph-container">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>GNN学生关系图可视化</span>
          <el-button type="primary" @click="refreshGraph">刷新图数据</el-button>
        </div>
      </template>
      <div id="container" ref="container" style="width:100%;height:600px;"></div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Graph } from '@antv/g6'

const container = ref(null)
let graph = null

// 模拟节点：学生，边：学习关联
const graphData = {
  nodes: [
    { id: 's1', label: '学生01', warn: 0 },
    { id: 's2', label: '学生02', warn: 1 },
    { id: 's3', label: '学生03', warn: 0 },
    { id: 's4', label: '学生04', warn: 1 },
    { id: 's5', label: '学生05', warn: 0 },
    { id: 's6', label: '学生06', warn: 1 },
  ],
  edges: [
    { source: 's1', target: 's2' },
    { source: 's1', target: 's3' },
    { source: 's2', target: 's4' },
    { source: 's3', target: 's5' },
    { source: 's4', target: 's6' },
    { source: 's2', target: 's5' },
  ]
}

const initGraph = () => {
  if(graph) graph.destroy()
  graph = new Graph({
    container: container.value,
    fitView: true,
    layout: {
      type: 'force',
      linkDistance: 120,
    },
    defaultNode: {
      size: 40,
      style: {
        fill: (d) => d.warn ? '#F56C6C' : '#67C23A',
      },
      labelCfg: {
        style: { fill: '#fff' }
      }
    },
    defaultEdge: {
      style: { stroke: '#aaa', lineWidth: 2 }
    }
  })
  graph.data(graphData)
  graph.render()
}

const refreshGraph = () => {
  initGraph()
}

onMounted(()=>{
  initGraph()
})
</script>

<style scoped>
.graph-container {
  margin-top: 10px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
