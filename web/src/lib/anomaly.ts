// OVERSEER — lightweight auto-anomaly (feature 6). Learns a rolling person-count
// baseline (EMA) and flags deviations; also raises a weapon alert. Client-side.
import { get } from 'svelte/store'
import { detections, activeCam, pushAlert } from './stores'

let baseline = 0
let n = 0
let lastActivity = 0

export function startAnomalyEngine() {
  detections.subscribe((dets) => {
    const now = Date.now()
    const persons = dets.filter((d) => d.cls === 'person').length

    // learn baseline (warm up, then slow EMA)
    baseline = n < 30 ? (baseline * n + persons) / (n + 1) : baseline * 0.98 + persons * 0.02
    n += 1

    // deviation from learned normal
    if (n > 40 && persons > baseline * 2.5 + 3 && now - lastActivity > 15000) {
      lastActivity = now
      pushAlert({
        ts: now, severity: 'warning', type: 'ANOMALY · UNUSUAL ACTIVITY',
        summary: `Crowd ${persons} vs baseline ${baseline.toFixed(1)}`,
        cam: get(activeCam) ?? '', ack: false,
      })
    }
    // Weapon alerts are raised server-side now (with a cropped image of the weapon).
  })
}
