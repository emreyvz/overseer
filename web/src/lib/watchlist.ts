// Watchlist: analyst-enrolled entities (pet / person / vehicle / object) captured
// straight from a live feed with a cropped image and a threat level, so they can
// be found later. Session-local (localStorage), appearance-based — OSINT-legal,
// no biometric identity DB. See the redesign notes.
import { writable } from 'svelte/store'

export type ThreatLevel = 'safe' | 'watch' | 'threat'
export type EntityKind = 'pet' | 'person' | 'vehicle' | 'object'

export interface WatchEntity {
  id: string
  kind: EntityKind
  name: string
  threat: ThreatLevel
  image?: string      // data URL of the cropped capture
  cam?: string        // camera name where enrolled
  camId?: string
  ts: number
  notes?: string
  color?: string      // dominant colour (for search)
  height?: string
}

const KEY = 'overseer.watchlist'
function load(): WatchEntity[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '[]')
  } catch {
    return []
  }
}

export const watchlist = writable<WatchEntity[]>(load())
watchlist.subscribe((l) => {
  try { localStorage.setItem(KEY, JSON.stringify(l)) } catch { /* quota / private mode */ }
})

export function enroll(e: Omit<WatchEntity, 'id' | 'ts'> & { id?: string; ts?: number }) {
  const entity: WatchEntity = { id: e.id ?? `WL_${Math.random().toString(36).slice(2, 9)}`, ts: e.ts ?? Date.now(), ...e } as WatchEntity
  watchlist.update((l) => [entity, ...l.filter((x) => x.id !== entity.id)])
  return entity
}
export function removeEntity(id: string) { watchlist.update((l) => l.filter((x) => x.id !== id)) }
export function setThreat(id: string, threat: ThreatLevel) { watchlist.update((l) => l.map((x) => (x.id === id ? { ...x, threat } : x))) }
