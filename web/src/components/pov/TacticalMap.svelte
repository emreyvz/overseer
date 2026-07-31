<script lang="ts">
  // Tactical god-view: a top-down radar built from a single flat camera. Each subject's ground
  // point is inverse-perspective projected onto a bird's-eye scope, so the operator sees the
  // whole space from above: who is where, moving which way, and (from the foresight engine)
  // where they will be next, drawn as a ghost blip ahead of the real contact. Click a contact
  // to select it on the main view.
  import { get } from 'svelte/store'
  import { tracks, predict, encounters, type FTrack } from '../../lib/foresight'
  import { detections, selectedDetection } from '../../lib/stores'
  import { sfx } from '../../lib/audio'

  const VB_W = 150, VB_H = 110
  const HORIZON_IMG = 0.30      // image y of the horizon line (above = too far to place)
  const FOV = 1.15              // assumed horizontal field of view (~66 deg)
  const NEAR_R = 0.10, FAR_R = 1.0
  const GHOST_DT = 2.2          // seconds ahead for the map's ghost blip
  const MOVING = 0.03

  const clamp = (v: number, a: number, b: number) => (v < a ? a : v > b ? b : v)
  // inverse-perspective: image ground point -> bird's-eye position in [0..1] scope space
  function project(gx: number, gy: number): { x: number; y: number } {
    const yy = clamp((gy - HORIZON_IMG) / (1 - HORIZON_IMG), 0, 1)   // 0 far .. 1 near
    const r = NEAR_R + (FAR_R - NEAR_R) * (1 - yy)                   // near objects close in
    const theta = (gx - 0.5) * FOV
    const lateral = Math.sin(theta) * (0.30 + 0.5 * r)
    return { x: clamp(0.5 + lateral, 0.03, 0.97), y: clamp(0.94 - r * Math.cos(theta) * 0.86, 0.05, 0.95) }
  }
  const toVB = (p: { x: number; y: number }) => ({ x: p.x * VB_W, y: p.y * VB_H })
  const colorOf = (t: FTrack): string =>
    t.alarm ? 'var(--scarlet)' : t.cls === 'vehicle' ? '#e8a13a' : t.cls === 'animal' ? '#8fd67a' : 'var(--cyan)'
  const radOf = (t: FTrack): number => (t.cls === 'vehicle' ? 3.4 : t.alarm ? 3.2 : 2.6)

  // scope geometry (constants in viewBox space)
  const APEX = toVB({ x: 0.5, y: 0.94 })
  const FARL = toVB(project(0, HORIZON_IMG))
  const FARR = toVB(project(1, HORIZON_IMG))
  const lerp = (a: { x: number; y: number }, b: { x: number; y: number }, f: number) => ({ x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f })
  const rings = [0.42, 0.72, 1.0].map((f) => {
    const l = lerp(APEX, FARL, f), r = lerp(APEX, FARR, f)
    return `M ${l.x} ${l.y} Q ${(l.x + r.x) / 2} ${(l.y + r.y) / 2 - 9 * f} ${r.x} ${r.y}`
  })
  const wedge = `M ${APEX.x} ${APEX.y} L ${FARL.x} ${FARL.y} L ${FARR.x} ${FARR.y} Z`

  const blips = $derived($tracks.map((t) => {
    const p = toVB(project(t.gx, t.gy))
    const trail = t.hist.map((h) => toVB(project(h.x, h.y)))
    const ghost = t.speed > MOVING ? toVB(project(predict(t, GHOST_DT).x, predict(t, GHOST_DT).y)) : null
    return { id: t.id, x: p.x, y: p.y, r: radOf(t), color: colorOf(t), alarm: t.alarm,
      trail: trail.map((q) => `${q.x},${q.y}`).join(' '), ghost }
  }))
  const encs = $derived(encounters($tracks, 2.6).map((e) => toVB(project(e.x, e.y))))

  function pick(id: string) {
    const d = get(detections).find((x) => x.id === id)
    if (d) { sfx('ping', { volume: 0.3 }); selectedDetection.set(d) }
  }
</script>

