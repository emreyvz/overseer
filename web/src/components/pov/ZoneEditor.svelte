<script lang="ts">
  // POV zone/line editor: draw new ones (→ a matching alert rule is created), and
  // see / delete the existing zones on this camera right on the feed.
  import { zoneEditor, activeCam, flashBanner } from '../../lib/stores'
  import { sfx } from '../../lib/audio'
  import { addZone, addRule, delZone, zones } from '../../lib/zones'

  let kind = $state<'area' | 'line'>('line')
  let name = $state('')
  let pts = $state<[number, number][]>([])
  let surface = $state<HTMLButtonElement>()

  let mine = $derived($zones.filter((z) => z.cam === ($activeCam ?? '')))
  const centroid = (p: [number, number][]): [number, number] =>
    [p.reduce((s, q) => s + q[0], 0) / p.length, p.reduce((s, q) => s + q[1], 0) / p.length]

  function norm(e: MouseEvent): [number, number] {
    const r = surface!.getBoundingClientRect()
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]
  }
  function click(e: MouseEvent) {
    if (e.detail === 0) return
    sfx('click', { volume: 0.3 })
    pts = [...pts, norm(e)]
    if (kind === 'line' && pts.length === 2) finish()
  }
  function finish() {
    if ((kind === 'line' && pts.length < 2) || (kind === 'area' && pts.length < 3)) return
    const zname = name.trim() || (kind === 'line' ? 'CROSSING LINE' : 'RESTRICTED AREA')
    const z = addZone({ name: zname, kind, cam: $activeCam ?? '', points: pts })
    addRule({ name: zname, event: kind === 'line' ? 'line' : 'restricted', zoneId: z.id, threshold: 0, severity: 'warning' })
    sfx('sonar'); flashBanner('ZONE + ALERT CREATED', false, 1400)
    pts = []; name = '' // keep the editor open to add / manage more
  }
  function removeZone(e: MouseEvent, id: string) { e.stopPropagation(); sfx('click'); delZone(id); flashBanner('ZONE REMOVED', false, 1000) }
  function close() { sfx('click'); zoneEditor.set(false) }
</script>

<div class="ze">
  <button class="surface" bind:this={surface} type="button"
    onclick={click} ondblclick={finish} class:line={kind === 'line'}>
    <svg class="draw" viewBox="0 0 100 100" preserveAspectRatio="none">
      <!-- existing zones on this camera (cyan) -->
      {#each mine as z}
        {#if z.kind === 'area' && z.points.length > 1}
          <polygon class="exist" points={z.points.map((p) => `${p[0] * 100},${p[1] * 100}`).join(' ')} />
        {:else if z.points.length > 1}
          <line class="exist" x1={z.points[0][0] * 100} y1={z.points[0][1] * 100} x2={z.points[1][0] * 100} y2={z.points[1][1] * 100} />
        {/if}
      {/each}
      <!-- current drawing (red) -->
      {#if pts.length > 1}
        {#if kind === 'area'}
          <polygon class="poly" points={pts.map((p) => `${p[0] * 100},${p[1] * 100}`).join(' ')} />
        {:else}
          <line class="ln" x1={pts[0][0] * 100} y1={pts[0][1] * 100} x2={pts[1][0] * 100} y2={pts[1][1] * 100} />
        {/if}
      {/if}
      {#each pts as p}<circle class="pt" cx={p[0] * 100} cy={p[1] * 100} r="0.7" />{/each}
    </svg>
  </button>

  <!-- delete chips over each existing zone -->
  {#each mine as z}
    {@const c = centroid(z.points)}
    <button class="zdel caps" style={`left:${c[0] * 100}%;top:${c[1] * 100}%`} onclick={(e) => removeZone(e, z.id)}>
      ✕ {z.name}
    </button>
  {/each}

  <div class="bar panel caps">
    <span class="lead hot">/// {kind === 'line' ? 'CROSSING LINE' : 'RESTRICTED AREA'}</span>
    <div class="seg">
      <button class:on={kind === 'line'} onclick={() => { kind = 'line'; pts = [] }}>LINE</button>
      <button class:on={kind === 'area'} onclick={() => { kind = 'area'; pts = [] }}>AREA</button>
    </div>
    <input bind:value={name} placeholder="ZONE NAME" spellcheck="false" />
    <span class="tip">{kind === 'line' ? 'CLICK 2 POINTS' : 'CLICK POINTS · DBL-CLICK FINISH'} · {mine.length} ON CAM · ✕ TO DELETE</span>
    <button class="go" onclick={finish}>ADD</button>
    <button class="go" onclick={close}>DONE (ESC)</button>
  </div>
</div>

<style>
  .ze { position: fixed; inset: 0; z-index: var(--z-cmd); }
  .surface { position: absolute; inset: 0; width: 100%; height: 100%; padding: 0; display: block; cursor: crosshair; background: rgba(0,0,0,0.15); }
  .draw { position: absolute; inset: 0; width: 100%; height: 100%; }
  .exist { fill: rgba(56,208,227,0.10); stroke: var(--cyan); stroke-width: 0.3; stroke-dasharray: 1.4 1; vector-effect: non-scaling-stroke; }
  line.exist { fill: none; }
  .poly { fill: rgba(225,6,0,0.14); stroke: var(--scarlet); stroke-width: 0.3; vector-effect: non-scaling-stroke; }
  .ln { stroke: var(--scarlet); stroke-width: 0.4; vector-effect: non-scaling-stroke; }
  .pt { fill: #fff; }
  .zdel { position: absolute; transform: translate(-50%, -50%); z-index: 2; padding: 2px 8px; background: rgba(0,0,0,0.7);
    border: 1px solid var(--cyan); color: var(--cyan); font-size: 8px; letter-spacing: 0.1em; cursor: pointer; white-space: nowrap; }
  .zdel:hover { border-color: var(--scarlet); color: var(--scarlet); background: rgba(225,6,0,0.15); }
  .bar { position: absolute; left: 50%; bottom: 12vh; transform: translateX(-50%); display: flex; align-items: center; gap: 12px;
    padding: 10px 14px; background: #000; border: 1px solid var(--ink); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .lead { color: var(--scarlet); }
  .seg button { padding: 4px 10px; border: 1px solid var(--ink-dim); color: var(--ink-dim); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .seg button.on { border-color: var(--scarlet); color: var(--scarlet); }
  input { background: #000; border: 1px solid var(--hairline); color: var(--ink); font-family: var(--font-mono);
    font-size: var(--fs-micro); padding: 5px 8px; text-transform: uppercase; }
  input:focus { outline: none; border-color: var(--scarlet); }
  .tip { font-size: 8px; color: var(--ink-ghost); }
  .go { padding: 5px 12px; border: 1px solid var(--ink); color: var(--ink); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .go:hover { background: var(--scarlet); border-color: var(--scarlet); color: #fff; }
</style>
