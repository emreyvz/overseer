// Privacy-friendly occupancy (catalog 49). A pure headcount from the live detection
// set — how many people are in view right now, plus the session peak. No identity,
// no tracklet retention: just the number, so it is safe to show at all times.
import { derived, writable } from 'svelte/store'
import { detections } from './stores'

export const occupancy = derived(detections, (dets) => dets.filter((d) => d.cls === 'person').length)

// Session peak occupancy — updated as the live count rises.
export const occupancyPeak = writable(0)
occupancy.subscribe((n) => occupancyPeak.update((p) => (n > p ? n : p)))

// Occupancy band for an at-a-glance cue (neutral — red stays reserved for alarms).
export function occupancyBand(n: number): 'clear' | 'busy' | 'crowded' {
  if (n >= 12) return 'crowded'
  if (n >= 5) return 'busy'
  return 'clear'
}

// Rolling occupancy history for the live trend sparkline (catalog 38, instant graph).
const HISTORY = 40
export const occupancyHistory = writable<number[]>([])
occupancy.subscribe((n) =>
  occupancyHistory.update((h) => {
    const next = [...h, n]
    return next.length > HISTORY ? next.slice(next.length - HISTORY) : next
  }),
)

// Map a history series to an SVG polyline within a w×h box (newest on the right).
export function sparklinePoints(series: number[], w: number, h: number): string {
  if (series.length < 2) return ''
  const max = Math.max(1, ...series)
  const step = w / (series.length - 1)
  return series
    .map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`)
    .join(' ')
}
