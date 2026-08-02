// OVERSEER — AI Operator: turn a natural-language (typed or spoken) command into a chain of
// concrete system actions, then run them. A fast deterministic router handles the common,
// unambiguous commands with zero latency; anything complex or multi-step is planned by the LLM
// (server /api/ai/operate) which returns the same {steps,say} shape. While a plan runs, the
// screen border lights up (green = navigation/query, red = alarm/critical) so the operator can
// see the AI is driving, and every step is written to a transparent transcript.
import { get, writable } from 'svelte/store'
import {
  mode, activeCam, cameras, stage, forensicSeed, zoneEditor, alertRules, watchlistOpen, operatorOpen,
  suggestionsOpen, spatialOpen, storageScreen, commandOpen, investigateCase, alertsScreen, objectRegister,
  rosterInit, modules, toggleModule, detections, alerts, timeline, timelineOpen, petRegistry,
  povZoom, muted, frame, system, narrateOn, followOn, xrayOn, selectedDetection,
  flashBanner, triggerGlitch, type Mode,
} from './stores'
import { sendCommand } from './ws'
import { SIM } from './sim'
import { api } from './api'
import { annotate } from './annotations'
import { zones, delZone } from './zones'
import { recordFeedback } from './feedback'
import { sfx, toggleMute } from './audio'

// ---- operator state (read by the border overlay + the console transcript) ------------------
export type BorderKind = 'nav' | 'alert'
export const operatorActive = writable<BorderKind | null>(null)
export const operatorBusy = writable(false)
export type LogKind = 'you' | 'step' | 'say' | 'ask' | 'error'
export type LogEntry = { t: number; text: string; kind: LogKind }
export const operatorLog = writable<LogEntry[]>([])

function olog(text: string, kind: LogKind = 'step') {
  operatorLog.update((l) => [...l.slice(-60), { t: Date.now(), text, kind }])
}

