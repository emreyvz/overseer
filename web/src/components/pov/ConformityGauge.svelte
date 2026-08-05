<script lang="ts">
  // GRAIN — per-subject conformity, drawn on the subject.
  //
  // An ordinary person carries a near-complete grey ring that nobody notices. That is the point:
  // 99.5% of the time this feature is silent. The ring erodes and warms as the percentile drops,
  // and only the genuinely rare gets a scarlet stub and a label.
  //
  // A third state exists and looks different from both: UNJUDGED, for a place the model has not
  // seen enough of. Collapsing ignorance into "anomalous" is why behavioural analytics has the
  // reputation it has.
  import { onDestroy, onMount } from 'svelte'
  import { detections, grainTracks } from '../../lib/stores'
  import { api } from '../../lib/api'
  import { FACTOR_LABEL } from '../../lib/grain'
  import { sfx } from '../../lib/audio'
  import { SIM } from '../../lib/sim'
  import type { Detection, GrainTrackRow } from '../../lib/types'

  const R = 9
  const C = 2 * Math.PI * R

  let openFor = $state<string | null>(null)
  let precedents = $state<GrainTrackRow[] | null>(null)
  let loadingPrec = $state(false)
  let hoverPrec = $state<GrainTrackRow | null>(null)

  // subjects the model has an opinion about, and only those
  const scored = $derived($detections.filter((d) => d.conformity && !d.coasting))
  const opened = $derived(scored.find((d) => d.id === openFor) ?? null)

  // A short foot-point trail, kept here rather than borrowed from foresight so the tick can sit
  // on the exact step the model disliked.
  //
  // The accumulator is a plain Map, NOT $state, and the effect publishes a fresh array rather
  // than incrementing a counter. An effect that both reads and writes the same $state (which a
  // `tick++` does) re-triggers itself forever; Svelte catches it as effect_update_depth_exceeded
  // and the whole overlay dies.
  const trails = new Map<string, [number, number][]>()
  let trailList = $state<{ id: string; pts: [number, number][]; tone: string }[]>([])

  $effect(() => {
    const dets = $detections
    for (const d of dets) {
      if (!d.conformity) continue
      const p: [number, number] = [d.bbox[0] + d.bbox[2] / 2, Math.min(0.999, d.bbox[1] + d.bbox[3])]
      const t = trails.get(d.id) ?? []
      const last = t[t.length - 1]
      if (!last || Math.hypot(last[0] - p[0], last[1] - p[1]) > 0.004) {
        t.push(p)
        if (t.length > 40) t.shift()
        trails.set(d.id, t)
      }
    }
    const live = new Map(dets.map((d) => [d.id, d]))
    for (const id of [...trails.keys()]) if (!live.has(id)) trails.delete(id)
    trailList = [...trails]
      .filter(([, pts]) => pts.length > 2)
      .map(([id, pts]) => ({ id, pts, tone: tone(live.get(id)!) }))
  })

  const arc = (p: number) => C * Math.max(0.04, Math.min(1, p / 100))
  const tone = (d: Detection) => {
    const c = d.conformity!
    if (c.state === 'unjudged') return 'unjudged'
    if (c.state === 'unusual') return 'unusual'
    return c.p < 12 ? 'watch' : 'ordinary'
  }

  async function openCard(d: Detection) {
    if (openFor === d.id) { openFor = null; precedents = null; return }
    openFor = d.id
    precedents = null
    sfx('click', { volume: 0.2 })
    // the newest scored row for this subject is what carries a database id
    const row = $grainTracks.find((t) => t.det_id === d.id)
    if (SIM) { precedents = $grainTracks.filter((t) => t.id !== row?.id).slice(0, 4); return }
    if (!row) return
    loadingPrec = true
    try {
      const r = await api.grainPrecedents(row.id, 6)
      precedents = r.precedents ?? []
    } catch { precedents = [] }
    loadingPrec = false
  }

  async function verdict(d: Detection, v: 'ordinary' | 'noteworthy') {
    const row = $grainTracks.find((t) => t.det_id === d.id)
    sfx(v === 'noteworthy' ? 'sonar' : 'click')
    openFor = null
    if (!row || SIM) return
    try { await api.grainVerdict(row.id, v) } catch { /* offline: the verdict is advisory */ }
  }

  const when = (ts: number) => new Date(ts).toLocaleDateString(undefined, { day: '2-digit', month: 'short' })
  const polyline = (path: [number, number][], w = 100, h = 100) =>
    path.map((p) => `${p[0] * w},${p[1] * h}`).join(' ')

  function onkey(e: KeyboardEvent) {
    if (e.key === 'Escape' && openFor) { e.stopPropagation(); openFor = null }
  }
  onMount(() => window.addEventListener('keydown', onkey, true))
  onDestroy(() => window.removeEventListener('keydown', onkey, true))
