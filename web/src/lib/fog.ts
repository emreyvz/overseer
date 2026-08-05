// OVERSEER — FOG OF WAR client engine.
//
// Keeps the observability field for the active camera warm while the overlay is on, and nowhere
// near warm when it is off. The backend build costs a depth pass, so this refreshes on a slow
// cadence (the geometry only changes when something large moves) and never polls in the
// background.
import { get } from 'svelte/store'
import { activeCam, coverage, modules, blindSpots } from './stores'
import { api } from './api'
import type { BlindSpot, Coverage, DoriTask } from './types'
import { SIM } from './sim'

const REFRESH_MS = 8000

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false
let lastKey = ''

/** Operator-chosen DORI task + target height. Persisted: it is a site property, not a session one. */
function persisted<T extends string | number>(key: string, def: T): T {
  try {
    const v = localStorage.getItem(key)
    if (v !== null) return (typeof def === 'number' ? (Number(v) as T) : (v as T))
  } catch { /* private mode */ }
  return def
}
export let fogTask: DoriTask = persisted<DoriTask>('overseer.fog.task', 'recognise')
export let fogHeight: number = persisted<number>('overseer.fog.height', 1.7)

export function setFogTask(t: DoriTask) {
  fogTask = t
  try { localStorage.setItem('overseer.fog.task', t) } catch { /* ignore */ }
  refresh(true)
}
export function setFogHeight(h: number) {
  fogHeight = h
  try { localStorage.setItem('overseer.fog.height', String(h)) } catch { /* ignore */ }
  refresh(true)
}

const fogOn = () => !!get(modules).find((m) => m.key === 'unseen')?.on

export async function refresh(force = false): Promise<void> {
  const cam = get(activeCam)
  if (!cam) { coverage.set(null); return }
  if (SIM) { coverage.set(simCoverage(cam)); blindSpots.set(simSpots()); return }
  if (inflight) return
  const key = `${cam}|${fogTask}|${fogHeight}`
  if (!force && key === lastKey && get(coverage)) {
    // still refresh periodically: shadows move even when the settings do not
  }
  inflight = true
  try {
    const r = await api.coverage(cam, fogTask, fogHeight)
    coverage.set(r.coverage ?? null)
    lastKey = key
    if (r.coverage) {
      const s = await api.blindSpots(cam).catch(() => ({ spots: [] as BlindSpot[] }))
      blindSpots.set(s.spots ?? [])
    }
  } catch {
    coverage.set(null)          // offline: the overlay shows its own empty state
  } finally {
    inflight = false
  }
}

/** Start/stop with the module toggle so a camera nobody is inspecting costs nothing. */
export function startFogEngine() {
  const tick = () => { if (fogOn()) refresh() }
  modules.subscribe(() => {
    if (fogOn() && timer === undefined) {
      refresh(true)
      timer = setInterval(tick, REFRESH_MS)
    } else if (!fogOn() && timer !== undefined) {
      clearInterval(timer); timer = undefined
      coverage.set(null); blindSpots.set([])
    }
  })
  activeCam.subscribe(() => { coverage.set(null); blindSpots.set([]); if (fogOn()) refresh(true) })
}

// ── derived helpers used by the overlay and the screen ─────────────────────────────────────

/** Cell index -> normalized rect, for painting the grain scrim. */
export function cellRect(cov: Coverage, idx: number): [number, number, number, number] {
  const [gw, gh] = cov.grid
  const cx = idx % gw, cy = Math.floor(idx / gw)
  return [cx / gw, cy / gh, 1 / gw, 1 / gh]
}

/** The band an operator is currently asking for, so the ladder can highlight it. */
export function activeBand(cov: Coverage) {
  return cov.bands.find((b) => b.task === cov.task) ?? null
}

/** Human sentence for a hovered DORI band. Concrete numbers beat adjectives. */
export function bandCaption(cov: Coverage, task: DoriTask): string {
  const b = cov.bands.find((x) => x.task === task)
  if (!b) return ''
  const verb = { detect: 'DETECT', observe: 'OBSERVE', recognise: 'RECOGNISE', identify: 'IDENTIFY' }[task]
  return `${verb} REACHES ~${b.range_m} M · BEYOND THAT A TARGET IS UNDER ${b.px_per_m} PX PER METRE`
}

