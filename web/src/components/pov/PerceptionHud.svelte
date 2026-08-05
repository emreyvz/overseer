<script lang="ts">
  // PERCEPTION — one column, all the readouts.
  //
  // Each engine used to position its own readout absolutely, which meant two of them switched on
  // together drew on top of each other. Laying them out in a single flex column makes collisions
  // structurally impossible instead of a matter of choosing the right offsets.
  //
  // It also lives OUTSIDE .zoomwrap: that wrapper is transformed, so it forms its own stacking
  // context down at the video layer and anything inside it paints underneath every rail.
  import { coverage, coverageScreen, grainScreen, grainStatus, modules, probes } from '../../lib/stores'
  import { COACH, coached } from '../../lib/perception'
  import { sfx } from '../../lib/audio'
  import type { DoriTask } from '../../lib/types'
  import CoachCard from './CoachCard.svelte'

  const RC = 2 * Math.PI * 26
  const on = (k: string) => !!$modules.find((m) => m.key === k)?.on

  const cov = $derived($coverage)
  const grain = $derived($grainStatus)
  const fogOn = $derived(on('unseen'))
  const grainOn = $derived(on('grain'))
  const listenOn = $derived(on('listen'))

  let hoverBand = $state<DoriTask | null>(null)

  const BAND_LABEL: Record<DoriTask, string> = {
    identify: 'IDENTIFY', recognise: 'RECOGNISE', observe: 'OBSERVE', detect: 'DETECT',
  }
  const BAND_MEANS: Record<DoriTask, string> = {
    identify: 'close enough to tell WHO someone is',
    recognise: 'close enough to recognise someone you know',
    observe: 'close enough to describe clothing and behaviour',
    detect: 'close enough to tell that someone is there at all',
  }

  // ONE coach at a time, in a fixed order. Three cards fighting for the same corner taught the
  // operator nothing and looked broken.
  const coach = $derived.by(() => {
    const order: [string, boolean][] = [
      ['unseen', fogOn], ['grain', grainOn], ['dream', on('dream')], ['listen', listenOn],
    ]
    return order.find(([id, active]) => active && !$coached.has(id) && COACH[id])?.[0] ?? null
  })
</script>

