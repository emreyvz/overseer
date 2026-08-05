// OVERSEER — the PERCEPTION suite's shared catalogue.
//
// The five engines were shipped with beautiful instrumentation and no way in: no visible entry
// point, no statement of what any of them is FOR, and readouts hidden underneath the rails. An
// operator could see the art and had no way to learn what it meant without asking.
//
// This file is the answer to "what is this and is it ready", in one place, in plain language.
// Every string here is written for someone seeing the feature for the first time.
import { derived, get, writable, type Readable } from 'svelte/store'
import {
  bedrockAsOf, coverage, coverageScreen, dreamConsole, dreamStatus, eardrumDrawer, grainScreen,
  grainStatus, listenPlacing, mode, modules, probes, stage, toggleModule,
} from './stores'

export type Ready = 'ready' | 'learning' | 'setup' | 'off'

export interface Feature {
  key: string                 // MODULES key, or '' for Bedrock which is a mode
  name: string
  /** What the operator will SEE, and why they should care. No jargon, no metaphor. */
  what: string
  /** The single next thing to do once it is on. */
  next: string
  /** Where its detail screen lives, for the OPEN action. */
  open: () => void
  openLabel: string
  accel: string               // the keyboard accelerator, shown but never required
}

export const FEATURES: Feature[] = [
  {
    key: 'unseen',
    name: 'FOG OF WAR',
    what: 'Shades the ground this camera cannot usefully watch: hidden behind something, too far '
      + 'away to identify anyone, too dark to use, or where it keeps losing people. The ring '
      + 'tells you how much of the view you are actually covering.',
    next: 'Press the coverage ring to see each blind spot and what would fix it.',
    open: () => coverageScreen.set(true),
    openLabel: 'COVERAGE REPORT',
    accel: 'U',
  },
  {
    key: 'dream',
    name: 'DREAMSTATE',
    what: 'Learns what this place normally looks like at this hour, then marks anything that does '
      + 'not match. It cannot tell you WHAT happened, only that something here is not usual.',
    next: 'Leave it on for a day. It stays blank until it has learned, and blank means calm.',
    open: () => dreamConsole.set('live'),
    openLabel: 'COMPARE WITH MEMORY',
    accel: 'M',
  },
  {
    key: 'grain',
    name: 'GRAIN',
    what: 'Learns which way people normally move through here and draws it as a slow current. '
      + 'Each person gets a ring that stays quiet unless their movement is rare for this place. '
      + 'It never looks at what anyone looks like.',
    next: 'Needs a couple of weeks of traffic before it will judge anyone.',
    open: () => grainScreen.set(true),
    openLabel: 'LEARNED MODEL',
    accel: 'H',
  },
  {
    key: 'listen',
    name: 'EARDRUM',
    what: 'Reads vibration from movements too small to see in the picture, so a camera with no '
      + 'microphone can tell you a machine is running rough or something struck a surface.',
    next: 'Draw a box on a machine or a rail, then freeze a baseline while it is healthy.',
    open: () => { listenPlacing.set(true) },
    openLabel: 'PLACE A PROBE',
    accel: 'L',
  },
  {
    key: '',
    name: 'BEDROCK',
    what: 'Turns the past into something you can question. Ask what happened, who was near what, '
      + 'and what the system believed at the time rather than what it believes now.',
    next: 'Open it and press BUILD THE RECORD once to import the history you already have.',
    open: () => { stage.set('live'); mode.set('bedrock') },
    openLabel: 'OPEN BEDROCK',
    accel: 'B',
  },
]

/** Is a feature switched on right now? Bedrock is a mode, so it is never "on". */
export function isOn(key: string): boolean {
  if (!key) return false
  return !!get(modules).find((m) => m.key === key)?.on
}

export function toggle(key: string) {
  if (key) toggleModule(key)
}

/** Readiness, plus the one line that explains the state. This is what stops an operator
 *  concluding a feature is broken when it is simply still learning. */
