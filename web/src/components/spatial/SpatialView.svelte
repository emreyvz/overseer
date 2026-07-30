<script lang="ts">
  // Spatial 3D scene view (Feature 4). Lifts the camera's flat frame into a navigable point
  // cloud using monocular depth (Depth Anything V2, run backend-side). We back-project the
  // depth grid through a pinhole model, colour each point from the RGB frame, and drop a marker
  // at every detected person/vehicle so the operator can *think in 3D* about the scene —
  // distances, layering, who stands where. Drag to orbit, scroll to zoom.
  import * as THREE from 'three'
  import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
  import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
  import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
  import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js'
  import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
  import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js'
  import { VignetteShader } from 'three/addons/shaders/VignetteShader.js'
  import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'
  import { onDestroy, onMount, tick } from 'svelte'
  import { api } from '../../lib/api'
  import { sfx } from '../../lib/audio'

  let { cam, onclose }: { cam: string; onclose: () => void } = $props()

  let host = $state<HTMLDivElement | null>(null)
  let loading = $state(true)
  let unavailable = $state(false)
  let reason = $state('')
  let camName = $state('')
  // staged reconstruction so the user watches the process, not a spinner:
  //   capture (grab frame) -> depth (scan-sweep the depth field over the frame) -> lift (to 3D)
  let phase = $state<'' | 'capture' | 'depth' | 'lift'>('')
  let previewCv = $state<HTMLCanvasElement | null>(null)
  let mapCv = $state<HTMLCanvasElement | null>(null)   // top-down mini-map canvas
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
  let measure = $state(false)                  // click-to-measure mode
  let measureDist = $state<number | null>(null) // last measured distance (scene units)

  // Back-projection / depth-to-Z tuning (settled by visual iteration across cameras).
  const ZNEAR = 1.0, ZFAR = 9.0, GAMMA = 1.6
  const MESH_MAXLEN = 0.32    // cull only wildly-stretched triangles; loose so the surface stays
                              // continuous (no torn gaps) — depth smoothing turns jumps into ramps
  const SKYCULL = 0.015       // keep almost everything (fills the far field); drop only pure sky
  const RELIEF = 0.6          // scale of height above the fitted ground trend: objects still stand up,
                              // but MODERATELY — 1.0 exaggerated the protrusions; the ground stays flat
                              // because its residual is ~0. (0.16 was the old over-flat "photo on floor".)
  const RELIEF_CAP = 2.2      // clamp raw residual height before scaling -> kills shapeless noise spikes
  const DISP_JUMP = 0.06      // cull triangles that straddle a depth discontinuity (an object's
                              // silhouette): its 3 disparities differ by more than this. Loose enough
                              // that noisy (wet/reflective) ground is NOT punched full of black-dot
                              // holes; ZRANGE below catches the long stretches this would miss.
  const ZRANGE = 2.0         // also cull a triangle spanning more than this in metric-ish Z: kills the
                              // long "back stretches" (a vehicle reaching to the end of the scene) that
                              // a gradual disparity ramp slips past DISP_JUMP. Flat ground has tiny
                              // Z-range per triangle, so it stays fully meshed.
  const CLS_COLOR: Record<string, string> = {
    person: '#35e0ff', vehicle: '#ffb038', animal: '#6be675', object: '#c9d4dc', weapon: '#ff3b3b',
  }

  let renderer: THREE.WebGLRenderer | null = null
  let composer: EffectComposer | null = null   // post-processing: AO + bloom + ACES tone map
  let sky: THREE.Mesh | null = null            // gradient atmosphere backdrop (no more black void)
  let scene: THREE.Scene | null = null
  let camera: THREE.PerspectiveCamera | null = null
  let controls: OrbitControls | null = null
  let mesh: THREE.Mesh | null = null           // foreground: the directly-observed surface
  let bgMesh: THREE.Mesh | null = null         // completed background reconstructed behind objects
  let markers: THREE.Group | null = null
  let shadows: THREE.Group | null = null       // soft contact-shadow blobs grounding entities
  let shadowTex: THREE.Texture | null = null   // shared radial blob (cached across rebuilds)
  let fgTex: THREE.Texture | null = null       // full-res texture map for the foreground mesh
  let raf = 0
  let ro: ResizeObserver | null = null
  let autoTimer: ReturnType<typeof setInterval> | null = null
  // top-down mini-map data + temporal-stabilisation state
  let mapPts: { x: number; z: number; color: string }[] = []
  let mapBox: { minX: number; maxX: number; minZ: number; maxZ: number } | null = null
  let prevDisp: Float32Array | null = null     // previous LIVE-frame disparity (for EMA)
  let prevKey = ''
  let measurePts: THREE.Vector3[] = []         // 3D points picked on the mesh
  let measureGroup: THREE.Group | null = null
  let downXY: [number, number] | null = null   // pointer-down pos (click vs drag discrimination)
  const raycaster = new THREE.Raycaster()

  function initThree() {
    if (!host) return
    const w = host.clientWidth, h = host.clientHeight
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(w, h)
    renderer.toneMapping = THREE.ACESFilmicToneMapping   // filmic highlight rolloff
    renderer.toneMappingExposure = 1.15
    host.appendChild(renderer.domElement)
    renderer.domElement.addEventListener('pointerdown', onPointerDown)
    renderer.domElement.addEventListener('pointerup', onPointerUp)
    scene = new THREE.Scene()
    scene.fog = new THREE.FogExp2(0x0b131c, 0.04)         // fog colour matches the sky horizon
    camera = new THREE.PerspectiveCamera(55, w / h, 0.1, 200)
    camera.position.set(0, 0, 0.1)
    controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.rotateSpeed = 0.7
    controls.zoomSpeed = 0.9
    addSky()
    buildComposer(w, h)
    const loop = () => {
      raf = requestAnimationFrame(loop)
      controls?.update()
      if (composer) composer.render()
      else if (renderer && scene && camera) renderer.render(scene, camera)
      drawMinimap()
    }
    loop()
    ro = new ResizeObserver(() => {
      if (!host || !renderer || !camera) return
      const nw = host.clientWidth, nh = host.clientHeight
      renderer.setSize(nw, nh)
      camera.aspect = nw / nh
      camera.updateProjectionMatrix()
      composer?.setSize(nw, nh)
    })
    ro.observe(host)
  }

  // Gradient atmosphere dome so the scene sits in a sky instead of a black void — a large
  // inverted sphere shaded top(sky)->horizon in the app's dark-tactical palette.
  function addSky() {
    if (!scene) return
    const uniforms = { top: { value: new THREE.Color(0x1b3350) }, bot: { value: new THREE.Color(0x0b131c) }, off: { value: 0.35 } }
    const mat = new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false, fog: false, uniforms,
      vertexShader: 'varying vec3 vP; void main(){ vP = position; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
      fragmentShader: 'varying vec3 vP; uniform vec3 top; uniform vec3 bot; uniform float off; void main(){ float h = clamp(normalize(vP).y*0.5+0.5+off,0.0,1.0); gl_FragColor = vec4(mix(bot,top,h),1.0); }',
    })
    sky = new THREE.Mesh(new THREE.SphereGeometry(140, 32, 16), mat)
    sky.renderOrder = -1
    scene.add(sky)
  }

  // Post-processing chain (verified via the offline Electron capture harness, scripts/gl_*):
  // RenderPass -> GTAO (ground-object contact/crease occlusion) -> subtle bloom on highlights ->
  // OutputPass (ACES tone map + sRGB). Kept unlit (texture carries the scene's real light).
  function buildComposer(w: number, h: number) {
    if (!renderer || !scene || !camera) return
    const rt = new THREE.WebGLRenderTarget(w, h, { type: THREE.HalfFloatType, samples: 4 })
    composer = new EffectComposer(renderer, rt)
    composer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    composer.setSize(w, h)
    composer.addPass(new RenderPass(scene, camera))
    const gtao = new GTAOPass(scene, camera, w, h)
    gtao.updateGtaoMaterial({ radius: 0.5, distanceExponent: 1.0, thickness: 0.7, scale: 1.0, samples: 16, distanceFallOff: 1.0, screenSpaceRadius: false })
    gtao.blendIntensity = 1.0
    composer.addPass(gtao)
    composer.addPass(new UnrealBloomPass(new THREE.Vector2(w, h), 0.22, 0.7, 0.9))   // gentler: don't explode blown highlights
    composer.addPass(new OutputPass())
    // subtle vignette LAST (sRGB/LDR): mixing toward black focuses the frame. Before the tone-map
    // it would feed negatives to ACES and orange the corners.
    const vig = new ShaderPass(VignetteShader)
    vig.uniforms.offset.value = 1.0; vig.uniforms.darkness.value = 0.85
    composer.addPass(vig)
  }

  const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

  async function loadScene(refresh = false) {
    if (refresh) sfx('sonar')
    loading = true; unavailable = false; reason = ''; phase = 'capture'
    let res: Awaited<ReturnType<typeof api.spatial>> | null = null
    try { res = await api.spatial(cam, 384) } catch { res = null }
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

  // Clean, smooth SILHOUETTE: morphologically tidy the "kept" (non-sky) mask so the mesh outline is
  // a smooth, well-defined shape instead of a jagged per-pixel fringe — close small notches, open
  // thin spikes, then trim a 1px border of unreliable edge pixels.
  function cleanMask(disp: Float32Array, w: number, h: number): Uint8Array {
    const base = new Uint8Array(w * h)
    for (let i = 0; i < w * h; i++) base[i] = disp[i] >= SKYCULL ? 1 : 0
    const morph = (m: Uint8Array, r: number, dilate: boolean): Uint8Array => {
      const tmp = new Uint8Array(w * h), out = new Uint8Array(w * h)
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        let v = dilate ? 0 : 1
        for (let d = -r; d <= r; d++) { const xx = x + d; if (xx < 0 || xx >= w) continue; v = dilate ? (v | m[y * w + xx]) : (v & m[y * w + xx]) }
        tmp[y * w + x] = v
      }
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        let v = dilate ? 0 : 1
        for (let d = -r; d <= r; d++) { const yy = y + d; if (yy < 0 || yy >= h) continue; v = dilate ? (v | tmp[yy * w + x]) : (v & tmp[yy * w + x]) }
        out[y * w + x] = v
      }
      return out
    }
    let m = morph(morph(base, 2, true), 2, false)   // close: fill small notches
    m = morph(morph(m, 2, false), 2, true)          // open: drop thin spikes / specks
    m = keepBig(m, w, h)                            // drop flyaway fragments (keep main body + sizable parts)
    m = fillHoles(m, w, h)                          // fill enclosed gaps -> solid, no black inside
    return morph(m, 5, false)                        // erode ~5px: trim the smear-prone boundary
  }

  // Keep only sizable connected regions of the mask (>=12% of the largest), dropping the little
  // flyaway fragments that leave ragged spikes/islands around the silhouette. Stack flood-fill.
  function keepBig(m: Uint8Array, w: number, h: number): Uint8Array {
    const lab = new Int32Array(w * h), sizes = [0]
    let cur = 0
    const stack: number[] = []
    for (let i = 0; i < w * h; i++) {
      if (m[i] !== 1 || lab[i]) continue
      cur++; sizes.push(0); lab[i] = cur; stack.length = 0; stack.push(i)
      while (stack.length) {
        const j = stack.pop()!; sizes[cur]++; const x = j % w, y = (j / w) | 0
        if (x > 0 && m[j - 1] === 1 && !lab[j - 1]) { lab[j - 1] = cur; stack.push(j - 1) }
        if (x < w - 1 && m[j + 1] === 1 && !lab[j + 1]) { lab[j + 1] = cur; stack.push(j + 1) }
        if (y > 0 && m[j - w] === 1 && !lab[j - w]) { lab[j - w] = cur; stack.push(j - w) }
        if (y < h - 1 && m[j + w] === 1 && !lab[j + w]) { lab[j + w] = cur; stack.push(j + w) }
      }
    }
    let mx = 0
    for (let c = 1; c <= cur; c++) if (sizes[c] > mx) mx = sizes[c]
    const keepMin = mx * 0.12
    const out = new Uint8Array(w * h)
    for (let i = 0; i < w * h; i++) out[i] = (lab[i] && sizes[lab[i]] >= keepMin) ? 1 : 0
    return out
  }

  // Replace only OUTLIER pixels (a disparity spike deviating from the local median by > thr) with
  // that median: fills speckle "from the neighbours" without smoothing real detail, so the
  // depth-jump cull stops punching black-dot holes into a noisy (wet/reflective) ground.
  function despike(disp: Float32Array, keep: Uint8Array, w: number, h: number, r = 2, thr = 0.03): Float32Array {
    const out = Float32Array.from(disp), win: number[] = []
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = y * w + x; if (!keep[i]) continue
      win.length = 0
      for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
        const xx = x + dx, yy = y + dy
        if (xx >= 0 && xx < w && yy >= 0 && yy < h && keep[yy * w + xx]) win.push(disp[yy * w + xx])
      }
      if (win.length < 3) continue
      win.sort((p, q) => p - q); const med = win[win.length >> 1]
      if (Math.abs(disp[i] - med) > thr) out[i] = med
    }
    return out
  }

  // Fill enclosed holes: any 0-region NOT connected to the frame border is an interior gap in the
  // surface — flood-fill the background from the border, then set every unreached 0 to 1. Result
  // is a simply-connected silhouette (a solid shape, no black patches inside the ground).
  function fillHoles(m: Uint8Array, w: number, h: number): Uint8Array {
    const out = Uint8Array.from(m)
    const reach = new Uint8Array(w * h)
    const stack: number[] = []
    const push = (x: number, y: number) => {
      if (x < 0 || x >= w || y < 0 || y >= h) return
      const i = y * w + x
      if (!reach[i] && m[i] === 0) { reach[i] = 1; stack.push(i) }
    }
    for (let x = 0; x < w; x++) { push(x, 0); push(x, h - 1) }
    for (let y = 0; y < h; y++) { push(0, y); push(w - 1, y) }
    while (stack.length) { const i = stack.pop()!; const x = i % w, y = (i / w) | 0; push(x - 1, y); push(x + 1, y); push(x, y - 1); push(x, y + 1) }
    for (let i = 0; i < w * h; i++) if (m[i] === 0 && !reach[i]) out[i] = 1
    return out
  }

  // Inpaint depth into the kept holes (pixels newly filled by fillHoles have no real disparity):
  // diffuse valid neighbouring depth inward so the filled area sits FLUSH with the surface
  // (borrowed "from other parts"), not as a far spike showing through as black.
  function fillDepth(disp: Float32Array, keep: Uint8Array, w: number, h: number): Float32Array {
    const out = Float32Array.from(disp)
    let todo: number[] = []
    for (let i = 0; i < w * h; i++) if (keep[i] && out[i] < SKYCULL) todo.push(i)
    for (let iter = 0; iter < 64 && todo.length; iter++) {
      const next: number[] = []
      for (const i of todo) {
        const x = i % w, y = (i / w) | 0
        let s = 0, n = 0
        if (x > 0 && out[i - 1] >= SKYCULL) { s += out[i - 1]; n++ }
        if (x < w - 1 && out[i + 1] >= SKYCULL) { s += out[i + 1]; n++ }
        if (y > 0 && out[i - w] >= SKYCULL) { s += out[i - w]; n++ }
        if (y < h - 1 && out[i + w] >= SKYCULL) { s += out[i + w]; n++ }
        if (n > 0) out[i] = s / n; else next.push(i)
      }
      if (next.length === todo.length) break
      todo = next
    }
    return out
  }

  // Smooth the disparity grid (separable box blur, a few passes) so the meshed surface is smooth
  // rather than stair-stepped, and sharp depth jumps become gentle ramps instead of torn edges.
  function smoothDisp(disp: Float32Array, w: number, h: number, radius = 2, iters = 2): Float32Array {
    let src = disp
    for (let it = 0; it < iters; it++) {
      const tmp = new Float32Array(w * h), out = new Float32Array(w * h)
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        let s = 0, n = 0
        for (let d = -radius; d <= radius; d++) { const xx = x + d; if (xx < 0 || xx >= w) continue; s += src[y * w + xx]; n++ }
        tmp[y * w + x] = s / n
      }
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        let s = 0, n = 0
        for (let d = -radius; d <= radius; d++) { const yy = y + d; if (yy < 0 || yy >= h) continue; s += tmp[yy * w + x]; n++ }
        out[y * w + x] = s / n
      }
      src = out
    }
    return src as Float32Array
  }

  // Edge-aware joint-bilateral smoothing: average neighbour disparities weighted by spatial
  // distance AND similarity in the guide image (RGB) and in disparity — so surfaces smooth out
  // while depth edges that line up with colour/texture edges stay crisp, instead of a box blur
  // rounding every edge. `rgba` is the (unchanging) guide; a couple of passes.
  function bilateralDisp(disp: Float32Array, rgba: Uint8ClampedArray, keep: Uint8Array, w: number, h: number,
                         radius = 3, sigmaC = 0.09, sigmaD = 0.04, iters = 2): Float32Array {
    const invC = 1 / (2 * sigmaC * sigmaC), invD = 1 / (2 * sigmaD * sigmaD), invS = 1 / (radius * radius + 1e-6)
    let src = disp
    for (let it = 0; it < iters; it++) {
      const out = new Float32Array(w * h)
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        const i = y * w + x
        if (!keep[i]) { out[i] = src[i]; continue }
        const p = i * 4, cr = rgba[p] / 255, cg = rgba[p + 1] / 255, cb = rgba[p + 2] / 255, dc = src[i]
        let s = 0, wsum = 0
        for (let dy = -radius; dy <= radius; dy++) {
          const yy = y + dy; if (yy < 0 || yy >= h) continue
          for (let dx = -radius; dx <= radius; dx++) {
            const xx = x + dx; if (xx < 0 || xx >= w) continue
            const j = yy * w + xx; if (!keep[j]) continue
            const q = j * 4, ddr = rgba[q] / 255 - cr, ddg = rgba[q + 1] / 255 - cg, ddb = rgba[q + 2] / 255 - cb
            const dv = src[j] - dc
            const wgt = Math.exp(-(dx * dx + dy * dy) * invS - (ddr * ddr + ddg * ddg + ddb * ddb) * invC - dv * dv * invD)
            s += src[j] * wgt; wsum += wgt
          }
        }
        out[i] = wsum > 0 ? s / wsum : dc
      }
      src = out
    }
    return src
  }

  // Solve a 3x3 linear system (Gaussian elimination w/ partial pivot) for the quadratic ground fit.
  function solve3(A: number[][], b: number[]): [number, number, number] | null {
    const m = [[...A[0], b[0]], [...A[1], b[1]], [...A[2], b[2]]]
    for (let i = 0; i < 3; i++) {
      let p = i
      for (let r = i + 1; r < 3; r++) if (Math.abs(m[r][i]) > Math.abs(m[p][i])) p = r
      if (Math.abs(m[p][i]) < 1e-9) return null
      ;[m[i], m[p]] = [m[p], m[i]]
      for (let r = 0; r < 3; r++) {
        if (r === i) continue
        const f = m[r][i] / m[i][i]
        for (let cc = i; cc < 4; cc++) m[r][cc] -= f * m[i][cc]
      }
    }
    return [m[0][3] / m[0][0], m[1][3] / m[1][1], m[2][3] / m[2][2]]
  }

  // Fit the ground's height trend Y = a + bZ + cZ² over the near/low image rows, so the same
  // correction can de-bow the mesh AND the entity markers consistently. Null if too few samples.
  function fitGround(disp: Float32Array, w: number, h: number, fx: number, cx: number, cy: number): [number, number, number] | null {
    const Zs: number[] = [], Ys: number[] = []
    for (let y = Math.floor(h * 0.5); y < h; y++) {
      for (let x = 0; x < w; x++) {
        const dv = disp[y * w + x]
        if (dv < SKYCULL) continue
        const Z = zOf(dv); Zs.push(Z); Ys.push(-(y - cy) * Z / fx)
      }
    }
    const n = Zs.length
    if (n < 40) return null
    // least-squares quadratic Y=a+bZ+cZ² over an index subset
    const quad = (idx: number[]): [number, number, number] | null => {
      let N = 0, sZ = 0, sZ2 = 0, sZ3 = 0, sZ4 = 0, sY = 0, sZY = 0, sZ2Y = 0
      for (const i of idx) { const Z = Zs[i], Y = Ys[i], Z2 = Z * Z; N++; sZ += Z; sZ2 += Z2; sZ3 += Z2 * Z; sZ4 += Z2 * Z2; sY += Y; sZY += Z * Y; sZ2Y += Z2 * Y }
      if (N < 3) return null
      return solve3([[N, sZ, sZ2], [sZ, sZ2, sZ3], [sZ2, sZ3, sZ4]], [sY, sZY, sZ2Y])
    }
    // RANSAC: standing objects/cars in the lower half pull a plain fit upward; robustly find the
    // dominant ground trend by keeping the model with the most inliers, then refit on them.
    const THRESH = 0.3
    const rnd = () => (Math.random() * n) | 0
    let best: [number, number, number] | null = null, bestCount = -1
    for (let it = 0; it < 60; it++) {
      const c = quad([rnd(), rnd(), rnd(), rnd(), rnd()])
      if (!c) continue
      let cnt = 0
      for (let i = 0; i < n; i++) { const Z = Zs[i]; if (Math.abs(Ys[i] - (c[0] + c[1] * Z + c[2] * Z * Z)) < THRESH) cnt++ }
      if (cnt > bestCount) { bestCount = cnt; best = c }
    }
    const allIdx = Array.from({ length: n }, (_, i) => i)
    if (!best) return quad(allIdx)
    const inl: number[] = []
    for (let i = 0; i < n; i++) { const Z = Zs[i]; if (Math.abs(Ys[i] - (best[0] + best[1] * Z + best[2] * Z * Z)) < THRESH) inl.push(i) }
    return inl.length >= 20 ? (quad(inl) ?? best) : best
  }

  async function buildCloud(d: NonNullable<Awaited<ReturnType<typeof api.spatial>>['scene']>) {
    if (!scene) return
    const { w, h, fov, image, depth, entities, bg_image, bg_depth } = d
    const fx = 0.5 * w / Math.tan((fov * Math.PI) / 180 / 2)
    const cx = w / 2, cy = h / 2

    // foreground: the directly-observed surface — a continuous triangle mesh with sharp
    // silhouettes (stretched skirts culled).
    const fg = await decodeLayer(image, depth, w, h)
    if (auto) applyTemporal(fg.disp, `${cam}:${w}x${h}`)   // LIVE: stabilise depth vs last frame
    // the completed background (computed backend-side) is decoded first: it's both the far-field
    // fill AND the back-cap that turns foreground objects into solid volumes.
    const bg = (bg_image && bg_depth) ? await decodeLayer(bg_image, bg_depth, w, h) : null
    // solid, gap-free surface: clean the silhouette + FILL enclosed holes, inpaint depth into those
    // holes from surrounding surface, then smooth. No black patches in the ground.
    const keep = cleanMask(fg.disp, w, h)
    // gentle scene-adaptive exposure from the SUBJECT (kept pixels): dark scenes stay dark-ish,
    // bright ones don't blow out — a tight nudge around the 1.15 base (never normalises to grey).
    if (renderer) {
      let lsum = 0, lcnt = 0
      for (let i = 0; i < keep.length; i++) { if (!keep[i]) continue; const p = i * 4; lsum += 0.299 * fg.rgba[p] + 0.587 * fg.rgba[p + 1] + 0.114 * fg.rgba[p + 2]; lcnt++ }
      const meanLum = lcnt ? (lsum / lcnt) / 255 : 0.25
      renderer.toneMappingExposure = Math.min(1.3, Math.max(0.9, 0.26 / Math.max(0.06, meanLum)))
    }
    // median kills speckle, then an EDGE-AWARE joint-bilateral (guided by the RGB frame) smooths
    // surfaces while keeping object edges crisp — keeps relief intact, no box-blur edge rounding.
    // The depth-discontinuity cull still removes silhouette smears.
    const fgDisp = bilateralDisp(despike(fillDepth(fg.disp, keep, w, h), keep, w, h), fg.rgba, keep, w, h, 3, 0.09, 0.04, 2)
    const bgDisp = bg ? smoothDisp(bg.disp, w, h, 4, 2) : null

    // shared ground de-bow: fit the ground's height trend once so the mesh flattens correctly
    // (monocular depth bows a flat road upward with distance — this straightens it).
    const gy = fitGround(fgDisp, w, h, fx, cx, cy)

    // full-res texture: a crisp copy of the frame UV-mapped onto the (coarse) mesh
    if (fgTex) { fgTex.dispose(); fgTex = null }
    if (d.tex_image) {
      const timg = new Image(); timg.src = 'data:image/jpeg;base64,' + d.tex_image
      try { await timg.decode() } catch { /* fall back to vertex colour */ }
      fgTex = new THREE.Texture(timg); fgTex.colorSpace = THREE.SRGBColorSpace
      fgTex.minFilter = THREE.LinearMipmapLinearFilter; fgTex.magFilter = THREE.LinearFilter
      fgTex.anisotropy = renderer?.capabilities.getMaxAnisotropy() ?? 1; fgTex.needsUpdate = true
    }

    // foreground as a SOLID: each object is extruded back to the reconstructed background and its
    // silhouette stitched, so it's an opaque, textured VOLUME — not a see-through shell.
    if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose() }
    // Class-aware back depth: give each DETECTED object a body depth from its class + apparent size,
    // so its occluded back is shaped like the object (a vehicle its full length, a person thin)
    // instead of a flat cap. Per-pixel map, 0 = use the default relief-based slab.
    const objDepth = new Float32Array(w * h)
    for (const b of (d.boxes ?? [])) {
      const x1 = Math.max(0, Math.floor(b.x1 * w)), x2 = Math.min(w, Math.ceil(b.x2 * w))
      const y1 = Math.max(0, Math.floor(b.y1 * h)), y2 = Math.min(h, Math.ceil(b.y2 * h))
      if (x2 <= x1 || y2 <= y1) continue
      const Zc = zOf(fgDisp[((y1 + y2) >> 1) * w + ((x1 + x2) >> 1)])
      const worldW = (x2 - x1) * Zc / fx          // the object's width in world units, at its depth
      const T = b.cls === 'vehicle' ? Math.min(1.6, Math.max(0.5, 1.5 * worldW))   // length ~ 1.5x width
        : b.cls === 'person' ? 0.15                                                // people are thin
          : Math.min(0.9, Math.max(0.3, worldW))                                   // generic object
      for (let y = y1; y < y2; y++) for (let x = x1; x < x2; x++) objDepth[y * w + x] = T
    }
    // no world-length cull (that tears far ground into holes); instead cut only triangles that
    // span a depth discontinuity (DISP_JUMP) -> crisp object silhouettes that stand up, while the
    // continuous ground stays a solid, gap-free surface. Culled boundaries are closed by the solid
    // side-walls (below), so no see-through black behind objects.
    mesh = layerMesh(fgDisp, fg.rgba, w, h, fx, cx, cy, 1e9, 0,
      { solid: true, bgdisp: bgDisp, maxT: 0.5, flattenCoef: gy, tex: fgTex, keep, dispJump: DISP_JUMP, zrange: ZRANGE, objDepth })
    scene.add(mesh)

    // thin completed-background layer behind everything, filling the far field (correct parallax).
    if (bgMesh) { scene.remove(bgMesh); bgMesh.geometry.dispose(); (bgMesh.material as THREE.Material).dispose(); bgMesh = null }
    if (bg && bgDisp) {
      // dispJump cull + a firm push-back so the completion layer stays BEHIND the foreground and its
      // ground-fill boundary can't stretch a blurry smear in front of objects (e.g. a car).
      bgMesh = layerMesh(bgDisp, bg.rgba, w, h, fx, cx, cy, MESH_MAXLEN * 2.5, 0.15, { flattenCoef: gy, dim: 0.6, dispJump: DISP_JUMP })
      scene.add(bgMesh)
    }

    // entity markers — de-bowed with the same trend so they sit on the flattened ground
    // entity positions feed the top-down mini-map only. NO floating 3D labels: sprite billboards
    // render as flat black rectangles under the post-processing pipeline (and clutter the scene) —
    // the mini-map dots + the mesh itself already show where things are.
    if (markers) { scene.remove(markers); disposeGroup(markers); markers = null }
    entityCount = entities.length
    mapPts = []
    for (const e of entities) {
      const Z = zOf(e.depth), X = (e.cx * w - cx) * Z / fx
      mapPts.push({ x: X, z: -Z, color: CLS_COLOR[e.cls] || '#c9d4dc' })
    }

    // soft contact shadows: a dark blob on the flattened ground under each entity, so they read
    // as standing ON the surface rather than floating (complements the GTAO crease occlusion).
    if (shadows) { scene.remove(shadows); disposeShadows() }
    shadows = new THREE.Group()
    const sblob = shadowBlobTex()
    for (const e of entities) {
      const Z = zOf(e.depth), X = (e.cx * w - cx) * Z / fx
      const r = e.cls === 'vehicle' ? 1.1 : e.cls === 'person' ? 0.5 : 0.7
      const sm = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
        new THREE.MeshBasicMaterial({ map: sblob, transparent: true, depthWrite: false, opacity: 0.9 }))
      sm.rotation.x = -Math.PI / 2; sm.scale.set(r * 2, r * 2, 1); sm.position.set(X, 0.02, -Z)
      shadows.add(sm)
    }
    scene.add(shadows)

    // framing: the ground is now flat (X-Z plane), so establish from ABOVE at a 3/4 angle that
    // reveals the layout, framed to the ground footprint.
    const box = new THREE.Box3().setFromBufferAttribute(mesh.geometry.getAttribute('position') as THREE.BufferAttribute)
    const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3())
    mapBox = { minX: box.min.x, maxX: box.max.x, minZ: box.min.z, maxZ: box.max.z }   // mini-map extent
    const vfov = camera!.fov * Math.PI / 180
    const foot = Math.max(size.x, size.z)
    const dist = (foot / 2) / Math.tan(vfov / 2) * 0.92 + size.y * 0.5   // tighter default frame
    const yaw = 22 * Math.PI / 180, pitch = 16 * Math.PI / 180   // lower angle -> standing objects read as 3D
    if (controls) { controls.target.copy(c); controls.minDistance = dist * 0.15; controls.maxDistance = dist * 5 }
    camera!.position.set(
      c.x + dist * Math.sin(yaw) * Math.cos(pitch),
      c.y + dist * Math.sin(pitch),
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
                     opt: { solid?: boolean; bgdisp?: Float32Array | null; maxT?: number; flattenCoef?: [number, number, number] | null; tex?: THREE.Texture | null; keep?: Uint8Array | null; dispJump?: number; zrange?: number; dim?: number; objDepth?: Float32Array | null } = {}): THREE.Mesh {
    const solid = !!opt.solid, bgdisp = opt.bgdisp ?? null
    const maxT = opt.maxT ?? 0.35, minT = 0.03, dim = opt.dim ?? 1   // dim<1 fades a guessed layer
    const pos: number[] = [], col: number[] = [], uv: number[] = []
    const vidx = new Int32Array(w * h).fill(-1)
    const vz: number[] = [], vpix: number[] = []
    const keep = opt.keep ?? cleanMask(disp, w, h)   // smooth, well-defined silhouette
    let vn = 0
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = y * w + x
        if (!keep[i]) continue
        vidx[i] = vn++
        const Z = zOf(disp[i])
        pos.push((x - cx) * Z / fx, -(y - cy) * Z / fx, -(Z + zbias)); vz.push(Z); vpix.push(i)
        const p = i * 4
        col.push(rgba[p] / 255 * dim, rgba[p + 1] / 255 * dim, rgba[p + 2] / 255 * dim)
        uv.push(x / (w - 1), 1 - y / (h - 1))     // UV into the full-res texture (flipY default)
      }
    }
    const nF = vn
    const idx: number[] = []
    const el = (va: number, vb: number) =>
      Math.hypot(pos[va * 3] - pos[vb * 3], pos[va * 3 + 1] - pos[vb * 3 + 1], pos[va * 3 + 2] - pos[vb * 3 + 2])
    const edge = new Map<string, number>()
    const bump = (a: number, b: number) => { const k = a < b ? a + '_' + b : b + '_' + a; edge.set(k, (edge.get(k) ?? 0) + 1) }
    const dj = opt.dispJump ?? Infinity   // max disparity spread a triangle may span before it's a
    const zr = opt.zrange ?? Infinity     // silhouette skirt; zr = max Z-span before it's a long stretch
    const tri = (a: number, b: number, c: number) => {
      const va = vidx[a], vb = vidx[b], vc = vidx[c]
      if (va < 0 || vb < 0 || vc < 0) return
      if (Math.max(el(va, vb), el(vb, vc), el(va, vc)) > maxlen) return
      const da = disp[a], db = disp[b], dc = disp[c]
      if (Math.max(Math.abs(da - db), Math.abs(db - dc), Math.abs(da - dc)) > dj) return
      const za = zOf(da), zb = zOf(db), zc = zOf(dc)
      if (Math.max(za, zb, zc) - Math.min(za, zb, zc) > zr) return   // long back-stretch -> cut
      idx.push(va, vb, vc)
      if (solid) { bump(va, vb); bump(vb, vc); bump(vc, va) }
    }
    for (let y = 0; y < h - 1; y++) {
      for (let x = 0; x < w - 1; x++) {
        const tl = y * w + x, tr = tl + 1, bl = tl + w, br = bl + 1
        tri(tl, bl, tr); tri(tr, bl, br)
      }
    }
    if (opt.flattenCoef) {   // de-bow: subtract the fitted ground trend so the GROUND flattens; each
      const [a, b, c] = opt.flattenCoef   // vertex keeps a CLAMPED, scaled residual above it -> objects
      for (let j = 0; j < nF; j++) {       // rise moderately and noise spikes can't shoot up (shapeless)
        const Z = vz[j]
        let r = pos[j * 3 + 1] - (a + b * Z + c * Z * Z)
        r = Math.max(-0.6, Math.min(RELIEF_CAP, r))
        pos[j * 3 + 1] = r * RELIEF
      }
    }
    if (solid) {
      for (let j = 0; j < nF; j++) {   // back vertices: DETECTED objects extrude to a class-appropriate
        const relief = Math.max(0, pos[j * 3 + 1])       // body depth (a vehicle its full length, a
        const od = opt.objDepth ? opt.objDepth[vpix[j]] : 0   // person thin); elsewhere the ground stays
        // only STANDING pixels (relief above the ground) get the object depth, so the ground/road
        // inside a detection box is not slabbed back with it.
        const T = (od > 0 && relief > 0.15) ? od : Math.min(maxT, minT + 0.45 * relief)
        pos.push(pos[j * 3], pos[j * 3 + 1], pos[j * 3 + 2] - T)
        col.push(col[j * 3] * 0.82, col[j * 3 + 1] * 0.82, col[j * 3 + 2] * 0.82)
        uv.push(uv[j * 2], uv[j * 2 + 1])           // back vertices reuse the front UV
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
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2))
    geo.setIndex(idx)
    geo.computeVertexNormals()   // GTAO's normal prepass needs normals; also drives the shading below
    // Soft normal-based shading baked into vertex colours: MeshBasicMaterial multiplies map*colour,
    // so 3D form reads (up-faces ~full brightness, sides/undersides darker) without PBR double-
    // lighting the already-lit texture. DARKEN-ONLY (<=1) so it can never blow the scene out.
    if (opt.tex) {
      const nrm = geo.getAttribute('normal'), cattr = geo.getAttribute('color')
      const L = new THREE.Vector3(0.35, 1.0, 0.45).normalize()
      for (let j = 0; j < cattr.count; j++) {
        const ndl = Math.max(0, nrm.getX(j) * L.x + nrm.getY(j) * L.y + nrm.getZ(j) * L.z)
        let sh = Math.min(1.0, 0.55 + 0.5 * ndl)
        if (j >= nF) sh *= 0.5   // back vertices = GUESSED (unseen) geometry -> fade, honest cue
        cattr.setXYZ(j, sh, sh, sh)
      }
      cattr.needsUpdate = true
    }
    // full-res texture (crisp) modulated by the shading vertex-colour; else per-vertex colour.
    const mat = opt.tex
      ? new THREE.MeshBasicMaterial({ map: opt.tex, vertexColors: true, side: THREE.DoubleSide })
      : new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide })
    return new THREE.Mesh(geo, mat)
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
    tex.colorSpace = THREE.SRGBColorSpace   // keep label colours correct through the tone-map pass
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

  function shadowBlobTex(): THREE.Texture {
    if (shadowTex) return shadowTex
    const cv = document.createElement('canvas'); cv.width = cv.height = 128
    const g = cv.getContext('2d')!
    const grad = g.createRadialGradient(64, 64, 3, 64, 64, 64)
    grad.addColorStop(0, 'rgba(0,0,0,0.6)'); grad.addColorStop(0.6, 'rgba(0,0,0,0.28)'); grad.addColorStop(1, 'rgba(0,0,0,0)')
    g.fillStyle = grad; g.fillRect(0, 0, 128, 128)
    shadowTex = new THREE.CanvasTexture(cv); shadowTex.colorSpace = THREE.SRGBColorSpace
    return shadowTex
  }
  // Top-down tactical mini-map: the ground footprint + entity dots (by class) + a camera marker,
  // redrawn each frame so the operator sees the layout from above while orbiting in 3D.
  function drawMinimap() {
    const cv = mapCv
    if (!cv || !mapBox || !camera || !controls) return
    const W = cv.width, H = cv.height, g = cv.getContext('2d')
    if (!g) return
    g.clearRect(0, 0, W, H)
    g.fillStyle = 'rgba(4,7,10,0.72)'; g.fillRect(0, 0, W, H)
    const pad = 10, { minX, maxX, minZ, maxZ } = mapBox
    const sc = Math.min((W - 2 * pad) / ((maxX - minX) || 1), (H - 2 * pad) / ((maxZ - minZ) || 1))
    const ox = pad + ((W - 2 * pad) - sc * (maxX - minX)) / 2
    const oy = pad + ((H - 2 * pad) - sc * (maxZ - minZ)) / 2
    const toX = (x: number) => ox + (x - minX) * sc
    const toY = (z: number) => oy + (z - minZ) * sc      // far (most-negative Z) -> top
    g.strokeStyle = 'rgba(120,224,255,0.30)'; g.lineWidth = 1
    g.strokeRect(toX(minX), toY(minZ), sc * (maxX - minX), sc * (maxZ - minZ))
    for (const p of mapPts) { g.fillStyle = p.color; g.beginPath(); g.arc(toX(p.x), toY(p.z), 3, 0, Math.PI * 2); g.fill() }
    // camera marker (clamped to the panel) + sight line toward the view target
    const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
    const cxp = clamp(toX(camera.position.x), 2, W - 2), cyp = clamp(toY(camera.position.z), 2, H - 2)
    g.strokeStyle = 'rgba(234,242,246,0.6)'; g.beginPath(); g.moveTo(cxp, cyp); g.lineTo(toX(controls.target.x), toY(controls.target.z)); g.stroke()
    g.fillStyle = '#eaf2f6'; g.beginPath(); g.arc(cxp, cyp, 3, 0, Math.PI * 2); g.fill()
  }

  // Temporal stabilisation for LIVE mode: EMA the disparity against the previous frame so the
  // surface stops jittering — but ADAPTIVELY: where the depth changed a lot (a moving person/
  // vehicle) take the new value fully (no ghosting); only blend the near-static background.
  function applyTemporal(disp: Float32Array, key: string) {
    if (prevDisp && prevKey === key && prevDisp.length === disp.length) {
      for (let i = 0; i < disp.length; i++) {
        const a = Math.abs(disp[i] - prevDisp[i]) > 0.06 ? 1 : 0.55
        disp[i] = a * disp[i] + (1 - a) * prevDisp[i]
      }
    }
    prevKey = key; prevDisp = Float32Array.from(disp)
  }

  // dispose shadow meshes (geometry + material) but KEEP the shared blob texture (cached)
  function disposeShadows() {
    shadows?.traverse((o) => {
      const m = o as THREE.Mesh
      if (m.geometry) m.geometry.dispose()
      if (m.material) (m.material as THREE.Material).dispose()
    })
  }

  // Click-to-measure: pick two points on the reconstructed surface and read the straight-line
  // distance between them (in scene units — relative depth has no metric scale; a true-metres
  // reading would need a metric-depth model, deferred). Distinguishes a click from an orbit-drag.
  function onPointerDown(e: PointerEvent) { downXY = [e.clientX, e.clientY] }
  function onPointerUp(e: PointerEvent) {
    const d = downXY; downXY = null
    if (!measure || !d) return
    if (Math.hypot(e.clientX - d[0], e.clientY - d[1]) > 5) return   // was a drag (orbit), not a pick
    measurePick(e.clientX, e.clientY)
  }
  function measurePick(clientX: number, clientY: number) {
    if (!renderer || !camera || !mesh) return
    const rect = renderer.domElement.getBoundingClientRect()
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1)
    raycaster.setFromCamera(ndc, camera)
    const targets: THREE.Object3D[] = [mesh]; if (bgMesh) targets.push(bgMesh)
    const hit = raycaster.intersectObjects(targets, false)[0]
    if (!hit) return
    if (measurePts.length >= 2) { measurePts = []; measureDist = null }   // third pick starts fresh
    measurePts.push(hit.point.clone())
    drawMeasure()
  }
  function drawMeasure() {
    clearMeasureObjs()
    if (!scene) return
    measureGroup = new THREE.Group()
    for (const p of measurePts) {
      const s = new THREE.Mesh(new THREE.SphereGeometry(0.06, 12, 12), new THREE.MeshBasicMaterial({ color: 0x35e0ff, depthTest: false }))
      s.position.copy(p); s.renderOrder = 998; measureGroup.add(s)
    }
    if (measurePts.length === 2) {
      const [a, b] = measurePts
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]),
        new THREE.LineBasicMaterial({ color: 0x35e0ff, depthTest: false }))
      line.renderOrder = 998; measureGroup.add(line)
      measureDist = a.distanceTo(b)
      const lbl = makeMarker(`${measureDist.toFixed(2)} u`, '#35e0ff')
      lbl.position.copy(a.clone().lerp(b, 0.5)); measureGroup.add(lbl)
    }
    scene.add(measureGroup)
  }
  function clearMeasureObjs() {
    if (measureGroup && scene) {
      scene.remove(measureGroup)
      measureGroup.traverse((o) => {
        const m = o as THREE.Mesh & THREE.Line & THREE.Sprite
        if (m.geometry) m.geometry.dispose()
        const mat = m.material as THREE.Material & { map?: THREE.Texture }
        if (mat) { mat.map?.dispose?.(); mat.dispose() }
      })
    }
    measureGroup = null
  }
  function clearMeasure() { measurePts = []; measureDist = null; clearMeasureObjs() }
  function toggleMeasure() { measure = !measure; sfx('click'); if (!measure) clearMeasure() }

  function toggleAuto() {
    auto = !auto
    sfx('click')
    if (auto) autoTimer = setInterval(() => loadScene(false), 4000)
    else { if (autoTimer) { clearInterval(autoTimer); autoTimer = null } prevDisp = null }
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
    if (renderer) { renderer.domElement.removeEventListener('pointerdown', onPointerDown); renderer.domElement.removeEventListener('pointerup', onPointerUp) }
    clearMeasure()
    controls?.dispose()
    if (mesh) { mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose() }
    if (bgMesh) { bgMesh.geometry.dispose(); (bgMesh.material as THREE.Material).dispose() }
    if (markers) disposeGroup(markers)
    if (shadows) disposeShadows()
    if (sky) { sky.geometry.dispose(); (sky.material as THREE.Material).dispose() }
    fgTex?.dispose()
    shadowTex?.dispose()
    composer?.dispose()
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
    <button class="ref caps" class:on={measure} onclick={toggleMeasure}>⟺ MEASURE</button>
    <button class="ref caps" class:on={auto} onclick={toggleAuto}>{auto ? '● LIVE' : '○ LIVE'}</button>
    <button class="ref caps" onclick={() => loadScene(true)}>↻ RECAPTURE</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  <div class="stage" bind:this={host}></div>

  {#if loading}
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
    <div class="minimap">
      <span class="mmlabel caps">◹ TOP-DOWN</span>
      <canvas bind:this={mapCv} width="184" height="150"></canvas>
    </div>
    {#if measure}
      <div class="hint caps meas">⟺ MEASURE · CLICK TWO POINTS ON THE SURFACE{#if measureDist !== null} · <b>{measureDist.toFixed(2)} UNITS</b>{/if}</div>
    {:else}
      <div class="hint caps">DRAG TO ORBIT · SCROLL TO ZOOM · RIGHT-DRAG TO PAN</div>
    {/if}
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
  .uahead { color: var(--scarlet); font-size: 12px; letter-spacing: 0.2em; }
  .uasub { color: var(--ink-dim); font-size: 9px; letter-spacing: 0.06em; text-transform: none; }
  .hint { position: absolute; bottom: 16px; left: 0; right: 0; text-align: center; color: var(--ink-ghost);
    font-size: 8px; letter-spacing: 0.2em; pointer-events: none; }
  .hint.meas { color: var(--cyan); } .hint b { color: #eaf2f6; }
  .minimap { position: absolute; left: 16px; bottom: 16px; padding: 6px; border: 1px solid var(--hairline);
    background: rgba(4,7,10,0.55); backdrop-filter: blur(2px); pointer-events: none; }
  .minimap canvas { display: block; }
  .mmlabel { display: block; color: var(--cyan); font-size: 7px; letter-spacing: 0.18em; margin-bottom: 4px; }
</style>
