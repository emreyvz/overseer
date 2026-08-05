<script lang="ts">
  // DREAMSTATE — the surprise veil.
  //
  // Calm draws NOTHING. Not a dimmed heatmap, not a grid: an empty frame. That restraint is the
  // feature. When cells do depart from what this place normally looks like, they fill with a
  // scrolling scarlet hatch (never a solid, so the video stays readable), the coherent blob gets
  // a boundary that traces on, and a sigma tag names the size of the surprise.
  //
  // The word everywhere is DIVERGENCE, never THREAT. The model has no semantics and must not
  // pretend to.
  import { onDestroy, onMount } from 'svelte'
  import { divergences, dreamConsole, dreamStatus } from '../../lib/stores'
  import { sfx } from '../../lib/audio'
  import { triggerGlitch } from '../../lib/stores'

  const TICK_MS = 84                  // ~12 fps: the hatch only needs to crawl

  let cv = $state<HTMLCanvasElement>()
  let raf = 0
  let lastT = 0
  let scroll = 0
  let lastDiv = 0

  const st = $derived($dreamStatus)
  // the blob currently on screen, if one fired in the last few seconds
  const live = $derived($divergences.find((d) => Date.now() - d.ts < 8000) ?? null)

  $effect(() => {
    const d = $divergences[0]
    if (d && d.id !== lastDiv) {
      lastDiv = d.id
      sfx('sonar')
      triggerGlitch(140)
    }
  })

  function frame(now: number) {
    raf = requestAnimationFrame(frame)
    if (now - lastT < TICK_MS) return
    lastT = now
    const el = cv, s = st
    if (!el || !s) return
    const w = el.clientWidth, h = el.clientHeight
    if (!w || !h) return
    if (el.width !== w || el.height !== h) { el.width = w; el.height = h }
    const ctx = el.getContext('2d')!
    ctx.clearRect(0, 0, w, h)
    const [gw, gh] = s.grid
    const thr = s.threshold || 5
    scroll = (scroll + 0.9) % 12
    const cw = w / gw, ch = h / gh
    ctx.save()
    ctx.strokeStyle = '#e10600'
    ctx.lineWidth = 1
    for (let i = 0; i < s.cells.length; i++) {
      const z = s.cells[i]
      if (z < thr) continue
      const cx = (i % gw) * cw, cy = Math.floor(i / gw) * ch
      // hatch, not fill: the operator must still be able to see what is underneath
      const alpha = Math.max(0.08, Math.min(0.42, (z - 3) / 6))
      ctx.globalAlpha = alpha
      ctx.beginPath()
      ctx.rect(cx, cy, cw, ch)
      ctx.save()
      ctx.clip()
      for (let x = cx - ch - scroll; x < cx + cw + ch; x += 6) {
        ctx.moveTo(x, cy + ch)
        ctx.lineTo(x + ch, cy)
      }
      ctx.stroke()
      ctx.restore()
    }
    ctx.restore()
  }

  onMount(() => { raf = requestAnimationFrame(frame) })
  onDestroy(() => cancelAnimationFrame(raf))

</script>

{#if st}
  <div class="dv">
    <canvas bind:this={cv} class="hatch"></canvas>

    {#if live}
      <svg class="edge" viewBox="0 0 100 100" preserveAspectRatio="none">
        <polygon class="blob" points={live.blob.map((p) => `${p[0] * 100},${p[1] * 100}`).join(' ')} />
      </svg>
      <button class="tag" style={`left:${live.blob[0][0] * 100}%; top:${live.blob[0][1] * 100}%`}
        onclick={() => dreamConsole.set(live.id)} title="Open this divergence">
        <span class="sig display">Δ {live.peak_sigma.toFixed(1)}σ</span>
        <span class="sub caps">
          UNEXPECTED · {live.triage === 'subject' ? 'SUBJECT BEHAVIOUR' : 'SCENE CHANGE'}
        </span>
      </button>
    {/if}

  </div>
{/if}

<style>
  .dv { position: absolute; inset: 0; z-index: 7; pointer-events: none; }
  .hatch { position: absolute; inset: 0; width: 100%; height: 100%; }
  .edge { position: absolute; inset: 0; width: 100%; height: 100%; }
  .blob { fill: none; stroke: var(--scarlet); stroke-width: 1; stroke-dasharray: 3 3;
    vector-effect: non-scaling-stroke; animation: trace 260ms var(--ease) both, ants 2.6s linear infinite 260ms; }
  @keyframes trace { from { opacity: 0; } }
  @keyframes ants { to { stroke-dashoffset: -12; } }

  .tag { position: absolute; transform: translate(-2px, -100%); pointer-events: auto;
    background: none; border: none; padding: 0 0 4px; cursor: crosshair; text-align: left;
    display: flex; flex-direction: column; gap: 1px; animation: rise 220ms var(--ease) both; }
  @keyframes rise { from { opacity: 0; transform: translate(-2px, calc(-100% + 6px)); } }
  .sig { font-family: var(--font-display); font-weight: 700; font-size: 13px; color: var(--scarlet);
    letter-spacing: 0.1em; text-shadow: 0 0 6px var(--scarlet-glow); }
  .sub { font-size: 8px; color: var(--ink-dim); letter-spacing: 0.12em; text-shadow: 0 0 4px #000; }
  .tag:hover .sub { color: var(--ink); }

</style>
