<script lang="ts">
  // EARDRUM — probe placement and the live brackets.
  //
  // The placement affordance is the whole trick: an operator cannot see texture, and a probe on
  // a blank wall returns noise forever without ever saying so. The cursor carries a live
  // trackability meter, and a box drawn somewhere hopeless is refused with the reason.
  import { onDestroy, onMount } from 'svelte'
  import { eardrumDrawer, listenPlacing, modules, probeFrames, probes, toggleModule } from '../../lib/stores'
  import { addProbe, deleteProbe, levelTone, loadProbes, suggestProbes } from '../../lib/eardrum'
  import { sfx } from '../../lib/audio'
  import type { Probe } from '../../lib/types'

  let host = $state<HTMLDivElement>()
  let scope = $state<HTMLCanvasElement>()
  let drawing = $state<{ x: number; y: number; w: number; h: number } | null>(null)
  let cursor = $state<{ x: number; y: number } | null>(null)
  let candidates = $state<{ roi: [number, number, number, number]; texture: number; rigid: boolean }[]>([])
  let refused = $state<string | null>(null)
  let raf = 0
  let lastT = 0

  const placing = $derived($listenPlacing)
  const frames = $derived($probeFrames)

  // A rough local trackability read so the cursor can advise BEFORE a probe is committed. The
  // authoritative score is the backend's; this only has to be right about "blank versus not".
  let texture = $state(0)
  function sampleTexture(nx: number, ny: number) {
    const img = document.querySelector('.pov img.feed') as HTMLImageElement | null
    const cv = document.createElement('canvas')
    cv.width = cv.height = 32
    const ctx = cv.getContext('2d', { willReadFrequently: true })
    if (!ctx || !img || !img.naturalWidth) { texture = 0; return }
    try {
      const sx = nx * img.naturalWidth - 32, sy = ny * img.naturalHeight - 32
      ctx.drawImage(img, sx, sy, 64, 64, 0, 0, 32, 32)
      const d = ctx.getImageData(0, 0, 32, 32).data
      let mean = 0
      for (let i = 0; i < d.length; i += 4) mean += d[i]
      mean /= d.length / 4
      let varr = 0
      for (let i = 0; i < d.length; i += 4) varr += (d[i] - mean) ** 2
      texture = Math.min(1, Math.sqrt(varr / (d.length / 4)) / 42)
    } catch { texture = 0 }
  }

  const textureWord = $derived(texture > 0.42 ? 'GOOD' : texture > 0.16 ? 'WEAK' : 'NONE')

  function pos(e: PointerEvent): { x: number; y: number } {
    const r = host!.getBoundingClientRect()
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height }
  }
  function down(e: PointerEvent) {
    if (!placing || !host) return
    const p = pos(e)
    drawing = { x: p.x, y: p.y, w: 0, h: 0 }
  }
  function move(e: PointerEvent) {
    if (!host) return
    const p = pos(e)
    cursor = p
    if (!drawing) { sampleTexture(p.x, p.y); return }
    drawing = { ...drawing, w: p.x - drawing.x, h: p.y - drawing.y }
  }
  async function up() {
    if (!drawing) return
    const d = drawing
    drawing = null
    const roi: [number, number, number, number] = [
      Math.min(d.x, d.x + d.w), Math.min(d.y, d.y + d.h),
      Math.max(0.03, Math.abs(d.w)), Math.max(0.03, Math.abs(d.h)),
    ]
    const r = await addProbe(roi)
    if (r.ok) { sfx('sonar'); ensureModule() }
    else {
      refused = r.reason ?? 'REFUSED'
      sfx('glitch')
      setTimeout(() => (refused = null), 2600)
    }
  }

  function ensureModule() {
    const m = $modules.find((x) => x.key === 'listen')
    if (m && !m.on) toggleModule('listen')
  }

  async function suggest() {
    sfx('sonar')
    candidates = await suggestProbes(5)
  }
  async function accept(c: { roi: [number, number, number, number] }) {
    const r = await addProbe(c.roi)
    if (r.ok) { candidates = candidates.filter((x) => x !== c); sfx('click'); ensureModule() }
  }
  async function acceptAll() {
    for (const c of [...candidates]) await accept(c)
  }

  // the micro-scope inside each bracket: ambient, tiny, and it makes the frame feel alive
  function frame(now: number) {
    raf = requestAnimationFrame(frame)
    if (now - lastT < 84) return
    lastT = now
    const cv = scope
    if (!cv || !host) return
    const w = host.clientWidth, h = host.clientHeight
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h }
    const ctx = cv.getContext('2d')!
    ctx.clearRect(0, 0, w, h)
    ctx.lineWidth = 1
    for (const p of $probes) {
      const f = frames[String(p.id)]
      if (!f || !f.wave.length || p.kind === 'ref') continue
      const bx = p.roi[0] * w, by = p.roi[1] * h
      const bw = p.roi[2] * w, bh = p.roi[3] * h
      const mid = by + bh / 2
      const amp = bh * 0.34
      const peak = Math.max(...f.wave.map(Math.abs)) || 1
      ctx.beginPath()
      f.wave.forEach((v, i) => {
        const x = bx + (i / (f.wave.length - 1)) * bw
        const y = mid - (v / peak) * amp
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
      })
      ctx.strokeStyle = 'rgba(56,208,227,0.5)'
      ctx.stroke()
    }
  }

  onMount(() => {
    loadProbes()
    raf = requestAnimationFrame(frame)
  })
  onDestroy(() => cancelAnimationFrame(raf))

  const label = (p: Probe) => (p.kind === 'ref' ? '⚓ REF' : p.name)
  const level = (p: Probe) => {
    const f = frames[String(p.id)]
    if (!f) return 0
    return Math.min(1, Math.max(0.02, (f.db + 6) / 24))
  }
