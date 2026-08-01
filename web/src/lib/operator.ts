// OVERSEER — AI Operator: turn a natural-language (typed or spoken) command into a chain of
// concrete system actions, then run them. A fast deterministic router handles the common,
// unambiguous commands with zero latency; anything complex or multi-step is planned by the LLM
// (server /api/ai/operate) which returns the same {steps,say} shape. While a plan runs, the
// screen border lights up (green = navigation/query, red = alarm/critical) so the operator can
// see the AI is driving, and every step is written to a transparent transcript.
import { get, writable } from 'svelte/store'
import {
  mode, activeCam, cameras, stage, forensicSeed, zoneEditor, alertRules, watchlistOpen, aiOpen,
  suggestionsOpen, spatialOpen, storageScreen, commandOpen, investigateCase, alertsScreen,
  rosterInit, flashBanner, triggerGlitch, type Mode,
} from './stores'
import { sendCommand } from './ws'
import { SIM } from './sim'
import { api } from './api'
import { sfx } from './audio'

// ---- operator state (read by the border overlay + the console transcript) ------------------
export type BorderKind = 'nav' | 'alert'
export const operatorActive = writable<BorderKind | null>(null)
export const operatorBusy = writable(false)
export type LogKind = 'step' | 'say' | 'ask' | 'error'
export type LogEntry = { t: number; text: string; kind: LogKind }
export const operatorLog = writable<LogEntry[]>([])

function olog(text: string, kind: LogKind = 'step') {
  operatorLog.update((l) => [...l.slice(-60), { t: Date.now(), text, kind }])
}

// ---- plan shape (shared with the server planner) -------------------------------------------
export type Step = { action: string; args?: Record<string, unknown> }
export type Plan = { steps?: Step[]; say?: string; ask?: string; border?: BorderKind; disabled?: boolean }

// ---- helpers -------------------------------------------------------------------------------
function findCam(q: string) {
  const list = get(cameras)
  const s = q.trim().toLowerCase()
  if (!s) return get(activeCam) ? list.find((c) => c.id === get(activeCam)) : undefined
  return (
    list.find((c) => c.name.toLowerCase() === s) ??
    list.find((c) => c.name.toLowerCase().includes(s)) ??
    list.find((c) => String(c.id) === s)
  )
}

const MODES: Mode[] = ['pov', 'montage', 'topology', 'forensic', 'archive', 'case', 'roster']

// ---- the action registry: the whole system's controllable surface -------------------------
// Each action drives real stores / API. Adding a capability here makes it available to both the
// deterministic router and the LLM planner. Returns a short human summary for the transcript.
type Action = (args: Record<string, unknown>) => Promise<string> | string
const S = (v: unknown, d = '') => (v == null ? d : String(v))

