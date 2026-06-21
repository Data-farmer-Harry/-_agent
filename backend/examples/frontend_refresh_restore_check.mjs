function readArg(name) {
  const prefix = `--${name}=`
  const match = process.argv.slice(2).find((item) => item.startsWith(prefix))
  return match ? match.slice(prefix.length) : ''
}

const FRONTEND_URL = readArg('frontend-url') || process.env.FRONTEND_URL || 'http://127.0.0.1:5174/'
const DEBUG_URL = readArg('debug-url') || process.env.CHROME_DEBUG_URL || 'http://127.0.0.1:9222/json/list'
const PROMPT =
  readArg('prompt') ||
  process.env.AGENT_PROMPT ||
  '请生成一张 Al-Zn 二元相图，温度范围 300K-1000K，突出液相线以及 FCC_A1 和 HCP_A3 两个主要固相区。'
const FOLLOW_UP_PROMPT = readArg('follow-up') || process.env.FOLLOW_UP_PROMPT || '帮我生成交互式html'
const WAIT_TIMEOUT_MS = Number(readArg('wait-timeout-ms') || process.env.SMOKE_WAIT_TIMEOUT_MS || 180000)

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

async function setTextareaValue(client, value) {
  await client.evaluate(`(() => {
    const textarea = document.querySelector('[data-testid="chat-input"]')
    if (!textarea) return false
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
    if (!setter) return false
    setter.call(textarea, ${JSON.stringify(value)})
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    textarea.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  })()`)
}

async function clickSend(client) {
  await client.evaluate(`(() => {
    const button = document.querySelector('[data-testid="send-button"]')
    if (!button) return false
    button.click()
    return true
  })()`)
}

async function resetConversation(client) {
  await client.evaluate(`(() => {
    const button = Array.from(document.querySelectorAll('button')).find((node) =>
      /新建研究课题|new/i.test(node.textContent || '')
    )
    if (!button) return false
    button.click()
    return true
  })()`)
}

async function waitForCompleted(client) {
  await client.waitFor(`(() => {
    const chips = Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim())
    return chips.includes('completed')
  })()`, WAIT_TIMEOUT_MS)
}