// ── SIM fixtures ────────────────────────────────────────────────────────────────────────────
// Hand-authored rather than computed: a fixture exists so the UI can be built and screenshotted
// without a backend, and duplicating the ground model in TypeScript would be a second source of
// truth for geometry that already has one.
function simCoverage(cam: string): Coverage {
  const gw = 48, gh = 27
  const unseen: number[] = []
  for (let cy = 0; cy < gh; cy++) {
    for (let cx = 0; cx < gw; cx++) {
      const ny = (cy + 0.5) / gh, nx = (cx + 0.5) / gw
      let u = 0
      if (ny < 0.30) u = 1                                   // above the horizon: not ground
      else u = Math.max(0, Math.min(1, (0.62 - ny) * 2.4))   // resolution falls off with range
      if (nx > 0.16 && nx < 0.30 && ny > 0.33 && ny < 0.66) u = Math.max(u, 0.92)  // the skip
      if (nx > 0.70 && nx < 0.80 && ny > 0.36 && ny < 0.58) u = Math.max(u, 0.88)  // a van
      if (nx < 0.10 && ny > 0.7) u = Math.max(u, 0.55)                             // dark corner
      unseen.push(Number(u.toFixed(3)))
    }
  }
  return {
    cam, sid: cam, task: fogTask, target_height_m: fogHeight,
    percent: 71.4, fov_deg: 60, grid: [gw, gh], unseen, cells_m2: 2.6,
    shadows: [
      { id: 0, polygon: [[0.16, 0.33], [0.30, 0.33], [0.30, 0.66], [0.16, 0.66]], persistent: true },
      { id: 1, polygon: [[0.70, 0.36], [0.80, 0.36], [0.80, 0.58], [0.70, 0.58]], persistent: false },
    ],
    bands: [
      { task: 'identify', px_per_m: 250, range_m: 6.7, y: 0.83 },
      { task: 'recognise', px_per_m: 125, range_m: 13.3, y: 0.62 },
      { task: 'observe', px_per_m: 63, range_m: 26.4, y: 0.46 },
      { task: 'detect', px_per_m: 25, range_m: 66.5, y: 0.35 },
    ],
    scale_estimated: true,
    ts: Date.now(),
  }
}

function simSpots(): BlindSpot[] {
  const now = Date.now() / 1000
  return [
    {
      id: 1, kind: 'occlusion', name: 'MID LEFT', persistent: true,
      polygon: [[0.16, 0.33], [0.30, 0.33], [0.30, 0.66], [0.16, 0.66]],
      area_m2: 14.2, first_seen: now - 6 * 86400, last_seen: now, events: 3,
      channels: { geometric: 1.0, optical: 0.2, radiometric: 0.0, empirical: 0.31 },
      remedies: [
        { text: 'MOVE OR REMOVE THE OBJECT AT MID LEFT', recovers_m2: 14.2 },
        { text: 'A SECOND CAMERA OPPOSITE MID LEFT WOULD SEE BEHIND IT', recovers_m2: 14.2 },
        { text: 'RAISE THE CAMERA: A HIGHER MOUNT SHORTENS EVERY SHADOW', recovers_m2: null },
      ],
    },
    {
      id: 2, kind: 'radiometric', name: 'NEAR LEFT', persistent: true,
      polygon: [[0.02, 0.70], [0.12, 0.70], [0.12, 0.94], [0.02, 0.94]],
      area_m2: 5.1, first_seen: now - 22 * 86400, last_seen: now, events: 0,
      channels: { geometric: 0.0, optical: 0.0, radiometric: 0.78, empirical: 0.12 },
      remedies: [
        { text: 'ADD LIGHT AT NEAR LEFT', recovers_m2: 5.1 },
        { text: 'CHECK THE LENS FOR DIRT OR CONDENSATION', recovers_m2: null },
      ],
    },
    {
      id: 3, kind: 'empirical', name: 'MID RIGHT', persistent: true,
      polygon: [[0.70, 0.36], [0.80, 0.36], [0.80, 0.58], [0.70, 0.58]],
      area_m2: null, first_seen: now - 3 * 86400, last_seen: now, events: 7,
      channels: { geometric: 0.4, optical: 0.0, radiometric: 0.0, empirical: 0.42 },
      remedies: [
        { text: 'TRACKS KEEP ENDING AT MID RIGHT — LOOK FOR AN OCCLUDER OR A LIGHTING EDGE', recovers_m2: null },
      ],
    },
  ]
}
