<script lang="ts">
  // Zoom / pan control. Drives a digital zoom on the live feed (works on ANY
  // camera) and, best-effort, a real ONVIF PTZ move for pan-tilt-zoom cameras.
  // Open by default so the control is always at hand.
  import { activeCam, povZoom } from '../../lib/stores'
  import { api } from '../../lib/api'
  import { sfx } from '../../lib/audio'

  let open = $state(true)
  const STEP = 0.35, ZMAX = 5, ZMIN = 1

  function clampPan(z: { zoom: number; x: number; y: number }) {
    const lim = Math.max(0, (z.zoom - 1) * 50) // % offset allowed at this zoom
    z.x = Math.max(-lim, Math.min(lim, z.x))
    z.y = Math.max(-lim, Math.min(lim, z.y))
    return z
  }
  function zoom(d: number) {
    sfx('click', { volume: 0.2 })
    povZoom.update((z) => clampPan({ ...z, zoom: Math.max(ZMIN, Math.min(ZMAX, +(z.zoom + d).toFixed(2))) }))
    ptz(0, 0, d > 0 ? 0.5 : -0.5)
  }
  // dx/dy are the direction the operator wants to look (right/up = +). The zoomed
  // content moves the opposite way to reveal that side.
  function pan(dx: number, dy: number) {
    sfx('click', { volume: 0.2 })
    povZoom.update((z) => clampPan({ ...z, x: z.x + dx * 12, y: z.y + dy * 12 }))
    ptz(dx * 0.5, dy * 0.5, 0)
  }
  function reset() { sfx('click', { volume: 0.25 }); povZoom.set({ zoom: 1, x: 0, y: 0 }); ptz(0, 0, 0) }
  // best-effort real PTZ; silently ignored on non-PTZ cameras
  function ptz(p: number, t: number, z: number) { const id = $activeCam; if (id) api.ptz(id, p, t, z).catch(() => {}) }
</script>

<div class="ptz" class:open>
  <button class="tab caps" onclick={() => { open = !open; sfx('click', { volume: 0.25 }) }}>ZOOM {($povZoom.zoom).toFixed(1)}×</button>
  {#if open}
    <div class="pad">
      <button class="k up"    onclick={() => pan(0, 1)}>▲</button>
      <button class="k left"  onclick={() => pan(1, 0)}>◀</button>
      <button class="k mid caps" onclick={reset} title="reset">⊙</button>
      <button class="k right" onclick={() => pan(-1, 0)}>▶</button>
      <button class="k down"  onclick={() => pan(0, -1)}>▼</button>
      <div class="zoom">
        <button class="k z" onclick={() => zoom(STEP)}>+</button>
        <button class="k z" onclick={() => zoom(-STEP)}>−</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .ptz { position: absolute; right: 24px; top: 50%; transform: translateY(-50%); z-index: var(--z-overlay); display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
  .tab { padding: 4px 10px; border: 1px solid var(--ink-dim); background: rgba(0,0,0,0.55); color: var(--ink); font-size: var(--fs-micro); letter-spacing: var(--tracking); cursor: pointer; }
  .ptz.open .tab { border-color: var(--ink); }
  .pad { display: grid; grid-template-columns: repeat(3, 32px); grid-template-rows: repeat(3, 32px) auto; gap: 4px;
    background: rgba(0,0,0,0.62); border: 1px solid var(--hairline); padding: 8px; }
  .k { border: 1px solid var(--hairline); background: #05070a; color: var(--ink-dim); font-size: 13px; cursor: pointer; user-select: none; }
  .k:hover { border-color: var(--ink); color: var(--ink); }
  .k:active { background: var(--ink); color: var(--scarlet-ink); }
  .up { grid-area: 1 / 2; } .left { grid-area: 2 / 1; } .mid { grid-area: 2 / 2; font-size: 12px; } .right { grid-area: 2 / 3; } .down { grid-area: 3 / 2; }
  .zoom { grid-area: 4 / 1 / 5 / 4; display: flex; gap: 4px; margin-top: 4px; }
  .zoom .z { flex: 1; height: 26px; font-size: 15px; }
</style>
