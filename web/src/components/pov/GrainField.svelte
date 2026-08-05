<script lang="ts">
  // GRAIN — the learned movement field, drawn as a slow current over the ground.
  //
  // Iron filings over a magnet: each streak points along its cell's modal heading and is as
  // bright as the cell is decided about that heading. A two-way corridor visibly differs from a
  // one-way flow, and a cell the model has barely seen renders as an unaligned, undrifting
  // speck, so ignorance looks like ignorance.
  //
  // Everything is one canvas at a hard-throttled 20 fps with pre-baked geometry. This app has
  // already paid once for an overlay that ran two O(n^2) passes every rAF on the same thread as
  // the video decode.
  import { onDestroy, onMount } from 'svelte'
  import { grainStatus } from '../../lib/stores'
  import { buildStreaks, type FieldDensity, type Streak } from '../../lib/grain'

  const TICK_MS = 50            // 20 fps
  const DRIFT = 0.16            // loops per second along a streak's own direction

  let cv = $state<HTMLCanvasElement>()
  let density = $state<FieldDensity>('normal')

  let streaks: Streak[] = []
  let key = ''
  let raf = 0
  let lastT = 0
  let t0 = 0

  const st = $derived($grainStatus)

  $effect(() => {
    const s = st
    if (!s) { streaks = []; key = ''; return }
    const k = `${s.cam}|${s.bucket}|${s.cells.length}|${density}`
    if (k !== key) { key = k; streaks = buildStreaks(s, density) }
  })

  function frame(now: number) {
    raf = requestAnimationFrame(frame)
    if (now - lastT < TICK_MS) return
    lastT = now
    const el = cv
    if (!el) return
    const w = el.clientWidth, h = el.clientHeight
    if (!w || !h) return
    if (el.width !== w || el.height !== h) { el.width = w; el.height = h }
    const ctx = el.getContext('2d')!
    ctx.clearRect(0, 0, w, h)
    if (!streaks.length) return
    if (!t0) t0 = now
    const phase = (((now - t0) / 1000) * DRIFT) % 1
    ctx.lineCap = 'round'
    ctx.lineWidth = 1
    // one path per alpha band rather than one stroke per streak: 6 draw calls instead of 1200
    for (let band = 0; band < 6; band++) {
      const lo = band / 6, hi = (band + 1) / 6
      ctx.beginPath()
      let any = false
      for (const s of streaks) {
        const a = s.alpha
        if (a < lo * 0.34 || a >= hi * 0.34) continue
        any = true
        // mature streaks drift along their heading; unlearned specks stay put
        const p = s.mature ? (s.phase + phase) % 1 : 0.5
        const travel = s.mature ? (p - 0.5) * s.len * 3 : 0
        const cx = (s.x + Math.cos(s.a) * travel) * w
        const cy = (s.y + Math.sin(s.a) * travel) * h
        const dx = Math.cos(s.a) * s.len * w * 0.5
        const dy = Math.sin(s.a) * s.len * h * 0.5
        ctx.moveTo(cx - dx, cy - dy)
        ctx.lineTo(cx + dx, cy + dy)
        any = true
      }
      if (!any) continue
      ctx.strokeStyle = `rgba(124,130,136,${(lo + hi) / 2 * 0.34})`
      ctx.stroke()
    }
  }

  onMount(() => { raf = requestAnimationFrame(frame) })
  onDestroy(() => cancelAnimationFrame(raf))
</script>

{#if st}
  <div class="gf">
    <canvas bind:this={cv} class="field"></canvas>
  </div>
{/if}

<style>
  .gf { position: absolute; inset: 0; z-index: 5; pointer-events: none; }
  .field { position: absolute; inset: 0; width: 100%; height: 100%; }
</style>
