function readArg(name) {
  const prefix = `--${name}=`
  const match = process.argv.slice(2).find((item) => item.startsWith(prefix))
  return match ? match.slice(prefix.length) : ''
}

const FRONTEND_URL = readArg('frontend-url') || process.env.FRONTEND_URL || 'http://127.0.0.1:5174/'
const DEBUG_URL = readArg('debug-url') || process.env.CHROME_DEBUG_URL || 'http://127.0.0.1:9222/json/list'
const PROMPT = readArg('prompt') || ''
const EXPECT_ROUTE = readArg('expect-route') || ''
const LOAD_RUN_LABEL = readArg('load-run-label') || ''
const LOAD_RUN_INDEX = Number(readArg('load-run-index') || '0')
const WAIT_TIMEOUT_MS = Number(readArg('wait-timeout-ms') || 300000)

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function getDebuggerUrl() {
  const response = await fetch(DEBUG_URL)
  const pages = await response.json()
  const page = pages.find((item) => typeof item.url === 'string' && item.url.startsWith(FRONTEND_URL))
  if (!page?.webSocketDebuggerUrl) {
    throw new Error(`Could not find a Chrome page for ${FRONTEND_URL}.`)
  }
  return page.webSocketDebuggerUrl
}

async function connectCdp(wsUrl) {
  const ws = new WebSocket(wsUrl)
  let id = 0
  const pending = new Map()

  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true })
    ws.addEventListener('error', reject, { once: true })
  })

  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data)
    if (!message.id || !pending.has(message.id)) {
      return
    }
    const { resolve, reject } = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) {
      reject(new Error(message.error.message || 'CDP error'))
      return
    }
    resolve(message.result)
  })

  const send = (method, params = {}) => {
    const currentId = ++id
    ws.send(JSON.stringify({ id: currentId, method, params }))
    return new Promise((resolve, reject) => pending.set(currentId, { resolve, reject }))
  }

  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    })
    return result.result?.value
  }

  const waitFor = async (expression, timeoutMs = WAIT_TIMEOUT_MS, intervalMs = 1000) => {
    const start = Date.now()
    while (Date.now() - start < timeoutMs) {
      const value = await evaluate(expression)
      if (value) {
        return value
      }
      await delay(intervalMs)
    }
    throw new Error(`Timed out waiting for: ${expression}`)
  }

  return { ws, send, evaluate, waitFor }
}

