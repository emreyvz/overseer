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
    accessory?: string[]
  }
  caseAlias?: string    // analyst-assigned only, case-scoped
  plate?: string        // vehicles: estimated licence plate from live ANPR
  subtype?: string      // vehicles: fine COCO type — car / truck / bus / motorcycle / bicycle
  speed?: number        // vehicles: rough km/h estimate (uncalibrated → comparable, not exact)
  make?: string         // vehicles: estimated brand from the ViT classifier (confidence-gated)
  intent?: { intent: string; confidence: number; why: string; alt?: string }  // people: estimated behaviour (probabilistic)
  coasting?: boolean    // last-known box held briefly through a momentary detection drop (predicted, not fresh)
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
  attrs: { upper_color?: string; lower_color?: string; height?: string; accessory?: string[]; subtype?: string; make?: string }
  obs: number
  cam?: string | null           // camera the subject was last seen on
  first_cam?: string | null     // camera the subject was FIRST seen on
  clip?: string | null          // short sighting clip (/snapshots/clips/...), prefix with API base
  watched?: boolean             // flagged as BOLO — re-sightings raise a WATCHLIST HIT alert
  trail?: RosterSighting[]      // movement trail: cameras visited, earliest first
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
