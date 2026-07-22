// OVERSEER — DEV-ONLY simulation. NOT a shipped demo mode.
// Enabled only with the `?sim=1` URL flag, for local visual development / screenshots
// when no backend is running. Real deployments show NO SIGNAL until a stream connects.
import {
  conn, frame, detections, system, cameras, metrics, pushAlert, pushEvent, activeCam,
} from './stores'
import type { Detection, DetKlass, Severity } from './types'

export const SIM = new URLSearchParams(location.search).has('sim')

const rnd = (a: number, b: number) => a + Math.random() * (b - a)

interface Walker { id: string; x: number; y: number; vx: number; vy: number; klass: DetKlass; sev: Severity; cls: Detection['cls'] }

function seed(): Walker[] {
  const mk = (i: number): Walker => ({
    id: `TK_${String(9 + i).padStart(3, '0')}.${Math.floor(rnd(100, 999))}-${i}`,
    x: rnd(0.15, 0.85), y: rnd(0.25, 0.8), vx: rnd(-0.03, 0.03), vy: rnd(-0.01, 0.01),
    klass: 'TRACKED', sev: 'info', cls: 'person',
  })
  const w = Array.from({ length: 5 }, (_, i) => mk(i))
  w[1].klass = 'ANOMALY'; w[1].sev = 'warning'
  w[2].klass = 'VEHICLE'; w[2].cls = 'vehicle'
  w[3].klass = 'ANIMAL'; w[3].cls = 'animal'
  return w
}

export function startSim() {
  const cams = [
    { id: '1', name: 'CAM 305 · SECTOR 3D', health: 'online' as const, coords: [41.0082, 28.9784] as [number, number], fps: 24 },
    { id: '2', name: 'CAM 218 · PLAZA', health: 'online' as const, coords: [41.0369, 28.9850] as [number, number], fps: 23 },
    { id: '3', name: 'CAM 231 · LOBBY', health: 'offline' as const, coords: [41.0553, 28.9769] as [number, number], fps: 0 },
    { id: '4', name: 'CAM 412 · TERMINAL', health: 'online' as const, coords: [41.0255, 28.9744] as [number, number], fps: 25 },
    { id: '5', name: 'CAM 507 · TRANSIT', health: 'online' as const, coords: [41.0138, 29.0089] as [number, number], fps: 22 },
    { id: '6', name: 'CAM 118 · PERIMETER', health: 'offline' as const, coords: [40.9903, 29.0250] as [number, number], fps: 0 },
  ]
  cameras.set(cams)
  activeCam.set('1')
  conn.set('online')

  const walkers = seed()
  let tick = 0

  const id = setInterval(() => {
    tick += 1
    for (const w of walkers) {
      w.x += w.vx; w.y += w.vy
      if (w.x < 0.1 || w.x > 0.9) w.vx *= -1
      if (w.y < 0.2 || w.y > 0.85) w.vy *= -1
    }
    detections.set(walkers.map<Detection>((w) => ({
      id: w.id, cls: w.cls, bbox: [w.x - 0.04, w.y - 0.09, 0.08, 0.18],
      conf: rnd(0.62, 0.96), severity: w.sev, klass: w.klass,
      attrs: { upper_color: 'navy', lower_color: 'gray', height: 'medium', accessory: w.klass === 'ANOMALY' ? ['backpack'] : [] },
    })))
    frame.set({ fps: rnd(23, 25), res: [1280, 720], inferenceMs: rnd(8, 16), brightness: 74, motionPct: rnd(1, 9) })
    system.set({ cpu: rnd(35, 55), gpu: rnd(28, 48), ram: rnd(55, 68), storageGB: 3.42, rec: 'event', recActive: tick % 12 < 4 })
    metrics.set([
      { label: 'RESOLUTION', value: '1280x720' }, { label: 'INFERENCE', value: `${rnd(8, 16).toFixed(1)} ms` },
      { label: 'BRIGHTNESS', value: '74' }, { label: 'MOTION %', value: rnd(1, 9).toFixed(1) },
      { label: 'PERSON', value: String(walkers.filter((w) => w.cls === 'person').length) },
      { label: 'VEHICLE', value: String(walkers.filter((w) => w.cls === 'vehicle').length) },
      { label: 'ANIMAL', value: String(walkers.filter((w) => w.cls === 'animal').length) },
      { label: 'DAY/NIGHT', value: 'DAY' }, { label: 'WEATHER', value: 'CLEAR' },
      { label: 'EVENTS TODAY', value: String(20 + Math.floor(tick / 8)) },
    ])

    if (tick % 40 === 12) {
      pushAlert({ ts: Date.now(), severity: 'critical', type: 'ABANDONED OBJECT', summary: 'Stationary object at lobby entrance — 12 min', cam: 'CAM 231', ack: false })
    } else if (tick % 40 === 28) {
      pushAlert({ ts: Date.now(), severity: 'warning', type: 'ANOMALY DETECTED', summary: 'Rapid movement — Sector 3D', cam: 'CAM 305', ack: false })
    }
    if (tick % 16 === 0) {
      pushEvent({ ts: Date.now(), type: 'PERSON', label: 'PERSON', conf: rnd(0.7, 0.95), cam: 'CAM 305' })
    }
  }, 120)

  return () => clearInterval(id)
}
