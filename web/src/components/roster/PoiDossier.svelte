<script lang="ts">
  // Cinematic "person of interest" dossier. Full-screen, dark and glassy: a subject hero with
  // a targeting reticle that locks onto the portrait and a large display designation, animated
  // count-up stats, and an interactive movement trace (pan/zoom map with live feeds at every
  // camera the subject was seen on). Rich, composed, animated — not a terminal read-out.
  import { onDestroy, onMount } from 'svelte'
  import { tweened } from 'svelte/motion'
  import { cubicOut } from 'svelte/easing'
  import { cameras, activeCam, mode, triggerGlitch, flashBanner, watchlistOpen } from '../../lib/stores'
  import { annotations, annotate } from '../../lib/annotations'
  import { enroll } from '../../lib/watchlist'
  import { api } from '../../lib/api'
  import { trUpper } from '../../lib/lexicon'
  import { sfx } from '../../lib/audio'
  import { sendCommand } from '../../lib/ws'
  import { SIM } from '../../lib/sim'
  import LiveThumb from '../LiveThumb.svelte'
  import type { RosterEntry } from '../../lib/types'

  let { entry, now, onclose }: { entry: RosterEntry; now: number; onclose: () => void } = $props()

  const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
  const LIVE_MS = 8000
  const VW = 1000, VH = 620
  const project = (lat: number, lng: number): [number, number] => [((lng + 180) / 360) * VW, ((90 - lat) / 180) * VH]

  let closing = $state(false)
  let cutout = $state(false)
  let focusIdx = $state(-1)

  let a = $derived($annotations[entry.id] ?? {})
  let live = $derived(now - entry.last_ts < LIVE_MS)
  let kind = $derived(entry.cls === 'vehicle' ? 'VEHICLE DOSSIER' : 'SUBJECT DOSSIER')
  const camByName = (name?: string | null) => $cameras.find((c) => c.name === name)
  const photo = $derived(entry.snapshot ? `${API}${entry.snapshot}?t=${entry.last_ts}` : '')
  const heroSrc = $derived(cutout ? `${API}/api/roster/${entry.id}/cutout?t=${entry.last_ts}` : photo)
  const clipUrl = $derived(entry.clip ? `${API}${entry.clip}` : '')
  const lastCamId = $derived(camByName(entry.cam)?.id)

  let trail = $derived(entry.trail ?? [])
  let geo = $derived(trail.length >= 2 && trail.every((t) => !!camByName(t.cam)?.coords))
  let nodes = $derived(trail.map((t, i) => {
    const cam = camByName(t.cam)
    let x: number, y: number
    if (geo && cam?.coords) { ;[x, y] = project(cam.coords[0], cam.coords[1]) }
    else {
      const n = trail.length
      x = n <= 1 ? VW * 0.5 : VW * (0.12 + 0.76 * (i / (n - 1)))
      y = VH * 0.5 + (i % 2 === 0 ? -1 : 1) * VH * 0.18
    }
    return { ...t, id: cam?.id, name: t.cam, x, y, cur: t.cam === entry.cam }
  }))
  let pathD = $derived(nodes.map((nd, i) => `${i ? 'L' : 'M'}${nd.x.toFixed(1)} ${nd.y.toFixed(1)}`).join(' '))
  let focusNode = $derived(nodes[focusIdx] ?? nodes[nodes.length - 1] ?? null)
  let designation = $derived(a.alias || entry.id)

  // BOLO / watchlist — optimistic toggle, reconciled by the 3s roster refresh
  let watchLocal = $state<boolean | null>(null)
  let watched = $derived(watchLocal ?? !!entry.watched)
  // journey supercut — the subject's per-camera clips stitched into one video
  let superUrl = $state<string | null>(null)
  let superLoading = $state(false)
  // cross-camera ReID search — enroll the subject and hand off to the animated LOCATE screen
  let finding = $state(false)
  $effect(() => { void entry.id; watchLocal = null; superUrl = null })  // reset per subject
  async function findAcross() {
    if (finding || !photo) return
    finding = true; sfx('sonar')
    try {
      const blob = await (await fetch(photo)).blob()
      const img = await new Promise<string>((ok, no) => {
        const fr = new FileReader(); fr.onload = () => ok(fr.result as string); fr.onerror = no; fr.readAsDataURL(blob)
      })
      enroll({ kind: entry.cls === 'vehicle' ? 'vehicle' : 'person', name: a.alias || entry.id,
        image: img, threat: 'watch', color: entry.attrs?.upper_color, cam: entry.cam ?? undefined })
      flashBanner('LOCATING ACROSS FEEDS_', false, 1400)
      watchlistOpen.set(true)
      close()
    } catch { flashBanner('CANNOT LOCATE', false, 1200); finding = false }
  }
  async function playJourney() {
    if (superLoading) return
    superLoading = true; sfx('sonar')
    try { const r = await api.supercut(entry.id); superUrl = `${API}${r.url}` }
    catch { flashBanner('NO JOURNEY CLIPS YET', false, 1400) }
    superLoading = false
  }
  async function toggleWatch() {
    const next = !watched
    watchLocal = next
    sfx(next ? 'sonar' : 'click')
    flashBanner(next ? `BOLO SET · ${entry.id}` : 'BOLO CLEARED', false, 1200)
    try { await api.watchRoster(entry.id, next) } catch { watchLocal = null }
  }

  const apprLine = $derived(
    [entry.attrs?.upper_color, entry.cls === 'person' ? entry.attrs?.height : undefined]
      .filter(Boolean).map((s) => trUpper(String(s))).join(' · '))
  const clock24 = (ms: number) => { const d = new Date(ms); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':') }
  const hhmm = (ms: number) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
  function ago(ms: number): string {
    const s = Math.max(0, Math.round((now - ms) / 1000))
    if (s < 5) return 'JUST NOW'
    if (s < 60) return `${s}S AGO`
    const m = Math.round(s / 60)
    return m < 60 ? `${m}M AGO` : `${Math.round(m / 60)}H AGO`
  }
  function duration(ms: number): string {
    const m = Math.round(ms / 60000)
    if (m < 1) return '<1M'
    return m < 60 ? `${m}M` : `${Math.floor(m / 60)}H ${m % 60}M`
  }

  // animated count-up stats — the cinematic touch
  const obsN = tweened(0, { duration: 1000, easing: cubicOut })
  const camN = tweened(0, { duration: 1000, easing: cubicOut })

  // — pan / zoom for the movement trace —
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
    tx = VW / 2 - k * ((a0 + b0) / 2); ty = VH / 2 - k * ((c0 + d0) / 2)
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
    tx = drag.tx + (e.clientX - drag.x) * s; ty = drag.ty + (e.clientY - drag.y) * s
  }
  function onUp() { drag = null }
  function focus(i: number) {
    sfx('click', { volume: 0.3 }); focusIdx = i
    const nd = nodes[i]; if (!nd) return
    const nk = Math.max(k, 2.6); k = nk; tx = VW / 2 - nk * nd.x; ty = VH / 2 - nk * nd.y
  }
  function observe(name?: string | null) {
    const cam = camByName(name); if (!cam) return
    sfx('glitch'); triggerGlitch(220)
    activeCam.set(cam.id)
    if (!SIM) sendCommand(`connect:${cam.name}`)
    flashBanner(`OBSERVING ${cam.name}`, false, 1200)
    mode.set('pov')
  }
  function close() { if (closing) return; sfx('click'); closing = true; setTimeout(onclose, 360) }
  function onkey(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); close() } }
  onMount(() => {
    sfx('sonar'); window.addEventListener('keydown', onkey, true)
    obsN.set(entry.obs); camN.set(nodes.length)
  })
  onDestroy(() => window.removeEventListener('keydown', onkey, true))
  const inv = $derived(1 / k)
