<script lang="ts">
  // Ego relationship network for the profile page: the subject at the centre, their direct
  // contacts (1 hop) and, in turn, WHO THOSE are linked to (2 hops). A deterministic force
  // layout arranges it; drag to pan, wheel to zoom (the standalone graph couldn't pan — this
  // one can). Click a node to open that subject's profile.
  import { onDestroy, onMount } from 'svelte'
  import { api, type EgoGraph, type EgoNode } from '../../lib/api'
  import { sfx } from '../../lib/audio'

  let { id, onopen }: { id: string; onopen?: (id: string) => void } = $props()
  const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
  const W = 620, H = 420

  let g = $state<EgoGraph>({ center: '', nodes: [], edges: [] })
  let loading = $state(true)
  let pos = $state<Record<string, { x: number; y: number }>>({})
  let hover = $state<string | null>(null)
  // pan/zoom transform
  let tx = $state(0), ty = $state(0), k = $state(1)
  let dragging = false, lastX = 0, lastY = 0, moved = false

  async function load() {
    loading = true
    try { g = await api.entityGraph(id) } catch { g = { center: id, nodes: [], edges: [] } }
    pos = layout(g.nodes, g.edges, g.center)
    tx = 0; ty = 0; k = 1
    loading = false
  }

  // deterministic force layout, centre node pinned at the middle (golden-angle seed, no random)
  function layout(nodes: EgoNode[], edges: EgoGraph['edges'], center: string) {
    const n = nodes.length
    const p: Record<string, { x: number; y: number; vx: number; vy: number; fixed: boolean }> = {}
    nodes.forEach((nd, i) => {
      if (nd.id === center) { p[nd.id] = { x: W / 2, y: H / 2, vx: 0, vy: 0, fixed: true }; return }
      const ang = i * 2.399963
      const r = Math.min(W, H) * (nd.hop === 1 ? 0.26 : 0.42) * (0.8 + 0.2 * Math.sqrt((i + 0.5) / Math.max(1, n)))
      p[nd.id] = { x: W / 2 + r * Math.cos(ang), y: H / 2 + r * Math.sin(ang), vx: 0, vy: 0, fixed: false }
    })
    const springs = edges.map((e) => ({ a: e.a, b: e.b, w: 0.5 + e.confidence }))
    for (let it = 0; it < 160; it++) {
      for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
        const a = p[nodes[i].id], b = p[nodes[j].id]
        let dx = a.x - b.x, dy = a.y - b.y
        const d2 = dx * dx + dy * dy + 0.01, d = Math.sqrt(d2), f = 2400 / d2
        dx /= d; dy /= d; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f
      }
      for (const e of springs) {
        const a = p[e.a], b = p[e.b]; if (!a || !b) continue
        let dx = b.x - a.x, dy = b.y - a.y
        const d = Math.hypot(dx, dy) + 0.01, target = 96 / e.w, f = (d - target) * 0.02
        dx /= d; dy /= d; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f
      }
      for (const nd of nodes) {
        const a = p[nd.id]
        if (a.fixed) { a.x = W / 2; a.y = H / 2; a.vx = 0; a.vy = 0; continue }
        a.vx += (W / 2 - a.x) * 0.004; a.vy += (H / 2 - a.y) * 0.004
        a.x += a.vx * 0.85; a.y += a.vy * 0.85; a.vx *= 0.82; a.vy *= 0.82
      }
    }
    const out: Record<string, { x: number; y: number }> = {}
    for (const nd of nodes) out[nd.id] = { x: p[nd.id].x, y: p[nd.id].y }
    return out
  }

  const near = (nid: string, e: EgoGraph['edges'][number]) => e.a === nid || e.b === nid
  const dim = (e: EgoGraph['edges'][number]) => !!hover && !near(hover, e)
  const src = (u?: string | null) => (u ? (u.startsWith('http') ? u : API + u) : '')

  function down(ev: PointerEvent) { dragging = true; moved = false; lastX = ev.clientX; lastY = ev.clientY; (ev.currentTarget as Element).setPointerCapture?.(ev.pointerId) }
  function move(ev: PointerEvent) {
    if (!dragging) return
    const dx = ev.clientX - lastX, dy = ev.clientY - lastY
    if (Math.abs(dx) + Math.abs(dy) > 2) moved = true
    tx += dx; ty += dy; lastX = ev.clientX; lastY = ev.clientY
  }
  function up() { dragging = false }
  function wheel(ev: WheelEvent) {
    ev.preventDefault()
    const f = ev.deltaY < 0 ? 1.12 : 1 / 1.12
    k = Math.max(0.4, Math.min(3.5, k * f))
  }
  function pick(nd: EgoNode) { if (nd.id === g.center) return; sfx('ping', { volume: 0.3 }); onopen?.(nd.id) }

  onMount(() => { load() })
  $effect(() => { void id; load() })
  onDestroy(() => {})
