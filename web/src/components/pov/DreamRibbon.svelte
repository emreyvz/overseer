<script lang="ts">
  // DREAMSTATE — the divergence ribbon.
  //
  // Five minutes of scalar surprise scrolling along the bottom edge, with the firing threshold
  // as a hairline. It exists so the operator can see that the scene is calm, which is a thing no
  // alert-driven system ever tells you, and so a spike from a minute ago is still visible and
  // still clickable.
  import { onDestroy, onMount } from 'svelte'
  import { divergences, dreamConsole, dreamStatus } from '../../lib/stores'
  import { sfx } from '../../lib/audio'

  const WINDOW_MS = 5 * 60 * 1000
  const TICK_MS = 250

  let cv = $state<HTMLCanvasElement>()
  let hover = $state<number | null>(null)

  const RING = 2 * Math.PI * 13
  const st = $derived($dreamStatus)
  const samples: { t: number; v: number }[] = []
  let timer: ReturnType<typeof setInterval> | undefined

  function draw() {
    const el = cv, s = st
    if (!el || !s) return
    const w = el.clientWidth, h = el.clientHeight
    if (!w || !h) return
    if (el.width !== w || el.height !== h) { el.width = w; el.height = h }
    const ctx = el.getContext('2d')!
    ctx.clearRect(0, 0, w, h)
    const now = Date.now()
    const thr = s.threshold || 5
    const top = Math.max(thr * 1.4, 8)
    const x = (t: number) => w - ((now - t) / WINDOW_MS) * w
    const y = (v: number) => h - (Math.min(v, top) / top) * (h - 4) - 2

    // baseline area
    ctx.beginPath()
    ctx.moveTo(x(samples[0]?.t ?? now), h)
    for (const p of samples) ctx.lineTo(x(p.t), y(p.v))
    ctx.lineTo(w, h)
    ctx.closePath()
    ctx.fillStyle = 'rgba(58,62,66,0.30)'
    ctx.fill()

    // the part above the threshold, in the only colour that means alarm
    ctx.beginPath()
    let open = false
    for (const p of samples) {
      if (p.v >= thr) {
        if (!open) { ctx.moveTo(x(p.t), h); open = true }
        ctx.lineTo(x(p.t), y(p.v))
      } else if (open) { ctx.lineTo(x(p.t), h); open = false }
    }
    if (open) ctx.lineTo(w, h)
    ctx.strokeStyle = '#e10600'
    ctx.lineWidth = 1.4
    ctx.stroke()

    // threshold hairline
    ctx.beginPath()
    ctx.moveTo(0, y(thr)); ctx.lineTo(w, y(thr))
    ctx.strokeStyle = 'rgba(225,6,0,0.45)'
    ctx.setLineDash([4, 4]); ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([])

    // playhead
    ctx.beginPath(); ctx.moveTo(w - 1, 0); ctx.lineTo(w - 1, h)
    ctx.strokeStyle = 'rgba(56,208,227,0.7)'; ctx.stroke()
  }

  onMount(() => {
    timer = setInterval(() => {
      const s = st
      if (s) {
        samples.push({ t: Date.now(), v: s.sigma })
        while (samples.length && Date.now() - samples[0].t > WINDOW_MS) samples.shift()
      }
      draw()
    }, TICK_MS)
  })
  onDestroy(() => clearInterval(timer))

  const recent = $derived($divergences.filter((d) => Date.now() - d.ts < WINDOW_MS))
</script>

{#if st}
  <div class="rb">
    <span class="cap caps">
      ◇ DIVERGENCE
      <span class="now" class:hot={st.sigma >= (st.threshold || 5)}>{st.sigma.toFixed(1)}σ</span>
    </span>

    <div class="plot">
      <canvas bind:this={cv}></canvas>
      {#each recent as d (d.id)}
        <button class="pin" style={`right:${((Date.now() - d.ts) / (5 * 60 * 1000)) * 100}%`}
          onclick={() => { dreamConsole.set(d.id); sfx('click', { volume: 0.2 }) }}
          onmouseenter={() => (hover = d.id)} onmouseleave={() => (hover = null)}
          title={`${d.peak_sigma.toFixed(1)} sigma`}>
          <span class="pdot"></span>
          {#if hover === d.id}
            <span class="ptip caps">{d.peak_sigma.toFixed(1)}σ · {d.triage === 'subject' ? 'SUBJECT' : 'SCENE'}</span>
          {/if}
        </button>
      {/each}
    </div>

    <button class="mat" onclick={() => dreamConsole.set('live')} title="Open the Dreamstate console">
      <svg viewBox="0 0 30 30">
        <circle class="mtrack" cx="15" cy="15" r="13" />
        <circle class="mprog" cx="15" cy="15" r="13"
          stroke-dasharray={RING} stroke-dashoffset={RING * (1 - st.maturity)} />
      </svg>
      <span class="mlbl caps">{st.maturity >= 1 ? 'MATURE' : 'LEARNING'}</span>
    </button>
  </div>
{/if}

<style>
  .rb { position: absolute; left: 0; right: 0; bottom: 84px; height: 34px; z-index: var(--z-panel);
    display: flex; align-items: center; gap: 12px; padding: 0 62px;
    animation: slide 220ms var(--ease) both; pointer-events: none; }
  @keyframes slide { from { opacity: 0; transform: translateY(8px); } }
  .cap { display: flex; align-items: baseline; gap: 7px; font-size: 8px; color: var(--ink-ghost);
    letter-spacing: 0.16em; white-space: nowrap; }
  .now { font-size: 10px; color: var(--ink-dim); letter-spacing: 0.06em; }
  .now.hot { color: var(--scarlet); text-shadow: 0 0 6px var(--scarlet-glow); }
  .plot { position: relative; flex: 1; height: 100%; }
  .plot canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
  .pin { position: absolute; bottom: 0; top: 0; width: 10px; margin-right: -5px; background: none;
    border: none; padding: 0; cursor: crosshair; pointer-events: auto; }
  .pdot { position: absolute; left: 50%; top: -3px; width: 5px; height: 5px; margin-left: -2.5px;
    background: var(--scarlet); box-shadow: 0 0 6px var(--scarlet-glow); }
  .ptip { position: absolute; left: 50%; top: -18px; transform: translateX(-50%);
    background: rgba(4,7,10,0.85); padding: 2px 6px; font-size: 8px; color: var(--ink);
    letter-spacing: 0.1em; white-space: nowrap; }
  .mat { position: relative; width: 30px; height: 30px; background: none; border: none; padding: 0;
    cursor: crosshair; pointer-events: auto; flex: 0 0 auto; }
  .mat svg { width: 30px; height: 30px; transform: rotate(-90deg); display: block; }
  .mtrack { fill: none; stroke: var(--hairline); stroke-width: 3; }
  .mprog { fill: none; stroke: var(--jade); stroke-width: 3; stroke-linecap: round;
    transition: stroke-dashoffset 700ms cubic-bezier(0.16, 1, 0.3, 1); }
  .mlbl { position: absolute; left: 50%; top: 31px; transform: translateX(-50%); font-size: 7px;
    color: var(--ink-ghost); letter-spacing: 0.12em; white-space: nowrap; }
  .mat:hover .mlbl { color: var(--jade); }
</style>
