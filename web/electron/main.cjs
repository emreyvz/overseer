// OVERSEER — Electron desktop shell: borderless fullscreen (brief §3 §4.1).
// Spawns the FastAPI bridge (uv run python -m server) which serves web/dist + API,
// then loads it same-origin. Falls back to the local build if the server is absent.
const { app, BrowserWindow, globalShortcut, ipcMain } = require('electron')
const { spawn } = require('node:child_process')
const http = require('node:http')
const path = require('node:path')

const REPO_ROOT = path.join(__dirname, '..', '..')
const SERVER_URL = process.env.OVERSEER_SERVER_URL || 'http://127.0.0.1:8787'
const SPAWN = process.env.OVERSEER_NO_SERVER !== '1'

let win = null
let server = null
let spawnedByUs = false

function startServer() {
  if (!SPAWN) return
  // Use the venv's Python DIRECTLY, not `uv run` — `uv run` re-syncs the env to the lock on every
  // launch and strips the spatial 3D extras installed --no-deps (MoGe / ROMP / bgutil), so the 3D
  // bodies + geometry silently vanish. Fall back to `uv run` only if the venv isn't built yet.
  const fs = require('node:fs')
  const venvPy = process.platform === 'win32'
    ? path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(REPO_ROOT, '.venv', 'bin', 'python')
  const [cmd, args] = fs.existsSync(venvPy)
    ? [venvPy, ['-m', 'server']]
    : ['uv', ['run', 'python', '-m', 'server']]
  server = spawn(cmd, args, { cwd: REPO_ROOT, shell: true, stdio: 'inherit' })
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
