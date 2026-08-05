// OVERSEER — REST client for the FastAPI bridge. Falls back gracefully when offline.
import type * as T from './types'

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'

async function get<T>(path: string): Promise<T> {
  const r = await fetch(API_BASE + path)
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json() as Promise<T>
}
async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(API_BASE + path, {
    method, headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json() as Promise<T>
}
const post = <T>(p: string, b: unknown) => send<T>('POST', p, b)
const put = <T>(p: string, b: unknown) => send<T>('PUT', p, b)
const del = <T>(p: string) => send<T>('DELETE', p)

export interface SearchHit { kind: string; ts: number; type: string; label: string; snapshot?: string | null }
export interface EventRow { id: number; ts: number; type: string; label: string; conf?: number | null; snapshot?: string | null }
export interface AlertRow { id: number; ts: number; severity: string; type: string; summary: string; cam: string; ack: boolean; snapshot?: string | null; clip?: string | null }
export interface Associate { id: string; count: number; cameras: string[]; first: number; last: number; confidence: number; cls: string; snapshot?: string | null; plate?: string | null; cam?: string | null }
export interface GraphNode { id: string; cls: string; snapshot?: string | null; plate?: string | null }
export interface GraphEdge { a: string; b: string; count: number; confidence: number; cameras: string[] }
export interface SocialGraph { nodes: GraphNode[]; edges: GraphEdge[] }
export interface EgoNode { id: string; hop: number; cls: string; snapshot?: string | null; plate?: string | null }
export interface EgoGraph { center: string; nodes: EgoNode[]; edges: GraphEdge[] }
export interface MergeCandidate { a: import('./types').RosterEntry; b: import('./types').RosterEntry; similarity: number; reason: string }
export interface CameraDna { id: number | string; name?: string | null; dna: string[]; reputation: number; frames: number; brightness?: number; motion?: number; fps?: number; reconnects?: number; person?: number; vehicle?: number }
export interface CaseRow { id: number; name: string; threat: string; notes: string; status?: string; created: number; targets: number }
export interface CaseEventRow { ts: number; kind: string; type: string; cam: string; severity: string; summary: string; snapshot?: string | null; clip?: string | null }
export interface SceneSubject { id: string; cls: string; snapshot?: string | null; plate?: string | null; seen: number; associates: Associate[] }
export interface CaseDetail { id: number; name: string; threat: string; notes: string; status: string; created: number; cameras: string[]; events: CaseEventRow[]; subjects: SceneSubject[]; aiSummary: string | null }
export interface SpatialEntity { id: string; cls: string; cx: number; cy: number; depth: number; conf: number; label: string }
export interface SpatialScene { cam: string; sid: string; w: number; h: number; fov: number; image: string; depth: string; entities: SpatialEntity[]; ts: number; bg_image?: string; bg_depth?: string; tex_image?: string }
export interface SuggestRule { name: string; event_type: string; source_id: number; severity: string }
export interface Suggestion {
  kind: 'alert' | 'camera' | 'zone' | 'coverage'
  cam: string; title: string; why: string; count?: number
  rule?: SuggestRule
  zone?: [number, number][]
  // FOG OF WAR: a persistent blind spot surfaced as a work item rather than a picture
  spot?: { id: number; polygon: [number, number][]; kind: string }
}
export interface Subject { id: number; cls: string; label?: string | null; first_seen: number; last_seen: number; sighting_count: number; day_count: number; plate?: string | null; attrs: Record<string, unknown>; snapshot?: string | null; watched: boolean; flags: string[] }
export interface Sighting { id: number; cam?: string | null; ts: number; snapshot?: string | null }
export interface Dossier extends Subject { per_camera: { cam: string; count: number }[]; hour_histogram: number[]; distinct_days: number; sightings: Sighting[] }
export interface Reconstruction { image: string | null; reason?: string; method?: string; frames_used?: number; frames_offered?: number }

export const api = {
  base: API_BASE,
  search: (q: string, opt: { source?: string; start?: number; end?: number } = {}) => {
    const p = new URLSearchParams({ q })
    if (opt.source) p.set('source', opt.source)
    if (opt.start) p.set('start', String(opt.start))
    if (opt.end) p.set('end', String(opt.end))
    return get<{ hits: SearchHit[]; deferred?: string[]; unmatched?: string[] }>(`/api/search?${p}`)
  },
  events: (limit = 200) => get<EventRow[]>(`/api/events?limit=${limit}`),
  alerts: (limit = 200) => get<AlertRow[]>(`/api/alerts?limit=${limit}`),
  stats: (start: number, end: number) => get<Record<string, number>>(`/api/stats?start=${start}&end=${end}`),
  storage: () => get<{ recordings: number; sizeGB: number; oldest: number; recent: { kind: string; start: number; end: number; sizeMB: number; mode: string }[] }>(`/api/storage`),
  recordings: () => get<{ id: number; kind: string; mode: string; start: number; end: number; sizeMB: number; url: string | null }[]>(`/api/recordings`),
  deleteRecording: (id: number) => del<{ ok: boolean }>(`/api/recordings/${id}`),
  storageCleanup: (what: 'snapshots' | 'clips' | 'recordings') => post<{ ok: boolean; removed: number }>(`/api/storage/cleanup`, { what }),
  cases: () => get<CaseRow[]>(`/api/cases`),
  addCase: (name: string) => post<{ id: number }>(`/api/cases`, { name }),
  caseDetail: (id: number) => get<CaseDetail>(`/api/cases/${id}`),
  caseFromAlert: (alert: unknown) => post<{ id: number }>(`/api/cases/from-alert`, { alert }),
  caseStatus: (id: number, status: string) => post<{ ok: boolean }>(`/api/cases/${id}/status`, { status }),
  updateCase: (id: number, patch: { name?: string; threat?: string; notes?: string }) => put<{ ok: boolean }>(`/api/cases/${id}`, patch),
  deleteCase: (id: number) => del<{ ok: boolean }>(`/api/cases/${id}`),
  addSource: (name: string, url: string) => post<{ id: number }>(`/api/sources`, { name, url }),
  discover: (user?: string, password?: string, timeout?: number) =>
    post<{ devices: { ip: string; name?: string; hardware?: string; location?: string; xaddr?: string; rtsp?: string | null }[] }>(`/api/discover`, { user, password, timeout }),
  updateSource: (id: string, name: string, url: string) => put<{ ok: boolean }>(`/api/sources/${id}`, { name, url }),
  deleteSource: (id: string) => del<{ ok: boolean }>(`/api/sources/${id}`),
  setCoords: (id: string, lat: number, lng: number) =>
    post<{ ok: boolean; coords: [number, number] }>(`/api/sources/${id}/coords`, { lat, lng }),
  ptz: (id: string, pan: number, tilt: number, zoom: number) =>
    post<{ ok: boolean; reason?: string }>(`/api/ptz/${id}`, { pan, tilt, zoom }),
  inspect: (id: string, x: number, y: number) =>
    post<{ detections: import('./types').Detection[] }>(`/api/inspect/${id}`, { x, y }),
  visualMatch: (image: string, kind?: string, minScore?: number) =>
    post<{ matches: { camId: string; cam: string; score: number; cls?: string; margin?: number; ambiguous?: boolean; bbox: [number, number, number, number] }[] }>(`/api/visualmatch`, { image, kind, minScore }),
  plateMatch: (plate: string) =>
    post<{ matches: { camId: string; cam: string; plate: string; score: number; bbox: [number, number, number, number] }[] }>(`/api/platematch`, { plate }),
  roster: () => get<import('./types').RosterEntry[]>(`/api/roster`),
  rosterGet: (id: string) => get<import('./types').RosterEntry>(`/api/roster/${id}`),
  watchRoster: (id: string, on: boolean) =>
    post<import('./types').RosterEntry>(`/api/roster/${id}/watch`, { on }),
  supercut: (id: string) => get<{ url: string }>(`/api/roster/${id}/supercut`),
  entityRelationships: (id: string) => get<{ associates: Associate[] }>(`/api/roster/${id}/relationships`),
  entityGraph: (id: string) => get<EgoGraph>(`/api/roster/${id}/graph`),
  relationships: () => get<SocialGraph>(`/api/relationships`),
  cameraDna: () => get<{ cameras: CameraDna[] }>(`/api/cameras/dna`),
  spatial: (sid: string, grid = 320) => get<{ scene: SpatialScene | null; reason?: string }>(`/api/spatial/${sid}?grid=${grid}`),
  spatialReel: (sid: string, n = 28, grid = 256) => get<{ frames: SpatialScene[]; reason?: string }>(`/api/spatial/reel/${sid}?n=${n}&grid=${grid}`),
  subjects: (cls?: string, limit = 200) => get<Subject[]>(`/api/subjects?limit=${limit}${cls ? '&cls=' + cls : ''}`),
  subjectDossier: (id: number) => get<{ dossier: Dossier | null }>(`/api/subjects/${id}/dossier`),
  subjectReconstruct: (id: number) => get<Reconstruction>(`/api/subjects/${id}/reconstruct`),
  reconstructPlate: (detId: string) => get<Reconstruction>(`/api/reconstruct/plate/${encodeURIComponent(detId)}`),
  suggestions: () => get<{ suggestions: Suggestion[] }>(`/api/suggestions`),
  addAlertRule: (rule: SuggestRule) => post<{ id: number }>(`/api/alerts/rules`, rule),
  mergeCandidates: () => get<{ candidates: MergeCandidate[] }>(`/api/roster/merge-candidates`),
  mergeRoster: (keep: string, drop: string) => post<import('./types').RosterEntry>(`/api/roster/merge`, { keep, drop }),
  mergeReject: (a: string, b: string) => post<{ ok: boolean }>(`/api/roster/merge-reject`, { a, b }),
  findAcross: (id: string) =>
    post<{ matches: { camId: string; cam: string; score: number; ambiguous?: boolean }[] }>(`/api/roster/${id}/find`, {}),
  detectionFilters: () => get<Record<string, boolean>>(`/api/detection/filters`),
  setDetectionFilters: (filters: Record<string, boolean>) =>
    post<Record<string, boolean>>(`/api/detection/filters`, filters),
  watchedPlates: () => get<{ plates: string[] }>(`/api/plates`),
  watchPlate: (plate: string, on: boolean) => post<{ plates: string[] }>(`/api/plates`, { plate, on }),
  aiStatus: () => get<AiStatus>(`/api/ai/status`),
  aiConfig: (cfg: { provider?: string; base_url?: string; api_key?: string; model?: string; vision_model?: string; features?: Record<string, boolean> }) =>
    post<AiStatus>(`/api/ai/config`, cfg),
  aiTest: (cfg: { provider?: string; base_url?: string; api_key?: string; model?: string }) =>
    post<{ ok: boolean; detail: string }>(`/api/ai/test`, cfg),
  aiChat: (prompt: string, system?: string) => post<{ reply: string | null; disabled?: boolean }>(`/api/ai/chat`, { prompt, system }),
  aiQuery: (text: string) => post<{ filter: { kind?: string; color?: string; height?: string; time?: string } | null; disabled?: boolean }>(`/api/ai/query`, { text }),
  aiSummarize: (events: { type: string; cam: string; label?: string }[]) => post<{ summary: string | null; disabled?: boolean }>(`/api/ai/summarize`, { events }),
  aiExplain: (alert: { type: string; summary: string; cam: string }) => post<{ explanation: string | null; disabled?: boolean }>(`/api/ai/explain`, { alert }),
  aiDescribe: (id: string) => post<{ description: string | null; disabled?: boolean }>(`/api/ai/describe/${id}`, {}),
  enhance: (id: string, box: [number, number, number, number]) => post<{ image: string | null }>(`/api/enhance/${id}`, { box }),
  aiVqa: (id: string, question: string) => post<{ answer: string | null; disabled?: boolean }>(`/api/ai/vqa/${id}`, { question }),
  aiRule: (text: string, apply = false, rule?: AiRule) => post<{ rule: AiRule | null; created?: number | null; disabled?: boolean }>(`/api/ai/rule`, { text, apply, rule }),
  aiCorrelate: (alerts: { ts: string; severity: string; type: string; cam: string; summary: string }[]) =>
    post<{ result: { incident?: boolean; title?: string; assessment?: string; action?: string; cams?: string[] } | null; disabled?: boolean }>(`/api/ai/correlate`, { alerts }),
  aiAdvise: (alert: { type: string; summary: string; cam: string }) => post<{ action: string | null; disabled?: boolean }>(`/api/ai/advise`, { alert }),
  aiSearchEvents: (text: string, events: { ts: string; type: string; cam: string; label?: string }[]) =>
    post<{ result: { answer?: string; matches?: number[] } | null; disabled?: boolean }>(`/api/ai/searchevents`, { text, events }),
  // AI Operator: plan a natural-language command into a chain of system actions.
  aiOperate: (command: string, context: unknown) =>
    post<{ steps?: { action: string; args?: Record<string, unknown>; as?: string }[]; say?: string; ask?: string; border?: 'nav' | 'alert'; disabled?: boolean }>(`/api/ai/operate`, { command, context }),
  // Offline speech-to-text: POST recorded audio bytes, get back the transcript.
  stt: async (audio: Blob, lang?: string): Promise<{ text: string | null; disabled?: boolean }> => {
    const r = await fetch(`${API_BASE}/api/stt${lang ? `?lang=${lang}` : ''}`, { method: 'POST', body: audio })
    if (!r.ok) throw new Error(`stt ${r.status}`)
    return r.json()
  },
  // ── PERCEPTION SUITE ──────────────────────────────────────────────────────────────────────
  // DREAMSTATE
  dream: (sid: string) => get<{ status: T.DreamStatus | null; reason?: string }>(`/api/dream/${sid}`),
  dreamPulse: (sid: string, hours = 24) => get<{ pulse: T.DreamPulse[] }>(`/api/dream/${sid}/pulse?hours=${hours}`),
  divergences: (sid?: string, limit = 100) =>
    get<{ divergences: T.Divergence[] }>(`/api/dream/divergences?limit=${limit}${sid ? `&sid=${sid}` : ''}`),
  dreamVerdict: (id: number, verdict: 'expected' | 'flagged' | null) =>
    post<{ ok: boolean }>(`/api/dream/divergence/${id}/verdict`, { verdict }),
  dreamMute: (sid: string, cells: number[], from_hour = 0, to_hour = 24) =>
    post<{ muted: number[] }>(`/api/dream/${sid}/mute`, { cells, from_hour, to_hour }),
  dreamThreshold: (sid: string, sigma: number) =>
    post<{ threshold: number }>(`/api/dream/${sid}/threshold`, { sigma }),
  dreamReset: (sid: string, mode: 'reregister' | 'relearn') =>
    post<{ ok: boolean; cc?: number }>(`/api/dream/${sid}/reset`, { mode }),

  // FOG OF WAR
  coverage: (sid: string, task?: string, height?: number) => {
    const p = new URLSearchParams()
    if (task) p.set('task', task)
    if (height) p.set('height', String(height))
    const q = p.toString()
    return get<{ coverage: T.Coverage | null; reason?: string }>(`/api/coverage/${sid}${q ? '?' + q : ''}`)
  },
  blindSpots: (sid: string) => get<{ spots: T.BlindSpot[] }>(`/api/coverage/${sid}/blindspots`),
  dismissBlindSpot: (id: number, on = true) => post<{ ok: boolean }>(`/api/blindspots/${id}/dismiss`, { on }),
  coverageReport: (sid: string) => get<Record<string, unknown>>(`/api/coverage/${sid}/report`),

  // GRAIN
  grain: (sid: string, bucket?: number, cls?: string) => {
    const p = new URLSearchParams()
    if (bucket !== undefined) p.set('bucket', String(bucket))
    if (cls) p.set('cls', cls)
    const q = p.toString()
    return get<{ status: T.GrainStatus | null; reason?: string }>(`/api/grain/${sid}${q ? '?' + q : ''}`)
  },
  grainTracks: (sid: string, limit = 100, unusualOnly = false) =>
    get<{ tracks: T.GrainTrackRow[] }>(`/api/grain/${sid}/tracks?limit=${limit}&unusual=${unusualOnly ? 1 : 0}`),
  grainPrecedents: (trackId: number, n = 6) =>
    get<{ precedents: T.GrainTrackRow[] }>(`/api/grain/track/${trackId}/precedents?n=${n}`),
  grainVerdict: (trackId: number, verdict: 'ordinary' | 'noteworthy' | null) =>
    post<{ ok: boolean }>(`/api/grain/track/${trackId}/verdict`, { verdict }),
  grainMute: (sid: string, cells: number[]) => post<{ muted: number[] }>(`/api/grain/${sid}/mute`, { cells }),

  // EARDRUM
  probes: (sid: string) => get<{ probes: T.Probe[] }>(`/api/probes/${sid}`),
  addProbe: (sid: string, roi: [number, number, number, number], name?: string, kind?: 'probe' | 'ref') =>
    post<{ probe: T.Probe | null; reason?: string }>(`/api/probes/${sid}`, { roi, name, kind }),
  updateProbe: (id: number, patch: { name?: string; kind?: 'probe' | 'ref'; enabled?: boolean }) =>
    put<{ probe: T.Probe }>(`/api/probes/${id}`, patch),
  deleteProbe: (id: number) => del<{ ok: boolean }>(`/api/probes/${id}`),
  probeSpectrum: (id: number) => get<{ spectrum: T.ProbeSpectrum | null }>(`/api/probes/${id}/spectrum`),
  probeTrend: (id: number, hours = 168) =>
    get<{ trend: { ts: number; rms: number; snr: number }[] }>(`/api/probes/${id}/trend?hours=${hours}`),
  probeBaseline: (id: number) => post<{ ok: boolean }>(`/api/probes/${id}/baseline`, {}),
  probeWave: (id: number, seconds = 8) => `${API_BASE}/api/probes/${id}/wave?seconds=${seconds}`,
  suggestProbes: (sid: string, n = 5) =>
    post<{ candidates: { roi: [number, number, number, number]; texture: number; rigid: boolean }[] }>(
      `/api/eardrum/${sid}/suggest`, { n }),
  eardrumModal: (sid: string) =>
    get<{ modes: { hz: number; damping: number; shape: number[] }[]; reason?: string }>(`/api/eardrum/${sid}/modal`),
  eardrumCalibrate: (sid: string) =>
    post<{ ok: boolean; line_rate?: number; mains?: number; reason?: string }>(`/api/eardrum/${sid}/calibrate`, {}),

  // BEDROCK
  bedrockQuery: (q: T.BedrockQuery) => post<T.BedrockResult & { error?: string; clause?: number }>(`/api/bedrock/query`, q),
  bedrockVocab: () => get<T.BedrockVocab>(`/api/bedrock/vocab`),
  bedrockEntity: (uid: number) =>
    get<{ entity: T.BedrockEntity | null; current: T.BedrockFact[]; history: T.BedrockFact[] }>(`/api/bedrock/entity/${uid}`),
  bedrockProvenance: (id: number) =>
    get<{ fact: T.BedrockFact | null; lineage: T.BedrockFact[]; snapshot?: string | null }>(`/api/bedrock/fact/${id}/provenance`),
  bedrockStats: () => get<{ facts: number; entities: number; oldest: number | null; backfill: { running: boolean; done: number; total: number; phase: string } }>(`/api/bedrock/stats`),
  bedrockBackfill: () => post<{ started: boolean }>(`/api/bedrock/backfill`, {}),
  bedrockPurge: (uid: number) => post<{ facts: number; entities: number; snapshots: number }>(`/api/bedrock/purge/${uid}`, {}),
  aiBedrock: (text: string) =>
    post<{ query: T.BedrockQuery | null; say?: string; disabled?: boolean }>(`/api/ai/bedrock`, { text }),

  shutdown: () => post<{ ok: boolean }>(`/api/shutdown`, {}),
}

export type AiFeatureKey = 'chat' | 'search' | 'summarize' | 'explain' | 'vision' | 'rules' | 'correlate' | 'advise' | 'semantic' | 'operate'
export interface AiStatus {
  enabled: boolean
  provider?: string
  model?: string
  base?: string
  vision?: boolean
  vision_model?: string
  keyHint?: string
  features?: Record<string, boolean>
}
export interface AiRule {
  name?: string
  event_type: string
  source_id?: number | null
  zone_id?: number | null
  min_count?: number | null
  min_confidence?: number | null
  severity?: string
  cooldown_s?: number
}
