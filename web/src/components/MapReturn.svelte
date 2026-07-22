<script lang="ts">
  // Leaving a camera for the map. The full-screen feed shrinks onto the camera's
  // actual node card on the map — same position, same size, same feed — then hands
  // off to that node's live thumbnail. It reads as the video zooming out until it
  // *is* the map thumbnail (not a cut, not a fade to a fake spot).
  import { onMount, onDestroy } from 'svelte'
  import { sfx } from '../lib/audio'
  import type { Camera } from '../lib/types'

  let { cam, tx = 50, ty = 50, oncomplete }:
    { cam: Camera; tx?: number; ty?: number; oncomplete: () => void } = $props()
  const SNAP = (import.meta.env.VITE_SNAP_BASE as string | undefined) ?? 'http://127.0.0.1:8787/snap'

  let src = $state('')
  let card = $state<HTMLElement>()
  let done = $state(false)
  const timers: ReturnType<typeof setTimeout>[] = []

  onMount(() => {
    sfx('whoosh')
    const im = new Image()
    im.onload = () => (src = im.src)
    im.src = `${SNAP}/${cam.id}?t=${Date.now()}`
    // Two frames so the map has laid out its node cards, then measure the target.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (!card) return
      const node = document.querySelector(`[data-node="${cam.id}"]`) as HTMLElement | null
      if (node) {
        const r = node.getBoundingClientRect()
        const s = Math.max(0.02, r.width / window.innerWidth)
        card.style.transformOrigin = '0 0'
        card.style.transform = `translate(${r.left}px, ${r.top}px) scale(${s})`
      } else {
        card.style.transformOrigin = `${tx}% ${ty}%` // no node (no coords) → recede toward its map area
        card.style.transform = 'scale(0.05)'
      }
    }))
    timers.push(setTimeout(() => (done = true), 820)) // landed → let the real node thumbnail take over
    timers.push(setTimeout(oncomplete, 940))
  })
  onDestroy(() => timers.forEach(clearTimeout))
</script>

<div class="mret" class:done>
  <div class="card" bind:this={card}>
    {#if src}<img class="cimg" {src} alt="" />{/if}
    <div class="cframe"></div>
  </div>
</div>

<style>
  .mret { position: absolute; inset: 0; z-index: var(--z-cmd); pointer-events: none; }
  .card { position: fixed; inset: 0; transform-origin: 0 0; background: #05070a; will-change: transform, opacity;
    transition: transform 820ms cubic-bezier(0.6, 0.02, 0.2, 1); box-shadow: 0 0 40px rgba(0,0,0,0.5); }
  .mret.done .card { opacity: 0; transition: opacity 150ms linear; } /* fade last, revealing the node's own thumb */
  .cimg { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; filter: saturate(0.55) contrast(1.05) brightness(0.92); }
  .cframe { position: absolute; inset: 0; pointer-events: none; border: 1px solid var(--cyan);
    background: radial-gradient(ellipse at center, transparent 60%, rgba(0,0,0,0.4) 100%); }
</style>
