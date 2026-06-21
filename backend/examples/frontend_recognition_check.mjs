const FRONTEND_URL = process.env.FRONTEND_URL || 'http://127.0.0.1:5174/'
const DEBUG_URL = process.env.CHROME_DEBUG_URL || 'http://127.0.0.1:9222/json/list'
const IMAGE_PATH =
  process.env.RECOGNITION_IMAGE_PATH ||
  '/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/benchmarks/assets/external_phase_diagrams/al_ni_pmc_phase_diagram.jpg'
const PROMPT = process.env.AGENT_PROMPT || '请识别这张相图截图，并提取体系、坐标轴和主要相区。'
const WAIT_TIMEOUT_MS = Number(process.env.SMOKE_WAIT_TIMEOUT_MS || 240000)

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
  console.error('[recognition-check] connected to Chrome debugger')

  await client.send('Page.enable')
  await client.send('Runtime.enable')
  await client.send('DOM.enable')
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1680,
    height: 1500,
    deviceScaleFactor: 1,
    mobile: false,
  })
  await client.send('Page.bringToFront')
  await client.send('Page.navigate', { url: FRONTEND_URL })
  await client.waitFor("document.readyState === 'complete'", 30000)
  await client.waitFor("Boolean(document.querySelector('[data-testid=\"chat-input\"]'))", 30000)
  console.error('[recognition-check] page ready')
  await client.waitFor(`(() => {
    const newConversationButton = Array.from(document.querySelectorAll('button')).find((node) =>
      /新建研究课题/i.test(node.textContent || '')
    )
    return Boolean(newConversationButton)
  })()`, 30000)
  await client.evaluate(`(() => {
    const newConversationButton = Array.from(document.querySelectorAll('button')).find((node) =>
      /新建研究课题/i.test(node.textContent || '')
    )
    if (!(newConversationButton instanceof HTMLButtonElement)) {
      return false
    }
    newConversationButton.click()
    return true
  })()`)
  await delay(1200)
  console.error('[recognition-check] started new conversation')

  const { root } = await client.send('DOM.getDocument', { depth: -1, pierce: true })
  const { nodeId } = await client.send('DOM.querySelector', {
    nodeId: root.nodeId,
    selector: 'input.upload-input',
  })
  await client.send('DOM.setFileInputFiles', {
    nodeId,
    files: [IMAGE_PATH],
  })
  await client.evaluate(`(() => {
    const input = document.querySelector('input.upload-input')
    if (!(input instanceof HTMLInputElement)) {
      return false
    }
    input.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  })()`)
  console.error('[recognition-check] file selected')

  await client.waitFor(`(() => {
    const uploadButton = Array.from(document.querySelectorAll('button')).find((node) =>
      /Assets Attached|Upload Data/i.test(node.textContent || '')
    )
    return /Assets Attached/i.test(uploadButton?.textContent || '')
  })()`, 30000)
  console.error('[recognition-check] upload acknowledged by UI')

  await client.evaluate(`(() => {
    const textarea = document.querySelector('[data-testid="chat-input"]')
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
    if (!textarea || !setter) {
      return false
    }
    setter.call(textarea, ${JSON.stringify(PROMPT)})
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    textarea.dispatchEvent(new Event('change', { bubbles: true }))
    document.querySelector('[data-testid="send-button"]')?.click()
    return true
  })()`)
  console.error('[recognition-check] prompt submitted')

  await client.waitFor(`(() => {
    const chips = Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim())
    return chips.includes('completed') || chips.includes('error')
  })()`, WAIT_TIMEOUT_MS)
  console.error('[recognition-check] terminal status observed')
  await client.waitFor(`(() => {
    const iframe = document.querySelector('iframe')
    const srcdoc = iframe?.srcdoc || ''
    return srcdoc.includes('recognition-reconstruction-canvas') &&
      srcdoc.includes('generated_canvas_vector_reconstruction') &&
      srcdoc.includes('data-priority-mode="structured_path_reconstruction"') &&
      srcdoc.includes('reconstruction_scene')
  })()`, WAIT_TIMEOUT_MS)
  console.error('[recognition-check] iframe markers verified')

  const snapshot = await client.evaluate(`(() => ({
    statusChips: Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim()),
    recognitionCard: Boolean(document.querySelector('.recognition-card')),
    iframeLength: (document.querySelector('iframe')?.srcdoc || '').length,
    iframeClientHeight: document.querySelector('iframe')?.clientHeight || 0,
    iframeHasGeneratedSvg: (document.querySelector('iframe')?.srcdoc || '').includes('recognition-generated-svg'),
    iframeHasReconstructionCanvas: (document.querySelector('iframe')?.srcdoc || '').includes('recognition-reconstruction-canvas'),
    iframeHasStructuredScene: (document.querySelector('iframe')?.srcdoc || '').includes('reconstruction_scene'),
    iframeHasCanvasPriorityMode: (document.querySelector('iframe')?.srcdoc || '').includes('data-priority-mode="structured_path_reconstruction"'),
    iframeHasFidelityBanner: (document.querySelector('iframe')?.srcdoc || '').includes('Structured-path mode'),
    iframeHasSourceImageLayer: (document.querySelector('iframe')?.srcdoc || '').includes('phase-source-image'),
    messages: Array.from(document.querySelectorAll('.conversation-bubble')).slice(-6).map((node) => node.textContent?.trim()),
    bodyText: document.body.innerText.slice(0, 4000),
  }))()`)

  console.log(JSON.stringify(snapshot))
  client.ws.close()
}

run().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error))
  process.exitCode = 1
})
