<script lang="ts">
  // Spatial 3D scene view (Feature 4). Lifts the camera's flat frame into a navigable point
  // cloud using monocular depth (Depth Anything V2, run backend-side). We back-project the
  // depth grid through a pinhole model, colour each point from the RGB frame, and drop a marker
  // at every detected person/vehicle so the operator can *think in 3D* about the scene —
  // distances, layering, who stands where. Drag to orbit, scroll to zoom.
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
  let dio = $state(true)     // semantic 3D DIORAMA (DEFAULT) — segmentation + depth object scene
  let full = $state(false)   // FULL generative 3D point cloud (DUSt3R / monocular)
  // staged reconstruction so the user watches the process, not a spinner:
  //   capture (grab frame) -> depth (scan-sweep the depth field over the frame) -> lift (to 3D)
  let phase = $state<'' | 'capture' | 'depth' | 'lift'>('')
  let previewCv = $state<HTMLCanvasElement | null>(null)
  const PHASE_LABEL: Record<string, string> = {
    capture: 'ACQUIRING FRAME', depth: 'ESTIMATING DEPTH FIELD', lift: 'LIFTING TO 3D',
  }
  const PHASE_STEP: Record<string, number> = { capture: 1, depth: 2, lift: 3 }
  // FULL-mode watchable loader: the captured frame is shown at once and the reconstruction is
  // narrated with a progress bar + rotating stages, so the operator never stares at a black void.
  let genProgress = $state(0)          // 0..100, eased toward ~94 until the real result lands
  let genStage = $state(0)
  const GEN_STEPS = ['CAPTURING VIEWPOINTS', 'MATCHING FEATURES', 'FUSING MULTI-VIEW GEOMETRY', 'BUILDING POINT CLOUD']
  const DIO_STEPS = ['ACQUIRING FRAME', 'PARSING THE SCENE', 'PLACING OBJECTS', 'BUILDING DIORAMA']
  const steps = $derived(dio ? DIO_STEPS : GEN_STEPS)
  let genRaf = 0
  let genActive = false
  let method = $state<'multiview' | 'monocular' | ''>('')
  let pointTex: THREE.Texture | null = null

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
  const MESH_MAXLEN = 0.13    // cull mesh triangles whose 3D edge is longer than this (kills skirts)
  const SKYCULL = 0.03        // drop the most-distant (sky / flat far background) points
  const CLS_COLOR: Record<string, string> = {
    person: '#35e0ff', vehicle: '#ffb038', animal: '#6be675', object: '#c9d4dc', weapon: '#ff3b3b',
  }

  let renderer: THREE.WebGLRenderer | null = null
  let scene: THREE.Scene | null = null
  let camera: THREE.PerspectiveCamera | null = null
  let controls: OrbitControls | null = null
  let mesh: THREE.Mesh | null = null           // foreground: the directly-observed surface
  let bgMesh: THREE.Mesh | null = null         // completed background reconstructed behind objects
  let pointsObj: THREE.Points | null = null    // FULL generative 3D reconstruction (point cloud)
  let markers: THREE.Group | null = null
  let dioGround: THREE.Mesh | null = null      // diorama: flat textured ground surface
  let dioObjects: THREE.Group | null = null    // diorama: stood-up object cutouts
  let dioTex: THREE.Texture[] = []             // diorama textures to dispose
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

  const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

  async function loadScene(refresh = false) {
    if (refresh) sfx('sonar')
    if (dio) { await loadDiorama(); return }
    if (full) { await loadFull(); return }
    loading = true; unavailable = false; reason = ''; phase = 'capture'
    let res: Awaited<ReturnType<typeof api.spatial>> | null = null
    try { res = await api.spatial(cam, 320) } catch { res = null }
    if (!res || !res.scene) {
      reason = REASON_TEXT[res?.reason ?? ''] ?? 'The spatial view is unavailable right now.'
      loading = false; unavailable = true; phase = ''; return
    }
    camName = res.scene.cam
    try {
      await revealDepth(res.scene)      // show the frame, then sweep the depth field over it
      phase = 'lift'
      await buildCloud(res.scene)       // build the 3D behind the veil
      await wait(520)                   // let it settle, then the veil fades to reveal it
    } catch { unavailable = true }
    loading = false; phase = ''
  }

  // FULL generative reconstruction: one long request (~30 s) that returns a completed point
  // cloud. We fire it immediately, and IN PARALLEL grab a fast frame+depth purely to drive a
  // watchable loading montage (the real captured frame + a live depth/scan sweep + a staged
  // progress bar) so the operator watches the scene being built instead of a black screen. The
  // fast depth-mesh is NOT put into the 3D scene — only the point cloud is ever shown.
  async function loadFull() {
    loading = true; unavailable = false; reason = ''; phase = 'capture'
    genProgress = 0; genStage = 0; method = ''
    const fullP = api.spatial3d(cam).catch(() => null)          // heavy job starts now
    const fastP = api.spatial(cam, 256).catch(() => null)       // quick frame for the montage
    startGenProgress()
    fastP.then((fast) => { if (fast?.scene && genActive && loading) { if (!camName) camName = fast.scene.cam; startGenPreview(fast.scene) } })
    const res = await fullP
    stopGenPreview()
    if (!res || !res.scene) {
      if (res?.reason === 'insufficient_vram') {
        reason = `Full 3D needs a more capable GPU — about ${res.need_gb} GB of VRAM, but only ${res.have_gb} GB is available. Use the fast depth-mesh mode instead.`
      } else {
        reason = REASON_TEXT[res?.reason ?? ''] ?? 'Full 3D reconstruction is unavailable right now.'
      }
      loading = false; unavailable = true; phase = ''; return
    }
    camName = res.scene.cam
    method = res.scene.method ?? ''
    genProgress = 100; genStage = GEN_STEPS.length - 1
    try { phase = 'lift'; buildPointCloud(res.scene); await wait(560) } catch { unavailable = true }
    loading = false; phase = ''
  }

  // Ease the progress bar toward ~94% over ~30 s (it never completes on its own — the real
  // result snaps it to 100), and advance the stage label with it.
  function startGenProgress() {
    genActive = true
    const t0 = performance.now(), SPAN = 30_000
    const tick2 = () => {
      if (!genActive) return
      const e = (performance.now() - t0) / SPAN
      genProgress = Math.min(94, 94 * (1 - Math.exp(-2.4 * e)))   // fast then asymptotic
      genStage = Math.min(GEN_STEPS.length - 1, Math.floor((genProgress / 94) * GEN_STEPS.length))
      genRaf = requestAnimationFrame(tick2)
    }
    tick2()
  }

  // Paint the captured frame into the preview canvas and endlessly sweep a depth-colourised
  // "reconstruction" band down it — the visual of the AI working the scene.
  async function startGenPreview(d: NonNullable<Awaited<ReturnType<typeof api.spatial>>['scene']>) {
    if (!genActive) return
    const { w, h, image, depth } = d
    await tick()
    const cv = previewCv
    if (!cv) return
    const bytes = Uint8Array.from(atob(depth), (c) => c.charCodeAt(0))
    const disp = new Float32Array(bytes.buffer)
    const img = new Image(); img.src = 'data:image/jpeg;base64,' + image
    try { await img.decode() } catch { return }
    cv.width = w; cv.height = h
    const g = cv.getContext('2d')!
    const dImg = g.createImageData(w, h)
    for (let i = 0; i < w * h; i++) { const [r, gg, b] = depthColor(disp[i]); const p = i * 4; dImg.data[p] = r; dImg.data[p + 1] = gg; dImg.data[p + 2] = b; dImg.data[p + 3] = 255 }
    const dCanvas = document.createElement('canvas'); dCanvas.width = w; dCanvas.height = h
    dCanvas.getContext('2d')!.putImageData(dImg, 0, 0)
    const t0 = performance.now(), BAND = h * 0.22
    const frame = () => {
      if (!genActive || !previewCv) return
      const p = ((performance.now() - t0) / 2600) % 1
      const yl = p * (h + BAND) - BAND
      g.clearRect(0, 0, w, h)
      g.drawImage(img, 0, 0, w, h)                                    // the live frame
      g.save(); g.beginPath(); g.rect(0, Math.max(0, yl), w, BAND); g.clip()
      g.drawImage(dCanvas, 0, 0, w, h); g.restore()                  // depth revealed inside the band
      g.fillStyle = 'rgba(120,224,255,0.85)'; g.fillRect(0, yl + BAND - 1.5, w, 2)
      genRaf2 = requestAnimationFrame(frame)
    }
    frame()
  }
  let genRaf2 = 0
  function stopGenPreview() {
    genActive = false
    if (genRaf) cancelAnimationFrame(genRaf)
    if (genRaf2) cancelAnimationFrame(genRaf2)
    genRaf = 0; genRaf2 = 0
  }

  // Semantic DIORAMA: one request (~8 s) that parses the scene and returns a textured ground, a
  // sky colour and stood-up object cutouts. Same watchable montage as the point-cloud path.
  async function loadDiorama() {
    loading = true; unavailable = false; reason = ''; phase = 'capture'
    genProgress = 0; genStage = 0; method = ''
    const dioP = api.diorama(cam).catch(() => null)
    const fastP = api.spatial(cam, 256).catch(() => null)
    startGenProgress()
    fastP.then((fast) => { if (fast?.scene && genActive && loading) { if (!camName) camName = fast.scene.cam; startGenPreview(fast.scene) } })
    const res = await dioP
    stopGenPreview()
    if (!res || !res.scene) {
      if (res?.reason === 'insufficient_vram') {
        reason = `The diorama needs a GPU with about ${res.need_gb} GB of VRAM, but only ${res.have_gb} GB is available.`
      } else {
        reason = REASON_TEXT[res?.reason ?? ''] ?? 'The 3D diorama is unavailable right now.'
      }
      loading = false; unavailable = true; phase = ''; return
    }
    camName = res.scene.cam
    genProgress = 100; genStage = GEN_STEPS.length - 1
    try { phase = 'lift'; await buildDiorama(res.scene); await wait(520) } catch (e) { console.error(e); unavailable = true }
    loading = false; phase = ''
  }

  function texFromB64Png(b64: string): THREE.Texture {
    const img = new Image(); img.src = 'data:image/png;base64,' + b64
    const tex = new THREE.Texture(img)
    tex.colorSpace = THREE.SRGBColorSpace
    tex.minFilter = THREE.LinearFilter
    img.decode().then(() => { tex.needsUpdate = true }).catch(() => {})
    dioTex.push(tex)
    return tex
  }

  async function buildDiorama(d: NonNullable<Awaited<ReturnType<typeof api.diorama>>['scene']>) {
    if (!scene || !camera) return
    clearScene()
    entityCount = d.objects.length
    const [sr, sg, sb] = d.sky
    scene.background = new THREE.Color(`rgb(${sr},${sg},${sb})`)
    scene.fog = new THREE.FogExp2((sr << 16) | (sg << 8) | sb, 0.012)   // subtle atmosphere
    const { w, h, fov } = d
    const fx = 0.5 * w / Math.tan((fov * Math.PI) / 180 / 2), cx = w / 2, cy = h / 2
    // ground: a solid textured surface over the ground-class pixels only
    const g = await decodeLayer(d.ground_image, d.ground_disp, w, h)
    dioGround = layerMesh(g.disp, g.rgba, w, h, fx, cx, cy, MESH_MAXLEN * 3.5, 0)
    scene.add(dioGround)
    // objects: each thing stood up as an image-textured cutout (cross for round things)
    dioObjects = new THREE.Group()
    for (const o of d.objects) {
      const tex = texFromB64Png(o.tex)
      const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, alphaTest: 0.35, side: THREE.DoubleSide })
      const geo = new THREE.PlaneGeometry(Math.max(o.w, 0.02), Math.max(o.h, 0.02))
      const mk = (ry: number) => {
        const m = new THREE.Mesh(geo, mat)
        m.position.set(o.pos[0], o.pos[1] + o.h / 2, o.pos[2]); m.rotation.y = ry; return m
      }
      dioObjects.add(mk(0))
      if (o.role === 'cross') dioObjects.add(mk(Math.PI / 2))
    }
    scene.add(dioObjects)
    // frame from a low 3/4 angle (the scene faces the original camera, like a stage)
    const box = new THREE.Box3().setFromObject(dioGround)
    if (dioObjects.children.length) box.expandByObject(dioObjects)
    const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3())
    const vfov = camera.fov * Math.PI / 180
    const dist = Math.max((size.y / 2) / Math.tan(vfov / 2), (size.x / 2) / Math.tan(vfov / 2) / camera.aspect) * 1.2 + size.z * 0.35
    const yaw = 14 * Math.PI / 180, pitch = 10 * Math.PI / 180
    if (controls) { controls.target.copy(c); controls.minDistance = dist * 0.1; controls.maxDistance = dist * 8 }
    camera.position.set(
      c.x + dist * Math.sin(yaw) * Math.cos(pitch),
      c.y - dist * Math.sin(pitch),
      c.z + dist * Math.cos(yaw) * Math.cos(pitch))
    camera.updateProjectionMatrix()
  }

  function clearScene() {
    if (mesh) { scene?.remove(mesh); mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose(); mesh = null }
    if (bgMesh) { scene?.remove(bgMesh); bgMesh.geometry.dispose(); (bgMesh.material as THREE.Material).dispose(); bgMesh = null }
    if (pointsObj) { scene?.remove(pointsObj); pointsObj.geometry.dispose(); (pointsObj.material as THREE.Material).dispose(); pointsObj = null }
    clearDiorama()
  }

  function clearDiorama() {
    if (dioGround) { scene?.remove(dioGround); dioGround.geometry.dispose(); (dioGround.material as THREE.Material).dispose(); dioGround = null }
    if (dioObjects) {
      scene?.remove(dioObjects)
      dioObjects.traverse((o) => { const m = o as THREE.Mesh; m.geometry?.dispose?.(); const mm = m.material as THREE.Material | undefined; mm?.dispose?.() })
      dioObjects = null
    }
    for (const t of dioTex) t.dispose()
    dioTex = []
    if (scene) { scene.background = null; scene.fog = new THREE.FogExp2(0x05070a, 0.045) }
  }

  function buildPointCloud(d: NonNullable<Awaited<ReturnType<typeof api.spatial3d>>['scene']>) {
    if (!scene) return
    const pb = Uint8Array.from(atob(d.points), (c) => c.charCodeAt(0))
    const pos = new Float32Array(pb.buffer)                     // N*3 xyz
    const cb = Uint8Array.from(atob(d.colors), (c) => c.charCodeAt(0))  // N*3 rgb
    const col = new Float32Array(cb.length)
    for (let i = 0; i < cb.length; i++) col[i] = cb[i] / 255
    clearScene()
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
    geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3))
    geo.computeBoundingBox()
    const bb = geo.boundingBox!
    const size = bb.getSize(new THREE.Vector3()), c = bb.getCenter(new THREE.Vector3())
    const diag = Math.max(size.length(), 1)
    if (!pointTex) pointTex = makeDiscTexture()
    // multi-view clouds are a wide, near-flat ground footprint — bigger points close the gaps;
    // the monocular fallback is denser/bumpier and wants finer points to keep detail.
    const isMV = method === 'multiview'
    const mat = new THREE.PointsMaterial({
      size: diag * (isMV ? 0.009 : 0.006), vertexColors: true, sizeAttenuation: true,
      map: pointTex, alphaTest: 0.5, transparent: true, depthWrite: true })
    pointsObj = new THREE.Points(geo, mat)
    scene.add(pointsObj)
    entityCount = 0
    // Framing: multi-view scenes vary (flat aerial vs forward-receding dashcam), so no single
    // angle is optimal — fit the whole cloud (bounding sphere) and establish from a moderate 3/4
    // angle that reads well for either; drag-to-orbit takes it from there. The bumpier monocular
    // fallback keeps its tuned low 3/4 framing.
    const vfov = camera!.fov * Math.PI / 180
    let dist: number, yaw: number, pitch: number
    if (isMV) {
      // ground is PCA-aligned to the X-Z plane, so establish from high above (near top-down) to
      // reveal the layout; fit the ground footprint (X-Z), not the thin vertical extent.
      const foot = Math.max(size.x, size.z)
      dist = (foot / 2) / Math.tan(vfov / 2) * 1.15
      yaw = 20 * Math.PI / 180; pitch = 44 * Math.PI / 180
    } else {
      dist = Math.max((size.y / 2) / Math.tan(vfov / 2), (size.x / 2) / Math.tan(vfov / 2) / camera!.aspect) * 1.55
      yaw = 16 * Math.PI / 180; pitch = 8 * Math.PI / 180
    }
    if (controls) { controls.target.copy(c); controls.minDistance = dist * 0.15; controls.maxDistance = dist * 6 }
    camera!.position.set(c.x + dist * Math.sin(yaw) * Math.cos(pitch), c.y - dist * Math.sin(pitch), c.z + dist * Math.cos(yaw) * Math.cos(pitch))
    camera!.updateProjectionMatrix()
  }

  // Inferno-ish ramp: near (1) = warm/bright, far (0) = dark violet — reads as a depth field.
  function depthColor(t: number): [number, number, number] {
    const stops: [number, number[]][] = [
      [0.0, [10, 6, 30]], [0.35, [90, 20, 90]], [0.6, [200, 60, 60]],
      [0.8, [240, 130, 30]], [1.0, [252, 230, 140]]]
    let a = stops[0], b = stops[stops.length - 1]
    for (let i = 0; i < stops.length - 1; i++) if (t >= stops[i][0] && t <= stops[i + 1][0]) { a = stops[i]; b = stops[i + 1]; break }
    const f = (t - a[0]) / (b[0] - a[0] + 1e-6)
    return [a[1][0] + (b[1][0] - a[1][0]) * f, a[1][1] + (b[1][1] - a[1][1]) * f, a[1][2] + (b[1][2] - a[1][2]) * f]
  }

  // Draw the captured frame, then run a scan line down it that leaves the depth field behind —
  // the operator literally watches depth being estimated. Resolves when the sweep completes.
  async function revealDepth(d: NonNullable<Awaited<ReturnType<typeof api.spatial>>['scene']>) {
    const { w, h, image, depth } = d
    const bytes = Uint8Array.from(atob(depth), (c) => c.charCodeAt(0))
    const disp = new Float32Array(bytes.buffer)
    const img = new Image(); img.src = 'data:image/jpeg;base64,' + image; await img.decode()
    phase = 'depth'
    await tick()
    const cv = previewCv
    if (!cv) return
    cv.width = w; cv.height = h
    const g = cv.getContext('2d')!
    // pre-render the depth colormap into an offscreen image
    const dImg = g.createImageData(w, h)
    for (let i = 0; i < w * h; i++) { const [r, gg, b] = depthColor(disp[i]); const p = i * 4; dImg.data[p] = r; dImg.data[p + 1] = gg; dImg.data[p + 2] = b; dImg.data[p + 3] = 255 }
    const dCanvas = document.createElement('canvas'); dCanvas.width = w; dCanvas.height = h
    dCanvas.getContext('2d')!.putImageData(dImg, 0, 0)
    const DUR = 900, t0 = performance.now()
    await new Promise<void>((resolve) => {
      const frame = () => {
        const p = Math.min(1, (performance.now() - t0) / DUR)
        g.clearRect(0, 0, w, h)
        g.drawImage(img, 0, 0, w, h)                 // the raw frame
        const yline = Math.round(p * h)
        g.drawImage(dCanvas, 0, 0, w, yline, 0, 0, w, yline)  // depth revealed above the line
        g.fillStyle = 'rgba(120,224,255,0.9)'; g.fillRect(0, yline - 1, w, 2)  // scan line
        if (p < 1) requestAnimationFrame(frame); else resolve()
      }
      frame()
    })
  }

  function zOf(disp01: number) {
    return ZNEAR + Math.pow(1 - disp01, GAMMA) * (ZFAR - ZNEAR)
  }

  async function buildCloud(d: NonNullable<Awaited<ReturnType<typeof api.spatial>>['scene']>) {
    if (!scene) return
    if (pointsObj) { scene.remove(pointsObj); pointsObj.geometry.dispose(); (pointsObj.material as THREE.Material).dispose(); pointsObj = null }
    const { w, h, fov, image, depth, entities, bg_image, bg_depth } = d
    const fx = 0.5 * w / Math.tan((fov * Math.PI) / 180 / 2)
    const cx = w / 2, cy = h / 2

    // foreground: the directly-observed surface — a continuous triangle mesh with sharp
    // silhouettes (stretched skirts culled).
    const fg = await decodeLayer(image, depth, w, h)
    // the completed background (computed backend-side) is decoded first: it's both the far-field
    // fill AND the back-cap that turns foreground objects into solid volumes.
    const bg = (bg_image && bg_depth) ? await decodeLayer(bg_image, bg_depth, w, h) : null

    // foreground as a SOLID: each object is extruded back to the reconstructed background and its
    // silhouette stitched, so it's an opaque, textured volume — not a see-through shell.
    if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose() }
    mesh = layerMesh(fg.disp, fg.rgba, w, h, fx, cx, cy, MESH_MAXLEN, 0,
      { solid: true, bgdisp: bg ? bg.disp : null, maxT: 0.35 })
    scene.add(mesh)

    // thin completed-background layer behind everything, filling the far field (correct parallax).
    if (bgMesh) { scene.remove(bgMesh); bgMesh.geometry.dispose(); (bgMesh.material as THREE.Material).dispose(); bgMesh = null }
    if (bg) {
      bgMesh = layerMesh(bg.disp, bg.rgba, w, h, fx, cx, cy, MESH_MAXLEN * 2.5, 0.04)
      scene.add(bgMesh)
    }

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

    // fit-to-bounds framing (on the foreground surface), offset off-axis so the 3D reads at once
    const box = new THREE.Box3().setFromBufferAttribute(mesh.geometry.getAttribute('position') as THREE.BufferAttribute)
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

  async function decodeLayer(image: string, depth: string, w: number, h: number) {
    const bytes = Uint8Array.from(atob(depth), (c) => c.charCodeAt(0))
    const disp = new Float32Array(bytes.buffer)
    const img = new Image(); img.src = 'data:image/jpeg;base64,' + image; await img.decode()
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h
    const ctx = cv.getContext('2d')!; ctx.drawImage(img, 0, 0, w, h)
    return { disp, rgba: ctx.getImageData(0, 0, w, h).data }
  }

  // Back-project a depth+colour grid into a triangle-mesh surface. `maxlen` culls stretched
  // silhouette skirts; `zbias` pushes the surface back so a rear layer never z-fights the front.
  // When `solid`, each surface vertex is EXTRUDED backward — to the reconstructed background
  // behind it (`bgdisp`), capped at `maxT` — and every silhouette boundary is stitched into a
  // side wall, so a paper-thin shell becomes a watertight, opaque VOLUME. A foreground object
  // we only saw the front of thus gets a completed, textured body (back + sides), not a
  // see-through sheet.
  function layerMesh(disp: Float32Array, rgba: Uint8ClampedArray, w: number, h: number,
                     fx: number, cx: number, cy: number, maxlen: number, zbias: number,
                     opt: { solid?: boolean; bgdisp?: Float32Array | null; maxT?: number } = {}): THREE.Mesh {
    const solid = !!opt.solid, bgdisp = opt.bgdisp ?? null
    const maxT = opt.maxT ?? 0.35, minT = 0.03
    const pos: number[] = [], col: number[] = []
    const vidx = new Int32Array(w * h).fill(-1)
    const vz: number[] = [], vpix: number[] = []
    let vn = 0
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = y * w + x
        if (disp[i] < SKYCULL) continue
        vidx[i] = vn++
        const Z = zOf(disp[i])
        pos.push((x - cx) * Z / fx, -(y - cy) * Z / fx, -(Z + zbias)); vz.push(Z); vpix.push(i)
        const p = i * 4
        col.push(rgba[p] / 255, rgba[p + 1] / 255, rgba[p + 2] / 255)
      }
    }
    const nF = vn
    const idx: number[] = []
    const el = (va: number, vb: number) =>
      Math.hypot(pos[va * 3] - pos[vb * 3], pos[va * 3 + 1] - pos[vb * 3 + 1], pos[va * 3 + 2] - pos[vb * 3 + 2])
    const edge = new Map<string, number>()
    const bump = (a: number, b: number) => { const k = a < b ? a + '_' + b : b + '_' + a; edge.set(k, (edge.get(k) ?? 0) + 1) }
    const tri = (a: number, b: number, c: number) => {
      const va = vidx[a], vb = vidx[b], vc = vidx[c]
      if (va < 0 || vb < 0 || vc < 0) return
      if (Math.max(el(va, vb), el(vb, vc), el(va, vc)) > maxlen) return
      idx.push(va, vb, vc)
      if (solid) { bump(va, vb); bump(vb, vc); bump(vc, va) }
    }
    for (let y = 0; y < h - 1; y++) {
      for (let x = 0; x < w - 1; x++) {
        const tl = y * w + x, tr = tl + 1, bl = tl + w, br = bl + 1
        tri(tl, bl, tr); tri(tr, bl, br)
      }
    }
    if (solid) {
      for (let j = 0; j < nF; j++) {   // back vertices: extruded to the background, capped
        const T = bgdisp ? Math.max(minT, Math.min(maxT, zOf(bgdisp[vpix[j]]) - vz[j])) : Math.min(maxT, minT + 0.12)
        pos.push(pos[j * 3], pos[j * 3 + 1], pos[j * 3 + 2] - T)
        col.push(col[j * 3] * 0.82, col[j * 3 + 1] * 0.82, col[j * 3 + 2] * 0.82)
      }
      const nFrontIdx = idx.length     // back shell (reversed winding)
      for (let t = 0; t < nFrontIdx; t += 3) idx.push(idx[t] + nF, idx[t + 2] + nF, idx[t + 1] + nF)
      for (const [k, c] of edge) {     // side walls closing every silhouette boundary
        if (c !== 1) continue
        const [a, b] = k.split('_').map(Number)
        idx.push(a, b, b + nF, a, b + nF, a + nF)
      }
    }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
    geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3))
    geo.setIndex(idx)
    return new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide }))
  }

  // soft radial disc used as the point sprite so splats are round with a feathered edge
  function makeDiscTexture(): THREE.Texture {
    const s = 64, cv = document.createElement('canvas'); cv.width = cv.height = s
    const g = cv.getContext('2d')!
    const grad = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2)
    grad.addColorStop(0, 'rgba(255,255,255,1)'); grad.addColorStop(0.7, 'rgba(255,255,255,1)')
    grad.addColorStop(1, 'rgba(255,255,255,0)')
    g.fillStyle = grad; g.beginPath(); g.arc(s / 2, s / 2, s / 2, 0, Math.PI * 2); g.fill()
    const tex = new THREE.CanvasTexture(cv); tex.needsUpdate = true
    return tex
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

  function setMode(m: 'diorama' | 'points') {
    dio = m === 'diorama'; full = m === 'points'; auto = false
    sfx('click'); loadScene(true)
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
    stopGenPreview()
    pointTex?.dispose()
    if (raf) cancelAnimationFrame(raf)
    ro?.disconnect()
    controls?.dispose()
    if (mesh) { mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose() }
    if (bgMesh) { bgMesh.geometry.dispose(); (bgMesh.material as THREE.Material).dispose() }
    if (pointsObj) { pointsObj.geometry.dispose(); (pointsObj.material as THREE.Material).dispose() }
    if (markers) disposeGroup(markers)
    if (dioGround) { dioGround.geometry.dispose(); (dioGround.material as THREE.Material).dispose() }
    if (dioObjects) dioObjects.traverse((o) => { const m = o as THREE.Mesh; m.geometry?.dispose?.(); (m.material as THREE.Material | undefined)?.dispose?.() })
    for (const t of dioTex) t.dispose()
    renderer?.dispose()
    if (renderer?.domElement && host?.contains(renderer.domElement)) host.removeChild(renderer.domElement)
  })