</script>

<div class="poi" class:closing role="dialog" aria-modal="true" aria-label={kind}>
  <div class="vign"></div>
  <div class="scan"></div>
  <button class="close caps" onclick={close}>CLOSE <span>✕</span></button>
  <div class="watermark caps">{entry.id}</div>

  <div class="stage">
    <!-- HERO -->
    <section class="hero">
      <div class="portrait" class:livef={live}>
        {#if entry.snapshot}<img src={heroSrc} alt="" />{:else}<div class="noimg caps">NO IMAGE ON FILE</div>{/if}
        <span class="ret tl"></span><span class="ret tr"></span><span class="ret bl"></span><span class="ret br"></span>
        <span class="reticle"></span>
        <span class="lock caps">LOCK</span>
        {#if watched}<span class="bolo caps">◉ BOLO · WATCHED</span>{/if}
        {#if entry.snapshot}<button class="cut caps" onclick={() => (cutout = !cutout)}>{cutout ? 'RESTORE BG' : 'ISOLATE'}</button>{/if}
      </div>

      <div class="ident">
        <div class="eyebrow caps"><span class="dotmark" class:on={live}></span>{kind}</div>
        <h1 class="name">{designation}</h1>
        <div class="sub caps">
          {#each [trUpper(entry.cls), entry.attrs?.subtype && trUpper(entry.attrs.subtype), entry.attrs?.make && `${trUpper(entry.attrs.make)} ~EST`, entry.attrs?.upper_color && trUpper(entry.attrs.upper_color)].filter(Boolean) as chip}
            <span class="tag">{chip}</span>
          {/each}
          {#if entry.plate}<span class="tag plate">▤ {entry.plate}</span>{/if}
        </div>
        <div class="statusline caps" class:live>
          <span class="pip"></span>{live ? `TRACKING · ${entry.cam ?? '—'}` : `LAST SEEN · ${entry.cam ?? '—'} · ${ago(entry.last_ts)}`}
        </div>
      </div>

      <div class="stats">
        <div class="stat"><span class="num">{Math.round($obsN)}</span><span class="lbl caps">SIGHTINGS</span></div>
        <div class="stat"><span class="num">{Math.round($camN)}</span><span class="lbl caps">CAMERAS</span></div>
        <div class="stat"><span class="num sm">{duration(entry.last_ts - entry.first_ts)}</span><span class="lbl caps">TRACKED</span></div>
        <div class="stat"><span class="num sm">{ago(entry.last_ts)}</span><span class="lbl caps">LAST SEEN</span></div>
      </div>

      <div class="chron">
        <div class="crow caps"><span class="k">FIRST CONTACT</span><span class="v">{entry.first_cam ?? '—'} · {clock24(entry.first_ts)}</span></div>
        {#if apprLine}<div class="crow caps"><span class="k">APPEARANCE</span><span class="v">{apprLine}</span></div>{/if}
      </div>

      <textarea class="notes" placeholder="INTELLIGENCE NOTE…" value={a.notes ?? ''}
        oninput={(ev) => annotate(entry.id, { notes: (ev.target as HTMLTextAreaElement).value })}></textarea>
      <div class="actions">
        <button class="watch caps" class:on={watched} onclick={toggleWatch}>{watched ? '◉ WATCHING' : '◎ WATCHLIST'}</button>
        {#if focusNode}<button class="observe caps" onclick={() => observe(focusNode.name)}><span class="ico"></span>OBSERVE</button>{/if}
      </div>
      <button class="find caps" onclick={findAcross}>{finding ? 'LOCATING_' : '⌖ FIND ACROSS CAMERAS'}</button>
    </section>

    <!-- LIVE + SIGHTING + MOVEMENT TRACE -->
    <section class="trace">
      <div class="livewall">
        <figure class="wcell">
          <figcaption class="wcap caps">
            <span class="wt">◈ {superUrl ? 'JOURNEY SUPERCUT' : 'SIGHTING'}</span>
            {#if superUrl}
              <button class="jbtn" onclick={() => (superUrl = null)}>× SIGHTING</button>
            {:else}
              <button class="jbtn" onclick={playJourney}>{superLoading ? 'BUILDING…' : '▶ PLAY JOURNEY'}</button>
            {/if}
          </figcaption>
          <div class="wbody">
            {#if superUrl}
              <!-- svelte-ignore a11y_media_has_caption -->
              <video src={superUrl} autoplay loop controls></video>
            {:else if clipUrl}
              <video src={clipUrl} autoplay loop muted playsinline></video>
            {:else if photo}
              <img src={photo} alt="" /><span class="scanline"></span>
            {:else}<div class="noimg caps">NO SIGHTING</div>{/if}
            <span class="brk tl"></span><span class="brk tr"></span><span class="brk bl"></span><span class="brk br"></span>
          </div>
        </figure>
        <figure class="wcell">
          <figcaption class="wcap caps"><span class="wt wlive" class:on={live}>{live ? '● LIVE FEED' : '○ FEED'}</span><span class="wsub">{entry.cam ?? '—'}</span></figcaption>
          <div class="wbody">
            {#if lastCamId}<LiveThumb id={lastCamId} fps={6} />{:else}<div class="noimg caps">NO FEED</div>{/if}
            {#if live}<span class="livetag caps"><span class="d"></span>TRACKING</span>{/if}
            <span class="brk tl"></span><span class="brk tr"></span><span class="brk bl"></span><span class="brk br"></span>
          </div>
        </figure>
      </div>

      <div class="mapwrap">
      <div class="thead caps"><span class="tt">MOVEMENT TRACE</span><span class="th-sub">{nodes.length} CAMERA{nodes.length > 1 ? 'S' : ''} · DRAG · ZOOM</span></div>
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <svg bind:this={svg} class="net" viewBox={`0 0 ${VW} ${VH}`} onwheel={onWheel}
        onpointerdown={onDown} onpointermove={onMove} onpointerup={onUp}>
        <defs><radialGradient id="pg" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="rgba(56,208,227,0.2)" /><stop offset="100%" stop-color="transparent" /></radialGradient></defs>
        <g bind:this={grp} transform={`translate(${tx} ${ty}) scale(${k})`}>
          {#each Array.from({ length: 13 }) as _, i}<line class="grat" x1={i * (VW / 12)} y1="0" x2={i * (VW / 12)} y2={VH} />{/each}
          {#each Array.from({ length: 9 }) as _, i}<line class="grat" x1="0" y1={i * (VH / 8)} x2={VW} y2={i * (VH / 8)} />{/each}
          {#if nodes.length >= 2}
            <path class="route base" d={pathD} /><path class="route draw" d={pathD} />
            <circle class="rpkt" r="3.6"><animateMotion dur={`${Math.max(3, nodes.length * 1.4)}s`} repeatCount="indefinite" path={pathD} /></circle>
          {/if}
          {#each nodes as nd, i (nd.name + i)}
            {@const big = focusIdx === i || (focusIdx === -1 && i === nodes.length - 1)}
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <g class="node" class:cur={nd.cur} class:big style={`animation-delay:${400 + i * 100}ms`}
              transform={`translate(${nd.x} ${nd.y}) scale(${inv})`} role="button" tabindex="0"
              onpointerdown={(e) => { e.stopPropagation(); focus(i) }}
              onkeydown={(e) => { if (e.key === 'Enter') observe(nd.name) }} ondblclick={() => observe(nd.name)}>
              <circle class="glow" r="30" fill="url(#pg)" />
              <circle class="halo" r="14" /><circle class="ring" r="8.5" />
              {#if nd.cur && live}<circle class="pinglive" r="8.5" />{/if}
              <text class="seq" x="0" y="3">{i + 1}</text>
              <foreignObject x="15" y={big ? -54 : -40} width={big ? 196 : 128} height={big ? 152 : 104} style="overflow: visible">
                <div class="ncard" class:big class:curcard={nd.cur}>
                  <div class="nthumb">
                    {#if nd.id}<LiveThumb id={nd.id} fps={big ? 4 : 2} />{:else}<div class="noimg caps">NO FEED</div>{/if}
                    {#if nd.cur && live}<span class="ndot"></span>{/if}
                  </div>
                  <div class="nmeta caps"><span class="nn">{nd.name}</span><span class="ns">{clock24(nd.first)} · {nd.count}×</span></div>
                </div>
              </foreignObject>
            </g>
          {/each}
        </g>
      </svg>

      <div class="trail caps">
        {#each nodes as nd, i (nd.name + i)}
          <button class="stop" class:on={i === focusIdx || (focusIdx === -1 && i === nodes.length - 1)} class:cur={nd.cur}
            onclick={() => focus(i)} ondblclick={() => observe(nd.name)}>
            <span class="sdot"></span>
            <span class="slab"><span class="sn">{nd.name}</span><span class="st">{hhmm(nd.first)} · {nd.count}×</span></span>
          </button>
          {#if i < nodes.length - 1}<span class="sconn"></span>{/if}
        {/each}
      </div>
      </div>
    </section>
  </div>
</div>

<style>
  .poi { position: fixed; inset: 0; z-index: var(--z-boot); color: var(--ink); overflow: hidden;
    background:
      radial-gradient(140% 90% at 22% 8%, rgba(56,208,227,0.08) 0%, transparent 42%),
      radial-gradient(120% 100% at 100% 100%, rgba(225,6,0,0.06) 0%, transparent 46%),
      #05080b;
    animation: boot 520ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  .poi.closing { animation: unboot 340ms cubic-bezier(0.4, 0, 1, 1) both; }
  @keyframes boot { from { opacity: 0; clip-path: inset(0 0 100% 0); } to { opacity: 1; clip-path: inset(0 0 0 0); } }
  @keyframes unboot { to { opacity: 0; transform: scale(1.015); filter: blur(3px); } }
  .vign { position: absolute; inset: 0; pointer-events: none; box-shadow: inset 0 0 220px rgba(0,0,0,0.8);
    background: repeating-linear-gradient(0deg, transparent 0 2px, rgba(0,0,0,0.14) 2px 3px); opacity: 0.5; }
  /* one-shot entrance scan sweep across the whole screen */
  .scan { position: absolute; left: 0; right: 0; height: 40vh; pointer-events: none; z-index: 3;
    background: linear-gradient(transparent, rgba(56,208,227,0.10), transparent); animation: sweep 900ms 120ms ease-out both; }
  @keyframes sweep { from { transform: translateY(-45vh); } to { transform: translateY(115vh); opacity: 0; } }

  .close { position: absolute; top: 22px; right: 26px; z-index: 6; display: flex; align-items: center; gap: 8px;
    background: none; border: 1px solid var(--hairline); color: var(--ink-dim); padding: 8px 14px; cursor: pointer;
    font-size: 10px; letter-spacing: 0.2em; }
  .close span { color: var(--ink-ghost); } .close:hover { border-color: var(--scarlet); color: var(--ink); }
  .close:hover span { color: var(--scarlet); }
  .watermark { position: absolute; right: -2vw; bottom: -6vh; z-index: 0; font-family: var(--font-display);
    font-size: 34vh; font-weight: 700; color: rgba(236,236,236,0.022); letter-spacing: -0.04em; pointer-events: none; user-select: none; }

  .stage { position: relative; z-index: 4; height: 100%; display: grid; grid-template-columns: 420px minmax(0, 1fr);
    gap: 26px; padding: 30px 34px; }

  /* ── HERO ── */
  .hero { display: flex; flex-direction: column; gap: 16px; min-height: 0; overflow: auto; padding-right: 4px; }
  .portrait { position: relative; aspect-ratio: 4/5; overflow: hidden; background: #080b0e;
    box-shadow: 0 30px 70px rgba(0,0,0,0.6); animation: portin 640ms 120ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes portin { from { opacity: 0; transform: scale(1.06); } }
  .portrait img { width: 100%; height: 100%; object-fit: cover; filter: grayscale(0.35) contrast(1.08) brightness(1.02); }
  .portrait::after { content: ''; position: absolute; left: 0; right: 0; height: 22%; pointer-events: none;
    background: linear-gradient(transparent, rgba(56,208,227,0.22), transparent); animation: pscan 1400ms 260ms ease-out both; }
  @keyframes pscan { from { transform: translateY(-25%); } to { transform: translateY(460%); opacity: 0; } }
  .portrait.livef { box-shadow: 0 30px 70px rgba(0,0,0,0.6), inset 0 0 0 1px rgba(56,208,227,0.5); }
  /* targeting reticle that locks in */
  .ret { position: absolute; width: 30px; height: 30px; border: 2px solid var(--ink); z-index: 2;
    animation: lockin 520ms cubic-bezier(0.2, 1.4, 0.3, 1) both; }
  .ret.tl { top: 12px; left: 12px; border-right: 0; border-bottom: 0; animation-delay: 340ms; }
  .ret.tr { top: 12px; right: 12px; border-left: 0; border-bottom: 0; animation-delay: 420ms; }
  .ret.bl { bottom: 12px; left: 12px; border-right: 0; border-top: 0; animation-delay: 500ms; }
  .ret.br { bottom: 12px; right: 12px; border-left: 0; border-top: 0; animation-delay: 580ms; }
  @keyframes lockin { from { opacity: 0; transform: scale(2.2); } }
  .reticle { position: absolute; inset: 30% 34%; border: 1px solid rgba(56,208,227,0.5); border-radius: 50%; z-index: 2;
    opacity: 0; animation: reticleon 800ms 620ms ease both; }
  .reticle::before, .reticle::after { content: ''; position: absolute; background: rgba(56,208,227,0.5); }
  .reticle::before { left: 50%; top: -8px; bottom: -8px; width: 1px; } .reticle::after { top: 50%; left: -8px; right: -8px; height: 1px; }
  @keyframes reticleon { from { opacity: 0; transform: scale(1.5) rotate(-40deg); } to { opacity: 0.8; transform: scale(1) rotate(0); } }
  .lock { position: absolute; top: 16px; right: 46px; z-index: 3; font-size: 9px; letter-spacing: 0.24em; color: var(--cyan);
    opacity: 0; animation: fadein 400ms 720ms both; }
  .cut { position: absolute; bottom: 12px; left: 12px; z-index: 3; padding: 4px 10px; border: 1px solid var(--hairline);
    background: rgba(5,8,11,0.7); color: var(--ink-dim); font-size: 8px; letter-spacing: 0.16em; cursor: pointer; }
  .cut:hover { border-color: var(--cyan); color: var(--cyan); }
  .noimg { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: 9px; letter-spacing: 0.16em; }

  .ident { animation: fadeup 560ms 200ms both; }
  .eyebrow { display: flex; align-items: center; gap: 8px; font-size: 10px; letter-spacing: 0.34em; color: var(--cyan); }
  .dotmark { width: 6px; height: 6px; border-radius: 50%; background: var(--ink-ghost); }
  .dotmark.on { background: var(--cyan); box-shadow: 0 0 8px var(--cyan); animation: pulse 1.4s ease-in-out infinite; }
  .name { font-family: var(--font-display); font-weight: 700; font-size: clamp(30px, 4.2vw, 58px); line-height: 0.98;
    letter-spacing: -0.01em; margin: 8px 0 12px; text-shadow: 0 2px 30px rgba(56,208,227,0.14);
    animation: revealname 700ms 300ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes revealname { from { opacity: 0; transform: translateY(12px); clip-path: inset(0 100% 0 0); } to { opacity: 1; transform: none; clip-path: inset(0 0 0 0); } }
  .sub { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
  .tag { padding: 4px 9px; border: 1px solid var(--hairline); font-size: 9px; letter-spacing: 0.1em; color: var(--ink-dim); }
  .tag.plate { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 40%, transparent); font-weight: 700; }
  .statusline { display: flex; align-items: center; gap: 8px; font-size: 10px; letter-spacing: 0.16em; color: var(--ink-dim); }
  .statusline .pip { width: 7px; height: 7px; background: var(--ink-ghost); }
  .statusline.live { color: var(--cyan); } .statusline.live .pip { background: var(--cyan); box-shadow: 0 0 8px var(--cyan); animation: pulse 1.2s ease-in-out infinite; }

  .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--hairline); border: 1px solid var(--hairline);
    animation: fadeup 560ms 380ms both; }
  .stat { background: #070a0d; padding: 12px 14px; display: flex; flex-direction: column; gap: 2px; }
  .num { font-family: var(--font-display); font-weight: 700; font-size: 30px; line-height: 1; color: var(--ink); font-variant-numeric: tabular-nums; }
  .num.sm { font-size: 19px; }
  .lbl { font-size: 8px; letter-spacing: 0.22em; color: var(--ink-ghost); }

  .chron { display: flex; flex-direction: column; gap: 5px; animation: fadeup 560ms 460ms both; }
  .crow { display: flex; justify-content: space-between; gap: 10px; font-size: 9px; padding-bottom: 5px; border-bottom: 1px solid var(--hairline); }
  .crow .k { color: var(--ink-ghost); letter-spacing: 0.14em; } .crow .v { color: var(--ink); text-align: right; }

  .notes { min-height: 52px; resize: vertical; background: rgba(7,10,13,0.7); border: 1px solid var(--hairline); color: var(--ink);
    font-family: var(--font-mono); font-size: 10px; letter-spacing: 0.04em; padding: 8px; animation: fadeup 560ms 520ms both; }
  .notes:focus { outline: none; border-color: var(--cyan); }
  .notes::placeholder { color: var(--ink-ghost); letter-spacing: 0.16em; }
  .actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; animation: fadeup 560ms 580ms both; }
  .observe, .watch { display: flex; align-items: center; justify-content: center; gap: 9px; padding: 12px; cursor: pointer;
    font-family: var(--font-display); font-size: 11px; letter-spacing: 0.16em; }
  .observe { background: linear-gradient(90deg, rgba(225,6,0,0.14), transparent); border: 1px solid var(--scarlet); color: var(--ink); }
  .observe:hover { background: var(--scarlet); color: #fff; }
  .observe .ico { width: 0; height: 0; border-left: 8px solid currentColor; border-top: 5px solid transparent; border-bottom: 5px solid transparent; }
  .watch { background: none; border: 1px solid var(--hairline); color: var(--ink-dim); }
  .watch:hover { border-color: var(--cyan); color: var(--cyan); }
  .watch.on { border-color: var(--cyan); color: #04070a; background: var(--cyan); }
  .find { padding: 10px; cursor: pointer; background: none; border: 1px solid var(--cyan); color: var(--cyan);
    font-family: var(--font-display); font-size: 11px; letter-spacing: 0.16em; animation: fadeup 560ms 620ms both; }
  .find:hover { background: var(--cyan); color: #04070a; }
  .bolo { position: absolute; top: 46px; left: 12px; z-index: 3; padding: 3px 9px; background: var(--scarlet); color: #fff;
    font-size: 8px; letter-spacing: 0.18em; box-shadow: 0 0 12px var(--scarlet-glow); animation: fadein 400ms 700ms both; }

  /* ── MOVEMENT TRACE ── */
  .trace { position: relative; display: flex; flex-direction: column; min-height: 0;
    border: 1px solid var(--hairline); background: rgba(4,7,10,0.4); box-shadow: 0 30px 80px rgba(0,0,0,0.5);
    animation: traceIn 640ms 260ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes traceIn { from { opacity: 0; transform: translateX(20px); } }

  /* big live wall: sighting clip + last-camera live feed */
  .livewall { flex: 0 0 54%; display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--hairline);
    border-bottom: 1px solid var(--hairline); min-height: 0; }
  .wcell { display: flex; flex-direction: column; min-height: 0; background: #05080b;
    animation: rise 520ms both cubic-bezier(0.16, 1, 0.3, 1); }
  .wcell:nth-child(2) { animation-delay: 130ms; }
  .wcap { flex: 0 0 auto; display: flex; justify-content: space-between; align-items: center; padding: 9px 12px;
    border-bottom: 1px solid var(--hairline); font-size: 9px; letter-spacing: 0.16em; background: rgba(4,7,10,0.6); }
  .wt { color: var(--ink-dim); } .wsub { color: var(--ink-ghost); font-size: 8px; }
  .jbtn { background: none; border: 1px solid var(--hairline); color: var(--cyan); font-size: 8px; letter-spacing: 0.14em;
    padding: 3px 8px; cursor: pointer; font-family: var(--font-mono); }
  .jbtn:hover { border-color: var(--cyan); background: rgba(56,208,227,0.1); }
  .wlive { color: var(--ink-ghost); } .wlive.on { color: var(--cyan); animation: pulse 1.6s ease-in-out infinite; }
  .wbody { position: relative; flex: 1; min-height: 0; overflow: hidden; background: #04060a; }
  .wbody video, .wbody img, .wbody :global(.img) { width: 100%; height: 100%; object-fit: cover; display: block; }
  .wbody img { filter: saturate(0.72) contrast(1.06); }
  .scanline { position: absolute; left: 0; right: 0; height: 32%; pointer-events: none;
    background: linear-gradient(transparent, rgba(56,208,227,0.16), transparent); animation: wscan 3.2s ease-in-out infinite; }
  @keyframes wscan { 0% { transform: translateY(-32%); } 100% { transform: translateY(330%); } }
  .brk { position: absolute; width: 13px; height: 13px; border: 2px solid rgba(236,236,236,0.55); z-index: 2; pointer-events: none; }
  .brk.tl { top: 7px; left: 7px; border-right: 0; border-bottom: 0; } .brk.tr { top: 7px; right: 7px; border-left: 0; border-bottom: 0; }
  .brk.bl { bottom: 7px; left: 7px; border-right: 0; border-top: 0; } .brk.br { bottom: 7px; right: 7px; border-left: 0; border-top: 0; }
  .livetag { position: absolute; top: 9px; left: 9px; z-index: 3; display: flex; align-items: center; gap: 6px;
    padding: 3px 9px; background: rgba(5,8,11,0.72); color: var(--cyan); font-size: 8px; letter-spacing: 0.16em; }
  .livetag .d { width: 6px; height: 6px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 6px var(--cyan); animation: pulse 1.2s ease-in-out infinite; }

  .mapwrap { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .thead { flex: 0 0 auto; display: flex; align-items: baseline; gap: 14px; padding: 12px 16px; border-bottom: 1px solid var(--hairline); }
  .tt { font-family: var(--font-display); font-size: 16px; letter-spacing: 0.16em; color: var(--ink); }
  .th-sub { font-size: 8px; letter-spacing: 0.16em; color: var(--ink-ghost); }
  .net { flex: 1; min-height: 0; width: 100%; touch-action: none; cursor: grab; }
  .net:active { cursor: grabbing; }
  .grat { stroke: var(--hairline); stroke-width: 0.4; vector-effect: non-scaling-stroke; opacity: 0.5; }
  .route { fill: none; vector-effect: non-scaling-stroke; }
  .route.base { stroke: rgba(56,208,227,0.14); stroke-width: 3; }
  .route.draw { stroke: var(--cyan); stroke-width: 1.6; filter: drop-shadow(0 0 6px var(--cyan)); stroke-dasharray: 1800; stroke-dashoffset: 1800; animation: draw 1400ms 400ms ease forwards; }
  @keyframes draw { to { stroke-dashoffset: 0; } }
  .rpkt { fill: #fff; filter: drop-shadow(0 0 6px var(--cyan)); }
  .node { cursor: pointer; animation: pop 460ms both cubic-bezier(0.16, 1, 0.3, 1); }
  .node:focus { outline: none; }
  @keyframes pop { from { opacity: 0; } }
  .node .halo { fill: rgba(56,208,227,0.08); }
  .node .ring { fill: rgba(56,208,227,0.15); stroke: var(--cyan); stroke-width: 1.5; filter: drop-shadow(0 0 6px var(--cyan)); }
  .node.cur .ring { stroke: #fff; fill: rgba(255,255,255,0.2); filter: drop-shadow(0 0 8px #fff); }
  .node .seq { fill: var(--ink); font-size: 9px; text-anchor: middle; font-family: var(--font-mono); font-weight: 700; }
  .node .glow { opacity: 0; transition: opacity 160ms; } .node.big .glow { opacity: 1; }
  .pinglive { fill: none; stroke: #fff; stroke-width: 1.4; animation: ping 1.8s ease-out infinite; }
  @keyframes ping { 0% { r: 8.5; opacity: 0.9; } 100% { r: 24; opacity: 0; } }
  .ncard { width: 118px; background: rgba(7,10,13,0.94); border: 1px solid var(--cyan); transition: width 160ms var(--ease); }
  .ncard.big { width: 184px; box-shadow: 0 0 20px rgba(56,208,227,0.35); }
  .ncard.curcard { border-color: #fff; box-shadow: 0 0 18px rgba(255,255,255,0.3); }
  .nthumb { position: relative; aspect-ratio: 16/9; overflow: hidden; background: #05070a; }
  .nthumb :global(.img) { width: 100%; height: 100%; object-fit: cover; }
  .ndot { position: absolute; top: 5px; right: 5px; width: 7px; height: 7px; border-radius: 50%; background: #fff; box-shadow: 0 0 6px #fff; animation: pulse 1.2s ease-in-out infinite; }
  .nmeta { display: flex; flex-direction: column; gap: 1px; padding: 5px 7px; }
  .nn { font-family: var(--font-display); font-size: 10px; letter-spacing: 0.08em; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ns { font-size: 8px; color: var(--ink-ghost); }

  .trail { flex: 0 0 auto; display: flex; align-items: center; gap: 2px; padding: 10px 14px; overflow-x: auto; border-top: 1px solid var(--hairline); background: rgba(4,7,10,0.6); }
  .stop { display: flex; align-items: center; gap: 8px; padding: 5px 10px; background: none; border: 1px solid transparent; cursor: pointer; flex: 0 0 auto; }
  .stop .sdot { width: 8px; height: 8px; border-radius: 50%; border: 1px solid var(--ink-dim); background: #0a0d10; flex: 0 0 auto; }
  .stop.cur .sdot { background: #fff; border-color: #fff; box-shadow: 0 0 6px #fff; }
  .stop.on { border-color: var(--cyan); background: rgba(56,208,227,0.08); }
  .slab { display: flex; flex-direction: column; line-height: 1.2; }
  .sn { font-family: var(--font-display); font-size: 10px; color: var(--ink); letter-spacing: 0.06em; white-space: nowrap; }
  .st { font-size: 7px; color: var(--ink-ghost); }
  .sconn { width: 22px; height: 1px; background: var(--hairline); flex: 0 0 auto; }

  @keyframes fadein { from { opacity: 0; } }
  @keyframes fadeup { from { opacity: 0; transform: translateY(12px); } }
  @keyframes pulse { 50% { opacity: 0.45; } }

  @media (max-width: 1080px) {
    .stage { grid-template-columns: 1fr; grid-auto-rows: min-content; overflow: auto; }
    .hero { overflow: visible; }
    .trace { min-height: 460px; }
    .watermark { display: none; }
  }
</style>
