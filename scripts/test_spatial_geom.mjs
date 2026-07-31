// scripts/test_spatial_geom.mjs
// Zero-dependency unit tests for the 3D SPATIAL placement math. Node has no bundler here and the
// logic lives inline in web/src/components/spatial/SpatialView.svelte (and mirrored in
// scripts/gl_verify_spatial.html), so these reimplement the SAME formulas and assert the invariants
// they must satisfy. If you change the placement math in SpatialView, update these to match.
//   run:  node scripts/test_spatial_geom.mjs

const ZNEAR = 1.0, ZFAR = 9.0, GAMMA = 1.6

// --- functions under test (verbatim formulas from SpatialView.svelte) ---
const zOf = (d) => ZNEAR + Math.pow(1 - d, GAMMA) * (ZFAR - ZNEAR)

// person: stand feet on the ground (Y=0) at the foot-pixel depth, height clamped to a human range
function placePerson({ dispAtFoot, pxf, cx, fx, fy, sh, h, bodyH }) {
  const Zc = zOf(dispAtFoot)
  const Xc = (pxf - cx) * Zc / fx
  const worldH = Math.max(1.2, Math.min(2.1, (sh * h) * Zc / fy))
  const s = Math.max(0.01, worldH / bodyH)
  return { Xc, Zc, worldH, scale: s, posY: -0 * s }   // feet ymin at 0 -> posY places ymin on ground
}

// vehicle: low-poly car on the ground at the body depth (floored at 3m), width clamped to a car
function placeVehicle({ depth, sw, sh, w, h, cx, cxImg, fx }) {
  const Zc = Math.max(3.0, zOf(Math.max(0.015, depth)))
  const Xc = (cx * w - cxImg) * Zc / fx
  const worldW = Math.max(1.5, Math.min(3.2, (sw * w) * Zc / fx))
  const rotY = (sw * w) / (sh * h) > 1.7 ? Math.PI / 2 : 0
  return { Xc, Zc, worldW, rotY }
}

// grazing cull: |normal . viewDir| < graze  -> triangle is edge-on (a depth "drip") -> cull
function grazeCull(pa, pb, pc, graze) {
  const e1 = [pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]]
  const e2 = [pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]]
  let n = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
  const nl = Math.hypot(...n) || 1; n = n.map((v) => v / nl)
  const c = [(pa[0] + pb[0] + pc[0]) / 3, (pa[1] + pb[1] + pc[1]) / 3, (pa[2] + pb[2] + pc[2]) / 3]
  const cl = Math.hypot(...c) || 1
  return Math.abs((n[0] * c[0] + n[1] * c[1] + n[2] * c[2]) / cl) < graze
}

// --- tiny test harness ---
let passed = 0, failed = 0
const approx = (a, b, eps = 1e-6) => Math.abs(a - b) < eps
function check(name, cond) {
  if (cond) { passed++; console.log(`  ok   ${name}`) }
  else { failed++; console.log(`  FAIL ${name}`) }
}

// 1) zOf: disparity 1 (nearest) -> ZNEAR, 0 (farthest) -> ZFAR, and monotonic decreasing
check('zOf(1) == ZNEAR', approx(zOf(1), ZNEAR))
check('zOf(0) == ZFAR', approx(zOf(0), ZFAR))
check('zOf monotonic decreasing in disparity', zOf(0.2) > zOf(0.4) && zOf(0.4) > zOf(0.8))

// 2) person: feet on ground, height clamped to human range, farther persons scale sanely
const near = placePerson({ dispAtFoot: 0.8, pxf: 100, cx: 96, fx: 200, fy: 200, sh: 0.4, h: 216, bodyH: 1.7 })
const far = placePerson({ dispAtFoot: 0.1, pxf: 100, cx: 96, fx: 200, fy: 200, sh: 0.12, h: 216, bodyH: 1.7 })
check('person worldH within [1.2, 2.1]', near.worldH >= 1.2 && near.worldH <= 2.1)
check('person feet placed on ground (posY == 0)', approx(near.posY, 0))
check('farther person is placed at larger Z', far.Zc > near.Zc)
check('person scale is positive', near.scale > 0 && far.scale > 0)

// 3) vehicle: min depth floor 3m, width clamped to a car, wide box -> side-on
const carNear = placeVehicle({ depth: 0.95, sw: 0.24, sh: 0.28, w: 120, h: 67, cx: 0.2, cxImg: 60, fx: 104 })
const carFar = placeVehicle({ depth: 0.08, sw: 0.02, sh: 0.03, w: 120, h: 67, cx: 0.85, cxImg: 60, fx: 104 })
check('vehicle Z floored at 3m', carNear.Zc >= 3.0)
check('vehicle width clamped to [1.5, 3.2]', carNear.worldW >= 1.5 && carNear.worldW <= 3.2 && carFar.worldW >= 1.5)
check('wide box (aspect>1.7) -> side-on rotation', placeVehicle({ depth: 0.5, sw: 0.3, sh: 0.08, w: 120, h: 67, cx: 0.5, cxImg: 60, fx: 104 }).rotY === Math.PI / 2)
check('tallish box -> facing camera (no rotation)', placeVehicle({ depth: 0.5, sw: 0.1, sh: 0.12, w: 120, h: 67, cx: 0.5, cxImg: 60, fx: 104 }).rotY === 0)

// 4) grazing cull: an edge-on triangle (normal perpendicular to view) is culled; a camera-facing one is kept
// camera-facing surface at z=-5 (normal ~ +z, view ~ -z -> |dot|~1)
const facing = [[-1, 0, -5], [1, 0, -5], [0, 1, -5]]
// edge-on "drip": a thin near->far vertical sliver (normal ~ perpendicular to the view ray)
const drip = [[0, 0, -2], [0, 0.02, -2], [0, 1, -8]]
check('camera-facing triangle is kept (not culled)', grazeCull(facing[0], facing[1], facing[2], 0.2) === false)
check('edge-on drip triangle is culled', grazeCull(drip[0], drip[1], drip[2], 0.2) === true)
check('graze=0 disables culling', grazeCull(drip[0], drip[1], drip[2], 0) === false)

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
