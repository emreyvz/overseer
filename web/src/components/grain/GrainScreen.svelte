<script lang="ts">
  // GRAIN — the model itself, browsable.
  //
  // Left: which normality you are looking at (hour, class, and a compare mode). Centre: the
  // learned field over the live frame, with a per-cell inspector. Right: today's tracks ranked
  // by how ordinary they were.
  //
  // The footer statement is permanent and deliberate. A behavioural model that cannot say what
  // it does NOT use is not deployable in half the world.
  import { onDestroy, onMount } from 'svelte'
  import { activeCam, cameras, grainStatus, grainTracks } from '../../lib/stores'
  import { api } from '../../lib/api'
  import {
    FACTOR_LABEL, buildStreaks, cellAt, cellStat, grainBucket, grainClass, refreshGrain,
    setGrainBucket, setGrainClass, type Streak,
  } from '../../lib/grain'
  import { sfx } from '../../lib/audio'
  import { SIM } from '../../lib/sim'
  import type { GrainCellStat, GrainStatus, GrainTrackRow } from '../../lib/types'
  import LiveThumb from '../LiveThumb.svelte'

  let { onclose }: { onclose: () => void } = $props()

  let cv = $state<HTMLCanvasElement>()
  let stage = $state<HTMLElement>()
  let hoverCell = $state<number | null>(null)
  let pinCell = $state<number | null>(null)
  let selTrack = $state<number | null>(null)
  let muteMode = $state(false)
  let muted = $state<number[]>([])
  let compare = $state(false)
  let compareBucket = $state<number>(0)
  let compareCells = $state<GrainCellStat[] | null>(null)
  let replay = $state(0)

  let streaks: Streak[] = []
  let raf = 0
  let lastT = 0
  let t0 = 0
  let key = ''

  const st = $derived($grainStatus)
  const cam = $derived($cameras.find((c) => c.id === $activeCam))
  const inspect = $derived(st ? cellStat(st, pinCell ?? hoverCell ?? -1) : null)
  const ledger = $derived([...$grainTracks].sort((a, b) => a.percentile - b.percentile))
  const unjudged = $derived(ledger.filter((t) => t.state === 'unjudged' || !t.verdict))
  const marked = $derived(ledger.filter((t) => t.verdict))
  const sel = $derived(ledger.find((t) => t.id === selTrack) ?? null)

  $effect(() => {
    const s = st
    if (!s) { streaks = []; key = ''; return }
    const k = `${s.cam}|${s.bucket}|${s.cells.length}|${compare}|${compareBucket}`
    if (k !== key) { key = k; streaks = buildStreaks(s, 'dense') }
  })

  function frame(now: number) {
    raf = requestAnimationFrame(frame)
    if (now - lastT < 50) return
    lastT = now
    const el = cv
    if (!el) return
    const w = el.clientWidth, h = el.clientHeight
    if (!w || !h) return
    if (el.width !== w || el.height !== h) { el.width = w; el.height = h }
    const ctx = el.getContext('2d')!
    ctx.clearRect(0, 0, w, h)
    if (!t0) t0 = now
    const phase = (((now - t0) / 1000) * 0.16) % 1
    ctx.lineWidth = 1
    ctx.lineCap = 'round'
    for (let band = 0; band < 6; band++) {
      const lo = band / 6, hi = (band + 1) / 6
      ctx.beginPath()
      let any = false
      for (const s of streaks) {
        if (s.alpha < lo * 0.34 || s.alpha >= hi * 0.34) continue
        any = true
        const p = s.mature ? (s.phase + phase) % 1 : 0.5
        const travel = s.mature ? (p - 0.5) * s.len * 3 : 0
        const cx = (s.x + Math.cos(s.a) * travel) * w
        const cy = (s.y + Math.sin(s.a) * travel) * h
        const dx = Math.cos(s.a) * s.len * w * 0.5
        const dy = Math.sin(s.a) * s.len * h * 0.5
        ctx.moveTo(cx - dx, cy - dy); ctx.lineTo(cx + dx, cy + dy)
      }
      if (!any) continue
      ctx.strokeStyle = `rgba(124,130,136,${((lo + hi) / 2) * 0.55})`
      ctx.stroke()
    }
    // the difference field: what exists in the compared condition but not this one
    if (compare && compareCells && st) {
      const [gw, gh] = st.grid
      const here = new Map(st.cells.map((c) => [c.cell, c]))
      ctx.lineWidth = 1.4
      ctx.beginPath()
      for (const c of compareCells) {
        if (!c.mature) continue
        const mine = here.get(c.cell)
        if (mine && mine.mature && Math.abs(mine.modal_heading - c.modal_heading) < 0.6) continue
        const cx = ((c.cell % gw) + 0.5) / gw * w, cy = (Math.floor(c.cell / gw) + 0.5) / gh * h
        const dx = Math.cos(c.modal_heading) * 8, dy = Math.sin(c.modal_heading) * 8
        ctx.moveTo(cx - dx, cy - dy); ctx.lineTo(cx + dx, cy + dy)
      }
      ctx.strokeStyle = 'rgba(225,6,0,0.55)'
      ctx.stroke()
    }
    // the replaying track
    if (sel && sel.path.length > 1) {
      const prog = (replay % 1000) / 1000
      const n = Math.max(2, Math.floor(sel.path.length * prog))
      ctx.beginPath()
      sel.path.slice(0, n).forEach((p, i) => {
        const x = p[0] * w, y = p[1] * h
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
      })
      ctx.strokeStyle = sel.state === 'unusual' ? 'rgba(225,6,0,0.9)' : 'rgba(56,208,227,0.8)'
      ctx.lineWidth = 2
      ctx.stroke()
      const head = sel.path[n - 1]
      ctx.beginPath()
      ctx.arc(head[0] * w, head[1] * h, 4, 0, Math.PI * 2)
      ctx.fillStyle = sel.state === 'unusual' ? '#e10600' : '#38d0e3'
      ctx.fill()
    }
  }

  function pointerCell(e: PointerEvent): number | null {
    if (!st || !stage) return null
    const r = stage.getBoundingClientRect()
    return cellAt(st, (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height)
  }
  function onMove(e: PointerEvent) { hoverCell = pointerCell(e) }
  async function onClick(e: PointerEvent) {
    const c = pointerCell(e)
    if (c === null) return
    if (muteMode) {
      muted = muted.includes(c) ? muted.filter((x) => x !== c) : [...muted, c]
      sfx('click', { volume: 0.2 })
      if (!SIM && $activeCam) await api.grainMute($activeCam, [c]).catch(() => undefined)
      return
    }
    pinCell = pinCell === c ? null : c
    sfx('click', { volume: 0.15 })
  }

  async function toggleCompare() {
    compare = !compare
    sfx('click', { volume: 0.2 })
    if (!compare) { compareCells = null; return }
    if (SIM || !$activeCam) { compareCells = st?.cells.map((c) => ({ ...c, modal_heading: c.modal_heading + 0.9 })) ?? null; return }
    const r = await api.grain($activeCam, compareBucket, grainClass).catch(() => ({ status: null }))
    compareCells = (r.status as GrainStatus | null)?.cells ?? null
  }

  async function verdict(t: GrainTrackRow, v: 'ordinary' | 'noteworthy') {
    sfx(v === 'noteworthy' ? 'sonar' : 'click')
    grainTracks.update((l) => l.map((x) => (x.id === t.id ? { ...x, verdict: v } : x)))
    if (!SIM) await api.grainVerdict(t.id, v).catch(() => undefined)
  }

  function step(dir: number) {
    if (!ledger.length) return
    const i = Math.max(0, ledger.findIndex((t) => t.id === selTrack))
    selTrack = ledger[(i + dir + ledger.length) % ledger.length].id
    replay = 0
    sfx('click', { volume: 0.15 })
  }

  const hhmm = (ts: number) => new Date(ts).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  const rose = (c: GrainCellStat) => c.heading.map((v, i) => {
    const a = (i + 0.5) * (Math.PI * 2 / 16)
    const r = 6 + v * 46
    return `${20 + Math.cos(a) * r},${20 + Math.sin(a) * r}`
  }).join(' ')

  function onkey(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.stopPropagation(); onclose(); return }
    if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); step(1) }
    else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); step(-1) }
    else if (e.key === 'c' || e.key === 'C') { e.preventDefault(); toggleCompare() }
    else if (e.key === 'm' || e.key === 'M') { e.preventDefault(); muteMode = !muteMode }
    else if (e.key === 'Enter' && sel) { e.preventDefault(); verdict(sel, 'ordinary') }
    else if ((e.key === 'n' || e.key === 'N') && sel) { e.preventDefault(); verdict(sel, 'noteworthy') }
    else if (/^[1-6]$/.test(e.key)) { e.preventDefault(); setGrainBucket(Number(e.key) - 1) }
  }

  onMount(() => {
    sfx('sonar'); refreshGrain(true)
    raf = requestAnimationFrame(frame)
    const id = setInterval(() => (replay += 40), 40)
    window.addEventListener('keydown', onkey, true)
    return () => clearInterval(id)
  })
  onDestroy(() => { cancelAnimationFrame(raf); window.removeEventListener('keydown', onkey, true) })
