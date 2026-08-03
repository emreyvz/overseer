<!-- Live narration: with one key the VLM continuously describes the active camera in natural
     language, shown as a subtitle and spoken aloud, so you can watch (or just LISTEN to) a feed
     without reading anything. Toggled by the N key, a HUD button, or the AI Operator. -->
<script lang="ts">
  import { onDestroy } from 'svelte'
  import { get } from 'svelte/store'
  import { narrateOn, activeCam, mode, stage, detections } from '../../lib/stores'
  import type { Detection } from '../../lib/types'
  import { api } from '../../lib/api'
  import { speak, loadPrefs } from '../../lib/speech'

  const INTERVAL = 8000
  let caption = $state('')
  let busy = $state(false)
  let timer: ReturnType<typeof setInterval> | undefined

  // Rich live narration from the structured detection data (colour, height, intent, facing,
  // groupings, entries/exits) so it says what is actually happening — not just a head count —
  // and varies each tick. Works with NO vision model configured; the VLM one-liner is only mixed
  // in occasionally for a visual gist.
  type D = Detection
  let prevIds = new Set<string>()   // last tick's tracklet ids, for entry/exit
  let narrTick = 0
  const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s)

  const INTENT_SAY: Record<string, string> = {
    loitering: 'loitering', waiting: 'waiting around', running: 'running', hurrying: 'hurrying',
    strolling: 'strolling by', transiting: 'passing through', pacing: 'pacing back and forth',
    wandering: 'wandering', searching: 'looking around',
  }

  function facingSay(f?: number): string {
    if (typeof f !== 'number') return ''
    if (f > 55 && f < 125) return ', facing the camera'
    if (f > 235 && f < 305) return ', facing away'
    return ''
  }

  function personSay(d: D): string {
    const a = d.attrs || {}
    const size = a.height === 'tall' ? 'tall ' : a.height === 'short' ? 'short ' : ''
    const colour = a.upper_color ? ` in ${a.upper_color}` : ''
    const who = size || colour ? `the ${size}person${colour}` : 'a person'
    const act = d.intent?.intent ? ` is ${INTENT_SAY[d.intent.intent] || d.intent.intent}` : ' is on the move'
    return `${who}${act}${facingSay(d.facing)}`
  }

  function vehicleSay(d: D): string {
    const a = d.attrs || {}
    const colour = a.upper_color ? `${a.upper_color} ` : ''
    const make = d.make ? `${d.make} ` : ''
    const type = d.bodytype || d.subtype || 'vehicle'
    const spd = typeof d.speed === 'number' && d.speed > 8 ? `, doing about ${Math.round(d.speed)} km/h` : ''
    const plate = d.plate ? ` (plate ${d.plate})` : ''
    return `a ${colour}${make}${type}${plate}${spd}`
  }

  const centre = (d: D): [number, number] => [d.bbox[0] + d.bbox[2] / 2, d.bbox[1] + d.bbox[3] / 2]
  function groupSay(people: D[]): string {
    for (let i = 0; i < people.length; i++)
      for (let j = i + 1; j < people.length; j++) {
        const [ax, ay] = centre(people[i]), [bx, by] = centre(people[j])
        if (Math.hypot(ax - bx, ay - by) < 0.13) {
          const c = people[i].attrs?.upper_color
          return `${cap(c ? `the person in ${c}` : 'two people')} appears to be with someone nearby.`
        }
      }
    return ''
  }

  function liveNarration(): string {
    const tracked = get(detections)                       // includes coasting, for stable entry/exit
    const active = tracked.filter((x) => !x.coasting)
    const people = active.filter((x) => x.cls === 'person')
    const vehicles = active.filter((x) => x.cls === 'vehicle')
    const ids = new Set(tracked.map((x) => x.id))
    const entered = [...ids].filter((id) => !prevIds.has(id))
    const left = [...prevIds].filter((id) => !ids.has(id))
    prevIds = ids
    if (!active.length) return 'The scene is quiet, nothing moving.'

    const parts: string[] = []
    // 1. entries first — the most salient change
    if (entered.length) {
      const e = active.find((x) => x.id === entered[0])
      if (e && e.cls === 'vehicle') parts.push(`${cap(vehicleSay(e))} has entered the scene.`)
      else if (e) parts.push(`A new person has entered${e.attrs?.upper_color ? `, in ${e.attrs.upper_color}` : ''}.`)
      else parts.push(entered.length === 1 ? 'Someone new has entered.' : `${entered.length} new subjects entered.`)
    }
    // 2. one person, rotating each tick so the narration varies over a static scene
    if (people.length) parts.push(cap(personSay(people[narrTick % people.length])) + '.')
    // 3. a grouping, if two people are close
    const g = groupSay(people)
    if (g) parts.push(g)
    // 4. a vehicle now and then
    if (vehicles.length && narrTick % 2 === 0) parts.push(cap(vehicleSay(vehicles[narrTick % vehicles.length])) + ' is in view.')
    // 5. exits
    if (left.length) parts.push(left.length === 1 ? 'Someone has left the frame.' : `${left.length} have left the frame.`)
    if (!parts.length) {                                   // fallback: a plain tally
      const bits: string[] = []
      if (people.length) bits.push(`${people.length} ${people.length === 1 ? 'person' : 'people'}`)
      if (vehicles.length) bits.push(`${vehicles.length} ${vehicles.length === 1 ? 'vehicle' : 'vehicles'}`)
      return bits.length ? `${bits.join(' and ')} in view.` : 'The scene is quiet, nothing moving.'
    }
    return parts.slice(0, 3).join(' ')
  }

  async function tick() {
    if (busy) return
    const id = $activeCam
    if (!id) return
    busy = true
    narrTick++
    try {
      // Mostly narrate from our own structured data (rich + always available); every 3rd tick, if a
      // vision model is configured, let it add a visual gist for variety.
      let text = ''
      if (narrTick % 3 === 0) {
        const r = await api.aiDescribe(id).catch(() => null)
        text = r?.description || ''
      }
      if (!text) text = liveNarration()
      caption = text
      speak(text, loadPrefs().lang)
    } finally { busy = false }
  }

  function stopLoop() { if (timer) { clearInterval(timer); timer = undefined } busy = false }

  $effect(() => {
    const live = $narrateOn && $stage === 'live' && $mode === 'pov' && !!$activeCam
    if (live && !timer) { caption = ''; tick(); timer = setInterval(tick, INTERVAL) }
    else if (!live) stopLoop()
  })
  onDestroy(stopLoop)
