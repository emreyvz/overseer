// OVERSEER — WS/REST data contract (brief §3). Code English; UI strings Turkish.

export type Severity = 'info' | 'warning' | 'critical'

export type DetKlass =
  | 'NORMAL' | 'TRACKED' | 'ANOMALY' | 'WARNING' | 'CRITICAL' | 'OBJECT' | 'VEHICLE' | 'ANIMAL' | 'WEAPON' | 'TARGET'

export type DetCls = 'person' | 'vehicle' | 'animal' | 'object'

export interface Detection {
  id: string            // anonymized tracklet id, e.g. "TK_009.557-1"
  cls: DetCls
  bbox: [number, number, number, number] // x,y,w,h in NORMALIZED [0..1] frame coords
  conf: number
  severity: Severity
  klass: DetKlass
  attrs?: {
    upper_color?: string
    lower_color?: string
    height?: 'short' | 'medium' | 'tall'
    height_cm?: number  // people: rough estimated stature in cm (uncalibrated → approximate)
    accessory?: string[]
  }
  caseAlias?: string    // analyst-assigned only, case-scoped
  plate?: string        // vehicles: estimated licence plate from live ANPR
  subtype?: string      // vehicles: fine COCO type — car / truck / bus / motorcycle / bicycle
  bodytype?: string     // vehicles: fine body type — sedan / hatchback / SUV / ... (CLIP zero-shot, gated)
  speed?: number        // vehicles: rough km/h estimate (uncalibrated → comparable, not exact)
  make?: string         // vehicles: estimated brand from the ViT classifier (confidence-gated)
  intent?: { intent: string; confidence: number; why: string; alt?: string }  // people: estimated behaviour (probabilistic)
  facing?: number       // people: estimated facing/attention heading in image space (deg; 0 = right, 90 = toward the camera). From pose; feeds Social X-ray
  coasting?: boolean    // last-known box held briefly through a momentary detection drop (predicted, not fresh)
  occluded?: boolean    // occlusion x-ray: held+extrapolated while the subject is behind cover
  conformity?: Conformity  // GRAIN: how ordinary this subject's MOVEMENT is for this place
}

// ── GRAIN ───────────────────────────────────────────────────────────────────────────────────
// How ordinary a subject's movement is for THIS place. Movement only: no appearance, identity or
// demographic feature ever reaches the model (enforced by a test on the feature extractor).
export type GrainState = 'ordinary' | 'unusual' | 'unjudged'
export interface Conformity {
  p: number                    // percentile in [0,100]; low = rare here
  state: GrainState
  factors: GrainFactors        // per-factor percentiles — the decomposition IS the explanation
  why?: string                 // plain sentence built from the dominant factors (no LLM)
  cell?: number                // ground cell the worst step happened in
  worst?: [number, number]     // normalized frame point of the worst step (for the trail tick)
}
export interface GrainFactors {
  path: number; speed: number; heading: number; dwell: number; order?: number
}
export interface GrainCellStat {
  cell: number; n: number
  heading: number[]            // 16-bin rose histogram (normalized)
  speed: number[]              // 12-bin histogram (normalized)
  modal_heading: number        // radians, image-space ground plane
  modal_speed: number          // scene units / s
  concentration: number        // von Mises kappa, normalized [0,1] — how strongly the place prefers it
  mature: boolean
}
export interface GrainStatus {
  cam: string
  tracks: number; days: number; mature: boolean; maturity: number  // [0,1]
  suspended?: string           // e.g. 'CROWDED' — scoring suspended, with the reason
  stale?: boolean              // camera moved: the ground grid no longer matches
  grid: [number, number]
  cells: GrainCellStat[]
  bucket: number; buckets: string[]
}
export interface GrainTrackRow {
  id: number; det_id: string; start_ts: number; end_ts: number
  percentile: number; state: GrainState; factors: GrainFactors
  why?: string; snapshot?: string | null
  verdict?: 'ordinary' | 'noteworthy' | null
  path: [number, number][]     // normalized frame points, for the ledger thumbnail + replay
}