</script>

<div class="cg">
  <!-- trails: ordinary in ink-dim, the disliked segment in scarlet -->
  <svg class="trails" viewBox="0 0 100 100" preserveAspectRatio="none">
    {#each trailList as t (t.id)}
      <polyline class="trail t-{t.tone}" points={polyline(t.pts)} />
    {/each}
    {#if hoverPrec}
      <polyline class="ghost" points={polyline(hoverPrec.path as [number, number][])} />
    {/if}
  </svg>

  {#each scored as d (d.id)}
    {@const c = d.conformity!}
    <button class="gauge t-{tone(d)}" style={`left:${d.bbox[0] * 100}%; top:${d.bbox[1] * 100}%`}
      onclick={() => openCard(d)} title="How ordinary this movement is for this place">
      <svg viewBox="0 0 24 24">
        {#if c.state === 'unjudged'}
          <circle class="ring dotted" cx="12" cy="12" r={R} />
          <text class="qm" x="12" y="15.5">?</text>
        {:else}
          <circle class="track" cx="12" cy="12" r={R} />
          <circle class="ring" cx="12" cy="12" r={R}
            stroke-dasharray={`${arc(c.p)} ${C}`} />
        {/if}
      </svg>
      {#if c.state === 'unusual'}
        <span class="tag caps">{c.p.toFixed(1)}% · UNUSUAL</span>
      {/if}
    </button>

    {#if c.worst && c.state === 'unusual'}
      <span class="tick" style={`left:${c.worst[0] * 100}%; top:${c.worst[1] * 100}%`}></span>
    {/if}
  {/each}

  <!-- WHY: the decomposition, the comparison, and the precedents -->
  {#if opened && opened.conformity}
    {@const c = opened.conformity}
    <div class="why panel" style={`left:${Math.min(72, opened.bbox[0] * 100 + 4)}%; top:${Math.min(58, opened.bbox[1] * 100)}%`}>
      <header class="wh caps">
        <span class="wid">{opened.id}</span>
        <span class="wp t-{tone(opened)}">{c.p.toFixed(1)}TH PERCENTILE</span>
        <span class="wst caps t-{tone(opened)}">{c.state}</span>
        <button class="wx" onclick={() => (openFor = null)} aria-label="close">✕</button>
      </header>

      {#if c.state === 'unjudged'}
        <p class="wtxt">
          This place has not been walked through enough for the model to have an opinion here.
          It is not saying this is normal, and it is not saying it is odd.
        </p>
      {:else}
        <div class="factors">
          {#each Object.entries(c.factors) as [k, v]}
            <div class="fac">
              <span class="fk caps">{FACTOR_LABEL[k] ?? k}</span>
              <!-- reads out from the CENTRE: 50 is ordinary, both tails are deviation -->
              <span class="fbar">
                <span class="fmid"></span>
                <span class="ffill" class:lead={v <= 10} class:mid={v > 10 && v <= 35}
                  style={`left:${Math.min(v, 50)}%; width:${Math.max(1.5, Math.abs(v - 50))}%`}></span>
              </span>
              <span class="fv caps" class:lead={v <= 10}>{v.toFixed(0)}</span>
            </div>
          {/each}
        </div>
        {#if c.why}<p class="wtxt">{c.why}</p>{/if}
      {/if}

      <div class="prec">
        <div class="pk caps">PRECEDENT</div>
        {#if loadingPrec}
          <div class="pmt caps"><span class="pulse">SEARCHING THE RECORD_</span></div>
        {:else if precedents && precedents.length}
          <div class="pstrip">
            {#each precedents as p (p.id)}
              <button class="pcell" onmouseenter={() => (hoverPrec = p)} onmouseleave={() => (hoverPrec = null)}
                title="Hover to trace this path over the frame">
                <svg viewBox="0 0 40 26" preserveAspectRatio="none">
                  <polyline points={p.path.map((q) => `${q[0] * 40},${q[1] * 26}`).join(' ')} />
                </svg>
                <span class="pdate caps">{when(p.start_ts)}</span>
                <span class="pv caps v-{p.verdict ?? 'none'}">
                  {p.verdict === 'noteworthy' ? 'NOTED' : p.verdict === 'ordinary' ? 'ORDINARY' : '—'}
                </span>
              </button>
            {/each}
          </div>
          <div class="pnote caps">
            {precedents.filter((p) => p.verdict === 'noteworthy').length} OF {precedents.length}
            COMPARABLE TRACKS WERE MARKED NOTEWORTHY
          </div>
        {:else}
          <div class="pmt caps">NO COMPARABLE TRACK IN THE RECORD YET</div>
        {/if}
      </div>

      <div class="wact">
        <button class="ok caps" onclick={() => verdict(opened, 'ordinary')}>✓ ORDINARY</button>
        <button class="flag caps" onclick={() => verdict(opened, 'noteworthy')}>⚑ NOTEWORTHY</button>
      </div>
      <div class="foot caps">MOVEMENT ONLY · NO APPEARANCE OR IDENTITY FEATURE IS USED</div>
    </div>
  {/if}
</div>

<style>
  .cg { position: absolute; inset: 0; z-index: 8; pointer-events: none; }
  .trails { position: absolute; inset: 0; width: 100%; height: 100%; }
  .trail { fill: none; stroke-width: 1; vector-effect: non-scaling-stroke; }
  .trail.t-ordinary { stroke: var(--ink-dim); stroke-opacity: 0.22; }
  .trail.t-watch { stroke: var(--amber); stroke-opacity: 0.4; }
  .trail.t-unusual { stroke: var(--scarlet); stroke-opacity: 0.7; }
  .trail.t-unjudged { stroke: var(--ink-ghost); stroke-opacity: 0.25; stroke-dasharray: 2 3; }
  .ghost { fill: none; stroke: var(--jade); stroke-opacity: 0.85; stroke-width: 1.6;
    vector-effect: non-scaling-stroke; stroke-dasharray: 4 3; animation: gdash 2s linear infinite; }
  @keyframes gdash { to { stroke-dashoffset: -14; } }

  .gauge { position: absolute; width: 24px; height: 24px; margin: -13px 0 0 -13px; padding: 0;
    background: none; border: none; cursor: crosshair; pointer-events: auto; }
  .gauge svg { width: 24px; height: 24px; transform: rotate(-90deg); overflow: visible; }
  .track { fill: none; stroke: var(--hairline); stroke-width: 2; }
  .ring { fill: none; stroke-width: 2; stroke-linecap: round; transition: stroke-dasharray 500ms; }
  .ring.dotted { stroke: var(--ink-ghost); stroke-dasharray: 2 3; }
  .qm { fill: var(--ink-ghost); font-size: 9px; text-anchor: middle; transform: rotate(90deg);
    transform-origin: 12px 12px; font-family: var(--font-mono); }
  .t-ordinary .ring { stroke: var(--ink-dim); opacity: 0.45; }
  .t-watch .ring { stroke: var(--amber); opacity: 0.85; }
  .t-unusual .ring { stroke: var(--scarlet); filter: drop-shadow(0 0 5px var(--scarlet-glow)); }
  .gauge:hover .ring { opacity: 1; }
  .tag { position: absolute; left: 26px; top: 4px; font-size: 8px; letter-spacing: 0.12em;
    color: var(--scarlet); text-shadow: 0 0 5px #000; white-space: nowrap; }

  .tick { position: absolute; width: 1px; height: 12px; margin: -6px 0 0 0; background: var(--scarlet);
    box-shadow: 0 0 6px var(--scarlet-glow); }

  .why { position: absolute; width: 300px; padding: 12px 13px 11px; pointer-events: auto;
    display: flex; flex-direction: column; gap: 9px; z-index: 2;
    animation: rise 220ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
  .wh { display: flex; align-items: center; gap: 8px; font-size: 9px; }
  .wid { color: var(--ink); }
  .wp { font-size: 8px; letter-spacing: 0.1em; }
  .wst { margin-left: auto; font-size: 8px; }
  .wp.t-unusual, .wst.t-unusual { color: var(--scarlet); }
  .wp.t-watch, .wst.t-watch { color: var(--amber); }
  .wp.t-ordinary, .wst.t-ordinary { color: var(--ink-dim); }
  .wp.t-unjudged, .wst.t-unjudged { color: var(--ink-ghost); }
  .wx { background: none; border: none; color: var(--ink-ghost); cursor: crosshair; font-size: 10px; padding: 0 0 0 4px; }
  .wx:hover { color: var(--scarlet); }

  .factors { display: flex; flex-direction: column; gap: 4px; }
  .fac { display: grid; grid-template-columns: 54px 1fr 22px; align-items: center; gap: 7px; }
  .fk { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.1em; }
  /* the bar reads out from the CENTRE: ordinary sits in the middle, both tails are deviation */
  .fbar { position: relative; height: 4px; background: var(--hairline); }
  .fmid { position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--ink-ghost); opacity: 0.6; }
  .ffill { position: absolute; top: 0; bottom: 0; background: var(--ink-dim); }
  .ffill.mid { background: var(--amber); }
  .ffill.lead { background: var(--scarlet); box-shadow: 0 0 7px var(--scarlet-glow); }
  .fv { font-size: 8px; color: var(--ink-dim); text-align: right; }
  .fv.lead { color: var(--scarlet); }
  .wtxt { font-size: 10px; color: var(--ink-dim); line-height: 1.65; margin: 0; }

  .prec { display: flex; flex-direction: column; gap: 6px; }
  .pk { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.16em; }
  .pstrip { display: flex; gap: 5px; flex-wrap: wrap; }
  .pcell { width: 44px; background: none; border: 1px solid var(--hairline); padding: 3px 2px 2px;
    cursor: crosshair; display: flex; flex-direction: column; align-items: center; gap: 2px; }
  .pcell:hover { border-color: var(--jade); }
  .pcell svg { width: 100%; height: 20px; }
  .pcell polyline { fill: none; stroke: var(--ink-dim); stroke-width: 1.4; vector-effect: non-scaling-stroke; }
  .pcell:hover polyline { stroke: var(--jade); }
  .pdate { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.06em; }
  .pv { font-size: 6px; letter-spacing: 0.06em; }
  .v-noteworthy { color: var(--scarlet); } .v-ordinary { color: var(--jade); } .v-none { color: var(--ink-ghost); }
  .pnote { font-size: 8px; color: var(--ink-dim); letter-spacing: 0.1em; line-height: 1.5; }
  .pmt { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.12em; }
  .pulse { animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.4; } }

  .wact { display: flex; gap: 7px; margin-top: 2px; }
  .ok, .flag { flex: 1; padding: 7px 0; border: 1px solid var(--ink-dim); background: none;
    color: var(--ink-dim); cursor: crosshair; font-size: 9px; letter-spacing: 0.14em; }
  .ok:hover { border-color: var(--jade); color: var(--jade); }
  .flag:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .foot { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.1em; line-height: 1.5; }
</style>
