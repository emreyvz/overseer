<script lang="ts">
  // Social graph: the discovered co-occurrence network between subjects. Nodes are people and
  // vehicles (with a photo), edges are associations weighted by confidence. A small deterministic
  // force layout arranges it; click a node to open that subject's profile.
  import { onDestroy, onMount } from 'svelte'
  import { api, type SocialGraph, type GraphNode } from '../../lib/api'
  import { sfx } from '../../lib/audio'

  let { onclose, onopen }: { onclose: () => void; onopen?: (id: string) => void } = $props()
  const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
  const W = 1000, H = 620

  let g = $state<SocialGraph>({ nodes: [], edges: [] })
  let loading = $state(true)
  let pos = $state<Record<string, { x: number; y: number }>>({})
  let hover = $state<string | null>(null)

  async function load() {
    loading = true
    try { g = await api.relationships() } catch { g = { nodes: [], edges: [] } }
    pos = layout(g.nodes, g.edges)
    loading = false
  }
  // deterministic force layout (golden-angle seed, no randomness — stable each open)
  function layout(nodes: GraphNode[], edges: SocialGraph['edges']) {
    const n = nodes.length
    const p: Record<string, { x: number; y: number; vx: number; vy: number }> = {}
    nodes.forEach((nd, i) => {
      const ang = i * 2.399963
      const r = Math.min(W, H) * 0.36 * Math.sqrt((i + 0.5) / Math.max(1, n))
      p[nd.id] = { x: W / 2 + r * Math.cos(ang), y: H / 2 + r * Math.sin(ang), vx: 0, vy: 0 }
    })
    const springs = edges.map((e) => ({ a: e.a, b: e.b, w: 0.5 + e.confidence }))
    for (let it = 0; it < 140; it++) {
      for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
        const a = p[nodes[i].id], b = p[nodes[j].id]
        let dx = a.x - b.x, dy = a.y - b.y
        const d2 = dx * dx + dy * dy + 0.01, d = Math.sqrt(d2), f = 2600 / d2
        dx /= d; dy /= d; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f
      }
      for (const e of springs) {
        const a = p[e.a], b = p[e.b]; if (!a || !b) continue
        let dx = b.x - a.x, dy = b.y - a.y
        const d = Math.hypot(dx, dy) + 0.01, target = 100 / e.w, f = (d - target) * 0.02
        dx /= d; dy /= d; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f
      }
      for (const nd of nodes) {
        const a = p[nd.id]
        a.vx += (W / 2 - a.x) * 0.002; a.vy += (H / 2 - a.y) * 0.002
        a.x += a.vx * 0.85; a.y += a.vy * 0.85; a.vx *= 0.82; a.vy *= 0.82
      }
    }
    const out: Record<string, { x: number; y: number }> = {}
    for (const nd of nodes) out[nd.id] = { x: p[nd.id].x, y: p[nd.id].y }
    return out
  }

  const near = (id: string, e: SocialGraph['edges'][number]) => e.a === id || e.b === id
  const dim = (e: SocialGraph['edges'][number]) => !!hover && !near(hover, e)
  function pick(nd: GraphNode) { sfx('ping', { volume: 0.3 }); onopen?.(nd.id) }
  function onkey(ev: KeyboardEvent) { if (ev.key === 'Escape') { ev.stopPropagation(); onclose() } }
  onMount(() => { sfx('sonar'); load(); window.addEventListener('keydown', onkey, true) })
  onDestroy(() => window.removeEventListener('keydown', onkey, true))
</script>

<div class="net" role="dialog" aria-label="Social graph">
  <header class="top caps">
    <span class="eyebrow">◈ RELATIONSHIP GRAPH</span>
    <span class="cnt">{g.nodes.length} SUBJECTS · {g.edges.length} LINKS</span>
    <span class="spacer"></span>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  {#if loading}
    <div class="empty caps">MAPPING RELATIONSHIPS_</div>
  {:else if g.nodes.length === 0}
    <div class="empty caps">NO ASSOCIATIONS YET · THE GRAPH BUILDS AS SUBJECTS ARE SEEN TOGETHER</div>
  {:else}
    <svg class="canvas" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
      {#each g.edges as e (e.a + e.b)}
        {#if pos[e.a] && pos[e.b]}
          <line class="edge" class:dim={dim(e)} x1={pos[e.a].x} y1={pos[e.a].y} x2={pos[e.b].x} y2={pos[e.b].y}
            style={`stroke-width:${0.6 + e.confidence * 2.4}; opacity:${dim(e) ? 0.06 : 0.12 + e.confidence * 0.5}`} />
        {/if}
      {/each}
      {#each g.nodes as nd (nd.id)}
        {#if pos[nd.id]}
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <g class="node" class:veh={nd.cls === 'vehicle'} transform={`translate(${pos[nd.id].x} ${pos[nd.id].y})`}
            role="button" tabindex="0" onpointerenter={() => (hover = nd.id)} onpointerleave={() => (hover = null)}
            onclick={() => pick(nd)} onkeydown={(ev) => { if (ev.key === 'Enter') pick(nd) }}>
            <circle class="halo" r="20" />
            <clipPath id={`c-${nd.id}`}><circle r="15" /></clipPath>
            {#if nd.snapshot}<image href={`${API}${nd.snapshot}`} x="-15" y="-15" width="30" height="30" clip-path={`url(#c-${nd.id})`} preserveAspectRatio="xMidYMid slice" />{/if}
            <circle class="ring" r="15" />
            <text class="lbl caps" y="30">{nd.id}</text>
          </g>
        {/if}
      {/each}
    </svg>
    <div class="hint caps">CLICK A SUBJECT TO OPEN ITS PROFILE · LINK THICKNESS = ASSOCIATION STRENGTH</div>
  {/if}
</div>

<style>
  .net { position: fixed; inset: 0; z-index: var(--z-boot); background: radial-gradient(120% 80% at 50% 0%, #0a1016 0%, #05070a 72%);
    color: var(--ink); display: flex; flex-direction: column; overflow: hidden; animation: nin 320ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes nin { from { opacity: 0; } }
  .top { display: flex; align-items: center; gap: 12px; padding: 13px 22px; border-bottom: 1px solid var(--hairline);
    font-size: var(--fs-label); letter-spacing: var(--tracking); background: #04070a; }
  .eyebrow { color: var(--scarlet); } .cnt { color: var(--ink-dim); font-size: 9px; } .spacer { flex: 1; }
  .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: pointer; font-size: 9px; letter-spacing: var(--tracking); }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .empty { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--ink-dim); letter-spacing: 0.16em; text-align: center; padding: 0 40px; }
  .canvas { flex: 1; min-height: 0; width: 100%; }
  .edge { stroke: var(--cyan); vector-effect: non-scaling-stroke; transition: opacity 120ms; }
  .node { cursor: pointer; } .node:focus { outline: none; }
  .node .halo { fill: rgba(56,208,227,0.06); }
  .node .ring { fill: none; stroke: var(--cyan); stroke-width: 1.4; }
  .node.veh .ring { stroke: var(--amber, #d8a200); }
  .node:hover .ring { stroke-width: 2.4; filter: drop-shadow(0 0 6px var(--cyan)); }
  .node .lbl { fill: var(--ink-dim); font-size: 9px; text-anchor: middle; letter-spacing: 0.08em; }
  .node:hover .lbl { fill: var(--ink); }
  .hint { position: absolute; bottom: 12px; left: 0; right: 0; text-align: center; font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.14em; }
</style>