async function run() {
  const debuggerUrl = await getDebuggerUrl()
  const client = await connectCdp(debuggerUrl)

  await client.send('Page.enable')
  await client.send('Runtime.enable')
  await client.send('DOM.enable')
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
  await resetConversation(client)
  await delay(500)

  await setTextareaValue(client, PROMPT)
  await clickSend(client)
  await client.waitFor("(document.querySelector('iframe')?.srcdoc || '').length > 1000", WAIT_TIMEOUT_MS)
  await waitForCompleted(client)

  const baselineCounts = await client.evaluate(`(() => ({
    messageCount: document.querySelectorAll('.conversation-bubble').length,
    assistantCount: document.querySelectorAll('.conversation-bubble-assistant').length,
  }))()`)

  await setTextareaValue(client, FOLLOW_UP_PROMPT)
  await clickSend(client)
  await client.waitFor(`(() => {
    const messages = Array.from(document.querySelectorAll('.conversation-bubble'))
    const lastMessage = messages[messages.length - 1]?.textContent || ''
    return messages.length > ${'${baselineCounts.messageCount}'} && lastMessage.includes(${JSON.stringify(FOLLOW_UP_PROMPT)})
  })()`.replace('${baselineCounts.messageCount}', String(baselineCounts.messageCount)), WAIT_TIMEOUT_MS)
  await client.waitFor(`(() => {
    const assistants = Array.from(document.querySelectorAll('.conversation-bubble-assistant'))
    const lastAssistant = assistants[assistants.length - 1]?.textContent || ''
    return assistants.length > ${'${baselineCounts.assistantCount}'} && /交互式|交互模拟器|html|result\\.html/i.test(lastAssistant)
  })()`.replace('${baselineCounts.assistantCount}', String(baselineCounts.assistantCount)), WAIT_TIMEOUT_MS)
  await client.waitFor(`(() => {
    const latestIframe = Array.from(document.querySelectorAll('iframe')).pop()
    const srcdoc = latestIframe?.srcdoc || ''
    return srcdoc.includes('recognition-simulator-root') &&
      srcdoc.includes('recognition-reconstruction-canvas') &&
      srcdoc.includes('generated_canvas_vector_reconstruction') &&
      srcdoc.includes('reconstruction_scene')
  })()`, WAIT_TIMEOUT_MS)
  await waitForCompleted(client)

  const beforeReload = await client.evaluate(`(() => {
    const latestIframe = Array.from(document.querySelectorAll('iframe')).pop()
    const srcdoc = latestIframe?.srcdoc || ''
    return {
      messageCount: document.querySelectorAll('.conversation-bubble').length,
      assistantCount: document.querySelectorAll('.conversation-bubble-assistant').length,
      lastAssistantMessage: Array.from(document.querySelectorAll('.conversation-bubble-assistant')).pop()?.textContent?.trim() || '',
      iframeLength: srcdoc.length,
      hasRecognitionRoot: srcdoc.includes('recognition-simulator-root'),
      hasGeneratedSvg: srcdoc.includes('recognition-generated-svg'),
      hasReconstructionCanvas: srcdoc.includes('recognition-reconstruction-canvas'),
      hasStructuredScene: srcdoc.includes('reconstruction_scene'),
      hasSourceImage: srcdoc.includes('phase-source-image') || srcdoc.includes('data:image/'),
      conversationId: window.localStorage.getItem('materials-agent-active-conversation-v1') || '',
    }
  })()`)

  await client.send('Page.reload', { ignoreCache: true })
  await client.waitFor("document.readyState === 'complete'", 30000)
  await client.waitFor("Boolean(document.querySelector('[data-testid=\"chat-input\"]'))", 30000)
  await client.waitFor(`(() => {
    const lastAssistant = Array.from(document.querySelectorAll('.conversation-bubble-assistant')).pop()?.textContent || ''
    const latestIframe = Array.from(document.querySelectorAll('iframe')).pop()
    const srcdoc = latestIframe?.srcdoc || ''
    return /交互式|交互模拟器|html|result\\.html/i.test(lastAssistant) &&
      srcdoc.includes('recognition-simulator-root') &&
      srcdoc.includes('recognition-reconstruction-canvas') &&
      srcdoc.includes('generated_canvas_vector_reconstruction') &&
      srcdoc.includes('reconstruction_scene')
  })()`, WAIT_TIMEOUT_MS)

  const afterReload = await client.evaluate(`(() => {
    const latestIframe = Array.from(document.querySelectorAll('iframe')).pop()
    const srcdoc = latestIframe?.srcdoc || ''
    return {
      messageCount: document.querySelectorAll('.conversation-bubble').length,
      assistantCount: document.querySelectorAll('.conversation-bubble-assistant').length,
      lastAssistantMessage: Array.from(document.querySelectorAll('.conversation-bubble-assistant')).pop()?.textContent?.trim() || '',
      iframeLength: srcdoc.length,
      hasRecognitionRoot: srcdoc.includes('recognition-simulator-root'),
      hasGeneratedSvg: srcdoc.includes('recognition-generated-svg'),
      hasReconstructionCanvas: srcdoc.includes('recognition-reconstruction-canvas'),
      hasStructuredScene: srcdoc.includes('reconstruction_scene'),
      hasSourceImage: srcdoc.includes('phase-source-image') || srcdoc.includes('data:image/'),
      conversationId: window.localStorage.getItem('materials-agent-active-conversation-v1') || '',
    }
  })()`)

  console.log(JSON.stringify({ beforeReload, afterReload }))
  client.ws.close()
  await delay(100)
}

run().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
