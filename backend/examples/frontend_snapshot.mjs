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
    messages: Array.from(document.querySelectorAll('.conversation-bubble')).map((node) => node.textContent?.trim()),
    statusChips: Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim()),
    summaryCards: Array.from(document.querySelectorAll('.summary-card strong')).map((node) => node.textContent?.trim()),
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
