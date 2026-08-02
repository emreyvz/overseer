<!-- Live "Enhance": press the button (or E), drag a box over any region, and it comes back as a
     crisp PHOTOGRAPHIC close-up (Lanczos + denoise + local contrast + gentle sharpen, only a light
     SR blend — so it reads like a sharp optical zoom, not a cartoonish super-res). -->
<script lang="ts">
  import { get } from 'svelte/store'
  import { enhanceMode, enhanceResult, activeCam, povZoom, mode, flashBanner } from '../../lib/stores'
  import { api } from '../../lib/api'
  import { sfx } from '../../lib/audio'

  let host = $state<HTMLDivElement>()
  let dragging = $state(false)
  let busy = $state(false)
  let rect = $state<{ x: number; y: number; w: number; h: number } | null>(null)
  let start = { x: 0, y: 0 }

  // undo the digital zoom/pan so the drawn box maps to real frame coordinates
  function screenToFrame(nx: number, ny: number): [number, number] {
    const z = get(povZoom)
    return [(nx - 0.5) / z.zoom + 0.5 - z.x / 100, (ny - 0.5) / z.zoom + 0.5 - z.y / 100]
  }

  function down(e: PointerEvent) {
    if (!host) return
    ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
    const r = host.getBoundingClientRect()
    start = { x: e.clientX - r.left, y: e.clientY - r.top }
    rect = { x: start.x, y: start.y, w: 0, h: 0 }
    dragging = true
  }
  function move(e: PointerEvent) {
    if (!dragging || !host) return
    const r = host.getBoundingClientRect()
    const cx = e.clientX - r.left, cy = e.clientY - r.top
    rect = { x: Math.min(start.x, cx), y: Math.min(start.y, cy), w: Math.abs(cx - start.x), h: Math.abs(cy - start.y) }
  }
  async function up() {
    if (!dragging || !host || !rect) { dragging = false; return }
    dragging = false
    const r = host.getBoundingClientRect()
    if (rect.w < 12 || rect.h < 12) { rect = null; return }
    const [fx0, fy0] = screenToFrame(rect.x / r.width, rect.y / r.height)
    const [fx1, fy1] = screenToFrame((rect.x + rect.w) / r.width, (rect.y + rect.h) / r.height)
    const box: [number, number, number, number] = [Math.min(fx0, fx1), Math.min(fy0, fy1), Math.abs(fx1 - fx0), Math.abs(fy1 - fy0)]
    const id = get(activeCam)
    if (!id) { cancel(); return }
    busy = true
    try {
      const { image } = await api.enhance(id, box)
      if (image) { enhanceResult.set(image); sfx('ping') } else { flashBanner('NOTHING TO ENHANCE THERE', true, 1400) }
    } catch { flashBanner('ENHANCE FAILED', true, 1400) }
    busy = false; rect = null; enhanceMode.set(false)
  }
  function cancel() { dragging = false; rect = null; enhanceMode.set(false) }
  function activate() { sfx('click'); enhanceResult.set(null); enhanceMode.set(true) }
  function closeResult() { enhanceResult.set(null) }
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && ($enhanceMode || $enhanceResult)) { e.stopPropagation(); cancel(); enhanceResult.set(null) }
  }
</script>

<svelte:window onkeydown={onKey} />

{#if $mode === 'pov'}
  {#if !$enhanceMode && !$enhanceResult}
    <button class="ebtn caps" onclick={activate} title="Draw a box to enhance that region (E)">⊹ ENHANCE</button>
  {/if}

  {#if $enhanceMode}
    <div class="ecatch" bind:this={host} onpointerdown={down} onpointermove={move} onpointerup={up} role="presentation">
      <div class="ehint caps">DRAG A BOX TO ENHANCE · ESC TO CANCEL</div>
      {#if rect}<div class="esel" style={`left:${rect.x}px;top:${rect.y}px;width:${rect.w}px;height:${rect.h}px`}></div>{/if}
      {#if busy}<div class="ebusy caps"><span class="sp"></span>ENHANCING…</div>{/if}
    </div>
  {/if}

  {#if $enhanceResult}
    <div class="escrim" onclick={closeResult} role="presentation"></div>
    <div class="eloupe">
      <div class="etop caps"><span>⊹ ENHANCED</span><button class="ex" onclick={closeResult} aria-label="close">✕</button></div>
      <img src={$enhanceResult} alt="enhanced region" />
      <div class="ebot caps"><button onclick={activate}>◱ SELECT AGAIN</button></div>
    </div>
  {/if}
{/if}

<style>
  .ebtn { position: absolute; right: 20px; bottom: 96px; z-index: var(--z-panel); background: rgba(4,7,10,0.7);
    border: 1px solid var(--ink-dim); color: var(--ink-dim); font: inherit; font-size: 9px; letter-spacing: 0.16em;
    padding: 6px 11px; cursor: pointer; }
  .ebtn:hover { color: var(--cyan); border-color: var(--cyan); }

  .ecatch { position: absolute; inset: 0; z-index: var(--z-boot); cursor: crosshair; background: rgba(4,7,10,0.14); }
  .ehint { position: absolute; top: 16px; left: 50%; transform: translateX(-50%); font-size: 10px; letter-spacing: 0.2em;
    color: var(--cyan); background: rgba(4,7,10,0.6); padding: 5px 12px; }
  .esel { position: absolute; border: 1.5px solid var(--cyan); background: rgba(56,208,227,0.1);
    box-shadow: 0 0 0 9999px rgba(4,7,10,0.28); }
  .ebusy { position: absolute; left: 50%; top: 50%; transform: translate(-50%,-50%); display: inline-flex; align-items: center; gap: 9px;
    color: var(--cyan); background: rgba(4,7,10,0.7); padding: 8px 16px; letter-spacing: 0.16em; font-size: 11px; }
  .sp { width: 12px; height: 12px; border: 2px solid var(--cyan); border-top-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .escrim { position: fixed; inset: 0; z-index: 390; background: rgba(4,6,8,0.7); backdrop-filter: blur(2px); }
  .eloupe { position: fixed; z-index: 391; left: 50%; top: 50%; transform: translate(-50%,-50%);
    max-width: 80vw; max-height: 80vh; background: rgba(10,12,14,0.98); border: 1px solid rgba(56,208,227,0.4);
    box-shadow: 0 24px 80px rgba(0,0,0,0.7); display: flex; flex-direction: column; animation: pop 220ms cubic-bezier(0.16,1,0.3,1); }
  @keyframes pop { from { opacity: 0; transform: translate(-50%,-50%) scale(0.96); } }
  .etop, .ebot { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; font-size: 10px; letter-spacing: 0.16em; color: var(--cyan); }
  .ebot { justify-content: center; }
  .etop .ex { background: none; border: 1px solid var(--ink-dim); color: var(--ink-dim); font: inherit; font-size: 11px; padding: 2px 8px; cursor: pointer; }
  .etop .ex:hover { color: var(--scarlet); border-color: var(--scarlet); }
  .eloupe img { max-width: 80vw; max-height: 66vh; object-fit: contain; display: block; image-rendering: auto; }
  .ebot button { background: none; border: 1px solid var(--ink-dim); color: var(--ink-dim); font: inherit; font-size: 10px; letter-spacing: 0.14em; padding: 5px 12px; cursor: pointer; }
  .ebot button:hover { color: var(--cyan); border-color: var(--cyan); }
</style>