// ── FOG OF WAR ──────────────────────────────────────────────────────────────────────────────
export type DoriTask = 'detect' | 'observe' | 'recognise' | 'identify'
export type BlindKind = 'occlusion' | 'resolution' | 'radiometric' | 'empirical' | 'indeterminate'
export interface BlindSpot {
  id: number
  kind: BlindKind
  name: string                 // auto-generated, e.g. "BEHIND THE SKIP · SW"
  polygon: [number, number][]  // normalized frame polygon
  area_m2: number | null       // estimated; scale is uncalibrated so treat as approximate
  persistent: boolean
  first_seen: number; last_seen: number
  events: number               // LOST_IN_FOG count attributed here
  channels: { geometric: number; optical: number; radiometric: number; empirical: number }
  remedies: { text: string; recovers_m2: number | null }[]
  dismissed?: boolean
  samples?: string[]           // snapshots of tracks that died here (empirical evidence)
}
export interface DoriBand {
  task: DoriTask
  px_per_m: number
  range_m: number
  y: number                    // normalized image y where this band's range crosses the ground
}
export interface Coverage {
  cam: string; sid: string
  task: DoriTask
  target_height_m: number
  percent: number              // coverage of OBSERVED ground at the chosen task
  fov_deg: number
  grid: [number, number]
  unseen: number[]             // flattened cell grid of `unseen` in [0,1]
  cells_m2: number
  shadows: { polygon: [number, number][]; persistent: boolean; id: number }[]
  bands: DoriBand[]
  scale_estimated: boolean     // monocular depth → metres are ESTIMATED, always surfaced in UI
  ts: number
}
export interface FogLoss {                 // live LOST IN FOG tracker
  det_id: string; spot: number
  entered: number; expected_exit: number; overdue: boolean
}

// ── DREAMSTATE ──────────────────────────────────────────────────────────────────────────────
export interface Divergence {
  id: number
  cam: string
  ts: number
  peak_sigma: number
  area_sigma_s: number         // integrated "size" of the surprise
  blob: [number, number][]     // normalized frame polygon
  cells: number[]              // qualifying cell indices
  snapshot?: string | null
  verdict?: 'expected' | 'flagged' | null
  tier: 'A' | 'B'
  triage?: 'scene' | 'subject'  // GRAIN coupling: was a low-percentile subject concurrent?
}
export interface DreamStatus {
  cam: string
  tier: 'A' | 'B'
  bucket: number
  buckets: { name: string; n: number; maturity: number }[]
  maturity: number             // current bucket, [0,1]
  sigma: number                // current peak sigma
  cells: number[]              // flattened current per-cell sigma grid
  grid: [number, number]
  stale?: boolean              // camera moved
  muted: number[]              // muted cell indices
  threshold: number
}
export interface DreamPulse { t: number; peak: number; mean: number }

// ── EARDRUM ─────────────────────────────────────────────────────────────────────────────────
export interface Probe {
  id: number
  name: string
  roi: [number, number, number, number]  // normalized x,y,w,h
  kind: 'probe' | 'ref'
  enabled: boolean
  texture: number              // Shi-Tomasi min-eigenvalue score at placement, [0,1]
  baseline?: boolean           // a baseline spectrum has been captured
}
export interface ProbePeak { hz: number; db: number; prominence: number; is_new?: boolean; shift?: number }
export interface ProbeFrame {
  id: number
  rms: number                  // current displacement RMS, px
  db: number                   // dB vs baseline (0 when no baseline)
  snr: number
  peaks: ProbePeak[]
  col: string                  // newest spectrogram column, base64 uint8
  wave: number[]               // newest waveform chunk (decimated) for the micro-scope
  saturated?: boolean          // common-mode reference lost / camera moving
}
export interface ProbeSpectrum {
  id: number
  freqs: number[]; psd: number[]
  baseline?: number[] | null
  floor: number                // measured noise floor in dB — peaks below are NOT real
  peaks: ProbePeak[]
  band: 'structural' | 'acoustic'
  nyquist: number
  interpretation?: {
    f0: number; rpm: number | null
    harmonics: { order: number; db: number }[]
    verdict: string; why: string; confidence: 1 | 2 | 3
  } | null
}

// ── BEDROCK ─────────────────────────────────────────────────────────────────────────────────
export type BedrockClause =
  | { t: 'kind'; kind: string }
  | { t: 'pred'; pred: string; obj?: number; val?: string | number | [number, number]; op?: string }
  | { t: 'allen'; rel: 'before' | 'after' | 'during' | 'overlaps' | 'meets' | 'starts' | 'finishes'; a: number; b: number }
  | { t: 'count'; pred: string; op: '>=' | '<=' | '=='; n: number }
  | { t: 'not'; clause: BedrockClause }
export interface BedrockQuery {
  select: 'entity' | 'fact'
  where: BedrockClause[]
  window?: { from: number; to: number }
  asOf?: number
  order?: 'time' | 'confidence' | 'duration'
  limit?: number
}
export interface BedrockEntity {
  uid: number; kind: string; ref: string; label?: string | null
  first_seen: number; last_seen: number; snapshot?: string | null
}
export interface BedrockFact {
  id: number
  subj: number; pred: string
  obj?: number | null; val?: string | null
  valid_from: number; valid_to: number | null
  tx_from: number; tx_to: number | null
  conf: number
  src_kind: string; src_ref?: string | null; model_id?: string | null
  snapshot?: string | null
  superseded_by?: number | null
}
export interface BedrockResult {
  entities: BedrockEntity[]
  facts: BedrockFact[]
  truncated: boolean
  estimated: number
  took_ms: number
  as_of: number | null
  window: { from: number; to: number }
}
export interface BedrockVocab {
  version: number
  predicates: { pred: string; family: string; object: 'entity' | 'literal' | 'number'; label: string }[]
  kinds: string[]
}