<div class="tac">
  <div class="thead caps">
    <span class="tt">◎ TACTICAL · GOD-VIEW</span>
    <span class="tc">{blips.length} CONTACT{blips.length === 1 ? '' : 'S'}</span>
  </div>
  <div class="scope">
    <svg viewBox={`0 0 ${VB_W} ${VB_H}`} preserveAspectRatio="xMidYMid meet">
      <path class="wedge" d={wedge} />
      {#each rings as d (d)}<path class="ring" {d} />{/each}
      <!-- radar pings from the camera origin -->
      <circle class="ping" cx={APEX.x} cy={APEX.y} r="6" />
      <circle class="ping p2" cx={APEX.x} cy={APEX.y} r="6" />
      <!-- camera at the apex -->
      <path class="cam" d={`M ${APEX.x} ${APEX.y - 4} L ${APEX.x - 3.4} ${APEX.y + 2.6} L ${APEX.x + 3.4} ${APEX.y + 2.6} Z`} />

      {#each blips as b (b.id)}
        {#if b.trail}<polyline class="trail" points={b.trail} style={`stroke:${b.color}`} />{/if}
        {#if b.ghost}
          <line class="gcon" x1={b.x} y1={b.y} x2={b.ghost.x} y2={b.ghost.y} style={`stroke:${b.color}`} />
          <circle class="gho" cx={b.ghost.x} cy={b.ghost.y} r={b.r * 0.9} style={`stroke:${b.color}`} />
        {/if}
      {/each}
      {#each blips as b (b.id)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <circle class="blip" class:alarm={b.alarm} cx={b.x} cy={b.y} r={b.r}
          style={`fill:${b.color}`} role="button" tabindex="-1" aria-label={`contact ${b.id}`}
          onclick={() => pick(b.id)} onkeydown={(e) => { if (e.key === 'Enter') pick(b.id) }} />
      {/each}
      {#each encs as e, i (i)}
        <circle class="encm" cx={e.x} cy={e.y} r="4.5" />
      {/each}
    </svg>
    {#if blips.length === 0}<div class="tempty caps">NO CONTACTS IN VIEW</div>{/if}
  </div>
  <div class="tlegend caps">
    <span><i class="lp per"></i>PERSON</span>
    <span><i class="lp veh"></i>VEHICLE</span>
    <span><i class="lp gho"></i>FORECAST</span>
  </div>
</div>

<style>
  .tac { position: absolute; left: 50%; bottom: 16px; transform: translateX(-50%); z-index: var(--z-panel);
    width: 300px; background: rgba(4,7,10,0.82); border: 1px solid var(--hairline); backdrop-filter: blur(3px);
    animation: tin 320ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes tin { from { opacity: 0; transform: translate(-50%, 12px); } }
  .thead { display: flex; align-items: center; justify-content: space-between; padding: 6px 10px; border-bottom: 1px solid var(--hairline); }
  .tt { font-size: 8px; color: var(--cyan); letter-spacing: 0.16em; }
  .tc { font-size: 8px; color: var(--ink-dim); letter-spacing: 0.1em; }
  .scope { position: relative; background: radial-gradient(120% 90% at 50% 100%, #071119 0%, #04070a 70%); }
  svg { display: block; width: 100%; height: auto; }
  .wedge { fill: rgba(56,208,227,0.05); stroke: rgba(56,208,227,0.18); stroke-width: 0.4; }
  .ring { fill: none; stroke: rgba(56,208,227,0.16); stroke-width: 0.4; stroke-dasharray: 1.5 2; }
  .cam { fill: var(--ink); }
  .ping { fill: none; stroke: var(--cyan); stroke-width: 0.5; transform-box: fill-box; transform-origin: center;
    animation: ping 3s ease-out infinite; opacity: 0; }
  .ping.p2 { animation-delay: 1.5s; }
  @keyframes ping { 0% { transform: scale(0.3); opacity: 0.5; } 100% { transform: scale(9); opacity: 0; } }
  .trail { fill: none; stroke-width: 0.7; opacity: 0.28; vector-effect: non-scaling-stroke; }
  .gcon { stroke-width: 0.5; stroke-dasharray: 1 1.4; opacity: 0.5; vector-effect: non-scaling-stroke; }
  .gho { fill: none; stroke-width: 0.7; stroke-dasharray: 1.4 1.4; opacity: 0.65; vector-effect: non-scaling-stroke; }
  .blip { cursor: pointer; stroke: rgba(0,0,0,0.5); stroke-width: 0.4; }
  .blip:hover { stroke: var(--ink); stroke-width: 0.6; }
  .blip.alarm { animation: bpulse 0.9s ease-in-out infinite; }
  @keyframes bpulse { 50% { opacity: 0.4; } }
  .encm { fill: none; stroke: var(--scarlet); stroke-width: 0.7; transform-box: fill-box; transform-origin: center; animation: bpulse 0.8s ease-in-out infinite; }
  .tempty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--ink-ghost); font-size: 8px; letter-spacing: 0.14em; }
  .tlegend { display: flex; gap: 12px; justify-content: center; padding: 5px 0 6px; border-top: 1px solid var(--hairline); }
  .tlegend span { display: flex; align-items: center; gap: 4px; font-size: 7px; color: var(--ink-dim); letter-spacing: 0.1em; }
  .lp { width: 6px; height: 6px; border-radius: 50%; }
  .lp.per { background: var(--cyan); } .lp.veh { background: #e8a13a; }
  .lp.gho { background: none; border: 1px solid var(--cyan); }
</style>
