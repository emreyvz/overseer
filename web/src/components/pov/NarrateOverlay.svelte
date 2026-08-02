<!-- Live narration: with one key the VLM continuously describes the active camera in natural
     language, shown as a subtitle and spoken aloud, so you can watch (or just LISTEN to) a feed
     without reading anything. Toggled by the N key, a HUD button, or the AI Operator. -->
<script lang="ts">
  import { onDestroy } from 'svelte'
  import { get } from 'svelte/store'
  import { narrateOn, activeCam, mode, stage, detections } from '../../lib/stores'
  import { api } from '../../lib/api'
  import { speak, loadPrefs } from '../../lib/speech'

  const INTERVAL = 8000
  let caption = $state('')
  let busy = $state(false)
  let timer: ReturnType<typeof setInterval> | undefined

  // Narrate from live detections when there's no vision model (or the VLM returns nothing) — so
  // narration is never silent: it always says who/what is in view.
  function liveNarration(): string {
    const d = get(detections).filter((x) => !x.coasting)
    const p = d.filter((x) => x.cls === 'person').length
    const v = d.filter((x) => x.cls === 'vehicle').length
    if (!p && !v) return 'The scene is quiet, nothing moving.'
    const bits: string[] = []
    if (p) bits.push(`${p} ${p === 1 ? 'person' : 'people'}`)
    if (v) bits.push(`${v} ${v === 1 ? 'vehicle' : 'vehicles'}`)
    return `${bits.join(' and ')} in view.`
  }

  async function tick() {
    if (busy) return
    const id = $activeCam
    if (!id) return
    busy = true
    try {
      const r = await api.aiDescribe(id).catch(() => null)
      const text = r?.description || liveNarration()   // VLM if available, else live-data narration
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