</script>

<div class="gs" role="dialog" aria-label="Grain">
  <header class="top caps">
    <span class="eyebrow">◈ GRAIN</span>
    <span class="cnt">◉ {cam?.name ?? 'CAM —'}</span>
    {#if st}<span class="cnt">{st.tracks.toLocaleString()} TRACKS · {st.days} DAY{st.days === 1 ? '' : 'S'}</span>{/if}
    <span class="spacer"></span>
    {#if st}
      <span class="chip caps" class:mature={st.mature}>{st.mature ? 'MATURE' : `LEARNING ${Math.round(st.maturity * 100)}%`}</span>
    {/if}
    <button class="ref caps" class:on={compare} onclick={toggleCompare}>⇄ COMPARE<span class="k">C</span></button>
    <button class="ref caps" class:on={muteMode} onclick={() => (muteMode = !muteMode)}>◱ MUTE<span class="k">M</span></button>
    <button class="ref caps" onclick={() => refreshGrain(true)}>↻ RESCAN</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  {#if !st}
    <div class="empty caps"><span class="pulse">READING THE GRAIN_</span></div>
  {:else}
    <div class="body">
      <!-- LEFT: which normality are we looking at -->
      <aside class="cond">
        <div class="ck caps">HOUR OF DAY</div>
        <div class="dial">
          <svg viewBox="0 0 120 120">
            {#each st.buckets as b, i}
              {@const a0 = (i / st.buckets.length) * Math.PI * 2 - Math.PI / 2}
              {@const a1 = ((i + 1) / st.buckets.length) * Math.PI * 2 - Math.PI / 2}
              <path class="seg" class:on={(grainBucket ?? st.bucket) === i}
                d={`M60,60 L${60 + 48 * Math.cos(a0)},${60 + 48 * Math.sin(a0)} A48,48 0 0,1 ${60 + 48 * Math.cos(a1)},${60 + 48 * Math.sin(a1)} Z`}
                role="button" tabindex="0"
                onclick={() => setGrainBucket(i)}
                onkeydown={(e) => { if (e.key === 'Enter') setGrainBucket(i) }} />
            {/each}
            <circle class="hub" cx="60" cy="60" r="20" />
            <text class="hubt" x="60" y="63">{st.buckets[grainBucket ?? st.bucket]}</text>
          </svg>
        </div>
        <div class="hint caps">1-6 JUMP TO A BUCKET</div>

        <div class="ck caps">SUBJECT CLASS</div>
        <div class="chips">
          {#each ['person', 'vehicle'] as c}
            <button class="tog caps" class:on={grainClass === c}
              onclick={() => setGrainClass(c as 'person' | 'vehicle')}>{c}</button>
          {/each}
        </div>

        {#if compare}
          <div class="ck caps">COMPARE AGAINST</div>
          <div class="chips wrap">
            {#each st.buckets as b, i}
              <button class="tog caps sm" class:on={compareBucket === i}
                onclick={() => { compareBucket = i; toggleCompare(); toggleCompare() }}>{b}</button>
            {/each}
          </div>
          <div class="hint caps">SCARLET STREAKS EXIST THERE BUT NOT HERE</div>
        {/if}

        {#if muteMode}
          <div class="ck caps">MUTE</div>
          <div class="hint caps">
            CLICK CELLS TO EXCLUDE THEM FROM SCORING · {muted.length} MUTED.
            USE THIS FOR THE DOORWAY WHERE STAFF ALWAYS WAIT.
          </div>
        {/if}
      </aside>

      <!-- CENTRE: the field -->
      <main class="stage">
        <div class="framewrap" bind:this={stage}
          role="presentation"
          onpointermove={onMove}
          onpointerleave={() => (hoverCell = null)}
          onpointerdown={onClick}>
          {#if $activeCam}<LiveThumb id={$activeCam} fps={4} />{:else}<div class="nolive caps">NO FEED</div>{/if}
          <canvas bind:this={cv} class="field"></canvas>
          {#if muteMode}
            <svg class="mutes" viewBox="0 0 100 100" preserveAspectRatio="none">
              {#each muted as c}
                {@const gw = st.grid[0]}
                {@const gh = st.grid[1]}
                <rect class="mrect" x={(c % gw) / gw * 100} y={Math.floor(c / gw) / gh * 100}
                  width={100 / gw} height={100 / gh} />
              {/each}
            </svg>
          {/if}
          {#if hoverCell !== null || pinCell !== null}
            {@const c = pinCell ?? hoverCell}
            {@const gw = st.grid[0]}
            {@const gh = st.grid[1]}
            <span class="cellbox" class:pinned={pinCell !== null}
              style={`left:${(c! % gw) / gw * 100}%; top:${Math.floor(c! / gw) / gh * 100}%;
                      width:${100 / gw}%; height:${100 / gh}%`}></span>
          {/if}
        </div>

        <!-- cell inspector -->
        <div class="inspect">
          {#if inspect}
            <div class="ins">
              <div class="ik caps">HEADING</div>
              <svg class="rose" viewBox="0 0 40 40">
                <circle class="rbg" cx="20" cy="20" r="17" />
                <polygon class="rpoly" points={rose(inspect)} />
              </svg>
            </div>
            <div class="ins">
              <div class="ik caps">SPEED</div>
              <div class="hist">
                {#each inspect.speed as v}<span class="hb" style={`height:${Math.max(2, v * 130)}%`}></span>{/each}
              </div>
            </div>
            <div class="ins stats">
              <div class="sk caps">OBSERVATIONS<span class="sv">{inspect.n.toLocaleString()}</span></div>
              <div class="sk caps">DECIDEDNESS<span class="sv">{Math.round(inspect.concentration * 100)}%</span></div>
              <div class="sk caps">STATE
                <span class="sv" class:un={!inspect.mature}>{inspect.mature ? 'LEARNED' : 'UNLEARNED'}</span>
              </div>
              {#if !inspect.mature}
                <div class="unote caps">THE MODEL HAS NOT SEEN THIS SPOT ENOUGH TO JUDGE ANYONE IN IT.</div>
              {/if}
            </div>
          {:else}
            <div class="ihint caps">HOVER THE FRAME TO INSPECT A CELL · CLICK TO PIN</div>
          {/if}
        </div>
      </main>

      <!-- RIGHT: the ledger -->
      <aside class="ledger">
        <div class="qgrp caps"><span class="qd u"></span>AWAITING VERDICT<span class="qgn">{unjudged.length}</span></div>
        {#each unjudged as t (t.id)}
          <button class="qrow" class:on={selTrack === t.id} onclick={() => { selTrack = t.id; replay = 0 }}>
            <svg class="pthumb" viewBox="0 0 40 26" preserveAspectRatio="none">
              <polyline points={t.path.map((p) => `${p[0] * 40},${p[1] * 26}`).join(' ')} />
            </svg>
            <span class="qmid">
              <span class="qttl">{t.det_id}</span>
              <span class="qsub caps">{hhmm(t.start_ts)} · {t.state}</span>
            </span>
            <span class="qp caps s-{t.state}">{t.percentile.toFixed(1)}</span>
          </button>
        {/each}
        {#if marked.length}
          <div class="qgrp caps"><span class="qd m"></span>MARKED<span class="qgn">{marked.length}</span></div>
          {#each marked as t (t.id)}
            <button class="qrow off" class:on={selTrack === t.id} onclick={() => { selTrack = t.id; replay = 0 }}>
              <svg class="pthumb" viewBox="0 0 40 26" preserveAspectRatio="none">
                <polyline points={t.path.map((p) => `${p[0] * 40},${p[1] * 26}`).join(' ')} />
              </svg>
              <span class="qmid">
                <span class="qttl">{t.det_id}</span>
                <span class="qsub caps">{t.verdict}</span>
              </span>
            </button>
          {/each}
        {/if}
        {#if !ledger.length}
          <div class="lempty caps">
            <div class="okring">✓</div>
            EVERY TRACK TODAY WAS ORDINARY
          </div>
        {/if}

        {#if sel}
          <div class="seldetail">
            <div class="sk caps">{sel.det_id} · {sel.percentile.toFixed(1)}TH PERCENTILE</div>
            {#if sel.why}<p class="swhy">{sel.why}</p>{/if}
            <div class="facs">
              {#each Object.entries(sel.factors) as [k, v]}
                <div class="fac">
                  <span class="fk caps">{FACTOR_LABEL[k] ?? k}</span>
                  <span class="fbar"><span class="fmid"></span>
                    <span class="ffill" class:lead={v <= 10} style={`left:${Math.min(v, 50)}%; width:${Math.max(1.5, Math.abs(v - 50))}%`}></span>
                  </span>
                </div>
              {/each}
            </div>
            <div class="sact">
              <button class="ok caps" onclick={() => verdict(sel, 'ordinary')}>✓ ORDINARY<span class="k">⏎</span></button>
              <button class="flag caps" onclick={() => verdict(sel, 'noteworthy')}>⚑ NOTEWORTHY<span class="k">N</span></button>
            </div>
          </div>
        {/if}
      </aside>
    </div>

    <footer class="foot caps">
      <span class="fl">
        LEARNED FROM {st.tracks.toLocaleString()} TRACKS OVER {st.days} DAY{st.days === 1 ? '' : 'S'}
        · {st.mature ? 'MATURE' : 'STILL LEARNING'}
      </span>
      <span class="fr">MOVEMENT ONLY · NO APPEARANCE, IDENTITY OR DEMOGRAPHIC FEATURE IS USED</span>
    </footer>
  {/if}
</div>

<style>
  .gs { position: fixed; inset: 0; z-index: var(--z-boot);
    background: radial-gradient(120% 80% at 50% 0%, #0a1016 0%, #05070a 72%);
    color: var(--ink); display: flex; flex-direction: column; overflow: hidden;
    animation: gin 300ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes gin { from { opacity: 0; } }
  .top { display: flex; align-items: center; gap: 12px; padding: 11px 22px;
    border-bottom: 1px solid var(--hairline); font-size: var(--fs-label);
    letter-spacing: var(--tracking); background: #04070a; z-index: 2; }
  .eyebrow { color: var(--scarlet); } .cnt { color: var(--ink-dim); font-size: 9px; } .spacer { flex: 1; }
  .chip { padding: 4px 9px; border: 1px solid var(--hairline); color: var(--amber); font-size: 8px; letter-spacing: 0.12em; }
  .chip.mature { color: var(--jade); }
  .ref, .x { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px;
    border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: crosshair;
    font-size: 9px; letter-spacing: var(--tracking); }
  .ref:hover { border-color: var(--cyan); color: var(--cyan); }
  .ref.on { border-color: var(--cyan); color: var(--cyan); background: rgba(56,208,227,0.1); }
  .ref .k, .ok .k, .flag .k { border: 1px solid var(--ink-ghost); padding: 0 4px; font-size: 8px; color: var(--ink-ghost); }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }

  .empty { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--ink-dim); letter-spacing: 0.16em; }
  .pulse { animation: pulse 1.2s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.4; } }

  .body { flex: 1; min-height: 0; display: grid; grid-template-columns: 260px 1fr 320px; }
  .cond, .ledger { overflow-y: auto; padding: 14px 12px 30px; background: rgba(4,7,10,0.4); }
  .cond { border-right: 1px solid var(--hairline); }
  .ledger { border-left: 1px solid var(--hairline); }
  .ck { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.18em; margin: 14px 2px 8px; }
  .ck:first-child { margin-top: 0; }
  .hint { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.12em; line-height: 1.6; margin-top: 6px; }

  .dial svg { width: 100%; max-width: 190px; display: block; margin: 0 auto; }
  .seg { fill: rgba(124,130,136,0.09); stroke: #04070a; stroke-width: 1.5; cursor: crosshair; transition: fill 160ms; }
  .seg:hover { fill: rgba(56,208,227,0.18); }
  .seg.on { fill: rgba(56,208,227,0.32); }
  .hub { fill: #04070a; stroke: var(--hairline); }
  .hubt { fill: var(--cyan); font-size: 7px; text-anchor: middle; font-family: var(--font-mono); letter-spacing: 0.1em; }

  .chips { display: flex; gap: 6px; } .chips.wrap { flex-wrap: wrap; }
  .tog { padding: 5px 10px; border: 1px solid var(--hairline); background: none; color: var(--ink-dim);
    cursor: crosshair; font-size: 9px; letter-spacing: 0.12em; }
  .tog.sm { font-size: 7px; padding: 4px 6px; }
  .tog:hover { color: var(--ink); } .tog.on { border-color: var(--cyan); color: var(--cyan); }

  .stage { min-width: 0; display: flex; flex-direction: column; }
  .framewrap { position: relative; flex: 1; min-height: 0; background: #05070a; overflow: hidden; cursor: crosshair; }
  .nolive { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: 10px; letter-spacing: 0.14em; }
  .field { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  .mutes { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  .mrect { fill: rgba(124,130,136,0.35); stroke: var(--ink-dim); stroke-width: 0.3; }
  .cellbox { position: absolute; border: 1px solid var(--cyan); pointer-events: none; opacity: 0.5; }
  .cellbox.pinned { opacity: 1; box-shadow: 0 0 10px rgba(56,208,227,0.4); }

  .inspect { display: flex; gap: 18px; align-items: flex-start; padding: 12px 18px;
    border-top: 1px solid var(--hairline); min-height: 128px; background: rgba(4,7,10,0.5); }
  .ins { display: flex; flex-direction: column; gap: 6px; }
  .ik { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.16em; }
  .rose { width: 92px; height: 92px; }
  .rbg { fill: none; stroke: var(--hairline); }
  .rpoly { fill: rgba(56,208,227,0.18); stroke: var(--cyan); stroke-width: 1; }
  .hist { display: flex; align-items: flex-end; gap: 2px; height: 80px; }
  .hb { width: 6px; background: var(--ink-dim); }
  .ins.stats { gap: 5px; }
  .sk { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.14em; display: flex; gap: 8px; }
  .sv { color: var(--ink); } .sv.un { color: var(--amber); }
  .unote { font-size: 8px; color: var(--amber); letter-spacing: 0.1em; line-height: 1.6; max-width: 300px; margin-top: 4px; }
  .ihint { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.14em; align-self: center; }

  .qgrp { display: flex; align-items: center; gap: 7px; font-size: 8px; color: var(--ink-dim); letter-spacing: 0.16em; margin: 12px 4px 7px; }
  .qgrp:first-child { margin-top: 0; }
  .qd { width: 6px; height: 6px; border-radius: 50%; }
  .qd.u { background: var(--scarlet); box-shadow: 0 0 6px var(--scarlet); }
  .qd.m { background: var(--ink-ghost); }
  .qgn { margin-left: auto; color: var(--ink-ghost); }
  .qrow { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; padding: 7px 8px;
    margin-bottom: 3px; background: none; border: 1px solid transparent; border-left: 2px solid transparent;
    cursor: crosshair; }
  .qrow:hover { background: rgba(56,208,227,0.05); }
  .qrow.on { background: rgba(56,208,227,0.09); border-color: var(--hairline); border-left-color: var(--cyan); }
  .qrow.off { opacity: 0.45; }
  .pthumb { width: 40px; height: 26px; flex: 0 0 auto; border: 1px solid var(--hairline); }
  .pthumb polyline { fill: none; stroke: var(--ink-dim); stroke-width: 1.4; vector-effect: non-scaling-stroke; }
  .qrow.on .pthumb polyline { stroke: var(--cyan); }
  .qmid { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
  .qttl { font-size: 10px; color: var(--ink-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .qrow.on .qttl { color: var(--ink); }
  .qsub { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.1em; }
  .qp { font-size: 9px; letter-spacing: 0.06em; }
  .s-unusual { color: var(--scarlet); } .s-ordinary { color: var(--ink-dim); } .s-unjudged { color: var(--ink-ghost); }
  .lempty { display: flex; flex-direction: column; gap: 12px; align-items: center; padding: 40px 10px;
    color: var(--ink-dim); font-size: 9px; letter-spacing: 0.14em; text-align: center; }
  .okring { width: 44px; height: 44px; border: 1px solid color-mix(in srgb, var(--jade) 50%, transparent);
    border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--jade); font-size: 18px; }

  .seldetail { margin-top: 14px; padding: 11px 10px; border: 1px solid var(--hairline); background: rgba(4,7,10,0.5);
    display: flex; flex-direction: column; gap: 9px; }
  .swhy { font-size: 10px; color: var(--ink-dim); line-height: 1.6; margin: 0; }
  .facs { display: flex; flex-direction: column; gap: 4px; }
  .fac { display: grid; grid-template-columns: 52px 1fr; align-items: center; gap: 7px; }
  .fk { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.1em; }
  .fbar { position: relative; height: 4px; background: var(--hairline); }
  .fmid { position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--ink-ghost); opacity: 0.6; }
  .ffill { position: absolute; top: 0; bottom: 0; background: var(--ink-dim); }
  .ffill.lead { background: var(--scarlet); box-shadow: 0 0 7px var(--scarlet-glow); }
  .sact { display: flex; gap: 7px; }
  .ok, .flag { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 6px;
    padding: 8px 0; border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim);
    cursor: crosshair; font-size: 9px; letter-spacing: 0.12em; }
  .ok:hover { border-color: var(--jade); color: var(--jade); }
  .flag:hover { border-color: var(--scarlet); color: var(--scarlet); }

  .foot { display: flex; align-items: center; justify-content: space-between; gap: 20px;
    padding: 10px 22px; border-top: 1px solid var(--hairline); background: #04070a;
    font-size: 8px; letter-spacing: 0.14em; }
  .fl { color: var(--ink-dim); }
  .fr { color: var(--ink-ghost); }

  @media (max-width: 1100px) {
    .body { grid-template-columns: 200px 1fr 260px; }
  }
</style>
