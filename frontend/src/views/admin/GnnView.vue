<template>
  <el-card shadow="hover">
    <h2>GNN图可视化</h2>
    <p>学生-课程关系网络图，节点：学生/课程；连线：选课关系</p>
    <el-divider />
    <div ref="container" style="width:100%;height:520px;border:1px solid #eee;"></div>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { Graph } from '@antv/g6'
const container = ref(null)
let graph = null

onMounted(() => {
  const graphData = {
    nodes: [
      { id: 'stu1', data: { label: '张三', type: 'student', risk: '正常' } },
      { id: 'stu2', data: { label: '李四', type: 'student', risk: '警惕' } },
      { id: 'stu3', data: { label: '王五', type: 'student', risk: '高危' } },
      { id: 'c1', data: { label: '数据结构', type: 'course', credit: 4 } },
      { id: 'c2', data: { label: '机器学习', type: 'course', credit: 3 } },
      { id: 'c3', data: { label: '高等数学', type: 'course', credit: 5 } },
    ],
    edges: [
      { source: 'stu1', target: 'c1' },
      { source: 'stu1', target: 'c2' },
      { source: 'stu2', target: 'c1' },
      { source: 'stu2', target: 'c3' },
      { source: 'stu3', target: 'c2' },
      { source: 'stu3', target: 'c3' },
    ]
  }

  graph = new Graph({
    container: container.value,
    width: container.value.clientWidth,
    height: container.value.clientHeight,
    data: graphData,
    layout: { type: 'force' },
    node: {
      style: (d) => {
        let fillColor = '#409eff'
        if(d.data.type === 'student'){
          if(d.data.risk === '高危') fillColor = '#f56c6c'
          else if(d.data.risk === '警惕') fillColor = '#e6a23c'
        }
        return {
          size: 35,
          fill: fillColor,
          labelText: d.data.label,
          labelFontSize: 12
        }
      }
    }
  })

  graph.render()

  graph.on('node:mouseenter', (e) => {
    const model = e.item.getData()
    let tip = ''
    if(model.type === 'student'){
      tip = `姓名：${model.label}\n风险等级：${model.risk}`
    }else{
      tip = `课程：${model.label}\n学分：${model.credit}`
    }
    console.log(tip)
  })

})

onUnmounted(()=>{
  if(graph) graph.destroy()
})
</script>

<style scoped>

</style>
