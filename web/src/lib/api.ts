// OVERSEER — REST client for the FastAPI bridge. Falls back gracefully when offline.
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
export interface Associate { id: string; count: number; cameras: string[]; first: number; last: number; confidence: number; cls: string; snapshot?: string | null; plate?: string | null; cam?: string | null }
export interface GraphNode { id: string; cls: string; snapshot?: string | null; plate?: string | null }
export interface GraphEdge { a: string; b: string; count: number; confidence: number; cameras: string[] }
export interface SocialGraph { nodes: GraphNode[]; edges: GraphEdge[] }
export interface MergeCandidate { a: import('./types').RosterEntry; b: import('./types').RosterEntry; similarity: number; reason: string }
export interface CameraDna { id: number | string; name?: string | null; dna: string[]; reputation: number; frames: number; brightness?: number; motion?: number; fps?: number; reconnects?: number; person?: number; vehicle?: number }
export interface CaseRow { id: number; name: string; threat: string; notes: string; status?: string; created: number; targets: number }
export interface CaseEventRow { ts: number; kind: string; type: string; cam: string; severity: string; summary: string; snapshot?: string | null; clip?: string | null }
export interface SceneSubject { id: string; cls: string; snapshot?: string | null; plate?: string | null; seen: number; associates: Associate[] }
export interface CaseDetail { id: number; name: string; threat: string; notes: string; status: string; created: number; cameras: string[]; events: CaseEventRow[]; subjects: SceneSubject[]; aiSummary: string | null }
export interface SpatialEntity { id: string; cls: string; cx: number; cy: number; depth: number; conf: number; label: string }
export interface SpatialScene { cam: string; sid: string; w: number; h: number; fov: number; image: string; depth: string; entities: SpatialEntity[]; ts: number }
export interface SuggestRule { name: string; event_type: string; source_id: number; severity: string }
export interface Suggestion { kind: 'alert' | 'camera'; cam: string; title: string; why: string; count?: number; rule?: SuggestRule }

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
  watchRoster: (id: string, on: boolean) =>
    post<import('./types').RosterEntry>(`/api/roster/${id}/watch`, { on }),
  supercut: (id: string) => get<{ url: string }>(`/api/roster/${id}/supercut`),
  entityRelationships: (id: string) => get<{ associates: Associate[] }>(`/api/roster/${id}/relationships`),
  relationships: () => get<SocialGraph>(`/api/relationships`),
  cameraDna: () => get<{ cameras: CameraDna[] }>(`/api/cameras/dna`),
  spatial: (sid: string, grid = 320) => get<{ scene: SpatialScene | null; reason?: string }>(`/api/spatial/${sid}?grid=${grid}`),
  suggestions: () => get<{ suggestions: Suggestion[] }>(`/api/suggestions`),
  addAlertRule: (rule: SuggestRule) => post<{ id: number }>(`/api/alerts/rules`, rule),
  mergeCandidates: () => get<{ candidates: MergeCandidate[] }>(`/api/roster/merge-candidates`),
  mergeRoster: (keep: string, drop: string) => post<import('./types').RosterEntry>(`/api/roster/merge`, { keep, drop }),
  mergeReject: (a: string, b: string) => post<{ ok: boolean }>(`/api/roster/merge-reject`, { a, b }),
  findAcross: (id: string) =>
    post<{ matches: { camId: string; cam: string; score: number; ambiguous?: boolean }[] }>(`/api/roster/${id}/find`, {}),
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
  aiRule: (text: string, apply = false, rule?: AiRule) => post<{ rule: AiRule | null; created?: number | null; disabled?: boolean }>(`/api/ai/rule`, { text, apply, rule }),
  aiCorrelate: (alerts: { ts: string; severity: string; type: string; cam: string; summary: string }[]) =>
    post<{ result: { incident?: boolean; title?: string; assessment?: string; action?: string; cams?: string[] } | null; disabled?: boolean }>(`/api/ai/correlate`, { alerts }),
  aiAdvise: (alert: { type: string; summary: string; cam: string }) => post<{ action: string | null; disabled?: boolean }>(`/api/ai/advise`, { alert }),
  aiSearchEvents: (text: string, events: { ts: string; type: string; cam: string; label?: string }[]) =>
    post<{ result: { answer?: string; matches?: number[] } | null; disabled?: boolean }>(`/api/ai/searchevents`, { text, events }),
  shutdown: () => post<{ ok: boolean }>(`/api/shutdown`, {}),
}

export type AiFeatureKey = 'chat' | 'search' | 'summarize' | 'explain' | 'vision' | 'rules' | 'correlate' | 'advise' | 'semantic'
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
