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
  let full = $state(false)   // FULL generative 3D (heavy) vs fast depth-mesh
  // staged reconstruction so the user watches the process, not a spinner:
  //   capture (grab frame) -> depth (scan-sweep the depth field over the frame) -> lift (to 3D)
  let phase = $state<'' | 'capture' | 'depth' | 'lift'>('')
  let previewCv = $state<HTMLCanvasElement | null>(null)
  const PHASE_LABEL: Record<string, string> = {
    capture: 'ACQUIRING FRAME', depth: 'ESTIMATING DEPTH FIELD', lift: 'LIFTING TO 3D',
  }
  const PHASE_STEP: Record<string, number> = { capture: 1, depth: 2, lift: 3 }

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

  // FULL generative reconstruction: one long request (~15 s) that returns a completed point
  // cloud. No frame-sweep — just a "reconstructing" state, then the cloud rises in.
  async function loadFull() {
    loading = true; unavailable = false; reason = ''; phase = 'capture'
    let res: Awaited<ReturnType<typeof api.spatial3d>> | null = null
    try { res = await api.spatial3d(cam) } catch { res = null }
    if (!res || !res.scene) {
      if (res?.reason === 'insufficient_vram') {
        reason = `Full 3D needs a more capable GPU — about ${res.need_gb} GB of VRAM, but only ${res.have_gb} GB is available. Use the fast depth-mesh mode instead.`
      } else {
        reason = REASON_TEXT[res?.reason ?? ''] ?? 'Full 3D reconstruction is unavailable right now.'
      }
      loading = false; unavailable = true; phase = ''; return
    }
    camName = res.scene.cam
    try { phase = 'lift'; buildPointCloud(res.scene); await wait(500) } catch { unavailable = true }
    loading = false; phase = ''
  }

  function clearScene() {
    if (mesh) { scene?.remove(mesh); mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose(); mesh = null }
    if (bgMesh) { scene?.remove(bgMesh); bgMesh.geometry.dispose(); (bgMesh.material as THREE.Material).dispose(); bgMesh = null }
    if (pointsObj) { scene?.remove(pointsObj); pointsObj.geometry.dispose(); (pointsObj.material as THREE.Material).dispose(); pointsObj = null }
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
    const mat = new THREE.PointsMaterial({ size: diag * 0.013, vertexColors: true, sizeAttenuation: true })
    pointsObj = new THREE.Points(geo, mat)
    scene.add(pointsObj)
    entityCount = 0
    // frame it (offset off-axis)
    const vfov = camera!.fov * Math.PI / 180
    const dist = Math.max((size.y / 2) / Math.tan(vfov / 2), (size.x / 2) / Math.tan(vfov / 2) / camera!.aspect) * 1.15
    const yaw = 16 * Math.PI / 180, pitch = 6 * Math.PI / 180
    if (controls) { controls.target.copy(c); controls.minDistance = dist * 0.2; controls.maxDistance = dist * 5 }
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
    if (mesh) { mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose() }
    if (bgMesh) { bgMesh.geometry.dispose(); (bgMesh.material as THREE.Material).dispose() }
    if (pointsObj) { pointsObj.geometry.dispose(); (pointsObj.material as THREE.Material).dispose() }
    if (markers) disposeGroup(markers)
    renderer?.dispose()
    if (renderer?.domElement && host?.contains(renderer.domElement)) host.removeChild(renderer.domElement)
  })
</script>

<div class="sv" role="dialog" aria-label="Spatial 3D scene">
  <header class="top caps">
    <span class="eyebrow">⛶ SPATIAL RECONSTRUCTION</span>
    <span class="camn">{camName || '—'}</span>
    <span class="mode">{full ? 'GENERATIVE · FULL 3D' : 'MONOCULAR DEPTH · 3D'}</span>
    <span class="spacer"></span>
    {#if entityCount}<span class="ec caps">◈ {entityCount} ENTIT{entityCount === 1 ? 'Y' : 'IES'}</span>{/if}
    <button class="ref caps" class:on={full} title="Generative full-3D reconstruction (heavy, fills every hole)"
      onclick={() => { full = !full; if (!full) auto = false; loadScene(true) }}>{full ? '◉ FULL 3D' : '○ FULL 3D'}</button>
    {#if !full}<button class="ref caps" class:on={auto} onclick={toggleAuto}>{auto ? '● LIVE' : '○ LIVE'}</button>{/if}
    <button class="ref caps" onclick={() => loadScene(true)}>↻ {full ? 'REBUILD' : 'RECAPTURE'}</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  <div class="stage" bind:this={host}></div>

  {#if loading && full}
    <div class="veil caps" class:lifting={phase === 'lift'}>
      <div class="reco">
        <div class="genring"></div>
        <div class="pl caps"><span class="pulse">{phase === 'lift' ? 'BUILDING POINT CLOUD' : 'GENERATING FULL 3D SCENE'}_</span></div>
        <div class="gensub caps">inpainting occluded surfaces across novel views · ~15s</div>
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
  .preview canvas { width: 100%; height: 100%; object-fit: cover; display: block; }
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
  .genring { width: 54px; height: 54px; border: 2px solid var(--hairline); border-top-color: var(--cyan); border-radius: 50%; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .gensub { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.1em; text-transform: none; }
  .uahead { color: var(--scarlet); font-size: 12px; letter-spacing: 0.2em; }
  .uasub { color: var(--ink-dim); font-size: 9px; letter-spacing: 0.06em; text-transform: none; }
  .hint { position: absolute; bottom: 16px; left: 0; right: 0; text-align: center; color: var(--ink-ghost);
    font-size: 8px; letter-spacing: 0.2em; pointer-events: none; }
</style>
