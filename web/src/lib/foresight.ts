// OVERSEER foresight engine. From the live detection stream it maintains a short motion
// history per tracklet, estimates each subject's ground-plane velocity, and predicts where it
// will be a few seconds ahead. Two visual features consume this:
//   · GhostLayer: translucent "ghosts" of each subject's near-future position on the video.
//   · TacticalMap: a top-down radar that also draws those predictions as ghost blips ahead
//                  of the real contact.
// It runs entirely on the frontend off the existing detection WS stream (no backend load).
import { writable } from 'svelte/store'
import { detections } from './stores'
import type { Detection } from './types'

export interface FTrack {
  id: string
  cls: string
  klass: string
  alarm: boolean
  gx: number; gy: number     // ground contact (bbox bottom-centre), normalised [0..1]
  cx: number; cy: number     // box centre, normalised
  w: number; h: number       // box size, normalised
  vx: number; vy: number     // ground-point velocity, normalised units / second
  speed: number              // |v|
  hist: { x: number; y: number }[]  // recent ground points (trail), newest last
}

export interface Encounter { a: FTrack; b: FTrack; x: number; y: number; t: number }

const HIST_MS = 1500       // how much motion history to retain per track
const VEL_WIN = 380        // velocity is measured over this window (ms)
const STALE_MS = 900       // drop a track unseen for this long
const MAX_TRAIL = 26
const JITTER = 0.014       // below this speed (norm/s) we treat the subject as stationary

interface Sample { t: number; gx: number; gy: number }
const store = new Map<string, { samples: Sample[]; det: Detection; last: number }>()

// Live tracks with velocity + trail. Updated on every detections frame.
export const tracks = writable<FTrack[]>([])

const isAlarm = (d: Detection): boolean => d.klass === 'WEAPON' || d.severity === 'critical'
const clamp01 = (v: number): number => (v < 0 ? 0 : v > 1 ? 1 : v)

function update(dets: Detection[]) {
  const now = performance.now()
  for (const d of dets) {
    const gx = d.bbox[0] + d.bbox[2] / 2
    const gy = d.bbox[1] + d.bbox[3]              // foot / ground contact
    let e = store.get(d.id)
    if (!e) { e = { samples: [], det: d, last: now }; store.set(d.id, e) }
    e.det = d; e.last = now
    e.samples.push({ t: now, gx, gy })
    while (e.samples.length && now - e.samples[0].t > HIST_MS) e.samples.shift()
  }
  for (const [id, e] of store) if (now - e.last > STALE_MS) store.delete(id)

  const out: FTrack[] = []
  for (const [id, e] of store) {
    const s = e.samples
    if (!s.length) continue
    const cur = s[s.length - 1]
    // reference sample ~VEL_WIN ago (fall back to the oldest we have)
    let ref = s[0]
    for (let i = s.length - 1; i >= 0; i--) { if (cur.t - s[i].t >= VEL_WIN) { ref = s[i]; break } }
    const dt = Math.max(1e-3, (cur.t - ref.t) / 1000)
    let vx = (cur.gx - ref.gx) / dt
    let vy = (cur.gy - ref.gy) / dt
    let speed = Math.hypot(vx, vy)
    if (speed < JITTER) { vx = 0; vy = 0; speed = 0 }   // kill idle jitter
    const d = e.det
    out.push({
      id, cls: d.cls, klass: d.klass, alarm: isAlarm(d),
      gx: cur.gx, gy: cur.gy,
      cx: d.bbox[0] + d.bbox[2] / 2, cy: d.bbox[1] + d.bbox[3] / 2,
      w: d.bbox[2], h: d.bbox[3], vx, vy, speed,
      hist: s.slice(-MAX_TRAIL).map((p) => ({ x: p.gx, y: p.gy })),
    })
  }
  tracks.set(out)
}

// Where this track is predicted to be dt seconds from now (image-space, clamped to frame).
export function predict(t: FTrack, dt: number): { x: number; y: number } {
  return { x: clamp01(t.gx + t.vx * dt), y: clamp01(t.gy + t.vy * dt) }
}

// Pairs of moving subjects predicted to converge within `horizon` seconds: the closest point
// of approach, only when they are actually approaching (closer ahead than they are now).
export function encounters(list: FTrack[], horizon: number): Encounter[] {
  const out: Encounter[] = []
  const moving = list.filter((t) => t.speed > 0.02)
  for (let i = 0; i < moving.length; i++) {
    for (let j = i + 1; j < moving.length; j++) {
      const a = moving[i], b = moving[j]
      let best = Infinity, bt = 0, bx = 0, by = 0
      for (let s = 0.25; s <= horizon; s += 0.25) {
        const pa = predict(a, s), pb = predict(b, s)
        const d = Math.hypot(pa.x - pb.x, pa.y - pb.y)
        if (d < best) { best = d; bt = s; bx = (pa.x + pb.x) / 2; by = (pa.y + pb.y) / 2 }
      }
      const nowGap = Math.hypot(a.gx - b.gx, a.gy - b.gy)
      if (best < 0.07 && best < nowGap - 0.02) out.push({ a, b, x: bx, y: by, t: bt })
    }
  }
  return out
}

// Start the engine once, at import. The subscription lives for the app's lifetime so history
// stays continuous across POV mounts/unmounts.
detections.subscribe((d) => update(d))
