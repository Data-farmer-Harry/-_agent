const FRONTEND_URL = process.env.FRONTEND_URL || 'http://127.0.0.1:5174/'
const DEBUG_URL = process.env.CHROME_DEBUG_URL || 'http://127.0.0.1:9222/json/list'
const PROMPT =
  process.env.AGENT_PROMPT ||
  '请把 Fe-Cu 相图的组会讲解整理成一个 HTML 页面，左边讲关键相区，右边放一个示意重绘区域。'
const EXPECT_ROUTE = process.env.EXPECT_ROUTE || 'phase_diagram.redraw_html'
const EXPECT_TOOL = process.env.EXPECT_TOOL || 'phase_diagram_html_redraw'

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

  const waitFor = async (expression, timeoutMs = 90000, intervalMs = 500) => {
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
  console.error('smoke:start')
  const debuggerUrl = await getDebuggerUrl()
  console.error('smoke:debugger')
  const client = await connectCdp(debuggerUrl)

  await client.send('Page.enable')
  await client.send('Runtime.enable')
  await client.send('Page.bringToFront')
  await client.send('Page.navigate', { url: FRONTEND_URL })
  console.error('smoke:navigated')
  await client.waitFor("document.readyState === 'complete'", 30000)
  await client.waitFor("Boolean(document.querySelector('textarea'))", 30000)
  await client.waitFor(
    "Array.from(document.querySelectorAll('button')).some((button) => button.textContent?.includes('让 Agent 开始工作') && !button.disabled)",
    30000,
  )
  console.error('smoke:ready')

  await client.evaluate(`(() => {
    const textarea = document.querySelector('textarea')
    if (!textarea) return false
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
    if (!setter) return false
    setter.call(textarea, ${JSON.stringify(PROMPT)})
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    textarea.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  })()`)

  await client.evaluate(`(() => {
    const button = Array.from(document.querySelectorAll('button')).find((item) => item.textContent?.includes('让 Agent 开始工作'))
    if (!button) return false
    button.click()
    return true
  })()`)
  console.error('smoke:clicked')

  await client.waitFor(`(() => {
    const chips = Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim())
    return chips.includes('ready') && chips.includes(${JSON.stringify(EXPECT_ROUTE)}) && chips.includes(${JSON.stringify(EXPECT_TOOL)})
  })()`, 90000)
  await client.waitFor(`(() => {
    const cards = Array.from(document.querySelectorAll('.summary-card strong')).map((node) => node.textContent?.trim())
    return cards.includes(${JSON.stringify(EXPECT_ROUTE)}) &&
      cards.includes(${JSON.stringify(EXPECT_TOOL)}) &&
      cards.some((value) => /^[a-f0-9]{12}$/i.test(value || ''))
  })()`, 90000)
  await client.waitFor("(document.querySelector('iframe')?.srcdoc || '').length > 1000", 90000)
  console.error('smoke:complete')

  const summary = await client.evaluate(`(() => {
    const text = document.body.innerText
    const iframeLength = (document.querySelector('iframe')?.srcdoc || '').length
    const messages = Array.from(document.querySelectorAll('.conversation-bubble p')).slice(-4).map((node) => node.textContent?.trim())
    const statusChips = Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim())
    const summaryCards = Array.from(document.querySelectorAll('.summary-card strong')).map((node) => node.textContent?.trim())
    return {
      iframeLength,
      hasReady: text.includes('ready'),
      hasCompleted: text.includes('completed'),
      hasExpectedRoute: text.includes(${JSON.stringify(EXPECT_ROUTE)}),
      hasExpectedTool: text.includes(${JSON.stringify(EXPECT_TOOL)}),
      messages,
      statusChips,
      summaryCards,
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
