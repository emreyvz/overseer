// Headless-ish Chromium (Electron) capture harness for the 3D SPATIAL render stack.
// Loads scripts/gl_verify_spatial.html offscreen (real WebGL: GTAO + bloom + tone mapping +
// sky dome on a live scene), waits for it to finish, and screenshots before/after PNGs so the
// GLSL output can be verified without the Python harness (which can't run shaders).
//   run from web/:  node_modules/.bin/electron ../scripts/gl_capture.cjs --sid=8
const { app, BrowserWindow } = require('electron')
const path = require('path')
const fs = require('fs')

const PAGE = 'file://' + path.resolve(__dirname, 'gl_verify_spatial.html').replace(/\\/g, '/')
const TMP = process.env.TEMP || process.env.TMP || '.'
const sidArg = process.argv.find((a) => a.startsWith('--sid='))
const SID = sidArg ? sidArg.split('=')[1] : '8'
const pitchArg = process.argv.find((a) => a.startsWith('--pitch='))
const PITCH = pitchArg ? pitchArg.split('=')[1] : ''
const yawArg = process.argv.find((a) => a.startsWith('--yaw='))
const YAW = yawArg ? yawArg.split('=')[1] : ''

async function shot(mode, out) {
  const win = new BrowserWindow({
    width: 1280, height: 800, show: false, paintWhenInitiallyHidden: true,
    webPreferences: { webSecurity: false, backgroundThrottling: false },
  })
  const errs = []
  win.webContents.on('console-message', (_e, _l, msg) => { if (/error|Error|undefined/.test(msg)) errs.push(msg) })
  let loaded = false
  for (let attempt = 0; attempt < 4 && !loaded; attempt++) {
    try { await win.loadURL(`${PAGE}?mode=${mode}&sid=${SID}${PITCH ? '&pitch=' + PITCH : ''}${YAW ? '&yaw=' + YAW : ''}`); loaded = true }
    catch (e) { await new Promise((r) => setTimeout(r, 600)) }
  }
  if (!loaded) { console.log(`[${mode}] load failed`); win.destroy(); return }
  const t0 = Date.now()
  let info = null
  while (Date.now() - t0 < 90000) {
    info = await win.webContents.executeJavaScript('({done: window.__done===true, err: window.__error||null, tris: window.__meshTris||0, measure: window.__measure||null})').catch(() => null)
    if (info && info.done) break
    await new Promise((r) => setTimeout(r, 300))
  }
  await new Promise((r) => setTimeout(r, 400))
  const img = await win.webContents.capturePage()
  fs.writeFileSync(out, img.toPNG())
  win.destroy()
  console.log(`[${mode}] tris=${info && info.tris} measure=${info ? JSON.stringify(info.measure) : '?'} err=${info && info.err ? info.err.split('\n')[0] : 'none'} console=${errs.slice(0, 2).join(' | ') || 'clean'} -> ${out}`)
}

const modeArg = process.argv.find((a) => a.startsWith('--mode='))
const MODE = modeArg ? modeArg.split('=')[1] : null   // one process per mode avoids GPU teardown races
app.whenReady().then(async () => {
  try {
    const modes = MODE ? [MODE] : ['before', 'after']
    for (const m of modes) await shot(m, path.join(TMP, `rv_gl_${m}.png`))
  } catch (e) { console.error('CAPTURE FAIL', e) }
  app.quit()
})