</script>

<div class="lp" class:placing bind:this={host}
  onpointerdown={down} onpointermove={move} onpointerup={up} onpointerleave={() => (drawing = null)}
  role="presentation">
  <canvas bind:this={scope} class="scope"></canvas>

  <!-- committed probes -->
  {#each $probes as p (p.id)}
    {@const f = frames[String(p.id)]}
    <div class="probe t-{levelTone(f)}" class:ref={p.kind === 'ref'} class:sat={f?.saturated}
      style={`left:${p.roi[0] * 100}%; top:${p.roi[1] * 100}%; width:${p.roi[2] * 100}%; height:${p.roi[3] * 100}%`}>
      <span class="c tl"></span><span class="c tr"></span><span class="c bl"></span><span class="c br"></span>
      <span class="plabel caps">{label(p)}</span>
      {#if p.kind !== 'ref'}
        <span class="lvl"><span class="lfill" style={`width:${level(p) * 100}%`}></span></span>
      {/if}
      {#if placing}
        <button class="del" onclick={(e) => { e.stopPropagation(); deleteProbe(p.id) }} aria-label="remove probe">✕</button>
      {/if}
    </div>
  {/each}

  <!-- suggested candidates: dashed until accepted -->
  {#each candidates as c, i (i)}
    <button class="cand" style={`left:${c.roi[0] * 100}%; top:${c.roi[1] * 100}%; width:${c.roi[2] * 100}%; height:${c.roi[3] * 100}%`}
      onclick={(e) => { e.stopPropagation(); accept(c) }}>
      <span class="crank caps">{i + 1}</span>
      <span class="ctex caps">{Math.round(c.texture * 100)}%{c.rigid ? ' · RIGID' : ''}</span>
    </button>
  {/each}

  {#if drawing}
    <div class="draw" style={`left:${Math.min(drawing.x, drawing.x + drawing.w) * 100}%;
      top:${Math.min(drawing.y, drawing.y + drawing.h) * 100}%;
      width:${Math.abs(drawing.w) * 100}%; height:${Math.abs(drawing.h) * 100}%`}></div>
  {/if}

  {#if placing}
    {#if cursor}
      <div class="tex caps" style={`left:${cursor.x * 100}%; top:${cursor.y * 100}%`}>
        <span class="bars">
          {#each [0.16, 0.34, 0.52] as t}<span class="b" class:on={texture > t}></span>{/each}
        </span>
        TEXTURE: {textureWord}
      </div>
    {/if}
    <div class="tools caps">
      <button class="tb" onclick={suggest}>◈ SUGGEST PROBES</button>
      {#if candidates.length}<button class="tb" onclick={acceptAll}>ACCEPT ALL<span class="k">A</span></button>{/if}
      <button class="tb" onclick={() => { eardrumDrawer.set(true) }}>⌁ ANALYSE<span class="k">⇧L</span></button>
      <button class="tb x" onclick={() => listenPlacing.set(false)}>✕ DONE</button>
    </div>
    <div class="hint caps">DRAG A BOX ON A TEXTURED, RIGID SURFACE. THE FIRST PROBE BECOMES THE REFERENCE.</div>
  {/if}

  {#if refused}
    <div class="refused caps">{refused}</div>
  {/if}
</div>

<style>
  .lp { position: absolute; inset: 0; z-index: 9; pointer-events: none; }
  .lp.placing { pointer-events: auto; cursor: crosshair;
    background: radial-gradient(ellipse at center, rgba(0,0,0,0.05), rgba(0,0,0,0.30)); }
  .scope { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }

  /* corner brackets, matching the app's reticle idiom rather than a solid box */
  .probe { position: absolute; pointer-events: none; }
  .c { position: absolute; width: 7px; height: 7px; border: 1px solid var(--cyan); }
  .c.tl { left: 0; top: 0; border-right: none; border-bottom: none; }
  .c.tr { right: 0; top: 0; border-left: none; border-bottom: none; }
  .c.bl { left: 0; bottom: 0; border-right: none; border-top: none; }
  .c.br { right: 0; bottom: 0; border-left: none; border-top: none; }
  .probe.ref .c { border-color: var(--ink-dim); }
  .probe.t-warn .c { border-color: var(--amber); }
  .probe.t-hot .c { border-color: var(--scarlet); box-shadow: 0 0 6px var(--scarlet-glow); }
  .probe.sat { opacity: 0.4; }
  .plabel { position: absolute; left: 0; top: -12px; font-size: 8px; color: var(--cyan);
    letter-spacing: 0.12em; text-shadow: 0 0 4px #000; white-space: nowrap; }
  .probe.ref .plabel { color: var(--ink-dim); }
  .probe.t-hot .plabel { color: var(--scarlet); }
  .lvl { position: absolute; left: 0; right: 0; bottom: -4px; height: 2px; background: rgba(236,236,236,0.12); }
  .lfill { position: absolute; inset: 0 auto 0 0; background: var(--cyan); transition: width 240ms; }
  .probe.t-warn .lfill { background: var(--amber); }
  .probe.t-hot .lfill { background: var(--scarlet); box-shadow: 0 0 5px var(--scarlet-glow); }
  .del { position: absolute; right: -8px; top: -14px; pointer-events: auto; background: none;
    border: none; color: var(--ink-ghost); font-size: 9px; cursor: crosshair; }
  .del:hover { color: var(--scarlet); }

  .cand { position: absolute; pointer-events: auto; background: rgba(47,191,143,0.06);
    border: 1px dashed var(--jade); cursor: crosshair; padding: 0; }
  .cand:hover { background: rgba(47,191,143,0.16); }
  .crank { position: absolute; left: 3px; top: 2px; font-size: 8px; color: var(--jade); }
  .ctex { position: absolute; left: 0; bottom: -11px; font-size: 7px; color: var(--jade);
    letter-spacing: 0.1em; white-space: nowrap; }

  .draw { position: absolute; border: 1px solid var(--cyan); background: rgba(56,208,227,0.08); }

  .tex { position: absolute; transform: translate(14px, -20px); display: flex; align-items: center;
    gap: 6px; font-size: 8px; color: var(--ink-dim); letter-spacing: 0.12em; pointer-events: none;
    background: rgba(4,7,10,0.7); padding: 2px 6px; white-space: nowrap; }
  .bars { display: inline-flex; gap: 2px; align-items: flex-end; }
  .b { width: 3px; height: 4px; background: var(--ink-ghost); }
  .b:nth-child(2) { height: 6px; } .b:nth-child(3) { height: 8px; }
  .b.on { background: var(--cyan); }

  .tools { position: absolute; left: 50%; top: 22px; transform: translateX(-50%); display: flex;
    gap: 7px; pointer-events: auto; }
  .tb { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px;
    border: 1px solid var(--hairline); background: rgba(4,7,10,0.8); color: var(--ink-dim);
    font-size: 8px; letter-spacing: 0.14em; cursor: crosshair; }
  .tb:hover { border-color: var(--cyan); color: var(--cyan); }
  .tb.x:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .tb .k { border: 1px solid var(--ink-ghost); padding: 0 3px; font-size: 7px; }
  .hint { position: absolute; left: 50%; bottom: 118px; transform: translateX(-50%); font-size: 8px;
    color: var(--ink-ghost); letter-spacing: 0.14em; white-space: nowrap; }
  .refused { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
    padding: 8px 14px; border: 1px solid var(--scarlet); background: rgba(4,7,10,0.85);
    color: var(--scarlet); font-size: 9px; letter-spacing: 0.14em;
    animation: rin 200ms var(--ease) both; }
  @keyframes rin { from { opacity: 0; transform: translate(-50%, -46%); } }
</style>
