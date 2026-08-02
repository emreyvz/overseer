// OVERSEER — English HUD lexicon (ALL CAPS, machine-verb, `_` for in-progress).

/** Uppercase helper (kept name for back-compat; UI is English). */
export const trUpper = (s: string): string => s.toUpperCase()
export const enUpper = trUpper

import type { ConnState, Severity } from './types'

export const LEX = {
  conn: {
    connecting: 'CONNECTING_',
    online: 'FEED ONLINE',
    reconnecting: 'RECONNECTING_',
    offline: 'NO SIGNAL',
  } as Record<ConnState, string>,

  scanning: 'SCANNING SOURCE_',
  vector: 'ACQUIRING TARGET VECTOR_',
  exporting: 'EXPORTING DATA_',
  trajectory: 'CALCULATING TRAJECTORY_',
  located: 'TARGET LOCATED',
  rec: '● REC',

  prompt: 'ENTER TARGET_',
  cmd: 'COMMAND_',
  cmdUnknown: 'COMMAND UNKNOWN',

  boot1: 'INITIALIZING PRIMARY OPERATIONS_',
  boot2: 'ESTABLISHING FEED ACCESS_',
  boot3: 'CORE ONLINE',

  anon: 'ANONYMOUS · EVENT-BASED ANALYSIS',

  dominant: 'DOMINANT',
  auxiliary: 'AUXILIARY',
  modules: 'MODULES',
  timeline: 'EVENT TIMELINE',
  modes: {
    pov: 'POV', montage: 'MONTAGE', topology: 'TOPOLOGY',
    forensic: 'FORENSIC', archive: 'ARCHIVE', case: 'CASE',
  },
} as const

export const SEVERITY_LABEL: Record<Severity, string> = {
  info: 'INFO', warning: 'WARNING', critical: 'CRITICAL',
}

export const MODULES = [
  { key: 'motion', label: 'MOTION', group: 'DETECTION' },
  { key: 'person', label: 'PERSON', group: 'DETECTION' },
  { key: 'vehicle', label: 'VEHICLE', group: 'DETECTION' },
  { key: 'animal', label: 'ANIMAL', group: 'DETECTION' },
  { key: 'weapon', label: 'WEAPON', group: 'DETECTION' },
  { key: 'track', label: 'TRACKING', group: 'DETECTION' },
  { key: 'daynight', label: 'DAY/NIGHT', group: 'ENVIRONMENT' },
  { key: 'fog', label: 'FOG', group: 'ENVIRONMENT' },
  { key: 'rain', label: 'RAIN', group: 'ENVIRONMENT' },
  { key: 'wind', label: 'WIND', group: 'ENVIRONMENT' },
  { key: 'weather', label: 'WEATHER', group: 'ENVIRONMENT' },
  { key: 'lightning', label: 'LIGHTNING', group: 'SKY' },
  { key: 'meteor', label: 'METEOR', group: 'SKY' },
  { key: 'satellite', label: 'SATELLITE', group: 'SKY' },
  { key: 'tracklet', label: 'TRACKLET', group: 'FORENSIC' },
  { key: 'heatmap', label: 'HEATMAP', group: 'VISUAL' },
  { key: 'tactical', label: 'TACTICAL', group: 'VISUAL' },
  { key: 'ghosts', label: 'FORESIGHT', group: 'VISUAL' },
  { key: 'social', label: 'SOCIAL X-RAY', group: 'VISUAL' },
] as const

export const clock = (d = new Date()): string =>
  [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')
