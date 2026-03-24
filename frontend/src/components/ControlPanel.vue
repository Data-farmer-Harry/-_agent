<script setup lang="ts">
import type { DiagramType } from '../types/api'

interface Props {
  diagramType: DiagramType
  temperatureMin: number
  temperatureMax: number
  pressure: number
  stepSize: number
}

interface Emits {
  (event: 'update:diagramType', value: DiagramType): void
  (event: 'update:temperatureMin', value: number): void
  (event: 'update:temperatureMax', value: number): void
  (event: 'update:pressure', value: number): void
  (event: 'update:stepSize', value: number): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

function emitTemperatureMin(value: string) {
  emit('update:temperatureMin', Number(value))
}

function emitTemperatureMax(value: string) {
  emit('update:temperatureMax', Number(value))
}

function emitPressure(value: string) {
  emit('update:pressure', Number(value))
}

function emitStepSize(value: string) {
  emit('update:stepSize', Number(value))
}
</script>

<template>
  <section class="panel">
    <div class="panel-header">
      <h2>参数控制</h2>
      <p>通过数值输入和滑块联动调整计算区间。</p>
    </div>

    <div class="field">
      <span>相图类型</span>
      <div class="segmented">
        <button
          type="button"
          :class="['segment', { active: props.diagramType === 'binary' }]"
          @click="emit('update:diagramType', 'binary')"
        >
          Binary
        </button>
        <button
          type="button"
          :class="['segment', { active: props.diagramType === 'ternary' }]"
          @click="emit('update:diagramType', 'ternary')"
        >
          Ternary
        </button>
      </div>
    </div>

    <div class="field-grid">
      <label class="field">
        <span>温度下限 (K)</span>
        <input
          :value="props.temperatureMin"
          type="number"
          min="0"
          max="4000"
          step="10"
          @input="emitTemperatureMin(($event.target as HTMLInputElement).value)"
        />
        <input
          :value="props.temperatureMin"
          type="range"
          min="0"
          max="3500"
          step="10"
          @input="emitTemperatureMin(($event.target as HTMLInputElement).value)"
        />
      </label>

      <label class="field">
        <span>温度上限 (K)</span>
        <input
          :value="props.temperatureMax"
          type="number"
          min="100"
          max="4000"
          step="10"
          @input="emitTemperatureMax(($event.target as HTMLInputElement).value)"
        />
        <input
          :value="props.temperatureMax"
          type="range"
          min="100"
          max="4000"
          step="10"
          @input="emitTemperatureMax(($event.target as HTMLInputElement).value)"
        />
      </label>

      <label class="field">
        <span>压力 (Pa)</span>
        <input
          :value="props.pressure"
          type="number"
          min="0"
          step="1000"
          @input="emitPressure(($event.target as HTMLInputElement).value)"
        />
      </label>

      <label class="field">
        <span>步长</span>
        <input
          :value="props.stepSize"
          type="number"
          min="1"
          max="500"
          step="1"
          @input="emitStepSize(($event.target as HTMLInputElement).value)"
        />
        <input
          :value="props.stepSize"
          type="range"
          min="1"
          max="250"
          step="1"
          @input="emitStepSize(($event.target as HTMLInputElement).value)"
        />
      </label>
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

.field-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
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

input {
  width: 100%;
  box-sizing: border-box;
}

input[type='number'] {
  border: 1px solid #bcccdc;
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 14px;
}

.segmented {
  display: inline-flex;
  gap: 8px;
}

.segment {
  border: 1px solid #bcccdc;
  background: #f8fbff;
  border-radius: 999px;
  padding: 8px 14px;
  cursor: pointer;
}

.segment.active {
  background: #1565c0;
  color: #ffffff;
  border-color: #1565c0;
}

@media (max-width: 960px) {
  .field-grid {
    grid-template-columns: 1fr;
  }
}
</style>
