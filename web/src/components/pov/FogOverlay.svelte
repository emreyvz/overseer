<script lang="ts">
  // FOG OF WAR — what this camera cannot see, drawn over the feed.
  //
  // The visual language for "unknown" is absence and noise, never colour: a dark scrim carrying
  // animated film grain and 45-degree hairlines, so it reads as static rather than as an alarm.
  // Scarlet is reserved for the one thing here that IS an alarm — a subject who walked into a
  // shadow and has not walked out.
  //
  // Performance: the whole scrim is two canvas draws (pattern, then a destination-in mask) at a
  // hard-throttled 12 fps. Overlays share the main thread with the video decode, and this app
  // has already paid once for an overlay that ran an O(n^2) pass every rAF.
  import { onDestroy, onMount } from 'svelte'
  import { coverage, coverageScreen, detections } from '../../lib/stores'
  import type { Coverage, DoriTask } from '../../lib/types'

  const RC = 2 * Math.PI * 26        // ring circumference, matching the suggestions gauge

  const TICK_MS = 84                 // ~12 fps: grain only needs to shimmer, not animate
  const TILE = 64

  let cv = $state<HTMLCanvasElement>()
  let hoverBand = $state<DoriTask | null>(null)

  let mask: HTMLCanvasElement | null = null
  let pattern: CanvasPattern | null = null
  let raf = 0
  let lastT = 0
  let scroll = 0
  let maskKey = ''

  const cov = $derived($coverage)

  // ── the noise + hairline tile ─────────────────────────────────────────────────────────────
  function buildTile(): HTMLCanvasElement {
    const t = document.createElement('canvas')
    t.width = t.height = TILE
    const c = t.getContext('2d')!
    // film grain
    const img = c.createImageData(TILE, TILE)
    for (let i = 0; i < img.data.length; i += 4) {
      const v = 4 + Math.random() * 26
      img.data[i] = img.data[i + 1] = img.data[i + 2] = v
      img.data[i + 3] = 255
    }
    c.putImageData(img, 0, 0)
    // 45-degree hairlines at a 6px pitch
    c.strokeStyle = 'rgba(236,236,236,0.075)'
    c.lineWidth = 1
    for (let i = -TILE; i < TILE * 2; i += 6) {
      c.beginPath(); c.moveTo(i, 0); c.lineTo(i + TILE, TILE); c.stroke()
    }
    return t
  }

  // ── the unseen mask, rebuilt only when the field changes ──────────────────────────────────
  function buildMask(c: Coverage) {
    const [gw, gh] = c.grid
    const m = document.createElement('canvas')
    m.width = gw; m.height = gh
    const ctx = m.getContext('2d')!
    const img = ctx.createImageData(gw, gh)
    for (let i = 0; i < gw * gh; i++) {
      const u = c.unseen[i] ?? 0
      img.data[i * 4] = img.data[i * 4 + 1] = img.data[i * 4 + 2] = 0
      // below 0.12 nothing is drawn at all: a calm scene must render as a clean frame
      img.data[i * 4 + 3] = u < 0.12 ? 0 : Math.round(Math.min(1, u) * 200)
    }
    ctx.putImageData(img, 0, 0)
    mask = m
  }

  function frame(now: number) {
    raf = requestAnimationFrame(frame)
    if (now - lastT < TICK_MS) return
    lastT = now
    const el = cv, c = cov
    if (!el || !c || !mask || !pattern) return
    const w = el.clientWidth, h = el.clientHeight
    if (!w || !h) return
    if (el.width !== w || el.height !== h) { el.width = w; el.height = h }
    const ctx = el.getContext('2d')!
    ctx.clearRect(0, 0, w, h)
    scroll = (scroll + 0.6) % TILE
    ctx.save()
    ctx.translate(-scroll, scroll * 0.5)
    ctx.fillStyle = pattern
    ctx.fillRect(0, -TILE, w + TILE * 2, h + TILE * 2)
    ctx.restore()
    // keep only the unseen region
    ctx.globalCompositeOperation = 'destination-in'
    ctx.imageSmoothingEnabled = true
    ctx.drawImage(mask, 0, 0, w, h)
    // punch the occluders back out so the objects doing the hiding stay crisp
    ctx.globalCompositeOperation = 'destination-out'
    for (const s of c.shadows) {
      const o = (s as { occluder?: number[] }).occluder
      if (!o) continue
      ctx.fillRect(o[0] * w, o[1] * h, o[2] * w, o[3] * h)
    }
    ctx.globalCompositeOperation = 'source-over'
  }

  $effect(() => {
    const c = cov
    if (!c) { mask = null; return }
    const key = `${c.grid.join('x')}|${c.ts}`
    if (key !== maskKey) { maskKey = key; buildMask(c) }
  })

  onMount(() => {
    const t = buildTile()
    pattern = cv?.getContext('2d')?.createPattern(t, 'repeat') ?? null
    raf = requestAnimationFrame(frame)
  })
  onDestroy(() => cancelAnimationFrame(raf))

  // ── LOST IN FOG ───────────────────────────────────────────────────────────────────────────
  // Computed client-side so the countdown is responsive; the backend raises the durable alert.
  interface Loss { id: string; spot: number; entered: number; expect: number; x: number; y: number }
  let losses = $state<Loss[]>([])
  // Accumulators are deliberately plain (not $state): the effect below both reads and writes
  // them, and reactive reads would make it retrigger itself forever.
  const last = new Map<string, { x: number; y: number; t: number; vy: number }>()
  const pending = new Map<string, Loss>()

  $effect(() => {
    const c = cov, list = $detections
    if (!c) { losses = []; last.clear(); pending.clear(); return }
    const now = performance.now()
    const live = new Set<string>()
    for (const d of list) {
      if (d.cls !== 'person') continue
      live.add(d.id)
      pending.delete(d.id)                    // visible again: never a loss
      const x = d.bbox[0] + d.bbox[2] / 2, y = Math.min(0.999, d.bbox[1] + d.bbox[3])
      const p = last.get(d.id)
      // normalized vertical speed, smoothed — it sets how long a crossing SHOULD take
      const vy = p ? (Math.abs(y - p.y) / Math.max(16, now - p.t)) * 1000 : 0.05
      last.set(d.id, { x, y, t: now, vy: p ? p.vy * 0.7 + vy * 0.3 : vy })
      // entering a shadow arms a countdown; it only becomes a loss once they stop being visible
      for (const s of c.shadows) {
        const [x0, y0] = s.polygon[0], [x1, y1] = s.polygon[2]
        if (x >= x0 && x <= x1 && y >= y0 && y <= y1) {
          const depth = Math.max(0.02, Math.abs(y1 - y0))
          const cross = (depth / Math.max(0.01, last.get(d.id)!.vy)) * 1000
          pending.set(d.id, { id: d.id, spot: s.id, entered: now, expect: now + cross * 2.5, x, y })
          break
        }
      }
    }
    const out: Loss[] = []
    for (const [id, l] of pending) {
      if (live.has(id)) continue
      if (now - l.entered > 120000) { pending.delete(id); continue }
      out.push(l)
    }
    // Prune the motion history for anyone no longer on screen and not being counted down.
    // Track ids are unique per subject, so without this the map grows for the whole session:
    // every person who ever walked past stays in memory forever.
    if (last.size > 256) {
      for (const [id, p] of last) {
        if (!live.has(id) && !pending.has(id) && now - p.t > 30000) last.delete(id)
      }
    }
    losses = out
  })

  let clock = $state(0)
  onMount(() => {
    const id = setInterval(() => (clock = performance.now()), 250)
    return () => clearInterval(id)
  })

  const secs = (ms: number) => {
    const s = Math.max(0, Math.floor(ms / 1000))
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
  }
  const overdue = (l: Loss) => clock > l.expect

  const bandLabel: Record<DoriTask, string> = {
    identify: 'IDENTIFY', recognise: 'RECOGNISE', observe: 'OBSERVE', detect: 'DETECT',
  }
