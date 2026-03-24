<script setup lang="ts">
interface Props {
  systemName: string
  notes: string
}

interface Emits {
  (event: 'update:systemName', value: string): void
  (event: 'update:notes', value: string): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()
</script>

<template>
  <section class="panel">
    <div class="panel-header">
      <h2>体系输入</h2>
      <p>输入材料体系与附加需求。</p>
    </div>

    <label class="field">
      <span>材料体系</span>
      <input
        :value="props.systemName"
        type="text"
        placeholder="例如：Fe-C 二元相图"
        @input="emit('update:systemName', ($event.target as HTMLInputElement).value)"
      />
    </label>

    <label class="field">
      <span>附加说明</span>
      <textarea
        :value="props.notes"
        rows="4"
        placeholder="例如：优先展示温度-成分关系，保留后续 pycalphad 接口"
        @input="emit('update:notes', ($event.target as HTMLTextAreaElement).value)"
      />
    </label>
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

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field span {
  font-weight: 600;
  color: #243b53;
}

input,
textarea {
  width: 100%;
  border: 1px solid #bcccdc;
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 14px;
  box-sizing: border-box;
}

textarea {
  resize: vertical;
}
</style>
