function readArg(name) {
  const prefix = `--${name}=`
  const match = process.argv.slice(2).find((item) => item.startsWith(prefix))
  return match ? match.slice(prefix.length) : ''
}

const FRONTEND_URL = readArg('frontend-url') || process.env.FRONTEND_URL || 'http://127.0.0.1:5174/'
const DEBUG_URL = readArg('debug-url') || process.env.CHROME_DEBUG_URL || 'http://127.0.0.1:9222/json/list'
const PROMPT = readArg('prompt') || process.env.AGENT_PROMPT || '请生成一张 Fe-Cu 二元相图，温度范围 300K-1800K，突出液相线和两固相区。'
const EXPECT_ROUTE = readArg('expect-route') || process.env.EXPECT_ROUTE || 'phase_diagram.generate'
const expectIframeArg = readArg('expect-iframe')
const EXPECT_IFRAME = expectIframeArg ? expectIframeArg !== 'false' : process.env.EXPECT_IFRAME !== 'false'
const FOLLOW_UP_PROMPT = readArg('follow-up') || process.env.FOLLOW_UP_PROMPT || ''
const EXPECT_FOLLOW_UP_PATTERN = readArg('expect-follow-up-pattern') || process.env.EXPECT_FOLLOW_UP_PATTERN || ''
const uploadImageArg = readArg('upload-image')
const UPLOAD_IMAGE = uploadImageArg ? uploadImageArg === 'true' : process.env.UPLOAD_IMAGE === 'true'
const expectArtifactPersistArg = readArg('expect-artifact-persist')
const EXPECT_ARTIFACT_PERSIST = expectArtifactPersistArg ? expectArtifactPersistArg === 'true' : process.env.EXPECT_ARTIFACT_PERSIST === 'true'
const WAIT_TIMEOUT_MS = Number(readArg('wait-timeout-ms') || process.env.SMOKE_WAIT_TIMEOUT_MS || 120000)
const MINI_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABAAB9hc4VQAAAABJRU5ErkJggg=='

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

  const waitFor = async (expression, timeoutMs = 120000, intervalMs = 500) => {
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

  await client.send('Page.enable')
  await client.send('Runtime.enable')
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1680,
    height: 1400,
    deviceScaleFactor: 1,
    mobile: false,
  })
  await client.send('Page.bringToFront')
  await client.send('Page.navigate', { url: FRONTEND_URL })
  await client.waitFor("document.readyState === 'complete'", 30000)
  await client.waitFor("Boolean(document.querySelector('[data-testid=\"chat-input\"]'))", 30000)
  await client.waitFor("Boolean(document.querySelector('[data-testid=\"send-button\"]'))", 30000)

  if (UPLOAD_IMAGE) {
    await client.evaluate(`(() => {
      const input = document.querySelector('.upload-input')
      const dropzone = document.querySelector('.upload-dropzone')
      if (!(input instanceof HTMLInputElement) || !(dropzone instanceof HTMLElement)) {
        return false
      }
      const binary = atob(${JSON.stringify(MINI_PNG_BASE64)})
      const bytes = new Uint8Array(binary.length)
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index)
      }
      const file = new File([bytes], 'diagram.png', { type: 'image/png' })
      const dataTransfer = new DataTransfer()
      dataTransfer.items.add(file)
      Object.defineProperty(input, 'files', {
        configurable: true,
        value: dataTransfer.files,
      })
      input.dispatchEvent(new Event('change', { bubbles: true }))
      dropzone.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer }))
      dropzone.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer }))
      return true
    })()`)
    await client.waitFor("document.querySelectorAll('.upload-preview-card').length > 0", 30000)
  }

  await client.evaluate(`(() => {
    const textarea = document.querySelector('[data-testid="chat-input"]')
    if (!textarea) return false
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
    if (!setter) return false
    setter.call(textarea, ${JSON.stringify(PROMPT)})
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    textarea.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  })()`)

  await client.evaluate(`(() => {
    const button = document.querySelector('[data-testid="send-button"]')
    if (!button) return false
    button.click()
    return true
  })()`)

  if (EXPECT_ROUTE !== 'skip') {
    await client.waitFor(`(() => {
      const chips = Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim())
      return chips.includes(${JSON.stringify(EXPECT_ROUTE)})
    })()`, WAIT_TIMEOUT_MS)
  }

  if (EXPECT_IFRAME) {
    await client.waitFor("(document.querySelector('iframe')?.srcdoc || '').length > 1000", WAIT_TIMEOUT_MS)
  } else if (EXPECT_ROUTE === 'recognition.analyze') {
    await client.waitFor("Boolean(document.querySelector('.recognition-card'))", WAIT_TIMEOUT_MS)
  }

  await client.waitFor(`(() => {
    const chips = Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim())
    return chips.includes('completed') || chips.includes('error')
  })()`, WAIT_TIMEOUT_MS)

  if (FOLLOW_UP_PROMPT) {
    const previousAssistantCount = await client.evaluate("document.querySelectorAll('.conversation-bubble-assistant').length")

    await client.evaluate(`(() => {
      const textarea = document.querySelector('[data-testid="chat-input"]')
      if (!textarea) return false
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
      if (!setter) return false
      setter.call(textarea, ${JSON.stringify(FOLLOW_UP_PROMPT)})
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
      textarea.dispatchEvent(new Event('change', { bubbles: true }))
      return true
    })()`)

    await client.evaluate(`(() => {
      const button = document.querySelector('[data-testid="send-button"]')
      if (!button) return false
      button.click()
      return true
    })()`)

    await client.waitFor(`(() => {
      const chips = Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim())
      return chips.includes('conversation.answer')
    })()`, WAIT_TIMEOUT_MS)

    await client.waitFor(`document.querySelectorAll('.conversation-bubble-assistant').length > ${Number(previousAssistantCount || 0)}`, WAIT_TIMEOUT_MS)

    if (EXPECT_FOLLOW_UP_PATTERN) {
      await client.waitFor(`(() => {
        const lastAssistant = Array.from(document.querySelectorAll('.conversation-bubble-assistant')).pop()?.textContent || ''
        return new RegExp(${JSON.stringify(EXPECT_FOLLOW_UP_PATTERN)}, 'i').test(lastAssistant)
      })()`, WAIT_TIMEOUT_MS)
    }

    if (EXPECT_ARTIFACT_PERSIST) {
      await client.waitFor("(document.querySelector('iframe')?.srcdoc || '').length > 1000", WAIT_TIMEOUT_MS)
    }
  }

  const summary = await client.evaluate(`(() => {
    const iframe = document.querySelector('iframe')
    const artifactBubble = document.querySelector('.conversation-artifact-bubble')
    const iframeLength = (iframe?.srcdoc || '').length
    const messages = Array.from(document.querySelectorAll('.conversation-bubble')).slice(-6).map((node) => node.textContent?.trim())
    const lastAssistantMessage = Array.from(document.querySelectorAll('.conversation-bubble-assistant')).pop()?.textContent?.trim() || ''
    const statusChips = Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim())
    const traceCards = Array.from(document.querySelectorAll('.trace-summary-card strong')).map((node) => node.textContent?.trim())
    return {
      iframeLength,
      iframeStyleHeight: iframe?.style?.height || '',
      iframeClientHeight: iframe?.clientHeight || 0,
      iframeClientWidth: iframe?.clientWidth || 0,
      artifactBubbleWidth: artifactBubble?.clientWidth || 0,
      messages,
      lastAssistantMessage,
      statusChips,
      traceCards,
      hasNoToolMessage:
        document.body.innerText.includes('这一轮由 LLM 直接回答') ||
        document.body.innerText.includes('本轮停留在对话模式，没有调用本地相图工具。'),
    }
  })()`)

  console.log(JSON.stringify(summary))
  client.ws.close()
  await delay(100)
  process.exit(0)
}

run().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
