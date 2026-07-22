<script lang="ts">
  // Overseer world-map topology (items 3,7,8,9,12,15).
  // Vector world map (crisp on zoom), cameras placed by lat/lng, camera cards +
  // live thumbnails, zoom-to-cursor pan/zoom, coordinate entry. Doubles as landing.
  import { onMount } from 'svelte'
  import { cameras, activeCam, camTrail, mode, pickerView, triggerGlitch, flashBanner } from '../../lib/stores'
  import { sfx } from '../../lib/audio'
  import { sendCommand } from '../../lib/ws'
  import { api } from '../../lib/api'
  import { SIM } from '../../lib/sim'
  import type { Camera } from '../../lib/types'
  import LiveThumb from '../LiveThumb.svelte'

  let { onpick }: { onpick?: (cam: Camera | null) => void } = $props()
  const landing = $derived(!!onpick)

  const VW = 1000, VH = 500
  const project = (lat: number, lng: number): [number, number] => [((lng + 180) / 360) * VW, ((90 - lat) / 180) * VH]
  const unproject = (x: number, y: number): [number, number] => [90 - (y / VH) * 180, (x / VW) * 360 - 180]
  let placed = $derived($cameras.filter((c) => c.coords))

  // — vector world map —
  let land = $state<string[]>([])
  onMount(async () => {
    try {
      const g = await (await fetch('/countries.geo.json')).json()
      const ring = (r: number[][]) => r.map((p, i) => `${i ? 'L' : 'M'}${project(p[1], p[0])[0].toFixed(1)} ${project(p[1], p[0])[1].toFixed(1)}`).join('') + 'Z'
      land = g.features.map((f: any) => {
        const gm = f.geometry
        if (gm.type === 'Polygon') return gm.coordinates.map(ring).join('')
        if (gm.type === 'MultiPolygon') return gm.coordinates.flat().map(ring).join('')
        return ''
      }).filter(Boolean)
    } catch { land = [] }
  })

  // — pan / zoom (zoom-to-cursor, no drift) —
  let tx = $state(0), ty = $state(0), k = $state(1)
  let svg = $state<SVGSVGElement>()
  let grp = $state<SVGGElement>()
  let drag: { x: number; y: number; tx: number; ty: number } | null = null
  let fitted = $state(false)

  $effect(() => {
    if (fitted || placed.length === 0) return
    const cs = placed.map((c) => project(c.coords![0], c.coords![1]))
    let a = Infinity, b = -Infinity, cc = Infinity, d = -Infinity
    for (const [x, y] of cs) { a = Math.min(a, x); b = Math.max(b, x); cc = Math.min(cc, y); d = Math.max(d, y) }
    const w = Math.max(b - a, 8), h = Math.max(d - cc, 8), pad = 3
    k = Math.min(12, Math.max(2, Math.min(VW / (w * pad), VH / (h * pad))))
    tx = VW / 2 - k * ((a + b) / 2)
    ty = VH / 2 - k * ((cc + d) / 2)
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
    const nk = Math.min(24, Math.max(0.9, k * (e.deltaY < 0 ? 1.15 : 0.87)))
    const loc = localAt(e.clientX, e.clientY)
    if (loc) { tx += (k - nk) * loc.x; ty += (k - nk) * loc.y }
    k = nk
  }
  function onDown(e: PointerEvent) {
    if (place && sel) return
    drag = { x: e.clientX, y: e.clientY, tx, ty }
    ;(e.currentTarget as Element).setPointerCapture(e.pointerId)
  }
  function onMove(e: PointerEvent) {
    if (!drag || !svg) return
    const s = VW / svg.clientWidth
    tx = drag.tx + (e.clientX - drag.x) * s
    ty = drag.ty + (e.clientY - drag.y) * s
  }
  function onUp(e: PointerEvent) {
    drag = null
    if (place && sel) {
      const loc = localAt(e.clientX, e.clientY)
      if (loc) { const [lat, lng] = unproject(loc.x, loc.y); save(sel, lat, lng); place = false }
    }
  }

  // — selection + coords —
  let sel = $state<string | null>(null)
  let hoverId = $state<string | null>(null)
  let mapOnline = $state<Record<string, boolean>>({})  // live-frame status per node
  let place = $state(false)
  let latIn = $state(''), lngIn = $state('')
  let selCam = $derived($cameras.find((c) => c.id === sel) ?? null)

  function selectCam(c: Camera) {
    sfx('click', { volume: 0.3 }); sel = c.id
    latIn = c.coords ? c.coords[0].toFixed(5) : ''
    lngIn = c.coords ? c.coords[1].toFixed(5) : ''
  }
  async function save(id: string, lat: number, lng: number) {
    sfx('sonar')
    cameras.update((l) => l.map((c) => (c.id === id ? { ...c, coords: [lat, lng] as [number, number] } : c)))
    latIn = lat.toFixed(5); lngIn = lng.toFixed(5)
    flashBanner('COORDINATE SAVED', false, 1000)
    try { await api.setCoords(id, lat, lng) } catch { /* offline */ }
  }
  function saveTyped() {
    const lat = parseFloat(latIn), lng = parseFloat(lngIn)
    if (sel && Number.isFinite(lat) && Number.isFinite(lng)) save(sel, lat, lng)
  }
  function observe(c: Camera) {
    triggerGlitch(200); sfx('glitch')
    if (onpick) { onpick(c); return }
    activeCam.set(c.id)
    if (!SIM) sendCommand(`connect:${c.name}`)
    mode.set('pov')
  }
  const inv = $derived(1 / k)
</script>

<section class="topo">
  <div class="hdr caps"><span class="hot">▽ ///</span> {landing ? 'COMMAND CENTER' : 'REGION / CAMERA NETWORK'}
    <span class="hint">DRAG · PAN &nbsp; WHEEL · ZOOM &nbsp; {landing ? 'CLICK NODE · OBSERVE' : 'ESC · POV'}</span></div>

  {#if landing}<button class="viewtoggle caps" onclick={() => pickerView.set('grid')}>▦ GRID VIEW</button>{/if}

  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <svg bind:this={svg} class="net" viewBox={`0 0 ${VW} ${VH}`} onwheel={onWheel}
    onpointerdown={onDown} onpointermove={onMove} onpointerup={onUp} class:placing={place && sel}>
    <g bind:this={grp} transform={`translate(${tx} ${ty}) scale(${k})`}>
      {#each Array.from({ length: 13 }) as _, i}<line class="grat" x1={i * (VW / 12)} y1="0" x2={i * (VW / 12)} y2={VH} />{/each}
      {#each Array.from({ length: 7 }) as _, i}<line class="grat" x1="0" y1={i * (VH / 6)} x2={VW} y2={i * (VH / 6)} />{/each}
      {#each land as d}<path class="land" {d} />{/each}

      {#each placed as c, i}
        {#if placed[i + 1]}
          {@const p = project(c.coords![0], c.coords![1])}{@const q = project(placed[i + 1].coords![0], placed[i + 1].coords![1])}
          <line class="flow" x1={p[0]} y1={p[1]} x2={q[0]} y2={q[1]} />
          <!-- live anonymized flow particles (feature 8) -->
          <circle class="pkt" r="1.8"><animateMotion dur={`${2.6 + (i % 3) * 0.8}s`} repeatCount="indefinite" path={`M${p[0]} ${p[1]} L${q[0]} ${q[1]}`} /></circle>
          <circle class="pkt" r="1.4"><animateMotion dur={`${2.6 + (i % 3) * 0.8}s`} begin="1.3s" repeatCount="indefinite" path={`M${p[0]} ${p[1]} L${q[0]} ${q[1]}`} /></circle>
        {/if}
      {/each}

      <!-- cross-camera observation route (feature 7) -->
      {#each $camTrail as id, i}
        {#if $camTrail[i + 1]}
          {@const ca = placed.find((c) => c.id === id)}{@const cb = placed.find((c) => c.id === $camTrail[i + 1])}
          {#if ca?.coords && cb?.coords}
            {@const a = project(ca.coords[0], ca.coords[1])}{@const b = project(cb.coords[0], cb.coords[1])}
            <line class="route" x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} />
            <circle class="rpkt" r="2.2"><animateMotion dur="2s" repeatCount="indefinite" path={`M${a[0]} ${a[1]} L${b[0]} ${b[1]}`} /></circle>
          {/if}
        {/if}
      {/each}

      {#each placed as c}
        {@const p = project(c.coords![0], c.coords![1])}
        {@const big = hoverId === c.id || sel === c.id}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <g class="node" class:sel={c.id === sel} class:off={mapOnline[c.id] === false}
          transform={`translate(${p[0]} ${p[1]}) scale(${inv})`} role="button" tabindex="0"
          onpointerenter={() => (hoverId = c.id)} onpointerleave={() => (hoverId = null)}
          onpointerdown={(e) => { e.stopPropagation(); selectCam(c) }}
          onkeydown={(e) => { if (e.key === 'Enter') observe(c) }} ondblclick={() => observe(c)}>
          <circle class="halo" r="12" />
          <circle class="ring" r="7" />
          <line class="tick" x1="-12" y1="0" x2="-9" y2="0" /><line class="tick" x1="9" y1="0" x2="12" y2="0" />
          <line class="tick" x1="0" y1="-12" x2="0" y2="-9" /><line class="tick" x1="0" y1="9" x2="0" y2="12" />

          <!-- always-on compact live card; enlarges on hover/select. The
               foreignObject grows with it (fixed size would clip the bottom + border). -->
          <foreignObject x="12" y={big ? -52 : -42} width={big ? 192 : 130} height={big ? 152 : 106} style="overflow: visible">
            <div class="ncard" class:big data-node={c.id}>
              <div class="nthumb"><LiveThumb id={c.id} fps={hoverId === c.id ? 4 : 2} onstatus={(o) => (mapOnline[c.id] = o)} /></div>
              <div class="nmeta caps"><span class="nn">/// {c.name}</span><span class="ns">CAM {c.id} · {mapOnline[c.id] ? 'ONLINE' : '—'}</span></div>
            </div>
          </foreignObject>
        </g>
      {/each}
    </g>
  </svg>

  <aside class="list panel caps">
    <div class="ph">SOURCES · {$cameras.length}</div>
    {#each $cameras as c}
      <button class="src" class:on={c.id === sel} onclick={() => selectCam(c)} ondblclick={() => observe(c)}>
        <span class="dot" class:live={c.health === 'online'}></span>
        <span class="nm">{c.name}</span><span class="cc">{c.coords ? '◈' : 'NO POS'}</span>
      </button>
    {/each}
    {#if $cameras.length === 0}<div class="mt">NO SOURCES</div>{/if}
  </aside>

  {#if selCam}
    <aside class="editor panel caps">
      <div class="tab" class:hot={selCam.health === 'online'}>/// {selCam.name}</div>
      <div class="r"><span class="k">CAM</span><span class="v">{selCam.id}</span></div>
      <div class="r"><span class="k">STATUS</span><span class="v">{selCam.health === 'online' ? 'ONLINE' : 'OFFLINE'}</span></div>
      <div class="r"><span class="k">POSITION</span><span class="v">{selCam.coords ? `${selCam.coords[0].toFixed(3)}, ${selCam.coords[1].toFixed(3)}` : 'NONE'}</span></div>
      <div class="coord">
        <div class="cl">ENTER COORDINATES</div>
        <div class="ins">
          <label>LAT<input type="number" step="0.00001" bind:value={latIn} placeholder="41.01" /></label>
          <label>LNG<input type="number" step="0.00001" bind:value={lngIn} placeholder="28.97" /></label>
        </div>
        <div class="btns">
          <button class="b" onclick={saveTyped}>SAVE</button>
          <button class="b" class:active={place} onclick={() => (place = !place)}>{place ? 'CLICK MAP…' : 'PLACE ON MAP'}</button>
        </div>
      </div>
      <button class="observe" onclick={() => observe(selCam!)}><span class="pip"></span>OBSERVE ▶</button>
    </aside>
  {/if}
</section>

<style>
  .topo { position: absolute; inset: 0; z-index: var(--z-boot); background: #04070a; overflow: hidden; }
  .hdr { position: absolute; top: 22px; left: 30px; z-index: 3; font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--scarlet); }
  .hdr .hint { color: var(--ink-ghost); margin-left: 14px; }
  .viewtoggle { position: absolute; top: 20px; right: 30px; z-index: 4; padding: 7px 14px; border: 1px solid var(--ink-dim); color: var(--ink-dim); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .viewtoggle:hover { border-color: var(--scarlet); color: var(--scarlet); }

  .net { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; cursor: grab; }
  .net:active { cursor: grabbing; } .net.placing { cursor: crosshair; }
  .land { fill: rgba(56,208,227,0.05); stroke: var(--cyan); stroke-width: 0.4; vector-effect: non-scaling-stroke; opacity: 0.55; }
  .grat { stroke: var(--hairline); stroke-width: 0.4; vector-effect: non-scaling-stroke; }
  .flow { stroke: var(--cyan); stroke-width: 1; stroke-dasharray: 4 4; opacity: 0.7; vector-effect: non-scaling-stroke; filter: drop-shadow(0 0 3px var(--cyan)); animation: flow 1.4s linear infinite; }
  @keyframes flow { to { stroke-dashoffset: -8; } }
  .pkt { fill: var(--cyan); filter: drop-shadow(0 0 4px var(--cyan)); }
  .route { stroke: var(--scarlet); stroke-width: 1.4; opacity: 0.85; vector-effect: non-scaling-stroke; filter: drop-shadow(0 0 5px var(--scarlet-glow)); }
  .rpkt { fill: var(--scarlet); filter: drop-shadow(0 0 5px var(--scarlet-glow)); }

  .node { cursor: crosshair; }
  .node:focus, .node:focus-visible { outline: none; } /* no white browser focus ring */
  .node .ring { fill: rgba(56,208,227,0.15); stroke: var(--cyan); stroke-width: 1.5; filter: drop-shadow(0 0 6px var(--cyan)); }
  .node .tick { stroke: var(--cyan); stroke-width: 1.2; }
  .node.off .ring { stroke: var(--ink-ghost); filter: none; fill: none; stroke-dasharray: 3 3; }
  .node.off .tick { stroke: var(--ink-ghost); }
  .node.sel .ring { stroke: var(--cyan); fill: rgba(56,208,227,0.22); filter: drop-shadow(0 0 8px var(--cyan)); }
  .node .halo { fill: rgba(56,208,227,0.1); stroke: none; }

  .ncard { width: 118px; background: rgba(10,11,12,0.9); border: 1px solid var(--cyan); transition: width 160ms var(--ease); }
  .ncard.big { width: 168px; box-shadow: 0 0 16px rgba(56,208,227,0.35); }
  .nthumb { position: relative; aspect-ratio: 16/9; overflow: hidden; background: #05070a; }
  .nmeta { display: flex; flex-direction: column; gap: 1px; padding: 4px 6px; }
  .nn { font-size: 9px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ns { font-size: 8px; color: var(--ink-ghost); }

  .list { position: absolute; left: 24px; top: 66px; width: 210px; max-height: 70vh; overflow-y: auto; z-index: 3; padding: 8px; }
  .list .ph { font-size: var(--fs-micro); color: var(--scarlet); letter-spacing: var(--tracking); margin-bottom: 6px; }
  .src { display: flex; align-items: center; gap: 8px; width: 100%; padding: 5px 4px; font-size: var(--fs-micro); color: var(--ink-dim); }
  .src.on { color: var(--ink); background: #14161a; }
  .src .dot { width: 7px; height: 7px; border: 1px solid var(--ink-ghost); flex: 0 0 auto; }
  .src .dot.live { background: var(--cyan); border-color: var(--cyan); box-shadow: 0 0 5px var(--cyan); }
  .src .nm { flex: 1; text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .src .cc { color: var(--ink-ghost); font-size: 8px; }
  .mt { color: var(--ink-ghost); font-size: var(--fs-micro); padding: 6px; }

  .editor { position: absolute; right: 24px; top: 66px; width: 250px; z-index: 3; padding: 0 0 10px; }
  .editor .tab { padding: 6px 10px; background: #000; border-bottom: 1px solid var(--hairline); font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink); }
  .editor .tab.hot { color: var(--cyan); }
  .editor .r { display: flex; justify-content: space-between; padding: 3px 10px; font-size: var(--fs-micro); }
  .editor .k { color: var(--ink-dim); } .editor .v { color: var(--ink); }
  .coord { border-top: 1px solid var(--hairline); margin-top: 6px; padding: 8px 10px; }
  .cl { font-size: var(--fs-micro); color: var(--scarlet); letter-spacing: var(--tracking); margin-bottom: 6px; }
  .ins { display: flex; gap: 8px; } .ins label { flex: 1; display: flex; flex-direction: column; gap: 3px; font-size: 8px; color: var(--ink-ghost); }
  .ins input { background: #000; border: 1px solid var(--hairline); color: var(--ink); font-family: var(--font-mono); font-size: var(--fs-micro); padding: 4px 6px; }
  .ins input:focus { outline: none; border-color: var(--cyan); }
  .btns { display: flex; gap: 6px; margin-top: 8px; }
  .b { flex: 1; padding: 5px; border: 1px solid var(--ink-dim); font-size: 8px; letter-spacing: 0.08em; color: var(--ink-dim); }
  .b:hover { border-color: var(--ink); color: var(--ink); } .b.active { border-color: var(--cyan); color: var(--cyan); }
  .observe { display: flex; align-items: center; justify-content: center; gap: 10px; width: calc(100% - 20px); margin: 10px 10px 0;
    padding: 9px; border: 1px solid var(--scarlet); color: var(--ink); font-family: var(--font-display); letter-spacing: var(--tracking); }
  .observe:hover { background: var(--scarlet); color: #fff; } .observe .pip { width: 8px; height: 8px; background: var(--scarlet); } .observe:hover .pip { background: #fff; }
</style>
