<template>
  <el-card shadow="hover">
    <template #header>
      <span>GNN模型学业预警结果</span>
    </template>
    <el-alert title="预警说明" type="info" description="红色标记为GNN模型识别出的高风险学生，存在挂科、辍学风险，建议重点干预。" show-icon />
    <el-divider />
    <el-table :data="warnData" border stripe>
      <el-table-column prop="id" label="学号"/>
      <el-table-column prop="name" label="姓名"/>
      <el-table-column prop="gcnScore" label="GCN预测分数"/>
      <el-table-column prop="prob" label="风险概率"/>
      <el-table-column prop="suggest" label="干预建议"/>
    </el-table>
  </el-card>
</template>

<script setup>
const warnData = [
  {
    id: '2024001',
    name: '张三',
    gcnScore: 0.86,
    prob: '86%',
    suggest: '安排补课、定期谈心'
  },
  {
    id: '2024004',
    name: '赵六',
    gcnScore: 0.79,
    prob: '79%',
    suggest: '联系家长，跟进出勤'
  }
]
</script>