</script>

{#if $narrateOn && $mode === 'pov'}
  <div class="narr" aria-live="polite">
    <div class="tagline caps"><span class="dot"></span>LIVE NARRATION{#if busy} · LOOKING…{/if}</div>
    {#if caption}<p class="cap">{caption}</p>{/if}
  </div>
{/if}

<style>
  .narr { position: absolute; left: 0; right: 0; bottom: 68px; z-index: var(--z-overlay); pointer-events: none;
    display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 0 8%; }
  .tagline { display: inline-flex; align-items: center; gap: 8px; font-size: 9px; letter-spacing: 0.22em; color: var(--cyan);
    background: rgba(4,7,10,0.6); padding: 4px 10px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 8px var(--cyan); animation: nblink 1.4s ease-in-out infinite; }
  @keyframes nblink { 50% { opacity: 0.3; } }
  .cap { margin: 0; max-width: 900px; text-align: center; font-size: 17px; line-height: 1.5; color: #fff;
    text-shadow: 0 2px 10px rgba(0,0,0,0.9), 0 0 3px rgba(0,0,0,0.9); background: rgba(4,7,10,0.32);
    padding: 6px 16px; letter-spacing: 0.01em; animation: capin 300ms ease; }
  @keyframes capin { from { opacity: 0; transform: translateY(6px); } }
  @media (prefers-reduced-motion: reduce) { .dot, .cap { animation: none; } }
</style>