export const ACTIONS: Record<string, Action> = {
  open_screen: ({ name }) => {
    const n = S(name).toLowerCase()
    if (MODES.includes(n as Mode)) { stage.set('live'); mode.set(n as Mode); triggerGlitch(160); return `opened ${n}` }
    const opener: Record<string, () => void> = {
      map: () => { stage.set('select') },
      watchlist: () => watchlistOpen.set(true),
      suggestions: () => suggestionsOpen.set(true),
      alerts: () => alertsScreen.set(true),
      storage: () => storageScreen.set(true),
      zones: () => { stage.set('live'); mode.set('pov'); zoneEditor.set(true) },
      rules: () => { stage.set('live'); alertRules.set(true) },
      assistant: () => aiOpen.set(true),
      command: () => commandOpen.set(true),
      spatial: () => { const c = get(activeCam); if (c) { stage.set('live'); mode.set('pov'); spatialOpen.set(c) } },
    }
    if (opener[n]) { opener[n](); return `opened ${n}` }
    return `no such screen: ${n}`
  },

  switch_camera: ({ name }) => {
    const cam = findCam(S(name))
    if (!cam) return `camera not found: ${S(name)}`
    stage.set('live'); mode.set('pov')
    activeCam.set(cam.id)
    if (!SIM) sendCommand(`connect:${cam.name}`)
    sfx('glitch'); flashBanner(`> ${cam.name}`, false, 900)
    return `switched to ${cam.name}`
  },

  // Multi-camera live wall (montage). Optional `cameras` selects which feeds to prioritise.
  side_by_side: ({ cameras: names }) => {
    stage.set('live'); mode.set('montage'); triggerGlitch(180)
    const list = Array.isArray(names) ? names.map((n) => S(n)) : []
    if (list.length) return `live wall: ${list.join(', ')}`
    return 'live wall (all cameras)'
  },

  forensic_search: ({ query, time }) => {
    const q = S(query)
    if (!q) return 'nothing to search'
    forensicSeed.set(time ? `${q} @${S(time)}` : q)
    stage.set('live'); mode.set('forensic')
    return `searching: ${q}`
  },

  find_watched: () => {
    stage.set('live'); mode.set('roster')
    rosterInit.set({ bolo: true })
    return 'roster: red-flagged subjects'
  },

  describe_scene: async ({ camera }) => {
    const id = findCam(S(camera))?.id ?? get(activeCam)
    if (!id) return 'no active camera to describe'
    const r = await api.aiDescribe(String(id)).catch(() => null)
    return r?.description || (r?.disabled ? 'scene description needs a vision model' : 'could not describe the scene')
  },

  create_case: async ({ name }) => {
    const c = await api.addCase(S(name, 'CASE')).catch(() => null)
    if (!c) return 'could not create the case'
    investigateCase.set(c.id); stage.set('live'); mode.set('case')
    return `case created: ${S(name, 'CASE')}`
  },

  // Standing rule (e.g. "alarm if you see a weapon") — NOT an immediate alarm.
  create_alert_rule: async ({ text }) => {
    const r = await api.aiRule(S(text), true).catch(() => null)
    if (r?.created) return `alert rule created: ${r.rule?.name ?? S(text)}`
    if (r?.disabled) return 'rule creation needs the AI configured'
    return 'could not create the rule'
  },

  say: ({ text }) => S(text),
}

// ---- deterministic router: instant, no LLM round-trip for the common commands --------------
// Returns a Plan when it confidently understands the command, else null (fall back to the LLM).
const SCREEN_WORDS: Array<[RegExp, string]> = [
  [/roster|künye|kişiler|personel/i, 'roster'],
  [/forensic|forensik|adli/i, 'forensic'],
  [/watchlist|izleme listesi|takip listesi|bolo/i, 'watchlist'],
  [/(smart )?(alert|zone)? ?(öneri|suggestion|suggest)/i, 'suggestions'],
  [/3 ?d|spatial|üç boyut/i, 'spatial'],
  [/storage|depolama|kayıtlar/i, 'storage'],
  [/(yan ?yana|side ?by ?side|montage|live ?wall|duvar)/i, 'montage'],
  [/(harita|\bmap\b|topology|topoloji)/i, 'topology'],
  [/(archive|arşiv)/i, 'archive'],
  [/(\bcase\b|vaka|dava)/i, 'case'],
  [/(zone|bölge).*(çiz|ekle|oluştur|draw|add)|(çiz|draw).*(zone|bölge)/i, 'zones'],
  [/(alert|alarm)? ?(kural|rule)/i, 'rules'],
]