export const readiness: Readable<Record<string, { state: Ready; note: string }>> = derived(
  [modules, coverage, dreamStatus, grainStatus, probes],
  ([$modules, $coverage, $dream, $grain, $probes]) => {
    const on = (k: string) => !!$modules.find((m) => m.key === k)?.on
    const out: Record<string, { state: Ready; note: string }> = {}

    out.unseen = !on('unseen')
      ? { state: 'off', note: 'Off' }
      : $coverage
        ? { state: 'ready', note: `Covering ${Math.round($coverage.percent)}% of the ground it can see` }
        : { state: 'learning', note: 'Working out the shape of the scene' }

    out.dream = !on('dream')
      ? { state: 'off', note: 'Off' }
      : !$dream
        ? { state: 'learning', note: 'Starting up' }
        : $dream.maturity >= 1
          ? { state: 'ready', note: 'Has learned this hour and is watching' }
          : { state: 'learning', note: `Learning this hour, ${Math.round($dream.maturity * 100)}% of the way` }

    out.grain = !on('grain')
      ? { state: 'off', note: 'Off' }
      : !$grain
        ? { state: 'learning', note: 'Starting up' }
        : $grain.mature
          ? { state: 'ready', note: `Learned from ${$grain.tracks.toLocaleString()} journeys` }
          : { state: 'learning', note: `Watching how people move, ${Math.round($grain.maturity * 100)}% of the way` }

    out.listen = !on('listen')
      ? { state: 'off', note: 'Off' }
      : $probes.length
        ? { state: 'ready', note: `Listening at ${$probes.length} point${$probes.length === 1 ? '' : 's'}` }
        : { state: 'setup', note: 'Nothing is listening yet' }

    out[''] = { state: 'ready', note: 'Ask the record a question' }
    return out
  },
)

export const READY_LABEL: Record<Ready, string> = {
  ready: 'READY', learning: 'LEARNING', setup: 'NEEDS SETUP', off: 'OFF',
}

// ── first-run coaching ──────────────────────────────────────────────────────────────────────
// Shown once, next to the thing it explains, then never again. The alternative is an operator
// staring at a beautiful overlay with no idea what it is telling them, which is exactly what
// happened.

const SEEN_KEY = 'overseer.perception.coached'

function seenSet(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(SEEN_KEY) || '[]')) } catch { return new Set() }
}

export const coached = writable<Set<string>>(seenSet())

export function hasCoached(id: string): boolean {
  return get(coached).has(id)
}

export function markCoached(id: string) {
  const s = new Set(get(coached))
  s.add(id)
  coached.set(s)
  try { localStorage.setItem(SEEN_KEY, JSON.stringify([...s])) } catch { /* private mode */ }
}

export function resetCoaching() {
  coached.set(new Set())
  try { localStorage.removeItem(SEEN_KEY) } catch { /* ignore */ }
}

/** The card shown the first time each overlay appears: what you are looking at, and what to do. */
export const COACH: Record<string, { title: string; body: string; tip?: string }> = {
  unseen: {
    title: 'THIS IS WHAT THE CAMERA CANNOT SEE',
    body: 'Anything covered in static is ground you are not really watching: behind an object, '
      + 'too far to make anyone out, too dark, or a spot where people keep disappearing from '
      + 'tracking. Clean picture means you can see it properly.',
    tip: 'The lines across the view are distances. Past each one, a person is too few pixels for '
      + 'the task named on it.',
  },
  dream: {
    title: 'AN EMPTY SCREEN IS THE GOOD NEWS',
    body: 'This draws nothing while the scene matches what it has learned about this hour. When '
      + 'something appears, changes or vanishes in a way this place does not normally do, that '
      + 'patch gets a red hatch and a number saying how unusual it is.',
    tip: 'It does not know WHAT it found. Open the comparison to see the remembered scene beside '
      + 'the live one.',
  },
  grain: {
    title: 'THE FAINT STREAKS ARE THE USUAL FLOW',
    body: 'Each streak points the way people normally walk in that spot, and the brighter it is '
      + 'the more consistent they are. Scattered, still specks mean the model has not seen that '
      + 'area enough to have an opinion.',
    tip: 'The ring on each person is how ordinary their movement is here. Quiet grey is normal; '
      + 'you are meant to ignore it.',
  },
  listen: {
    title: 'YOU ARE PLACING A VIBRATION SENSOR',
    body: 'Draw a box on something solid and textured, like a machine housing, a rail or a bolt. '
      + 'Overseer measures how much that surface trembles by tracking movements far smaller than '
      + 'one pixel.',
    tip: 'The first box becomes the reference: put it somewhere that does NOT vibrate, so the '
      + 'camera’s own shake can be subtracted from everything else.',
  },
}
