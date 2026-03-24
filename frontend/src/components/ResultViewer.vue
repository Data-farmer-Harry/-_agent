<script setup lang="ts">
interface Props {
  htmlContent: string
  isLoading: boolean
}

const props = defineProps<Props>()
</script>

<template>
  <section class="viewer">
    <div class="viewer-header">
      <h2>实验室结果</h2>
      <span v-if="props.isLoading" class="status">正在生成并执行…</span>
    </div>

    <div v-if="props.htmlContent" class="iframe-shell">
      <iframe :srcdoc="props.htmlContent" title="Phase Diagram Result" sandbox="allow-scripts allow-same-origin" />
    </div>
    <div v-else class="empty-state">
      <p>暂无结果。请在左侧输入体系并点击“生成并运行”。</p>
    </div>
  </section>
</template>

<style scoped>
.viewer {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 720px;
  padding: 18px;
  background: #ffffff;
  border: 1px solid #d9e0ea;
  border-radius: 14px;
}

.viewer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.viewer-header h2 {
  margin: 0;
  font-size: 20px;
}

.status {
  font-size: 13px;
  color: #1565c0;
}

.iframe-shell {
  flex: 1;
  min-height: 0;
  border: 1px solid #d9e0ea;
  border-radius: 12px;
  overflow: hidden;
}

iframe {
  width: 100%;
  height: 100%;
  min-height: 650px;
  border: 0;
  background: #ffffff;
}

.empty-state {
  display: grid;
  place-items: center;
  flex: 1;
  min-height: 650px;
  border: 1px dashed #bcccdc;
  border-radius: 12px;
  color: #52606d;
  background: #f8fbff;
  text-align: center;
  padding: 20px;
}
</style>
