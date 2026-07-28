<script lang="ts">
  // Full-screen subject dossier laid out like a CASE FILE: a subject/evidence column, a
  // central interactive movement map (pan/zoom, live feeds at every camera the subject was
  // seen on), and an intelligence column (chronology, per-camera log, notes). Rich detail on
  // screen AND the interactive map — not a bare map, not a text popup.
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
  const VW = 1000, VH = 560
  const project = (lat: number, lng: number): [number, number] => [((lng + 180) / 360) * VW, ((90 - lat) / 180) * VH]

  let closing = $state(false)
  let cutout = $state(false)
  let focusIdx = $state(-1)

  let a = $derived($annotations[entry.id] ?? {})
  let live = $derived(now - entry.last_ts < LIVE_MS)
  let title = $derived(entry.cls === 'vehicle' ? 'VEHICLE' : 'PERSON')
  const camByName = (name?: string | null) => $cameras.find((c) => c.name === name)
  const photo = $derived(entry.snapshot ? `${API}${entry.snapshot}?t=${entry.last_ts}` : '')
  const heroSrc = $derived(cutout ? `${API}/api/roster/${entry.id}/cutout?t=${entry.last_ts}` : photo)

  let trail = $derived(entry.trail ?? [])
  let geo = $derived(trail.length >= 2 && trail.every((t) => !!camByName(t.cam)?.coords))
  let nodes = $derived(trail.map((t, i) => {
    const cam = camByName(t.cam)
    let x: number, y: number
    if (geo && cam?.coords) {
      ;[x, y] = project(cam.coords[0], cam.coords[1])
    } else {
      const n = trail.length
      x = n <= 1 ? VW * 0.5 : VW * (0.12 + 0.76 * (i / (n - 1)))
      y = VH * 0.5 + (i % 2 === 0 ? -1 : 1) * VH * 0.16
    }
    return { ...t, id: cam?.id, name: t.cam, x, y, cur: t.cam === entry.cam }
  }))
  let pathD = $derived(nodes.map((nd, i) => `${i ? 'L' : 'M'}${nd.x.toFixed(1)} ${nd.y.toFixed(1)}`).join(' '))
  let focusNode = $derived(nodes[focusIdx] ?? nodes[nodes.length - 1] ?? null)

  const apprLine = $derived(
    [entry.attrs?.upper_color, entry.cls === 'person' ? entry.attrs?.height : undefined]
      .filter(Boolean).map((s) => trUpper(String(s))).join(' · '))
  const clock24 = (ms: number) => { const d = new Date(ms); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':') }
  const hhmm = (ms: number) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
  const dmy = (ms: number) => new Date(ms).toLocaleDateString('en-GB')
  function ago(ms: number): string {
    const s = Math.max(0, Math.round((now - ms) / 1000))
    if (s < 5) return 'just now'
    if (s < 60) return `${s}s ago`
    const m = Math.round(s / 60)
    return m < 60 ? `${m}m ago` : `${Math.round(m / 60)}h ago`
  }
  function duration(ms: number): string {
    const m = Math.round(ms / 60000)
    if (m < 1) return '<1m'
    return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`
  }

  // — pan / zoom for the movement map —
  let tx = $state(0), ty = $state(0), k = $state(1)
  let svg = $state<SVGSVGElement>()
  let grp = $state<SVGGElement>()
  let drag: { x: number; y: number; tx: number; ty: number } | null = null
  let fitted = $state(false)

  $effect(() => {
    if (fitted || nodes.length === 0) return
    let a0 = Infinity, b0 = -Infinity, c0 = Infinity, d0 = -Infinity
    for (const nd of nodes) { a0 = Math.min(a0, nd.x); b0 = Math.max(b0, nd.x); c0 = Math.min(c0, nd.y); d0 = Math.max(d0, nd.y) }
    const w = Math.max(b0 - a0, 40), h = Math.max(d0 - c0, 40), pad = 2.4
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
    const nd = nodes[i]; if (!nd) return
    const nk = Math.max(k, 2.6)
    k = nk; tx = VW / 2 - nk * nd.x; ty = VH / 2 - nk * nd.y
  }
  function observe(name?: string | null) {
    const cam = camByName(name); if (!cam) return
    sfx('glitch'); triggerGlitch(220)
    activeCam.set(cam.id)
    if (!SIM) sendCommand(`connect:${cam.name}`)
    flashBanner(`OBSERVING ${cam.name}`, false, 1200)
    mode.set('pov')
  }
  function close() { if (closing) return; sfx('click'); closing = true; setTimeout(onclose, 320) }
  function onkey(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); close() } }
  onMount(() => { sfx('sonar'); window.addEventListener('keydown', onkey, true) })
  onDestroy(() => window.removeEventListener('keydown', onkey, true))
  const inv = $derived(1 / k)
</script>

<div class="poi" class:closing role="dialog" aria-modal="true" aria-label="Subject dossier">
  <header class="top caps">
    <span class="eyebrow">/// CASE FILE</span>
    <span class="idc">{title} · {a.alias || entry.id}</span>
    <span class="live" class:on={live}>{live ? '● LIVE' : '○ IDLE'}</span>
    <span class="spacer"></span>
    <span class="ref caps">REF {entry.id} · OPENED {dmy(entry.first_ts)}</span>
    <button class="x caps" onclick={close}>✕ CLOSE</button>
  </header>

  <div class="sheet">
    <!-- SUBJECT / EVIDENCE -->
    <aside class="col subject">
      <div class="panel photo c1">
        <div class="frame" class:livef={live}>
          {#if entry.snapshot}
            <img src={heroSrc} alt="" />
            <span class="cn tl"></span><span class="cn tr"></span><span class="cn bl"></span><span class="cn br"></span>
            <button class="cut caps" onclick={() => (cutout = !cutout)}>{cutout ? '◧ SHOW BG' : '◨ REMOVE BG'}</button>
            <span class="pidtag caps">{a.alias || entry.id}</span>
          {:else}<div class="nofeed caps">NO IMAGE</div>{/if}
        </div>
      </div>
      <div class="panel c2">
        <div class="ph caps">/// IDENTITY</div>
        <div class="rows">
          <div class="r caps"><span class="k">CLASS</span><span class="v">{trUpper(entry.cls)}</span></div>
          {#if entry.attrs?.subtype}<div class="r caps"><span class="k">TYPE</span><span class="v">{trUpper(entry.attrs.subtype)}</span></div>{/if}
          {#if entry.attrs?.make}<div class="r caps"><span class="k">MAKE</span><span class="v">{trUpper(entry.attrs.make)}<span class="est"> ~est</span></span></div>{/if}
          {#if entry.plate}<div class="r caps"><span class="k">PLATE</span><span class="v plate">{entry.plate}</span></div>{/if}
          {#if apprLine}<div class="r caps"><span class="k">APPEARANCE</span><span class="v">{apprLine}</span></div>{/if}
        </div>
      </div>
      <div class="panel c3">
        <div class="ph caps">/// STATUS</div>
        <div class="rows">
          <div class="r caps"><span class="k">STATE</span><span class="v" class:hot={live}>{live ? 'IN VIEW · TRACKING' : 'OUT OF VIEW'}</span></div>
          <div class="r caps"><span class="k">ON CAMERA</span><span class="v">{entry.cam ?? '—'}</span></div>
          <div class="r caps"><span class="k">LAST SEEN</span><span class="v">{ago(entry.last_ts)}</span></div>
        </div>
      </div>
    </aside>

    <!-- MOVEMENT MAP -->
    <main class="col map c2">
      <div class="ph caps"><span>/// MOVEMENT · {nodes.length} CAM{nodes.length > 1 ? 'S' : ''}</span>
        <span class="maphint">DRAG · PAN&nbsp;&nbsp;WHEEL · ZOOM</span></div>
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <svg bind:this={svg} class="net" viewBox={`0 0 ${VW} ${VH}`} onwheel={onWheel}
        onpointerdown={onDown} onpointermove={onMove} onpointerup={onUp}>
        <defs><radialGradient id="poig" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="rgba(56,208,227,0.18)" /><stop offset="100%" stop-color="transparent" /></radialGradient></defs>
        <g bind:this={grp} transform={`translate(${tx} ${ty}) scale(${k})`}>
          {#each Array.from({ length: 13 }) as _, i}<line class="grat" x1={i * (VW / 12)} y1="0" x2={i * (VW / 12)} y2={VH} />{/each}
          {#each Array.from({ length: 8 }) as _, i}<line class="grat" x1="0" y1={i * (VH / 7)} x2={VW} y2={i * (VH / 7)} />{/each}
          {#if nodes.length >= 2}
            <path class="route base" d={pathD} />
            <path class="route draw" d={pathD} />
            <circle class="rpkt" r="3.4"><animateMotion dur={`${Math.max(3, nodes.length * 1.4)}s`} repeatCount="indefinite" path={pathD} /></circle>
          {/if}
          {#each nodes as nd, i (nd.name + i)}
            {@const big = focusIdx === i || (focusIdx === -1 && i === nodes.length - 1)}
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <g class="node" class:cur={nd.cur} class:big style={`animation-delay:${140 + i * 90}ms`}
              transform={`translate(${nd.x} ${nd.y}) scale(${inv})`} role="button" tabindex="0"
              onpointerdown={(e) => { e.stopPropagation(); focus(i) }}
              onkeydown={(e) => { if (e.key === 'Enter') observe(nd.name) }} ondblclick={() => observe(nd.name)}>
              <circle class="glow" r="26" fill="url(#poig)" />
              <circle class="halo" r="13" /><circle class="ring" r="8" />
              {#if nd.cur && live}<circle class="pinglive" r="8" />{/if}
              <text class="seq" x="0" y="3">{i + 1}</text>
              <foreignObject x="14" y={big ? -52 : -38} width={big ? 184 : 122} height={big ? 146 : 100} style="overflow: visible">
                <div class="ncard" class:big class:curcard={nd.cur}>
                  <div class="nthumb">
                    {#if nd.id}<LiveThumb id={nd.id} fps={big ? 4 : 2} />{:else}<div class="nofeed caps">NO FEED</div>{/if}
                    {#if nd.cur && live}<span class="ndot"></span>{/if}
                  </div>
                  <div class="nmeta caps"><span class="nn">/// {nd.name}</span><span class="ns">{clock24(nd.first)} · {nd.count}×{#if big} · ▶ DBL{/if}</span></div>
                </div>
              </foreignObject>
            </g>
          {/each}
        </g>
      </svg>
      <div class="timeline caps">
        {#each nodes as nd, i (nd.name + i)}
          <button class="tstep" class:on={i === focusIdx || (focusIdx === -1 && i === nodes.length - 1)} class:cur={nd.cur} onclick={() => focus(i)}>
            <span class="tdot"></span><span class="tlabel"><span class="tn">{nd.name}</span><span class="tt">{clock24(nd.first)}</span></span>
          </button>
          {#if i < nodes.length - 1}<span class="tconn"></span>{/if}
        {/each}
      </div>
    </main>

    <!-- INTELLIGENCE -->
    <aside class="col intel">
      <div class="panel c1">
        <div class="ph caps">/// CHRONOLOGY</div>
        <div class="rows">
          <div class="r caps"><span class="k">FIRST SEEN</span><span class="v">{entry.first_cam ?? '—'}</span></div>
          <div class="r caps"><span class="k">AT</span><span class="v">{clock24(entry.first_ts)} · {dmy(entry.first_ts)}</span></div>
          <div class="r caps"><span class="k">LAST SEEN</span><span class="v">{entry.cam ?? '—'} · {ago(entry.last_ts)}</span></div>
          <div class="r caps"><span class="k">TRACKED FOR</span><span class="v">{duration(entry.last_ts - entry.first_ts)}</span></div>
          <div class="r caps"><span class="k">SIGHTINGS</span><span class="v">{entry.obs}×</span></div>
        </div>
      </div>
      <div class="panel log c2">
        <div class="ph caps">/// CAMERA LOG</div>
        <div class="logrows">
          {#each nodes as nd, i (nd.name + i)}
            <button class="logrow caps" class:cur={nd.cur} onclick={() => focus(i)} ondblclick={() => observe(nd.name)}>
              <span class="lseq">{i + 1}</span>
              <span class="lname">{nd.name}</span>
              <span class="ltime">{hhmm(nd.first)}{#if nd.last - nd.first > 60000}–{hhmm(nd.last)}{/if}</span>
              <span class="lcount">{nd.count}×</span>
            </button>
          {/each}
          {#if nodes.length === 0}<div class="nofeed caps" style="padding:12px">NO SIGHTINGS</div>{/if}
        </div>
      </div>
      <div class="panel c3">
        <div class="ph caps">/// NOTES</div>
        <textarea class="notes" placeholder="Add an intelligence note…" value={a.notes ?? ''}
          oninput={(ev) => annotate(entry.id, { notes: (ev.target as HTMLTextAreaElement).value })}></textarea>
      </div>
      {#if focusNode}
        <button class="observe caps c3" onclick={() => observe(focusNode.name)}><span class="pip"></span>OBSERVE {focusNode.name} ▶</button>
      {/if}
    </aside>
  </div>
</div>

<style>
  .poi { position: fixed; inset: 0; z-index: var(--z-boot); background: radial-gradient(120% 80% at 50% 0%, #071016 0%, #04070a 72%);
    color: var(--ink); display: flex; flex-direction: column; overflow: hidden;
    animation: poiin 420ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  .poi.closing { animation: poiout 300ms cubic-bezier(0.4, 0, 1, 1) both; }
  @keyframes poiin { from { opacity: 0; clip-path: inset(5% 5% 5% 5%); } to { opacity: 1; clip-path: inset(0 0 0 0); } }
  @keyframes poiout { to { opacity: 0; transform: scale(1.015); } }

  .top { display: flex; align-items: center; gap: 12px; padding: 13px 22px; border-bottom: 1px solid var(--hairline);
    font-size: var(--fs-label); letter-spacing: var(--tracking); background: #04070a; }
  .eyebrow { color: var(--scarlet); }
  .idc { color: var(--ink); font-weight: 700; letter-spacing: 0.16em; }
  .live { font-size: 9px; color: var(--ink-ghost); }
  .live.on { color: var(--cyan); animation: pulse 1.6s ease-in-out infinite; }
  .spacer { flex: 1; }
  .ref { color: var(--ink-ghost); font-size: 8px; }
  .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: pointer; font-size: 9px; letter-spacing: var(--tracking); }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }

  .sheet { flex: 1; min-height: 0; display: grid; grid-template-columns: 300px minmax(0, 1fr) 300px; gap: 12px; padding: 14px; }
  .col { display: flex; flex-direction: column; gap: 12px; min-height: 0; }
  .col.subject, .col.intel { overflow: auto; }
  .panel { border: 1px solid var(--hairline); background: rgba(7,11,14,0.6); animation: rise 440ms both cubic-bezier(0.16, 1, 0.3, 1); }
  .c1 { animation-delay: 60ms; } .c2 { animation-delay: 130ms; } .c3 { animation-delay: 200ms; }
  @keyframes rise { from { transform: translateY(14px); opacity: 0; } }
  .ph { display: flex; justify-content: space-between; align-items: center; padding: 7px 10px; background: #04070a;
    border-bottom: 1px solid var(--hairline); font-size: 9px; color: var(--scarlet); letter-spacing: var(--tracking); }
  .rows { padding: 9px 10px; display: flex; flex-direction: column; gap: 5px; }
  .r { display: flex; justify-content: space-between; gap: 10px; font-size: 9px; }
  .r .k { color: var(--ink-dim); } .r .v { color: var(--ink); text-align: right; }
  .r .v.hot { color: var(--cyan); } .r .v.plate { color: var(--cyan); letter-spacing: 0.1em; font-weight: 700; }
  .est { color: var(--ink-dim); }

  /* subject photo */
  .photo { padding: 10px; }
  .frame { position: relative; aspect-ratio: 3/4; overflow: hidden;
    background: repeating-conic-gradient(#0d1114 0% 25%, #0a0d10 0% 50%) 50% / 20px 20px; }
  .frame img { width: 100%; height: 100%; object-fit: cover; filter: saturate(0.72) contrast(1.06); }
  .frame.livef { box-shadow: inset 0 0 0 1px var(--cyan), inset 0 0 24px rgba(56,208,227,0.2); }
  .cn { position: absolute; width: 14px; height: 14px; border: 2px solid var(--ink); opacity: 0.9; }
  .tl { top: 5px; left: 5px; border-right: 0; border-bottom: 0; } .tr { top: 5px; right: 5px; border-left: 0; border-bottom: 0; }
  .bl { bottom: 5px; left: 5px; border-right: 0; border-top: 0; } .br { bottom: 5px; right: 5px; border-left: 0; border-top: 0; }
  .cut { position: absolute; bottom: 6px; right: 6px; padding: 3px 8px; border: 1px solid var(--ink-dim); background: rgba(5,7,10,0.82); color: var(--ink-dim); font-size: 8px; letter-spacing: 0.08em; cursor: pointer; }
  .cut:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .pidtag { position: absolute; top: 6px; left: 6px; font-size: 9px; letter-spacing: 0.14em; color: #fff; background: rgba(5,7,10,0.72); padding: 2px 7px; }

  /* movement map panel */
  .map { border: 1px solid var(--hairline); background: rgba(4,7,10,0.5); animation: rise 440ms 130ms both cubic-bezier(0.16, 1, 0.3, 1); }
  .map .ph { flex: 0 0 auto; }
  .maphint { color: var(--ink-ghost); font-size: 8px; }
  .net { flex: 1; min-height: 0; width: 100%; touch-action: none; cursor: grab; }
  .net:active { cursor: grabbing; }
  .grat { stroke: var(--hairline); stroke-width: 0.4; vector-effect: non-scaling-stroke; opacity: 0.55; }
  .route { fill: none; vector-effect: non-scaling-stroke; }
  .route.base { stroke: rgba(255,60,60,0.18); stroke-width: 3; }
  .route.draw { stroke: var(--scarlet); stroke-width: 1.6; filter: drop-shadow(0 0 5px var(--scarlet-glow)); stroke-dasharray: 1600; stroke-dashoffset: 1600; animation: draw 1200ms 220ms ease forwards; }
  @keyframes draw { to { stroke-dashoffset: 0; } }
  .rpkt { fill: #fff; filter: drop-shadow(0 0 6px var(--scarlet)); }
  .node { cursor: pointer; animation: pop 420ms both cubic-bezier(0.16, 1, 0.3, 1); }
  .node:focus { outline: none; }
  @keyframes pop { from { opacity: 0; } }
  .node .halo { fill: rgba(56,208,227,0.08); }
  .node .ring { fill: rgba(56,208,227,0.15); stroke: var(--cyan); stroke-width: 1.5; filter: drop-shadow(0 0 6px var(--cyan)); }
  .node.cur .ring { stroke: #fff; fill: rgba(255,255,255,0.2); filter: drop-shadow(0 0 8px #fff); }
  .node .seq { fill: var(--ink); font-size: 9px; text-anchor: middle; font-family: var(--font-mono); font-weight: 700; }
  .node .glow { opacity: 0; transition: opacity 160ms; } .node.big .glow { opacity: 1; }
  .pinglive { fill: none; stroke: #fff; stroke-width: 1.4; animation: ping 1.8s ease-out infinite; }
  @keyframes ping { 0% { r: 8; opacity: 0.9; } 100% { r: 22; opacity: 0; } }
  .ncard { width: 112px; background: rgba(8,11,14,0.92); border: 1px solid var(--cyan); transition: width 160ms var(--ease); }
  .ncard.big { width: 172px; box-shadow: 0 0 18px rgba(56,208,227,0.35); }
  .ncard.curcard { border-color: #fff; box-shadow: 0 0 16px rgba(255,255,255,0.3); }
  .nthumb { position: relative; aspect-ratio: 16/9; overflow: hidden; background: #05070a; }
  .nthumb :global(.img) { width: 100%; height: 100%; object-fit: cover; }
  .ndot { position: absolute; top: 5px; right: 5px; width: 7px; height: 7px; border-radius: 50%; background: #fff; box-shadow: 0 0 6px #fff; animation: pulse 1.2s ease-in-out infinite; }
  .nmeta { display: flex; flex-direction: column; gap: 1px; padding: 4px 6px; }
  .nn { font-size: 9px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ns { font-size: 8px; color: var(--ink-ghost); }
  .nofeed { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: 8px; }

  .timeline { flex: 0 0 auto; display: flex; align-items: center; gap: 2px; padding: 8px 12px; overflow-x: auto; border-top: 1px solid var(--hairline); background: #04070a; }
  .tstep { display: flex; align-items: center; gap: 7px; padding: 4px 9px; background: none; border: 1px solid transparent; cursor: pointer; flex: 0 0 auto; }
  .tstep .tdot { width: 8px; height: 8px; border-radius: 50%; border: 1px solid var(--ink-dim); background: #0a0d10; flex: 0 0 auto; }
  .tstep.cur .tdot { background: #fff; border-color: #fff; box-shadow: 0 0 6px #fff; }
  .tstep.on { border-color: var(--cyan); background: rgba(56,208,227,0.08); }
  .tlabel { display: flex; flex-direction: column; line-height: 1.2; }
  .tn { font-size: 9px; color: var(--ink); white-space: nowrap; } .tt { font-size: 7px; color: var(--ink-ghost); }
  .tconn { width: 20px; height: 1px; background: var(--hairline); flex: 0 0 auto; }

  /* camera log */
  .log .logrows { display: flex; flex-direction: column; max-height: 220px; overflow-y: auto; }
  .logrow { display: grid; grid-template-columns: 18px 1fr auto auto; align-items: center; gap: 8px; width: 100%;
    padding: 6px 10px; background: none; border: 0; border-bottom: 1px solid var(--hairline); cursor: pointer; text-align: left; }
  .logrow:hover { background: rgba(56,208,227,0.06); }
  .logrow.cur { background: rgba(255,255,255,0.06); }
  .lseq { color: var(--ink-ghost); font-size: 8px; } .lname { color: var(--ink); font-size: 9px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ltime { color: var(--ink-dim); font-size: 8px; } .lcount { color: var(--cyan); font-size: 8px; }

  .notes { width: 100%; min-height: 64px; resize: vertical; background: #05070a; border: 0; color: var(--ink); font-family: inherit; font-size: 10px; padding: 8px; }
  .notes:focus { outline: none; }
  .observe { display: flex; align-items: center; justify-content: center; gap: 9px; padding: 10px; border: 1px solid var(--scarlet);
    background: none; color: var(--ink); cursor: pointer; font-size: 9px; letter-spacing: var(--tracking); animation: rise 440ms 200ms both cubic-bezier(0.16, 1, 0.3, 1); }
  .observe:hover { background: var(--scarlet); color: #fff; }
  .observe .pip { width: 8px; height: 8px; background: var(--scarlet); } .observe:hover .pip { background: #fff; }
  @keyframes pulse { 50% { opacity: 0.45; } }

  @media (max-width: 1040px) {
    .sheet { grid-template-columns: 1fr; grid-auto-rows: min-content; overflow: auto; }
    .col.subject, .col.intel { overflow: visible; }
    .map { min-height: 420px; }
  }
</style>