async function run() {
  const debuggerUrl = await getDebuggerUrl()
  const client = await connectCdp(debuggerUrl)
  await client.send('Runtime.enable')
  await client.send('Page.enable')

  if (PROMPT || LOAD_RUN_LABEL) {
    await client.send('Emulation.setDeviceMetricsOverride', {
      width: 1680,
      height: 1600,
      deviceScaleFactor: 1,
      mobile: false,
    })
    await client.send('Page.bringToFront')
    await client.send('Page.navigate', { url: FRONTEND_URL })
    await client.waitFor("document.readyState === 'complete'", 30000)
    await client.waitFor("Boolean(document.querySelector('[data-testid=\"chat-input\"]'))", 30000)
    await client.waitFor("Boolean(document.querySelector('[data-testid=\"send-button\"]'))", 30000)
    if (LOAD_RUN_LABEL) {
      await client.waitFor("document.querySelectorAll('aside button').length > 0", 30000)
      await client.evaluate(`(() => {
        const label = ${JSON.stringify(LOAD_RUN_LABEL)}
        const targetIndex = ${Number.isFinite(LOAD_RUN_INDEX) ? LOAD_RUN_INDEX : 0}
        const buttons = Array.from(document.querySelectorAll('aside button'))
          .filter((node) => (node.textContent || '').includes(label))
        const target = buttons[targetIndex]
        if (!target) {
          return false
        }
        target.click()
        return true
      })()`)
      await client.waitFor(`(() => {
        const chips = Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim())
        return chips.includes('completed') || chips.includes('error')
      })()`, WAIT_TIMEOUT_MS)
    } else {
      await client.evaluate(`(() => {
        const textarea = document.querySelector('[data-testid="chat-input"]')
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
        if (!textarea || !setter) return false
        setter.call(textarea, ${JSON.stringify(PROMPT)})
        textarea.dispatchEvent(new Event('input', { bubbles: true }))
        textarea.dispatchEvent(new Event('change', { bubbles: true }))
        document.querySelector('[data-testid="send-button"]')?.click()
        return true
      })()`)
      if (EXPECT_ROUTE) {
        await client.waitFor(`(() => Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim()).includes(${JSON.stringify(EXPECT_ROUTE)}))()`)
      }
      await client.waitFor(`(() => {
        const chips = Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim())
        return chips.includes('completed') || chips.includes('error')
      })()`)
    }
  }

  const snapshot = await client.evaluate(`(() => ({
    bodyText: document.body.innerText.slice(0, 4000),
    iframeLength: (document.querySelector('iframe')?.srcdoc || '').length,
    iframeHasGeneratedSvg: (document.querySelector('iframe')?.srcdoc || '').includes('recognition-generated-svg'),
    iframeHasReconstructionCanvas: (document.querySelector('iframe')?.srcdoc || '').includes('recognition-reconstruction-canvas'),
    iframeHasStructuredScene: (document.querySelector('iframe')?.srcdoc || '').includes('reconstruction_scene'),
    iframeHasStructuredMode: (document.querySelector('iframe')?.srcdoc || '').includes('structured_path_reconstruction'),
    iframeHasSourceImageLayer: (document.querySelector('iframe')?.srcdoc || '').includes('phase-source-image'),
    iframeHasRecognizedSourceImage: (document.querySelector('iframe')?.srcdoc || '').includes('recognized-source-image'),
    iframeHasDataImage: (document.querySelector('iframe')?.srcdoc || '').includes('data:image/png;base64'),
    messages: Array.from(document.querySelectorAll('.conversation-bubble')).map((node) => node.textContent?.trim()),
    statusChips: Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim()),
    summaryCards: Array.from(document.querySelectorAll('.summary-card strong')).map((node) => node.textContent?.trim()),
    qualityCard: Boolean(document.querySelector('[data-testid="lammps-quality-card"]')),
    qualityTitle: document.querySelector('[data-testid="lammps-quality-title"]')?.textContent?.trim() || '',
    executionTrustCard: Boolean(document.querySelector('[data-testid="lammps-execution-trust-card"]')),
    executionModeBadge: document.querySelector('[data-testid="lammps-execution-mode-badge"]')?.textContent?.trim() || '',
    scienceUsableBadge: document.querySelector('[data-testid="lammps-science-usable-badge"]')?.textContent?.trim() || '',
    executionRiskNotes: document.querySelector('[data-testid="lammps-execution-risk-notes"]')?.textContent?.trim() || '',
    trustArtifactLinks: Array.from(document.querySelectorAll('[data-testid="lammps-trust-artifact-link"]')).map((node) => node.textContent?.trim()),
    recoveryControls: Boolean(document.querySelector('[data-testid="lammps-resume-retry-controls"]')),
    recoveryAction: document.querySelector('[data-testid="lammps-recovery-action"]')?.textContent?.trim() || '',
    recoveryMode: document.querySelector('[data-testid="lammps-recovery-mode"]')?.textContent?.trim() || '',
    recoveryCheckpoint: document.querySelector('[data-testid="lammps-recovery-checkpoint"]')?.textContent?.trim() || '',
    recoveryNotice: document.querySelector('[data-testid="lammps-recovery-notice"]')?.textContent?.trim() || '',
    recoveryArtifactLinks: Array.from(document.querySelectorAll('[data-testid="lammps-recovery-artifact-link"]')).map((node) => node.textContent?.trim()),
    runModeBadge: document.querySelector('[data-testid="lammps-run-mode-badge"]')?.textContent?.trim() || '',
    scientificBadge: document.querySelector('[data-testid="lammps-scientific-badge"]')?.textContent?.trim() || '',
    syntheticBadge: document.querySelector('[data-testid="lammps-synthetic-badge"]')?.textContent?.trim() || '',
    dagTimelineCard: Boolean(document.querySelector('[data-testid="lammps-dag-timeline-card"]')),
    dagStatus: document.querySelector('[data-testid="lammps-dag-status"]')?.textContent?.trim() || '',
    dagNodes: Array.from(document.querySelectorAll('[data-testid="lammps-dag-node"]')).map((node) => node.textContent?.trim()),
    dagDegradation: document.querySelector('[data-testid="lammps-dag-degradation"]')?.textContent?.trim() || '',
    lifecycleTransitions: document.querySelector('[data-testid="lammps-lifecycle-transitions"]')?.textContent?.trim() || '',
    dagCheckpoints: document.querySelector('[data-testid="lammps-dag-checkpoints"]')?.textContent?.trim() || '',
    redBlueCard: Boolean(document.querySelector('[data-testid="lammps-red-blue-card"]')),
    redReviewStatus: document.querySelector('[data-testid="lammps-red-review-status"]')?.textContent?.trim() || '',
    redReviewScore: document.querySelector('[data-testid="lammps-red-review-score"]')?.textContent?.trim() || '',
    redFindings: document.querySelector('[data-testid="lammps-red-findings"]')?.textContent?.trim() || '',
    bluePatchHistory: document.querySelector('[data-testid="lammps-blue-patch-history"]')?.textContent?.trim() || '',
    bluePatchDiff: document.querySelector('[data-testid="lammps-blue-patch-diff"]')?.textContent?.trim() || '',
    parseAudit: document.querySelector('[data-testid="lammps-parse-audit"]')?.textContent?.trim() || '',
    evidenceRefs: document.querySelector('[data-testid="lammps-evidence-refs"]')?.textContent?.trim() || '',
    evidenceDrilldown: document.querySelector('[data-testid="lammps-evidence-drilldown"]')?.textContent?.trim() || '',
    sharedMemoryEvidence: document.querySelector('[data-testid="lammps-shared-memory-evidence"]')?.textContent?.trim() || '',
    sharedMemoryL1: document.querySelector('[data-testid="lammps-shared-memory-l1"]')?.textContent?.trim() || '',
    sharedMemoryL2: document.querySelector('[data-testid="lammps-shared-memory-l2"]')?.textContent?.trim() || '',
    sharedMemoryL3: document.querySelector('[data-testid="lammps-shared-memory-l3"]')?.textContent?.trim() || '',
    sharedMemoryLocked: document.querySelector('[data-testid="lammps-shared-memory-locked"]')?.textContent?.trim() || '',
    evidenceSourceTypes: Array.from(document.querySelectorAll('[data-testid="lammps-evidence-source-type"]')).map((node) => node.textContent?.trim()),
    evidenceSourceLinks: Array.from(document.querySelectorAll('[data-testid="lammps-evidence-source-link"]')).map((node) => node.textContent?.trim()),
    provenanceDrilldown: document.querySelector('[data-testid="lammps-provenance-drilldown"]')?.textContent?.trim() || '',
    imageCards: document.querySelectorAll('img').length,
    videoCards: document.querySelectorAll('video').length,
    videoDiagnostics: Array.from(document.querySelectorAll('video')).map((video) => ({
      currentSrc: video.currentSrc,
      readyState: video.readyState,
      networkState: video.networkState,
      error: video.error ? { code: video.error.code, message: video.error.message || '' } : null,
      clientWidth: video.clientWidth,
      clientHeight: video.clientHeight,
    })),
    markdownCards: document.querySelectorAll('pre').length,
    downloadButtons: Array.from(document.querySelectorAll('a[download]')).map((node) => node.textContent?.trim()),
    mediaCaptions: Array.from(document.querySelectorAll('figcaption, video + span')).map((node) => node.textContent?.trim()),
  }))()`)
  console.log(JSON.stringify(snapshot))
  client.ws.close()
  process.exit(0)
}

run().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
