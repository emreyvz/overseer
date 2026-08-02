<script lang="ts">
  // Social X-ray: draws each person's attention direction as a cone and links people who are
  // interacting (facing each other, watching, standing together, or approaching). Facing comes
  // from the pose-derived heading on the detection (FTrack.facing), falling back to motion heading;
  // interactions are a frontend proximity + mutual-facing pass over the foresight tracks. SVG layer,
  // pixel-aligned to the feed (mounted inside the POV zoom wrapper), no backend load beyond the
  // facing angle already on the stream.
  import { tracks, type FTrack } from '../../lib/foresight'

  let W = $state(1)
  let H = $state(1)

  const D2R = Math.PI / 180
  const CONE_HALF = 26 * D2R          // half-angle of the attention cone
  const CONE_LEN = 2.6                // cone length in body-heights
  const TOWARD = Math.cos(42 * D2R)   // alignment above which one is "facing" the other (tight, to be sure)

  const head = (t: FTrack) => ({ x: t.cx * W, y: (t.cy - t.h / 2) * H })   // eyes ~ box top-centre

  let people = $derived($tracks.filter((t) => t.cls === 'person'))

  interface Cone { x: number; y: number; p1x: number; p1y: number; p2x: number; p2y: number; rx: number; ry: number; alarm: boolean }
  let cones = $derived.by<Cone[]>(() => {
    const out: Cone[] = []
    for (const t of people) {
      if (typeof t.facing !== 'number') continue
      const hd = head(t)
      const len = Math.max(t.h * CONE_LEN * H, 44)
      const a = t.facing * D2R
      out.push({
        x: hd.x, y: hd.y,
        rx: hd.x + Math.cos(a) * len, ry: hd.y + Math.sin(a) * len,
        p1x: hd.x + Math.cos(a - CONE_HALF) * len, p1y: hd.y + Math.sin(a - CONE_HALF) * len,
        p2x: hd.x + Math.cos(a + CONE_HALF) * len, p2y: hd.y + Math.sin(a + CONE_HALF) * len,
        alarm: t.alarm,
      })
    }
    return out
  })

  function facesToward(t: FTrack, tx: number, ty: number): number {
    if (typeof t.facing !== 'number') return -1
    const hd = head(t)
    const vx = tx - hd.x, vy = ty - hd.y
    const vl = Math.hypot(vx, vy)
    if (vl < 1e-3) return -1
    const a = t.facing * D2R
    return (Math.cos(a) * vx + Math.sin(a) * vy) / vl   // cos of the angle between facing and A->B
  }

  interface Link { ax: number; ay: number; bx: number; by: number; mx: number; my: number; label: string; kind: string; prox: number }
  let links = $derived.by<Link[]>(() => {
    const out: Link[] = []
    for (let i = 0; i < people.length; i++) {
      for (let j = i + 1; j < people.length; j++) {
        const a = people[i], b = people[j]
        const gap = Math.hypot(a.gx - b.gx, a.gy - b.gy)
        const avgH = (a.h + b.h) / 2 || 0.1
        const prox = gap / avgH                          // separation in body-heights
        const ha = head(a), hb = head(b)
        // Only claim an interaction with real evidence: facing (engaged/watching) or clear dynamics
        // (approaching). Mere proximity is NOT enough - people standing side by side in a queue face
        // the same way, not each other, so they get no link. Facing must be a pose read (both moving
        // subjects share a heading, which would false-positive), so require a still-ish subject.
        const aFacing = typeof a.facing === 'number' && a.speed < 0.06
        const bFacing = typeof b.facing === 'number' && b.speed < 0.06
        const aFb = aFacing ? facesToward(a, hb.x, hb.y) : -1
        const bFa = bFacing ? facesToward(b, ha.x, ha.y) : -1
        const closing = ((a.vx - b.vx) * (a.gx - b.gx) + (a.vy - b.vy) * (a.gy - b.gy)) < -0.003
        let label = '', kind = ''
        if (prox < 2.1 && aFb > TOWARD && bFa > TOWARD) { label = 'ENGAGED'; kind = 'engaged' }
        else if (prox < 2.1 && (aFb > TOWARD || bFa > TOWARD)) { label = 'WATCHING'; kind = 'watch' }
        else if (prox < 3.2 && closing && (a.speed > 0.05 || b.speed > 0.05)) { label = 'APPROACHING'; kind = 'approach' }
        else continue
        out.push({ ax: ha.x, ay: ha.y, bx: hb.x, by: hb.y, mx: (ha.x + hb.x) / 2, my: (ha.y + hb.y) / 2, label, kind, prox })
      }
    }
    return out.sort((p, q) => p.prox - q.prox).slice(0, 12)   // closest interactions, capped for clarity
  })
</script>

<div class="social" bind:clientWidth={W} bind:clientHeight={H}>
  <svg viewBox="0 0 {W} {H}" preserveAspectRatio="none">
    {#each cones as c}
      <path class="cone" class:alarm={c.alarm} d="M{c.x} {c.y} L{c.p1x} {c.p1y} L{c.p2x} {c.p2y} Z" />
      <line class="ray" class:alarm={c.alarm} x1={c.x} y1={c.y} x2={c.rx} y2={c.ry} />
      <circle class="eye" class:alarm={c.alarm} cx={c.x} cy={c.y} r="2.6" />
    {/each}
    {#each links as l}
      <line class="link {l.kind}" x1={l.ax} y1={l.ay} x2={l.bx} y2={l.by} />
      <circle class="node {l.kind}" cx={l.ax} cy={l.ay} r="3" />
      <circle class="node {l.kind}" cx={l.bx} cy={l.by} r="3" />
      <text class="ltxt {l.kind}" x={l.mx} y={l.my - 4}>{l.label}</text>
    {/each}
  </svg>
</div>

<style>
  .social { position: absolute; inset: 0; z-index: 6; pointer-events: none; }
  svg { width: 100%; height: 100%; overflow: visible; }
  .cone { fill: #37cfe0; opacity: 0.1; }
  .cone.alarm { fill: var(--scarlet); }
  .ray { stroke: #6fe6f2; stroke-width: 1.4; opacity: 0.55; }
  .ray.alarm { stroke: var(--scarlet); }
  .eye { fill: #9ff0fa; }
  .eye.alarm { fill: var(--scarlet); }

  .link { stroke-width: 1.6; opacity: 0.9; stroke-dasharray: 5 4; }
  .node { opacity: 0.95; }
  .ltxt { font-family: var(--font-mono, monospace); font-size: 9px; letter-spacing: 0.12em; text-anchor: middle; opacity: 0.95; }

  .engaged { stroke: var(--scarlet, #e10600); fill: var(--scarlet, #e10600); }
  .engaged.link { opacity: 1; stroke-dasharray: none; }
  .watch { stroke: #f0a63c; fill: #f0a63c; }
  .group { stroke: #43d19e; fill: #43d19e; }
  .approach { stroke: #37cfe0; fill: #37cfe0; }
  text.ltxt { paint-order: stroke; stroke: #05070a; stroke-width: 2.4px; }
</style>
