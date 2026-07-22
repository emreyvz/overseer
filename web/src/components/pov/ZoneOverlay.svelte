<script lang="ts">
  // Renders saved analyst zones/lines for the active camera over the POV.
  import { zones } from '../../lib/zones'
  import { activeCam } from '../../lib/stores'
  let visible = $derived($zones.filter((z) => z.cam === $activeCam))
  const pstr = (pts: [number, number][]) => pts.map((p) => `${p[0] * 100},${p[1] * 100}`).join(' ')
</script>

{#if visible.length}
  <svg class="zov" viewBox="0 0 100 100" preserveAspectRatio="none">
    {#each visible as z}
      {#if z.kind === 'area'}
        <polygon class="area" points={pstr(z.points)} />
        <text class="lbl" x={z.points[0][0] * 100} y={z.points[0][1] * 100 - 1}>{z.name}</text>
      {:else if z.points.length >= 2}
        <line class="line" x1={z.points[0][0] * 100} y1={z.points[0][1] * 100} x2={z.points[1][0] * 100} y2={z.points[1][1] * 100} />
        <text class="lbl" x={z.points[0][0] * 100} y={z.points[0][1] * 100 - 1}>{z.name}</text>
      {/if}
    {/each}
  </svg>
{/if}

<style>
  .zov { position: absolute; inset: 0; width: 100%; height: 100%; z-index: var(--z-overlay); pointer-events: none; }
  .area { fill: rgba(225,6,0,0.1); stroke: var(--scarlet); stroke-width: 0.28; vector-effect: non-scaling-stroke; opacity: 0.85; }
  .line { stroke: var(--scarlet); stroke-width: 0.35; vector-effect: non-scaling-stroke; stroke-dasharray: 2 1.2; }
  .lbl { fill: var(--scarlet); font-family: var(--font-mono); font-size: 2.4px; letter-spacing: 0.1px; text-transform: uppercase; }
</style>
