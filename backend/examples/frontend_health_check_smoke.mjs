const FRONTEND_URL = process.env.FRONTEND_URL || 'http://127.0.0.1:5174/'
const DEBUG_URL = process.env.CHROME_DEBUG_URL || 'http://127.0.0.1:9222/json/list'
const WAIT_TIMEOUT_MS = Number(process.env.SMOKE_WAIT_TIMEOUT_MS || 60000)

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function getDebuggerUrl() {
  const response = await fetch(DEBUG_URL)
  let pages = await response.json()
  const expected = new URL(FRONTEND_URL)
  let page = pages.find((item) => {
    if (typeof item.url !== 'string') {
      return false
    }
    try {
      const current = new URL(item.url)
      return current.origin === expected.origin
    } catch {
      return item.url.startsWith(FRONTEND_URL.replace(/\/$/, ''))
    }
  })
  if (!page) {
    const debugBase = DEBUG_URL.replace(/\/json\/list$/, "")
    const createResponse = await fetch(`${debugBase}/json/new?${encodeURIComponent(FRONTEND_URL)}`, { method: "PUT" })
    if (!createResponse.ok) {
      throw new Error(`Could not create Chrome page via CDP: HTTP ${createResponse.status}`)
    }
    await delay(500)
    const refreshed = await fetch(DEBUG_URL)
    pages = await refreshed.json()
    page = pages.find((item) => {
      if (typeof item.url !== 'string') {
        return false
      }
      try {
        const current = new URL(item.url)
        return current.origin === expected.origin
      } catch {
        return item.url.startsWith(FRONTEND_URL.replace(/\/$/, ''))
      }
    })
  }
  if (!page?.webSocketDebuggerUrl) {
    throw new Error(`Could not find a Chrome page for ${FRONTEND_URL}. Available pages: ${JSON.stringify(pages.map((item) => item.url))}`)
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

  const waitFor = async (expression, timeoutMs = WAIT_TIMEOUT_MS, intervalMs = 300) => {
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
  try {
    await client.send('Page.enable')
    await client.send('Runtime.enable')
    await client.send('DOM.enable')
    await client.send('Emulation.setDeviceMetricsOverride', {
      width: 1500,
      height: 1000,
      deviceScaleFactor: 1,
      mobile: false,
    })
    await client.send('Page.bringToFront')
    await client.send('Page.navigate', { url: FRONTEND_URL })
    await client.waitFor("document.readyState === 'complete'", 30000)
    await client.waitFor("Boolean(document.querySelector('[data-testid=\"chat-input\"]'))", 30000)

    await client.evaluate(`(() => {
      const buttons = Array.from(document.querySelectorAll('button'))
      const settings = buttons.find((button) => /系统偏好设置/.test(button.textContent || ''))
      settings?.click()
      return Boolean(settings)
    })()`)
    await client.waitFor("Boolean(document.querySelector('[data-testid=\"system-health-check-button\"]'))", 30000)

    await client.evaluate(`document.querySelector('[data-testid="system-health-check-button"]')?.click()`)
    await client.waitFor("Boolean(document.querySelector('[data-testid=\"system-health-summary\"]'))", WAIT_TIMEOUT_MS)
    await client.waitFor("document.querySelectorAll('[data-testid=\"system-health-check-card\"]').length >= 8", WAIT_TIMEOUT_MS)

    const summary = await client.evaluate(`(() => {
      const cards = Array.from(document.querySelectorAll('[data-testid="system-health-check-card"]'))
      return {
        cardCount: cards.length,
        names: cards.map((card) => card.querySelector('p.text-sm')?.textContent?.trim()).filter(Boolean),
        overall: document.querySelector('[data-testid="system-health-summary"]')?.textContent?.trim() || '',
      }
    })()`)
    console.log(JSON.stringify(summary))
  } finally {
    client.ws.close()
  }
}

run().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
