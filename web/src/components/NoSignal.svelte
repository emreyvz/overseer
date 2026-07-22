<script lang="ts">
  // Analog TV "no signal" static (item 7). Chunky grayscale noise + roll bars.
  import { onMount, onDestroy } from 'svelte'
  let { label = true }: { label?: boolean } = $props()
  let cv = $state<HTMLCanvasElement>()
  let raf = 0
  let last = 0

  onMount(() => {
    const ctx = cv?.getContext('2d')
    if (!ctx || !cv) return
    const W = 168, H = 94
    cv.width = W; cv.height = H
    let roll = 0
    const img = ctx.createImageData(W, H)
    const d = img.data
    function draw(t: number) {
      raf = requestAnimationFrame(draw)
      if (t - last < 55) return
      last = t
      for (let i = 0; i < d.length; i += 4) {
        const v = (Math.random() * 255) | 0
        d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255
      }
      ctx!.putImageData(img, 0, 0)
      roll = (roll + 3) % H
      ctx!.fillStyle = 'rgba(255,255,255,0.05)'; ctx!.fillRect(0, roll, W, 7)
      ctx!.fillStyle = 'rgba(0,0,0,0.45)'; ctx!.fillRect(0, (roll + H / 2) % H, W, 4)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  })
  onDestroy(() => cancelAnimationFrame(raf))
</script>

<div class="ns">
  <canvas bind:this={cv} class="cv"></canvas>
  {#if label}<span class="txt caps">NO SIGNAL</span>{/if}
</div>

<style>
  .ns { position: absolute; inset: 0; overflow: hidden; background: #050505; }
  .cv { width: 100%; height: 100%; image-rendering: pixelated; opacity: 0.55; }
  .txt { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
    font-size: var(--fs-micro); letter-spacing: var(--tracking-wide); color: var(--ink-dim);
    background: rgba(0,0,0,0.55); padding: 2px 8px; border: 1px solid var(--hairline); }
</style>
