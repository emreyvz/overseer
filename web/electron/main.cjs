// OVERSEER — Electron desktop shell: borderless fullscreen (brief §3 §4.1).
// Spawns the FastAPI bridge (uv run python -m server) which serves web/dist + API,
// then loads it same-origin. Falls back to the local build if the server is absent.
const { app, BrowserWindow, globalShortcut, ipcMain } = require('electron')
const { spawn, spawnSync } = require('node:child_process')
const http = require('node:http')
const path = require('node:path')
const fs = require('node:fs')

const REPO_ROOT = path.join(__dirname, '..', '..')
const SERVER_URL = process.env.OVERSEER_SERVER_URL || 'http://127.0.0.1:8787'
const SPAWN = process.env.OVERSEER_NO_SERVER !== '1'

let win = null
let server = null
let spawnedByUs = false

// Packaged app: the Python backend source + a uv binary ship as resources. On first run we copy
// the source to a writable per-user dir, `uv sync` there (downloads the right torch for this
// machine) and fetch models, then run the server from that dir.
function packagedBackend() {
  const res = process.resourcesPath
  return {
    uv: path.join(res, 'bin', process.platform === 'win32' ? 'uv.exe' : 'uv'),
    src: path.join(res, 'backend'),
    run: path.join(app.getPath('userData'), 'app'),
  }
}

function firstRunSetup(b) {
  const done = path.join(b.run, '.setup-ok')
  if (fs.existsSync(done)) return
  try {
    fs.mkdirSync(b.run, { recursive: true })
    fs.cpSync(b.src, b.run, { recursive: true })          // copy Python source to a writable dir
    const env = { ...process.env, UV_PROJECT_ENVIRONMENT: path.join(b.run, '.venv') }
    spawnSync(b.uv, ['sync'], { cwd: b.run, stdio: 'inherit', env })
    spawnSync(b.uv, ['run', 'python', '-m', 'match.tools.export_models'], { cwd: b.run, stdio: 'inherit', env })
    fs.writeFileSync(done, new Date().toISOString())
  } catch (e) {
    console.error('first-run setup failed:', e.message)
  }
}

function startServer() {
  if (!SPAWN) return
  if (app.isPackaged) {
    const b = packagedBackend()
    firstRunSetup(b)                                        // blocking on first launch only
    const env = { ...process.env, UV_PROJECT_ENVIRONMENT: path.join(b.run, '.venv') }
    server = spawn(b.uv, ['run', 'python', '-m', 'server'], { cwd: b.run, stdio: 'inherit', env })
  } else {
    server = spawn('uv', ['run', 'python', '-m', 'server'], { cwd: REPO_ROOT, shell: true, stdio: 'inherit' })
  }
  spawnedByUs = true
  server.on('error', (e) => console.error('server spawn failed:', e.message))
}

function ping(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => { res.destroy(); resolve(res.statusCode < 500) })
    req.on('error', () => resolve(false))
    req.setTimeout(1000, () => { req.destroy(); resolve(false) })
  })
}

async function waitForServer(url, tries = 30) {
  for (let i = 0; i < tries; i++) {
    if (await ping(url)) return true
    await new Promise((r) => setTimeout(r, 700))
  }
  return false
}

function createWindow() {
  win = new BrowserWindow({
    fullscreen: true, frame: false, backgroundColor: '#000000',
    autoHideMenuBar: true, show: false,
    webPreferences: {
      contextIsolation: true, nodeIntegration: false,
      devTools: false,              // no DevTools — professional, locked-down shell
      preload: path.join(__dirname, 'preload.cjs'),
    },
  })
  win.once('ready-to-show', () => win.show())
  win.on('closed', () => (win = null))
  // No right-click context menu.
  win.webContents.on('context-menu', (e) => e.preventDefault())
}

app.whenReady().then(async () => {
  createWindow()
  const alreadyUp = await ping(SERVER_URL)
  if (!alreadyUp) startServer()               // reuse an existing server instead of double-binding
  const up = alreadyUp || (SPAWN ? await waitForServer(SERVER_URL) : await ping(SERVER_URL))
  if (up) {
    win.loadURL(SERVER_URL)
  } else {
    // no backend, load the local build; UI shows NO SIGNAL (or add ?sim=1)
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }

  globalShortcut.register('CommandOrControl+Q', () => app.quit())
  const reload = () => win && win.webContents.reloadIgnoringCache()
  globalShortcut.register('CommandOrControl+R', reload)
  globalShortcut.register('F5', reload)
})

ipcMain.on('app-quit', () => app.quit())

app.on('window-all-closed', () => app.quit())
app.on('will-quit', () => {
  globalShortcut.unregisterAll()
  if (server && spawnedByUs) { try { server.kill() } catch (_) { /* noop */ } }
})
