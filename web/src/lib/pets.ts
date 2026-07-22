// OVERSEER — pet registry (feature 2). Owners register animals and search for lost ones.
// Identity is appearance-based visual re-identification (species + colour + ReID),
// NOT biometric face recognition (OSINT-legal). Stored locally (analyst/owner scope).
import { writable } from 'svelte/store'

export interface Pet {
  id: string
  name: string
  owner: string
  species: string
  color: string
  lost: boolean
  snapshot?: string
}

const KEY = 'overseer.pets'
const load = (): Pet[] => { try { return JSON.parse(localStorage.getItem(KEY) || '[]') } catch { return [] } }

export const pets = writable<Pet[]>(load())
pets.subscribe((v) => { try { localStorage.setItem(KEY, JSON.stringify(v)) } catch { /* */ } })

let seq = 0
export function addPet(p: Omit<Pet, 'id'>) {
  pets.update((l) => [...l, { ...p, id: `P${Date.now().toString(36)}${seq++}` }])
}
export function removePet(id: string) { pets.update((l) => l.filter((x) => x.id !== id)) }
export function setLost(id: string, lost: boolean) { pets.update((l) => l.map((x) => (x.id === id ? { ...x, lost } : x))) }
