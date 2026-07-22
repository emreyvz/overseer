<script lang="ts">
  // Live crowd-density heatmap over the POV (feature 5). Decaying red density from
  // person detections. Gated on the HEATMAP module toggle.
  import { onMount, onDestroy } from 'svelte'
  import { get } from 'svelte/store'
  import { detections, modules } from '../../lib/stores'

  const GW = 128, GH = 72
  let cv = $state<HTMLCanvasElement>()
  let raf = 0
  const grid = new Float32Array(GW * GH)

  function enabled() { return !!get(modules).find((m) => m.key === 'heatmap')?.on }

  onMount(() => {
    const ctx = cv?.getContext('2d')
    if (!ctx || !cv) return
    cv.width = GW; cv.height = GH
    const img = ctx.createImageData(GW, GH)
    function frame() {
      raf = requestAnimationFrame(frame)
      const on = enabled()
      for (let i = 0; i < grid.length; i++) grid[i] *= 0.94  // decay
      if (on) {
        for (const d of get(detections)) {
          if (d.cls !== 'person' && d.cls !== 'animal') continue
          const cx = Math.floor((d.bbox[0] + d.bbox[2] / 2) * GW)
          const cy = Math.floor((d.bbox[1] + d.bbox[3]) * GH)
          for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
            const x = cx + dx, y = cy + dy
            if (x < 0 || y < 0 || x >= GW || y >= GH) continue
            grid[y * GW + x] += Math.exp(-(dx * dx + dy * dy) / 8) * 0.6
          }
        }
      }
      const px = img.data
      for (let i = 0; i < grid.length; i++) {
        const v = on ? Math.min(1, grid[i]) : 0
        px[i * 4] = 225; px[i * 4 + 1] = 20 * (1 - v); px[i * 4 + 2] = 0
        px[i * 4 + 3] = Math.floor(v * 190)
      }
      ctx!.putImageData(img, 0, 0)
    }
    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  })
  onDestroy(() => cancelAnimationFrame(raf))
</script>

<canvas bind:this={cv} class="heat" aria-hidden="true"></canvas>

<style>
  .heat { position: absolute; inset: 0; width: 100%; height: 100%; z-index: var(--z-grid);
    pointer-events: none; image-rendering: auto; filter: blur(6px); mix-blend-mode: screen; opacity: 0.8; }
</style>
