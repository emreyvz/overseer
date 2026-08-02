<!-- Follow-cam: once a subject is locked (selected), the digital PTZ smoothly zooms in and keeps
     them centred as they move, like an operator riding the joystick. Toggle with F, a HUD button,
     or the AI Operator. Uses the same motion model as the boxes, so the framing glides. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { get } from 'svelte/store'
  import { followOn, followState, povZoom, mode, flashBanner } from '../../lib/stores'
  import { predictedDetections } from '../../lib/motion'

  const FOLLOW_ZOOM = 2.3
  const EASE = 0.1
  const LOST_GRACE = 1600         // hold this long while the target is lost; re-acquire resumes
  let raf = 0
  let lostSince = 0
  const clamp = (v: number, m: number) => (v < -m ? -m : v > m ? m : v)

  function loop() {
    raf = requestAnimationFrame(loop)
    if (!get(followOn) || get(mode) !== 'pov') return
    const fs = get(followState)
    // Lost: keep the framing for a grace window; if the tracker re-acquires it (fs.lost clears),
    // follow resumes seamlessly; otherwise drop out of follow.
    if (!fs || fs.lost) {
      if (!lostSince) lostSince = performance.now()
      else if (performance.now() - lostSince > LOST_GRACE) { lostSince = 0; followOn.set(false); flashBanner('TARGET LOST · FOLLOW OFF', true, 1200) }
      return
    }
    lostSince = 0
    const d = get(predictedDetections).find((x) => x.id === fs.id)
    if (!d) return
    const cx = d.bbox[0] + d.bbox[2] / 2
    const cy = d.bbox[1] + d.bbox[3] / 2
    const z = FOLLOW_ZOOM
    const maxPan = (0.5 - 0.5 / z) * 100          // don't pan past the frame edges
    const tx = clamp(100 * (0.5 - cx), maxPan)
    const ty = clamp(100 * (0.5 - cy), maxPan)
    povZoom.update((p) => ({
      zoom: p.zoom + (z - p.zoom) * EASE,
      x: p.x + (tx - p.x) * EASE,
      y: p.y + (ty - p.y) * EASE,
    }))
  }
  onMount(() => { raf = requestAnimationFrame(loop) })
  onDestroy(() => cancelAnimationFrame(raf))

  // Turning follow off eases the framing back to the full scene.
  let wasOn = false
  $effect(() => {
    if ($followOn) wasOn = true
    else if (wasOn) { wasOn = false; povZoom.set({ zoom: 1, x: 0, y: 0 }) }
  })
  const hasTarget = $derived($followOn && !!$followState && !$followState.lost)
</script>

{#if $followOn && $mode === 'pov'}
  <div class="follow caps" class:live={hasTarget}>
    <span class="dot"></span>{hasTarget ? 'FOLLOW-CAM' : 'FOLLOW-CAM · SELECT A TARGET'}
  </div>
{/if}

<style>
  .follow { position: absolute; top: 14px; left: 50%; transform: translateX(-50%); z-index: var(--z-overlay);
    pointer-events: none; display: inline-flex; align-items: center; gap: 8px; font-size: 9px; letter-spacing: 0.2em;
    color: var(--ink-dim); background: rgba(4,7,10,0.6); padding: 4px 12px; border: 1px solid rgba(255,255,255,0.12); }
  .follow.live { color: #2fbf8f; border-color: rgba(47,191,143,0.5); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 8px currentColor; animation: fblink 1.2s ease-in-out infinite; }
  @keyframes fblink { 50% { opacity: 0.35; } }
  @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
</style>
