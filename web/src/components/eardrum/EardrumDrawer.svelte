<script lang="ts">
  // EARDRUM — the analysis drawer.
  //
  // Deliberately a drawer and not a full screen: the operator has to keep watching the feed
  // while reading the spectrum. The measured noise floor is drawn across the spectrum as a
  // dashed line, because a product that lets someone read a peak below its own floor is lying
  // to them.
  import { onDestroy, onMount } from 'svelte'
  import { activeCam, cameras, eardrumDrawer, listenPlacing, probeFrames, probes } from '../../lib/stores'
  import { api } from '../../lib/api'
  import { BAND_LABEL, columns, deleteProbe, loadProbes, spectrumFor } from '../../lib/eardrum'
  import { sfx } from '../../lib/audio'
  import { SIM } from '../../lib/sim'
  import type { Probe, ProbeSpectrum } from '../../lib/types'
  import Explain from '../Explain.svelte'
  import ScreenIntro from '../ScreenIntro.svelte'

  let { onclose }: { onclose: () => void } = $props()

  let selId = $state<number | null>(null)
  let spec = $state<ProbeSpectrum | null>(null)
  let sg = $state<HTMLCanvasElement>()
  let wave = $state<HTMLCanvasElement>()
  let modal = $state<{ hz: number; damping: number; shape: number[] }[] | null>(null)
  let modalOpen = $state(false)
  let busy = $state(false)
  let baseHeld = $state(0)
  let holdTimer: ReturnType<typeof setInterval> | undefined
  let raf = 0
  let lastT = 0
  let modalPhase = $state(0)

  const cam = $derived($cameras.find((c) => c.id === $activeCam))
  const list = $derived($probes)
  const sel = $derived(list.find((p) => p.id === selId) ?? list.find((p) => p.kind !== 'ref') ?? list[0] ?? null)
  const frames = $derived($probeFrames)
  const refProbe = $derived(list.find((p) => p.kind === 'ref') ?? null)
  const saturated = $derived(Object.values(frames).some((f) => f.saturated))
  // a baseline taken from two seconds of noise is worse than none, so the action waits
  const enoughSignal = $derived(!!spec && spec.psd.length > 8)

  $effect(() => {
    const p = sel
    if (!p) { spec = null; return }
    spectrumFor(p.id).then((s) => (spec = s))
  })

  function draw(now: number) {
    raf = requestAnimationFrame(draw)
    if (now - lastT < 100) return
    lastT = now
    modalPhase = now / 260
    const p = sel
    // spectrogram: time flows right to left, frequency up, monochrome with scarlet only at the
    // top decile so it obeys the same colour discipline as everything else
    const cv = sg
    if (cv && p) {
      const w = cv.clientWidth, h = cv.clientHeight
      if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h }
      const ctx = cv.getContext('2d')!
      ctx.clearRect(0, 0, w, h)
      const cols = columns(p.id)
      if (cols.length) {
        const cw = Math.max(1, w / 240)
        const img = ctx.createImageData(Math.max(1, Math.ceil(cw)), h)
        cols.forEach((col, i) => {
          const x = w - (cols.length - i) * cw
          if (x + cw < 0) return
          for (let y = 0; y < h; y++) {
            const bin = Math.floor((1 - y / h) * (col.length - 1))
            const v = col[bin] / 255
            let r: number, g: number, b: number
            if (v > 0.82) { r = 225; g = 6 + (1 - v) * 40; b = 0 }
            else { const t = v / 0.82; r = g = b = Math.round(5 + t * 225) }
            for (let px = 0; px < img.width; px++) {
              const o = (y * img.width + px) * 4
              img.data[o] = r; img.data[o + 1] = g; img.data[o + 2] = b; img.data[o + 3] = 255
            }
          }
          ctx.putImageData(img, Math.round(x), 0)
        })
      }
    }
    // the live scope above it
    const wv = wave
    if (wv && p) {
      const w = wv.clientWidth, h = wv.clientHeight
      if (wv.width !== w || wv.height !== h) { wv.width = w; wv.height = h }
      const ctx = wv.getContext('2d')!
      ctx.clearRect(0, 0, w, h)
      const f = frames[String(p.id)]
      if (f?.wave?.length) {
        const peak = Math.max(...f.wave.map(Math.abs)) || 1
        ctx.beginPath()
        f.wave.forEach((v, i) => {
          const x = (i / (f.wave.length - 1)) * w
          const y = h / 2 - (v / peak) * (h / 2 - 2)
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
        })
        ctx.strokeStyle = '#38d0e3'
        ctx.lineWidth = 1
        ctx.stroke()
      }
    }
  }

  async function setBaseline() {
    const p = sel
    if (!p || busy) return
    busy = true
    sfx('sonar')
    if (!SIM) await api.probeBaseline(p.id).catch(() => undefined)
    probes.update((l) => l.map((x) => (x.id === p.id ? { ...x, baseline: true } : x)))
    spec = await spectrumFor(p.id)
    busy = false
  }
  function startHold() {
    if (!sel?.baseline) { setBaseline(); return }
    baseHeld = 0
    holdTimer = setInterval(() => {
      baseHeld += 60
      if (baseHeld >= 800) { clearInterval(holdTimer); baseHeld = 0; setBaseline() }
    }, 60)
  }
  function endHold() { clearInterval(holdTimer); baseHeld = 0 }

  async function runModal() {
    modalOpen = !modalOpen
    if (!modalOpen || !$activeCam) return
    sfx('sonar')
    if (SIM) {
      modal = [{ hz: 3.2, damping: 0.018, shape: [1, 0.62, -0.41, -0.9] },
               { hz: 8.7, damping: 0.026, shape: [1, -0.5, 0.3, 0.8] }]
      return
    }
    try { modal = (await api.eardrumModal($activeCam)).modes ?? [] } catch { modal = [] }
  }

  function play() {
    const p = sel
    if (!p) return
    sfx('click')
    if (SIM) return
    const a = new Audio(api.probeWave(p.id, 8))
    a.play().catch(() => undefined)
  }

  function step(dir: number) {
    if (!list.length) return
    const i = Math.max(0, list.findIndex((p) => p.id === sel?.id))
    selId = list[(i + dir + list.length) % list.length].id
    sfx('click', { volume: 0.15 })
  }

  function onkey(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.stopPropagation(); onclose(); return }
    if (e.key === '[') { e.preventDefault(); step(-1) }
    else if (e.key === ']') { e.preventDefault(); step(1) }
    else if (e.key === 'p' || e.key === 'P') { e.preventDefault(); play() }
  }

  onMount(() => {
    loadProbes()
    raf = requestAnimationFrame(draw)
    window.addEventListener('keydown', onkey, true)
  })
  onDestroy(() => { cancelAnimationFrame(raf); window.removeEventListener('keydown', onkey, true) })

  const tone = (p: Probe) => {
    const f = frames[String(p.id)]
    if (!f) return ''
    return f.db >= 12 ? 'hot' : f.db >= 6 ? 'warn' : ''
  }
  // spectrum path in the panel's own coordinate space
  const path = (vals: number[], floor: number) => {
    if (!vals.length) return ''
    const top = Math.max(...vals), lo = Math.min(floor - 6, ...vals)
    const rng = Math.max(1, top - lo)
    return vals.map((v, i) => `${(i / (vals.length - 1)) * 100},${100 - ((v - lo) / rng) * 100}`).join(' ')
  }
  const floorY = (s: ProbeSpectrum) => {
    const top = Math.max(...s.psd), lo = Math.min(s.floor - 6, ...s.psd)
    return 100 - ((s.floor - lo) / Math.max(1, top - lo)) * 100
  }
  const peakX = (s: ProbeSpectrum, hz: number) => (hz / Math.max(1e-6, s.nyquist)) * 100
