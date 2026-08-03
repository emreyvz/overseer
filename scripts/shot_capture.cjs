// Offscreen Electron screenshot of the real frontend running in SIM mode (?sim=1).
const { app, BrowserWindow } = require('electron')
const fs = require('fs')

app.commandLine.appendSwitch('no-sandbox')
app.commandLine.appendSwitch('use-gl', 'angle')
app.commandLine.appendSwitch('use-angle', 'swiftshader')
app.commandLine.appendSwitch('ignore-gpu-blocklist')
setTimeout(() => { console.log('HARD TIMEOUT'); process.exit(0) }, 38000)

const arg = (k, d) => { const a = process.argv.find((x) => x.startsWith(`--${k}=`)); return a ? a.split('=').slice(1).join('=') : d }
const URL = arg('url', 'http://127.0.0.1:8799/?sim=1&shot=operator')
const OUT = arg('out', 'operator_shot.png')
const SEL = arg('sel', '[aria-label="AI Operator"]')
const log = (...a) => console.log('[cap]', ...a)
const withTimeout = (p, ms, tag) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout ' + tag)), ms))])

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1920, height: 1080, show: false, paintWhenInitiallyHidden: true,
    webPreferences: { webSecurity: false, backgroundThrottling: false },
  })
  win.webContents.on('console-message', (_e, lvl, msg) => { if (lvl >= 2) log('PAGE-ERR:', msg.slice(0, 200)) })
  win.webContents.on('did-fail-load', (_e, code, desc) => log('did-fail-load', code, desc))
  try {
    log('loading', URL)
    await withTimeout(win.loadURL(URL), 15000, 'load')
    log('loaded')
  } catch (e) { log('load error', e.message) }
  const t0 = Date.now()
  let seen = false
  while (Date.now() - t0 < 12000) {
    seen = await win.webContents.executeJavaScript(`!!document.querySelector(${JSON.stringify(SEL)})`).catch(() => false)
    if (seen) { log('selector present'); break }
    await new Promise((r) => setTimeout(r, 400))
  }
  if (!seen) log('selector NOT found; capturing anyway')
  await new Promise((r) => setTimeout(r, 1500))
  try {
    log('capturing')
    const img = await withTimeout(win.webContents.capturePage(), 10000, 'capture')
    fs.writeFileSync(OUT, img.toPNG())
    log('WROTE', OUT, fs.statSync(OUT).size, 'bytes')
  } catch (e) { log('capture error', e.message) }
  try { win.destroy() } catch (e) {}
  app.quit(); process.exit(0)
})
