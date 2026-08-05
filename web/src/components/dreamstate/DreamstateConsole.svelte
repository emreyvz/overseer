<script lang="ts">
  // DREAMSTATE — the console.
  //
  // The whole pitch, made literal: on the left, what this camera remembers being there; on the
  // right, what is there now; a divider you drag between them, and the divergence outlined on
  // both so the shape is present on one side and absent on the other. An operator understands it
  // in under a second and without reading a word.
  //
  // The DREAM side is the running background plate. It is not a hallucination and it is not a
  // render: it is the camera's own scene with everything that moves averaged out, which is the
  // most honest possible picture of "what I expect to be here".
  import { onDestroy, onMount } from 'svelte'
  import { activeCam, cameras, divergences, dreamStatus } from '../../lib/stores'
  import { api } from '../../lib/api'
  import { loadDream, plateUrl, seedDreamSim, wouldFire } from '../../lib/dreamstate'
  import { sfx } from '../../lib/audio'
  import { SIM } from '../../lib/sim'
  import type { DreamPulse, Divergence } from '../../lib/types'
  import LiveThumb from '../LiveThumb.svelte'

  let { open, onclose }: { open: number | 'live'; onclose: () => void } = $props()

  const RC = 2 * Math.PI * 26

  let wipe = $state(50)
  let dragging = $state(false)
  let stage = $state<HTMLElement>()
  let pulse = $state<DreamPulse[]>([])
  let loading = $state(true)
  let sigma = $state(5)
  let busy = $state(false)
  let hoverCell = $state<number | null>(null)
  let plateOk = $state(true)

  const st = $derived($dreamStatus)
  const cam = $derived($cameras.find((c) => c.id === $activeCam))
  const list = $derived($divergences)
  const unjudged = $derived(list.filter((d) => !d.verdict))
  const flagged = $derived(list.filter((d) => d.verdict === 'flagged'))
  const expected = $derived(list.filter((d) => d.verdict === 'expected'))
  const flat = $derived([...unjudged, ...flagged, ...expected])
  let selId = $state<number | null>(null)
  const sel = $derived(flat.find((d) => d.id === selId) ?? (open === 'live' ? flat[0] ?? null : flat.find((d) => d.id === open) ?? flat[0] ?? null))

  const when = (ts: number) => new Date(ts).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const dur = (d: Divergence) => Math.max(1, Math.round(d.area_sigma_s / Math.max(1, d.peak_sigma * d.cells.length)))

  function startDrag(e: PointerEvent) {
    e.preventDefault(); dragging = true
    window.addEventListener('pointermove', onDrag)
    window.addEventListener('pointerup', endDrag)
  }
  function onDrag(e: PointerEvent) {
    if (!dragging || !stage) return
    const r = stage.getBoundingClientRect()
    wipe = Math.max(2, Math.min(98, ((e.clientX - r.left) / r.width) * 100))
  }
  function endDrag() {
    dragging = false
    window.removeEventListener('pointermove', onDrag)
    window.removeEventListener('pointerup', endDrag)
  }

  async function verdict(d: Divergence | null, v: 'expected' | 'flagged') {
    if (!d || busy) return
    busy = true
    sfx(v === 'flagged' ? 'sonar' : 'click')
    divergences.update((l) => l.map((x) => (x.id === d.id ? { ...x, verdict: v } : x)))
    if (!SIM) await api.dreamVerdict(d.id, v).catch(() => undefined)
    busy = false
  }

  async function muteCells(d: Divergence | null) {
    if (!d || !$activeCam || busy) return
    busy = true
    sfx('click')
    if (!SIM) await api.dreamMute($activeCam, d.cells).catch(() => undefined)
    busy = false
  }

  async function applySigma() {
    if (!$activeCam || SIM) return
    await api.dreamThreshold($activeCam, sigma).catch(() => undefined)
  }

  async function reset(mode: 'reregister' | 'relearn') {
    if (!$activeCam || busy) return
    busy = true
    const r = SIM ? { ok: true } : await api.dreamReset($activeCam, mode).catch(() => ({ ok: false }))
    busy = false
    sfx(r.ok ? 'click' : 'glitch')
  }

  function step(dir: number) {
    if (!flat.length) return
    const i = Math.max(0, flat.findIndex((d) => d.id === sel?.id))
    selId = flat[(i + dir + flat.length) % flat.length].id
    sfx('click', { volume: 0.15 })
  }

  const fires = $derived(pulse.length ? wouldFire(pulse, sigma) : 0)
  const firesNow = $derived(pulse.length && st ? wouldFire(pulse, st.threshold || 5) : 0)

  function onkey(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.stopPropagation(); onclose(); return }
    if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); step(1) }
    else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); step(-1) }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); wipe = Math.max(2, wipe - 2) }
    else if (e.key === 'ArrowRight') { e.preventDefault(); wipe = Math.min(98, wipe + 2) }
    else if (e.key === 'Enter') { e.preventDefault(); verdict(sel, 'expected') }
    else if (e.key === 'f' || e.key === 'F') { e.preventDefault(); verdict(sel, 'flagged') }
  }

  onMount(async () => {
    sfx('sonar')
    seedDreamSim()
    const r = await loadDream(24)
    pulse = r.pulse
    sigma = $dreamStatus?.threshold ?? 5
    loading = false
    window.addEventListener('keydown', onkey, true)
  })
  onDestroy(() => { window.removeEventListener('keydown', onkey, true); endDrag() })
