<script lang="ts">
  // Renders saved analyst zones/lines for the active camera over the POV. Lines are drawn as a
  // clear tripwire: a soft glow underlay, a dashed scarlet line, endpoint markers and a small
  // perpendicular tick so the operator can actually see it (the old 0.35px stroke was invisible).
  import { zones } from '../../lib/zones'
  import { activeCam } from '../../lib/stores'
  let visible = $derived($zones.filter((z) => z.cam === $activeCam))
  const pstr = (pts: [number, number][]) => pts.map((p) => `${p[0] * 100},${p[1] * 100}`).join(' ')

  // midpoint + a short perpendicular tick, so a line reads as a directional tripwire
  function tick(a: [number, number], b: [number, number]) {
    const mx = (a[0] + b[0]) * 50, my = (a[1] + b[1]) * 50   // *100/2
    let dx = (b[0] - a[0]) * 100, dy = (b[1] - a[1]) * 100
    const len = Math.hypot(dx, dy) || 1
    dx /= len; dy /= len
    const nx = -dy, ny = dx, t = 2.2
    return { x1: mx - nx * t, y1: my - ny * t, x2: mx + nx * t, y2: my + ny * t }
  }
</script>

{#if visible.length}
  <svg class="zov" viewBox="0 0 100 100" preserveAspectRatio="none">
    {#each visible as z}
      {#if z.kind === 'area'}
        <polygon class="area-glow" points={pstr(z.points)} />
        <polygon class="area" points={pstr(z.points)} />
        {#each z.points as p}<circle class="vtx" cx={p[0] * 100} cy={p[1] * 100} r="0.7" />{/each}
        <text class="lbl" x={z.points[0][0] * 100} y={z.points[0][1] * 100 - 1.4}>{z.name}</text>
      {:else if z.points.length >= 2}
        {@const t = tick(z.points[0], z.points[1])}
        <line class="line-glow" x1={z.points[0][0] * 100} y1={z.points[0][1] * 100} x2={z.points[1][0] * 100} y2={z.points[1][1] * 100} />
        <line class="line" x1={z.points[0][0] * 100} y1={z.points[0][1] * 100} x2={z.points[1][0] * 100} y2={z.points[1][1] * 100} />
        <line class="dir" x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} />
        <circle class="end" cx={z.points[0][0] * 100} cy={z.points[0][1] * 100} r="0.9" />
        <circle class="end" cx={z.points[1][0] * 100} cy={z.points[1][1] * 100} r="0.9" />
        <text class="lbl" x={z.points[0][0] * 100} y={z.points[0][1] * 100 - 1.4}>{z.name}</text>
      {/if}
    {/each}
  </svg>
{/if}

<style>
  .zov { position: absolute; inset: 0; width: 100%; height: 100%; z-index: var(--z-overlay); pointer-events: none; }
  /* stroke-width is in px here (non-scaling-stroke): the old 0.35 rendered sub-pixel. */
  .area-glow { fill: none; stroke: var(--scarlet); stroke-width: 6; vector-effect: non-scaling-stroke; opacity: 0.18; }
  .area { fill: rgba(225,6,0,0.12); stroke: var(--scarlet); stroke-width: 1.6; vector-effect: non-scaling-stroke; opacity: 0.9; }
  .line-glow { stroke: var(--scarlet); stroke-width: 6; vector-effect: non-scaling-stroke; opacity: 0.22; stroke-linecap: round; }
  .line { stroke: var(--scarlet); stroke-width: 2; vector-effect: non-scaling-stroke; stroke-dasharray: 5 3; stroke-linecap: round; }
  .dir { stroke: var(--scarlet); stroke-width: 1.6; vector-effect: non-scaling-stroke; opacity: 0.9; }
  .end { fill: var(--scarlet); stroke: #000; stroke-width: 0.6; vector-effect: non-scaling-stroke; }
  .vtx { fill: var(--scarlet); }
  .lbl { fill: var(--scarlet); font-family: var(--font-mono); font-size: 2.6px; letter-spacing: 0.1px; text-transform: uppercase; paint-order: stroke; stroke: rgba(0,0,0,0.7); stroke-width: 0.5px; }
</style>
