// OVERSEER — audio layer (Howler). Professional, restrained: ≤2 concurrent, low volume.
import { Howl, Howler } from 'howler'
import { get } from 'svelte/store'
import { muted } from './stores'
import { SFX, MUSIC, type SfxKey } from './assets'

Howler.volume(0.5)
muted.subscribe((m) => Howler.mute(m))

const cache = new Map<SfxKey, Howl>()
let active = 0

function howl(key: SfxKey): Howl {
  let h = cache.get(key)
  if (!h) {
    h = new Howl({ src: [SFX[key]], html5: true, preload: true, volume: 0.45 })
    cache.set(key, h)
  }
  return h
}

/** Play an SFX. `critical` bypasses the ≤2 concurrency cap. */
export function sfx(key: SfxKey, opts: { volume?: number; critical?: boolean } = {}) {
  if (get(muted)) return
  if (!opts.critical && active >= 2) return
  const h = howl(key)
  if (opts.volume != null) h.volume(opts.volume)
  active += 1
  const doneId = h.play()
  h.once('end', () => { active = Math.max(0, active - 1) }, doneId)
  h.once('stop', () => { active = Math.max(0, active - 1) }, doneId)
}

let ambience: Howl | null = null
/** Start the low ambient bed (boot/steady). Loops quietly. */
export function startAmbience() {
  if (ambience) return
  ambience = new Howl({ src: [SFX.ambience], html5: true, loop: true, volume: 0.16 })
  ambience.play()
}
export function stopAmbience() { ambience?.fade(0.16, 0, 600); setTimeout(() => { ambience?.stop(); ambience = null }, 650) }

let bed: Howl | null = null
export function musicBed(on: boolean) {
  if (on && !bed) { bed = new Howl({ src: [MUSIC.operations], html5: true, loop: true, volume: 0.1 }); bed.play() }
  if (!on && bed) { bed.stop(); bed = null }
}

export function toggleMute() { muted.update((m) => !m) }

// — Single-keypress mechanical tick (synthesized; short + randomized per key) —
let actx: AudioContext | null = null
function ac(): AudioContext {
  actx ??= new (window.AudioContext || (window as any).webkitAudioContext)()
  if (actx.state === 'suspended') actx.resume()
  return actx
}

/** One short key click. Call once per real keystroke; pitch/length randomized. */
export function keyTick() {
  if (get(muted)) return
  try {
    const c = ac()
    const t = c.currentTime
    const dur = 0.018 + Math.random() * 0.022
    const buf = c.createBuffer(1, Math.max(1, Math.floor(c.sampleRate * dur)), c.sampleRate)
    const data = buf.getChannelData(0)
    for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / data.length) ** 3
    const src = c.createBufferSource(); src.buffer = buf
    const bp = c.createBiquadFilter(); bp.type = 'bandpass'
    bp.frequency.value = 1700 + Math.random() * 1400; bp.Q.value = 0.9
    const g = c.createGain(); g.gain.value = 0.11 + Math.random() * 0.04
    src.connect(bp).connect(g).connect(c.destination)
    src.start(t); src.stop(t + dur)
  } catch { /* audio unavailable */ }
}
