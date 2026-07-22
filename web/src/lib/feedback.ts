// Operator feedback log (catalog 71 + 116). Marking an alert real/false is stored
// locally and rolled up per alert-type+camera, so the system can later down-weight
// noisy sources and auto-tune thresholds. Session-local (localStorage).
import { writable, get } from 'svelte/store'

export type Verdict = 'real' | 'false'
export interface Feedback { key: string; type: string; cam: string; verdict: Verdict; ts: number }

const KEY = 'overseer.feedback'
function load(): Feedback[] { try { return JSON.parse(localStorage.getItem(KEY) || '[]') } catch { return [] } }
export const feedback = writable<Feedback[]>(load())
feedback.subscribe((l) => { try { localStorage.setItem(KEY, JSON.stringify(l.slice(-500))) } catch { /* quota */ } })

export function recordFeedback(type: string, cam: string, verdict: Verdict) {
  feedback.update((l) => [...l, { key: `${type}|${cam}`, type, cam, verdict, ts: Date.now() }])
}

// False-positive rate per type+camera — drives noisy-source suppression / tuning.
export function fpRate(type: string, cam: string): { n: number; falseRate: number } {
  const rows = get(feedback).filter((f) => f.key === `${type}|${cam}`)
  if (!rows.length) return { n: 0, falseRate: 0 }
  const f = rows.filter((r) => r.verdict === 'false').length
  return { n: rows.length, falseRate: f / rows.length }
}

// --- Visual-match feedback (catalog 12 + 13). When the operator rejects a match, we
// remember it per object class and quietly RAISE the match threshold for that class —
// so a search that keeps producing bad "vehicle" hits gets stricter over time, while a
// reliable "person" search stays sensitive. Self-improving, no model retraining needed.
const MKEY = 'overseer.matchfb'
type MatchFb = { kind: string; verdict: Verdict; ts: number }
function loadM(): MatchFb[] { try { return JSON.parse(localStorage.getItem(MKEY) || '[]') } catch { return [] } }
export const matchFb = writable<MatchFb[]>(loadM())
matchFb.subscribe((l) => { try { localStorage.setItem(MKEY, JSON.stringify(l.slice(-300))) } catch { /* quota */ } })

export function recordMatchFeedback(kind: string, verdict: Verdict) {
  matchFb.update((l) => [...l, { kind: kind || 'object', verdict, ts: Date.now() }])
}

// Adaptive minimum match score for a class. Baseline 0.42 (backend default); every
// recent false rejection nudges it up, capped, so persistent bad matches get filtered.
export function matchMinScore(kind: string): number {
  const rows = get(matchFb).filter((f) => f.kind === (kind || 'object')).slice(-20)
  if (rows.length < 3) return 0.42
  const falseRate = rows.filter((r) => r.verdict === 'false').length / rows.length
  return Math.min(0.7, 0.42 + falseRate * 0.28)  // up to +0.28 when everything is rejected
}
