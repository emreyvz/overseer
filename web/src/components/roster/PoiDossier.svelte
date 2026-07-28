<script lang="ts">
  // Full-screen, interactive "subject of interest" screen — a spatial journey map (in the
  // language of the topology screen) that traces where the subject was seen across cameras,
  // in order, with live feeds at every stop. Not a popup: it takes over the screen, pans and
  // zooms, and lets the operator jump straight to any camera to keep tracking live.
  import { onDestroy, onMount } from 'svelte'
  import { cameras, activeCam, mode, triggerGlitch, flashBanner } from '../../lib/stores'
  import { annotations, annotate } from '../../lib/annotations'
  import { trUpper } from '../../lib/lexicon'
  import { sfx } from '../../lib/audio'
  import { sendCommand } from '../../lib/ws'
  import { SIM } from '../../lib/sim'
  import LiveThumb from '../LiveThumb.svelte'
  import type { RosterEntry } from '../../lib/types'

  let { entry, now, onclose }: { entry: RosterEntry; now: number; onclose: () => void } = $props()

  const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
  const LIVE_MS = 8000
  const VW = 1000, VH = 500
  const project = (lat: number, lng: number): [number, number] => [((lng + 180) / 360) * VW, ((90 - lat) / 180) * VH]

  let closing = $state(false)
  let cutout = $state(false)
  let focusIdx = $state(-1)          // which stop is focused (-1 = latest)

  let a = $derived($annotations[entry.id] ?? {})
  let live = $derived(now - entry.last_ts < LIVE_MS)
  let title = $derived(entry.cls === 'vehicle' ? 'VEHICLE OF INTEREST' : 'PERSON OF INTEREST')
  const camByName = (name?: string | null) => $cameras.find((c) => c.name === name)
  const photo = $derived(entry.snapshot ? `${API}${entry.snapshot}?t=${entry.last_ts}` : '')
  const heroSrc = $derived(cutout ? `${API}/api/roster/${entry.id}/cutout?t=${entry.last_ts}` : photo)

  // — the journey: one node per camera the subject visited, in chronological order —
  let trail = $derived(entry.trail ?? [])
  // geo layout when every stop has coordinates; otherwise a laid-out journey graph
  let geo = $derived(trail.length >= 2 && trail.every((t) => !!camByName(t.cam)?.coords))
  let nodes = $derived(trail.map((t, i) => {
    const cam = camByName(t.cam)
    let x: number, y: number
    if (geo && cam?.coords) {
      ;[x, y] = project(cam.coords[0], cam.coords[1])
    } else {
      const n = trail.length
      x = n <= 1 ? VW * 0.5 : VW * (0.14 + 0.72 * (i / (n - 1)))
      y = VH * 0.5 + (i % 2 === 0 ? -1 : 1) * VH * 0.12
    }
    return { ...t, id: cam?.id, name: t.cam, x, y, cur: t.cam === entry.cam }
  }))
  let pathD = $derived(nodes.map((nd, i) => `${i ? 'L' : 'M'}${nd.x.toFixed(1)} ${nd.y.toFixed(1)}`).join(' '))
  let focusNode = $derived(nodes[focusIdx] ?? nodes[nodes.length - 1] ?? null)

  const clock24 = (ms: number) => { const d = new Date(ms); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':') }
  function ago(ms: number): string {
    const s = Math.max(0, Math.round((now - ms) / 1000))
    if (s < 5) return 'just now'
    if (s < 60) return `${s}s ago`
    const m = Math.round(s / 60)
    return m < 60 ? `${m}m ago` : `${Math.round(m / 60)}h ago`
  }

  // — pan / zoom (zoom-to-cursor), mirroring the topology map —
  let tx = $state(0), ty = $state(0), k = $state(1)
  let svg = $state<SVGSVGElement>()
  let grp = $state<SVGGElement>()
  let drag: { x: number; y: number; tx: number; ty: number } | null = null
  let fitted = $state(false)

  $effect(() => {
    if (fitted || nodes.length === 0) return
    let a0 = Infinity, b0 = -Infinity, c0 = Infinity, d0 = -Infinity
    for (const nd of nodes) { a0 = Math.min(a0, nd.x); b0 = Math.max(b0, nd.x); c0 = Math.min(c0, nd.y); d0 = Math.max(d0, nd.y) }
    const w = Math.max(b0 - a0, 40), h = Math.max(d0 - c0, 40), pad = 2.2
    k = Math.min(6, Math.max(1, Math.min(VW / (w * pad), VH / (h * pad))))
    tx = VW / 2 - k * ((a0 + b0) / 2)
    ty = VH / 2 - k * ((c0 + d0) / 2)
    fitted = true
  })

  function localAt(cx: number, cy: number): DOMPoint | null {
    if (!svg || !grp) return null
    const pt = svg.createSVGPoint(); pt.x = cx; pt.y = cy
    const ctm = grp.getScreenCTM()
    return ctm ? pt.matrixTransform(ctm.inverse()) : null
  }
  function onWheel(e: WheelEvent) {
    e.preventDefault()
    const nk = Math.min(16, Math.max(0.8, k * (e.deltaY < 0 ? 1.15 : 0.87)))
    const loc = localAt(e.clientX, e.clientY)
    if (loc) { tx += (k - nk) * loc.x; ty += (k - nk) * loc.y }
    k = nk
  }
  function onDown(e: PointerEvent) { drag = { x: e.clientX, y: e.clientY, tx, ty }; (e.currentTarget as Element).setPointerCapture(e.pointerId) }
  function onMove(e: PointerEvent) {
    if (!drag || !svg) return
    const s = VW / svg.clientWidth
    tx = drag.tx + (e.clientX - drag.x) * s
    ty = drag.ty + (e.clientY - drag.y) * s
  }
  function onUp() { drag = null }

  function focus(i: number) {
    sfx('click', { volume: 0.3 }); focusIdx = i
    const nd = nodes[i]
    if (!nd) return
    const nk = Math.max(k, 2.4)
    k = nk; tx = VW / 2 - nk * nd.x; ty = VH / 2 - nk * nd.y
  }
  // jump to a camera's live POV to keep tracking there
  function observe(name?: string | null) {
    const cam = camByName(name)
    if (!cam) return
    sfx('glitch'); triggerGlitch(220)
    activeCam.set(cam.id)
    if (!SIM) sendCommand(`connect:${cam.name}`)
    flashBanner(`OBSERVING ${cam.name}`, false, 1200)
    mode.set('pov')
  }

  function close() {
    if (closing) return
    sfx('click'); closing = true
    setTimeout(onclose, 340)
  }
  function onkey(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); close() } }
  onMount(() => { sfx('sonar'); window.addEventListener('keydown', onkey, true) })
  onDestroy(() => window.removeEventListener('keydown', onkey, true))
  const inv = $derived(1 / k)
