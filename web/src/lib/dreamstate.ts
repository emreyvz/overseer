// OVERSEER — DREAMSTATE client engine.
//
// The live field and the divergence feed arrive over the WS (see ws.ts), so this file only owns
// what the console needs on demand: the pulse history, the divergence ledger, and the SIM
// fixtures that let the whole thing be built and screenshotted with no backend.
import { get } from 'svelte/store'
import { activeCam, divergences, dreamStatus, modules } from './stores'
import { api } from './api'
import type { Divergence, DreamPulse, DreamStatus } from './types'
import { SIM } from './sim'

export const plateUrl = (cam: string) => `${api.base}/api/dream/${cam}/plate`

/** Load the ledger and the 24 h pulse for the console. */
export async function loadDream(hours = 24): Promise<{ pulse: DreamPulse[] }> {
  const cam = get(activeCam)
  if (!cam) return { pulse: [] }
  if (SIM) { divergences.set(simDivergences()); dreamStatus.set(simStatus(cam)); return { pulse: simPulse(hours) } }
  try {
    const [d, p] = await Promise.all([
      api.divergences(cam, 200).catch(() => ({ divergences: [] as Divergence[] })),
      api.dreamPulse(cam, hours).catch(() => ({ pulse: [] as DreamPulse[] })),
    ])
    divergences.set(d.divergences ?? [])
    return { pulse: p.pulse ?? [] }
  } catch {
    return { pulse: [] }
  }
}

/** In SIM the status never arrives over the socket, so seed it once. */
export function seedDreamSim() {
  const cam = get(activeCam)
  if (SIM && cam) { dreamStatus.set(simStatus(cam)); divergences.set(simDivergences()) }
}

/** Start/stop with the module toggle, mirroring the fog and grain engines.
 *
 *  In production the live field arrives over the WS and this is a no-op; in SIM it seeds the
 *  fixture, without which the veil and the ribbon can never be seen in development at all. */
export function startDreamEngine() {
  modules.subscribe(() => {
    const on = !!get(modules).find((m) => m.key === 'dream')?.on
    if (on) seedDreamSim()
    else if (SIM) { dreamStatus.set(null); divergences.set([]) }
  })
  activeCam.subscribe(() => {
    if (get(modules).find((m) => m.key === 'dream')?.on) seedDreamSim()
  })
}

/** How many times a given threshold WOULD have fired over the loaded pulse.
 *  This is what turns the sensitivity slider from a guess into a measurement. */
export function wouldFire(pulse: DreamPulse[], sigma: number): number {
  let n = 0
  let armed = false
  for (const p of pulse) {
    if (p.peak >= sigma) { if (!armed) { n++; armed = true } }
    else armed = false
  }
  return n
}

// ── SIM fixtures ────────────────────────────────────────────────────────────────────────────
function simStatus(cam: string): DreamStatus {
  const gw = 24, gh = 14
  const cells = new Array(gw * gh).fill(0).map((_, i) => {
    const cy = Math.floor(i / gw), cx = i % gw
    // a quiet scene with one warm patch where a pallet appeared
    if (cy >= 5 && cy <= 7 && cx >= 8 && cx <= 10) return 5.4 + Math.random() * 1.6
    return Math.random() * 1.8
  })
  return {
    cam, tier: 'A', bucket: 3,
    buckets: [
      { name: 'NIGHT', n: 120, maturity: 0.4 }, { name: 'DAWN', n: 60, maturity: 0.2 },
      { name: 'MORNING', n: 900, maturity: 1 }, { name: 'MIDDAY', n: 1400, maturity: 1 },
      { name: 'AFTERNOON', n: 1100, maturity: 1 }, { name: 'DUSK', n: 300, maturity: 1 },
    ],
    maturity: 1, sigma: 6.2,
    cells: cells.map((v) => Number(v.toFixed(2))), grid: [gw, gh],
    muted: [], threshold: 5,
  }
}

function simPulse(hours: number): DreamPulse[] {
  const now = Date.now()
  const out: DreamPulse[] = []
  for (let i = hours * 60; i >= 0; i--) {
    const t = now - i * 60000
    let peak = 0.8 + Math.random() * 1.6
    if (i === 42) peak = 6.2
    if (i === 310) peak = 5.4
    if (i === 705) peak = 7.9
    if (i === 980) peak = 4.6
    out.push({ t, peak: Number(peak.toFixed(2)), mean: Number((peak / 4).toFixed(3)) })
  }
  return out
}

function simDivergences(): Divergence[] {
  const now = Date.now()
  const box = (x: number, y: number, w: number, h: number): [number, number][] =>
    [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
  return [
    {
      id: 3, cam: 'NORTH GATE', ts: now - 42 * 60000, peak_sigma: 6.2, area_sigma_s: 41.4,
      blob: box(0.33, 0.36, 0.13, 0.21), cells: [128, 129, 130, 152, 153, 154],
      snapshot: null, verdict: null, tier: 'A', triage: 'scene',
    },
    {
      id: 2, cam: 'NORTH GATE', ts: now - 310 * 60000, peak_sigma: 5.4, area_sigma_s: 18.2,
      blob: box(0.62, 0.55, 0.10, 0.14), cells: [201, 202], snapshot: null,
      verdict: 'expected', tier: 'A', triage: 'subject',
    },
    {
      id: 1, cam: 'NORTH GATE', ts: now - 705 * 60000, peak_sigma: 7.9, area_sigma_s: 96.1,
      blob: box(0.12, 0.62, 0.18, 0.24), cells: [300, 301, 302, 324, 325], snapshot: null,
      verdict: 'flagged', tier: 'A', triage: 'scene',
    },
  ]
}