export interface FrameMeta {
  fps: number
  res: [number, number]
  inferenceMs: number
  brightness: number
  motionPct: number
  movingCam?: boolean   // camera itself is in motion (dashcam) — speeds are ego-compensated
}

export interface Alert {
  ts: number
  severity: Severity
  type: string          // event label, e.g. "RESTRICTED ZONE BREACH"
  summary: string
  cam: string
  snapshot?: string
  clip?: string         // short MP4 of the incident moment (replayable)
  ack: boolean
  threat?: string       // assigned threat number on push, e.g. "THR-1042"
  hits?: number         // how many times this incident repeated while deduped
  reason?: string       // AI 'why this matters' explanation (on demand)
  action?: string       // AI recommended operator action (on demand)
  mark?: AlertMark      // where to look in the replay: triggering object + zone
}

// Incident marker for the annotated replay overlay.
export interface AlertMark {
  bbox?: [number, number, number, number]  // normalized x,y,w,h of the triggering object
  zone?: [number, number][]                // normalized zone polygon (for zone breaches)
  kind: string          // 'person' | 'vehicle' | 'object' | 'weapon' | 'animal'
  label: string         // short caps label, e.g. "WEAPON", "ABANDONED OBJECT"
}

export interface SystemStat {
  cpu: number
  gpu: number | null
  ram: number
  storageGB: number
  rec: 'off' | 'continuous' | 'motion' | 'event'
  recActive: boolean
}

export interface Camera {
  id: string
  name: string
  url?: string
  health: 'online' | 'offline'
  coords?: [number, number]
  fps: number
  // download progress for looped YouTube sources (absent for live cameras)
  download?: { status: 'idle' | 'downloading' | 'ready' | 'failed'; progress: number }
}

export interface RosterEntry {
  id: string
  cls: 'person' | 'vehicle' | string
  snapshot: string | null      // /snapshots/... path, prefix with the API base
  plate: string | null
  attrs: { upper_color?: string; lower_color?: string; height?: string; height_cm?: number; accessory?: string[]; subtype?: string; bodytype?: string; make?: string }
  obs: number
  cam?: string | null           // camera the subject was last seen on
  first_cam?: string | null     // camera the subject was FIRST seen on
  clip?: string | null          // short sighting clip (/snapshots/clips/...), prefix with API base
  watched?: boolean             // flagged as BOLO — re-sightings raise a WATCHLIST HIT alert
  trail?: RosterSighting[]      // movement trail: cameras visited, earliest first
  subject_uid?: number | null   // persistent long-term subject id (features 5/6/7), if recorded
  subject_flags?: string[]      // e.g. ['repeat_visitor']
  first_ts: number
  last_ts: number
}

export interface RosterSighting {
  cam: string
  first: number                 // ms epoch of first sighting on this camera
  last: number                  // ms epoch of most recent sighting on this camera
  count: number
  clip?: string | null          // this leg's sighting clip (used to build the journey supercut)
}

export type ConnState = 'connecting' | 'online' | 'reconnecting' | 'offline'

export interface TimelineEvent {
  ts: number
  type: string
  label: string
  conf?: number
  cam: string
  snapshot?: string
}

// Object-of-interest visual tracker target.
export interface OOITarget { id: string; name: string; bbox: [number, number, number, number]; lost: boolean; conf: number }

// Dashboard metric rows (auxiliary rail) — label:value pairs.
export interface Metric { label: string; value: string }

// Module toggle (left rail).
export interface ModuleToggle { key: string; label: string; group: string; on: boolean }

export type WsMessage =
  | { t: 'frame'; d: FrameMeta }
  | { t: 'detections'; d: Detection[] }
  | { t: 'alert'; d: Alert }
  | { t: 'system'; d: SystemStat }
  | { t: 'cameras'; d: Camera[] }
  | { t: 'conn'; d: ConnState }
  | { t: 'event'; d: TimelineEvent }
  | { t: 'metrics'; d: Metric[] }
  | { t: 'ooi'; d: OOITarget[] }
  | { t: 'divergence'; d: Divergence }     // DREAMSTATE fired
  | { t: 'dream'; d: DreamStatus }         // DREAMSTATE live status (2 Hz)
  | { t: 'probe'; d: ProbeFrame }          // EARDRUM spectral frame (~4 Hz per probe)
  | { t: 'grain'; d: GrainTrackRow }       // GRAIN scored a closed track as unusual
  | { t: 'coverage'; d: Coverage }         // FOG OF WAR field refreshed