</script>

<div class="sv" role="dialog" aria-label="Spatial 3D scene">
  <header class="top caps">
    <span class="eyebrow">⛶ SPATIAL RECONSTRUCTION</span>
    <span class="camn">{camName || '—'}</span>
    <span class="mode">{dio ? 'SEMANTIC DIORAMA · 3D' : full ? (method === 'multiview' ? 'MULTI-VIEW STEREO · 3D' : method === 'monocular' ? 'MONOCULAR GEN · 3D' : 'POINT CLOUD · 3D') : 'MONOCULAR DEPTH · 3D'}</span>
    <span class="spacer"></span>
    {#if entityCount}<span class="ec caps">◈ {entityCount} {dio ? (entityCount === 1 ? 'OBJECT' : 'OBJECTS') : (entityCount === 1 ? 'ENTITY' : 'ENTITIES')}</span>{/if}
    <button class="ref caps" class:on={dio} title="Semantic 3D diorama — objects detected, placed & textured from the frame"
      onclick={() => { if (!dio) setMode('diorama') }}>{dio ? '◉ DIORAMA' : '○ DIORAMA'}</button>
    <button class="ref caps" class:on={!dio && full} title="Full 3D point cloud (multi-view / generative)"
      onclick={() => { if (dio || !full) setMode('points') }}>{(!dio && full) ? '◉ POINTS' : '○ POINTS'}</button>
    <button class="ref caps" onclick={() => loadScene(true)}>↻ REBUILD</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  <div class="stage" bind:this={host}></div>

  {#if loading && (full || dio)}
    <div class="veil caps" class:lifting={phase === 'lift'}>
      <div class="reco">
        <div class="preview show gen">
          <canvas bind:this={previewCv}></canvas>
        </div>
        <div class="genbar"><span class="genfill" style="width:{genProgress}%"></span></div>
        <div class="steps caps">
          {#each steps as s, i}
            <span class="step" class:on={genStage === i} class:done={genStage > i}><span class="dot"></span>{s}</span>
          {/each}
        </div>
        <div class="pl caps"><span class="pulse">{steps[genStage] || 'RECONSTRUCTING'}_ · {Math.round(genProgress)}%</span></div>
        <div class="gensub caps">{dio ? 'parsing the scene · detecting & placing objects in 3D · ~10s' : 'multi-view stereo · fusing camera viewpoints into real 3D geometry · ~25s'}</div>
      </div>
    </div>
  {:else if loading}
    <div class="veil caps" class:lifting={phase === 'lift'}>
      <div class="reco">
        <div class="preview" class:show={phase !== ''}>
          <canvas bind:this={previewCv}></canvas>
          {#if phase === 'capture'}<div class="scanbox"><span class="scanline"></span></div>{/if}
        </div>
        <div class="steps caps">
          {#each ['capture', 'depth', 'lift'] as p}
            <span class="step" class:on={phase === p} class:done={PHASE_STEP[phase] > PHASE_STEP[p]}>
              <span class="dot"></span>{PHASE_LABEL[p]}
            </span>
          {/each}
        </div>
        <div class="pl caps"><span class="pulse">{PHASE_LABEL[phase] || 'RECONSTRUCTING'}_</span></div>
      </div>
    </div>
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
    gap: 12px; color: var(--ink-dim); letter-spacing: 0.18em; background: rgba(4,6,10,0.6); }
  .veil.lifting { animation: veilout 520ms ease forwards; }
  @keyframes veilout { to { opacity: 0; } }
  .pulse { animation: pulse 1.2s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.4; } }
  /* staged reconstruction loader */
  .reco { display: flex; flex-direction: column; align-items: center; gap: 16px; width: min(52vw, 560px); }
  .preview { position: relative; width: 62%; aspect-ratio: 16/9; border: 1px solid var(--hairline); background: #04070a;
    overflow: hidden; opacity: 0; transform: scale(0.96); transition: opacity 300ms, transform 300ms; box-shadow: 0 0 40px rgba(0,0,0,0.6); }
  .preview.show { opacity: 1; transform: scale(1); }
  .preview.gen { width: 88%; box-shadow: 0 0 60px rgba(0,0,0,0.7), 0 0 0 1px var(--cyan-dim, rgba(53,224,255,0.25)) inset; }
  .preview canvas { width: 100%; height: 100%; object-fit: cover; display: block; }
  /* progress bar for the generative reconstruction */
  .genbar { width: 88%; height: 3px; background: rgba(120,224,255,0.12); overflow: hidden; }
  .genfill { display: block; height: 100%; background: linear-gradient(90deg, #1c6b7e, var(--cyan)); box-shadow: 0 0 10px var(--cyan);
    transition: width 240ms linear; }
  .scanbox { position: absolute; inset: 0; }
  .scanbox .scanline { position: absolute; left: 0; right: 0; height: 2px; background: var(--cyan); box-shadow: 0 0 12px var(--cyan);
    animation: sweep 1.1s ease-in-out infinite; }
  @keyframes sweep { 0% { top: 4%; } 50% { top: 92%; } 100% { top: 4%; } }
  .steps { display: flex; gap: 18px; }
  .step { display: flex; align-items: center; gap: 6px; font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.14em; }
  .step .dot { width: 6px; height: 6px; border: 1px solid var(--ink-ghost); border-radius: 50%; }
  .step.on { color: var(--cyan); } .step.on .dot { border-color: var(--cyan); background: var(--cyan); box-shadow: 0 0 8px var(--cyan); }
  .step.done { color: var(--ink-dim); } .step.done .dot { border-color: var(--ink-dim); background: var(--ink-dim); box-shadow: none; }
  .pl { font-size: 10px; color: var(--cyan); letter-spacing: 0.2em; }
  .gensub { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.1em; text-transform: none; }
  .uahead { color: var(--scarlet); font-size: 12px; letter-spacing: 0.2em; }
  .uasub { color: var(--ink-dim); font-size: 9px; letter-spacing: 0.06em; text-transform: none; }
  .hint { position: absolute; bottom: 16px; left: 0; right: 0; text-align: center; color: var(--ink-ghost);
    font-size: 8px; letter-spacing: 0.2em; pointer-events: none; }
</style>
