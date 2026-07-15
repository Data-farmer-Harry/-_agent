import { app, BrowserWindow, dialog, shell } from 'electron'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const RUNTIME_REVISION = 'v1'
const BACKEND_START_TIMEOUT_MS = 120_000

let backendProcess = null
let backendUrl = ''
let mainWindow = null
let setupWindow = null
let shuttingDown = false

function runProcess(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env || process.env,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let output = ''
    child.stdout.on('data', (chunk) => { output += chunk.toString() })
    child.stderr.on('data', (chunk) => { output += chunk.toString() })
    child.on('error', reject)
    child.on('exit', (code) => {
      if (code === 0) resolve(output)
      else reject(new Error(`${command} exited with ${code}\n${output}`))
    })
  })
}

function showSetupWindow(message = '首次启动正在初始化本地计算环境，请稍候…') {
  if (setupWindow && !setupWindow.isDestroyed()) {
    return
  }
  setupWindow = new BrowserWindow({
    width: 560,
    height: 310,
    resizable: false,
    show: false,
    backgroundColor: '#f4f7fb',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  const html = `<!doctype html><meta charset="utf-8"><style>
    body{margin:0;background:#f4f7fb;color:#243248;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    main{height:100vh;display:grid;place-items:center;text-align:center;padding:32px;box-sizing:border-box}
    .mark{width:58px;height:58px;margin:auto;border-radius:18px;background:linear-gradient(135deg,#5577d9,#31a6a0);box-shadow:0 14px 32px #496db533}
    h1{font-size:22px;margin:20px 0 8px}.note{font-size:14px;line-height:1.7;color:#63738a}
    .bar{height:6px;width:280px;overflow:hidden;border-radius:999px;background:#dce5f1;margin:24px auto 0}
    .bar:after{content:"";display:block;height:100%;width:45%;border-radius:inherit;background:#4d8eae;animation:move 1.3s infinite ease-in-out}
    @keyframes move{from{transform:translateX(-110%)}to{transform:translateX(330%)}}
  </style><main><section><div class="mark"></div><h1>MatterLab 研材体</h1><div class="note">${message}<br>该步骤只在首次启动或运行时升级后执行。</div><div class="bar"></div></section></main>`
  setupWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`)
  setupWindow.once('ready-to-show', () => setupWindow?.show())
}

function runtimePython(runtimeRoot) {
  return process.platform === 'win32'
    ? path.join(runtimeRoot, 'python.exe')
    : path.join(runtimeRoot, 'bin', 'python')
}

function existingPath(candidates) {
  return candidates.find((candidate) => candidate && fs.existsSync(candidate)) || ''
}

async function ensurePackagedRuntime() {
  if (!app.isPackaged) {
    const condaPrefix = process.env.CONDA_PREFIX || ''
    return {
      root: condaPrefix,
      python: process.env.MATTERLAB_PYTHON || existingPath([
        condaPrefix && runtimePython(condaPrefix),
      ]) || (process.platform === 'win32' ? 'python' : 'python3'),
    }
  }

  const archive = path.join(process.resourcesPath, 'runtime.tar.gz')
  const target = path.join(app.getPath('userData'), `runtime-${RUNTIME_REVISION}-${process.platform}-${process.arch}`)
  const marker = path.join(target, '.matterlab-runtime-ready')
  if (fs.existsSync(marker) && fs.existsSync(runtimePython(target))) {
    return { root: target, python: runtimePython(target) }
  }
  if (!fs.existsSync(archive)) {
    throw new Error(`Packaged computation runtime is missing: ${archive}`)
  }

  showSetupWindow()
  fs.rmSync(target, { recursive: true, force: true })
  fs.mkdirSync(target, { recursive: true })
  try {
    await runProcess('tar', ['-xzf', archive, '-C', target])
    const python = runtimePython(target)
    const unpackScript = process.platform === 'win32'
      ? existingPath([
          path.join(target, 'Scripts', 'conda-unpack-script.py'),
          path.join(target, 'Scripts', 'conda-unpack.py'),
        ])
      : path.join(target, 'bin', 'conda-unpack')
    if (!fs.existsSync(python) || !fs.existsSync(unpackScript)) {
      throw new Error('The extracted computation runtime is incomplete.')
    }
    await runProcess(python, [unpackScript], { cwd: target })
    fs.writeFileSync(path.join(target, '.matterlab-runtime-ready'), new Date().toISOString())
  } catch (error) {
    fs.rmSync(target, { recursive: true, force: true })
    throw error
  }
  return { root: target, python: runtimePython(target) }
}

function runtimeExecutables(runtimeRoot) {
  if (!runtimeRoot) return { lammps: '', potentials: '', binDirs: [] }
  const vendorRoot = app.isPackaged ? path.join(process.resourcesPath, 'lammps') : ''
  let vendorManifest = {}
  try {
    vendorManifest = JSON.parse(fs.readFileSync(path.join(vendorRoot, 'matterlab-lammps-manifest.json'), 'utf8'))
  } catch {
    vendorManifest = {}
  }
  const vendorExecutable = vendorManifest.executable ? path.join(vendorRoot, vendorManifest.executable) : ''
  const vendorPotentials = vendorManifest.potentials ? path.join(vendorRoot, vendorManifest.potentials) : ''
  const binDirs = (process.platform === 'win32'
    ? [path.join(runtimeRoot, 'Library', 'bin'), path.join(runtimeRoot, 'Scripts'), runtimeRoot]
    : [path.join(runtimeRoot, 'bin')]).concat(vendorExecutable ? [path.dirname(vendorExecutable)] : [])
  return {
    lammps: existingPath([
      vendorExecutable,
      vendorRoot && path.join(vendorRoot, 'bin', 'lmp.exe'),
      vendorRoot && path.join(vendorRoot, 'lmp.exe'),
      ...binDirs.map((dir) => path.join(dir, process.platform === 'win32' ? 'lmp.exe' : 'lmp')),
      ...binDirs.map((dir) => path.join(dir, process.platform === 'win32' ? 'lammps.exe' : 'lammps')),
      ...binDirs.map((dir) => path.join(dir, process.platform === 'win32' ? 'lmp_serial.exe' : 'lmp_serial')),
    ]),
    potentials: existingPath([
      vendorPotentials,
      vendorRoot && path.join(vendorRoot, 'potentials'),
      vendorRoot && path.join(vendorRoot, 'share', 'lammps', 'potentials'),
      path.join(runtimeRoot, 'share', 'lammps', 'potentials'),
      path.join(runtimeRoot, 'Library', 'share', 'lammps', 'potentials'),
    ]),
    binDirs,
  }
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close(() => resolve(port))
    })
  })
}

function waitForBackend(url) {
  const started = Date.now()
  return new Promise((resolve, reject) => {
    const probe = () => {
      const request = http.get(`${url}/api/health`, (response) => {
        response.resume()
        if (response.statusCode === 200) resolve()
        else retry()
      })
      request.setTimeout(1500, () => request.destroy())
      request.on('error', retry)
    }
    const retry = () => {
      if (Date.now() - started >= BACKEND_START_TIMEOUT_MS) {
        reject(new Error('The local MatterLab backend did not become ready in time.'))
      } else {
        setTimeout(probe, 350)
      }
    }
    probe()
  })
}

function stopBackend() {
  if (!backendProcess || backendProcess.exitCode !== null) return
  if (process.platform === 'win32') {
    spawn('taskkill', ['/PID', String(backendProcess.pid), '/T', '/F'], { windowsHide: true })
  } else {
    try { process.kill(-backendProcess.pid, 'SIGTERM') } catch { backendProcess.kill('SIGTERM') }
  }
}

async function startBackend(runtime, port) {
  const root = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..')
  const backendRoot = path.join(root, 'backend')
  const frontendRoot = path.join(root, 'frontend', ...(app.isPackaged ? [] : ['dist']))
  const userData = app.getPath('userData')
  const logsDir = path.join(userData, 'logs')
  fs.mkdirSync(logsDir, { recursive: true })
  const executables = runtimeExecutables(runtime.root)
  const pathEntries = [...executables.binDirs, process.env.PATH || ''].filter(Boolean)
  const env = {
    ...process.env,
    PYTHONPATH: [backendRoot, process.env.PYTHONPATH || ''].filter(Boolean).join(path.delimiter),
    PATH: pathEntries.join(path.delimiter),
    MATTERLAB_DESKTOP: '1',
    MATTERLAB_DESKTOP_FRONTEND_DIR: frontendRoot,
  }
  if (app.isPackaged) {
    Object.assign(env, {
      MATTERLAB_USER_DATA_DIR: userData,
      PHASE_DIAGRAM_CONFIG_FILE: path.join(userData, 'config', 'llm_config.json'),
      PHASE_DIAGRAM_ENV_FILE: path.join(userData, 'config', '.env'),
      PHASE_DIAGRAM_TMP_DIR: path.join(userData, 'outputs'),
      PHASE_DIAGRAM_PYTHON_EXECUTABLE: runtime.python,
    })
  }
  if (executables.lammps) env.LAMMPS_CMD = executables.lammps
  if (executables.potentials) env.POTENTIALS_DIR = executables.potentials

  const log = fs.createWriteStream(path.join(logsDir, 'backend.log'), { flags: 'a' })
  backendProcess = spawn(
    runtime.python,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'],
    {
      cwd: backendRoot,
      env,
      detached: process.platform !== 'win32',
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  backendProcess.stdout.pipe(log)
  backendProcess.stderr.pipe(log)
  backendProcess.on('exit', (code) => {
    if (!shuttingDown && code !== 0 && mainWindow && !mainWindow.isDestroyed()) {
      void dialog.showMessageBox(mainWindow, {
        type: 'error',
        title: 'MatterLab backend stopped',
        message: '本地计算服务意外停止。',
        detail: `退出码：${code ?? 'unknown'}\n日志：${path.join(logsDir, 'backend.log')}`,
      })
    }
  })
  return `http://127.0.0.1:${port}`
}

function createMainWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 960,
    minWidth: 1100,
    minHeight: 720,
    show: false,
    backgroundColor: '#f4f7fb',
    title: 'MatterLab 研材体',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  mainWindow.webContents.setWindowOpenHandler(({ url: target }) => {
    if (/^https?:\/\//i.test(target)) void shell.openExternal(target)
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (event, target) => {
    if (!target.startsWith(url)) event.preventDefault()
  })
  mainWindow.once('ready-to-show', () => {
    setupWindow?.close()
    setupWindow = null
    mainWindow?.show()
  })
  void mainWindow.loadURL(url)
}

async function launch() {
  try {
    const runtime = await ensurePackagedRuntime()
    showSetupWindow('正在启动本地 Agent 与科学计算服务…')
    const port = await reservePort()
    backendUrl = await startBackend(runtime, port)
    await waitForBackend(backendUrl)
    createMainWindow(backendUrl)
  } catch (error) {
    setupWindow?.close()
    const detail = error instanceof Error ? error.stack || error.message : String(error)
    await dialog.showMessageBox({
      type: 'error',
      title: 'MatterLab failed to start',
      message: 'MatterLab 启动失败。',
      detail,
    })
    app.quit()
  }
}

const hasLock = app.requestSingleInstanceLock()
if (!hasLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })
  app.whenReady().then(launch)
}

app.on('before-quit', () => {
  shuttingDown = true
  stopBackend()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length !== 0) return
  if (backendProcess && backendUrl) createMainWindow(backendUrl)
  else void launch()
})
