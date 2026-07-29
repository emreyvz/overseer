<script lang="ts">
  // Spatial 3D scene view (Feature 4). Lifts the camera's flat frame into a navigable point
  // cloud using monocular depth (Depth Anything V2, run backend-side). We back-project the
  // depth grid through a pinhole model, colour each point from the RGB frame, and drop a marker
  // at every detected person/vehicle so the operator can *think in 3D* about the scene —
  // distances, layering, who stands where. Drag to orbit, scroll to zoom.
  import * as THREE from 'three'
  import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
  import { onDestroy, onMount } from 'svelte'
  import { api } from '../../lib/api'
  import { sfx } from '../../lib/audio'

  let { cam, onclose }: { cam: string; onclose: () => void } = $props()

  let host = $state<HTMLDivElement | null>(null)
  let loading = $state(true)
  let unavailable = $state(false)
  let reason = $state('')
  let camName = $state('')

  const REASON_TEXT: Record<string, string> = {
    no_frame: "No live frame on this camera yet. Open it in the live view, then try again.",
    depth_unavailable: "Depth model isn't available. Install dependencies with  uv sync  and restart the backend.",
    no_source: 'That camera no longer exists.',
    disabled: 'Spatial view is disabled in the configuration (spatial.enabled).',
    backend_down: 'The backend is not reachable.',
  }
  let entityCount = $state(0)
  let auto = $state(false)

  // Back-projection / depth-to-Z tuning (settled by visual iteration across cameras).
  const ZNEAR = 1.0, ZFAR = 9.0, GAMMA = 1.6
  const EDGE = 0.055          // cull flying pixels at strong depth discontinuities
  const SKYCULL = 0.03        // drop the most-distant (sky / flat far background) points
  const CLS_COLOR: Record<string, string> = {
    person: '#35e0ff', vehicle: '#ffb038', animal: '#6be675', object: '#c9d4dc', weapon: '#ff3b3b',
  }

  let renderer: THREE.WebGLRenderer | null = null
  let scene: THREE.Scene | null = null
  let camera: THREE.PerspectiveCamera | null = null
  let controls: OrbitControls | null = null
  let cloud: THREE.Points | null = null
  let markers: THREE.Group | null = null
  let raf = 0
  let ro: ResizeObserver | null = null
  let autoTimer: ReturnType<typeof setInterval> | null = null

  function initThree() {
    if (!host) return
    const w = host.clientWidth, h = host.clientHeight
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(w, h)
    host.appendChild(renderer.domElement)
    scene = new THREE.Scene()
    scene.fog = new THREE.FogExp2(0x05070a, 0.045)
    camera = new THREE.PerspectiveCamera(55, w / h, 0.1, 200)
    camera.position.set(0, 0, 0.1)
    controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.rotateSpeed = 0.7
    controls.zoomSpeed = 0.9
    const loop = () => {
      raf = requestAnimationFrame(loop)
      controls?.update()
      if (renderer && scene && camera) renderer.render(scene, camera)
    }
    loop()
    ro = new ResizeObserver(() => {
      if (!host || !renderer || !camera) return
      const nw = host.clientWidth, nh = host.clientHeight
      renderer.setSize(nw, nh)
      camera.aspect = nw / nh
      camera.updateProjectionMatrix()
    })
    ro.observe(host)
  }

  async function loadScene(refresh = false) {
    if (refresh) sfx('sonar')
    loading = true; unavailable = false; reason = ''
    let res: Awaited<ReturnType<typeof api.spatial>> | null = null
    try { res = await api.spatial(cam, 320) } catch { res = null }
    if (!res || !res.scene) {
      reason = REASON_TEXT[res?.reason ?? ''] ?? 'The spatial view is unavailable right now.'
      loading = false; unavailable = true; return
    }
    camName = res.scene.cam
    try { await buildCloud(res.scene) } catch { unavailable = true }
    loading = false
  }

  function zOf(disp01: number) {
    return ZNEAR + Math.pow(1 - disp01, GAMMA) * (ZFAR - ZNEAR)
  }

  async function buildCloud(d: NonNullable<Awaited<ReturnType<typeof api.spatial>>['scene']>) {
    if (!scene) return
    const { w, h, fov, image, depth, entities } = d
    // decode the float32 depth grid (0..1, 1 = nearest)
    const bytes = Uint8Array.from(atob(depth), (c) => c.charCodeAt(0))
    const disp = new Float32Array(bytes.buffer)
    // decode the RGB frame for per-point colour
    const img = new Image()
    img.src = 'data:image/jpeg;base64,' + image
    await img.decode()
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h
    const ctx = cv.getContext('2d')!
    ctx.drawImage(img, 0, 0, w, h)
    const rgba = ctx.getImageData(0, 0, w, h).data

    const fx = 0.5 * w / Math.tan((fov * Math.PI) / 180 / 2)
    const cx = w / 2, cy = h / 2
    const pos: number[] = [], col: number[] = []
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = y * w + x
        if (disp[i] < SKYCULL) continue          // sky / far flat background
        if (x > 0 && x < w - 1 && y > 0 && y < h - 1) {
          const gx = Math.abs(disp[i + 1] - disp[i - 1])
          const gy = Math.abs(disp[i + w] - disp[i - w])
          if (gx > EDGE || gy > EDGE) continue   // edge-aware cull
        }
        const Z = zOf(disp[i])
        pos.push((x - cx) * Z / fx, -(y - cy) * Z / fx, -Z)
        const p = i * 4
        col.push(rgba[p] / 255, rgba[p + 1] / 255, rgba[p + 2] / 255)
      }
    }
    // (re)build the cloud
    if (cloud) { scene.remove(cloud); cloud.geometry.dispose(); (cloud.material as THREE.Material).dispose() }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
    geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3))
    const mat = new THREE.PointsMaterial({ size: 0.035, vertexColors: true, sizeAttenuation: true })
    cloud = new THREE.Points(geo, mat)
    scene.add(cloud)

    // entity markers
    if (markers) { scene.remove(markers); disposeGroup(markers) }
    markers = new THREE.Group()
    entityCount = entities.length
    for (const e of entities) {
      const Z = zOf(e.depth)
      const u = e.cx * w, v = e.cy * h
      const X = (u - cx) * Z / fx, Y = -(v - cy) * Z / fx
      const spr = makeMarker(e.label || e.cls, CLS_COLOR[e.cls] || '#c9d4dc')
      spr.position.set(X, Y, -Z)
      markers.add(spr)
    }
    scene.add(markers)

    // fit-to-bounds framing, offset a touch off-axis so the 3D structure reads immediately
    const box = new THREE.Box3().setFromBufferAttribute(geo.getAttribute('position') as THREE.BufferAttribute)
    const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3())
    const vfov = camera!.fov * Math.PI / 180
    const fitH = (size.y / 2) / Math.tan(vfov / 2)
    const fitW = (size.x / 2) / Math.tan(vfov / 2) / camera!.aspect
    const dist = Math.max(fitH, fitW) * 1.08 + size.z * 0.4
    const yaw = 18 * Math.PI / 180, pitch = 6 * Math.PI / 180
    if (controls) { controls.target.copy(c); controls.minDistance = dist * 0.25; controls.maxDistance = dist * 4 }
    camera!.position.set(
      c.x + dist * Math.sin(yaw) * Math.cos(pitch),
      c.y - dist * Math.sin(pitch),
      c.z + dist * Math.cos(yaw) * Math.cos(pitch))
    camera!.updateProjectionMatrix()
  }

  function makeMarker(text: string, color: string): THREE.Sprite {
    const cw = 256, ch = 72
    const cv = document.createElement('canvas'); cv.width = cw; cv.height = ch
    const g = cv.getContext('2d')!
    g.fillStyle = color
    g.beginPath(); g.arc(28, ch / 2, 9, 0, Math.PI * 2); g.fill()
    g.strokeStyle = color; g.lineWidth = 2
    g.beginPath(); g.arc(28, ch / 2, 16, 0, Math.PI * 2); g.stroke()
    g.font = '600 26px "JetBrains Mono", monospace'
    g.fillStyle = '#eaf2f6'
    g.textBaseline = 'middle'
    g.fillText(text.slice(0, 14), 50, ch / 2)
    const tex = new THREE.CanvasTexture(cv)
    tex.minFilter = THREE.LinearFilter
    // always-visible (depthTest off) so a marker is never swallowed by the cloud it sits in
    const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false, depthWrite: false }))
    spr.scale.set(1.15, 0.32, 1)
    spr.renderOrder = 999
    return spr
  }

  function disposeGroup(g: THREE.Group) {
    g.traverse((o) => {
      const s = o as THREE.Sprite
      if (s.material) { (s.material as THREE.SpriteMaterial).map?.dispose(); (s.material as THREE.Material).dispose() }
    })
  }

  function toggleAuto() {
    auto = !auto
    sfx('click')
    if (auto) autoTimer = setInterval(() => loadScene(false), 4000)
    else if (autoTimer) { clearInterval(autoTimer); autoTimer = null }
  }

  function onkey(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); onclose() } }

  onMount(() => {
    sfx('sonar')
    initThree()
    loadScene(false)
    window.addEventListener('keydown', onkey, true)
  })
  onDestroy(() => {
    window.removeEventListener('keydown', onkey, true)
    if (autoTimer) clearInterval(autoTimer)
    if (raf) cancelAnimationFrame(raf)
    ro?.disconnect()
    controls?.dispose()
    if (cloud) { cloud.geometry.dispose(); (cloud.material as THREE.Material).dispose() }
    if (markers) disposeGroup(markers)
    renderer?.dispose()
    if (renderer?.domElement && host?.contains(renderer.domElement)) host.removeChild(renderer.domElement)
  })
