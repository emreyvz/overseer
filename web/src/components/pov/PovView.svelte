<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { activeCam, cameras, conn, povZoom, flashBanner, enrollOpen, spatialOpen, modules } from '../../lib/stores'
  import { SIM } from '../../lib/sim'
  import { LEX } from '../../lib/lexicon'
  import { api } from '../../lib/api'
  import { sfx } from '../../lib/audio'
  import type { Detection } from '../../lib/types'
  import { initFeedGL, type FeedGL } from '../../lib/hud/feedgl'
  import ConformityGauge from './ConformityGauge.svelte'
  import DetectionLayer from './DetectionLayer.svelte'
  import DreamRibbon from './DreamRibbon.svelte'
  import DreamVeil from './DreamVeil.svelte'
  import FogOverlay from './FogOverlay.svelte'
  import GhostLayer from './GhostLayer.svelte'
  import GrainField from './GrainField.svelte'
  import SocialXray from './SocialXray.svelte'
  import TacticalMap from './TacticalMap.svelte'
  import MatchHighlight from './MatchHighlight.svelte'
  import PtzPad from './PtzPad.svelte'
  import LiveThumb from '../LiveThumb.svelte'
  import NoSignal from '../NoSignal.svelte'

  const STREAM_BASE = (import.meta.env.VITE_STREAM_BASE as string | undefined) ?? 'http://127.0.0.1:8787/stream'

  let cam = $derived($cameras.find((c) => c.id === $activeCam))
  let live = $derived($conn === 'online' && !SIM && !!$activeCam)
  // Connecting stays visible until fully online (or the connection is dropped).
  let connecting = $derived(($conn === 'connecting' || $conn === 'reconnecting') && !SIM && !!$activeCam)
  let failed = $state(false)

  // experiential overlays (left MODULES rail): predictive ghosts on the feed + the top-down
  // tactical god-view. Both feed off the shared foresight engine; shown only over a real feed.
  let ghostsOn = $derived(!!$modules.find((m) => m.key === 'ghosts')?.on)
  let socialOn = $derived(!!$modules.find((m) => m.key === 'social')?.on)
  let tacticalOn = $derived(!!$modules.find((m) => m.key === 'tactical')?.on)
  // PERCEPTION overlays: they model the place, so they ride inside the zoom wrapper with the
  // detections (the fog belongs to the ground, not to the screen).
  let unseenOn = $derived(!!$modules.find((m) => m.key === 'unseen')?.on)
  let grainOn = $derived(!!$modules.find((m) => m.key === 'grain')?.on)
  let dreamOn = $derived(!!$modules.find((m) => m.key === 'dream')?.on)
  let showOverlays = $derived((live && !failed) || SIM)

  // 3D fly-between: the old feed zooms out and banks away in perspective while the
  // new feed flies in from depth and settles to fullscreen — both visible, angled,
  // mid-transition. Reads as pulling the camera off one screen and onto another.
  let switching = $state(false)
  let panOld = $state<{ id: string; name: string } | null>(null)
  let panNew = $state<{ id: string; name: string } | null>(null)
  let prevCam: string | null = null
  let swTimer: ReturnType<typeof setTimeout> | undefined
  const nameOf = (id: string | null) => $cameras.find((c) => c.id === id)?.name ?? 'CAM —'
  $effect(() => {
    const cur = $activeCam
    if (prevCam !== null && cur !== prevCam && cur) {
      failed = false
      panOld = { id: prevCam, name: nameOf(prevCam) }
      panNew = { id: cur, name: nameOf(cur) }
      switching = true
      povZoom.set({ zoom: 1, x: 0, y: 0 }) // reset digital zoom for the new camera
      clearTimeout(swTimer)
      swTimer = setTimeout(() => { switching = false; panOld = null; panNew = null }, 980)
    } else if (prevCam === null) {
      failed = false
    }
    prevCam = cur
  })

  // "Look closer": click a spot the live pass missed → enhanced re-analysis there.
  let inspectOn = $state(false)
  let scanning = $state(false)
  let scanPt = $state({ x: 0.5, y: 0.5 })
  let hits = $state<Detection[]>([])
  let hitTimer: ReturnType<typeof setTimeout> | undefined
  async function lookCloser(e: MouseEvent) {
    if (!live || !$activeCam) return
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height
    scanPt = { x, y }; scanning = true; hits = []; sfx('sonar')
    try {
      const res = await api.inspect($activeCam, x, y)
      hits = res.detections ?? []
      flashBanner(hits.length ? `${hits.length} TARGET${hits.length > 1 ? 'S' : ''} FOUND — CLICK TO ENROLL` : 'NO TARGET FOUND', hits.length === 0, 1400)
      clearTimeout(hitTimer); hitTimer = setTimeout(() => (hits = []), 6000)
    } catch { flashBanner('INSPECT FAILED', true, 1200) }
    scanning = false
  }

  let cv = $state<HTMLCanvasElement>()
  let img = $state<HTMLImageElement>()
  let gl: FeedGL | null = null
  let glOk = $state(false)

  onMount(() => {
    if (cv) { gl = initFeedGL(cv); glOk = !!gl }
  })
  onDestroy(() => gl?.destroy())

  // Feed the GL renderer the MJPEG <img> when live, else procedural (null).
  $effect(() => {
    if (!gl) return
    gl.setSource(live && !failed && img ? img : null)
  })

  let time = $state('')
  $effect(() => {
    const id = setInterval(() => {
      const d = new Date()
      time = [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')
    }, 1000)
    return () => clearInterval(id)
  })
</script>

<div class="pov">
  <!-- digital zoom/pan wrapper: feed + detection overlay transform together (stay aligned) -->
  <div class="zoomwrap" style={`transform: scale(${$povZoom.zoom}) translate(${$povZoom.x}%, ${$povZoom.y}%)`}>
    <canvas bind:this={cv} class="gl" class:show={glOk}></canvas>

    <!-- MJPEG source (hidden; used as GL texture, or shown directly if no WebGL2) -->
    {#if live && !failed}
      <img bind:this={img} class="feed" class:hidden={glOk} src={`${STREAM_BASE}/${$activeCam}`}
        alt="" crossorigin="anonymous" onerror={() => (failed = true)} />
    {/if}

    <div class="grid-overlay"></div>
    {#if grainOn && showOverlays}<GrainField />{/if}
    {#if unseenOn && showOverlays}<FogOverlay />{/if}
    <DetectionLayer />
    {#if grainOn && showOverlays}<ConformityGauge />{/if}
    {#if dreamOn && showOverlays}<DreamVeil />{/if}
    {#if ghostsOn && showOverlays}<GhostLayer />{/if}
    {#if socialOn && showOverlays}<SocialXray />{/if}
    <MatchHighlight />
  </div>

  {#if dreamOn && showOverlays}<DreamRibbon />{/if}
  {#if tacticalOn && showOverlays}<TacticalMap />{/if}

  {#if connecting}
    <!-- keep the camera's thumbnail on screen while connecting so there is no black flash -->
    {#if $activeCam}<div class="cthumb"><LiveThumb id={$activeCam} fps={4} /></div>{/if}
    <div class="connecting">
      <div class="cspin"></div>
      <div class="ctext caps">{$conn === 'reconnecting' ? 'RECONNECTING' : 'CONNECTING'} → {cam?.name ?? 'CAM —'}</div>
      <div class="csub caps">ESTABLISHING FEED<span class="cdot"></span></div>
    </div>
  {:else if !SIM && (!live || failed)}
    <NoSignal />
  {/if}

  {#if live && !failed}<PtzPad />{/if}

  <!-- look-closer: catch layer, scan reticle, and enrollable hits -->
  {#if inspectOn && live && !failed}
    <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
    <div class="inspectcatch" onclick={lookCloser} role="presentation"></div>
  {/if}
  {#if scanning}<div class="scanret" style={`left:${scanPt.x * 100}%;top:${scanPt.y * 100}%`}></div>{/if}
  {#each hits as h (h.id)}
    <button class="ihit" style={`left:${h.bbox[0] * 100}%;top:${h.bbox[1] * 100}%;width:${h.bbox[2] * 100}%;height:${h.bbox[3] * 100}%`}
      onclick={() => enrollOpen.set(h)} title="enroll this target">
      <span class="ihlabel caps">{h.klass} {Math.round(h.conf * 100)}% · ⊕</span>
    </button>
  {/each}
  {#if live && !failed}
    <button class="inspectbtn caps" class:on={inspectOn} onclick={() => { inspectOn = !inspectOn; hits = []; sfx('click', { volume: 0.3 }) }}>
      ⌖ SCAN POINT{inspectOn ? ' · ON' : ''}
    </button>
    <button class="spatialbtn caps" onclick={() => { if ($activeCam) { spatialOpen.set($activeCam); sfx('click', { volume: 0.3 }) } }}>
      ⛶ 3D SPATIAL
    </button>
  {/if}

  {#if switching && panOld && panNew}
    <div class="fly">
      <div class="scene">
        <div class="face old"><div class="fscreen"><LiveThumb id={panOld.id} fps={4} /></div><div class="flabel caps">{panOld.name}</div></div>
        <div class="face new"><div class="fscreen"><LiveThumb id={panNew.id} fps={4} /></div><div class="flabel caps">{panNew.name}</div></div>
      </div>
      <div class="flyhud caps">▸ VECTORING → {panNew.name}</div>
    </div>
  {/if}

  <div class="loc caps">{cam?.name ?? 'KAM —'}</div>
  <div class="time caps">{time}</div>
  <div class="anon caps">{LEX.anon}</div>
</div>

<style>
  .pov { position: absolute; inset: 0; z-index: var(--z-video); overflow: hidden; background: #000; }
  .zoomwrap { position: absolute; inset: 0; transform-origin: center center; transition: transform 200ms var(--ease); will-change: transform; }

  /* 3D fly-between: old feed banks away, new feed flies in from depth */
  .fly { position: absolute; inset: 0; z-index: var(--z-overlay); overflow: hidden; background: #000; perspective: 1400px; }
  .scene { position: absolute; inset: 0; transform-style: preserve-3d; }
  .face { position: absolute; inset: 0; transform-style: preserve-3d; backface-visibility: hidden; will-change: transform, opacity; }
  .fscreen { position: absolute; inset: 0; overflow: hidden; background: #05070a;
    border: 2px solid var(--ink); box-shadow: 0 0 60px rgba(0,0,0,0.85), inset 0 0 70px rgba(0,0,0,0.55); }
  .flabel { position: absolute; left: 50%; bottom: 12%; transform: translateX(-50%); font-size: var(--fs-label);
    letter-spacing: var(--tracking); color: var(--ink); text-shadow: 0 0 6px #000; }
  /* pure depth dolly (no left-right rotation): old zooms out into depth on the
     left, new zooms in from depth on the right; both face the viewer flat */
  .face.old { animation: flyout 980ms cubic-bezier(0.55, 0.05, 0.25, 1) forwards; }
  .face.new { opacity: 0; animation: flyin 980ms cubic-bezier(0.55, 0.05, 0.25, 1) forwards; }
  @keyframes flyout {
    0% { transform: translateZ(0) translateX(0) scale(1); opacity: 1; }
    44% { transform: translateZ(-300px) translateX(-27%) scale(0.6); opacity: 1; }
    100% { transform: translateZ(-620px) translateX(-42%) scale(0.4); opacity: 0; }
  }
  @keyframes flyin {
    0% { transform: translateZ(-620px) translateX(42%) scale(0.4); opacity: 0; }
    44% { transform: translateZ(-300px) translateX(27%) scale(0.6); opacity: 1; }
    100% { transform: translateZ(0) translateX(0) scale(1); opacity: 1; }
  }
  .flyhud { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-family: var(--font-display); font-weight: 700;
    font-size: 18px; letter-spacing: var(--tracking-wide); color: var(--scarlet); text-shadow: 0 0 14px var(--scarlet-glow);
    animation: flyhud 980ms var(--ease); pointer-events: none; }
  @keyframes flyhud { 0%, 100% { opacity: 0; } 40%, 60% { opacity: 1; } }

  /* connecting overlay — persists until the feed is fully online */
  .cthumb { position: absolute; inset: 0; z-index: 1; }
  .connecting { position: absolute; inset: 0; z-index: var(--z-overlay); display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 14px;
    background: radial-gradient(ellipse at center, rgba(0,0,0,0.12), rgba(0,0,0,0.42)); }
  .cspin { width: 46px; height: 46px; border: 2px solid var(--hairline); border-top-color: var(--scarlet); border-radius: 50%; animation: cspin 900ms linear infinite; }
  @keyframes cspin { to { transform: rotate(360deg); } }
  .ctext { font-size: var(--fs-title); letter-spacing: var(--tracking-wide); color: var(--ink); }
  .csub { font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink-dim); }
  .cdot::after { content: '…'; animation: blink 1.1s steps(3) infinite; }
  @keyframes blink { 50% { opacity: 0.25; } }
  .gl { position: absolute; inset: 0; width: 100%; height: 100%; display: none; }
  .gl.show { display: block; }
  .feed { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; filter: saturate(0.55) contrast(1.05) brightness(0.9); }
  .feed.hidden { opacity: 0; pointer-events: none; }

  /* look-closer */
  .inspectcatch { position: absolute; inset: 0; z-index: 7; cursor: crosshair; }
  .scanret { position: absolute; width: 60px; height: 60px; transform: translate(-50%, -50%); z-index: 8; pointer-events: none;
    border: 1px solid var(--cyan); border-radius: 50%; box-shadow: 0 0 18px rgba(56,208,227,0.6); animation: scanret 700ms ease-out infinite; }
  @keyframes scanret { 0% { transform: translate(-50%, -50%) scale(0.3); opacity: 1; } 100% { transform: translate(-50%, -50%) scale(1.4); opacity: 0; } }
  .ihit { position: absolute; z-index: 8; border: 1.5px solid var(--cyan); background: rgba(56,208,227,0.08); cursor: crosshair; box-shadow: 0 0 14px rgba(56,208,227,0.4); animation: ihit 240ms var(--ease); }
  @keyframes ihit { from { opacity: 0; transform: scale(1.1); } }
  .ihit:hover { background: rgba(56,208,227,0.18); }
  .ihlabel { position: absolute; top: -15px; left: 0; font-size: var(--fs-micro); color: var(--cyan); text-shadow: 0 0 4px #000; white-space: nowrap; }
  .inspectbtn { position: absolute; left: 62px; bottom: 52px; z-index: var(--z-overlay); padding: 4px 10px; border: 1px solid var(--ink-dim);
    background: rgba(0,0,0,0.5); color: var(--ink-dim); font-size: var(--fs-micro); letter-spacing: var(--tracking); cursor: pointer; }
  .inspectbtn:hover { border-color: var(--cyan); color: var(--cyan); }
  /* stacked directly above LOOK CLOSER so it never overlaps it regardless of that label's width */
  .spatialbtn { position: absolute; left: 62px; bottom: 82px; z-index: var(--z-overlay); padding: 4px 10px; border: 1px solid var(--ink-dim);
    background: rgba(0,0,0,0.5); color: var(--ink-dim); font-size: var(--fs-micro); letter-spacing: var(--tracking); cursor: pointer; }
  .spatialbtn:hover { border-color: var(--cyan); color: var(--cyan); }
  .inspectbtn.on { border-color: var(--cyan); color: var(--cyan); background: rgba(56,208,227,0.12); }

  .loc { position: absolute; left: 62px; bottom: 26px; z-index: var(--z-overlay); font-size: var(--fs-label); color: var(--ink); }
  .time { position: absolute; right: 62px; bottom: 26px; z-index: var(--z-overlay); font-size: var(--fs-label); color: var(--ink); }
  .anon { position: absolute; left: 50%; bottom: 26px; transform: translateX(-50%); z-index: var(--z-overlay);
    font-size: var(--fs-micro); letter-spacing: var(--tracking); color: var(--ink-ghost); opacity: 0.7; }
</style>
