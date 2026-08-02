// OVERSEER — shared reactive state (svelte stores).
import { get, writable } from 'svelte/store'
import type {
  Alert, Camera, ConnState, Detection, FrameMeta, Metric, OOITarget, SystemStat, TimelineEvent,
} from './types'
import { MODULES } from './lexicon'
import { fpRate } from './feedback'
import { api } from './api'

export type Mode = 'pov' | 'montage' | 'topology' | 'forensic' | 'archive' | 'case' | 'roster'

export const booted = writable(false)
export type Stage = 'boot' | 'select' | 'live'
export const stage = writable<Stage>('boot')
export const pickerView = writable<'map' | 'grid'>('map')  // landing: world map by default (item 15)
export const mode = writable<Mode>('pov')

export const conn = writable<ConnState>('offline')
export const frame = writable<FrameMeta>({ fps: 0, res: [0, 0], inferenceMs: 0, brightness: 0, motionPct: 0 })
export const detections = writable<Detection[]>([])

// Track coasting: a tracked box that momentarily drops out of the detector (a swimmer going
// under, a car behind a pillar, motion blur) would otherwise blink off and on. We hold its
// last-known box for a short grace window and re-emit it as `coasting` (drawn faded) until it
// reappears or the window lapses — so tracking stays visually continuous. Only real ByteTrack
// ids are coasted (TK_<src>.<n>); per-frame recall extras (TK_<src>.x<n>) are not.
const COAST_MS = 700       // normal brief hold through a momentary detector drop
const XRAY_MS = 2600       // occlusion x-ray: hold a subject much longer while it is behind cover
const _clamp01 = (n: number) => (n < 0 ? 0 : n > 1 ? 1 : n)
const _coast = new Map<string, { det: Detection; ts: number; cx: number; cy: number; vx: number; vy: number }>()
const _stableTrack = (id: string) => /^TK_\d+\.\d+$/.test(id)
export function applyDetections(list: Detection[], now = Date.now()) {
  const seen = new Set<string>()
  for (const d of list) {
    if (!_stableTrack(d.id)) continue
    const cx = d.bbox[0] + d.bbox[2] / 2, cy = d.bbox[1] + d.bbox[3] / 2
    const prev = _coast.get(d.id)
    let vx = 0, vy = 0
    if (prev) {
      const dt = Math.max(50, now - prev.ts)
      vx = prev.vx * 0.6 + ((cx - prev.cx) / dt) * 0.4     // smoothed normalized velocity /ms
      vy = prev.vy * 0.6 + ((cy - prev.cy) / dt) * 0.4
    }
    _coast.set(d.id, { det: d, ts: now, cx, cy, vx, vy })
    seen.add(d.id)
  }
  const out = list.slice()
  const window = get(xrayOn) ? XRAY_MS : COAST_MS
  for (const [id, rec] of _coast) {
    if (seen.has(id)) continue
    const age = now - rec.ts
    if (age > window) { _coast.delete(id); continue }
    // extrapolate the box forward along its last velocity, so the ghost is where the subject
    // WOULD be behind cover, not frozen where it disappeared.
    const b = rec.det.bbox
    const ex = _clamp01(b[0] + rec.vx * age)
    const ey = _clamp01(b[1] + rec.vy * age)
    out.push({ ...rec.det, bbox: [ex, ey, b[2], b[3]], coasting: true, occluded: age > COAST_MS })
  }
  detections.set(out)
}
export function resetCoast() { _coast.clear() }

export const ooiTargets = writable<OOITarget[]>([])
export const objectRegister = writable(false)  // OOI draw tool overlay
export const petRegistry = writable(false)     // pet registry / find-my-pet overlay
export const forensicSeed = writable<string>('') // pre-seed a forensic search (e.g. find-my-pet)
export const storageScreen = writable(false)   // storage / recordings management overlay
export const alerts = writable<Alert[]>([])
export const timeline = writable<TimelineEvent[]>([])
export const metrics = writable<Metric[]>([])
export const system = writable<SystemStat>({ cpu: 0, gpu: null, ram: 0, storageGB: 0, rec: 'off', recActive: false })
export const cameras = writable<Camera[]>([])
export const activeCam = writable<string | null>(null)
// Digital PTZ (zoom/pan) applied to the live feed + detection overlay together.
export const povZoom = writable<{ zoom: number; x: number; y: number }>({ zoom: 1, x: 0, y: 0 })

