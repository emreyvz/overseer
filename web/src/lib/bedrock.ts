// OVERSEER — BEDROCK client: vocabulary, query state, and the SIM fixture.
//
// The query is built from chips, never typed as text, and the plain-language box compiles into
// the same chips. That is the trust mechanism: the operator watches their sentence become a
// structured query and fixes a wrong reading by editing one chip instead of re-prompting.
import { get, writable } from 'svelte/store'
import { bedrockAsOf, bedrockQuery, bedrockResult } from './stores'
import { api } from './api'
import type { BedrockClause, BedrockEntity, BedrockFact, BedrockQuery, BedrockResult, BedrockVocab } from './types'
import { SIM } from './sim'

export const vocab = writable<BedrockVocab | null>(null)
export const suggestions = writable<{ label: string; query: BedrockQuery; count: number }[]>([])
export const running = writable(false)
export const queryError = writable<{ message: string; clause?: number; hint?: string } | null>(null)
export const stats = writable<{ facts: number; entities: number; backfill: { running: boolean; done: number; total: number; phase: string } } | null>(null)

export const DAY = 86_400_000
export const WINDOWS: [string, number][] = [
  ['1H', 3_600_000], ['24H', DAY], ['7D', 7 * DAY], ['30D', 30 * DAY], ['ALL', 3650 * DAY],
]

/** Families in the order the timeline stacks them: presence at the bottom, behaviour on top. */
export const FAMILY_ROW: Record<string, number> = {
  presence: 0, spatial: 0, identity: 1, appearance: 1, behaviour: 2, system: 2,
}
export const FAMILY_COLOUR: Record<string, string> = {
  presence: 'var(--ink-dim)', spatial: 'var(--ink-dim)', appearance: 'var(--ink-dim)',
  identity: 'var(--cyan)', behaviour: 'var(--jade)', system: 'var(--scarlet)',
}

export function predMeta(v: BedrockVocab | null, pred: string) {
  return v?.predicates.find((p) => p.pred === pred) ?? null
}

export function defaultQuery(): BedrockQuery {
  const now = Date.now()
  return { select: 'entity', where: [{ t: 'kind', kind: 'person' }],
           window: { from: now - DAY, to: now }, limit: 200 }
}

export async function loadVocab(): Promise<void> {
  if (SIM) { vocab.set(simVocab()); suggestions.set(simSuggestions()); stats.set(simStats()); return }
  try {
    const v = await api.bedrockVocab()
    vocab.set(v)
    suggestions.set(((v as unknown as { suggestions?: never[] }).suggestions ?? []) as never[])
  } catch { vocab.set(null) }
  try { stats.set(await api.bedrockStats() as never) } catch { /* offline */ }
}

export async function runQuery(q?: BedrockQuery): Promise<void> {
  const query = q ?? get(bedrockQuery) ?? defaultQuery()
  const asOf = get(bedrockAsOf)
  const full: BedrockQuery = { ...query, asOf: asOf ?? undefined }
  bedrockQuery.set(query)
  running.set(true)
  queryError.set(null)
  if (SIM) {
    await new Promise((r) => setTimeout(r, 260))
    bedrockResult.set(simResult(full))
    running.set(false)
    return
  }
  try {
    const r = await api.bedrockQuery(full)
    if (r.error) {
      queryError.set({ message: r.error, clause: r.clause, hint: (r as { hint?: string }).hint })
      bedrockResult.set(null)
    } else {
      bedrockResult.set(r as BedrockResult)
    }
  } catch {
    queryError.set({ message: 'BACKEND UNREACHABLE' })
    bedrockResult.set(null)
  } finally {
    running.set(false)
  }
}

/** A human sentence for one clause, used by the chip row and the read-back. */
export function clauseText(v: BedrockVocab | null, c: BedrockClause, entities: BedrockEntity[] = []): string[] {
  if (c.t === 'kind') return ['ANY', c.kind.toUpperCase(), '']
  if (c.t === 'pred') {
    const m = predMeta(v, c.pred)
    const obj = c.obj != null ? (entities.find((e) => e.uid === c.obj)?.label ?? `#${c.obj}`) : null
    const val = Array.isArray(c.val) ? `${c.val[0]} TO ${c.val[1]}` : c.val != null ? String(c.val) : 'ANYTHING'
    return ['', (m?.label ?? c.pred).toUpperCase(), String(obj ?? val).toUpperCase()]
  }
  if (c.t === 'count') return ['HAPPENED', `${c.op} ${c.n}`, (predMeta(v, c.pred)?.label ?? c.pred).toUpperCase()]
  if (c.t === 'allen') return [`CLAUSE ${c.a + 1}`, c.rel.toUpperCase(), `CLAUSE ${c.b + 1}`]
  if (c.t === 'not') {
    const inner = clauseText(v, c.clause, entities)
    return [inner[0], `NOT ${inner[1]}`, inner[2]]
  }
  return ['', '', '']
}

/** Facts grouped into lanes, one per entity, for the timeline. */
export function lanes(res: BedrockResult): { entity: BedrockEntity; facts: BedrockFact[] }[] {
  const by = new Map<number, BedrockFact[]>()
  for (const f of res.facts) {
    const l = by.get(f.subj) ?? []
    l.push(f)
    by.set(f.subj, l)
  }
  return res.entities
    .map((e) => ({ entity: e, facts: by.get(e.uid) ?? [] }))
    .filter((l) => l.facts.length)
    .sort((a, b) => b.facts.length - a.facts.length)
}