</script>

<div class="ego">
  {#if loading}
    <div class="empty caps">MAPPING NETWORK_</div>
  {:else if g.nodes.length <= 1}
    <div class="empty caps">NO KNOWN ASSOCIATIONS YET</div>
  {:else}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <svg class="canvas" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet"
      onpointerdown={down} onpointermove={move} onpointerup={up} onpointerleave={up} onwheel={wheel}
      role="application" aria-label="Relationship network">
      <g transform={`translate(${tx} ${ty}) scale(${k})`}>
        {#each g.edges as e (e.a + e.b)}
          {#if pos[e.a] && pos[e.b]}
            <line class="edge" class:dim={dim(e)} x1={pos[e.a].x} y1={pos[e.a].y} x2={pos[e.b].x} y2={pos[e.b].y}
              style={`stroke-width:${0.5 + e.confidence * 2.2}; opacity:${dim(e) ? 0.05 : 0.12 + e.confidence * 0.5}`} />
          {/if}
        {/each}
        {#each g.nodes as nd (nd.id)}
          {#if pos[nd.id]}
            {@const R = nd.id === g.center ? 22 : nd.hop === 1 ? 16 : 12}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <g class="node" class:center={nd.id === g.center} class:veh={nd.cls === 'vehicle'} class:h2={nd.hop === 2}
              transform={`translate(${pos[nd.id].x} ${pos[nd.id].y})`}
              role="button" tabindex="0" onpointerenter={() => (hover = nd.id)} onpointerleave={() => (hover = null)}
              ondblclick={() => pick(nd)} onkeydown={(e) => { if (e.key === 'Enter') pick(nd) }}>
              <circle class="halo" r={R + 5} />
              <clipPath id={`e-${nd.id}`}><circle r={R} /></clipPath>
              {#if nd.snapshot}<image href={src(nd.snapshot)} x={-R} y={-R} width={R * 2} height={R * 2} clip-path={`url(#e-${nd.id})`} preserveAspectRatio="xMidYMid slice" />{/if}
              <circle class="ring" r={R} />
              <text class="lbl caps" y={R + 12}>{nd.id}</text>
            </g>
          {/if}
        {/each}
      </g>
    </svg>
    <div class="hint caps">DRAG TO PAN · SCROLL TO ZOOM · CENTRE = THIS SUBJECT</div>
  {/if}
</div>

<style>
  .ego { position: relative; width: 100%; height: 100%; min-height: 260px; background: #05070a; border: 1px solid var(--hairline); overflow: hidden; }
  .canvas { width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
  .canvas:active { cursor: grabbing; }
  .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--ink-dim); font-size: 9px; letter-spacing: 0.16em; }
  .edge { stroke: var(--cyan); vector-effect: non-scaling-stroke; transition: opacity 120ms; }
  .node { cursor: pointer; } .node.center { cursor: default; }
  .node .halo { fill: rgba(56,208,227,0.06); }
  .node .ring { fill: none; stroke: var(--cyan); stroke-width: 1.4; }
  .node.veh .ring { stroke: var(--amber, #d8a200); }
  .node.center .ring { stroke: var(--scarlet); stroke-width: 2.2; filter: drop-shadow(0 0 8px var(--scarlet)); }
  .node.h2 .ring { stroke: var(--ink-dim); }
  .node:hover .ring { stroke-width: 2.4; filter: drop-shadow(0 0 6px var(--cyan)); }
  .node .lbl { fill: var(--ink-dim); font-size: 8px; text-anchor: middle; letter-spacing: 0.06em; }
  .node.center .lbl { fill: var(--ink); }
  .node:hover .lbl { fill: var(--ink); }
  .hint { position: absolute; bottom: 6px; left: 0; right: 0; text-align: center; font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.14em; pointer-events: none; }
</style>
