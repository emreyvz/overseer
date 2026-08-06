<script lang="ts">
  // PERCEPTION — one column, all the readouts.
  //
  // Each engine used to position its own readout absolutely, which meant two of them switched on
  // together drew on top of each other. Laying them out in a single flex column makes collisions
  // structurally impossible instead of a matter of choosing the right offsets.
  //
  // It also lives OUTSIDE .zoomwrap: that wrapper is transformed, so it forms its own stacking
  // context down at the video layer and anything inside it paints underneath every rail.
  //
  // Two things were wrong with the first version and both were mine:
  //
  // 1. It was set in SIX PIXEL type in a 158px column. That is roughly half the size at which
  //    text stops being text and becomes texture. Every word here was, in practice, unreadable —
  //    the operator's report was "the panel is tiny, nothing is legible", which was exactly right.
  // 2. Nothing looked like a way in. Opening a detail screen meant clicking a progress ring or a
  //    heading — no border, no arrow, no verb. The operator found one screen BY ACCIDENT and had
  //    no idea it could be opened at all. If a surface can open, the thing that opens it has to
  //    look like a button and say what it opens.
  import {
    blindSpots, coverage, coverageScreen, eardrumDrawer, grainScreen, grainStatus, modules, probes,
  } from '../../lib/stores'
  import { COACH, coached } from '../../lib/perception'
  import { sfx } from '../../lib/audio'
  import CoachCard from './CoachCard.svelte'

  const RC = 2 * Math.PI * 26
  const on = (k: string) => !!$modules.find((m) => m.key === k)?.on

  const cov = $derived($coverage)
  const grain = $derived($grainStatus)
  const fogOn = $derived(on('unseen'))
  const grainOn = $derived(on('grain'))
  const listenOn = $derived(on('listen'))
  const gaps = $derived($blindSpots.filter((s) => !s.dismissed).length)

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
        <!-- Every card leads with what it IS and what it is FOR, because an operator who has just
             switched something on has no other way to find out. Two short lines, not a paragraph. -->
        <header class="hd">
          <span class="nm">FOG OF WAR</span>
          <span class="for">what this camera cannot really see</span>
        </header>

        <div class="ringrow">
          <div class="ring">
            <svg viewBox="0 0 60 60">
              <circle class="rtrack" cx="30" cy="30" r="26" />
              <circle class="rprog" cx="30" cy="30" r="26"
                stroke-dasharray={RC} stroke-dashoffset={RC * (1 - cov.percent / 100)} />
            </svg>
            <span class="rval">{Math.round(cov.percent)}%</span>
          </div>
          <div class="ringtxt">
            of this view is watched well enough to <b>{cov.task}</b> someone
          </div>
        </div>

        <div class="rowline"><span class="swatch static"></span><span class="lt">Static = not really watched</span></div>
        <div class="rowline"><span class="swatch clear"></span><span class="lt">Clear = seen properly</span></div>

        <!-- The four distance bands used to be listed here. They pushed the way in below the fold
             of a scrolling column, which meant the one control that mattered was the one you
             could not see. Reference detail belongs on the screen this button opens. -->
        <button class="open" onclick={() => { coverageScreen.set(true); sfx('sonar') }}>
          {gaps ? `SEE ALL ${gaps} BLIND SPOT${gaps === 1 ? '' : 'S'}` : 'OPEN THE COVERAGE REPORT'}
          <span class="arw">▸</span>
        </button>
      </section>
    {/if}

    {#if grainOn && grain}
      <section class="card">
        <header class="hd">
          <span class="nm">GRAIN</span>
          <span class="for">how people normally move through here</span>
          <span class="state" class:ready={grain.mature}>{grain.mature ? 'READY' : 'LEARNING'}</span>
        </header>

        {#if !grain.mature}
          <div class="bar"><span class="fill" style={`width:${Math.round(grain.maturity * 100)}%`}></span></div>
          <div class="sub">Watched {grain.tracks.toLocaleString()} journeys so far. Not judging anyone yet.</div>
        {:else}
          <div class="sub">Learned from {grain.tracks.toLocaleString()} journeys over {grain.days} day{grain.days === 1 ? '' : 's'}.</div>
        {/if}
        {#if grain.suspended}<div class="warn">Paused · {grain.suspended}</div>{/if}
        {#if grain.stale}<div class="warn">The camera moved, so the model no longer fits this view.</div>{/if}

        <div class="lk">THE RING ON EACH PERSON</div>
        <div class="rowline"><span class="pip ord"></span><span class="lt">Ordinary here. Ignore it.</span></div>
        <div class="rowline"><span class="pip odd"></span><span class="lt">Rare for this place.</span></div>
        <div class="rowline"><span class="pip unk"></span><span class="lt">Not seen enough to say.</span></div>

        <button class="open" onclick={() => { grainScreen.set(true); sfx('sonar') }}>
          OPEN THE LEARNED MODEL<span class="arw">▸</span>
        </button>
      </section>
    {/if}

    {#if listenOn && $probes.length}
      <section class="card">
        <header class="hd">
          <span class="nm">EARDRUM</span>
          <span class="for">vibration read off surfaces, too small to see</span>
        </header>
        <!-- Kept deliberately short. With all three engines on, a longer card pushed its own way
             in below the fold of the column, which is the failure this whole card exists to
             avoid. The key to the marks on the video lives in the drawer this button opens. -->
        <div class="sub">
          {$probes.length} listening point{$probes.length === 1 ? '' : 's'}. Tells you a machine is
          running rough, or that something struck a surface.
        </div>

        <!-- This button did not exist. With probes already placed there was NO visible way to
             reach the analysis, only Shift+L, so the operator reported the UI simply not opening. -->
        <button class="open" onclick={() => { eardrumDrawer.set(true); sfx('sonar') }}>
          OPEN WHAT THEY ARE HEARING<span class="arw">▸</span>
        </button>
      </section>
    {/if}
  </div>

  {#if coach}<CoachCard id={coach} place="left" />{/if}
</div>

<style>
  .hud { position: absolute; inset: 0; z-index: calc(var(--z-panel) + 1); pointer-events: none; }
  /* clear of the auxiliary rail (right:20px, 300px wide) and above it in paint order.
     244px wide because the previous 158px forced type down to a size nobody can read. */
  .col { position: absolute; right: 336px; top: 84px; width: 244px;
    max-height: calc(100vh - 172px); overflow-y: auto; overflow-x: hidden;
    display: flex; flex-direction: column; gap: 9px; }
  .card { pointer-events: auto; background: rgba(4,7,10,0.82); border: 1px solid var(--hairline);
    padding: 12px 12px 11px; display: flex; flex-direction: column; gap: 6px;
    backdrop-filter: blur(3px); animation: cin 240ms var(--ease); }
  /* No `both` fill: with it, an entry animation that has not started yet holds its
     `from` state, and an element whose `from` is opacity 0 stays invisible forever. */
  @keyframes cin { from { opacity: 0; transform: translateX(8px); } }

  .hd { display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; margin-bottom: 2px; }
  .nm { font-size: 11px; color: var(--ink); letter-spacing: 0.16em; }
  .for { grid-column: 1 / -1; font-size: 11px; color: var(--ink-dim); line-height: 1.5; }
  .state { font-size: 8px; color: var(--amber); letter-spacing: 0.12em; align-self: start;
    border: 1px solid currentColor; padding: 1px 5px; }
  .state.ready { color: var(--jade); }

  .ringrow { display: flex; align-items: center; gap: 11px; margin: 4px 0 2px; }
  .ring { position: relative; width: 54px; height: 54px; flex: 0 0 auto; }
  .ring svg { width: 54px; height: 54px; transform: rotate(-90deg); display: block; }
  .rtrack { fill: none; stroke: var(--hairline); stroke-width: 4; }
  .rprog { fill: none; stroke: var(--cyan); stroke-width: 4; stroke-linecap: round;
    transition: stroke-dashoffset 700ms cubic-bezier(0.16, 1, 0.3, 1);
    filter: drop-shadow(0 0 4px color-mix(in srgb, var(--cyan) 60%, transparent)); }
  .rval { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    font-size: 15px; color: var(--cyan); }
  .ringtxt { font-size: 11px; color: var(--ink-dim); line-height: 1.5; }
  .ringtxt b { color: var(--ink); font-weight: 500; text-transform: uppercase; letter-spacing: 0.08em; }

  .sub { font-size: 11px; color: var(--ink-dim); line-height: 1.5; }
  .warn { font-size: 10px; color: var(--amber); line-height: 1.5; }
  .lk { font-size: 9px; color: var(--ink-ghost); letter-spacing: 0.16em; margin-top: 7px;
    padding-top: 7px; border-top: 1px solid var(--hairline); }

  .rowline { display: flex; align-items: center; gap: 8px; }
  .lt { font-size: 10px; color: var(--ink-dim); line-height: 1.45; }
  .swatch { width: 20px; height: 12px; flex: 0 0 auto; border: 1px solid var(--hairline); }
  .swatch.static { background:
    repeating-linear-gradient(45deg, rgba(236,236,236,0.10) 0 1px, transparent 1px 5px), #0a0d12; }
  .swatch.clear { background: #2a3138; }
  .pip { width: 12px; height: 12px; border-radius: 50%; flex: 0 0 auto; border: 2px solid var(--ink-dim); }
  .pip.ord { opacity: 0.45; }
  .pip.odd { border-color: var(--scarlet); box-shadow: 0 0 5px var(--scarlet-glow); }
  .pip.unk { border-style: dotted; border-color: var(--ink-ghost); }

  .bar { position: relative; height: 4px; background: var(--hairline); }
  .fill { position: absolute; inset: 0 auto 0 0; background: var(--jade); transition: width 600ms; }


  /* The way in. Bordered, full width, names its destination and carries an arrow, so it reads as
     a door rather than as one more label. */
  .open { display: flex; align-items: center; justify-content: space-between; gap: 8px;
    width: 100%; margin-top: 9px; padding: 9px 10px; background: none; cursor: crosshair;
    border: 1px solid color-mix(in srgb, var(--cyan) 55%, transparent); color: var(--cyan);
    font-size: 10px; letter-spacing: 0.14em; text-align: left;
    transition: background 140ms, color 140ms, border-color 140ms; }
  .open:hover { background: var(--cyan); color: #04070a; border-color: var(--cyan);
    box-shadow: 0 0 16px color-mix(in srgb, var(--cyan) 35%, transparent); }
  .arw { flex: 0 0 auto; }
</style>
