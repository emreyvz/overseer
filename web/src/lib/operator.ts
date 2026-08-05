// OVERSEER — AI Operator: turn a natural-language (typed or spoken) command into a chain of
// concrete system actions, then run them. A fast deterministic router handles the common,
// unambiguous commands with zero latency; anything complex or multi-step is planned by the LLM
// (server /api/ai/operate) which returns the same {steps,say} shape. While a plan runs, the
// screen border lights up (green = navigation/query, red = alarm/critical) so the operator can
// see the AI is driving, and every step is written to a transparent transcript.
import { get, writable } from 'svelte/store'
import {
  mode, activeCam, cameras, stage, forensicSeed, zoneEditor, alertRules, watchlistOpen, operatorOpen,
  suggestionsOpen, spatialOpen, walkthroughAuto, storageScreen, commandOpen, investigateCase, alertsScreen, objectRegister,
  rosterInit, modules, toggleModule, detections, alerts, timeline, timelineOpen, petRegistry,
  povZoom, muted, frame, system, narrateOn, followOn, xrayOn, enhanceMode, selectedDetection,
  flashBanner, triggerGlitch, coverage, coverageScreen, blindSpots, grainScreen, grainStatus,
  divergences, dreamConsole, dreamStatus, bedrockQuery, bedrockResult, bedrockAsOf,
  probes, probeFrames, eardrumDrawer, listenPlacing, type Mode,
} from './stores'
import { setFogTask } from './fog'
import { runQuery as runBedrock } from './bedrock'
import type { DoriTask } from './types'
import { sendCommand } from './ws'
import { SIM } from './sim'
import { api } from './api'
import { annotate } from './annotations'
import { zones, delZone } from './zones'
import { recordFeedback } from './feedback'
import { aiStatus } from './ai'
import { sfx, toggleMute } from './audio'

