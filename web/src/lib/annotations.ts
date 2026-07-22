// OVERSEER — analyst annotations (item 5). OSINT-legal: notes/params are analyst-assigned,
// session/local-scoped (localStorage), NOT a permanent biometric database.
import { writable } from 'svelte/store'

export interface Annotation {
  alias?: string
  notes?: string
  species?: string       // for animals (dog, cat, …) — owner tracking aid
  owner?: string         // analyst-linked owner label (e.g. links a pet to a person case)
  upper_color?: string
  height?: string
  threat?: 'low' | 'medium' | 'high'
}

const KEY = 'overseer.annotations'
function load(): Record<string, Annotation> {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') } catch { return {} }
}

export const annotations = writable<Record<string, Annotation>>(load())
annotations.subscribe((v) => { try { localStorage.setItem(KEY, JSON.stringify(v)) } catch { /* ignore */ } })

export function annotate(id: string, patch: Annotation) {
  annotations.update((a) => ({ ...a, [id]: { ...a[id], ...patch } }))
}
