<script lang="ts">
  // Semantic World Model view (⛶ 3D SPATIAL). The backend parses the camera frame into a
  // Scene-Graph IR of independent, editable 3D objects on an inferred ground (see
  // docs/world-model-architecture.md) — NOT a pixel/point reconstruction. Here we render that IR:
  // a lit ground plane + one clean volume per object (class-coloured, sized & placed from the
  // graph) with a label. These placeholder volumes are swapped for real procedural / retrieved /
  // generated assets in later phases, behind the same IR. Drag to orbit, scroll to zoom.
  import * as THREE from 'three'
  import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
  import { onDestroy, onMount, tick } from 'svelte'
  import { api } from '../../lib/api'
  import { sfx } from '../../lib/audio'

  let { cam, onclose }: { cam: string; onclose: () => void } = $props()

  let host = $state<HTMLDivElement | null>(null)
  let loading = $state(true)
  let unavailable = $state(false)
  let reason = $state('')
  let camName = $state('')
  let objectCount = $state(0)
  let previewCv = $state<HTMLCanvasElement | null>(null)
  let genProgress = $state(0)
  let genStage = $state(0)
  const STEPS = ['ACQUIRING FRAME', 'PARSING THE SCENE', 'PLACING OBJECTS', 'BUILDING WORLD']

  const REASON_TEXT: Record<string, string> = {
    no_frame: 'No live frame on this camera yet. Open it in the live view, then try again.',
    depth_unavailable: "Scene model isn't available. Install dependencies with  uv sync  and restart the backend.",
    no_source: 'That camera no longer exists.',
    disabled: 'The 3D view is disabled in the configuration (spatial.enabled).',
    backend_down: 'The backend is not reachable.',
  }
  const CLASS_COLOR: Record<string, string> = {
    building: '#b0a896', wall: '#aaa59b', fence: '#96825a', bridge: '#969699', vehicle: '#7f8aa0',
    boat: '#8c8c96', motorcycle: '#8c7878', bicycle: '#8c8278', person: '#c8aa96', pole: '#787878',
    streetlight: '#787878', traffic_light: '#e0553a', sign: '#96966e', bench: '#8c6e50',
    tree: '#468238', bush: '#5a824a', mountain: '#78786e', rock: '#82796e', awning: '#a08c78',
  }

  let renderer: THREE.WebGLRenderer | null = null
  let scene: THREE.Scene | null = null
  let camera: THREE.PerspectiveCamera | null = null
  let controls: OrbitControls | null = null
  let world: THREE.Group | null = null           // the whole built scene (ground + objects)
  let labels: THREE.Group | null = null
  let raf = 0, genRaf = 0, genRaf2 = 0, genActive = false
  let ro: ResizeObserver | null = null

  const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

  function initThree() {
    if (!host) return
    const w = host.clientWidth, h = host.clientHeight
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(w, h)
    host.appendChild(renderer.domElement)
    scene = new THREE.Scene()
    camera = new THREE.PerspectiveCamera(55, w / h, 0.05, 500)
    camera.position.set(0, 3, 6)
    controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true; controls.dampingFactor = 0.08
    controls.rotateSpeed = 0.7; controls.zoomSpeed = 0.9
    const loop = () => { raf = requestAnimationFrame(loop); controls?.update(); if (renderer && scene && camera) renderer.render(scene, camera) }
    loop()
    ro = new ResizeObserver(() => {
      if (!host || !renderer || !camera) return
      const nw = host.clientWidth, nh = host.clientHeight
      renderer.setSize(nw, nh); camera.aspect = nw / nh; camera.updateProjectionMatrix()
    })
    ro.observe(host)
  }

  async function loadScene(refresh = false) {
    if (refresh) sfx('sonar')
    loading = true; unavailable = false; reason = ''; genProgress = 0; genStage = 0
    const wmP = api.worldmodel(cam).catch(() => null)
    const fastP = api.spatial(cam, 256).catch(() => null)         // frame for the watch-while-building montage
    startGenProgress()
    fastP.then((fast) => { if (fast?.scene && genActive && loading) { if (!camName) camName = fast.scene.cam; startPreview(fast.scene) } })
    const res = await wmP
    stopPreview()
    if (!res || !res.scene) {
      reason = res?.reason === 'insufficient_vram'
        ? `The 3D world model needs a GPU with about ${res.need_gb} GB of VRAM, but only ${res.have_gb} GB is available.`
        : REASON_TEXT[res?.reason ?? ''] ?? 'The 3D world model is unavailable right now.'
      loading = false; unavailable = true; return
    }
    camName = res.scene.cam
    genProgress = 100; genStage = STEPS.length - 1
    try { buildWorld(res.scene); await wait(480) } catch (e) { console.error(e); unavailable = true }
    loading = false
  }

  function clearWorld() {
    for (const g of [world, labels]) {
      if (!g) continue
      scene?.remove(g)
      g.traverse((o) => {
        const m = o as THREE.Mesh & THREE.Sprite
        m.geometry?.dispose?.()
        const mat = m.material as THREE.Material | THREE.Material[] | undefined
        if (Array.isArray(mat)) mat.forEach((x) => x.dispose()); else mat?.dispose?.()
        ;(m.material as THREE.SpriteMaterial | undefined)?.map?.dispose?.()
      })
    }
    world = labels = null
  }

  function buildWorld(d: NonNullable<Awaited<ReturnType<typeof api.worldmodel>>['scene']>) {
    if (!scene || !camera) return
    clearWorld()
    objectCount = d.nodes.length
    const [sr, sg, sb] = d.lighting.sky
    scene.background = new THREE.Color(`rgb(${sr},${sg},${sb})`)
    scene.fog = new THREE.FogExp2((sr << 16) | (sg << 8) | sb, 0.012)

    world = new THREE.Group(); labels = new THREE.Group()
    // lighting: sky/ground hemisphere + a directional sun
    world.add(new THREE.HemisphereLight(new THREE.Color(`rgb(${sr},${sg},${sb})`), 0x404038, 1.1))
    const sun = new THREE.DirectionalLight(0xfff4e0, 1.4)
    const [sx, sy, sz] = d.lighting.sun
    sun.position.set(sx * 10, sy * 10 + 5, sz * 10); world.add(sun)

    // depth-derived sizes are noisy and merged segments (e.g. a tree line) can explode; clamp each
    // object's dims to a few × the median so nothing dominates, and fit the ground to what remains.
    const bigs = d.nodes.map((n) => Math.max(n.dimensions.w, n.dimensions.h, n.dimensions.l)).sort((a, b) => a - b)
    const med = bigs.length ? bigs[Math.floor(bigs.length / 2)] : 1
    const cap = Math.max(med * 2.5, 1.2)
    const clamp = (v: number) => Math.max(0.08, Math.min(v, cap))
    let ext = 4
    for (const n of d.nodes) ext = Math.max(ext, Math.abs(n.transform.position[0]) + cap, Math.abs(n.transform.position[2]) + cap)

    // ground plane
    const gsize = Math.max(ext * 2.2, 8)
    const gmat = new THREE.MeshStandardMaterial({ color: groundColor(d.terrain.type), roughness: 0.95, metalness: 0.0 })
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(gsize, gsize), gmat)
    ground.rotation.x = -Math.PI / 2
    world.add(ground)
    // a faint grid so the ground reads as a built level, not a void
    const grid = new THREE.GridHelper(gsize, Math.max(6, Math.round(gsize / 2)), 0x000000, 0x000000)
    ;(grid.material as THREE.Material).opacity = 0.08; (grid.material as THREE.Material).transparent = true
    world.add(grid)

    // one clean volume per object, placed & sized from the scene graph
    for (const n of d.nodes) {
      const w = clamp(n.dimensions.w), h = clamp(n.dimensions.h), l = clamp(n.dimensions.l)
      const [px, py, pz] = n.transform.position
      const col = CLASS_COLOR[n.class] ?? ('#' + (n.material.tint ?? [150, 150, 150]).map((v) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, '0')).join(''))
      const foliage = n.class === 'tree' || n.class === 'bush'
      const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(col), roughness: 0.85, metalness: 0.05,
        transparent: foliage, opacity: foliage ? 0.92 : 1 })
      let geo: THREE.BufferGeometry, cy2 = py + h / 2
      if (foliage) { const r = Math.min(Math.max(w, l) / 2, cap * 0.5); geo = new THREE.SphereGeometry(r, 12, 9); cy2 = py + Math.max(h, r) }
      else if (n.class === 'pole' || n.class === 'streetlight') geo = new THREE.CylinderGeometry(Math.max(w, 0.06) / 2, Math.max(w, 0.06) / 2, h, 8)
      else geo = new THREE.BoxGeometry(w, h, l)
      const mesh = new THREE.Mesh(geo, mat)
      mesh.position.set(px, cy2, pz)
      const q = n.transform.rotation_quat
      mesh.quaternion.set(q[0], q[1], q[2], q[3])
      world.add(mesh)
      const spr = makeLabel(`${n.subtype || n.class}`.toUpperCase(), col)
      spr.position.set(px, cy2 + h * 0.6 + 0.4, pz)
      labels.add(spr)
    }
    scene.add(world); scene.add(labels)

    // frame the whole world from a low 3/4 angle
    const box = new THREE.Box3().setFromObject(world)
    const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3())
    const vfov = camera.fov * Math.PI / 180
    const dist = Math.max((size.y / 2) / Math.tan(vfov / 2), (size.x / 2) / Math.tan(vfov / 2) / camera.aspect) * 1.15 + size.z * 0.4
    const yaw = 20 * Math.PI / 180, pitch = 16 * Math.PI / 180
    if (controls) { controls.target.copy(c); controls.minDistance = dist * 0.1; controls.maxDistance = dist * 8 }
    camera.position.set(c.x + dist * Math.sin(yaw) * Math.cos(pitch), c.y + dist * Math.sin(pitch), c.z + dist * Math.cos(yaw) * Math.cos(pitch))
    camera.updateProjectionMatrix()
  }

  function groundColor(type: string): THREE.Color {
    const m: Record<string, string> = { asphalt: '#3b3f45', concrete: '#8a8a86', grass: '#4a6b3a',
      sand: '#c9b489', dirt: '#6b5842', water: '#33526b' }
    return new THREE.Color(m[type] ?? '#44474d')
  }

  function makeLabel(text: string, color: string): THREE.Sprite {
    const cw = 256, ch = 64, cv = document.createElement('canvas'); cv.width = cw; cv.height = ch
    const g = cv.getContext('2d')!
    g.fillStyle = color; g.beginPath(); g.arc(20, ch / 2, 7, 0, Math.PI * 2); g.fill()
    g.font = '600 24px "JetBrains Mono", monospace'; g.fillStyle = '#eaf2f6'; g.textBaseline = 'middle'
    g.fillText(text.slice(0, 14), 38, ch / 2)
    const tex = new THREE.CanvasTexture(cv); tex.minFilter = THREE.LinearFilter
    const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false, depthWrite: false }))
    spr.scale.set(1.1, 0.28, 1); spr.renderOrder = 999
    return spr
  }

  // ---- watch-while-building montage (never a black screen) ----
  function startGenProgress() {
    genActive = true
    const t0 = performance.now(), SPAN = 9000
    const tick2 = () => {
      if (!genActive) return
      const e = (performance.now() - t0) / SPAN
      genProgress = Math.min(94, 94 * (1 - Math.exp(-2.6 * e)))
      genStage = Math.min(STEPS.length - 1, Math.floor((genProgress / 94) * STEPS.length))
      genRaf = requestAnimationFrame(tick2)
    }
    tick2()
  }
  async function startPreview(d: NonNullable<Awaited<ReturnType<typeof api.spatial>>['scene']>) {
    if (!genActive) return
    const { w, h, image, depth } = d
    await tick()
    const cv = previewCv; if (!cv) return
    const bytes = Uint8Array.from(atob(depth), (c) => c.charCodeAt(0))
    const disp = new Float32Array(bytes.buffer)
    const img = new Image(); img.src = 'data:image/jpeg;base64,' + image
    try { await img.decode() } catch { return }
    cv.width = w; cv.height = h
    const g = cv.getContext('2d')!
    const dImg = g.createImageData(w, h)
    for (let i = 0; i < w * h; i++) { const t = disp[i]; const p = i * 4; dImg.data[p] = 40 + t * 80; dImg.data[p + 1] = 120 + t * 100; dImg.data[p + 2] = 200; dImg.data[p + 3] = 255 }
    const dcv = document.createElement('canvas'); dcv.width = w; dcv.height = h; dcv.getContext('2d')!.putImageData(dImg, 0, 0)
    const t0 = performance.now(), BAND = h * 0.22
    const frame = () => {
      if (!genActive || !previewCv) return
      const p = ((performance.now() - t0) / 2600) % 1, yl = p * (h + BAND) - BAND
      g.clearRect(0, 0, w, h); g.drawImage(img, 0, 0, w, h)
      g.save(); g.beginPath(); g.rect(0, Math.max(0, yl), w, BAND); g.clip(); g.drawImage(dcv, 0, 0, w, h); g.restore()
      g.fillStyle = 'rgba(120,224,255,0.85)'; g.fillRect(0, yl + BAND - 1.5, w, 2)
      genRaf2 = requestAnimationFrame(frame)
    }
    frame()
  }
  function stopPreview() { genActive = false; if (genRaf) cancelAnimationFrame(genRaf); if (genRaf2) cancelAnimationFrame(genRaf2); genRaf = genRaf2 = 0 }

  function onkey(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); onclose() } }
  onMount(() => { sfx('sonar'); initThree(); loadScene(false); window.addEventListener('keydown', onkey, true) })
  onDestroy(() => {
    window.removeEventListener('keydown', onkey, true)
    stopPreview(); if (raf) cancelAnimationFrame(raf)
    ro?.disconnect(); controls?.dispose(); clearWorld()
    renderer?.dispose()
    if (renderer?.domElement && host?.contains(renderer.domElement)) host.removeChild(renderer.domElement)
  })