/** How many facts differ between the current belief and a rewound transaction time. */
export function beliefDelta(now: BedrockResult | null, then: BedrockResult | null): number {
  if (!now || !then) return 0
  const a = new Set(now.facts.map((f) => f.id))
  const b = new Set(then.facts.map((f) => f.id))
  let n = 0
  for (const id of a) if (!b.has(id)) n++
  for (const id of b) if (!a.has(id)) n++
  return n
}

// ── SIM fixtures ────────────────────────────────────────────────────────────────────────────
function simVocab(): BedrockVocab {
  const mk = (pred: string, family: string, object: 'entity' | 'literal' | 'number', label: string) =>
    ({ pred, family, object, label })
  return {
    version: 1,
    kinds: ['person', 'vehicle', 'animal', 'object', 'zone', 'camera', 'event', 'alert', 'subject'],
    predicates: [
      mk('seen_on', 'presence', 'entity', 'WAS SEEN ON'),
      mk('present_in', 'presence', 'entity', 'WAS INSIDE'),
      mk('entered', 'presence', 'entity', 'ENTERED'),
      mk('exited', 'presence', 'entity', 'LEFT'),
      mk('near', 'spatial', 'entity', 'WAS NEAR'),
      mk('co_present_with', 'spatial', 'entity', 'WAS THERE WITH'),
      mk('wore', 'appearance', 'literal', 'WORE'),
      mk('has_plate', 'appearance', 'literal', 'HAS PLATE'),
      mk('is_subtype', 'appearance', 'literal', 'IS A'),
      mk('estimated_height', 'appearance', 'number', 'IS ROUGHLY (CM)'),
      mk('intent', 'behaviour', 'literal', 'APPEARED TO BE'),
      mk('conformity', 'behaviour', 'number', 'CONFORMITY PERCENTILE'),
      mk('same_as', 'identity', 'entity', 'IS THE SAME AS'),
      mk('alerted', 'system', 'literal', 'RAISED THE ALERT'),
      mk('diverged', 'system', 'number', 'DIVERGED BY (SIGMA)'),
    ],
  }
}

function simSuggestions() {
  const now = Date.now()
  return [
    { label: 'ANY PERSON · WAS SEEN ON · NORTH GATE · LAST 24H', count: 412,
      query: { select: 'entity' as const, where: [{ t: 'kind' as const, kind: 'person' }],
               window: { from: now - DAY, to: now }, limit: 200 } },
    { label: 'ANY VEHICLE · HAS PLATE · LAST 7D', count: 38,
      query: { select: 'entity' as const, where: [{ t: 'pred' as const, pred: 'has_plate' }],
               window: { from: now - 7 * DAY, to: now }, limit: 200 } },
  ]
}

function simStats() {
  return { facts: 41_882, entities: 1_204,
           backfill: { running: false, done: 4, total: 4, phase: 'DONE' } }
}

function simResult(q: BedrockQuery): BedrockResult {
  const now = Date.now()
  const from = q.window?.from ?? now - DAY
  const to = q.window?.to ?? now
  const span = to - from
  const asOf = q.asOf ?? null
  const ent = (uid: number, kind: string, ref: string, label: string): BedrockEntity =>
    ({ uid, kind, ref, label, first_seen: from + span * 0.1, last_seen: to })
  const entities = [
    ent(41, 'vehicle', 'TK_009.12', 'WHITE VAN 34ABC123'),
    ent(42, 'person', 'TK_009.44', 'DRIVER'),
    ent(43, 'person', 'TK_009.51', 'SECOND PERSON'),
  ]
  let id = 500
  const f = (subj: number, pred: string, val: string | null, a: number, b: number | null,
             conf: number, src: string, model: string, txAt = 0, superseded = false): BedrockFact => ({
    id: id++, subj, pred, obj: null, val,
    valid_from: from + span * a, valid_to: b === null ? null : from + span * b,
    tx_from: from + span * (txAt || a), tx_to: superseded ? to - span * 0.05 : null,
    conf, src_kind: src, src_ref: `frame:${41000 + id}`, model_id: model, snapshot: null,
    superseded_by: null,
  })
  const facts = [
    f(41, 'seen_on', 'NORTH GATE', 0.20, 0.34, 0.94, 'detector', 'yolo'),
    f(41, 'has_plate', '34ABC123', 0.22, 0.33, 0.91, 'anpr', 'anpr'),
    f(41, 'is_subtype', 'van', 0.20, 0.34, 0.82, 'detector', 'yolo'),
    f(42, 'seen_on', 'NORTH GATE', 0.24, 0.40, 0.90, 'detector', 'yolo'),
    f(42, 'wore', 'dark', 0.24, 0.40, 0.61, 'detector', 'palette'),
    f(42, 'co_present_with', 'WHITE VAN 34ABC123', 0.24, 0.33, 0.77, 'detector', 'yolo'),
    f(42, 'conformity', '0.4', 0.31, 0.36, 0.80, 'grain', 'grain-a'),
    // a belief the system only formed later, and one it has since retracted
    f(42, 'same_as', 'SUBJECT 214', 0.24, null, 0.78, 'reid', 'osnet', 0.86),
    f(43, 'seen_on', 'NORTH GATE', 0.55, 0.68, 0.88, 'detector', 'yolo'),
    f(43, 'same_as', 'SUBJECT 214', 0.55, 0.68, 0.55, 'reid', 'osnet', 0.55, true),
  ]
  const visible = facts.filter((x) => {
    if (asOf === null) return x.tx_to === null
    return x.tx_from <= asOf && (x.tx_to === null || x.tx_to > asOf)
  })
  return {
    entities, facts: visible, truncated: false, estimated: facts.length,
    took_ms: 18.4, as_of: asOf, window: { from, to },
  }
}
