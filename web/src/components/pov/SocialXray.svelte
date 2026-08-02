<script lang="ts">
  // Social X-ray: each person's attention direction as a cone, plus links between people who are
  // genuinely interacting. Designed to be READABLE, not twitchy: positions and links are eased and
  // hysteretic (a link must persist to appear and lingers as it fades), so it does not flicker. Only
  // links with real evidence are drawn (facing each other / watching / approaching), so unrelated
  // people are never joined. Click a person and it shows only that person's interactions.
  import { onMount, onDestroy } from 'svelte'
  import { get } from 'svelte/store'
  import { tracks, type FTrack } from '../../lib/foresight'
  import { selectedDetection } from '../../lib/stores'

  let W = $state(1)
  let H = $state(1)
  let raf = 0

  const D2R = Math.PI / 180
  const CONE_HALF = 24 * D2R
  const CONE_LEN = 1.35               // body-heights: short, so a cone never reaches a distant person
  const TOWARD = Math.cos(32 * D2R)   // tight facing gate: only a definite "looking at" counts
  const EASE = 0.18                   // position smoothing per frame
  const RISE = 0.12                   // link fade-in per frame (~0.25 s to full)
  const FALL = 0.006                  // link fade-out per frame (~2.8 s lingering, so you can read it)
  const SHOW = 0.35                   // only draw a link once it has persisted past this strength

  interface P { hx: number; hy: number; fx: number; fy: number; appear: number; facing: boolean }
  interface L { s: number; kind: string; label: string; shown: boolean }
  const P = new Map<string, P>()      // eased per-track head position + facing
  const LNK = new Map<string, L>()    // link strengths (hysteresis), key = "idA|idB"

  let cones = $state<Array<{ x: number; y: number; p1x: number; p1y: number; p2x: number; p2y: number; op: number; alarm: boolean }>>([])
  let links = $state<Array<{ ax: number; ay: number; bx: number; by: number; mx: number; my: number; op: number; kind: string; label: string; lab: boolean }>>([])

  // Eye level: a little below the top of the head, so the attention cone reads as a line of sight.
  const headPix = (t: FTrack) => ({ x: t.cx * W, y: (t.cy - t.h * 0.42) * H })

  function facesToward(fx: number, fy: number, fromx: number, fromy: number, tox: number, toy: number): number {
    const vx = tox - fromx, vy = toy - fromy
    const vl = Math.hypot(vx, vy)
    if (vl < 1e-3) return -1
    return (fx * vx + fy * vy) / vl
  }

  function frame() {
    raf = requestAnimationFrame(frame)
    const list = get(tracks).filter((t) => t.cls === 'person')
    const sel = get(selectedDetection)
    const focus = sel && sel.cls === 'person' ? sel.id : null
    const seen = new Set<string>()

    // 1. ease each person's head position + facing vector
    for (const t of list) {
      seen.add(t.id)
      const hp = headPix(t)
      let p = P.get(t.id)
      if (!p) { p = { hx: hp.x, hy: hp.y, fx: 1, fy: 0, appear: 0, facing: false }; P.set(t.id, p) }
      p.hx += (hp.x - p.hx) * EASE
      p.hy += (hp.y - p.hy) * EASE
      const hasFace = typeof t.facing === 'number' && t.speed < 0.06
      p.facing = hasFace
      if (hasFace) {
        const a = (t.facing as number) * D2R
        p.fx += (Math.cos(a) - p.fx) * EASE
        p.fy += (Math.sin(a) - p.fy) * EASE
      }
      p.appear = Math.min(1, p.appear + 0.08)
    }
    for (const [id, p] of P) {   // fade out + drop people who left
      if (!seen.has(id)) { p.appear -= 0.06; if (p.appear <= 0.02) P.delete(id) }
    }

    // 2. candidate interactions this frame (tight: proximity + facing / approaching)
    const cand = new Map<string, { kind: string; label: string }>()
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const a = list[i], b = list[j]
        const gap = Math.hypot(a.gx - b.gx, a.gy - b.gy)
        const prox = gap / (((a.h + b.h) / 2) || 0.1)
        if (prox > 3.2) continue
        const pa = P.get(a.id), pb = P.get(b.id)
        if (!pa || !pb) continue
        const aFb = pa.facing ? facesToward(pa.fx, pa.fy, pa.hx, pa.hy, pb.hx, pb.hy) : -1
        const bFa = pb.facing ? facesToward(pb.fx, pb.fy, pb.hx, pb.hy, pa.hx, pa.hy) : -1
        const closing = ((a.vx - b.vx) * (a.gx - b.gx) + (a.vy - b.vy) * (a.gy - b.gy)) < -0.003
        let kind = '', label = ''
        if (prox < 2.1 && aFb > TOWARD && bFa > TOWARD) { kind = 'engaged'; label = 'ENGAGED' }
        else if (prox < 2.2 && (aFb > TOWARD || bFa > TOWARD)) { kind = 'watch'; label = 'WATCHING' }
        else if (prox < 3.2 && closing && (a.speed > 0.05 || b.speed > 0.05)) { kind = 'approach'; label = 'APPROACHING' }
        else continue
        cand.set(a.id < b.id ? `${a.id}|${b.id}` : `${b.id}|${a.id}`, { kind, label })
      }
    }

    // 3. hysteresis: strengthen present links, decay absent ones
    for (const [k, c] of cand) {
      let l = LNK.get(k)
      if (!l) { l = { s: 0, kind: c.kind, label: c.label, shown: false }; LNK.set(k, l) }
      l.kind = c.kind; l.label = c.label
      l.s = Math.min(1, l.s + RISE)
      if (l.s >= SHOW) l.shown = true          // latch on once it has genuinely persisted
    }
    for (const [k, l] of LNK) {
      if (!cand.has(k)) { l.s -= FALL; if (l.s <= 0.02) LNK.delete(k) }   // linger + slow fade
    }

    // 4. render lists (respecting the click-to-focus filter)
    const cs: typeof cones = []
    for (const [id, p] of P) {
      if (!p.facing || p.appear < 0.06) continue
      if (focus && id !== focus) continue        // focused: show ONLY that person's cone + gaze
      const t = list.find((x) => x.id === id)
      const len = Math.max((t ? t.h : 0.1) * CONE_LEN * H, 34)
      const ang = Math.atan2(p.fy, p.fx)
      cs.push({
        x: p.hx, y: p.hy,
        p1x: p.hx + Math.cos(ang - CONE_HALF) * len, p1y: p.hy + Math.sin(ang - CONE_HALF) * len,
        p2x: p.hx + Math.cos(ang + CONE_HALF) * len, p2y: p.hy + Math.sin(ang + CONE_HALF) * len,
        op: p.appear * 0.16, alarm: !!(t && t.alarm),
      })
    }
    const ls: typeof links = []
    for (const [k, l] of LNK) {
      if (!l.shown || l.s < 0.1) continue        // only latched links, lingering as they slowly fade
      const [ia, ib] = k.split('|')
      if (focus && ia !== focus && ib !== focus) continue   // focused: only this person's links
      const pa = P.get(ia), pb = P.get(ib)
      if (!pa || !pb) continue
      ls.push({ ax: pa.hx, ay: pa.hy, bx: pb.hx, by: pb.hy, mx: (pa.hx + pb.hx) / 2, my: (pa.hy + pb.hy) / 2, op: Math.min(1, l.s), kind: l.kind, label: l.label, lab: false })
    }
    ls.sort((p, q) => q.op - p.op)
    // Label every visible link, but never stack two labels on the same spot (greedy spacing).
    const placed: { x: number; y: number }[] = []
    for (const l of ls) {
      if (l.op < 0.35) continue
      if (placed.every((p) => Math.hypot(p.x - l.mx, p.y - l.my) > 44)) { l.lab = true; placed.push({ x: l.mx, y: l.my }) }
    }
    cones = cs
    links = ls.slice(0, 14)
  }

  onMount(() => { raf = requestAnimationFrame(frame) })
  onDestroy(() => cancelAnimationFrame(raf))
