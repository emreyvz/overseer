<script lang="ts">
  // Register an unknown object for visual tracking: draw a box, name it, register.
  import { objectRegister, flashBanner } from '../../lib/stores'
  import { sfx } from '../../lib/audio'
  import { sendCommand } from '../../lib/ws'

  let name = $state('')
  let p1 = $state<[number, number] | null>(null)
  let p2 = $state<[number, number] | null>(null)
  let cur = $state<[number, number] | null>(null)
  let surface = $state<HTMLButtonElement>()

  function norm(e: MouseEvent): [number, number] {
    const r = surface!.getBoundingClientRect()
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]
  }
  function click(e: MouseEvent) {
    if (e.detail === 0) return
    sfx('click', { volume: 0.3 })
    if (!p1) { p1 = norm(e); p2 = null }
    else if (!p2) p2 = norm(e)
    else { p1 = norm(e); p2 = null }
  }
  function move(e: MouseEvent) { if (p1 && !p2) cur = norm(e) }

  let rect = $derived.by(() => {
    const b = p2 ?? cur
    if (!p1 || !b) return null
    const x = Math.min(p1[0], b[0]), y = Math.min(p1[1], b[1])
    return { x, y, w: Math.abs(b[0] - p1[0]), h: Math.abs(b[1] - p1[1]) }
  })

  function register() {
    if (!rect || rect.w < 0.01 || rect.h < 0.01) { sfx('error'); return }
    sfx('sonar')
    sendCommand(`ooi:${name.trim() || 'OBJECT'}|${rect.x.toFixed(4)},${rect.y.toFixed(4)},${rect.w.toFixed(4)},${rect.h.toFixed(4)}`)
    flashBanner('OBJECT REGISTERED · TRACKING', false, 1600)
    objectRegister.set(false)
  }
  function cancel() { sfx('click'); objectRegister.set(false) }
</script>

<div class="or">
  <button class="surface" bind:this={surface} type="button" onclick={click} onpointermove={move}>
    {#if rect}
      <svg class="draw" viewBox="0 0 100 100" preserveAspectRatio="none">
        <rect class="box" x={rect.x * 100} y={rect.y * 100} width={rect.w * 100} height={rect.h * 100} />
      </svg>
    {/if}
  </button>

  <div class="bar panel caps">
    <span class="lead hot">/// REGISTER OBJECT</span>
    <input bind:value={name} placeholder="OBJECT NAME (e.g. RED BACKPACK)" spellcheck="false" />
    <span class="tip">{p1 && !p2 ? 'CLICK 2ND CORNER' : 'CLICK TWO CORNERS'}</span>
    <button class="go" onclick={register}>REGISTER ▸</button>
    <button class="go" onclick={cancel}>CANCEL (ESC)</button>
  </div>
</div>

<style>
  .or { position: fixed; inset: 0; z-index: var(--z-cmd); }
  .surface { position: absolute; inset: 0; width: 100%; height: 100%; padding: 0; display: block; cursor: crosshair; background: rgba(0,0,0,0.15); }
  .draw { position: absolute; inset: 0; width: 100%; height: 100%; }
  .box { fill: rgba(56,208,227,0.14); stroke: var(--cyan); stroke-width: 0.35; vector-effect: non-scaling-stroke; }
  .bar { position: absolute; left: 50%; bottom: 12vh; transform: translateX(-50%); display: flex; align-items: center; gap: 12px;
    padding: 10px 14px; background: #000; border: 1px solid var(--ink); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .lead { color: var(--scarlet); }
  input { background: #000; border: 1px solid var(--hairline); color: var(--ink); font-family: var(--font-mono);
    font-size: var(--fs-micro); padding: 5px 8px; text-transform: uppercase; min-width: 200px; }
  input:focus { outline: none; border-color: var(--cyan); }
  .tip { font-size: 8px; color: var(--ink-ghost); }
  .go { padding: 5px 12px; border: 1px solid var(--ink); color: var(--ink); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .go:hover { background: var(--cyan); border-color: var(--cyan); color: #000; }
</style>
