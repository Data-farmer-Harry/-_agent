<script setup lang="ts">
import type { PlanStep, TaskRoute, ToolObservation } from '../types/api'

interface Props {
  runId: string
  route: TaskRoute | null
  planSteps: PlanStep[]
  timeline: ToolObservation[]
  status: string
  terminationReason: string
  isLoading: boolean
}

const props = defineProps<Props>()

function stepStatusClass(status: string): string {
  return `status-${status}`
}
</script>

<template>
  <section class="trace-panel">
    <div class="panel-header">
      <div>
        <h2>Agent 过程</h2>
        <p>展示 route、计划步骤与 tool 时间线，不展示原始隐式推理。</p>
      </div>
      <span v-if="props.isLoading" class="status-live">实时更新中…</span>
    </div>

    <div class="summary-grid">
      <div class="summary-card">
        <span class="label">Run ID</span>
        <strong>{{ props.runId || '待分配' }}</strong>
      </div>
      <div class="summary-card">
        <span class="label">Route</span>
        <strong>{{ props.route?.name || '待路由' }}</strong>
      </div>
      <div class="summary-card">
        <span class="label">状态</span>
        <strong>{{ props.status || '待执行' }}</strong>
      </div>
      <div class="summary-card">
        <span class="label">终止原因</span>
        <strong>{{ props.terminationReason || '—' }}</strong>
      </div>
    </div>

    <details v-if="props.route?.reason" class="route-reason">
      <summary>Route reason</summary>
      <p>{{ props.route.reason }}</p>
    </details>

    <div class="section-block">
      <h3>计划步骤</h3>
      <ul class="step-list">
        <li v-for="step in props.planSteps" :key="step.index" :class="['step-item', stepStatusClass(step.status)]">
          <div class="step-top">
            <strong>Step {{ step.index }} · {{ step.tool_name }}</strong>
            <span class="badge">{{ step.status }}</span>
          </div>
          <p>{{ step.description || '无描述。' }}</p>
        </li>
      </ul>
      <p v-if="!props.planSteps.length" class="empty-text">暂无计划步骤。</p>
    </div>

    <div class="section-block">
      <h3>Tool 时间线</h3>
      <ul class="timeline-list">
        <li v-for="item in props.timeline" :key="`${item.step_index}-${item.tool_name}-${item.summary}`" class="timeline-item">
          <div class="step-top">
            <strong>Step {{ item.step_index }} · {{ item.tool_name }}</strong>
            <span class="badge" :class="item.success ? 'status-completed' : 'status-failed'">
              {{ item.success ? 'completed' : 'failed' }}
            </span>
          </div>
          <p>{{ item.summary }}</p>
        </li>
      </ul>
      <p v-if="!props.timeline.length" class="empty-text">运行开始后会在这里持续显示 tool 调用过程。</p>
    </div>
  </section>
</template>

<style scoped>
.trace-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 16px;
  padding: 18px;
  background: #ffffff;
  border: 1px solid #d9e0ea;
  border-radius: 14px;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.panel-header h2,
.section-block h3 {
  margin: 0;
}

.panel-header p,
.route-reason p,
.step-item p,
.timeline-item p,
.empty-text {
  margin: 6px 0 0;
  color: #52606d;
  font-size: 13px;
}

.status-live {
  color: #1565c0;
  font-size: 13px;
  font-weight: 600;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.summary-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  border: 1px solid #d9e0ea;
  border-radius: 12px;
  background: #f8fbff;
}

.label {
  font-size: 12px;
  color: #7b8794;
}

.route-reason {
  padding: 12px;
  border: 1px dashed #bcccdc;
  border-radius: 12px;
}

.step-list,
.timeline-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.step-item,
.timeline-item {
  padding: 12px;
  border: 1px solid #d9e0ea;
  border-radius: 12px;
  background: #fbfdff;
}

.step-top {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
}

.badge {
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 600;
  background: #e9eef5;
  color: #334e68;
}

.status-pending { border-color: #d9e2ec; }
.status-running { border-color: #93c5fd; background: #eff6ff; }
.status-completed { background: #dcfce7; color: #166534; }
.status-failed { background: #fee2e2; color: #991b1b; }
.status-skipped { border-color: #e5e7eb; background: #f8fafc; }

@media (max-width: 1080px) {
  .summary-grid {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