</script>

<div class="poi" class:closing role="dialog" aria-modal="true" aria-label={title}>
  <header class="top caps">
    <span class="eyebrow">◈ {title}</span>
    <span class="idc">{a.alias || entry.id}</span>
    <span class="live" class:on={live}>{live ? '● LIVE' : '○ IDLE'}</span>
    <span class="spacer"></span>
    <span class="hint">DRAG · PAN&nbsp;&nbsp;WHEEL · ZOOM&nbsp;&nbsp;ESC · BACK</span>
    <button class="x caps" onclick={close}>✕ BACK</button>
  </header>

  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <svg bind:this={svg} class="net" viewBox={`0 0 ${VW} ${VH}`} onwheel={onWheel}
    onpointerdown={onDown} onpointermove={onMove} onpointerup={onUp}>
    <defs>
      <radialGradient id="poiglow" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stop-color="rgba(56,208,227,0.18)" /><stop offset="100%" stop-color="transparent" />
      </radialGradient>
    </defs>
    <g bind:this={grp} transform={`translate(${tx} ${ty}) scale(${k})`}>
      {#each Array.from({ length: 13 }) as _, i}<line class="grat" x1={i * (VW / 12)} y1="0" x2={i * (VW / 12)} y2={VH} />{/each}
      {#each Array.from({ length: 7 }) as _, i}<line class="grat" x1="0" y1={i * (VH / 6)} x2={VW} y2={i * (VH / 6)} />{/each}

      <!-- the movement route + a pulse travelling the subject's path in order -->
      {#if nodes.length >= 2}
        <path class="route base" d={pathD} />
        <path class="route draw" d={pathD} />
        <circle class="rpkt" r="3.4"><animateMotion dur={`${Math.max(3, nodes.length * 1.4)}s`} repeatCount="indefinite" path={pathD} /></circle>
      {/if}

      {#each nodes as nd, i (nd.name + i)}
        {@const big = focusIdx === i || (focusIdx === -1 && i === nodes.length - 1)}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <g class="node" class:cur={nd.cur} class:big style={`animation-delay:${120 + i * 90}ms`}
          transform={`translate(${nd.x} ${nd.y}) scale(${inv})`} role="button" tabindex="0"
          onpointerdown={(e) => { e.stopPropagation(); focus(i) }}
          onkeydown={(e) => { if (e.key === 'Enter') observe(nd.name) }} ondblclick={() => observe(nd.name)}>
          <circle class="glow" r="26" fill="url(#poiglow)" />
          <circle class="halo" r="13" />
          <circle class="ring" r="8" />
          {#if nd.cur && live}<circle class="pinglive" r="8" />{/if}
          <text class="seq" x="0" y="3">{i + 1}</text>
          <foreignObject x="14" y={big ? -54 : -40} width={big ? 190 : 128} height={big ? 150 : 104} style="overflow: visible">
            <div class="ncard" class:big class:curcard={nd.cur}>
              <div class="nthumb">
                {#if nd.id}<LiveThumb id={nd.id} fps={big ? 4 : 2} />{:else}<div class="nooff caps">NO FEED</div>{/if}
                {#if nd.cur && live}<span class="ndot"></span>{/if}
              </div>
              <div class="nmeta caps">
                <span class="nn">/// {nd.name}</span>
                <span class="ns">{clock24(nd.first)} · {nd.count}×{#if big} · DBL-CLICK ▶{/if}</span>
              </div>
            </div>
          </foreignObject>
        </g>
      {/each}
    </g>
  </svg>

  <!-- subject identity card (glass, bottom-left) -->
  <aside class="hero">
    <div class="frame" class:livef={live}>
      {#if entry.snapshot}
        <img src={heroSrc} alt="" />
        <span class="c tl"></span><span class="c tr"></span><span class="c bl"></span><span class="c br"></span>
        <button class="cut caps" onclick={() => (cutout = !cutout)}>{cutout ? '◧ BG' : '◨ CUT'}</button>
      {:else}<div class="nooff caps">NO IMAGE</div>{/if}
    </div>
    <div class="hmeta">
      <div class="chips">
        <span class="chip caps">{trUpper(entry.cls)}</span>
        {#if entry.attrs?.subtype}<span class="chip caps">{trUpper(entry.attrs.subtype)}</span>{/if}
        {#if entry.attrs?.make}<span class="chip caps">{trUpper(entry.attrs.make)} ~est</span>{/if}
        {#if entry.attrs?.upper_color}<span class="chip caps">{trUpper(entry.attrs.upper_color)}</span>{/if}
        {#if entry.plate}<span class="chip plate caps">▤ {entry.plate}</span>{/if}
      </div>
      <div class="hrow caps"><span class="k">FIRST</span><span class="v">{entry.first_cam ?? '—'} · {clock24(entry.first_ts)}</span></div>
      <div class="hrow caps"><span class="k">LAST</span><span class="v">{entry.cam ?? '—'} · {ago(entry.last_ts)}</span></div>
      <div class="hrow caps"><span class="k">SIGHTINGS</span><span class="v">{entry.obs}× · {nodes.length} CAM{nodes.length > 1 ? 'S' : ''}</span></div>
      {#if focusNode}<button class="observe caps" onclick={() => observe(focusNode.name)}><span class="pip"></span>OBSERVE {focusNode.name} ▶</button>{/if}
      <textarea class="notes" placeholder="Intelligence note…" value={a.notes ?? ''}
        oninput={(ev) => annotate(entry.id, { notes: (ev.target as HTMLTextAreaElement).value })}></textarea>
    </div>
  </aside>

  <!-- chronological timeline scrubber -->
  {#if nodes.length}
    <div class="timeline caps">
      {#each nodes as nd, i (nd.name + i)}
        <button class="tstep" class:on={i === focusIdx || (focusIdx === -1 && i === nodes.length - 1)} class:cur={nd.cur} onclick={() => focus(i)}>
          <span class="tdot"></span>
          <span class="tlabel"><span class="tn">{nd.name}</span><span class="tt">{clock24(nd.first)}</span></span>
        </button>
        {#if i < nodes.length - 1}<span class="tconn"></span>{/if}
      {/each}
    </div>
  {/if}
</div>

<style>
  .poi { position: fixed; inset: 0; z-index: var(--z-boot); background: radial-gradient(120% 80% at 50% 0%, #071016 0%, #04070a 70%);
    overflow: hidden; animation: poiin 420ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  .poi.closing { animation: poiout 320ms cubic-bezier(0.4, 0, 1, 1) both; }
  @keyframes poiin { from { opacity: 0; clip-path: inset(6% 6% 6% 6%); } to { opacity: 1; clip-path: inset(0 0 0 0); } }
  @keyframes poiout { to { opacity: 0; transform: scale(1.02); } }

  .top { position: absolute; top: 0; left: 0; right: 0; z-index: 5; display: flex; align-items: center; gap: 12px;
    padding: 14px 22px; font-size: var(--fs-label); letter-spacing: var(--tracking);
    background: linear-gradient(#04070a 40%, transparent); }
  .eyebrow { color: var(--scarlet); }
  .idc { color: var(--ink); font-weight: 700; letter-spacing: 0.18em; }
  .live { font-size: 9px; color: var(--ink-ghost); }
  .live.on { color: var(--cyan); animation: pulse 1.6s ease-in-out infinite; }
  .spacer { flex: 1; }
  .hint { color: var(--ink-ghost); font-size: 8px; }
  .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: pointer;
    font-size: 9px; letter-spacing: var(--tracking); }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }

  .net { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; cursor: grab; }
  .net:active { cursor: grabbing; }
  .grat { stroke: var(--hairline); stroke-width: 0.4; vector-effect: non-scaling-stroke; opacity: 0.6; }
  .route { fill: none; vector-effect: non-scaling-stroke; }
  .route.base { stroke: rgba(255,60,60,0.18); stroke-width: 3; }
  .route.draw { stroke: var(--scarlet); stroke-width: 1.6; filter: drop-shadow(0 0 5px var(--scarlet-glow));
    stroke-dasharray: 1400; stroke-dashoffset: 1400; animation: draw 1200ms 200ms ease forwards; }
  @keyframes draw { to { stroke-dashoffset: 0; } }
  .rpkt { fill: #fff; filter: drop-shadow(0 0 6px var(--scarlet)); }

  .node { cursor: pointer; animation: pop 420ms both cubic-bezier(0.16, 1, 0.3, 1); }
  .node:focus { outline: none; }
  @keyframes pop { from { opacity: 0; } }
  .node .halo { fill: rgba(56,208,227,0.08); }
  .node .ring { fill: rgba(56,208,227,0.15); stroke: var(--cyan); stroke-width: 1.5; filter: drop-shadow(0 0 6px var(--cyan)); }
  .node.cur .ring { stroke: #fff; fill: rgba(255,255,255,0.2); filter: drop-shadow(0 0 8px #fff); }
  .node .seq { fill: var(--ink); font-size: 9px; text-anchor: middle; font-family: var(--font-mono); font-weight: 700; }
  .node .glow { opacity: 0; transition: opacity 160ms; }
  .node.big .glow { opacity: 1; }
  .pinglive { fill: none; stroke: #fff; stroke-width: 1.4; transform-origin: center; animation: ping 1.8s ease-out infinite; }
  @keyframes ping { 0% { r: 8; opacity: 0.9; } 100% { r: 22; opacity: 0; } }

  .ncard { width: 118px; background: rgba(8,11,14,0.92); border: 1px solid var(--cyan); transition: width 160ms var(--ease); }
  .ncard.big { width: 176px; box-shadow: 0 0 18px rgba(56,208,227,0.35); }
  .ncard.curcard { border-color: #fff; box-shadow: 0 0 16px rgba(255,255,255,0.3); }
  .nthumb { position: relative; aspect-ratio: 16/9; overflow: hidden; background: #05070a; }
  .nthumb :global(.img) { width: 100%; height: 100%; object-fit: cover; }
  .ndot { position: absolute; top: 5px; right: 5px; width: 7px; height: 7px; border-radius: 50%; background: #fff; box-shadow: 0 0 6px #fff; animation: pulse 1.2s ease-in-out infinite; }
  .nmeta { display: flex; flex-direction: column; gap: 1px; padding: 4px 6px; }
  .nn { font-size: 9px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ns { font-size: 8px; color: var(--ink-ghost); }
  .nooff { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: 8px; }

  /* subject hero card */
  .hero { position: absolute; left: 22px; bottom: 78px; z-index: 5; width: 300px; display: flex; gap: 12px;
    background: rgba(6,10,13,0.82); border: 1px solid var(--scarlet); padding: 12px; backdrop-filter: blur(8px);
    box-shadow: 0 18px 50px rgba(0,0,0,0.6); animation: heroin 500ms 120ms both cubic-bezier(0.16, 1, 0.3, 1); }
  @keyframes heroin { from { transform: translateY(18px); opacity: 0; } }
  .frame { position: relative; width: 108px; flex: 0 0 auto; aspect-ratio: 3/4; overflow: hidden;
    background: repeating-conic-gradient(#0d1114 0% 25%, #0a0d10 0% 50%) 50% / 18px 18px; }
  .frame img { width: 100%; height: 100%; object-fit: cover; filter: saturate(0.7) contrast(1.06); }
  .frame.livef { box-shadow: inset 0 0 0 1px var(--cyan), inset 0 0 22px rgba(56,208,227,0.2); }
  .c { position: absolute; width: 12px; height: 12px; border: 2px solid var(--ink); opacity: 0.9; }
  .tl { top: 4px; left: 4px; border-right: 0; border-bottom: 0; } .tr { top: 4px; right: 4px; border-left: 0; border-bottom: 0; }
  .bl { bottom: 4px; left: 4px; border-right: 0; border-top: 0; } .br { bottom: 4px; right: 4px; border-left: 0; border-top: 0; }
  .cut { position: absolute; bottom: 5px; right: 5px; padding: 2px 6px; border: 1px solid var(--ink-dim);
    background: rgba(5,7,10,0.82); color: var(--ink-dim); font-size: 7px; letter-spacing: 0.08em; cursor: pointer; }
  .hmeta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 2px; }
  .chip { padding: 3px 7px; border: 1px solid var(--hairline); color: var(--ink); font-size: 7px; letter-spacing: 0.08em; }
  .chip.plate { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 40%, transparent); font-weight: 700; }
  .hrow { display: flex; justify-content: space-between; gap: 8px; font-size: 8px; }
  .hrow .k { color: var(--ink-dim); } .hrow .v { color: var(--ink); text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .observe { display: flex; align-items: center; justify-content: center; gap: 8px; margin-top: 4px; padding: 7px;
    border: 1px solid var(--scarlet); background: none; color: var(--ink); cursor: pointer; font-size: 8px; letter-spacing: var(--tracking); }
  .observe:hover { background: var(--scarlet); color: #fff; }
  .observe .pip { width: 7px; height: 7px; background: var(--scarlet); } .observe:hover .pip { background: #fff; }
  .notes { margin-top: 2px; min-height: 34px; resize: none; background: #05070a; border: 1px solid var(--hairline);
    color: var(--ink); font-family: inherit; font-size: 9px; padding: 5px; }
  .notes:focus { border-color: var(--scarlet); outline: none; }

  /* timeline scrubber */
  .timeline { position: absolute; left: 0; right: 0; bottom: 0; z-index: 5; display: flex; align-items: center;
    gap: 2px; padding: 12px 22px; overflow-x: auto; background: linear-gradient(transparent, #04070a 55%);
    animation: heroin 500ms 200ms both cubic-bezier(0.16, 1, 0.3, 1); }
  .tstep { display: flex; align-items: center; gap: 7px; padding: 5px 10px; background: none; border: 1px solid transparent;
    cursor: pointer; flex: 0 0 auto; }
  .tstep .tdot { width: 8px; height: 8px; border-radius: 50%; border: 1px solid var(--ink-dim); background: #0a0d10; flex: 0 0 auto; }
  .tstep.cur .tdot { background: #fff; border-color: #fff; box-shadow: 0 0 6px #fff; }
  .tstep.on { border-color: var(--cyan); background: rgba(56,208,227,0.08); }
  .tlabel { display: flex; flex-direction: column; line-height: 1.2; }
  .tn { font-size: 9px; color: var(--ink); letter-spacing: 0.06em; white-space: nowrap; }
  .tt { font-size: 7px; color: var(--ink-ghost); }
  .tconn { width: 22px; height: 1px; background: var(--hairline); flex: 0 0 auto; }
  @keyframes pulse { 50% { opacity: 0.45; } }

  @media (max-width: 760px) {
    .hero { width: min(300px, 90vw); bottom: 92px; }
  }
</style>