</script>

<div class="ed panel">
  <header class="top caps">
    <span class="eyebrow">◈ EARDRUM</span>
    <span class="cnt">◉ {cam?.name ?? 'CAM —'}</span>
    <span class="cnt">{list.length} PROBE{list.length === 1 ? '' : 'S'}</span>
    {#if !refProbe && list.length}
      <span class="warnchip caps">
        NO <Explain term="reference probe" plain /> YET · EVERY READING STILL INCLUDES THE CAMERA'S OWN SHAKE
      </span>
    {/if}
    {#if saturated}
      <span class="warnchip caps hot">CAMERA IS MOVING · MEASUREMENT SUSPENDED</span>
    {/if}
    <span class="spacer"></span>
    <!-- "Why would I set a baseline?" was the first thing an operator asked, so the button says
         it. It is also disabled with a stated reason rather than silently. -->
    <button class="tb caps" onmousedown={startHold} onmouseup={endHold} onmouseleave={endHold}
      disabled={busy || !sel || !enoughSignal}
      title={!sel ? 'Select a probe first'
        : !enoughSignal ? 'Listen for a few more seconds first'
        : 'Freeze how this surface behaves TODAY, while it is healthy. Every later reading is '
          + 'compared against it, so a machine that slowly drifts out of true shows up as a peak '
          + 'that moved.'}>
      <span class="hfill" style={`width:${(baseHeld / 800) * 100}%`}></span>
      <span class="ht">⊕ {sel?.baseline ? 'REPLACE BASELINE' : 'SET BASELINE'}</span>
    </button>
    <span class="why caps">
      {#if !sel}SELECT A PROBE
      {:else if !enoughSignal}LISTENING_
      {:else if sel.baseline}COMPARING AGAINST THE HEALTHY READING YOU SAVED
      {:else}SAVE HOW IT SOUNDS WHILE HEALTHY, SO DRIFT SHOWS UP LATER
      {/if}
    </span>
    <button class="tb caps" class:on={modalOpen} onclick={runModal}
      disabled={list.filter((p) => p.kind !== 'ref').length < 3}
      title="Needs three probes on the same structure">◈ MODAL ANALYSIS</button>
    <button class="tb caps" onclick={() => listenPlacing.set(true)}>+ ADD PROBE</button>
    <button class="x caps" onclick={onclose}>✕</button>
  </header>

  <ScreenIntro
    what="A camera with no microphone, telling you a machine is running rough."
    hint="It measures how much a surface trembles, from movements far smaller than one pixel."
    look="Pick a probe below. The chart on the right is what that surface is doing." />

  {#if !list.length}
    <div class="empty caps">
      <div class="rings"><span></span><span></span><span></span></div>
      <div>NOTHING IS LISTENING</div>
      <div class="sub">PLACE A PROBE ON A TEXTURED, RIGID SURFACE IN THE FEED.</div>
      <button class="go caps" onclick={() => listenPlacing.set(true)}>◈ PLACE PROBES</button>
    </div>
  {:else}
    <div class="body">
      <aside class="strip">
        {#each list as p (p.id)}
          {@const f = frames[String(p.id)]}
          <button class="prow {tone(p)}" class:on={sel?.id === p.id} class:ref={p.kind === 'ref'}
            onclick={() => (selId = p.id)}>
            <span class="pmid">
              <span class="pn">{p.kind === 'ref' ? '⚓ ' : ''}{p.name}</span>
              <span class="psub caps">{f ? `${f.db >= 0 ? '▲' : '▼'} ${Math.abs(f.db).toFixed(1)} dB` : '—'}</span>
            </span>
            <span class="spark">
              {#each columns(p.id).slice(-24) as col, i}
                <span class="sb" style={`height:${Math.min(100, (Math.max(...col) / 255) * 100)}%`}></span>
              {/each}
            </span>
          </button>
        {/each}
        <button class="prow add caps" onclick={() => listenPlacing.set(true)}>+ ADD PROBE</button>
      </aside>

      <main class="centre">
        {#if modalOpen}
          <div class="modal">
            {#if modal === null}
              <div class="mid caps"><span class="pulse">DECOMPOSING_</span></div>
            {:else if !modal.length}
              <div class="mid caps">
                <Explain term="modal analysis" plain /> NEEDS THREE PROBES ON THE SAME STRUCTURE
              </div>
            {:else}
              {@const m = modal[0]}
              {@const amp = Math.sin(modalPhase) * 14}
              {@const gap = 80 / Math.max(1, m.shape.length - 1)}
              <svg class="shape" viewBox="0 0 100 60" preserveAspectRatio="none">
                <polyline class="rest" points={m.shape.map((_, i) => `${10 + i * gap},30`).join(' ')} />
                <polyline class="mode" points={m.shape.map((s, i) => `${10 + i * gap},${30 + s * amp}`).join(' ')} />
                {#each m.shape as s, i}
                  <circle class="node" cx={10 + i * gap} cy={30 + s * amp} r="1.6" />
                {/each}
              </svg>
              <div class="mlist caps">
                {#each modal as m, i}
                  <span class="mrow">MODE {i + 1} · {m.hz.toFixed(2)} Hz · ζ {(m.damping * 100).toFixed(1)}%</span>
                {/each}
                <span class="mnote">SHOWN AT EXAGGERATED AMPLITUDE. A FALLING NATURAL FREQUENCY IS THE CANONICAL STIFFNESS-LOSS SIGNATURE.</span>
              </div>
            {/if}
          </div>
        {:else}
          <canvas bind:this={wave} class="scope"></canvas>
          <canvas bind:this={sg} class="sgram"></canvas>
          <div class="sgax caps">
            <span>0 Hz</span><span class="mid2">{spec ? (spec.nyquist / 2).toFixed(1) : '—'} Hz</span>
            <span>{spec ? spec.nyquist.toFixed(1) : '—'} Hz</span>
          </div>
        {/if}
      </main>

      <aside class="analysis">
        <section class="card">
          <div class="ck caps"><Explain term="spectrum" plain /></div>
          <div class="cnote">A spike means something is repeating at a steady rate. Left is slow, right is fast.</div>
          {#if spec && spec.psd.length}
            <svg class="spec" viewBox="0 0 100 100" preserveAspectRatio="none">
              {#if spec.baseline}
                <polyline class="base" points={path(spec.baseline, spec.floor)} />
              {/if}
              <polyline class="live" points={path(spec.psd, spec.floor)} />
              <line class="floor" x1="0" x2="100" y1={floorY(spec)} y2={floorY(spec)} />
              {#each spec.peaks as pk}
                <line class="pk" class:new={pk.is_new} x1={peakX(spec, pk.hz)} x2={peakX(spec, pk.hz)} y1="0" y2="100" />
              {/each}
            </svg>
            <div class="floorlbl caps">
              <Explain term="noise floor" plain /> · ANYTHING UNDER THE DASHED LINE IS NOT REAL
            </div>
            <div class="peaks">
              {#each spec.peaks.slice(0, 4) as pk}
                <div class="prow2 caps">
                  <span class="phz">{pk.hz.toFixed(2)} Hz</span>
                  <span class="pdb" title="How far this spike stands out above the surrounding noise">{pk.prominence.toFixed(0)} dB</span>
                  {#if pk.is_new}<span class="tagn">NEW</span>{/if}
                  {#if pk.shift}<span class="tags">Δ {pk.shift > 0 ? '+' : ''}{pk.shift.toFixed(2)} Hz</span>{/if}
                  {#if pk.rise}<span class="tagr">+{pk.rise.toFixed(1)} dB</span>{/if}
                </div>
              {/each}
              {#if !spec.peaks.length}<div class="none caps">NOTHING ABOVE THE NOISE FLOOR</div>{/if}
            </div>
          {:else}
            <div class="none caps"><span class="pulse">LISTENING_</span></div>
          {/if}
        </section>

        {#if spec?.interpretation}
          <section class="card">
            <div class="ck caps">WHAT THIS PATTERN USUALLY MEANS</div>
            <div class="cnote">Reading the spikes the way a maintenance engineer would.</div>
            <div class="f0 caps">RUNNING AT {spec.interpretation.rpm} RPM · {spec.interpretation.f0.toFixed(2)} Hz</div>
            <div class="clbl caps">STRENGTH AT EACH <Explain term="harmonic" plain /></div>
            <div class="harms">
              {#each spec.interpretation.harmonics as h}
                <div class="hrow caps">
                  <span class="ho">{h.order}x</span>
                  <span class="hbar"><span class="hfill2" style={`width:${Math.min(100, (h.db + 60) * 1.6)}%`}></span></span>
                </div>
              {/each}
            </div>
            <div class="verdict caps">{spec.interpretation.verdict}</div>
            <div class="why caps">WHY: {spec.interpretation.why}</div>
            <div class="conf caps">
              CONFIDENCE
              {#each [1, 2, 3] as p}<span class="pip" class:on={spec.interpretation.confidence >= p}></span>{/each}
            </div>
          </section>
        {/if}

        <section class="card">
          <div class="ck caps">WHAT YOU CAN HEAR <Explain term="structural band" bare /></div>
          <div class="band caps">{BAND_LABEL[spec?.band ?? 'structural']}</div>
          <button class="play caps" onclick={play}>▶ PLAY 8s · +3 OCT<span class="k">P</span></button>
          <div class="disc caps">
            RECONSTRUCTED FROM SURFACE MOTION · BAND-LIMITED IN CODE TO {spec ? spec.nyquist.toFixed(0) : '15'} Hz ·
            NOT AN AUDIO RECORDING AND CANNOT CARRY SPEECH
          </div>
        </section>
      </aside>
    </div>
  {/if}
</div>

<style>
  /* Opaque, not the shared translucent .panel carbon. Over a live feed and two rails, 86% let the
     nav bar and the fog card read straight through the charts — the drawer looked broken and
     nothing in it was legible. A drawer that covers content has to actually cover it.
     52vh because at 38vh the analysis column was cut off at the bottom of the screen. */
  .ed { position: absolute; left: 0; right: 0; bottom: 0; height: 52vh; min-height: 420px;
    max-height: 660px; background: #05070a;
    z-index: var(--z-panel); display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 -18px 44px rgba(0,0,0,0.75);
    border-top: 1px solid var(--hairline); animation: up 260ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes up { from { transform: translateY(100%); } }
  .top { display: flex; align-items: center; gap: 10px; padding: 8px 16px;
    border-bottom: 1px solid var(--hairline); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .eyebrow { color: var(--scarlet); } .cnt { color: var(--ink-dim); font-size: 11px; } .spacer { flex: 1; }
  .warnchip { padding: 3px 7px; border: 1px solid color-mix(in srgb, var(--amber) 45%, transparent);
    color: var(--amber); font-size: 10px; letter-spacing: 0.1em; }
  .warnchip.hot { border-color: color-mix(in srgb, var(--scarlet) 55%, transparent); color: var(--scarlet); }
  .tb { position: relative; overflow: hidden; padding: 5px 10px; border: 1px solid var(--ink-dim);
    background: none; color: var(--ink-dim); font-size: 11px; letter-spacing: 0.12em; cursor: crosshair; }
  .tb:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
  .tb.on { border-color: var(--cyan); color: var(--cyan); background: rgba(56,208,227,0.1); }
  .tb:disabled { opacity: 0.35; }
  .why { font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.1em; max-width: 190px;
    line-height: 1.5; }
  .hfill { position: absolute; left: 0; top: 0; bottom: 0; background: rgba(56,208,227,0.3); }
  .ht { position: relative; }
  .x { padding: 5px 9px; border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim);
    font-size: 11px; cursor: crosshair; }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }

  .empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 10px; color: var(--ink-dim); font-size: 10px; letter-spacing: 0.16em; }
  .empty .sub { font-size: 11px; color: var(--ink-ghost); }
  .rings { position: relative; width: 40px; height: 40px; }
  .rings span { position: absolute; inset: 0; border: 1px solid var(--cyan); border-radius: 50%;
    opacity: 0; animation: ring 2.4s ease-out infinite; }
  .rings span:nth-child(2) { animation-delay: 0.8s; } .rings span:nth-child(3) { animation-delay: 1.6s; }
  @keyframes ring { 0% { transform: scale(0.2); opacity: 0.8; } 100% { transform: scale(1); opacity: 0; } }
  .go { padding: 8px 16px; border: 1px solid var(--cyan); background: none; color: var(--cyan);
    font-size: 11px; letter-spacing: 0.16em; cursor: crosshair; }
  .go:hover { background: var(--cyan); color: #04070a; }

  .body { flex: 1; min-height: 0; display: grid; grid-template-columns: 200px 1fr 300px; }
  .strip { overflow-y: auto; border-right: 1px solid var(--hairline); padding: 6px; }
  .prow { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; padding: 6px 7px;
    margin-bottom: 3px; background: none; border: 1px solid transparent; border-left: 2px solid transparent;
    cursor: crosshair; }
  .prow:hover { background: rgba(56,208,227,0.05); }
  .prow.on { background: rgba(56,208,227,0.09); border-color: var(--hairline); border-left-color: var(--cyan); }
  .prow.ref { opacity: 0.7; border-bottom: 1px solid var(--hairline); }
  .pmid { display: flex; flex-direction: column; gap: 1px; flex: 1; min-width: 0; }
  .pn { font-size: 11px; color: var(--ink-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .prow.on .pn { color: var(--ink); }
  .psub { font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.1em; }
  .prow.warn .psub { color: var(--amber); } .prow.hot .psub { color: var(--scarlet); }
  .spark { display: flex; align-items: flex-end; gap: 1px; height: 16px; width: 50px; flex: 0 0 auto; }
  .sb { flex: 1; background: var(--ink-ghost); min-height: 1px; }
  .prow.hot .sb { background: var(--scarlet); }
  .prow.add { justify-content: center; color: var(--ink-ghost); font-size: 11px;
    letter-spacing: 0.14em; border: 1px dashed var(--hairline); }
  .prow.add:hover { color: var(--cyan); border-color: var(--cyan); }

  .centre { position: relative; display: flex; flex-direction: column; min-width: 0; }
  .scope { height: 44px; width: 100%; border-bottom: 1px solid var(--hairline); flex: 0 0 auto; }
  .sgram { flex: 1; width: 100%; min-height: 0; }
  .sgax { display: flex; justify-content: space-between; padding: 3px 8px; font-size: 10px;
    color: var(--ink-ghost); letter-spacing: 0.12em; border-top: 1px solid var(--hairline); }
  .sgax .mid2 { color: var(--ink-ghost); }

  .modal { flex: 1; display: flex; flex-direction: column; min-height: 0; }
  .modal .mid { flex: 1; display: flex; align-items: center; justify-content: center;
    color: var(--ink-dim); font-size: 11px; letter-spacing: 0.14em; }
  .shape { flex: 1; min-height: 0; }
  .rest { fill: none; stroke: var(--ink-ghost); stroke-width: 0.4; stroke-dasharray: 2 2; }
  .mode { fill: none; stroke: var(--cyan); stroke-width: 0.8; }
  .node { fill: var(--cyan); }
  .mlist { display: flex; flex-direction: column; gap: 3px; padding: 6px 10px;
    border-top: 1px solid var(--hairline); }
  .mrow { font-size: 11px; color: var(--ink-dim); letter-spacing: 0.12em; }
  .mnote { font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.1em; line-height: 1.5; }

  .analysis { overflow-y: auto; border-left: 1px solid var(--hairline); padding: 8px;
    display: flex; flex-direction: column; gap: 8px; }
  .card { border: 1px solid var(--hairline); padding: 8px 9px; display: flex; flex-direction: column; gap: 6px; }
  .ck { font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.18em; }
  /* The card title names the panel; this line says what the operator is actually looking at.
     Lower case and un-tracked on purpose, so it reads as a sentence rather than more chrome. */
  .cnote { font-size: 11px; color: var(--ink-ghost); line-height: 1.5; letter-spacing: 0;
    text-transform: none; margin: 2px 0 4px; }
  .clbl { font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.18em; margin-top: 6px; }
  .spec { width: 100%; height: 70px; }
  .live { fill: none; stroke: var(--cyan); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .base { fill: none; stroke: var(--jade); stroke-width: 1; stroke-dasharray: 3 2;
    vector-effect: non-scaling-stroke; opacity: 0.75; }
  .floor { stroke: var(--ink-ghost); stroke-width: 1; stroke-dasharray: 4 3; vector-effect: non-scaling-stroke; }
  .pk { stroke: var(--ink-dim); stroke-width: 1; opacity: 0.35; vector-effect: non-scaling-stroke; }
  .pk.new { stroke: var(--scarlet); opacity: 0.8; }
  .floorlbl { font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.1em; }
  .peaks { display: flex; flex-direction: column; gap: 2px; }
  .prow2 { display: flex; align-items: baseline; gap: 6px; font-size: 11px; }
  .phz { color: var(--ink); } .pdb { color: var(--ink-ghost); }
  .tagn { color: var(--scarlet); } .tags { color: var(--amber); } .tagr { color: var(--amber); }
  .none { font-size: 11px; color: var(--ink-ghost); letter-spacing: 0.12em; }
  .pulse { animation: pl 1.2s ease-in-out infinite; } @keyframes pl { 50% { opacity: 0.4; } }

  .f0 { font-size: 10px; color: var(--ink); letter-spacing: 0.1em; }
  .harms { display: flex; flex-direction: column; gap: 3px; }
  .hrow { display: grid; grid-template-columns: 20px 1fr; gap: 6px; align-items: center; font-size: 10px; }
  .ho { color: var(--ink-ghost); }
  .hbar { position: relative; height: 4px; background: var(--hairline); }
  .hfill2 { position: absolute; inset: 0 auto 0 0; background: var(--cyan); }
  .verdict { font-size: 11px; color: var(--amber); letter-spacing: 0.1em; line-height: 1.4; }
  .why { font-size: 10px; color: var(--ink-dim); letter-spacing: 0.1em; }
  .conf { display: flex; align-items: center; gap: 4px; font-size: 10px; color: var(--ink-ghost); }
  .pip { width: 10px; height: 3px; background: var(--hairline); }
  .pip.on { background: var(--amber); }

  .band { font-size: 11px; color: var(--cyan); letter-spacing: 0.1em; }
  .play { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 7px 0;
    border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim); font-size: 11px;
    letter-spacing: 0.14em; cursor: crosshair; }
  .play:hover { border-color: var(--cyan); color: var(--cyan); }
  .play .k { border: 1px solid var(--ink-ghost); padding: 0 3px; font-size: 10px; }
  .disc { font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.1em; line-height: 1.6; }

  @media (max-width: 1000px) { .body { grid-template-columns: 150px 1fr 240px; } }
</style>
