// Smooth, real-time box motion. The server pushes detection boxes at the analysis rate
// (sparse, with latency); rendered raw they either lag behind or jump a step per update.
//
// Every animation frame each box's rendered position is eased toward the latest measured
// position with a critically-damped low-pass filter (exponential smoothing), plus a small
// velocity feed-forward so the box sits ON the object (centred) instead of trailing it.
// Because the filter always glides toward a moving target, motion is continuous and
// sub-pixel — no per-measurement steps — while a hard jump (track-id reuse) snaps instead
// of sliding a box across the frame.
import { get, writable } from 'svelte/store'

import { detections } from './stores'
import type { Detection } from './types'

type Box = [number, number, number, number]
type Track = { render: Box; target: Box; vx: number; vy: number; t: number; seen: number }

const tracks = new Map<string, Track>()

/** Detections with smoothed, latency-compensated boxes, refreshed every animation frame. */
export const predictedDetections = writable<Detection[]>([])

const TAU_MS = 70        // smoothing time constant (smaller = snappier, larger = smoother)
const LEAD_MS = 45       // velocity feed-forward to cancel push latency (kept small so the box sits
                         // ON the subject, not ahead of it)
const VEL_EMA = 0.35     // velocity smoothing
const MAX_SPEED = 0.004  // per-ms normalized speed clamp — rejects id-reuse / detector jumps
const SNAP_DIST = 0.25   // centre jump beyond this => teleport (don't glide across the frame)

let started = false
let lastFrame = 0

function nowMs(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

const center = (b: Box): [number, number] => [b[0] + b[2] / 2, b[1] + b[3] / 2]
const clamp01 = (n: number): number => (n < 0 ? 0 : n > 1 ? 1 : n)

/** Fold a fresh server frame into the motion model (updates targets + velocities). */
function ingest(dets: Detection[]): void {
  const t = nowMs()
  const alive = new Set<string>()
  for (const d of dets) {
    alive.add(d.id)
    const prev = tracks.get(d.id)
    if (prev) {
      const dt = Math.max(16, t - prev.t)
      const [cx, cy] = center(d.bbox)
      const [pcx, pcy] = center(prev.target)
      let vx = (cx - pcx) / dt
      let vy = (cy - pcy) / dt
      if (Math.hypot(vx, vy) > MAX_SPEED) { vx = 0; vy = 0 } // implausible jump -> no fling
      prev.vx = prev.vx * (1 - VEL_EMA) + vx * VEL_EMA
      prev.vy = prev.vy * (1 - VEL_EMA) + vy * VEL_EMA
      prev.target = d.bbox
      prev.t = t
      prev.seen = t
    } else {
      tracks.set(d.id, { render: d.bbox, target: d.bbox, vx: 0, vy: 0, t, seen: t })
    }
  }
  for (const [id, tr] of tracks) {
    if (!alive.has(id) && t - tr.seen > 600) tracks.delete(id)
  }
}

/** Current smoothed box for a track id (or the fallback if unknown). */
export function predictedBox(id: string, fallback: Box): Box {
  const tr = tracks.get(id)
  return tr ? ([tr.render[0], tr.render[1], tr.render[2], tr.render[3]] as Box) : fallback
}

function step(now: number): void {
  const dtFrame = lastFrame ? Math.min(64, now - lastFrame) : 16
  lastFrame = now
  const alpha = 1 - Math.exp(-dtFrame / TAU_MS)
  for (const tr of tracks.values()) {
    const tx = clamp01(tr.target[0] + tr.vx * LEAD_MS)
    const ty = clamp01(tr.target[1] + tr.vy * LEAD_MS)
    const tw = tr.target[2]
    const th = tr.target[3]
    const jump = Math.hypot(
      tr.render[0] + tr.render[2] / 2 - (tx + tw / 2),
      tr.render[1] + tr.render[3] / 2 - (ty + th / 2),
    )
    if (jump > SNAP_DIST) {
      tr.render = [tx, ty, tw, th]
    } else {
      tr.render = [
        tr.render[0] + (tx - tr.render[0]) * alpha,
        tr.render[1] + (ty - tr.render[1]) * alpha,
        tr.render[2] + (tw - tr.render[2]) * alpha,
        tr.render[3] + (th - tr.render[3]) * alpha,
      ]
    }
  }
  const base = get(detections)
  predictedDetections.set(base.map((d) => {
    const tr = tracks.get(d.id)
    return tr ? { ...d, bbox: [tr.render[0], tr.render[1], tr.render[2], tr.render[3]] as Box } : d
  }))
  requestAnimationFrame(step)
}

/** Start the model: update targets on every server push, smooth+publish every frame. */
export function startMotion(): void {
  if (started || typeof window === 'undefined') return
  started = true
  detections.subscribe(ingest)
  requestAnimationFrame(step)
}

if (typeof window !== 'undefined') startMotion()
