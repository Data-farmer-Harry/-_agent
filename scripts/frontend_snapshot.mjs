const FRONTEND_URL = process.env.FRONTEND_URL || 'http://127.0.0.1:5174/'
const DEBUG_URL = process.env.CHROME_DEBUG_URL || 'http://127.0.0.1:9222/json/list'

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

  return { ws, send, evaluate }
}

async function run() {
  const debuggerUrl = await getDebuggerUrl()
  const client = await connectCdp(debuggerUrl)
  await client.send('Runtime.enable')
  const snapshot = await client.evaluate(`(() => ({
    bodyText: document.body.innerText.slice(0, 4000),
    iframeLength: (document.querySelector('iframe')?.srcdoc || '').length,
    messages: Array.from(document.querySelectorAll('.conversation-bubble p')).map((node) => node.textContent?.trim()),
    statusChips: Array.from(document.querySelectorAll('.status-chip')).map((node) => node.textContent?.trim()),
    summaryCards: Array.from(document.querySelectorAll('.summary-card strong')).map((node) => node.textContent?.trim()),
  }))()`)
  console.log(JSON.stringify(snapshot))
  client.ws.close()
  process.exit(0)
}

run().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
