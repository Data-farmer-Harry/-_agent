<script setup lang="ts">
import type { ClientSettings } from '../types/api'

interface Props {
  settings: ClientSettings
}

interface Emits {
  (event: 'reset'): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()
</script>

<template>
  <section class="panel">
    <div class="panel-header">
      <h2>接口设置</h2>
      <p>优先使用 localStorage 中的配置，修改后立即生效。</p>
    </div>

    <div class="field-grid">
      <label class="field">
        <span>API Base URL</span>
        <input :value="props.settings.apiBaseUrl" type="text" @input="props.settings.apiBaseUrl = ($event.target as HTMLInputElement).value" />
      </label>
      <label class="field">
        <span>Generate Path</span>
        <input :value="props.settings.generatePath" type="text" @input="props.settings.generatePath = ($event.target as HTMLInputElement).value" />
      </label>
      <label class="field">
        <span>Run Path</span>
        <input :value="props.settings.runPath" type="text" @input="props.settings.runPath = ($event.target as HTMLInputElement).value" />
      </label>
      <label class="field">
        <span>Generate & Run Path</span>
        <input :value="props.settings.generateAndRunPath" type="text" @input="props.settings.generateAndRunPath = ($event.target as HTMLInputElement).value" />
      </label>
      <label class="field">
        <span>Timeout (ms)</span>
        <input :value="props.settings.requestTimeoutMs" type="number" min="1000" step="1000" @input="props.settings.requestTimeoutMs = Number(($event.target as HTMLInputElement).value)" />
      </label>
      <label class="toggle-row">
        <span>启用自动重试</span>
        <input :checked="props.settings.enableAutoRetry" type="checkbox" @change="props.settings.enableAutoRetry = ($event.target as HTMLInputElement).checked" />
      </label>
    </div>

    <button class="reset-button" type="button" @click="emit('reset')">恢复默认配置</button>
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

.field-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.field,
.toggle-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field span,
.toggle-row span {
  font-weight: 600;
  color: #243b53;
}

input[type='text'],
input[type='number'] {
  border: 1px solid #bcccdc;
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 14px;
}

.toggle-row {
  justify-content: center;
}

.toggle-row input {
  width: 18px;
  height: 18px;
}

.reset-button {
  align-self: flex-start;
  border: none;
  border-radius: 10px;
  padding: 10px 14px;
  background: #eef5ff;
  color: #0b4f93;
  cursor: pointer;
}

@media (max-width: 960px) {
  .field-grid {
    grid-template-columns: 1fr;
  }
}
</style>
