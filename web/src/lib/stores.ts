// OVERSEER — shared reactive state (svelte stores).
import { writable } from 'svelte/store'
import type {
  Alert, Camera, ConnState, Detection, FrameMeta, Metric, OOITarget, SystemStat, TimelineEvent,
} from './types'
import { MODULES } from './lexicon'
import { fpRate } from './feedback'

export type Mode = 'pov' | 'montage' | 'topology' | 'forensic' | 'archive' | 'case' | 'roster'

export const booted = writable(false)
export type Stage = 'boot' | 'select' | 'live'
export const stage = writable<Stage>('boot')
export const pickerView = writable<'map' | 'grid'>('map')  // landing: world map by default (item 15)
export const mode = writable<Mode>('pov')

export const conn = writable<ConnState>('offline')
export const frame = writable<FrameMeta>({ fps: 0, res: [0, 0], inferenceMs: 0, brightness: 0, motionPct: 0 })
export const detections = writable<Detection[]>([])
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

export const modules = writable(MODULES.map((m) => ({ ...m, on: !['heatmap', 'motion'].includes(m.key) })))

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
export const graphOpen = writable(false)                     // social / relationship graph screen
export const mergeOpen = writable(false)                     // identity merge center
export const suggestionsOpen = writable(false)               // smart suggestions advisor screen
export const spatialOpen = writable<string | null>(null)     // 3D spatial scene for a camera (source id)
export const dossierOpen = writable(false)  // stationary editor panel for the selected tracklet

// Switching cameras clears the previous camera's transient panels (selection,
// dossier, enroll) — they don't belong to the new feed.
let _panelCam: string | null = null
activeCam.subscribe((id) => {
  if (_panelCam !== null && id !== _panelCam) {
    selectedDetection.set(null)
    dossierOpen.set(false)
    enrollOpen.set(null)
  }
  _panelCam = id
})

export const railsHover = writable(false)
export const timelineOpen = writable(false)
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

/** Flash a status banner for `ms`. */
export function flashBanner(text: string, alarm = false, ms = 1600) {
  banner.set({ text, alarm })
  if (ms > 0) setTimeout(() => banner.set(null), ms)
}