// A compact guide to the whole app, given to the assistant so it can answer "how do I…" and
// "what does X do" questions about ANY feature, not just run commands.
export const APP_GUIDE = `Overseer is a single-camera AI surveillance app. You are its operator assistant: you can DO things
(the actions) and also EXPLAIN how to use any part of the app. Key knowledge:
SCREENS (open by voice/text, a nav button, or a key): POV live view; Roster (people/vehicles seen,
filter by colour/type/height/BOLO); Forensic (appearance search by colour/type/plate/time); Watchlist;
Smart Suggestions (key G — proactive alert & zone recommendations you accept in one click); Spatial 3D
(key D — lift a frame into a 3D scene); Storage; Cases; Alerts board (all alerts across cameras);
Alert Rules; Zone editor (key Z).
HOW TO ADD AN ALERT: three ways — (1) open Smart Suggestions (G) and accept a proposed rule; (2) open
Alert Rules and add one; (3) just tell me, e.g. "alarm on loitering at the store" or "alarm if you see
a weapon" (a weapon turns on weapon detection which auto-alerts).
HOW TO MAKE A ZONE: press Z (or say "draw zone") to draw a line/area on the POV, or accept a zone
suggestion in Smart Suggestions (it proposes where to put it). Zones drive loitering/line-cross/
restricted alerts.
HOW TO WATCH SOMEONE/A PLATE: click a subject then ENROLL, or say "watch that person"/"watch plate 34ABC".
DETECTION & OVERLAYS: the left panel toggles every detector (person/vehicle/animal/weapon/motion/
tracking) and overlays (heatmap, tactical, foresight, tracklet), plus OCCLUSION X-RAY (keep tracking a
subject behind cover) and LIVE NARRATION (describe the scene aloud). Say "show the heatmap" etc.
EXPERIENTIAL: Follow-Cam (key F or the FOLLOW button on a locked target's card) keeps them centred;
Live Narration (key N) speaks what the camera sees; Occlusion X-ray shows subjects behind cover;
Enhance (key E or the ⊹ ENHANCE button) lets you drag a box on the frame to clarify that region.
SHORTCUTS: I operator, G suggestions, Z zone, W watchlist, A forensic, D spatial 3D, N narration,
F follow, E enhance, 1-9 switch camera, SPACE command palette.
YOU (the operator) can chain 70+ actions from one sentence and answer questions from live data.`

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
  // Look at the live frame and answer a visual question (colour, object, what someone holds…) —
  // sends a snapshot to the vision model, for anything not in our structured data.
  ask_vision: async ({ question, camera }) => {
    const id = findCam(S(camera))?.id ?? get(activeCam)
    if (!id) return 'no camera to look at'
    const q = S(question); if (!q) return 'what should I look for in the frame?'
    const r = await api.aiVqa(String(id), q).catch(() => null)
    return r?.answer || (r?.disabled ? 'this needs a vision model configured (settings ⚙)' : 'could not read the frame')
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
    if (col) cand = cand.filter((r) => {
      const uc = (r.attrs?.upper_color ?? '').toLowerCase(), lc = (r.attrs?.lower_color ?? '').toLowerCase(), st = (r.attrs?.subtype ?? '').toLowerCase()
      return uc.includes(col) || col.includes(uc) && !!uc || lc.includes(col) || st.includes(col)
    })
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
  summarize_case: async ({ id }) => {
    const cid = id != null && !isNaN(Number(id)) ? Number(id) : get(investigateCase)
    if (cid == null) return 'open a case first, or tell me which one'
    const d = await api.caseDetail(Number(cid)).catch(() => null)
    if (!d) return 'could not load that case'
    if (d.aiSummary) return d.aiSummary
    const ev = (d.events ?? []).slice(0, 30).map((e) => ({ type: (e as { type?: string }).type ?? '?', cam: (e as { cam?: string }).cam ?? '', label: (e as { label?: string }).label }))
    const r = ev.length ? await api.aiSummarize(ev).catch(() => null) : null
    return r?.summary || `${d.name}: ${d.events?.length ?? 0} events, ${d.subjects?.length ?? 0} subjects`
  },
  alerts_with_clips: () => {
    const withClip = get(alerts).filter((a) => a.clip)
    stage.set('live'); alertsScreen.set(true)
    return withClip.length ? `${withClip.length} alert${withClip.length === 1 ? '' : 's'} have a replay clip` : 'no alerts have a clip yet'
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
  // Social X-ray: draw each person's attention direction + who is interacting with whom.
  social_xray: ({ on }) => {
    const want = on !== false
    if (want) { stage.set('live'); mode.set('pov') }
    const m = get(modules).find((x) => x.key === 'social')
    if (m && m.on !== want) toggleModule('social')
    return want ? 'social x-ray on — showing gaze and who is interacting' : 'social x-ray off'
  },
  // Walkthrough: open the 3D view in live mode so you can move through the scene as it rebuilds.
  walkthrough: ({ camera }) => {
    const id = camera ? (findCam(S(camera))?.id ?? get(activeCam)) : get(activeCam)
    if (!id) return 'no camera selected for the 3D walkthrough'
    stage.set('live'); mode.set('pov'); walkthroughAuto.set(true); spatialOpen.set(String(id))
    return 'opening the 3D walkthrough — the scene keeps rebuilding; drag to move through it in 3D'
  },
  enhance: () => {
    stage.set('live'); mode.set('pov'); enhanceMode.set(true)
    return 'draw a box on the frame and I will clarify that region'
  },

  // ── FOG OF WAR ────────────────────────────────────────────────────────────────────────────
  fog_of_war: ({ on }) => {
    const want = on !== false
    if (want) { stage.set('live'); mode.set('pov') }
    const m = get(modules).find((x) => x.key === 'unseen')
    if (m && m.on !== want) toggleModule('unseen')
    return want
      ? 'fog of war on — everything this camera cannot see is now drawn as static'
      : 'fog of war off'
  },
  coverage_report: () => {
    stage.set('live'); coverageScreen.set(true)
    const c = get(coverage)
    return c
      ? { say: `coverage is ${Math.round(c.percent)} percent of the observed ground at the ${c.task} standard`,
          value: c.percent }
      : 'opening the coverage report — building the observability field now'
  },
  coverage_task: ({ task }) => {
    const t = S(task).toLowerCase()
    const valid = ['detect', 'observe', 'recognise', 'recognize', 'identify']
    if (!valid.includes(t)) return `pick one of: detect, observe, recognise, identify`
    setFogTask((t === 'recognize' ? 'recognise' : t) as DoriTask)
    return `coverage is now measured against the ${t} standard`
  },
  blind_spots: () => {
    const spots = get(blindSpots).filter((s) => !s.dismissed && s.persistent)
    stage.set('live'); coverageScreen.set(true)
    if (!spots.length) return { say: 'no persistent blind spots on this camera', value: 0 }
    const worst = spots[0]
    return { say: `${spots.length} persistent blind spot${spots.length === 1 ? '' : 's'}; the worst is ${worst.name}`,
             value: spots.length }
  },

  // ── GRAIN ─────────────────────────────────────────────────────────────────────────────────
  grain: ({ on }) => {
    const want = on !== false
    if (want) { stage.set('live'); mode.set('pov') }
    const m = get(modules).find((x) => x.key === 'grain')
    if (m && m.on !== want) toggleModule('grain')
    return want
      ? 'grain on — showing the learned current of this place, and how ordinary each subject is'
      : 'grain off'
  },
  who_is_odd: () => {
    const odd = get(detections).filter((d) => d.conformity?.state === 'unusual')
    if (!odd.length) {
      const st = get(grainStatus)
      if (st && !st.mature) {
        return { say: `still learning this place — ${Math.round(st.maturity * 100)} percent of the way there`, value: 0 }
      }
      return { say: 'nobody in view is moving unusually for this place', value: 0 }
    }
    const worst = odd.reduce((a, b) => ((a.conformity!.p <= b.conformity!.p) ? a : b))
    selectedDetection.set(worst)
    return {
      say: `${odd.length} subject${odd.length === 1 ? '' : 's'} moving unusually; ${worst.id} is at the ${worst.conformity!.p.toFixed(1)} percentile — ${worst.conformity!.why}`,
      value: odd.length,
    }
  },
  grain_model: () => {
    stage.set('live'); grainScreen.set(true)
    const st = get(grainStatus)
    return st
      ? { say: `learned from ${st.tracks} tracks over ${st.days} days`, value: st.tracks }
      : 'opening the grain model'
  },

  // ── DREAMSTATE ────────────────────────────────────────────────────────────────────────────
  dreamstate: ({ on }) => {
    const want = on !== false
    if (want) { stage.set('live'); mode.set('pov') }
    const m = get(modules).find((x) => x.key === 'dream')
    if (m && m.on !== want) toggleModule('dream')
    return want
      ? 'dreamstate on — I will mark anything that does not match what this place normally looks like at this hour'
      : 'dreamstate off'
  },
  anything_odd: () => {
    const st = get(dreamStatus)
    const recent = get(divergences).filter((d) => Date.now() - d.ts < 3600_000)
    if (st && st.maturity < 1) {
      return { say: `still learning this hour — ${Math.round(st.maturity * 100)} percent of the way there, so I am not reporting yet`, value: 0 }
    }
    if (!recent.length) return { say: 'nothing has diverged in the last hour; the scene is behaving', value: 0 }
    const worst = recent.reduce((a, b) => (a.peak_sigma >= b.peak_sigma ? a : b))
    dreamConsole.set(worst.id)
    return {
      say: `${recent.length} divergence${recent.length === 1 ? '' : 's'} in the last hour; the largest is ${worst.peak_sigma.toFixed(1)} sigma and looks like a ${worst.triage === 'subject' ? 'subject behaviour' : 'scene change'}`,
      value: recent.length,
    }
  },
  dream_console: () => { stage.set('live'); dreamConsole.set('live'); return 'opening the dreamstate console' },
  dream_sensitivity: async ({ sigma }) => {
    const v = Math.max(3, Math.min(8, Number(sigma) || 5))
    const cam = get(activeCam)
    if (cam && !SIM) await api.dreamThreshold(cam, v).catch(() => undefined)
    return `divergence threshold set to ${v.toFixed(1)} sigma`
  },

  // ── BEDROCK ───────────────────────────────────────────────────────────────────────────────
  bedrock_query: async ({ text }) => {
    stage.set('live'); mode.set('bedrock')
    const question = S(text)
    if (!question) return 'opening bedrock'
    if (SIM) return `asking bedrock: ${question}`
    try {
      const r = await api.aiBedrock(question)
      if (!r.query) return r.say ?? 'I could not turn that into a query I can run'
      bedrockQuery.set(r.query)
      await runBedrock(r.query)
      const res = get(bedrockResult)
      return {
        say: res ? `${res.entities.length} match${res.entities.length === 1 ? '' : 'es'} in the record` : 'query ran',
        value: res?.entities.length ?? 0,
      }
    } catch { return 'bedrock is unreachable' }
  },
  bedrock_asof: async ({ when }) => {
    // belief-time travel: "what did we know last Tuesday"
    const ms = Number(when)
    bedrockAsOf.set(Number.isFinite(ms) && ms > 0 ? ms : null)
    stage.set('live'); mode.set('bedrock')
    await runBedrock()
    return ms > 0
      ? `showing what the system believed on ${new Date(ms).toLocaleString()}`
      : 'back to what the system believes now'
  },

  // ── EARDRUM ───────────────────────────────────────────────────────────────────────────────
  listen: ({ on }) => {
    const want = on !== false
    if (want) { stage.set('live'); mode.set('pov'); listenPlacing.set(true) }
    else listenPlacing.set(false)
    const m = get(modules).find((x) => x.key === 'listen')
    if (m && m.on !== want) toggleModule('listen')
    return want
      ? 'listening mode on — drag a box on a textured, rigid surface and I will read its vibration'
      : 'listening off'
  },
  probe_status: () => {
    const list = get(probes)
    if (!list.length) return { say: 'no probes are placed on this camera', value: 0 }
    const frames = get(probeFrames)
    const hot = list.filter((p) => (frames[String(p.id)]?.db ?? 0) >= 6)
    stage.set('live'); eardrumDrawer.set(true)
    if (!hot.length) return { say: `${list.length} probes, all within their baseline`, value: 0 }
    const worst = hot.reduce((a, b) =>
      ((frames[String(a.id)]?.db ?? 0) >= (frames[String(b.id)]?.db ?? 0) ? a : b))
    const f = frames[String(worst.id)]
    const peak = f?.peaks?.[0]
    return {
      say: `${worst.name} is ${f?.db.toFixed(1)} dB above its baseline`
        + (peak ? `, strongest at ${peak.hz.toFixed(1)} hertz` : ''),
      value: hot.length,
    }
  },
  set_baseline: async () => {
    const list = get(probes).filter((p) => p.kind !== 'ref')
    if (!list.length) return 'place a probe first'
    if (!SIM) for (const p of list) await api.probeBaseline(p.id).catch(() => undefined)
    return `baseline frozen for ${list.length} probe${list.length === 1 ? '' : 's'}`
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
  if (/(social x.?ray|sosyal|gaze|attention|who.*(talking|interact)|kim.*(konuş|etkileş)|bakış)/i.test(low)) return { steps: [{ action: 'social_xray', args: { on: !/(kapat|\boff\b|disable|gizle)/i.test(low) } }], border: 'nav' }
  // FOG OF WAR — "what can't you see", "kör nokta", "coverage"
  if (/(blind ?spot|kör nokta|göremediğ|what.*(can.?t|cannot).*see|neyi göremiyor)/i.test(low)) return { steps: [{ action: 'blind_spots' }], border: 'nav' }
  if (/(coverage|kapsama|kaç.*yüzde.*gör|how much.*see)/i.test(low)) return { steps: [{ action: 'coverage_report' }], border: 'nav' }
  if (/(fog of war|savaş sisi|sis(i)? (aç|kapat)|unseen)/i.test(low)) return { steps: [{ action: 'fog_of_war', args: { on: !/(kapat|\boff\b|disable|gizle)/i.test(low) } }], border: 'nav' }
  // GRAIN — "who is behaving oddly", "normal nedir burada"
  if (/(who.*(odd|unusual|strange|weird|out of place)|kim.*(tuhaf|garip|anormal|sıra ?dışı))/i.test(low)) return { steps: [{ action: 'who_is_odd' }], border: 'nav' }
  if (/(grain model|learned (normal|pattern)|öğrenilen|normal nedir|akış deseni)/i.test(low)) return { steps: [{ action: 'grain_model' }], border: 'nav' }
  if (/(grain|doku|davranış deseni|behaviou?ral (grain|field))/i.test(low)) return { steps: [{ action: 'grain', args: { on: !/(kapat|\boff\b|disable|gizle)/i.test(low) } }], border: 'nav' }
  // DREAMSTATE — "is anything off", "her şey normal mi"
  if (/(anything (odd|off|unusual|wrong)|her ?şey normal|bir ?şey var mı|divergence|sapma)/i.test(low)) return { steps: [{ action: 'anything_odd' }], border: 'nav' }
  if (/(dream ?state|rüya|beklenti modeli|expectation model)/i.test(low)) return { steps: [{ action: 'dreamstate', args: { on: !/(kapat|\boff\b|disable|gizle)/i.test(low) } }], border: 'nav' }
  // BEDROCK — questions about the PAST go to the fact store, not to live detections
  if (/(has .*(been here|come here) before|daha önce (geldi|burada)|ever been here|kaç kez geldi)/i.test(low)) return { steps: [{ action: 'bedrock_query', args: { text }  }], border: 'nav' }
  if (/(bedrock|kayıt defteri|fact store|what did we (know|believe))/i.test(low)) return { steps: [{ action: 'bedrock_query', args: { text }  }], border: 'nav' }
  // EARDRUM — "listen to that", "titreşim"
  if (/(vibration|titreşim|probe status|makine sesi|machine (sound|health))/i.test(low)) return { steps: [{ action: 'probe_status' }], border: 'nav' }
  if (/(eardrum|listen|dinle|kulak)/i.test(low)) return { steps: [{ action: 'listen', args: { on: !/(kapat|off|disable|dur)/i.test(low) } }], border: 'nav' }
  if (/(walkthrough|walk.?through|chronoscape|zaman yolcul|3d.*(gez|dolaş|walk|yürü)|içinde (gez|dolaş))/i.test(low)) return { steps: [{ action: 'walkthrough', args: {} }], border: 'nav' }
  if (/(enhance|netleştir|yakınlaş.*netleş|clarify|zoom.*enhance|büyüt.*netleş)/i.test(low)) return { steps: [{ action: 'enhance', args: {} }], border: 'nav' }
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
const ANSWER_ACTIONS = new Set(['count', 'count_people', 'count_vehicles', 'count_alerts', 'describe_scene', 'ask_vision',
  'last_seen', 'find_subject', 'watch_subject', 'super_fuse', 'unwatch_subject',
  'camera_status', 'camera_dna', 'summarize', 'correlate_alerts', 'system_status', 'storage_status',
  'list_cameras', 'offline_cameras', 'busiest_camera', 'latest_alert', 'explain_alert', 'advise_alert',
  'search_events', 'count_subjects', 'list_watched', 'stats', 'help', 'relationships', 'alerts_here',
  'list_cases', 'list_plates', 'list_zones', 'quietest_camera', 'night_cameras', 'flagged_cameras',
  'where_seen', 'repeat_last', 'summarize_case', 'alerts_with_clips', 'say'])

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
    // The ACTUAL outcome (an answer/verify action's result) wins over the LLM's optimistic "Done"
    // confirmation, so we never say "added to the watchlist" when the subject wasn't found.
    if (lastAnswer) plan.say = lastAnswer            // already logged in the loop
    else if (plan.say) olog(plan.say, 'say')
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
  // When a provider is configured, the LLM interprets EVERYTHING — no local keyword guessing, so a
  // stray word never fires the wrong command. The deterministic router is only a fallback for when
  // there is no LLM at all.
  const aiUp = get(aiStatus).enabled
  const local = aiUp ? null : routeCommand(cmd)
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
    // A question the planner didn't turn into actions or an answer -> answer it.
    if (!plan?.steps?.length && !plan?.say && !plan?.ask) {
      // If it is about the scene (not a how-to), LOOK at the active camera and answer from the frame
      // — so "what is the boat at the dock?" gets a visual answer instead of a dead-end chat reply.
      const howTo = /\bhow (do|to|can) i\b|how to|nas[ıi]l|nereden|where.*(button|setting|menu|option|tab)|hangi (men[üu]|ekran|sekme)/i.test(cmd)
      const camId = get(activeCam)
      if (camId && !howTo) {
        const r = await api.aiVqa(String(camId), cmd).catch(() => null)
        if (r?.answer) { olog(r.answer, 'say'); return { say: r.answer } }
      }
      const { reply, disabled } = await api.aiChat(
        cmd, `${APP_GUIDE}\n\nAnswer the operator concisely. For "how do I…" or "what does X do" give the
quick steps from the guide above; for anything else answer in 1-2 sentences.`)
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