</script>

<div class="sv" role="dialog" aria-label="Semantic 3D world model">
  <header class="top caps">
    <span class="eyebrow">⛶ SEMANTIC WORLD MODEL</span>
    <span class="camn">{camName || '—'}</span>
    <span class="mode">SCENE GRAPH · 3D</span>
    <span class="spacer"></span>
    {#if objectCount}<span class="ec caps">◈ {objectCount} OBJECT{objectCount === 1 ? '' : 'S'}</span>{/if}
    <button class="ref caps" onclick={() => loadScene(true)}>↻ REBUILD</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  <div class="stage" bind:this={host}></div>

  {#if loading}
    <div class="veil caps">
      <div class="reco">
        <div class="preview show gen"><canvas bind:this={previewCv}></canvas></div>
        <div class="genbar"><span class="genfill" style="width:{genProgress}%"></span></div>
        <div class="steps caps">
          {#each STEPS as s, i}
            <span class="step" class:on={genStage === i} class:done={genStage > i}><span class="dot"></span>{s}</span>
          {/each}
        </div>
        <div class="pl caps"><span class="pulse">{STEPS[genStage] || 'BUILDING'}_ · {Math.round(genProgress)}%</span></div>
        <div class="gensub caps">parsing the scene · detecting, sizing & placing objects on an inferred ground · ~10s</div>
      </div>
    </div>
  {:else if unavailable}
    <div class="veil caps">
      <div class="uahead">3D WORLD MODEL UNAVAILABLE</div>
      <div class="uasub">{reason}</div>
      <button class="ref caps" onclick={() => loadScene(true)}>↻ RETRY</button>
    </div>
  {:else}
    <div class="hint caps">DRAG TO ORBIT · SCROLL TO ZOOM · RIGHT-DRAG TO PAN · SCENE GRAPH OF EDITABLE OBJECTS</div>
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
  .ref:hover { border-color: var(--cyan); color: var(--cyan); }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .stage { position: absolute; inset: 49px 0 0 0; }
  .veil { position: absolute; inset: 49px 0 0 0; display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 12px; color: var(--ink-dim); letter-spacing: 0.18em; background: rgba(4,6,10,0.6); }
  .pulse { animation: pulse 1.2s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.4; } }
  .reco { display: flex; flex-direction: column; align-items: center; gap: 16px; width: min(52vw, 560px); }
  .preview { position: relative; width: 88%; aspect-ratio: 16/9; border: 1px solid var(--hairline); background: #04070a;
    overflow: hidden; box-shadow: 0 0 60px rgba(0,0,0,0.7), 0 0 0 1px rgba(53,224,255,0.25) inset; }
  .preview canvas { width: 100%; height: 100%; object-fit: cover; display: block; }
  .genbar { width: 88%; height: 3px; background: rgba(120,224,255,0.12); overflow: hidden; }
  .genfill { display: block; height: 100%; background: linear-gradient(90deg, #1c6b7e, var(--cyan)); box-shadow: 0 0 10px var(--cyan); transition: width 240ms linear; }
  .steps { display: flex; gap: 18px; }
  .step { display: flex; align-items: center; gap: 6px; font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.14em; }
  .step .dot { width: 6px; height: 6px; border: 1px solid var(--ink-ghost); border-radius: 50%; }
  .step.on { color: var(--cyan); } .step.on .dot { border-color: var(--cyan); background: var(--cyan); box-shadow: 0 0 8px var(--cyan); }
  .step.done { color: var(--ink-dim); } .step.done .dot { border-color: var(--ink-dim); background: var(--ink-dim); }
  .pl { font-size: 10px; color: var(--cyan); letter-spacing: 0.2em; }
  .gensub { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.1em; text-transform: none; }
  .uahead { color: var(--scarlet); font-size: 12px; letter-spacing: 0.2em; }
  .uasub { color: var(--ink-dim); font-size: 9px; letter-spacing: 0.06em; text-transform: none; max-width: 460px; text-align: center; }
  .hint { position: absolute; bottom: 16px; left: 0; right: 0; text-align: center; color: var(--ink-ghost);
    font-size: 8px; letter-spacing: 0.2em; pointer-events: none; }
</style>
