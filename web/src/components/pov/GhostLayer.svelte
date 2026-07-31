<script lang="ts">
  // Predictive ghosts: for every moving subject, translucent "ghosts" of where it will be in
  // ~1..2.6s are drawn on the live frame, plus a motion vector. When two subjects are predicted
  // to converge, a pulsing reticle marks the meeting point before it happens. Sits inside the
  // zoom wrapper so it stays aligned with the feed. Reads the shared foresight engine.
  import { tracks, predict, encounters, type FTrack } from '../../lib/foresight'

  const HORIZON = 2.6                 // seconds we look ahead
  const STEPS = [0.87, 1.73, 2.6]     // ghost snapshots along the horizon
  const OPACS = [0.46, 0.3, 0.16]
  const MOVING = 0.03                 // only ghost subjects actually on the move (norm/s)

  let W = $state(0), H = $state(0)

  const colorOf = (t: FTrack): string =>
    t.alarm ? 'var(--scarlet)' : t.cls === 'vehicle' ? '#e8a13a' : t.cls === 'animal' ? '#8fd67a' : 'var(--cyan)'

  const movers = $derived($tracks.filter((t) => t.speed > MOVING))
  // one ghost rect per (track, horizon step)
  const ghosts = $derived(
    movers.flatMap((t) =>
      STEPS.map((s, i) => {
        const p = predict(t, s)                 // predicted ground point (bottom-centre)
        return {
          key: `${t.id}:${i}`,
          x: (p.x - t.w / 2) * W, y: (p.y - t.h) * H, w: t.w * W, h: t.h * H,
          op: OPACS[i], color: colorOf(t),
        }
      }),
    ),
  )
  // motion vector: current box centre -> predicted centre at the full horizon
  const vectors = $derived(
    movers.map((t) => {
      const p = predict(t, HORIZON)
      return { key: t.id, x1: t.cx * W, y1: t.cy * H, x2: p.x * W, y2: (p.y - t.h / 2) * H, color: colorOf(t) }
    }),
  )
  const enc = $derived(encounters($tracks, HORIZON).map((e) => ({ x: e.x * W, y: e.y * H, t: e.t })))
</script>

<div class="glayer" bind:clientWidth={W} bind:clientHeight={H}>
  {#if W > 0 && H > 0 && (ghosts.length || enc.length)}
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      <defs>
        <marker id="fhead" markerWidth="7" markerHeight="7" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="currentColor" />
        </marker>
      </defs>
      {#each vectors as v (v.key)}
        <line class="vec" x1={v.x1} y1={v.y1} x2={v.x2} y2={v.y2}
          style={`color:${v.color};stroke:${v.color}`} marker-end="url(#fhead)" />
      {/each}
      {#each ghosts as g (g.key)}
        <rect class="ghost" x={g.x} y={g.y} width={g.w} height={g.h}
          style={`stroke:${g.color};fill:${g.color};fill-opacity:0.06;opacity:${g.op}`} />
      {/each}
      {#each enc as e, i (i)}
        <g class="enc" transform={`translate(${e.x} ${e.y})`}>
          <circle class="er" r="20" />
          <circle class="er d2" r="12" />
          <line x1="-16" y1="0" x2="16" y2="0" /><line x1="0" y1="-16" x2="0" y2="16" />
          <text class="et" y="-26">PREDICTED ENCOUNTER · {e.t.toFixed(1)}s</text>
        </g>
      {/each}
    </svg>
  {/if}
</div>

<style>
  .glayer { position: absolute; inset: 0; z-index: 6; pointer-events: none; }
  svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  .ghost { fill: none; stroke-width: 1.3; stroke-dasharray: 4 4; vector-effect: non-scaling-stroke; }
  .vec { stroke-width: 1.4; stroke-dasharray: 2 5; opacity: 0.55; vector-effect: non-scaling-stroke; }
  .enc line { stroke: var(--scarlet); stroke-width: 1.4; vector-effect: non-scaling-stroke; }
  .enc .er { fill: none; stroke: var(--scarlet); stroke-width: 1.2; vector-effect: non-scaling-stroke; transform-box: fill-box; transform-origin: center; animation: pulse 1.1s ease-out infinite; }
  .enc .er.d2 { animation-delay: 0.35s; }
  .enc .et { fill: var(--scarlet); font-size: 9px; letter-spacing: 0.12em; text-anchor: middle; text-shadow: 0 0 5px #000; }
  @keyframes pulse { 0% { opacity: 0.9; transform: scale(0.6); } 100% { opacity: 0; transform: scale(1.25); } }
</style>