export function routeCommand(raw: string): Plan | null {
  const text = raw.trim()
  const low = text.toLowerCase()
  if (!text) return null

  // "X kamerasına geç" / "switch to X (camera)" / "go to camera X"
  const camMatch =
    low.match(/(?:switch|go|geç|git|aç|open)\s*(?:to\s*)?(?:the\s*)?(?:camera\s*|cam\s*)?["“]?([\w\s-]+?)["”]?\s*(?:camera|cam|kameras[ıi]na|kameras[ıi]|kameraya)?\s*(?:geç|git|switch|aç)?\.?$/i)
  const camMatch2 = low.match(/["“]?([\w\s-]+?)["”]?\s*kameras[ıi]na\s*geç/i)
  if (/kamera|camera|\bcam\b/.test(low) && (camMatch2 || camMatch)) {
    const name = (camMatch2?.[1] || camMatch?.[1] || '').trim()
    if (name && findCam(name)) return { steps: [{ action: 'switch_camera', args: { name } }], border: 'nav' }
  }

  // side-by-side / live wall
  if (/(yan ?yana|side ?by ?side|live ?wall)/i.test(low)) {
    return { steps: [{ action: 'side_by_side', args: {} }], border: 'nav' }
  }

  // "describe the scene" / "sahneyi anlat"
  if (/(sahneyi anlat|anlat|describe (the )?scene|ne görüyorsun|what do you see)/i.test(low)) {
    return { steps: [{ action: 'describe_scene', args: {} }], border: 'nav' }
  }

  // red-flagged roster
  if (/(kırmızı|red[- ]?flag|watched|bolo|tehlikeli)/i.test(low) && /(roster|kişi|subject|people|person)/i.test(low)) {
    return { steps: [{ action: 'open_screen', args: { name: 'roster' } }, { action: 'find_watched', args: {} }], border: 'nav' }
  }

  // "open X screen" / "X ekranını aç" — needs an open verb OR the word "ekran/screen"
  const wantsOpen = /(\baç\b|open|göster|show|ekran|screen|geçiş)/i.test(low)
  if (wantsOpen) {
    for (const [re, screen] of SCREEN_WORDS) {
      if (re.test(low)) {
        if (screen === 'montage') return { steps: [{ action: 'side_by_side', args: {} }], border: 'nav' }
        return { steps: [{ action: 'open_screen', args: { name: screen } }], border: 'nav' }
      }
    }
  }

  // "search/find <q>" / "<q> ara/bul"
  const sm = low.match(/(?:forensic\s*)?(?:search|find|ara|bul|aratt?[ıi]r)\b[:\s]+(.{2,})/i) ||
             low.match(/^(.{2,}?)\s+(?:ara|bul)\.?$/i)
  if (sm && sm[1]) return { steps: [{ action: 'forensic_search', args: { query: sm[1].trim() } }], border: 'nav' }

  return null
}

// ---- executor ------------------------------------------------------------------------------
export async function runPlan(plan: Plan): Promise<void> {
  if (plan.ask) { olog(plan.ask, 'ask'); return }
  const steps = plan.steps ?? []
  if (!steps.length && plan.say) { olog(plan.say, 'say'); return }
  operatorBusy.set(true)
  operatorActive.set(plan.border ?? 'nav')
  try {
    for (const step of steps) {
      const fn = ACTIONS[step.action]
      if (!fn) { olog(`unknown action: ${step.action}`, 'error'); continue }
      try {
        const summary = await fn(step.args ?? {})
        if (summary) olog(summary, step.action === 'say' ? 'say' : 'step')
      } catch (e) {
        olog(`${step.action} failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
      }
    }
    if (plan.say) olog(plan.say, 'say')
  } finally {
    // let the border linger a beat so a fast chain still registers visually
    setTimeout(() => operatorActive.set(null), 900)
    operatorBusy.set(false)
  }
}

// ---- top-level entry: route locally, else ask the server planner ---------------------------
export async function operate(command: string): Promise<Plan> {
  const cmd = command.trim()
  if (!cmd) return { say: '' }
  olog(cmd, 'say')
  const local = routeCommand(cmd)
  if (local) { await runPlan(local); return local }
  // LLM fallback: give the planner live context so it can resolve references.
  const context = {
    cameras: get(cameras).map((c) => c.name),
    active_camera: get(cameras).find((c) => c.id === get(activeCam))?.name ?? null,
    mode: get(mode),
    actions: Object.keys(ACTIONS),
  }
  try {
    const plan = (await api.aiOperate(cmd, context)) as Plan
    if (plan?.disabled) { olog('AI Operator needs the assistant configured', 'error'); return plan }
    await runPlan(plan)
    return plan
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    olog(`could not plan that: ${msg}`, 'error')
    return { say: msg }
  }
}
