// OVERSEER — EARDRUM client.
//
// The live spectral frames arrive over the WS (see ws.ts); this owns probe management, the
// rolling spectrogram history the drawer draws, and the SIM fixture.
import { get } from 'svelte/store'
import { activeCam, probeFrames, probes } from './stores'
import { api } from './api'
import type { Probe, ProbeFrame, ProbeSpectrum } from './types'
import { SIM } from './sim'

/** Columns kept per probe: 60 s at the 4 Hz push rate. */
const HISTORY = 240
const history = new Map<number, Uint8Array[]>()

export function pushColumn(f: ProbeFrame) {
  const bytes = hexBytes(f.col)
  if (!bytes.length) return
  const h = history.get(f.id) ?? []
  h.push(bytes)
  while (h.length > HISTORY) h.shift()
  history.set(f.id, h)
}
export const columns = (id: number): Uint8Array[] => history.get(id) ?? []
export function clearHistory() { history.clear() }

function hexBytes(hex: string): Uint8Array {
  if (!hex || hex.length % 2) return new Uint8Array(0)
  const out = new Uint8Array(hex.length / 2)
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16)
  return out
}

export async function loadProbes(): Promise<void> {
  const cam = get(activeCam)
  if (!cam) { probes.set([]); return }
  if (SIM) { probes.set(simProbes()); simStart(); return }
  try { probes.set((await api.probes(cam)).probes ?? []) } catch { probes.set([]) }
}

export async function addProbe(roi: [number, number, number, number], name?: string)
  : Promise<{ ok: boolean; reason?: string }> {
  const cam = get(activeCam)
  if (!cam) return { ok: false, reason: 'NO CAMERA' }
  if (SIM) {
    const n = get(probes).length
    probes.update((l) => [...l, { id: 900 + n, name: n ? `P${n + 1}` : 'REF', roi,
      kind: n ? 'probe' : 'ref', enabled: true, texture: 0.6 }])
    return { ok: true }
  }
  try {
    const r = await api.addProbe(cam, roi, name)
    if (!r.probe) return { ok: false, reason: (r.reason ?? 'REFUSED').toUpperCase() }
    probes.update((l) => [...l, r.probe as Probe])
    return { ok: true }
  } catch { return { ok: false, reason: 'BACKEND UNREACHABLE' } }
}

export async function deleteProbe(id: number): Promise<void> {
  probes.update((l) => l.filter((p) => p.id !== id))
  history.delete(id)
  if (!SIM) await api.deleteProbe(id).catch(() => undefined)
}

export async function suggestProbes(n = 5) {
  const cam = get(activeCam)
  if (!cam) return []
  if (SIM) {
    return [
      { roi: [0.31, 0.34, 0.07, 0.12] as [number, number, number, number], texture: 0.82, rigid: true },
      { roi: [0.52, 0.29, 0.07, 0.12] as [number, number, number, number], texture: 0.74, rigid: true },
      { roi: [0.63, 0.46, 0.07, 0.12] as [number, number, number, number], texture: 0.61, rigid: false },
      { roi: [0.22, 0.58, 0.07, 0.12] as [number, number, number, number], texture: 0.55, rigid: true },
    ]
  }
  try { return (await api.suggestProbes(cam, n)).candidates ?? [] } catch { return [] }
}

export async function spectrumFor(id: number): Promise<ProbeSpectrum | null> {
  if (SIM) return simSpectrum(id)
  try { return (await api.probeSpectrum(id)).spectrum } catch { return null }
}

/** Peak level relative to baseline decides the bracket colour, so a healthy probe is quiet. */
export function levelTone(f: ProbeFrame | undefined): 'idle' | 'warn' | 'hot' {
  if (!f) return 'idle'
  if (f.db >= 12) return 'hot'
  if (f.db >= 6) return 'warn'
  return 'idle'
}

export const BAND_LABEL: Record<string, string> = {
  structural: 'STRUCTURAL · 0 TO fps/2',
  acoustic: 'ACOUSTIC · ROLLING SHUTTER',
}