</script>

<div class="dc" role="dialog" aria-label="Dreamstate">
  <header class="top caps">
    <span class="eyebrow">◈ DREAMSTATE</span>
    <span class="cnt">◉ {cam?.name ?? 'CAM —'}</span>
    <span class="cnt">{list.length} DIVERGENCE{list.length === 1 ? '' : 'S'}</span>
    <span class="spacer"></span>
    {#if st}
      <div class="gauge" title="How well this hour has been learned">
        <svg viewBox="0 0 60 60">
          <circle class="track" cx="30" cy="30" r="26" />
          <circle class="prog" cx="30" cy="30" r="26"
            stroke-dasharray={RC} stroke-dashoffset={RC * (1 - st.maturity)} />
        </svg>
        <span class="gval">{Math.round(st.maturity * 100)}%</span>
      </div>
      <span class="glabel caps">MATURITY</span>
      <span class="chip caps">TIER {st.tier}</span>
    {/if}
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  {#if loading}
    <div class="empty caps"><span class="pulse">READING THE RECORD_</span></div>
  {:else if st?.stale}
    <div class="empty caps stale">
      <div class="warnring">◇</div>
      <div class="big display">SCENE CHANGED</div>
      <div class="sub">
        THE CAMERA MOVED. THE LEARNED MODEL NO LONGER MATCHES THIS VIEW, SO IT IS NOT REPORTING.
      </div>
      <div class="racts">
        <button class="go caps" onclick={() => reset('reregister')} disabled={busy}>↺ RE-REGISTER</button>
        <button class="alt caps" onclick={() => reset('relearn')} disabled={busy}>⌫ RELEARN FROM SCRATCH</button>
      </div>
    </div>
  {:else}
    <div class="body">
      <aside class="queue">
        {#if unjudged.length}
          <div class="qgrp caps"><span class="qd u"></span>UNJUDGED<span class="qgn">{unjudged.length}</span></div>
          {#each unjudged as d (d.id)}
            <button class="qrow" class:on={sel?.id === d.id} onclick={() => (selId = d.id)}>
              <span class="pdot" style={`width:${5 + Math.min(6, d.peak_sigma / 2)}px; height:${5 + Math.min(6, d.peak_sigma / 2)}px`}></span>
              <span class="qmid">
                <span class="qttl">{when(d.ts)}</span>
                <span class="qsub caps">{d.triage === 'subject' ? 'SUBJECT' : 'SCENE'} · {dur(d)}s</span>
              </span>
              <span class="qsig caps">{d.peak_sigma.toFixed(1)}σ</span>
            </button>
          {/each}
        {/if}
        {#if flagged.length}
          <div class="qgrp caps"><span class="qd f"></span>FLAGGED<span class="qgn">{flagged.length}</span></div>
          {#each flagged as d (d.id)}
            <button class="qrow off" class:on={sel?.id === d.id} onclick={() => (selId = d.id)}>
              <span class="pdot"></span>
              <span class="qmid"><span class="qttl">{when(d.ts)}</span></span>
              <span class="qsig caps">{d.peak_sigma.toFixed(1)}σ</span>
            </button>
          {/each}
        {/if}
        {#if expected.length}
          <div class="qgrp caps"><span class="qd e"></span>MARKED EXPECTED<span class="qgn">{expected.length}</span></div>
          {#each expected as d (d.id)}
            <button class="qrow off" class:on={sel?.id === d.id} onclick={() => (selId = d.id)}>
              <span class="pdot ok"></span>
              <span class="qmid"><span class="qttl">{when(d.ts)}</span></span>
            </button>
          {/each}
        {/if}
        {#if !flat.length}
          <div class="lempty caps">
            <div class="okring">✓</div>
            NO DIVERGENCE TODAY<br />THE SCENE IS BEHAVING
          </div>
        {/if}
      </aside>

      <main class="stage">
        <!-- THE WIPE: memory on the left, the world on the right -->
        <div class="wipewrap" bind:this={stage}>
          <div class="side world">
            {#if $activeCam}<LiveThumb id={$activeCam} fps={8} />{/if}
          </div>
          <div class="side dream" style={`clip-path: inset(0 ${100 - wipe}% 0 0)`}>
            {#if SIM || !plateOk}
              <div class="noplate caps">
                <span>NO PLATE YET</span>
                <span class="np2">THE BACKGROUND MODEL WARMS UP OVER THE FIRST MINUTE</span>
              </div>
            {:else if $activeCam}
              <img class="plate" src={plateUrl($activeCam)} alt="" onerror={() => (plateOk = false)} />
            {/if}
          </div>

          {#if sel}
            <svg class="mark" viewBox="0 0 100 100" preserveAspectRatio="none">
              <polygon class="blob" points={sel.blob.map((p) => `${p[0] * 100},${p[1] * 100}`).join(' ')} />
            </svg>
          {/if}

          <button class="divider" style={`left:${wipe}%`} onpointerdown={startDrag}
            role="slider" aria-label="Dream to world wipe" aria-valuemin="0" aria-valuemax="100"
            aria-valuenow={Math.round(wipe)}
            onkeydown={(e) => { if (e.key === 'ArrowLeft') wipe = Math.max(2, wipe - 2); if (e.key === 'ArrowRight') wipe = Math.min(98, wipe + 2) }}>
            <span class="dhandle"></span>
            <span class="dlabel caps">DREAM ◂ | ▸ WORLD</span>
          </button>

          {#if sel}
            <span class="stamp caps">PREDICTED AT {when(sel.ts)} · Δ {sel.peak_sigma.toFixed(1)}σ</span>
            <span class="triage caps t-{sel.triage}">
              {sel.triage === 'subject' ? '◈ SUBJECT BEHAVIOUR' : '◈ SCENE CHANGE'}
            </span>
          {/if}
        </div>

        <div class="lower">
          <!-- 24h timeline -->
          <section class="pnl">
            <div class="pk caps">LAST 24 HOURS</div>
            <div class="tl">
              {#each pulse.filter((_, i) => i % 3 === 0) as p (p.t)}
                <span class="tick" class:hot={p.peak >= (st?.threshold ?? 5)}
                  style={`height:${Math.min(100, (p.peak / 10) * 100)}%`}></span>
              {/each}
              <span class="thr" style={`bottom:${Math.min(100, ((st?.threshold ?? 5) / 10) * 100)}%`}></span>
            </div>
            <div class="tlx caps"><span>-24H</span><span>NOW</span></div>
          </section>

          <!-- cell inspector: where "normal" finally becomes visible -->
          <section class="pnl">
            <div class="pk caps">EXPECTATION FIELD</div>
            {#if st}
              <div class="grid" style={`grid-template-columns: repeat(${st.grid[0]}, 1fr)`}>
                {#each st.cells as z, i}
                  <span class="cell" class:hot={z >= (st.threshold || 5)} class:mute={st.muted.includes(i)}
                    style={`opacity:${Math.min(1, 0.06 + z / 8)}`}
                    role="presentation"
                    onmouseenter={() => (hoverCell = i)} onmouseleave={() => (hoverCell = null)}></span>
                {/each}
              </div>
              <div class="cinfo caps">
                {#if hoverCell !== null}
                  CELL {hoverCell} · {st.cells[hoverCell].toFixed(2)}σ
                  {#if st.muted.includes(hoverCell)}· MUTED{/if}
                {:else}
                  {st.buckets[st.bucket].name} · {st.buckets[st.bucket].n} OBSERVATIONS
                  {#if st.buckets[st.bucket].maturity < 1}· UNLEARNED, NOT FIRING{/if}
                {/if}
              </div>
            {/if}
          </section>

          <!-- verdict + the sensitivity slider that measures instead of guessing -->
          <section class="pnl">
            <div class="pk caps">VERDICT</div>
            <div class="vacts">
              <button class="ok caps" onclick={() => verdict(sel, 'expected')} disabled={!sel || busy}>✓ EXPECTED<span class="k">⏎</span></button>
              <button class="flag caps" onclick={() => verdict(sel, 'flagged')} disabled={!sel || busy}>⚑ FLAG<span class="k">F</span></button>
            </div>
            <button class="mini caps" onclick={() => muteCells(sel)} disabled={!sel || busy}>◱ MUTE THESE CELLS</button>

            <div class="pk caps sens">SENSITIVITY</div>
            <input class="slider" type="range" min="3" max="8" step="0.1" bind:value={sigma}
              oninput={() => { /* live preview only */ }} onchange={applySigma} aria-label="sigma threshold" />
            <div class="scap caps">
              AT {Number(sigma).toFixed(1)}σ, TODAY WOULD HAVE FIRED {fires} TIME{fires === 1 ? '' : 'S'}
              <span class="dim">(NOW {firesNow})</span>
            </div>
            <div class="note caps">
              DREAMSTATE REPORTS DIVERGENCE, NOT THREAT. IT CANNOT NAME WHAT HAPPENED.
            </div>
          </section>
        </div>
      </main>
    </div>
  {/if}
</div>

<style>
  .dc { position: fixed; inset: 0; z-index: var(--z-boot);
    background: radial-gradient(120% 80% at 50% 0%, #0a1016 0%, #05070a 72%);
    color: var(--ink); display: flex; flex-direction: column; overflow: hidden;
    animation: din 300ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes din { from { opacity: 0; } }
  .top { display: flex; align-items: center; gap: 12px; padding: 11px 22px;
    border-bottom: 1px solid var(--hairline); font-size: var(--fs-label);
    letter-spacing: var(--tracking); background: #04070a; z-index: 2; }
  .eyebrow { color: var(--scarlet); } .cnt { color: var(--ink-dim); font-size: 9px; } .spacer { flex: 1; }
  .gauge { position: relative; width: 40px; height: 40px; }
  .gauge svg { width: 40px; height: 40px; transform: rotate(-90deg); }
  .gauge .track { fill: none; stroke: var(--hairline); stroke-width: 4; }
  .gauge .prog { fill: none; stroke: var(--jade); stroke-width: 4; stroke-linecap: round;
    transition: stroke-dashoffset 700ms cubic-bezier(0.16, 1, 0.3, 1); }
  .gval { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    font-size: 9px; color: var(--jade); letter-spacing: 0; }
  .glabel { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.16em; margin-left: -4px; }
  .chip { padding: 4px 8px; border: 1px solid var(--hairline); color: var(--ink-dim); font-size: 8px; }
  .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none;
    cursor: crosshair; font-size: 9px; letter-spacing: var(--tracking); }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }

  .empty { flex: 1; display: flex; flex-direction: column; gap: 14px; align-items: center;
    justify-content: center; color: var(--ink-dim); letter-spacing: 0.16em; text-align: center; padding: 0 40px; }
  .empty .sub { font-size: 9px; color: var(--ink-ghost); max-width: 520px; line-height: 1.7; }
  .empty .big { font-size: 26px; letter-spacing: var(--tracking-wide); color: var(--amber); }
  .warnring { width: 54px; height: 54px; border: 1px solid color-mix(in srgb, var(--amber) 50%, transparent);
    border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--amber); font-size: 22px; }
  .racts { display: flex; gap: 10px; margin-top: 6px; }
  .pulse { animation: pl 1.2s ease-in-out infinite; } @keyframes pl { 50% { opacity: 0.4; } }

  .body { flex: 1; min-height: 0; display: grid; grid-template-columns: 320px 1fr; }
  .queue { border-right: 1px solid var(--hairline); overflow-y: auto; padding: 12px 10px 40px;
    background: rgba(4,7,10,0.4); }
  .qgrp { display: flex; align-items: center; gap: 7px; font-size: 8px; color: var(--ink-dim);
    letter-spacing: 0.16em; margin: 14px 6px 7px; }
  .qgrp:first-child { margin-top: 4px; }
  .qd { width: 6px; height: 6px; border-radius: 50%; }
  .qd.u { background: var(--scarlet); box-shadow: 0 0 6px var(--scarlet); }
  .qd.f { background: var(--amber); } .qd.e { background: var(--jade); }
  .qgn { margin-left: auto; color: var(--ink-ghost); }
  .qrow { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left; padding: 8px 10px;
    margin-bottom: 3px; background: none; border: 1px solid transparent; border-left: 2px solid transparent;
    cursor: crosshair; }
  .qrow:hover { background: rgba(56,208,227,0.05); }
  .qrow.on { background: rgba(56,208,227,0.09); border-color: var(--hairline); border-left-color: var(--cyan); }
  .qrow.off { opacity: 0.45; }
  .pdot { width: 7px; height: 7px; border-radius: 50%; background: var(--scarlet); flex: 0 0 auto;
    box-shadow: 0 0 6px var(--scarlet); }
  .pdot.ok { background: var(--jade); box-shadow: 0 0 6px var(--jade); }
  .qmid { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
  .qttl { font-size: 11px; color: var(--ink-dim); }
  .qrow.on .qttl { color: var(--ink); }
  .qsub { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.1em; }
  .qsig { font-size: 9px; color: var(--scarlet); }
  .lempty { display: flex; flex-direction: column; gap: 12px; align-items: center; padding: 50px 10px;
    color: var(--ink-dim); font-size: 9px; letter-spacing: 0.14em; text-align: center; line-height: 1.8; }
  .okring { width: 48px; height: 48px; border: 1px solid color-mix(in srgb, var(--jade) 50%, transparent);
    border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--jade);
    font-size: 20px; animation: breathe 4s ease-in-out infinite; }
  @keyframes breathe { 50% { opacity: 0.5; } }

  .stage { min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
  .wipewrap { position: relative; flex: 1 1 62%; min-height: 0; background: #05070a; overflow: hidden;
    border-bottom: 1px solid var(--hairline); }
  .side { position: absolute; inset: 0; }
  .side.dream { filter: saturate(0.35) brightness(1.06) contrast(0.95); }
  .plate { width: 100%; height: 100%; object-fit: cover; }
  .noplate { display: flex; flex-direction: column; gap: 8px; align-items: center; justify-content: center;
    height: 100%; background: repeating-linear-gradient(45deg, #0a0d12 0 8px, #070a0e 8px 16px);
    color: var(--ink-ghost); font-size: 10px; letter-spacing: 0.16em; text-align: center; padding: 0 20px; }
  .np2 { font-size: 8px; }
  .mark { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  .blob { fill: none; stroke: var(--scarlet); stroke-width: 1.4; vector-effect: non-scaling-stroke;
    stroke-dasharray: 5 3; animation: ants 2.6s linear infinite; }
  @keyframes ants { to { stroke-dashoffset: -16; } }
  .divider { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; padding: 0;
    border: none; background: var(--cyan); cursor: ew-resize; touch-action: none;
    box-shadow: 0 0 10px rgba(56,208,227,0.5); }
  .dhandle { position: absolute; left: 50%; top: 50%; width: 14px; height: 14px; margin: -7px 0 0 -7px;
    border: 1px solid var(--cyan); background: #04070a; }
  .dlabel { position: absolute; left: 50%; top: calc(50% + 16px); transform: translateX(-50%);
    font-size: 8px; color: var(--cyan); letter-spacing: 0.12em; white-space: nowrap; }
  .stamp { position: absolute; left: 12px; bottom: 10px; font-size: 8px; color: var(--ink-dim);
    background: rgba(4,7,10,0.7); padding: 3px 8px; letter-spacing: 0.1em; }
  .triage { position: absolute; right: 12px; top: 10px; font-size: 8px; padding: 3px 8px;
    background: rgba(4,7,10,0.72); letter-spacing: 0.12em; }
  .t-subject { color: var(--scarlet); } .t-scene { color: var(--cyan); }

  .lower { flex: 0 0 auto; display: grid; grid-template-columns: 1.1fr 1fr 0.9fr; gap: 1px;
    background: var(--hairline); max-height: 38%; }
  .pnl { background: #05070a; padding: 11px 14px; display: flex; flex-direction: column; gap: 8px; overflow: hidden; }
  .pk { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.18em; }
  .pk.sens { margin-top: 4px; }

  .tl { position: relative; display: flex; align-items: flex-end; gap: 1px; height: 74px;
    border-bottom: 1px solid var(--hairline); }
  .tick { flex: 1; min-width: 1px; background: var(--ink-ghost); }
  .tick.hot { background: var(--scarlet); box-shadow: 0 0 4px var(--scarlet-glow); }
  .thr { position: absolute; left: 0; right: 0; height: 1px; background: var(--scarlet); opacity: 0.45; }
  .tlx { display: flex; justify-content: space-between; font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.12em; }

  .grid { display: grid; gap: 1px; }
  .cell { aspect-ratio: 1; background: var(--scarlet); }
  .cell.hot { box-shadow: 0 0 4px var(--scarlet-glow); }
  .cell.mute { background: var(--ink-ghost); opacity: 0.5 !important; }
  .cinfo { font-size: 8px; color: var(--ink-dim); letter-spacing: 0.12em; min-height: 12px; }

  .vacts { display: flex; gap: 7px; }
  .ok, .flag, .mini, .go, .alt { display: inline-flex; align-items: center; justify-content: center;
    gap: 6px; border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim);
    cursor: crosshair; letter-spacing: 0.12em; }
  .ok, .flag { flex: 1; padding: 9px 0; font-size: 9px; }
  .ok:hover:not(:disabled) { border-color: var(--jade); color: var(--jade); }
  .flag:hover:not(:disabled) { border-color: var(--scarlet); color: var(--scarlet); }
  .mini { padding: 6px 10px; font-size: 8px; }
  .mini:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
  .go { padding: 11px 20px; font-size: 11px; border-color: var(--cyan); color: var(--cyan); letter-spacing: 0.16em; }
  .go:hover:not(:disabled) { background: var(--cyan); color: #04070a; }
  .alt { padding: 11px 20px; font-size: 11px; letter-spacing: 0.16em; }
  .alt:hover:not(:disabled) { border-color: var(--scarlet); color: var(--scarlet); }
  .ok:disabled, .flag:disabled, .mini:disabled, .go:disabled, .alt:disabled { opacity: 0.4; }
  .ok .k, .flag .k { border: 1px solid var(--ink-ghost); padding: 0 4px; font-size: 8px; }

  .slider { width: 100%; accent-color: var(--cyan); cursor: crosshair; }
  .scap { font-size: 8px; color: var(--cyan); letter-spacing: 0.1em; line-height: 1.5; }
  .scap .dim { color: var(--ink-ghost); }
  .note { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.1em; line-height: 1.6; margin-top: auto; }

  @media (max-width: 1000px) { .lower { grid-template-columns: 1fr 1fr; } }
</style>
