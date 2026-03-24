<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import ControlPanel from './components/ControlPanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import ErrorPanel from './components/ErrorPanel.vue'
import ResultViewer from './components/ResultViewer.vue'
import AgentTracePanel from './components/AgentTracePanel.vue'
import { useLocalSettings } from './composables/useLocalSettings'
import { generateAndRun, getLatestResultHtml, getRunResultHtml, streamGenerateAndRun } from './services/api'
import type { AgentStreamEvent, DiagramRequest, DiagramType, GenerateAndRunResponse, PlanStep, TaskRoute, ToolObservation } from './types/api'

const { settings, resetSettings } = useLocalSettings()

const form = reactive<DiagramRequest>({
  system_name: 'Fe-C',
  diagram_type: 'binary',
  temperature_min: 300,
  temperature_max: 1800,
  pressure: 101325,
  step_size: 50,
  notes: '请生成一张结构完整、带分区与边界标注的相图。',
})

const htmlContent = ref('')
const generatedCode = ref('')
const stdout = ref('')
const stderr = ref('')
const runId = ref('')
const routeName = ref('')
const traceCount = ref(0)
const routeInfo = ref<TaskRoute | null>(null)
const planSteps = ref<PlanStep[]>([])
const timeline = ref<ToolObservation[]>([])
const terminationReason = ref('')
const isLoading = ref(false)
const statusMessage = ref('')

const canSubmit = computed(() => form.system_name.trim().length > 0 && form.temperature_max > form.temperature_min)

function updateDiagramType(value: DiagramType) {
  form.diagram_type = value
}

function updateTemperatureMin(value: number) {
  form.temperature_min = value
  if (form.temperature_max <= value) {
    form.temperature_max = value + Math.max(form.step_size, 1)
  }
}

function updateTemperatureMax(value: number) {
  form.temperature_max = value
  if (value <= form.temperature_min) {
    form.temperature_min = Math.max(0, value - Math.max(form.step_size, 1))
  }
}

function updateStepSize(value: number) {
  form.step_size = Math.max(1, value)
}

async function loadLatestResult() {
  try {
    const latestHtml = await getLatestResultHtml(settings)
    htmlContent.value = latestHtml
    statusMessage.value = '已加载最近一次生成的相图。'
  } catch {
    statusMessage.value = '待执行'
  }
}

function updateStep(step: PlanStep) {
  const index = planSteps.value.findIndex((item) => item.index === step.index)
  if (index >= 0) {
    planSteps.value[index] = step
  } else {
    planSteps.value.push(step)
  }
  planSteps.value = [...planSteps.value].sort((a, b) => a.index - b.index)
}

function applyStreamEvent(event: AgentStreamEvent) {
  runId.value = event.run_id

  if (event.type === 'run_started') {
    routeInfo.value = event.payload.route as TaskRoute
    routeName.value = routeInfo.value?.name || ''
    planSteps.value = ((event.payload.plan_steps as PlanStep[] | undefined) || []).map((step) => ({ ...step }))
    statusMessage.value = 'Agent 已开始规划与执行。'
    return
  }

  if (event.type === 'step_started' || event.type === 'step_skipped') {
    updateStep(event.payload.step as PlanStep)
    traceCount.value = planSteps.value.length
    return
  }

  if (event.type === 'step_completed' || event.type === 'step_failed') {
    updateStep(event.payload.step as PlanStep)
    timeline.value = [...timeline.value, event.payload.observation as ToolObservation]
    traceCount.value = timeline.value.length
    return
  }

  if (event.type === 'run_completed' || event.type === 'run_error') {
    const response = event.payload.response as GenerateAndRunResponse | undefined
    if (response) {
      generatedCode.value = response.generated_code || ''
      stdout.value = response.stdout || ''
      stderr.value = response.stderr || ''
      terminationReason.value = response.termination_reason || ''
      traceCount.value = response.trace.length
    }
  }
}

async function fallbackGenerateAndRun(requestPayload: DiagramRequest) {
  const response: GenerateAndRunResponse = await generateAndRun(settings, requestPayload)
  htmlContent.value = response.html_content || ''
  generatedCode.value = response.generated_code
  stdout.value = response.stdout || ''
  stderr.value = response.stderr || ''
  runId.value = response.run_id || ''
  routeName.value = response.route || ''
  routeInfo.value = response.route ? { name: response.route, reason: response.route_reason || '' } : null
  planSteps.value = response.plan_steps || []
  timeline.value = response.trace || []
  traceCount.value = response.trace.length
  terminationReason.value = response.termination_reason || ''
  statusMessage.value = response.success ? '执行完成。' : '执行失败，请查看日志。'
}

