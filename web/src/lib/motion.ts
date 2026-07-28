// Client-side dead-reckoning so detection boxes track the object in real time instead of
// lagging a frame behind. The server pushes boxes at the analysis rate (with latency), and
// the old code snapped to the last position and CSS-tweened toward it — so a box always
// chased where the object WAS. Here each track's velocity is estimated from successive
// server positions (EMA-smoothed) and the box is extrapolated forward every animation
// frame, with a small lead to counter push latency and a hard cap so a stopped or
// swapped track never drifts away.
import { get, writable } from 'svelte/store'

import { detections } from './stores'
import type { Detection } from './types'

type Box = [number, number, number, number]
type Track = { bbox: Box; vx: number; vy: number; t: number; seen: number }

const tracks = new Map<string, Track>()

/** Detections with boxes extrapolated to "now", refreshed every animation frame. */
export const predictedDetections = writable<Detection[]>([])

const LEAD_MS = 80         // predict slightly ahead to cancel push latency
const MAX_EXTRAP_MS = 300  // never dead-reckon further than this past the last real fix
const VEL_EMA = 0.45       // velocity smoothing (higher = snappier, lower = steadier)
const MAX_SPEED = 0.004    // per-ms normalized speed clamp — rejects id-reuse / detector jumps

let started = false

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

const center = (b: Box): [number, number] => [b[0] + b[2] / 2, b[1] + b[3] / 2]
const clamp01 = (n: number): number => (n < 0 ? 0 : n > 1 ? 1 : n)

/** Fold a fresh server frame into the velocity model. */
function ingest(dets: Detection[]): void {
  const t = nowMs()
  const alive = new Set<string>()
  for (const d of dets) {
    alive.add(d.id)
    const prev = tracks.get(d.id)
    if (prev) {
      const dt = Math.max(16, t - prev.t)
      const [cx, cy] = center(d.bbox)
      const [pcx, pcy] = center(prev.bbox)
      let vx = (cx - pcx) / dt
      let vy = (cy - pcy) / dt
      if (Math.hypot(vx, vy) > MAX_SPEED) { vx = 0; vy = 0 } // implausible jump -> don't fling
      prev.vx = prev.vx * (1 - VEL_EMA) + vx * VEL_EMA
      prev.vy = prev.vy * (1 - VEL_EMA) + vy * VEL_EMA
      prev.bbox = d.bbox
      prev.t = t
      prev.seen = t
    } else {
      tracks.set(d.id, { bbox: d.bbox, vx: 0, vy: 0, t, seen: t })
    }
  }
  for (const [id, tr] of tracks) {
    if (!alive.has(id) && t - tr.seen > 600) tracks.delete(id)
  }
}

/** Extrapolated box for a track id, or the fallback if we have no motion model for it. */
export function predictedBox(id: string, fallback: Box): Box {
  const tr = tracks.get(id)
  if (!tr) return fallback
  const dt = Math.min(MAX_EXTRAP_MS, nowMs() - tr.t + LEAD_MS)
  const [x, y, w, h] = tr.bbox
  return [clamp01(x + tr.vx * dt), clamp01(y + tr.vy * dt), w, h]
}

function step(): void {
  const base = get(detections)
  predictedDetections.set(base.map((d) => ({ ...d, bbox: predictedBox(d.id, d.bbox) })))
  requestAnimationFrame(step)
}

/** Start the model: update velocities on every server push, extrapolate every frame. */
export function startMotion(): void {
  if (started || typeof window === 'undefined') return
  started = true
  detections.subscribe(ingest)
  requestAnimationFrame(step)
}

if (typeof window !== 'undefined') startMotion()