</script>

<div class="sv" role="dialog" aria-label="Spatial 3D scene">
  <header class="top caps">
    <span class="eyebrow">⛶ SPATIAL RECONSTRUCTION</span>
    <span class="camn">{camName || '—'}</span>
    <span class="mode">MONOCULAR DEPTH · 3D</span>
    <span class="spacer"></span>
    {#if entityCount}<span class="ec caps">◈ {entityCount} ENTIT{entityCount === 1 ? 'Y' : 'IES'}</span>{/if}
    <button class="ref caps" class:on={auto} onclick={toggleAuto}>{auto ? '● LIVE' : '○ LIVE'}</button>
    <button class="ref caps" onclick={() => loadScene(true)}>↻ RECAPTURE</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  <div class="stage" bind:this={host}></div>

  {#if loading}
    <div class="veil caps"><span class="pulse">RECONSTRUCTING DEPTH FIELD_</span></div>
  {:else if unavailable}
    <div class="veil caps">
      <div class="uahead">SPATIAL VIEW UNAVAILABLE</div>
      <div class="uasub">{reason}</div>
      <button class="ref caps" onclick={() => loadScene(true)}>↻ RETRY</button>
    </div>
  {:else}
    <div class="hint caps">DRAG TO ORBIT · SCROLL TO ZOOM · RIGHT-DRAG TO PAN</div>
  {/if}
</div>

<style>
  .sv { position: fixed; inset: 0; z-index: var(--z-boot); background: radial-gradient(120% 90% at 50% 10%, #0a1016 0%, #04060a 78%);
    color: var(--ink); display: flex; flex-direction: column; overflow: hidden; animation: svin 320ms cubic-bezier(0.16,1,0.3,1) both; }
  @keyframes svin { from { opacity: 0; } }
  .top { display: flex; align-items: center; gap: 12px; padding: 12px 22px; border-bottom: 1px solid var(--hairline);
    font-size: var(--fs-label); letter-spacing: var(--tracking); background: #04070a; z-index: 2; }
  .eyebrow { color: var(--scarlet); } .camn { color: var(--ink); font-size: 10px; letter-spacing: 0.14em; }
  .mode { color: var(--ink-ghost); font-size: 8px; } .spacer { flex: 1; }
  .ec { color: var(--cyan); font-size: 9px; }
  .ref, .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: pointer; font-size: 9px; letter-spacing: var(--tracking); }
  .ref:hover { border-color: var(--cyan); color: var(--cyan); } .ref.on { border-color: var(--cyan); color: var(--cyan); }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .stage { position: absolute; inset: 49px 0 0 0; }
  .veil { position: absolute; inset: 49px 0 0 0; display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 12px; color: var(--ink-dim); letter-spacing: 0.18em; background: rgba(4,6,10,0.35); }
  .pulse { animation: pulse 1.2s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.4; } }
  .uahead { color: var(--scarlet); font-size: 12px; letter-spacing: 0.2em; }
  .uasub { color: var(--ink-dim); font-size: 9px; letter-spacing: 0.06em; text-transform: none; }
  .hint { position: absolute; bottom: 16px; left: 0; right: 0; text-align: center; color: var(--ink-ghost);
    font-size: 8px; letter-spacing: 0.2em; pointer-events: none; }
</style>
