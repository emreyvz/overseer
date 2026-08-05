// Headless verification that every new PERCEPTION surface actually mounts, is visible, is the
// right size, contains the parts it is supposed to contain, and logs no runtime errors.
// Typechecking cannot catch a component that throws on mount or loops forever in an effect.
const { app, BrowserWindow } = require('electron')
app.commandLine.appendSwitch('no-sandbox')
app.commandLine.appendSwitch('use-gl', 'angle')
app.commandLine.appendSwitch('use-angle', 'swiftshader')
setTimeout(() => { console.log('HARD TIMEOUT'); process.exit(1) }, 180000)
// destroying each window between cases would otherwise fire window-all-closed and quit the app
// after the first surface, which is why an earlier run only ever reported one result
app.on('window-all-closed', () => {})

const BASE = 'http://127.0.0.1:8799/index.html'
const CASES = [
  ['fog',          '.fog canvas.scrim', ['.hud .col .card']],
  ['coverage',     '.cs',               ['.eyebrow', '.gauge', '.queue']],
  ['grain',        '.gf canvas.field',  ['.hud .col .card']],
  ['grainscreen',  '.gs',               ['.dial svg', '.ledger', '.foot .fr']],
  ['dream',        '.dv',               ['.rb .cap']],
  ['dreamconsole', '.dc',               ['.wipewrap', '.divider', '.slider', '.grid']],
  ['eardrum',      '.ed',               ['.strip', '.sgram', '.analysis .card']],
  ['probes',       '.lp.placing',       ['.tools', '.probe .c']],
  ['bedrock',      '.bd',               ['.lens .clauses', '.seg', '.band .track']],
  ['fog',          '.hud .col .card',   ['.ring', '.band', '.mean']],
]

app.whenReady().then(async () => {
  let bad = 0
  for (const [shot, root, parts] of CASES) {
    const win = new BrowserWindow({
      width: 1920, height: 1080, show: false, paintWhenInitiallyHidden: true,
      webPreferences: { webSecurity: false, backgroundThrottling: false },
    })
    const errs = []
    win.webContents.on('console-message', (_e, lvl, msg) => {
      if (lvl >= 2 && !/WebGL|Security Warning|Insecure|allowRunning/.test(String(msg))) {
        errs.push(String(msg).slice(0, 140))
      }
    })
    let loaded = false
    for (let a = 0; a < 4 && !loaded; a++) {
      try { await win.loadURL(`${BASE}?v=${Date.now()}&sim=1&shot=${shot}`); loaded = true }
      catch { await new Promise((r) => setTimeout(r, 900)) }
    }
    if (!loaded) { console.log(`FAIL  ${shot.padEnd(13)} could not load`); bad++; win.destroy(); continue }
    await new Promise((r) => setTimeout(r, 4500))
    // A hidden window never ticks animations: they sit at currentTime 0 holding their `from`
    // keyframe, so an entry animation that starts at opacity 0 reads as invisible forever. Fast
    // forward them before measuring, or the harness reports false failures.
    await win.webContents.executeJavaScript(
      `document.getAnimations().forEach((a) => { try { a.finish() } catch {} })`).catch(() => {})
    const res = await win.webContents.executeJavaScript(`(() => {
      const r = document.querySelector(${JSON.stringify(root)})
      if (!r) return { root: false }
      const cs = getComputedStyle(r), b = r.getBoundingClientRect()
      return {
        root: true,
        visible: cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity > 0.01,
        w: Math.round(b.width), h: Math.round(b.height),
        parts: ${JSON.stringify(parts)}.map((s) => [s, !!document.querySelector(s)]),
      }
    })()`).catch((e) => ({ root: false, err: String(e) }))
    const missing = (res.parts || []).filter(([, ok]) => !ok).map(([s]) => s)
    const ok = res.root && res.visible && res.w > 100 && res.h > 100 && !missing.length && !errs.length
    if (!ok) bad++
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${shot.padEnd(13)} root=${res.root} vis=${res.visible} ${res.w}x${res.h}`
      + (missing.length ? ` MISSING=${missing.join(',')}` : '')
      + (errs.length ? ` ERRORS=${errs.join(' | ')}` : ''))
    win.destroy()
    await new Promise((r) => setTimeout(r, 400))
  }
  console.log(bad ? `\n${bad} SURFACE(S) FAILED` : '\nALL SURFACES OK')
  process.exit(bad ? 1 : 0)
})
