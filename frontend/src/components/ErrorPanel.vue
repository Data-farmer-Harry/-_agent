<script setup lang="ts">
interface Props {
  stdout: string
  stderr: string
}

const props = defineProps<Props>()
</script>

<template>
  <section class="panel">
    <div class="panel-header">
      <h2>执行日志</h2>
      <p>执行失败时可在此查看 stdout 和 stderr。</p>
    </div>

    <div class="log-block">
      <h3>stderr</h3>
      <pre>{{ props.stderr || '暂无错误输出。' }}</pre>
    </div>

    <div class="log-block">
      <h3>stdout</h3>
      <pre>{{ props.stdout || '暂无标准输出。' }}</pre>
    </div>
  </section>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  background: #ffffff;
  border: 1px solid #d9e0ea;
  border-radius: 12px;
}

.panel-header h2 {
  margin: 0;
  font-size: 18px;
}

.panel-header p {
  margin: 6px 0 0;
  color: #52606d;
  font-size: 13px;
}

.log-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.log-block h3 {
  margin: 0;
  font-size: 14px;
  color: #243b53;
}

pre {
  margin: 0;
  padding: 12px;
  min-height: 80px;
  max-height: 180px;
  overflow: auto;
  border-radius: 10px;
  background: #0f172a;
  color: #d6e3f0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