// Optimistic source add/remove that survives the 2s heartbeat `cameras` broadcast,
// so the picker updates instantly instead of flickering or lagging behind the server.
const _pendingDel = new Set<string>()
let _optimistic: Camera[] = []
export function optimisticAddCamera(c: Camera) {
  _optimistic = [..._optimistic.filter((o) => o.id !== c.id), c]
  cameras.update((l) => (l.some((x) => x.id === c.id) ? l : [...l, c]))
}
export function optimisticRemoveCamera(id: string) {
  _pendingDel.add(id)
  _optimistic = _optimistic.filter((o) => o.id !== id)
  cameras.update((l) => l.filter((x) => x.id !== id))
}
export function applyServerCameras(list: Camera[]) {
  for (const id of [..._pendingDel]) if (!list.some((c) => c.id === id)) _pendingDel.delete(id)
  const merged = list.filter((c) => !_pendingDel.has(c.id))            // hide sources being deleted
  _optimistic = _optimistic.filter((o) => !list.some((c) => c.id === o.id || c.name === o.name))
  for (const o of _optimistic) if (!merged.some((c) => c.id === o.id)) merged.push(o) // keep unsynced adds
  cameras.set(merged)
}
export const camTrail = writable<string[]>([])  // recent observed-camera sequence (cross-camera flow route)

let _lastTrail: string | null = null
activeCam.subscribe((id) => {
  if (!id || id === _lastTrail) return
  _lastTrail = id
  camTrail.update((t) => (t[t.length - 1] === id ? t : [...t, id]).slice(-8))
})

// DETECTION-group modules that gate REAL backend load (persisted server-side, authority on
// boot). The rest are display-only and persist in localStorage alone.
const DETECTION_KEYS = ['motion', 'person', 'vehicle', 'animal', 'weapon', 'track']
const MODULES_LS = 'overseer.modules'

function seedModules() {
  let saved: Record<string, boolean> = {}
  try { saved = JSON.parse(localStorage.getItem(MODULES_LS) || '{}') } catch { saved = {} }
  return MODULES.map((m) => ({
    ...m,
    on: m.key in saved ? !!saved[m.key] : !['heatmap', 'motion', 'tactical', 'ghosts'].includes(m.key),
  }))
}
export const modules = writable(seedModules())

function saveModulesLocal() {
  try {
    const map = Object.fromEntries(get(modules).map((m) => [m.key, m.on]))
    localStorage.setItem(MODULES_LS, JSON.stringify(map))
  } catch { /* private mode / quota: non-fatal, backend still persists the class gates */ }
}

// Flip one module, persist locally, and for the DETECTION classes tell the backend so
// the disabled class immediately drops off its processing budget (and stays off next session).
export function toggleModule(key: string) {
  modules.update((list) => list.map((m) => (m.key === key ? { ...m, on: !m.on } : m)))
  saveModulesLocal()
  if (DETECTION_KEYS.includes(key)) {
    const filters = Object.fromEntries(
      get(modules).filter((m) => DETECTION_KEYS.includes(m.key)).map((m) => [m.key, m.on]),
    )
    api.setDetectionFilters(filters).catch(() => { /* offline: localStorage still carries it */ })
  }
}

// On boot, let the backend's persisted class gates win for the DETECTION group (they are the
// source of truth; they actually govern the server's load), then mirror into localStorage.
export async function hydrateModules() {
  try {
    const f = await api.detectionFilters()
    if (f && Object.keys(f).length) {
      modules.update((list) => list.map((m) => (m.key in f ? { ...m, on: !!f[m.key] } : m)))
      saveModulesLocal()
    }
  } catch { /* backend down: keep localStorage-seeded state */ }
}

// UI state
export const commandOpen = writable(false)
export const zoneEditor = writable(false)   // POV line/area drawing overlay (items 6,7)
export const alertRules = writable(false)    // alert-rule creator overlay (item 13)
export const selectedDetection = writable<Detection | null>(null)
// A located search/watchlist hit to spotlight on its camera: green pointer brackets so the
// operator immediately sees WHERE on the frame the match is when the camera opens.
export const matchHighlight = writable<{ camId: string; bbox: [number, number, number, number]; ts: number } | null>(null)
export const enrollOpen = writable<Detection | null>(null)  // detection being enrolled to the watchlist
export const watchlistOpen = writable(false)                 // watchlist browser screen
export const aiOpen = writable(false)                        // AI assistant console
export const investigateCase = writable<number | null>(null) // open case id in the investigation workspace
export const suggestionsOpen = writable(false)               // smart suggestions advisor screen
export const spatialOpen = writable<string | null>(null)     // 3D spatial scene for a camera (source id)
export const dossierOpen = writable(false)  // stationary editor panel for the selected tracklet
export const alertsScreen = writable(false)                  // cross-camera alerts board (all cameras)
export const operatorOpen = writable(false)                  // central AI Operator console (voice + text)
// Roster filter the AI Operator can drive (applied on mount AND live while the roster is open):
// class, free-text query, BOLO-only, and attribute filters (colour / vehicle subtype / height).
export const rosterInit = writable<
  { cls?: string; bolo?: boolean; query?: string; color?: string; subtype?: string; height?: string; sort?: string } | null
