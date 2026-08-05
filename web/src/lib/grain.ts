// OVERSEER — GRAIN client engine.
//
// Keeps the learned movement field for the active camera warm while the overlay is on. The field
// changes on the scale of days, so this refreshes slowly; the per-subject conformity arrives on
// the detection stream and needs nothing from here.
import { get } from 'svelte/store'
import { activeCam, grainStatus, grainTracks, modules } from './stores'
import { api } from './api'
import type { GrainCellStat, GrainStatus, GrainTrackRow } from './types'
import { SIM } from './sim'

const REFRESH_MS = 30000        // the grain of a place does not change in a minute

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

export let grainBucket: number | null = null    // null = whatever bucket we are in now
export let grainClass: 'person' | 'vehicle' = 'person'

const grainOn = () => !!get(modules).find((m) => m.key === 'grain')?.on

export async function refreshGrain(force = false): Promise<void> {
  const cam = get(activeCam)
  if (!cam) { grainStatus.set(null); return }
  if (SIM) { grainStatus.set(simField(cam)); grainTracks.set(simTracks()); return }
  if (inflight && !force) return
  inflight = true
  try {
    const r = await api.grain(cam, grainBucket ?? undefined, grainClass)
    grainStatus.set(r.status ?? null)
    const t = await api.grainTracks(cam, 120).catch(() => ({ tracks: [] as GrainTrackRow[] }))
    grainTracks.set(t.tracks ?? [])
  } catch {
    grainStatus.set(null)
  } finally {
    inflight = false
  }
}

export function setGrainBucket(b: number | null) { grainBucket = b; refreshGrain(true) }
export function setGrainClass(c: 'person' | 'vehicle') { grainClass = c; refreshGrain(true) }

export function startGrainEngine() {
  modules.subscribe(() => {
    if (grainOn() && timer === undefined) {
      refreshGrain(true)
      timer = setInterval(() => { if (grainOn()) refreshGrain() }, REFRESH_MS)
    } else if (!grainOn() && timer !== undefined) {
      clearInterval(timer); timer = undefined
      grainStatus.set(null)
    }
  })
  activeCam.subscribe(() => { grainStatus.set(null); grainTracks.set([]); if (grainOn()) refreshGrain(true) })
}

// ── streak field ────────────────────────────────────────────────────────────────────────────
// The visual identity of the whole feature: iron filings over a magnet. Each streak sits in a
// cell, points along that cell's modal heading, and is as bright as the cell is decided. Cells
// the model has barely seen render as static, unaligned specks, so ignorance LOOKS like
// ignorance rather than like a weak current.

export interface Streak {
  x: number; y: number          // normalized position
  a: number                     // heading, radians
  len: number                   // normalized length
  alpha: number
  phase: number                 // 0..1 drift position along its own direction
  mature: boolean
}

export type FieldDensity = 'sparse' | 'normal' | 'dense'
const PER_CELL: Record<FieldDensity, number> = { sparse: 1, normal: 2, dense: 4 }

/** Deterministic jitter, so the field does not shimmer between rebuilds. */
function hash(n: number): number {
  const x = Math.sin(n * 12.9898) * 43758.5453
  return x - Math.floor(x)
}

export function buildStreaks(st: GrainStatus, density: FieldDensity = 'normal'): Streak[] {
  const [gw, gh] = st.grid
  const per = PER_CELL[density]
  const out: Streak[] = []
  let maxSpeed = 0
  for (const c of st.cells) maxSpeed = Math.max(maxSpeed, c.modal_speed)
  const norm = maxSpeed > 0 ? maxSpeed : 1
  for (const c of st.cells) {
    const cx = (c.cell % gw) / gw, cy = Math.floor(c.cell / gw) / gh
    for (let k = 0; k < per; k++) {
      const h1 = hash(c.cell * 7 + k * 13), h2 = hash(c.cell * 31 + k * 17)
      const jitterA = c.mature ? (hash(c.cell + k) - 0.5) * (1 - c.concentration) * 2.2
                               : (hash(c.cell + k) - 0.5) * 6.283
      out.push({
        x: cx + h1 / gw,
        y: cy + h2 / gh,
        a: c.modal_heading + jitterA,
        len: 0.006 + 0.016 * Math.min(1, c.modal_speed / norm),
        // an undecided cell is dim; a decided one is legible. Never bright: this is background
        // structure, not an alarm.
        alpha: c.mature ? 0.10 + 0.24 * c.concentration : 0.06,
        phase: hash(c.cell * 3 + k),
        mature: c.mature,
      })
    }
  }
  return out
}