<div class="hud">
  <div class="col">
    {#if fogOn && cov}
      <section class="card">
        <button class="ring" onclick={() => { coverageScreen.set(true); sfx('sonar') }}>
          <svg viewBox="0 0 60 60">
            <circle class="rtrack" cx="30" cy="30" r="26" />
            <circle class="rprog" cx="30" cy="30" r="26"
              stroke-dasharray={RC} stroke-dashoffset={RC * (1 - cov.percent / 100)} />
          </svg>
          <span class="rval">{Math.round(cov.percent)}%</span>
        </button>
        <div class="ttl caps">OF THIS VIEW IS ACTUALLY WATCHED</div>
        <div class="sub caps">AT “{cov.task}” LEVEL · PRESS TO SEE THE GAPS</div>
        <div class="rowline"><span class="swatch static"></span><span class="lt caps">NOT REALLY WATCHED</span></div>
        <div class="rowline"><span class="swatch clear"></span><span class="lt caps">SEEN PROPERLY</span></div>
        <div class="hint caps">HIDDEN, TOO FAR, TOO DARK, OR KEEPS LOSING PEOPLE</div>

        <div class="lk caps">HOW FAR IS GOOD ENOUGH</div>
        {#each cov.bands as b (b.task)}
          <button class="band caps" class:on={b.task === cov.task}
            onmouseenter={() => (hoverBand = b.task)} onmouseleave={() => (hoverBand = null)}>
            <span>{BAND_LABEL[b.task]}</span><span class="bdist">to ~{b.range_m} m</span>
          </button>
        {/each}
        <div class="mean">{BAND_MEANS[hoverBand ?? cov.task]}</div>
        {#if cov.scale_estimated}<div class="hint caps">DISTANCES ARE ESTIMATED</div>{/if}
      </section>
    {/if}

    {#if grainOn && grain}
      <section class="card">
        <button class="chd" onclick={() => { grainScreen.set(true); sfx('sonar') }}>
          <span class="ttl caps">GRAIN</span>
          <span class="state caps" class:ready={grain.mature}>{grain.mature ? 'READY' : 'LEARNING'}</span>
        </button>
        {#if !grain.mature}
          <div class="bar"><span class="fill" style={`width:${Math.round(grain.maturity * 100)}%`}></span></div>
          <div class="sub caps">WATCHED {grain.tracks.toLocaleString()} JOURNEYS · NOT JUDGING ANYONE YET</div>
        {:else}
          <div class="sub caps">LEARNED FROM {grain.tracks.toLocaleString()} JOURNEYS OVER {grain.days} DAY{grain.days === 1 ? '' : 'S'}</div>
        {/if}
        {#if grain.suspended}<div class="warn caps">PAUSED · {grain.suspended}</div>{/if}
        {#if grain.stale}<div class="warn caps">THE CAMERA MOVED · MODEL NO LONGER VALID</div>{/if}
        <div class="lk caps">THE RING ON EACH PERSON</div>
        <div class="rowline"><span class="pip ord"></span><span class="lt caps">ORDINARY HERE · IGNORE IT</span></div>
        <div class="rowline"><span class="pip odd"></span><span class="lt caps">RARE FOR THIS PLACE</span></div>
        <div class="rowline"><span class="pip unk"></span><span class="lt caps">NOT SEEN THIS SPOT ENOUGH TO SAY</span></div>
        <div class="hint caps">FAINT STREAKS = WHICH WAY PEOPLE NORMALLY WALK</div>
      </section>
    {/if}

    {#if listenOn && $probes.length}
      <section class="card">
        <div class="ttl caps">EARDRUM</div>
        <div class="sub caps">{$probes.length} LISTENING POINT{$probes.length === 1 ? '' : 'S'}</div>
        <div class="rowline"><span class="kwave"></span><span class="lt caps">LIVE SURFACE MOVEMENT, MAGNIFIED</span></div>
        <div class="rowline"><span class="kbar"></span><span class="lt caps">HOW HARD IT SHAKES VS ITS BASELINE</span></div>
        <div class="rowline"><span class="kanchor">⚓</span><span class="lt caps">THE STILL REFERENCE, SUBTRACTED OUT</span></div>
      </section>
    {/if}
  </div>

  {#if coach}<CoachCard id={coach} place="left" />{/if}
</div>

<style>
  .hud { position: absolute; inset: 0; z-index: calc(var(--z-panel) + 1); pointer-events: none; }
  /* clear of the auxiliary rail (right:20px, 300px wide) and above it in paint order */
  .col { position: absolute; right: 336px; top: 84px; width: 158px;
    max-height: calc(100vh - 260px); overflow-y: auto; overflow-x: hidden;
    display: flex; flex-direction: column; gap: 7px; }
  .card { pointer-events: auto; background: rgba(4,7,10,0.62); border: 1px solid var(--hairline);
    padding: 9px 8px; display: flex; flex-direction: column; gap: 4px;
    animation: cin 240ms var(--ease); }
  /* No `both` fill: with it, an entry animation that has not started yet holds its
     `from` state, and an element whose `from` is opacity 0 stays invisible forever. */
  @keyframes cin { from { opacity: 0; transform: translateX(8px); } }

  .ring { position: relative; align-self: center; width: 46px; height: 46px; padding: 0;
    background: none; border: none; cursor: crosshair; }
  .ring svg { width: 46px; height: 46px; transform: rotate(-90deg); display: block; }
  .rtrack { fill: none; stroke: var(--hairline); stroke-width: 4; }
  .rprog { fill: none; stroke: var(--cyan); stroke-width: 4; stroke-linecap: round;
    transition: stroke-dashoffset 700ms cubic-bezier(0.16, 1, 0.3, 1);
    filter: drop-shadow(0 0 4px color-mix(in srgb, var(--cyan) 60%, transparent)); }
  .rval { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    font-size: 12px; color: var(--cyan); }

  .chd { display: flex; align-items: center; gap: 6px; background: none; border: none; padding: 0;
    cursor: crosshair; }
  .chd:hover .ttl { color: var(--cyan); }
  .ttl { font-size: 8px; color: var(--ink); letter-spacing: 0.13em; line-height: 1.5; text-align: left; }
  .state { margin-left: auto; font-size: 6px; color: var(--amber); letter-spacing: 0.1em; }
  .state.ready { color: var(--jade); }
  .sub { font-size: 6px; color: var(--ink-dim); letter-spacing: 0.1em; line-height: 1.5; }
  .hint { font-size: 6px; color: var(--ink-ghost); letter-spacing: 0.1em; line-height: 1.5; }
  .warn { font-size: 6px; color: var(--amber); letter-spacing: 0.1em; line-height: 1.5; }
  .lk { font-size: 6px; color: var(--ink-ghost); letter-spacing: 0.14em; margin-top: 5px;
    padding-top: 5px; border-top: 1px solid var(--hairline); }

  .rowline { display: flex; align-items: center; gap: 6px; }
  .lt { font-size: 6px; color: var(--ink-dim); letter-spacing: 0.08em; line-height: 1.4; }
  .swatch { width: 15px; height: 9px; flex: 0 0 auto; border: 1px solid var(--hairline); }
  .swatch.static { background:
    repeating-linear-gradient(45deg, rgba(236,236,236,0.10) 0 1px, transparent 1px 5px), #0a0d12; }
  .swatch.clear { background: #2a3138; }
  .pip { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; border: 2px solid var(--ink-dim); }
  .pip.ord { opacity: 0.45; }
  .pip.odd { border-color: var(--scarlet); box-shadow: 0 0 5px var(--scarlet-glow); }
  .pip.unk { border-style: dotted; border-color: var(--ink-ghost); }
  .kwave { width: 15px; height: 6px; flex: 0 0 auto;
    background: linear-gradient(90deg, transparent, var(--cyan), transparent); opacity: 0.7; }
  .kbar { width: 15px; height: 2px; flex: 0 0 auto; background: var(--cyan); }
  .kanchor { width: 15px; flex: 0 0 auto; color: var(--ink-dim); font-size: 8px; text-align: center; }

  .bar { position: relative; height: 3px; background: var(--hairline); }
  .fill { position: absolute; inset: 0 auto 0 0; background: var(--jade); transition: width 600ms; }

  .band { display: flex; justify-content: space-between; gap: 6px; background: none; border: none;
    padding: 1px 0; color: var(--ink-dim); font-size: 6px; letter-spacing: 0.1em; cursor: crosshair; }
  .band:hover { color: var(--ink); }
  .band.on { color: var(--cyan); }
  .bdist { color: var(--ink-ghost); }
  .mean { font-size: 6px; color: var(--cyan); letter-spacing: 0.05em; line-height: 1.6; margin-top: 3px; }
</style>
