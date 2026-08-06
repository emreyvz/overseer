// Headless verification that every new PERCEPTION surface actually mounts, is visible, is the
// right size, contains the parts it is supposed to contain, and logs no runtime errors.
// Typechecking cannot catch a component that throws on mount or loops forever in an effect.
const { app, BrowserWindow } = require('electron')
app.commandLine.appendSwitch('no-sandbox')
app.commandLine.appendSwitch('use-gl', 'angle')
app.commandLine.appendSwitch('use-angle', 'swiftshader')
// 15 checks, each paying for a fresh BrowserWindow. Under software GL that setup can cost the
// best part of a minute on a loaded machine, so this ceiling is a runaway guard, not a budget.
setTimeout(() => { console.log('HARD TIMEOUT'); process.exit(1) }, 1800000)
// destroying each window between cases would otherwise fire window-all-closed and quit the app
// after the first surface, which is why an earlier run only ever reported one result
app.on('window-all-closed', () => {})

const BASE = 'http://127.0.0.1:8799/index.html'
// Each row is [shot, rootSelector, requiredParts]. A detail screen additionally declares how
// many <Explain> markers it must carry, because "the operator cannot tell what any of this is"
// was the actual defect and a screen that quietly loses its explanations regresses straight back
// into it. `.intro` is the plain-language line that answers "what am I looking at".
const CASES = [
  ['fog',          '.fog canvas.scrim', ['.hud .col .card']],
  ['coverage',     '.cs',               ['.eyebrow', '.gauge', '.queue', '.intro'], 3],
  ['grain',        '.gf canvas.field',  ['.hud .col .card']],
  ['grainscreen',  '.gs',               ['.dial svg', '.ledger', '.foot .fr', '.intro'], 3],
  ['dream',        '.dv',               ['.rb .cap']],
  ['dreamconsole', '.dc',               ['.wipewrap', '.divider', '.slider', '.grid', '.intro'], 3],
  ['eardrum',      '.ed',               ['.strip', '.sgram', '.analysis .card', '.intro'], 2],
  ['probes',       '.lp.placing',       ['.tools', '.probe .c']],
  ['bedrock',      '.bd',               ['.lens .clauses', '.seg', '.band .track', '.intro'], 2],
  // The readout card must carry its heading, what it is FOR, and a visible way in. The DORI
  // band list used to live here and was moved to the screen that button opens.
  ['fog',          '.hud .col .card',   ['.ring', '.hd .nm', '.hd .for', 'button.open']],
]

app.whenReady().then(async () => {
  let bad = 0
  for (const [shot, root, parts, minExplain = 0] of CASES) {
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
        explains: document.querySelectorAll('.ex').length,
      }
    })()`).catch((e) => ({ root: false, err: String(e) }))
    const missing = (res.parts || []).filter(([, ok]) => !ok).map(([s]) => s)
    const thin = (res.explains || 0) < minExplain
    const ok = res.root && res.visible && res.w > 100 && res.h > 100 && !missing.length
      && !thin && !errs.length
    if (!ok) bad++
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${shot.padEnd(13)} root=${res.root} vis=${res.visible} ${res.w}x${res.h}`
      + (minExplain ? ` explain=${res.explains}/${minExplain}` : '')
      + (missing.length ? ` MISSING=${missing.join(',')}` : '')
      + (errs.length ? ` ERRORS=${errs.join(' | ')}` : ''))
    win.destroy()
    await new Promise((r) => setTimeout(r, 400))
  }
  // ── second pass: does asking a term actually answer? ──────────────────────────────────────
  // "I cannot tell what any of this is without asking you" was reported twice. The fix is a
  // promise made by a dotted underline: one sentence of plain English is a hover away. A promise
  // is worth testing, so every marker on every detail screen is hovered and required to produce
  // a readable bubble that is fully on-screen and that nothing paints over.
  for (const shot of ['coverage', 'grainscreen', 'dreamconsole', 'eardrum', 'bedrock']) {
    const win = new BrowserWindow({
      width: 1920, height: 1080, show: false, paintWhenInitiallyHidden: true,
      webPreferences: { webSecurity: false, backgroundThrottling: false },
    })
    await win.loadURL(`${BASE}?v=${Date.now()}&sim=1&shot=${shot}`).catch(() => {})
    await new Promise((r) => setTimeout(r, 4200))
    const marks = await win.webContents.executeJavaScript(`(async () => {
      const out = []
      for (const m of [...document.querySelectorAll('.ex')]) {
        // A real operator scrolls a marker into view before hovering it; without this the test
        // hovers markers parked below the fold of a scrolling panel and blames the placement.
        m.scrollIntoView({ block: 'center' })
        await new Promise((r) => setTimeout(r, 40))
        m.dispatchEvent(new MouseEvent('mouseenter'))
        await new Promise((r) => setTimeout(r, 60))
        document.getAnimations().forEach((a) => { try { a.finish() } catch {} })
        const b = document.querySelector('.bub')
        const term = m.textContent.trim().slice(0, 24)
        if (!b) { out.push({ term, ok: false, why: 'no bubble' }) }
        else {
          const r = b.getBoundingClientRect()
          const words = (b.querySelector('.bw')?.textContent || '').trim()
          // elementFromPoint sees straight THROUGH pointer-events:none, which the bubble sets on
          // purpose so it never steals the hover that opened it. Re-enable hit testing for the
          // measurement only: it changes nothing about paint order, which is what is under test.
          const pe = b.style.pointerEvents
          b.style.pointerEvents = 'auto'
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
          b.style.pointerEvents = pe
          out.push({
            term,
            ok: r.width > 100 && r.height > 20 && words.length > 20
              && r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth
              && !!hit && (hit === b || b.contains(hit)),
            why: \`\${Math.round(r.left)},\${Math.round(r.top)} \${Math.round(r.width)}x\${Math.round(r.height)} words=\${words.length} covered-by=\${hit ? hit.className : 'nothing'}\`,
          })
        }
        m.dispatchEvent(new MouseEvent('mouseleave'))
        await new Promise((r) => setTimeout(r, 20))
      }
      return out
    })()`).catch((e) => [{ term: '?', ok: false, why: String(e).slice(0, 120) }])
    const fails = marks.filter((m) => !m.ok)
    if (fails.length || !marks.length) bad++
    console.log(`${fails.length || !marks.length ? 'FAIL' : 'PASS'}  ${`explain:${shot}`.padEnd(21)} ${marks.length} marker(s)`)
    for (const f of fails) console.log(`        x "${f.term}" — ${f.why}`)
    win.destroy()
    await new Promise((r) => setTimeout(r, 300))
  }

  console.log(bad ? `\n${bad} CHECK(S) FAILED` : '\nALL SURFACES OK · EVERY MARKER ANSWERS')
  process.exit(bad ? 1 : 0)
})