// ---- plan shape (shared with the server planner) -------------------------------------------
// A step may name its result with `as`, and later steps may reference it in args as "$name"
// (data passing) — e.g. find a car `as:"car"`, then watch {subject:"$car"}. This is what lets the
// Operator run a real chain: "go to the street cam, if there's a car add it to the watchlist,
// name it, enhance its photo, tell me when it was last seen".
export type Step = { action: string; args?: Record<string, unknown>; as?: string }
export type Plan = { steps?: Step[]; say?: string; ask?: string; border?: BorderKind; disabled?: boolean }
// An action returns either a human summary string, or {say, value} where `value` is the data a
// later step consumes via `as` + "$ref".
type ActionResult = string | { say?: string; value?: unknown } | void

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
type Action = (args: Record<string, unknown>) => Promise<ActionResult> | ActionResult
const S = (v: unknown, d = '') => (v == null ? d : String(v))
// resolve "$name" args against the running chain's result bag (data passing between steps)
function resolveArgs(args: Record<string, unknown> | undefined, bag: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(args ?? {})) {
    out[k] = typeof v === 'string' && v.startsWith('$') ? bag[v.slice(1)] : v
  }
  return out
}

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
      assistant: () => operatorOpen.set(true),
      command: () => commandOpen.set(true),
      enroll: () => { stage.set('live'); mode.set('pov'); objectRegister.set(true) },
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

  // Drive the roster's detailed filters: class, colour, vehicle type, height, BOLO-only, text.
  filter_roster: ({ cls, color, subtype, height, watched, query }) => {
    stage.set('live'); mode.set('roster')
    rosterInit.set({
      cls: cls != null ? S(cls).toLowerCase() : undefined,
      color: color != null ? S(color).toLowerCase() : undefined,
      subtype: subtype != null ? S(subtype).toLowerCase() : undefined,
      height: height != null ? S(height).toLowerCase() : undefined,
      bolo: watched != null ? watched !== false : undefined,
      query: query != null ? S(query) : undefined,
    })
    const bits = [cls, color, subtype, height, watched ? 'watched' : ''].map((x) => S(x)).filter(Boolean)
    return `roster filtered${bits.length ? ': ' + bits.join(' ') : ''}`
  },

  // Detailed forensic appearance search across the record (colour, type, make, height, time…).
  search_forensic: ({ kind, color, height, subtype, make, query, time }) => {
    const terms = [kind, color, height, subtype, make, query].map((x) => S(x)).filter(Boolean)
    const seed = terms.join(' ').trim()
    if (!seed && !time) return 'nothing to search'
    forensicSeed.set(time ? `${seed} @${S(time)}` : seed)
    stage.set('live'); mode.set('forensic')
    return `forensic search: ${seed || S(time)}`
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

  // Toggle ANY module/overlay by name (every checkbox in the panel): detection classes, the
  // environment/sky analysers, and the visual overlays (heatmap, tactical, foresight, tracklet).
  // "alarm on weapons" turns weapon detection on, which then auto-alerts on sight.
  set_module: ({ key, on }) => {
    const q = S(key).toLowerCase().trim()
    const mods = get(modules)
    const m = mods.find((x) => x.key === q)
      || mods.find((x) => x.label.toLowerCase() === q)
      || (/(foresight|ghost|predict)/.test(q) ? mods.find((x) => x.key === 'ghosts') : undefined)
      || (/track/.test(q) ? mods.find((x) => x.key === 'track') : undefined)
      || (/(day.?night|night)/.test(q) ? mods.find((x) => x.key === 'daynight') : undefined)
      || mods.find((x) => x.key.includes(q) || x.label.toLowerCase().includes(q))
    if (!m) return `no such module: ${q}`
    const want = on !== false
    if (m.on !== want) toggleModule(m.key)
    return `${m.label.toLowerCase()} ${want ? 'on' : 'off'}`
  },
  watch_plate: async ({ plate }) => {
    const p = S(plate).toUpperCase().replace(/\s+/g, '')
    if (!p) return 'no plate given'
    await api.watchPlate(p, true).catch(() => {})
    return `watching plate ${p} — a re-read on any camera will alert`
  },

  // Standing rule (e.g. "alarm if you see a weapon") — NOT an immediate alarm.
  create_alert_rule: async ({ text }) => {
    const r = await api.aiRule(S(text), true).catch(() => null)
    if (r?.created) return `alert rule created: ${r.rule?.name ?? S(text)}`
    if (r?.disabled) return 'rule creation needs the AI configured'
    return 'could not create the rule'
  },

  // ---- queries: answer questions from live data (the app's core purpose) -------------------
  count: ({ cls }) => {
    const want = S(cls, 'person').toLowerCase()
    const live = get(detections).filter((d) => !d.coasting)
    const n = want === 'any'
      ? live.length
      : live.filter((d) => d.cls === want).length
    const camName = get(cameras).find((c) => c.id === get(activeCam))?.name ?? 'the current camera'
    const noun = want === 'vehicle' ? (n === 1 ? 'vehicle' : 'vehicles')
      : want === 'person' ? (n === 1 ? 'person' : 'people') : (n === 1 ? 'object' : 'objects')
    return `${n} ${noun} on ${camName} right now`
  },
  count_people: () => ACTIONS.count({ cls: 'person' }),
  count_vehicles: () => ACTIONS.count({ cls: 'vehicle' }),

  // how many un-acked alerts, optionally by severity
  count_alerts: ({ severity }) => {
    const sev = S(severity).toLowerCase()
    const list = get(alerts).filter((a) => !a.ack && (!sev || a.severity === sev))
    return `${list.length} ${sev ? sev + ' ' : ''}alert${list.length === 1 ? '' : 's'} active`
  },

  // ---- subjects: find one, then act on it (data-passing chain) -----------------------------
  // Find the most recent roster subject matching cls (+ optional camera / colour). Returns the
  // subject as `value` so later steps can consume it via `as` + "$ref".
  find_subject: async ({ cls, camera, color }) => {
    const rows = await api.roster().catch(() => [] as any[])
    const want = S(cls).toLowerCase()
    const cam = S(camera).toLowerCase()
    const col = S(color).toLowerCase()
    let cand = rows.filter((r) => !want || want === 'any' || r.cls === want)
    if (cam) cand = cand.filter((r) => (r.cam ?? '').toLowerCase().includes(cam) || (r.first_cam ?? '').toLowerCase().includes(cam))
    if (col) cand = cand.filter((r) => (r.attrs?.upper_color ?? '').toLowerCase() === col || (r.attrs?.subtype ?? '').toLowerCase().includes(col))
    cand.sort((a, b) => (b.last_ts ?? 0) - (a.last_ts ?? 0))
    const hit = cand[0]
    if (!hit) return `no ${want || 'subject'}${cam ? ' on ' + camera : ''} found`
    const label = `${hit.cls}${hit.plate ? ' ' + hit.plate : ''}`
    return { say: `found a ${label}`, value: { id: hit.id, cls: hit.cls, plate: hit.plate, subject_uid: hit.subject_uid ?? null, last_ts: hit.last_ts, name: '' } }
  },
  // Add a found subject to the watchlist and optionally name it.
  watch_subject: async ({ subject, name }) => {
    const s = subject as { id?: string; name?: string } | undefined
    if (!s?.id) return 'no subject to add to the watchlist'
    await api.watchRoster(s.id, true).catch(() => {})
    const nm = S(name)
    if (nm) { annotate(s.id, { alias: nm }); s.name = nm }
    return `added ${nm || 'the subject'} to the watchlist`
  },
  // Super-resolution "clarify" of a subject's photo (uses the persistent subject if available).
  super_fuse: async ({ subject }) => {
    const s = subject as { id?: string; subject_uid?: number | null; name?: string } | undefined
    if (!s?.id) return 'no subject to enhance'
    const who = s.name || 'the subject'
    if (s.subject_uid != null) {
      const r = await api.subjectReconstruct(s.subject_uid).catch(() => null)
      if (r) return `enhanced ${who}'s photo (super-fuse)`
    }
    const r = await api.supercut(s.id).catch(() => null)
    return r?.url ? `built an enhanced supercut for ${who}` : `could not enhance ${who}'s photo`
  },
  // When was this subject last seen.
  last_seen: ({ subject }) => {
    const s = subject as { last_ts?: number; name?: string } | undefined
    if (!s?.last_ts) return 'no sighting on record for that subject'
    const when = new Date(s.last_ts).toLocaleString()
    return `${s.name || 'the subject'} was last seen ${when}`
  },

  // ---- view control -------------------------------------------------------------------------
  zoom: ({ level, x, y }) => {
    const z = Math.max(1, Math.min(5, Number(level) || 2))
    povZoom.set({ zoom: z, x: Number(x) || 0, y: Number(y) || 0 })
    return z <= 1 ? 'reset zoom' : `zoomed ${z}×`
  },
  reset_view: () => { povZoom.set({ zoom: 1, x: 0, y: 0 }); return 'view reset' },
  next_camera: ({ dir }) => {
    const list = get(cameras); if (!list.length) return 'no cameras'
    const i = Math.max(0, list.findIndex((c) => c.id === get(activeCam)))
    const cam = list[(i + (Number(dir) || 1) + list.length) % list.length]
    stage.set('live'); mode.set('pov'); activeCam.set(cam.id)
    if (!SIM) sendCommand(`connect:${cam.name}`)
    return `switched to ${cam.name}`
  },
  go_home: () => { stage.set('select'); return 'back to the map' },
  fullscreen: ({ on }) => {
    const want = on !== false
    try { if (want && !document.fullscreenElement) document.documentElement.requestFullscreen?.(); else if (!want) document.exitFullscreen?.() } catch { /* noop */ }
    return want ? 'fullscreen on' : 'fullscreen off'
  },
  mute: ({ on }) => {
    const want = on !== false
    if (get(muted) !== want) toggleMute()
    return want ? 'muted' : 'unmuted'
  },

  // ---- alerts / analysis --------------------------------------------------------------------
  acknowledge_alerts: () => {
    let n = 0
    alerts.update((l) => l.map((a) => { if (!a.ack) n++; return { ...a, ack: true } }))
    return `acknowledged ${n} alert${n === 1 ? '' : 's'}`
  },
  summarize: async () => {
    const ev = [
      ...get(alerts).slice(0, 20).map((a) => ({ type: a.type, cam: a.cam, label: a.summary })),
      ...get(timeline).slice(0, 20).map((e) => ({ type: e.type, cam: e.cam, label: e.label })),
    ]
    if (!ev.length) return 'nothing to summarise yet'
    const r = await api.aiSummarize(ev).catch(() => null)
    return r?.summary || (r?.disabled ? 'summaries need the AI configured' : 'could not summarise')
  },
  correlate_alerts: async () => {
    const al = get(alerts).slice(0, 20).map((a) => ({ ts: new Date(a.ts).toLocaleTimeString(), severity: a.severity, type: a.type, cam: a.cam, summary: a.summary }))
    if (!al.length) return 'no alerts to correlate'
    const r = await api.aiCorrelate(al).catch(() => null)
    if (r?.disabled) return 'correlation needs the AI configured'
    if (r?.result?.incident) return `${r.result.title || 'incident'}: ${r.result.assessment || ''}${r.result.action ? ` — ${r.result.action}` : ''}`
    return r?.result?.assessment || 'the alerts look independent'
  },

  // ---- status queries -----------------------------------------------------------------------
  camera_status: ({ camera }) => {
    const camName = findCam(S(camera))?.name ?? get(cameras).find((c) => c.id === get(activeCam))?.name ?? 'the camera'
    const f = get(frame)
    const live = get(detections).filter((d) => !d.coasting)
    const ppl = live.filter((d) => d.cls === 'person').length
    const veh = live.filter((d) => d.cls === 'vehicle').length
    return `${camName}: ${f.fps.toFixed(0)} fps, brightness ${Math.round(f.brightness)}, ${ppl} people, ${veh} vehicles right now`
  },
  camera_dna: async ({ camera }) => {
    const camName = findCam(S(camera))?.name ?? get(cameras).find((c) => c.id === get(activeCam))?.name
    const r = await api.cameraDna().catch(() => null)
    const c = r?.cameras?.find((x) => x.name === camName)
    if (!c) return `no profile yet for ${camName ?? 'that camera'}`
    const tags = (c.dna ?? []).join(', ') || 'still learning'
    return `${camName}: ${tags} (reputation ${Math.round((c.reputation ?? 0) * 100)}%)`
  },
  system_status: () => {
    const s = get(system)
    return `CPU ${Math.round(s.cpu)}%, GPU ${s.gpu == null ? 'n/a' : Math.round(s.gpu) + '%'}, RAM ${Math.round(s.ram)}%, storage ${s.storageGB.toFixed(1)} GB`
  },
  storage_status: async () => {
    const r = await api.storage().catch(() => null)
    return r ? `${r.recordings} recordings, ${r.sizeGB.toFixed(2)} GB used` : 'storage info unavailable'
  },
  list_cameras: () => {
    const names = get(cameras).map((c) => c.name)
    return names.length ? `${names.length} cameras: ${names.join(', ')}` : 'no cameras'
  },
  offline_cameras: () => {
    const off = get(cameras).filter((c) => c.health === 'offline').map((c) => c.name)
    return off.length ? `offline: ${off.join(', ')}` : 'all cameras online'
  },
  busiest_camera: async ({ go }) => {
    const r = await api.cameraDna().catch(() => null)
    const cams = (r?.cameras ?? []) as Array<{ name?: string; person?: number; vehicle?: number }>
    if (!cams.length) return 'no activity data yet'
    const top = [...cams].sort((a, b) => ((b.person ?? 0) + (b.vehicle ?? 0)) - ((a.person ?? 0) + (a.vehicle ?? 0)))[0]
    if (go === true) {
      const c = get(cameras).find((x) => x.name === top.name)
      if (c) { stage.set('live'); mode.set('pov'); activeCam.set(c.id); if (!SIM) sendCommand(`connect:${c.name}`) }
    }
    return `busiest is ${top.name} (${(top.person ?? 0) + (top.vehicle ?? 0)} seen)`
  },
  pan: ({ dir }) => {
    const cur = get(povZoom); const d = S(dir).toLowerCase(); const step = 0.15
    let x = cur.x, y = cur.y
    if (/left|sol/.test(d)) x -= step; else if (/right|sağ/.test(d)) x += step
    else if (/up|yukar/.test(d)) y -= step; else if (/down|aşağ/.test(d)) y += step
    povZoom.set({ zoom: Math.max(1.4, cur.zoom), x: Math.max(-1, Math.min(1, x)), y: Math.max(-1, Math.min(1, y)) })
    return `panned ${d || 'view'}`
  },

  // ---- alerts: top / explain / advise / open a case -----------------------------------------
  latest_alert: () => {
    const a = get(alerts)[0]
    return a ? `latest: ${a.severity} ${a.type} at ${a.cam} — ${a.summary}` : 'no alerts'
  },
  explain_alert: async () => {
    const a = get(alerts)[0]; if (!a) return 'no alert to explain'
    const r = await api.aiExplain({ type: a.type, summary: a.summary, cam: a.cam }).catch(() => null)
    return r?.explanation || (r?.disabled ? 'explanations need the AI configured' : 'could not explain that alert')
  },
  advise_alert: async () => {
    const a = get(alerts)[0]; if (!a) return 'no alert to advise on'
    const r = await api.aiAdvise({ type: a.type, summary: a.summary, cam: a.cam }).catch(() => null)
    return r?.action || (r?.disabled ? 'advice needs the AI configured' : 'no advice')
  },
  case_from_alert: async () => {
    const a = get(alerts)[0]; if (!a) return 'no alert to open a case from'
    const c = await api.caseFromAlert({ ts: String(a.ts), severity: a.severity, type: a.type, cam: a.cam, summary: a.summary }).catch(() => null)
    if (c?.id == null) return 'could not open a case'
    investigateCase.set(c.id); stage.set('live'); mode.set('case')
    return `opened a case for ${a.type}`
  },
  search_events: async ({ query }) => {
    const q = S(query); if (!q) return 'ask a question about recent events'
    const ev = get(timeline).slice(0, 60).map((e) => ({ ts: new Date(e.ts).toLocaleTimeString(), type: e.type, cam: e.cam, label: e.label }))
    if (!ev.length) return 'no events on the timeline yet'
    const r = await api.aiSearchEvents(q, ev).catch(() => null)
    return r?.result?.answer || (r?.disabled ? 'semantic search needs the AI configured' : 'no matching events')
  },

  // ---- roster stats ---------------------------------------------------------
  count_subjects: async ({ cls, color, subtype }) => {
    const rows = await api.roster().catch(() => [] as any[])
    const c = S(cls).toLowerCase(), col = S(color).toLowerCase(), st = S(subtype).toLowerCase()
    const n = rows.filter((r) =>
      (!c || r.cls === c) &&
      (!col || (r.attrs?.upper_color ?? '').toLowerCase() === col) &&
      (!st || (r.attrs?.subtype ?? '').toLowerCase().includes(st))).length
    const desc = [col, st, c].filter(Boolean).join(' ') || 'subjects'
    return `${n} ${desc} seen this session`
  },
  list_watched: async () => {
    const rows = await api.roster().catch(() => [] as any[])
    const w = rows.filter((r) => r.watched)
    return w.length
      ? `${w.length} watched: ${w.slice(0, 6).map((r) => (r.attrs?.upper_color ? r.attrs.upper_color + ' ' : '') + r.cls).join(', ')}`
      : 'no watched subjects'
  },

  // ---- zones ----------------------------------------------------------------
  clear_zones: () => {
    const zs = get(zones); const n = zs.length
    zs.forEach((z) => delZone(z.id))
    return n ? `cleared ${n} zone${n === 1 ? '' : 's'}` : 'no zones to clear'
  },

  // ---- forensic plate + stats + help ----------------------------------------
  find_plate: ({ plate }) => {
    const p = S(plate).toUpperCase().replace(/\s+/g, '')
    if (!p) return 'give a plate to search for'
    forensicSeed.set(p); stage.set('live'); mode.set('forensic')
    return `searching for plate ${p}`
  },
  stats: async ({ period }) => {
    const nowMs = Date.now()
    const win = /week|hafta|7/.test(S(period)) ? 7 : 1
    const r = await api.stats((nowMs - win * 86400000) / 1000, nowMs / 1000).catch(() => null)
    if (!r) return 'stats unavailable'
    const top = Object.entries(r).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, v]) => `${v} ${k.toLowerCase()}`)
    return top.length ? `last ${win}d: ${top.join(', ')}` : 'no events in that window'
  },
  help: () => 'I can switch/compare cameras, open any screen, search and filter the roster and '
    + 'forensics in detail, count people/vehicles/alerts, answer camera and system status, toggle any '
    + 'detector or overlay, create zones/rules/cases, watch plates or subjects, super-fuse a photo, '
    + 'brief you, and chain all of it together. Just say what you want.',

  // ---- more alert / subject / panel control ----------------------------------
  mark_false: () => {
    const a = get(alerts)[0]; if (!a) return 'no alert to mark'
    recordFeedback(a.type, a.cam, 'false')
    alerts.update((l) => l.map((x) => (x === a ? { ...x, ack: true } : x)))
    return `marked "${a.type}" at ${a.cam} as a false alarm`
  },
  unwatch_subject: async ({ subject }) => {
    const s = subject as { id?: string; name?: string } | undefined
    if (!s?.id) return 'no subject to remove'
    await api.watchRoster(s.id, false).catch(() => {})
    return `removed ${s.name || 'the subject'} from the watchlist`
  },
  relationships: async ({ subject }) => {
    const s = subject as { id?: string } | undefined
    if (!s?.id) return 'no subject given'
    const r = await api.entityRelationships(s.id).catch(() => null)
    const a = r?.associates ?? []
    return a.length
      ? `${a.length} associates; most often with a ${a[0].cls}${a[0].plate ? ' ' + a[0].plate : ''} (${a[0].count}×)`
      : 'no associates found for that subject'
  },
  alerts_here: ({ camera }) => {
    const camName = findCam(S(camera))?.name ?? get(cameras).find((c) => c.id === get(activeCam))?.name
    const n = get(alerts).filter((x) => x.cam === camName && !x.ack).length
    return `${n} active alert${n === 1 ? '' : 's'} on ${camName ?? 'this camera'}`
  },
  close_panels: () => {
    zoneEditor.set(false); alertRules.set(false); objectRegister.set(false); storageScreen.set(false)
    watchlistOpen.set(false); suggestionsOpen.set(false); alertsScreen.set(false); spatialOpen.set(null)
    return 'closed the open panels'
  },
  reconnect: () => {
    const c = get(cameras).find((x) => x.id === get(activeCam))
    if (!c) return 'no active camera'
    if (!SIM) sendCommand(`connect:${c.name}`)
    return `reconnecting ${c.name}`
  },

  // ---- cases / objects / pets / spatial / timeline / plates ------------------
  open_case: async ({ id, name }) => {
    if (id != null && !isNaN(Number(id))) { investigateCase.set(Number(id)); stage.set('live'); mode.set('case'); return `opened case ${id}` }
    if (name) {
      const list = await api.cases().catch(() => [])
      const c = list.find((x) => x.name.toLowerCase().includes(S(name).toLowerCase()))
      if (c) { investigateCase.set(c.id); stage.set('live'); mode.set('case'); return `opened case ${c.name}` }
    }
    stage.set('live'); mode.set('case'); return 'opened cases'
  },
  list_cases: async () => {
    const list = await api.cases().catch(() => [])
    return list.length ? `${list.length} case${list.length === 1 ? '' : 's'}: ${list.slice(0, 6).map((c) => c.name).join(', ')}` : 'no cases yet'
  },
  track_object: () => { stage.set('live'); mode.set('pov'); objectRegister.set(true); return 'draw a box around the object to track' },
  find_pet: () => { stage.set('live'); mode.set('pov'); petRegistry.set(true); return 'pet finder opened' },
  open_spatial: ({ camera }) => {
    const id = findCam(S(camera))?.id ?? get(activeCam)
    if (!id) return 'no camera for the 3D scene'
    stage.set('live'); mode.set('pov'); spatialOpen.set(String(id))
    return '3D scene'
  },
  timeline: ({ on }) => { timelineOpen.set(on !== false); return on === false ? 'timeline hidden' : 'timeline open' },
  list_plates: async () => {
    const r = await api.watchedPlates().catch(() => null)
    const p = r?.plates ?? []
    return p.length ? `watching ${p.length} plate${p.length === 1 ? '' : 's'}: ${p.join(', ')}` : 'no plates on the watchlist'
  },
  unwatch_plate: async ({ plate }) => {
    const p = S(plate).toUpperCase().replace(/\s+/g, '')
    if (!p) return 'which plate?'
    await api.watchPlate(p, false).catch(() => {})
    return `stopped watching ${p}`
  },

  // ---- camera analytics / locate / recap ------------------------------------
  list_zones: () => {
    const zs = get(zones)
    return zs.length ? `${zs.length} zone${zs.length === 1 ? '' : 's'}: ${zs.map((z) => z.name).join(', ')}` : 'no zones drawn'
  },
  quietest_camera: async () => {
    const r = await api.cameraDna().catch(() => null)
    const cams = (r?.cameras ?? []).filter((c) => (c.frames ?? 0) > 20)
    if (!cams.length) return 'not enough data yet'
    const q = [...cams].sort((a, b) => ((a.person ?? 0) + (a.vehicle ?? 0)) - ((b.person ?? 0) + (b.vehicle ?? 0)))[0]
    return `quietest is ${q.name} (${(q.person ?? 0) + (q.vehicle ?? 0)} seen)`
  },
  night_cameras: async () => {
    const r = await api.cameraDna().catch(() => null)
    const n = (r?.cameras ?? []).filter((c) => (c.dna ?? []).some((t) => /night/.test(t))).map((c) => c.name)
    return n.length ? `night-dominant: ${n.join(', ')}` : 'no night-dominant cameras'
  },
  flagged_cameras: async () => {
    const r = await api.cameraDna().catch(() => null)
    const f = (r?.cameras ?? []).filter((c) => (c.reputation ?? 1) < 0.4 && (c.frames ?? 0) > 30).map((c) => c.name)
    return f.length ? `low detection quality: ${f.join(', ')}` : 'all cameras look healthy'
  },
  where_seen: async ({ subject }) => {
    const s = subject as { id?: string } | undefined
    if (!s?.id) return 'no subject given'
    const e = await api.rosterGet(s.id).catch(() => null)
    const trailCams = (e?.trail ?? []).map((t) => (t as { cam?: string }).cam).filter(Boolean)
    const uniq = [...new Set([e?.first_cam, ...trailCams, e?.cam].filter(Boolean))]
    return uniq.length ? `seen on: ${uniq.join(' → ')}` : 'no camera trail recorded'
  },
  locate: async ({ subject }) => {
    const s = subject as { id?: string; name?: string } | undefined
    if (!s?.id) return 'no subject to locate'
    const r = await api.findAcross(s.id).catch(() => null)
    const top = (r?.matches ?? []).slice().sort((a, b) => b.score - a.score)[0]
    if (!top) return `couldn't locate ${s.name || 'the subject'} right now`
    const cam = get(cameras).find((c) => c.id === top.camId || c.name === top.cam)
    if (cam) { stage.set('live'); mode.set('pov'); activeCam.set(cam.id); if (!SIM) sendCommand(`connect:${cam.name}`) }
    return `${s.name || 'the subject'} is on ${top.cam} (${Math.round(top.score * 100)}% match)`
  },
  repeat_last: () => {
    const last = [...get(operatorLog)].reverse().find((e) => e.kind === 'say')
    return last ? last.text : 'nothing to repeat'
  },

  // ---- experiential: narrate / follow-cam / occlusion x-ray ------------------
  narrate: ({ on }) => {
    const want = on !== false
    if (want) { stage.set('live'); mode.set('pov') }
    narrateOn.set(want)
    return want ? 'live narration on — I will describe the scene aloud' : 'narration off'
  },
  follow: ({ on }) => {
    const want = on !== false
    if (want && !get(selectedDetection)) return 'lock onto a target first, then I can follow it'
    followOn.set(want)
    return want ? 'follow-cam on' : 'follow-cam off'
  },
  xray: ({ on }) => {
    const want = on !== false
    xrayOn.set(want)
    return `occlusion x-ray ${want ? 'on — subjects behind cover stay tracked' : 'off'}`
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

  // enable/disable a detector; "alarm on / if you see a weapon" turns weapon detection on
  const MODS: Array<[RegExp, string]> = [
    [/silah|weapon|gun|knife|bıçak/i, 'weapon'],
    [/insan|kişi|person|people|pedestrian/i, 'person'],
    [/araç|araba|vehicle|\bcar\b/i, 'vehicle'],
    [/hayvan|animal/i, 'animal'],
    [/hareket|motion/i, 'motion'],
  ]
  if (/(tespit|detection|algıla|modül|module|dedekt)/i.test(low) ||
      /(silah|weapon|gun).*(alarm|gör|see|tetikle|trigger)|(alarm|gör|see).*(silah|weapon|gun)/i.test(low)) {
    for (const [re, key] of MODS) {
      if (re.test(low)) {
        const on = !/(kapat|disable|\boff\b|durdur|stop)/i.test(low)
        return { steps: [{ action: 'set_module', args: { key, on } }], border: key === 'weapon' ? 'alert' : 'nav' }
      }
    }
  }
  // show / hide any overlay or analyser by name
  if (/(show|hide|göster|gizle|enable|disable|toggle|\baç|kapat)/i.test(low)) {
    const OV: Array<[RegExp, string]> = [
      [/heat ?map|ısı ?harita/i, 'heatmap'], [/tactical|taktik|god ?view|radar/i, 'tactical'],
      [/foresight|öngörü|ghost|predict/i, 'ghosts'], [/tracklet|iz(ler)?/i, 'tracklet'],
      [/day.?night|gündüz|gece/i, 'daynight'], [/weather|hava durumu/i, 'weather'],
    ]
    for (const [re, key] of OV) {
      if (re.test(low)) {
        const on = !/(hide|gizle|kapat|disable|\boff\b|kaldır)/i.test(low)
        return { steps: [{ action: 'set_module', args: { key, on } }], border: 'nav' }
      }
    }
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
  const wantsOpen = /(\baç|open|göster|show|ekran|screen|geçiş)/i.test(low)
  if (wantsOpen) {
    for (const [re, screen] of SCREEN_WORDS) {
      if (re.test(low)) {
        if (screen === 'montage') return { steps: [{ action: 'side_by_side', args: {} }], border: 'nav' }
        return { steps: [{ action: 'open_screen', args: { name: screen } }], border: 'nav' }
      }
    }
  }

  // query: "how many people/cars (on camera X)?" / "kaç insan/araba var (X kamerasında)?"
  if (/(kaç|how many|how much)/i.test(low) && /(insan|kişi|kisi|adam|people|person|pedestrian|araç|araba|arac|\bcars?\b|vehicles?|alarm|alert)/i.test(low)) {
    const cls = /(araç|araba|arac|\bcars?\b|vehicles?)/i.test(low) ? 'vehicle' : /(alarm|alert)/i.test(low) ? 'alert' : 'person'
    const steps: Step[] = []
    const cm = low.match(/([\wçğıöşü-]+)\s*kameras[ıi]nda/i) || low.match(/\bon (?:the )?(.+?)(?:\s+camera)?\s*\??$/i)
    const camName = (cm?.[1] || cm?.[2] || '').trim()
    if (camName && findCam(camName)) steps.push({ action: 'switch_camera', args: { name: camName } })
    steps.push(cls === 'alert' ? { action: 'count_alerts', args: {} } : { action: 'count', args: { cls } })
    return { steps, border: 'nav' }
  }

  // quick view / system verbs
  if (/(zoom out|uzaklaş|reset (zoom|view)|görünümü sıfırla)/i.test(low)) return { steps: [{ action: 'reset_view', args: {} }], border: 'nav' }
  if (/(zoom|yakınlaş|büyüt)/i.test(low)) { const m = low.match(/(\d)/); return { steps: [{ action: 'zoom', args: { level: m ? Number(m[1]) : 2.5 } }], border: 'nav' } }
  if (/(next|sonraki)\s*(camera|kamera)|sonraki kameraya/i.test(low)) return { steps: [{ action: 'next_camera', args: { dir: 1 } }], border: 'nav' }
  if (/(previous|önceki|prev)\s*(camera|kamera)|önceki kameraya/i.test(low)) return { steps: [{ action: 'next_camera', args: { dir: -1 } }], border: 'nav' }
  if (/(unmute|sesi aç)/i.test(low)) return { steps: [{ action: 'mute', args: { on: false } }], border: 'nav' }
  if (/(\bmute\b|sustur|sesi kapat)/i.test(low)) return { steps: [{ action: 'mute', args: { on: true } }], border: 'nav' }
  if (/(haritaya (dön|geç)|ana ekran|go home|to the map|back to (the )?map)/i.test(low)) return { steps: [{ action: 'go_home', args: {} }], border: 'nav' }
  if (/(özetle|brief(ing)?|summary|summarize|neler oldu|ne oldu|what happened|shift report)/i.test(low)) return { steps: [{ action: 'summarize', args: {} }], border: 'nav' }
  if (/(acknowledge|onayla|okundu işaretle|clear (the )?alerts|alarmları onayla)/i.test(low)) return { steps: [{ action: 'acknowledge_alerts', args: {} }], border: 'nav' }
  if (/(camera (status|health)|kamera durumu|nasıl (görünüyor|durumda))/i.test(low)) return { steps: [{ action: 'camera_status', args: {} }], border: 'nav' }
  if (/(system|sistem).*(status|durum|load|yük)|kaynak kullanım|cpu.*gpu/i.test(low)) return { steps: [{ action: 'system_status', args: {} }], border: 'nav' }
  if (/(offline|çevrimdışı|kapalı|down).*(camera|kamera)|(camera|kamera).*(offline|çevrimdışı)/i.test(low)) return { steps: [{ action: 'offline_cameras', args: {} }], border: 'nav' }
  if (/(which|list|hangi).*(camera|kamera)|kameraları listele|list all cameras/i.test(low)) return { steps: [{ action: 'list_cameras', args: {} }], border: 'nav' }
  if (/(busiest|en (yoğun|kalabalık)).*(camera|kamera)/i.test(low)) return { steps: [{ action: 'busiest_camera', args: {} }], border: 'nav' }
  if (/(latest|last|son|en son).*(alert|alarm)/i.test(low)) return { steps: [{ action: 'latest_alert', args: {} }], border: 'nav' }
  if (/(clear|remove).*(zone|bölge)|(zone|bölge).*(temizle|sil|kaldır)/i.test(low)) return { steps: [{ action: 'clear_zones', args: {} }], border: 'nav' }
  if (/(what can you do|ne yapabilirsin|neler yapabilirsin|\bhelp\b|yardım|komutlar)/i.test(low)) return { steps: [{ action: 'help', args: {} }], border: 'nav' }
  if (/(false alarm|yanlış alarm|mark.*false|hatalı alarm)/i.test(low)) return { steps: [{ action: 'mark_false', args: {} }], border: 'nav' }
  if (/(close|kapat|dismiss).*(panel|overlay|pencere)|panelleri kapat/i.test(low)) return { steps: [{ action: 'close_panels', args: {} }], border: 'nav' }
  if (/(reconnect|yeniden bağlan|tekrar bağlan)/i.test(low)) return { steps: [{ action: 'reconnect', args: {} }], border: 'nav' }
  if (/(list|hangi|which).*(case|vaka|dava)|vakaları listele/i.test(low)) return { steps: [{ action: 'list_cases', args: {} }], border: 'nav' }
  if (/(list|hangi|which).*(plate|plaka)|plakaları listele/i.test(low)) return { steps: [{ action: 'list_plates', args: {} }], border: 'nav' }
  if (/(track|takip).*(object|nesne|obje)|nesne takip/i.test(low)) return { steps: [{ action: 'track_object', args: {} }], border: 'nav' }
  if (/(find|bul).*(pet|evcil)|evcil hayvan/i.test(low)) return { steps: [{ action: 'find_pet', args: {} }], border: 'nav' }
  if (/(timeline|zaman çizelgesi|olay geçmişi)/i.test(low)) return { steps: [{ action: 'timeline', args: {} }], border: 'nav' }
  if (/(quietest|en (sakin|sessiz)).*(camera|kamera)/i.test(low)) return { steps: [{ action: 'quietest_camera', args: {} }], border: 'nav' }
  if (/(night|gece).*(camera|kamera)|karanlık kamera/i.test(low)) return { steps: [{ action: 'night_cameras', args: {} }], border: 'nav' }
  if (/(list|hangi|which).*(zone|bölge)|bölgeleri listele/i.test(low)) return { steps: [{ action: 'list_zones', args: {} }], border: 'nav' }
  if (/(repeat|tekrar( et| söyle)|ne demiştin)/i.test(low)) return { steps: [{ action: 'repeat_last', args: {} }], border: 'nav' }
  if (/(narrat|sesli anlat|canlı anlat|anlatmaya başla|start describing)/i.test(low)) return { steps: [{ action: 'narrate', args: { on: !/(kapat|stop|\boff\b|durdur|sustur)/i.test(low) } }], border: 'nav' }
  if (/(follow.?cam|takip et|takip kamerası|follow (the |that )?(target|subject|him|her|it|kişi))/i.test(low)) return { steps: [{ action: 'follow', args: { on: !/(kapat|stop|\boff\b|durdur|bırak)/i.test(low) } }], border: 'nav' }
  if (/(x.?ray|röntgen|thru cover|behind cover|arkasını gör|occlusion)/i.test(low)) return { steps: [{ action: 'xray', args: { on: !/(kapat|\boff\b|disable|gizle)/i.test(low) } }], border: 'nav' }
  if (/(plaka|plate)\s*[:#]?\s*([a-z0-9]{4,})/i.test(low)) { const m = low.match(/(plaka|plate)\s*[:#]?\s*([a-z0-9\s]{4,})/i); return { steps: [{ action: 'find_plate', args: { plate: m?.[2] ?? '' } }], border: 'nav' } }

  // "search/find <q>" / "<q> ara/bul"
  const sm = low.match(/(?:forensic\s*)?(?:search|find|ara|bul|aratt?[ıi]r)\b[:\s]+(.{2,})/i) ||
             low.match(/^(.{2,}?)\s+(?:ara|bul)\.?$/i)
  if (sm && sm[1]) return { steps: [{ action: 'forensic_search', args: { query: sm[1].trim() } }], border: 'nav' }

  return null
}

// Does a plan change what's on screen (navigation), or is it just an answer/query? The console
// keeps answers in the full panel and only docks to the companion for navigation.
export function planNavigates(plan: Plan): boolean {
  return (plan.steps ?? []).some((s) => !ANSWER_ACTIONS.has(s.action))
}

// ---- executor ------------------------------------------------------------------------------
// Actions whose return value is an ANSWER to the operator (spoken + shown as a reply), not just a
// step log. The last such answer in a chain becomes the plan's spoken reply.
const ANSWER_ACTIONS = new Set(['count', 'count_people', 'count_vehicles', 'count_alerts', 'describe_scene',
  'last_seen', 'camera_status', 'camera_dna', 'summarize', 'correlate_alerts', 'system_status', 'storage_status',
  'list_cameras', 'offline_cameras', 'busiest_camera', 'latest_alert', 'explain_alert', 'advise_alert',
  'search_events', 'count_subjects', 'list_watched', 'stats', 'help', 'relationships', 'alerts_here',
  'list_cases', 'list_plates', 'list_zones', 'quietest_camera', 'night_cameras', 'flagged_cameras',
  'where_seen', 'repeat_last', 'say'])

export async function runPlan(plan: Plan): Promise<void> {
  if (plan.ask) { olog(plan.ask, 'ask'); return }
  const steps = plan.steps ?? []
  if (!steps.length && plan.say) { olog(plan.say, 'say'); return }
  operatorBusy.set(true)
  operatorActive.set(plan.border ?? 'nav')
  let lastAnswer = ''
  const bag: Record<string, unknown> = {}   // results of prior steps, for "$ref" data passing
  try {
    for (const step of steps) {
      const fn = ACTIONS[step.action]
      if (!fn) { olog(`unknown action: ${step.action}`, 'error'); continue }
      try {
        const r = await fn(resolveArgs(step.args, bag))
        const summary = typeof r === 'string' ? r : (r?.say ?? '')
        const value = typeof r === 'string' || !r ? undefined : r.value
        if (step.as && value !== undefined) bag[step.as] = value
        const isAnswer = ANSWER_ACTIONS.has(step.action)
        if (summary) { olog(summary, isAnswer ? 'say' : 'step'); if (isAnswer) lastAnswer = summary }
      } catch (e) {
        olog(`${step.action} failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
      }
    }
    if (plan.say) olog(plan.say, 'say')
    else if (lastAnswer) plan.say = lastAnswer   // so the caller speaks the answer
  } finally {
    // let the border linger a beat so a fast chain still registers visually
    setTimeout(() => operatorActive.set(null), 900)
    operatorBusy.set(false)
  }
}

// ---- top-level entry: route locally, else ask the server planner ---------------------------
// A request with a sequence marker ("then", "and then", "sonra", "ardından", ";") or several
// clauses is multi-step: the single-shot deterministic router would grab only the first part and
// stop, so we send the WHOLE thing to the LLM planner, which builds the full chain.
function isCompound(cmd: string): boolean {
  const low = cmd.toLowerCase()
  if (/(;|\bsonra\b|\bsonrasında\b|\bardından\b|daha sonra|then\b|after that|and then|ve sonra|bir de)/.test(low)) return true
  // several imperative verbs -> likely a chain
  const verbs = (low.match(/\b(geç|git|aç|bul|ara|ekle|yap|getir|göster|oluştur|netleştir|izle|say|switch|go|open|find|search|add|make|get|show|create|enhance|watch|count)\b/g) || []).length
  return verbs >= 2
}

export async function operate(command: string): Promise<Plan> {
  const cmd = command.trim()
  if (!cmd) return { say: '' }
  olog(cmd, 'you')
  // Simple single commands take the instant local path; compound ones go straight to the planner.
  const local = isCompound(cmd) ? null : routeCommand(cmd)
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
    if (plan?.disabled) { olog('the AI Operator needs a provider configured (settings ⚙)', 'error'); return plan }
    // A question the planner didn't turn into actions or an answer -> answer it as chat.
    if (!plan?.steps?.length && !plan?.say && !plan?.ask) {
      const { reply, disabled } = await api.aiChat(
        cmd, 'You are Overseer, a concise surveillance operations assistant. Answer in 1-2 sentences.')
      const say = disabled ? 'chat needs a provider configured (settings ⚙)' : (reply || 'no answer')
      olog(say, 'say')
      return { say }
    }
    await runPlan(plan)
    return plan
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    olog(`could not plan that: ${msg}`, 'error')
    return { say: msg }
  }
}