// ── SIM fixtures ────────────────────────────────────────────────────────────────────────────
let simTimer: ReturnType<typeof setInterval> | undefined

function simProbes(): Probe[] {
  return [
    { id: 901, name: 'REF', roi: [0.08, 0.72, 0.07, 0.12], kind: 'ref', enabled: true, texture: 0.71 },
    { id: 902, name: 'HOUSING', roi: [0.31, 0.34, 0.07, 0.12], kind: 'probe', enabled: true, texture: 0.82, baseline: true },
    { id: 903, name: 'CONDUIT', roi: [0.52, 0.29, 0.07, 0.12], kind: 'probe', enabled: true, texture: 0.74 },
    { id: 904, name: 'RAIL', roi: [0.63, 0.46, 0.07, 0.12], kind: 'probe', enabled: true, texture: 0.61 },
  ]
}

function simStart() {
  if (simTimer) return
  let t = 0
  simTimer = setInterval(() => {
    t += 0.25
    for (const p of get(probes)) {
      const hot = p.id === 902
      const col = new Uint8Array(128)
      for (let i = 0; i < 128; i++) {
        const hz = (i / 128) * 15
        let v = 12 + Math.random() * 16
        if (hot) {
          if (Math.abs(hz - 12.25) < 0.3) v = 210 + Math.random() * 30
          if (Math.abs(hz - 6.1) < 0.25) v = 150 + Math.random() * 20
        }
        if (Math.abs(hz - 2.0) < 0.2) v = 90 + Math.random() * 20
        col[i] = Math.min(255, v)
      }
      const f: ProbeFrame = {
        id: p.id,
        rms: hot ? 0.021 + Math.sin(t) * 0.002 : 0.004,
        db: hot ? 8.4 : p.kind === 'ref' ? 0 : 1.2,
        snr: hot ? 41 : 14,
        peaks: hot
          ? [{ hz: 12.25, db: -18, prominence: 41, rise: 8.4 },
             { hz: 6.1, db: -26, prominence: 27 },
             { hz: 2.0, db: -38, prominence: 12, is_new: true }]
          : [{ hz: 2.0, db: -41, prominence: 11 }],
        col: Array.from(col).map((b) => b.toString(16).padStart(2, '0')).join(''),
        wave: Array.from({ length: 96 }, (_, i) =>
          (hot ? 0.03 : 0.006) * Math.sin((i + t * 12) * 0.55) + (Math.random() - 0.5) * 0.004),
      }
      probeFrames.update((m) => ({ ...m, [String(f.id)]: f }))
      pushColumn(f)
    }
  }, 250)
}

function simSpectrum(id: number): ProbeSpectrum {
  const n = 220
  const freqs = Array.from({ length: n }, (_, i) => (i / n) * 15)
  const hot = id === 902
  const psd = freqs.map((hz) => {
    let v = -62 + Math.random() * 3
    if (hot) {
      v += 44 * Math.exp(-((hz - 12.25) ** 2) / 0.02)
      v += 30 * Math.exp(-((hz - 6.1) ** 2) / 0.02)
    }
    v += 14 * Math.exp(-((hz - 2.0) ** 2) / 0.03)
    return Number(v.toFixed(2))
  })
  return {
    id, freqs: freqs.map((f) => Number(f.toFixed(3))), psd,
    baseline: hot ? psd.map((v, i) => v - (Math.abs(freqs[i] - 12.25) < 0.4 ? 8.4 : 0)) : null,
    floor: -59, band: 'structural', nyquist: 15,
    peaks: hot
      ? [{ hz: 12.25, db: -18, prominence: 41, rise: 8.4 },
         { hz: 6.1, db: -26, prominence: 27 },
         { hz: 2.0, db: -38, prominence: 12, is_new: true }]
      : [{ hz: 2.0, db: -41, prominence: 11 }],
    interpretation: hot
      ? { f0: 6.1, rpm: 366, harmonics: [{ order: 1, db: -26 }, { order: 2, db: -18 }],
          verdict: 'CONSISTENT WITH MISALIGNMENT', why: '2x is 8 dB above 1x', confidence: 2 }
      : null,
  }
}
