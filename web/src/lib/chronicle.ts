// OVERSEER Chronoscape recorder. Accumulates a multi-minute, downsampled trail of every tracked
// subject's ground-contact point from the live detection stream, so the 3D Spatial view can replay
// the scene's recent history in spacetime (4D: the static reconstructed ground + time). Runs purely
// on the frontend off the existing detection WS stream, so it adds no backend load. The subscription
// lives for the app's lifetime, so history keeps accumulating whether or not the 3D view is open.
import { writable } from 'svelte/store'
import { detections } from './stores'
import type { Detection } from './types'

export interface ChronSample { t: number; x: number; y: number }   // t = performance.now() ms; x,y normalised foot point
export interface ChronTrack { id: string; cls: string; klass: string; last: number; samples: ChronSample[] }

const WINDOW_MS = 5 * 60 * 1000    // retain the last 5 minutes of movement
const MIN_STEP_MS = 250            // downsample: at most one sample per subject per 250 ms
const MAX_SAMPLES = 24000          // hard memory guard across all subjects
const MAX_PER_TRACK = 1400

const store = new Map<string, ChronTrack>()

// Lightweight, reactive summary for badges (the heavy sample data is pulled via chronSnapshot()).
export const chronStats = writable<{ spanMs: number; tracks: number; samples: number }>({ spanMs: 0, tracks: 0, samples: 0 })

function record(dets: Detection[]) {
  const now = performance.now()
  for (const d of dets) {
    if (d.cls !== 'person' && d.cls !== 'vehicle' && d.cls !== 'animal') continue   // movers only
    const x = d.bbox[0] + d.bbox[2] / 2
    const y = d.bbox[1] + d.bbox[3]                 // foot / ground contact
    let e = store.get(d.id)
    if (!e) { e = { id: d.id, cls: d.cls, klass: d.klass, last: now, samples: [] }; store.set(d.id, e) }
    e.last = now; e.klass = d.klass
    const prev = e.samples[e.samples.length - 1]
    if (!prev || now - prev.t >= MIN_STEP_MS) {
      e.samples.push({ t: now, x, y })
      if (e.samples.length > MAX_PER_TRACK) e.samples.shift()
    }
  }
  // Age out samples past the window, then drop empty tracks.
  let total = 0
  for (const [id, e] of store) {
    while (e.samples.length && now - e.samples[0].t > WINDOW_MS) e.samples.shift()
    if (!e.samples.length) { store.delete(id); continue }
    total += e.samples.length
  }
  // Hard cap: in a very busy scene, shed the globally-oldest samples until under budget.
  while (total > MAX_SAMPLES) {
    let oldest: ChronTrack | null = null
    for (const e of store.values()) if (e.samples.length && (!oldest || e.samples[0].t < oldest.samples[0].t)) oldest = e
    if (!oldest) break
    oldest.samples.shift(); total--
    if (!oldest.samples.length) store.delete(oldest.id)
  }
  chronStats.set({ spanMs: spanMs(), tracks: store.size, samples: total })
}

function spanMs(): number {
  let start = Infinity, end = -Infinity
  for (const e of store.values()) {
    const s = e.samples
    if (!s.length) continue
    if (s[0].t < start) start = s[0].t
    if (s[s.length - 1].t > end) end = s[s.length - 1].t
  }
  return isFinite(start) ? end - start : 0
}

// A snapshot of the recorded history (shallow-cloned track list; sample arrays are shared read-only).
export function chronSnapshot(): ChronTrack[] {
  return [...store.values()].map((e) => ({ ...e, samples: e.samples }))
}

// The [start, end] timestamp bounds of everything recorded (performance.now() ms).
export function chronSpan(): { start: number; end: number } {
  let start = Infinity, end = -Infinity
  for (const e of store.values()) for (const s of e.samples) { if (s.t < start) start = s.t; if (s.t > end) end = s.t }
  if (!isFinite(start)) { const n = performance.now(); return { start: n, end: n } }
  return { start, end }
}

detections.subscribe((d) => record(d))