</script>

{#if cov}
  <div class="fog">
    <canvas bind:this={cv} class="scrim"></canvas>

    <svg class="lines" viewBox="0 0 100 100" preserveAspectRatio="none">
      <!-- shadow boundaries: marching ants, low-opacity cyan (a boundary is a data path) -->
      {#each cov.shadows as s (s.id)}
        <polygon class="shadow" class:persistent={s.persistent}
          points={s.polygon.map((p) => `${p[0] * 100},${p[1] * 100}`).join(' ')} />
      {/each}
      <!-- DORI ladder: the actual ground ranges, projected -->
      {#each cov.bands as b (b.task)}
        {#if b.y > 0 && b.y < 1}
          <line class="band" class:on={b.task === cov.task} class:hov={hoverBand === b.task}
            x1="0" x2="100" y1={b.y * 100} y2={b.y * 100} />
        {/if}
      {/each}
    </svg>

    <!-- band labels sit outside the SVG so they never get stretched by preserveAspectRatio -->
    {#each cov.bands as b (b.task)}
      {#if b.y > 0 && b.y < 1}
        <button class="blabel caps" class:on={b.task === cov.task}
          style={`top:${b.y * 100}%`}
          onmouseenter={() => (hoverBand = b.task)}
          onmouseleave={() => (hoverBand = null)}
          aria-label={`${bandLabel[b.task]} range`}>
          {bandLabel[b.task]} ◂ {b.range_m} M
        </button>
      {/if}
    {/each}

    {#if hoverBand}
      {@const b = cov.bands.find((x) => x.task === hoverBand)}
      {#if b}
        <div class="bcap caps" style={`top:calc(${b.y * 100}% + 14px)`}>
          AT {b.range_m} M A TARGET IS ~{b.px_per_m} PX PER METRE{cov.scale_estimated ? ' · ESTIMATED' : ''}
        </div>
      {/if}
    {/if}

    <!-- coverage readout: the one number this whole overlay exists to produce -->
    <button class="ring" onclick={() => coverageScreen.set(true)} title="Open the coverage report">
      <svg viewBox="0 0 60 60">
        <circle class="rtrack" cx="30" cy="30" r="26" />
        <circle class="rprog" cx="30" cy="30" r="26"
          stroke-dasharray={RC} stroke-dashoffset={RC * (1 - cov.percent / 100)} />
      </svg>
      <span class="rval">{Math.round(cov.percent)}%</span>
      <span class="rlbl caps">COVERAGE</span>
      <span class="rtask caps">{cov.task}</span>
    </button>

    <!-- LOST IN FOG: the only scarlet in this overlay -->
    {#each losses as l (l.id)}
      <div class="loss" class:over={overdue(l)} style={`left:${l.x * 100}%; top:${l.y * 100}%`}>
        <span class="lring"></span>
        <span class="ltxt caps">
          {l.id} UNSEEN {secs(clock - l.entered)}
          {#if !overdue(l)}<span class="lexp">· EXPECTED EXIT {secs(Math.max(0, l.expect - clock))}</span>
          {:else}<span class="lover">· OVERDUE</span>{/if}
        </span>
      </div>
    {/each}
  </div>
{/if}

<style>
  .fog { position: absolute; inset: 0; z-index: 6; pointer-events: none; }
  .scrim { position: absolute; inset: 0; width: 100%; height: 100%; }
  .lines { position: absolute; inset: 0; width: 100%; height: 100%; }

  .shadow { fill: none; stroke: var(--cyan); stroke-opacity: 0.35; stroke-width: 1;
    vector-effect: non-scaling-stroke; stroke-dasharray: 4 4; animation: ants 3s linear infinite; }
  .shadow.persistent { stroke-opacity: 0.55; stroke-dasharray: 7 3; }
  @keyframes ants { to { stroke-dashoffset: -16; } }

  .band { stroke: var(--ink-dim); stroke-opacity: 0.28; stroke-width: 1; vector-effect: non-scaling-stroke; }
  .band.on { stroke: var(--cyan); stroke-opacity: 0.5; }
  .band.hov { stroke-opacity: 0.9; }

  .blabel { position: absolute; right: 66px; transform: translateY(-50%); pointer-events: auto;
    background: rgba(4,7,10,0.62); border: none; padding: 2px 7px; cursor: crosshair;
    font-size: 8px; letter-spacing: 0.14em; color: var(--ink-ghost); white-space: nowrap; }
  .blabel:hover { color: var(--ink); }
  .blabel.on { color: var(--cyan); }

  .bcap { position: absolute; right: 66px; transform: translateY(0); font-size: 8px;
    letter-spacing: 0.12em; color: var(--ink-dim); background: rgba(4,7,10,0.78);
    padding: 3px 8px; white-space: nowrap; animation: fade 160ms var(--ease); }
  @keyframes fade { from { opacity: 0; } }

  /* coverage ring — the standard gauge idiom from the suggestions cockpit */
  .ring { position: absolute; top: 74px; right: 62px; width: 52px; pointer-events: auto;
    background: none; border: none; padding: 0; cursor: crosshair; display: block; }
  .ring svg { width: 52px; height: 52px; transform: rotate(-90deg); display: block; }
  .rtrack { fill: none; stroke: var(--hairline); stroke-width: 4; }
  .rprog { fill: none; stroke: var(--cyan); stroke-width: 4; stroke-linecap: round;
    transition: stroke-dashoffset 700ms cubic-bezier(0.16, 1, 0.3, 1);
    filter: drop-shadow(0 0 4px color-mix(in srgb, var(--cyan) 60%, transparent)); }
  .rval { position: absolute; left: 0; top: 0; width: 52px; height: 52px; display: flex;
    align-items: center; justify-content: center; font-size: 11px; color: var(--cyan); }
  .rlbl { position: absolute; left: 50%; top: 54px; transform: translateX(-50%);
    font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.16em; white-space: nowrap; }
  .rtask { position: absolute; left: 50%; top: 66px; transform: translateX(-50%);
    font-size: 8px; color: var(--ink-dim); letter-spacing: 0.12em; white-space: nowrap; }
  .ring:hover .rlbl, .ring:hover .rtask { color: var(--cyan); }

  .loss { position: absolute; transform: translate(-50%, -100%); pointer-events: none; }
  .lring { position: absolute; left: 50%; top: 0; width: 26px; height: 26px; margin: -13px 0 0 -13px;
    border: 1px solid var(--cyan); border-radius: 50%; opacity: 0.7;
    animation: lpulse 1.6s ease-out infinite; }
  .loss.over .lring { border-color: var(--scarlet); box-shadow: 0 0 12px var(--scarlet-glow); }
  @keyframes lpulse { 0% { transform: scale(0.5); opacity: 0.9; } 100% { transform: scale(1.6); opacity: 0; } }
  .ltxt { position: absolute; left: 16px; top: -8px; font-size: 9px; letter-spacing: 0.1em;
    color: var(--cyan); text-shadow: 0 0 5px #000; white-space: nowrap; }
  .loss.over .ltxt { color: var(--scarlet); }
  .lexp { color: var(--ink-dim); }
  .lover { color: var(--scarlet); }
</style>
