// OVERSEER — Electron desktop shell: borderless fullscreen (brief §3 §4.1).
// Spawns the FastAPI bridge (which serves web/dist + API), then loads it same-origin.
// Packaged: on first launch it sets up the Python env (uv sync) + models, showing a splash.
const { app, BrowserWindow, globalShortcut, ipcMain } = require('electron')
const { spawn } = require('node:child_process')
const http = require('node:http')
const path = require('node:path')
const fs = require('node:fs')

const REPO_ROOT = path.join(__dirname, '..', '..')
const SERVER_URL = process.env.OVERSEER_SERVER_URL || 'http://127.0.0.1:8787'
const SPAWN = process.env.OVERSEER_NO_SERVER !== '1'

let win = null
let server = null
let spawnedByUs = false

// ---- packaged backend (Python source + a uv binary ship as resources) --------------------
function packagedBackend() {
  const res = process.resourcesPath
  return {
    uv: path.join(res, 'bin', process.platform === 'win32' ? 'uv.exe' : 'uv'),
    src: path.join(res, 'backend'),
    run: path.join(app.getPath('userData'), 'app'),   // writable copy (data/models/.venv live here)
  }
}

function runOnce(cmd, args, opts) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, opts)
    p.on('close', (code) => resolve(code))
    p.on('error', (e) => { console.error('spawn error:', e.message); resolve(-1) })
  })
}

// Run a long step while ticking an elapsed-seconds counter onto the splash, so a multi-minute
// one-time download never looks frozen.
async function runWithHeartbeat(label, cmd, args, opts) {
  const t0 = Date.now()
  setStatus(label + '...')
  const iv = setInterval(() => setStatus(label + ' (' + Math.round((Date.now() - t0) / 1000) + 's, one-time download)...'), 3000)
  const code = await runOnce(cmd, args, opts)
  clearInterval(iv)
  return code
}

// First launch only: copy the backend to a writable dir, install deps (torch matched to this
// machine's GPU/CPU) and fetch models. Async, with splash status updates. Throws on a hard
// failure so the shell shows an error instead of a blank screen.
async function firstRunSetup(b) {
  const done = path.join(b.run, '.setup-ok')
  if (fs.existsSync(done)) return
  setStatus('First run: preparing Overseer (this can take several minutes)...')
  fs.mkdirSync(b.run, { recursive: true })
  fs.cpSync(b.src, b.run, { recursive: true })
  const env = { ...process.env, UV_PROJECT_ENVIRONMENT: path.join(b.run, '.venv') }
  const syncCode = await runWithHeartbeat('Installing the AI runtime', b.uv, ['sync'], { cwd: b.run, stdio: 'inherit', env })
  if (syncCode !== 0) throw new Error('installing the AI runtime failed (uv sync exit ' + syncCode + ')')
  // Models are best-effort: the app runs (at reduced accuracy) if some fail, so a model-fetch
  // hiccup must not block startup.
  await runWithHeartbeat('Fetching AI models', b.uv, ['run', 'python', '-m', 'match.tools.export_models'], { cwd: b.run, stdio: 'inherit', env })
  fs.writeFileSync(done, new Date().toISOString())
}

function startServer() {
  if (!SPAWN) return
  if (app.isPackaged) {
    const b = packagedBackend()
    const env = { ...process.env, UV_PROJECT_ENVIRONMENT: path.join(b.run, '.venv') }
    server = spawn(b.uv, ['run', 'python', '-m', 'server'], { cwd: b.run, stdio: 'inherit', env })
  } else {
    server = spawn('uv', ['run', 'python', '-m', 'server'], { cwd: REPO_ROOT, shell: true, stdio: 'inherit' })
  }
  spawnedByUs = true
  server.on('error', (e) => console.error('server spawn failed:', e.message))
}

// ---- helpers -----------------------------------------------------------------------------
function ping(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => { res.destroy(); resolve(res.statusCode < 500) })
    req.on('error', () => resolve(false))
    req.setTimeout(1000, () => { req.destroy(); resolve(false) })
  })
}

async function waitForServer(url, tries = 430, onTick) {
  for (let i = 0; i < tries; i++) {
    if (await ping(url)) return true
    if (onTick && i > 0 && i % 14 === 0) onTick(Math.round((i * 700) / 1000))   // ~ every 10 s
    await new Promise((r) => setTimeout(r, 700))
  }
  return false
}

function setStatus(msg) {
  if (win && !win.isDestroyed()) {
    win.webContents.executeJavaScript(`window.setStatus && window.setStatus(${JSON.stringify(msg)})`).catch(() => {})
  }
}

function createWindow() {
  win = new BrowserWindow({
    fullscreen: true, frame: false, backgroundColor: '#05070a',
    autoHideMenuBar: true, show: false,
    webPreferences: {
      contextIsolation: true, nodeIntegration: false,
      devTools: false,              // no DevTools — professional, locked-down shell
      preload: path.join(__dirname, 'preload.cjs'),
    },
  })
  win.once('ready-to-show', () => win.show())
  win.on('closed', () => (win = null))
  win.webContents.on('context-menu', (e) => e.preventDefault())
}

function registerShortcuts() {
  globalShortcut.register('CommandOrControl+Q', () => app.quit())
  const reload = () => win && win.webContents.reloadIgnoringCache()
  globalShortcut.register('CommandOrControl+R', reload)
  globalShortcut.register('F5', reload)
}

app.whenReady().then(async () => {
  createWindow()
  win.loadFile(path.join(__dirname, 'splash.html'))    // show the splash immediately
  registerShortcuts()                                  // Ctrl+Q works even during setup

  const alreadyUp = await ping(SERVER_URL)             // reuse an existing server if present
  if (!alreadyUp && SPAWN) {
    try {
      if (app.isPackaged) await firstRunSetup(packagedBackend())
      setStatus('Starting the analysis server (the first launch loads the AI models)...')
      startServer()
    } catch (e) {
      // Hard setup failure: keep the splash and explain, rather than dropping to a blank screen.
      setStatus('Setup failed: ' + e.message + '. Press Ctrl+Q, relaunch, and if it persists share the logs in ' + app.getPath('userData') + '.')
      return
    }
  }
  const up = alreadyUp || (SPAWN
    ? await waitForServer(SERVER_URL, 430, (s) => setStatus('Starting the analysis server... (' + s + 's) the first launch loads the AI models, this can take a couple of minutes.'))
    : await ping(SERVER_URL))
  if (up) {
    win.loadURL(SERVER_URL)
  } else if (!SPAWN) {
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))   // dev / no-server mode only
  } else {
    // Do NOT load dist over file:// here: the SPA uses absolute asset paths and would render a
    // blank grey screen. Keep the splash up and tell the operator what to do.
    setStatus('The analysis server did not come up in time. Press Ctrl+Q, relaunch once more, and if it keeps happening share the logs in ' + app.getPath('userData') + '.')
  }
})

ipcMain.on('app-quit', () => app.quit())

app.on('window-all-closed', () => app.quit())
app.on('will-quit', () => {
  globalShortcut.unregisterAll()
  if (server && spawnedByUs) { try { server.kill() } catch (_) { /* noop */ } }
})
