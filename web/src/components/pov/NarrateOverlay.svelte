<!-- Live narration: with one key the VLM continuously describes the active camera in natural
     language, shown as a subtitle and spoken aloud, so you can watch (or just LISTEN to) a feed
     without reading anything. Toggled by the N key, a HUD button, or the AI Operator. -->
<script lang="ts">
  import { onDestroy } from 'svelte'
  import { narrateOn, activeCam, mode, stage } from '../../lib/stores'
  import { api } from '../../lib/api'
  import { speak, loadPrefs } from '../../lib/speech'

  const INTERVAL = 8000
  let caption = $state('')
  let busy = $state(false)
  let timer: ReturnType<typeof setInterval> | undefined

  async function tick() {
    if (busy) return
    const id = $activeCam
    if (!id) return
    busy = true
    try {
      const r = await api.aiDescribe(id)
      if (r?.disabled) { caption = 'NARRATION NEEDS A VISION MODEL (⚙ set a vision model)'; stopLoop(); return }
      if (r?.description) { caption = r.description; speak(r.description, loadPrefs().lang) }
    } catch { /* keep the last caption on a transient failure */ }
    busy = false
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