>(null)

// Switching cameras clears the previous camera's transient panels (selection,
// dossier, enroll) — they don't belong to the new feed.
let _panelCam: string | null = null
activeCam.subscribe((id) => {
  if (_panelCam !== null && id !== _panelCam) {
    selectedDetection.set(null)
    dossierOpen.set(false)
    enrollOpen.set(null)
    resetCoast()   // don't carry the old feed's coasted boxes onto the new camera
  }
  _panelCam = id
})

export const railsHover = writable(false)
export const timelineOpen = writable(false)
// Experiential "new way to see" features:
export const narrateOn = writable(false)   // live VLM narration of the active camera (spoken)
export const followOn = writable(false)    // follow-cam: digital PTZ keeps the locked subject centred
// The tracked target's current live id (updates on re-acquire) + whether it is momentarily lost,
// published by DetectionLayer so the follow-cam can ride re-acquisition and stop when truly lost.
export const followState = writable<{ id: string; lost: boolean } | null>(null)
export const xrayOn = writable(true)       // occlusion x-ray: hold + predict a subject behind cover
export const enhanceMode = writable(false)                  // live "enhance": box-select clarify tool active
export const enhanceResult = writable<string | null>(null)  // last enhanced crop (data URL)
export const muted = writable(false)
export const banner = writable<{ text: string; alarm: boolean } | null>(null)
export const shuttingDown = writable(false)  // exit animation gate (item 16)

// Glitch pulse (camera-switch / mode-change / critical). PostFx & transitions read this.
export const glitch = writable(false)
let glitchTimer: ReturnType<typeof setTimeout> | undefined
export function triggerGlitch(ms = 140) {
  glitch.set(true)
  clearTimeout(glitchTimer)
  glitchTimer = setTimeout(() => glitch.set(false), ms)
}

/** Push an alert to the top of the list (newest first), cap 200. */
// De-duplicate alerts: the same event (type+camera) won't drop again while it is
// still within the cooldown, even if the pipeline keeps re-triggering it.
const _alertSeen = new Map<string, number>()
let _threatSeq = 1041
const ALERT_DEDUP_MS = 90000
export function pushAlert(a: Alert) {
  const key = `${a.type}|${a.cam}`
  // Self-tuning (catalog 116): suppress a source the operator keeps marking false.
  const fp = fpRate(a.type, a.cam)
  if (fp.n >= 4 && fp.falseRate >= 0.75) return
  const last = _alertSeen.get(key) ?? -Infinity
  if (a.ts - last < ALERT_DEDUP_MS) {
    // Grouping (catalog 65): don't re-drop; bump the existing incident's repeat count.
    alerts.update((list) => list.map((x) => (`${x.type}|${x.cam}` === key ? { ...x, hits: (x.hits ?? 1) + 1, ts: a.ts } : x)))
    return
  }
  _alertSeen.set(key, a.ts)
  const withId: Alert = { ...a, threat: a.threat ?? `THR-${_threatSeq++}`, hits: 1 }
  alerts.update((list) => [withId, ...list].slice(0, 200))
}
export function pushEvent(e: TimelineEvent) {
  timeline.update((list) => [e, ...list].slice(0, 500))
}

/** Load persisted alert history from the backend and merge it into the live list (newest first),
 * so past alerts — with their snapshot + replayable clip — are visible after a restart / on load. */
export async function hydrateAlerts() {
  try {
    const rows = await api.alerts()
    alerts.update((live) => {
      const seen = new Set(live.map((a) => `${a.ts}|${a.type}|${a.cam}`))
      const hist = rows
        .filter((r) => !seen.has(`${r.ts}|${r.type}|${r.cam}`))
        .map((r): Alert => ({
          ts: r.ts, severity: r.severity as Alert['severity'], type: r.type, summary: r.summary,
          cam: r.cam, ack: true, snapshot: r.snapshot ?? undefined, clip: r.clip ?? undefined,
          threat: `THR-${Math.round(r.ts) % 100000}`, hits: 1,
        }))   // history is acknowledged (goes to RECENT, never re-features as an active incident)
      return [...live, ...hist].sort((a, b) => b.ts - a.ts).slice(0, 200)
    })
  } catch { /* offline — keep whatever the WS delivered */ }
}

/** Flash a status banner for `ms`. */
export function flashBanner(text: string, alarm = false, ms = 1600) {
  banner.set({ text, alarm })
  if (ms > 0) setTimeout(() => banner.set(null), ms)
}