</script>

<div class="social" bind:clientWidth={W} bind:clientHeight={H}>
  <svg viewBox="0 0 {W} {H}" preserveAspectRatio="none">
    {#each cones as c}
      <path class="cone" class:alarm={c.alarm} style="opacity:{c.op}" d="M{c.x} {c.y} L{c.p1x} {c.p1y} L{c.p2x} {c.p2y} Z" />
    {/each}
    {#each links as l (l.label + l.ax)}
      <line class="link {l.kind}" style="opacity:{l.op}" x1={l.ax} y1={l.ay} x2={l.bx} y2={l.by} />
      <circle class="node {l.kind}" style="opacity:{l.op}" cx={l.ax} cy={l.ay} r="3" />
      <circle class="node {l.kind}" style="opacity:{l.op}" cx={l.bx} cy={l.by} r="3" />
      {#if l.lab}<text class="ltxt {l.kind}" style="opacity:{l.op}" x={l.mx} y={l.my - 4}>{l.label}</text>{/if}
    {/each}
  </svg>
</div>

<style>
  .social { position: absolute; inset: 0; z-index: 6; pointer-events: none; }
  svg { width: 100%; height: 100%; overflow: visible; }
  .cone { fill: #37cfe0; }
  .cone.alarm { fill: var(--scarlet); }
  .link { stroke-width: 1.6; stroke-dasharray: 5 4; }
  .ltxt { font-family: var(--font-mono, monospace); font-size: 9px; letter-spacing: 0.12em; text-anchor: middle;
    paint-order: stroke; stroke: #05070a; stroke-width: 2.4px; }
  .engaged { stroke: var(--scarlet, #e10600); fill: var(--scarlet, #e10600); }
  .engaged.link { stroke-dasharray: none; }
  .watch { stroke: #f0a63c; fill: #f0a63c; }
  .approach { stroke: #37cfe0; fill: #37cfe0; }
</style>
