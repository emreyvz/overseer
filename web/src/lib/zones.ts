// OVERSEER — analyst zones + alert rules (items 6,7,13). Client-side overlay logic so
// "draw a line → get an alert" works immediately; backend ZoneMonitor also emits its own.
import { get, writable } from 'svelte/store'
import { detections, activeCam, pushAlert } from './stores'

export type ZoneKind = 'area' | 'line'
export interface Zone { id: string; name: string; kind: ZoneKind; cam: string; points: [number, number][] }
export type RuleEvent = 'restricted' | 'crowd' | 'line' | 'loiter' | 'queue'
export interface Rule { id: string; name: string; event: RuleEvent; zoneId: string; threshold: number; severity: 'info' | 'warning' | 'critical' }

const ZKEY = 'overseer.zones', RKEY = 'overseer.rules'
const jload = <T>(k: string, d: T): T => { try { return JSON.parse(localStorage.getItem(k) || '') as T } catch { return d } }

export const zones = writable<Zone[]>(jload<Zone[]>(ZKEY, []))
export const rules = writable<Rule[]>(jload<Rule[]>(RKEY, []))
zones.subscribe((v) => { try { localStorage.setItem(ZKEY, JSON.stringify(v)) } catch { /* */ } })
rules.subscribe((v) => { try { localStorage.setItem(RKEY, JSON.stringify(v)) } catch { /* */ } })

let seq = 0
const uid = () => `${Date.now().toString(36)}${seq++}`
export function addZone(z: Omit<Zone, 'id'>): Zone { const nz = { ...z, id: uid() }; zones.update((l) => [...l, nz]); return nz }
export function delZone(id: string) { zones.update((l) => l.filter((z) => z.id !== id)); rules.update((l) => l.filter((r) => r.zoneId !== id)) }
export function addRule(r: Omit<Rule, 'id'>) { rules.update((l) => [...l, { ...r, id: uid() }]) }
export function delRule(id: string) { rules.update((l) => l.filter((r) => r.id !== id)) }

// — geometry —
function pointInPoly(x: number, y: number, poly: [number, number][]): boolean {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j]
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}
function side(a: [number, number], b: [number, number], p: [number, number]): number {
  return Math.sign((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]))
}

// — engine: evaluate rules against live detections (throttled per rule) —
const lastFire: Record<string, number> = {}
const prevPos: Record<string, [number, number]> = {}
const dwell: Record<string, number> = {}  // ruleId:detId -> first-enter ms (loitering)
let nowMs = 0
const RULE_LABEL: Record<RuleEvent, string> = {
  restricted: 'RESTRICTED ZONE BREACH', crowd: 'CROWD DETECTED', line: 'LINE CROSSED',
  loiter: 'LOITERING DETECTED', queue: 'QUEUE FORMED',
}

export function startZoneEngine() {
  detections.subscribe((dets) => {
    nowMs += 120
    const cam = get(activeCam) ?? ''
    const zs = get(zones), rs = get(rules)
    const centers = dets.map((d) => ({ id: d.id, x: d.bbox[0] + d.bbox[2] / 2, y: d.bbox[1] + d.bbox[3], person: d.cls === 'person' || d.cls === 'animal' }))

    for (const r of rs) {
      const z = zs.find((zz) => zz.id === r.zoneId)
      if (!z) continue
      if (lastFire[r.id] && nowMs - lastFire[r.id] < 8000) continue

      let hit = false
      if (r.event === 'restricted' && z.kind === 'area') {
        hit = centers.some((c) => c.person && pointInPoly(c.x, c.y, z.points))
      } else if ((r.event === 'crowd' || r.event === 'queue') && z.kind === 'area') {
        hit = centers.filter((c) => c.person && pointInPoly(c.x, c.y, z.points)).length > r.threshold
      } else if (r.event === 'loiter' && z.kind === 'area') {
        const inside = new Set<string>()
        for (const c of centers) {
          if (!c.person || !pointInPoly(c.x, c.y, z.points)) continue
          inside.add(c.id)
          const key = `${r.id}:${c.id}`
          if (!dwell[key]) dwell[key] = nowMs
          else if (nowMs - dwell[key] > (r.threshold || 5) * 1000) hit = true
        }
        for (const k of Object.keys(dwell)) if (k.startsWith(`${r.id}:`) && !inside.has(k.split(':')[1])) delete dwell[k]
      } else if (r.event === 'line' && z.kind === 'line' && z.points.length >= 2) {
        const a = z.points[0], b = z.points[1]
        for (const c of centers) {
          const prev = prevPos[c.id]
          if (prev && side(a, b, prev) !== side(a, b, [c.x, c.y]) && side(a, b, prev) !== 0) { hit = true; break }
        }
      }
      if (hit) {
        lastFire[r.id] = nowMs
        pushAlert({ ts: Date.now(), severity: r.severity, type: RULE_LABEL[r.event], summary: `${r.name} · ${z.name}`, cam, ack: false })
      }
    }
    for (const c of centers) prevPos[c.id] = [c.x, c.y]
  })
}