/** Cell index at a normalized point, for the mute tool and the cell inspector. */
export function cellAt(st: GrainStatus, nx: number, ny: number): number {
  const [gw, gh] = st.grid
  const cx = Math.min(gw - 1, Math.max(0, Math.floor(nx * gw)))
  const cy = Math.min(gh - 1, Math.max(0, Math.floor(ny * gh)))
  return cy * gw + cx
}

export function cellStat(st: GrainStatus, cell: number): GrainCellStat | null {
  return st.cells.find((c) => c.cell === cell) ?? null
}

export const FACTOR_LABEL: Record<string, string> = {
  path: 'PATH', speed: 'SPEED', heading: 'HEADING', dwell: 'DWELL', order: 'ORDER',
}

// ── SIM fixtures ────────────────────────────────────────────────────────────────────────────
function simField(cam: string): GrainStatus {
  const gw = 48, gh = 27
  const cells: GrainCellStat[] = []
  for (let cy = 0; cy < gh; cy++) {
    for (let cx = 0; cx < gw; cx++) {
      const ny = (cy + 0.5) / gh, nx = (cx + 0.5) / gw
      if (ny < 0.42) continue                                    // above the walkable ground
      // a strong left-to-right walkway, an eddy at the smoking area, a still patch by the bins
      let heading = 0, conc = 0.9, speed = 0.09, n = 300
      if (ny > 0.60 && ny < 0.74) { heading = 0; conc = 0.95; speed = 0.10; n = 900 }
      else if (nx > 0.62 && nx < 0.76 && ny > 0.75) { heading = Math.PI * 0.6; conc = 0.25; speed = 0.03; n = 140 }
      else if (nx > 0.16 && nx < 0.30 && ny > 0.78) { heading = 0; conc = 0.05; speed = 0.004; n = 18 }
      else { heading = Math.PI * (nx < 0.5 ? 0.1 : -0.1); conc = 0.45; speed = 0.06; n = 90 }
      const rose = Array.from({ length: 16 }, (_, i) => {
        const a = (i + 0.5) * (Math.PI * 2 / 16)
        return Math.max(0, Math.cos(a - heading)) ** (2 + conc * 12)
      })
      const tot = rose.reduce((a, b) => a + b, 0) || 1
      cells.push({
        cell: cy * gw + cx, n,
        heading: rose.map((v) => v / tot),
        speed: Array.from({ length: 12 }, (_, i) => Math.exp(-((i - 6) ** 2) / 8)),
        modal_heading: heading, modal_speed: speed, concentration: conc,
        mature: n >= 40,
      })
    }
  }
  return {
    cam, tracks: 12410, days: 18, mature: true, maturity: 1,
    grid: [gw, gh], cells, bucket: 3,
    buckets: ['NIGHT', 'DAWN', 'MORNING', 'MIDDAY', 'AFTERNOON', 'DUSK'],
  }
}

function simTracks(): GrainTrackRow[] {
  const now = Date.now()
  const line = (n: number, f: (i: number) => [number, number]): [number, number][] =>
    Array.from({ length: n }, (_, i) => f(i / (n - 1)))
  return [
    {
      id: 901, det_id: 'TK_009.44', start_ts: now - 240000, end_ts: now - 198000,
      percentile: 0.3, state: 'unusual',
      factors: { path: 1.2, speed: 46, heading: 8.1, dwell: 0.4 },
      why: 'Stood still far longer than anyone normally does here, and took a route this place almost never sees.',
      path: line(24, (u) => [0.18 + u * 0.34, 0.68 - u * 0.06]),
    },
    {
      id: 900, det_id: 'TK_009.41', start_ts: now - 900000, end_ts: now - 880000,
      percentile: 62.4, state: 'ordinary', factors: { path: 71, speed: 55, heading: 88, dwell: 50 },
      why: '', path: line(24, (u) => [0.10 + u * 0.78, 0.70]),
    },
    {
      id: 899, det_id: 'TK_009.38', start_ts: now - 1800000, end_ts: now - 1780000,
      percentile: 50, state: 'unjudged', factors: { path: 50, speed: 50, heading: 50, dwell: 50 },
      why: '', path: line(18, (u) => [0.06 + u * 0.10, 0.90 - u * 0.04]),
    },
  ]
}