async function onGenerateAndRun() {
  if (!canSubmit.value) {
    stderr.value = '请确保材料体系非空，且温度上限大于温度下限。'
    return
  }

  isLoading.value = true
  statusMessage.value = '正在生成代码并执行…'
  stderr.value = ''
  stdout.value = ''
  generatedCode.value = ''
  runId.value = ''
  routeName.value = ''
  routeInfo.value = null
  planSteps.value = []
  timeline.value = []
  traceCount.value = 0
  terminationReason.value = ''

  const requestPayload = { ...form }

  try {
    await streamGenerateAndRun(settings, requestPayload, applyStreamEvent)

    if (runId.value) {
      htmlContent.value = await getRunResultHtml(settings, runId.value)
    }

    statusMessage.value = stderr.value ? '执行结束，请查看 agent 过程。' : '执行完成。'
  } catch (error) {
    try {
      statusMessage.value = '流式请求失败，正在回退到普通模式…'
      await fallbackGenerateAndRun(requestPayload)
    } catch (fallbackError) {
      htmlContent.value = ''
      generatedCode.value = ''
      stdout.value = ''
      runId.value = ''
      routeName.value = ''
      routeInfo.value = null
      planSteps.value = []
      timeline.value = []
      traceCount.value = 0
      terminationReason.value = ''
      stderr.value = fallbackError instanceof Error ? fallbackError.message : error instanceof Error ? error.message : '请求失败。'
      statusMessage.value = '请求失败。'
    }
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  void loadLatestResult()
})
</script>

<template>
  <main class="app-shell">
    <section class="left-pane">
      <header class="hero">
        <div>
          <h1>材料相图 Agent</h1>
          <p>输入材料体系、调整参数，并在本地运行生成 Plotly 相图。</p>
        </div>
        <button class="primary-button" type="button" :disabled="isLoading || !canSubmit" @click="onGenerateAndRun">
          {{ isLoading ? '运行中…' : '生成并运行' }}
        </button>
      </header>

      <div class="status-bar">
        <span>状态：{{ statusMessage || '待执行' }}</span>
        <span>主接口：{{ settings.generateAndRunPath }}</span>
        <span v-if="runId">Run ID：{{ runId }}</span>
        <span v-if="routeName">Route：{{ routeName }}</span>
        <span v-if="traceCount">Steps：{{ traceCount }}</span>
      </div>

      <ChatPanel
        :system-name="form.system_name"
        :notes="form.notes"
        @update:system-name="form.system_name = $event"
        @update:notes="form.notes = $event"
      />

      <ControlPanel
        :diagram-type="form.diagram_type"
        :temperature-min="form.temperature_min"
        :temperature-max="form.temperature_max"
        :pressure="form.pressure"
        :step-size="form.step_size"
        @update:diagram-type="updateDiagramType"
        @update:temperature-min="updateTemperatureMin"
        @update:temperature-max="updateTemperatureMax"
        @update:pressure="form.pressure = $event"
        @update:step-size="updateStepSize"
      />

      <SettingsPanel :settings="settings" @reset="resetSettings" />
      <ErrorPanel :stdout="stdout" :stderr="stderr" />

      <section v-if="generatedCode" class="panel generated-code">
        <div class="panel-header">
          <h2>生成代码</h2>
          <p>当前占位代码可直接在后端本地执行。</p>
        </div>
        <pre>{{ generatedCode }}</pre>
      </section>
    </section>

    <section class="right-pane">
      <ResultViewer :html-content="htmlContent" :is-loading="isLoading" />
      <AgentTracePanel
        :run-id="runId"
        :route="routeInfo"
        :plan-steps="planSteps"
        :timeline="timeline"
        :status="statusMessage"
        :termination-reason="terminationReason"
        :is-loading="isLoading"
      />
    </section>
  </main>
</template>

<style scoped>
:global(body) {
  margin: 0;
  font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #eef2f7;
  color: #102a43;
}

:global(*) {
  box-sizing: border-box;
}

.app-shell {
  display: grid;
  grid-template-columns: 40% 60%;
  gap: 18px;
  min-height: 100vh;
  padding: 18px;
}

.left-pane {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.right-pane {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px;
  background: linear-gradient(135deg, #0b4f93, #1976d2);
  color: white;
  border-radius: 14px;
}

.hero h1 {
  margin: 0;
  font-size: 28px;
}

.hero p {
  margin: 8px 0 0;
  max-width: 540px;
  color: rgba(255, 255, 255, 0.92);
}

.primary-button {
  border: none;
  border-radius: 12px;
  padding: 12px 18px;
  background: #ffffff;
  color: #0b4f93;
  cursor: pointer;
  font-weight: 700;
  min-width: 132px;
}

.primary-button:disabled {
  opacity: 0.65;
  cursor: not-allowed;
}

.status-bar {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  background: #ffffff;
  border: 1px solid #d9e0ea;
  border-radius: 12px;
  color: #486581;
  font-size: 13px;
}

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

.generated-code pre {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  border-radius: 10px;
  background: #0f172a;
  color: #d6e3f0;
  font-size: 12px;
  white-space: pre-wrap;
}

@media (max-width: 1080px) {
  .app-shell {
    grid-template-columns: 1fr;
  }

  .status-bar,
  .hero {
    flex-direction: column;
  }
}
</style>
