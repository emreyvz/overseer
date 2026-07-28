<script lang="ts">
  // Green pointer brackets over a located search / watchlist hit, so the moment its camera
  // opens the operator sees exactly where on the frame the match is. Pulses in, then clears
  // itself after a few seconds so it draws attention without lingering.
  import { onDestroy } from 'svelte'
  import { matchHighlight, activeCam } from '../../lib/stores'

  const pc = (n: number) => `${(n * 100).toFixed(2)}%`
  let timer: ReturnType<typeof setTimeout> | undefined
  $effect(() => {
    const h = $matchHighlight
    if (timer) clearTimeout(timer)
    if (h) timer = setTimeout(() => matchHighlight.set(null), 7000)
  })
  onDestroy(() => { if (timer) clearTimeout(timer) })
</script>

{#if $matchHighlight && $matchHighlight.camId === $activeCam}
  {@const b = $matchHighlight.bbox}
  <div class="mh" style={`left:${pc(b[0])};top:${pc(b[1])};width:${pc(b[2])};height:${pc(b[3])}`}>
    <span class="br p1"></span><span class="br p2"></span><span class="br p3"></span><span class="br p4"></span>
    <span class="pulse"></span>
    <span class="lbl caps">◈ LOCATED</span>
  </div>
{/if}

<style>
  .mh { position: absolute; z-index: 7; pointer-events: none; --grn: #35e07f;
    animation: mhin 420ms cubic-bezier(0.2, 0.9, 0.2, 1); }
  @keyframes mhin { from { transform: scale(1.28); opacity: 0; } }
  .br { position: absolute; width: 17px; height: 17px; border: 2px solid var(--grn);
    filter: drop-shadow(0 0 6px rgba(53, 224, 127, 0.7)); }
  .p1 { top: -2px; left: -2px; border-right: 0; border-bottom: 0; }
  .p2 { top: -2px; right: -2px; border-left: 0; border-bottom: 0; }
  .p3 { bottom: -2px; left: -2px; border-right: 0; border-top: 0; }
  .p4 { bottom: -2px; right: -2px; border-left: 0; border-top: 0; }
  .pulse { position: absolute; inset: -3px; border: 1.5px solid var(--grn);
    animation: mhpulse 1.3s ease-out infinite; }
  @keyframes mhpulse { 0% { inset: -3px; opacity: 0.85; } 100% { inset: -15px; opacity: 0; } }
  .lbl { position: absolute; left: 0; top: -19px; font-size: 10px; letter-spacing: 0.14em;
    color: var(--grn); text-shadow: 0 0 6px rgba(53, 224, 127, 0.6); white-space: nowrap; }
</style>
