<template>
  <el-card shadow="hover">
    <h2>GNN学业预警平台</h2>
    <p>基于图神经网络的学生学业风险预警系统</p>
    <el-divider />
    <el-row :gutter="20">
      <el-col :span="8">
        <el-statistic title="总学生数" :value="126" />
      </el-col>
      <el-col :span="8">
        <el-statistic title="预警人数" :value="23" value-style="color:#F56C6C"/>
      </el-col>
      <el-col :span="8">
        <el-statistic title="正常学生" :value="103" value-style="color:#67C23A"/>
      </el-col>
    </el-row>
    <el-divider />
    <div ref="pieRef" style="width: 100%;height:360px;"></div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import * as echarts from 'echarts'

const pieRef = ref(null)
let chart = null

onMounted(() => {
  chart = echarts.init(pieRef.value)
  const option = {
    title: { text: '学生风险分布' },
    tooltip: {},
    series: [
      {
        type: 'pie',
        radius: '60%',
        data: [
          { name: '正常', value: 103, itemStyle:{color:'#67C23A'} },
          { name: '警惕', value: 14, itemStyle:{color:'#E6A23C'} },
          { name: '高危', value: 9, itemStyle:{color:'#F56C6C'} }
        ]
      }
    ]
  }
  chart.setOption(option)
})
</script>
