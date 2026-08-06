<script lang="ts">
  // BEDROCK — the past as a database.
  //
  // Three panes: the question on the left (chips, never text), the answer in the middle as a
  // Gantt of reality, and the evidence on the right. Underneath, two independent time axes, one
  // of which rewinds what the system KNEW rather than what happened.
  import { onDestroy, onMount } from 'svelte'
  import { bedrockAsOf, bedrockQuery, bedrockResult, mode, triggerGlitch } from '../../lib/stores'
  import {
    beliefDelta, defaultQuery, loadVocab, queryError, runQuery, running, stats,
  } from '../../lib/bedrock'
  import { api } from '../../lib/api'
  import { sfx } from '../../lib/audio'
  import { SIM } from '../../lib/sim'
  import type { BedrockEntity, BedrockFact, BedrockQuery, BedrockResult } from '../../lib/types'
  import QueryLens from '../bedrock/QueryLens.svelte'
  import FactTimeline from '../bedrock/FactTimeline.svelte'
  import TimeDial from '../bedrock/TimeDial.svelte'
  import Inspector from '../bedrock/Inspector.svelte'
  import ScreenIntro from '../ScreenIntro.svelte'

  type View = 'timeline' | 'graph' | 'table'
  let view = $state<View>('timeline')
  let selFact = $state<BedrockFact | null>(null)
  let selEntity = $state<BedrockEntity | null>(null)
  let hoverFact = $state<BedrockFact | null>(null)
  let nowResult = $state<BedrockResult | null>(null)   // the live-belief result, for the delta
  let backfilling = $state(false)

  const q = $derived($bedrockQuery ?? defaultQuery())
  const res = $derived($bedrockResult)
  const st = $derived($stats)
  const delta = $derived(beliefDelta(nowResult, res))
  const empty = $derived(!!res && !res.entities.length)
  const needsBackfill = $derived(!!st && st.facts === 0 && !st.backfill.running)

  async function run(next?: BedrockQuery) {
    const query = next ?? q
    bedrockQuery.set(query)
    await runQuery(query)
    if ($bedrockAsOf === null) { nowResult = $bedrockResult; return }
    // Rewound: run the same query once more at live belief so the dial can say how many facts
    // differ. Two passes, never three, and only while the operator is actually time-travelling.
    const rewound = $bedrockResult
    const saved = $bedrockAsOf
    bedrockAsOf.set(null)
    await runQuery(query)
    nowResult = $bedrockResult
    bedrockAsOf.set(saved)
    bedrockResult.set(rewound)
  }

  function pickFact(f: BedrockFact) {
    selFact = f
    selEntity = res?.entities.find((e) => e.uid === f.subj) ?? null
    sfx('click', { volume: 0.15 })
  }
  function pickEntity(e: BedrockEntity) { selEntity = e; selFact = null; sfx('click', { volume: 0.15 }) }

  async function purge(uid: number) {
    triggerGlitch(220); sfx('glitch')
    if (!SIM) await api.bedrockPurge(uid).catch(() => undefined)
    selEntity = null; selFact = null
    await run()
  }

  async function backfill() {
    backfilling = true
    sfx('sonar')
    if (!SIM) await api.bedrockBackfill().catch(() => undefined)
    const poll = setInterval(async () => {
      try {
        const s = await api.bedrockStats()
        stats.set(s as never)
        if (!s.backfill.running) { clearInterval(poll); backfilling = false; run() }
      } catch { clearInterval(poll); backfilling = false }
    }, 1200)
    if (SIM) { clearInterval(poll); backfilling = false }
  }

  function onkey(e: KeyboardEvent) {
    const t = e.target as HTMLElement | null
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return
    if (e.key === '/') { e.preventDefault(); (document.querySelector('.askin') as HTMLElement)?.focus() }
    else if (e.key === 'Enter') { e.preventDefault(); run() }
    else if (e.key === '1') view = 'timeline'
    else if (e.key === '2') view = 'graph'
    else if (e.key === '3') view = 'table'
  }

  onMount(async () => {
    sfx('sonar')
    await loadVocab()
    if (!$bedrockQuery) bedrockQuery.set(defaultQuery())
    await run()
    window.addEventListener('keydown', onkey, true)
  })
  onDestroy(() => window.removeEventListener('keydown', onkey, true))

  const stamp = (ms: number) => new Date(ms).toLocaleString(undefined,
    { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
</script>

<div class="bd">
  <header class="top caps">
    <span class="eyebrow">◈ BEDROCK</span>
    {#if st}<span class="cnt">{st.facts.toLocaleString()} FACTS · {st.entities.toLocaleString()} ENTITIES</span>{/if}
    {#if res}<span class="cnt">{res.entities.length} MATCHED IN {res.took_ms} MS</span>{/if}
    <span class="spacer"></span>
    <div class="seg caps">
      {#each [['timeline', '▤ TIMELINE', '1'], ['graph', '◈ GRAPH', '2'], ['table', '▦ TABLE', '3']] as [k, label, key]}
        <button class="sb" class:on={view === k} onclick={() => (view = k as View)}>{label}<span class="k">{key}</span></button>
      {/each}
    </div>
    <button class="x caps" onclick={() => mode.set('pov')}>✕ CLOSE</button>
  </header>

  <ScreenIntro
    what="Ask the past a question, and see what the system believed at the time rather than only what it believes now."
    hint="Build the question on the left. The two sliders at the bottom set when it happened and when we knew." />

  <div class="body">
    <QueryLens query={q} entities={res?.entities ?? []} onrun={run} onchange={(n) => bedrockQuery.set(n)} />

    <main class="stage">
      {#if $running}
        <div class="mid caps"><span class="scan"></span><span class="pulse">QUERYING THE RECORD_</span></div>
      {:else if needsBackfill}
        <div class="mid caps col">
          <div class="big display">BEDROCK IS EMPTY</div>
          <div class="sub">
            NOTHING HAS BEEN PROJECTED YET. THE EVENTS, ALERTS, SIGHTINGS AND SUBJECTS ALREADY IN
            THE DATABASE CAN BE TURNED INTO FACTS WITHOUT TOUCHING THEM.
          </div>
          <button class="go caps" onclick={backfill} disabled={backfilling}>
            {backfilling ? `${st?.backfill.phase ?? 'WORKING'}_` : '▶ BUILD THE RECORD'}
          </button>
          {#if backfilling && st}
            <div class="bar"><span class="fill" style={`width:${(st.backfill.done / Math.max(1, st.backfill.total)) * 100}%`}></span></div>
            <div class="sub">{st.backfill.phase} · {(st.backfill as { facts?: number }).facts ?? 0} FACTS</div>
          {/if}
        </div>
      {:else if $queryError}
        <div class="mid caps col">
          <div class="warn">{$queryError.message}</div>
          <div class="sub">NARROW THE QUESTION AND RUN IT AGAIN.</div>
        </div>
      {:else if empty}
        <div class="mid caps col">
          <div>NO FACTS MATCH</div>
          <div class="sub">TRY WIDENING THE WINDOW, OR START FROM ONE OF THE SUGGESTIONS ON THE LEFT.</div>
        </div>
      {:else if res}
        {#if view === 'timeline'}
          <FactTimeline result={res} selected={selFact?.id ?? null} onselect={pickFact} onhover={(f) => (hoverFact = f)} />
        {:else if view === 'graph'}
          <div class="graph">
            <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
              {#each res.entities as e, i}
                {@const a = (i / Math.max(1, res.entities.length)) * Math.PI * 2}
                {@const x = 50 + Math.cos(a) * 30}
                {@const y = 50 + Math.sin(a) * 30}
                {#each res.facts.filter((f) => f.subj === e.uid && f.obj != null) as f}
                  {@const j = res.entities.findIndex((z) => z.uid === f.obj)}
                  {#if j >= 0}
                    {@const b = (j / Math.max(1, res.entities.length)) * Math.PI * 2}
                    <line class="edge" x1={x} y1={y} x2={50 + Math.cos(b) * 30} y2={50 + Math.sin(b) * 30}
                      style={`opacity:${0.2 + f.conf * 0.6}`} />
                  {/if}
                {/each}
                <circle class="node" cx={x} cy={y} r="3.2" role="button" tabindex="0"
                  onclick={() => pickEntity(e)} onkeydown={(ev) => { if (ev.key === 'Enter') pickEntity(e) }} />
                <text class="nlabel" x={x} y={y + 7}>{(e.label || e.ref).slice(0, 16)}</text>
              {/each}
            </svg>
            <div class="ghint caps">NODES ARE ENTITIES · EDGES ARE FACTS THAT POINT AT ANOTHER ENTITY</div>
          </div>
        {:else}
          <div class="table">
            <div class="trow th caps"><span>TIME</span><span>SUBJECT</span><span>PREDICATE</span><span>OBJECT</span><span>CONF</span><span>SOURCE</span></div>
            {#each res.facts as f (f.id)}
              <button class="trow" class:on={selFact?.id === f.id} onclick={() => pickFact(f)}>
                <span>{stamp(f.valid_from)}</span>
                <span>{res.entities.find((e) => e.uid === f.subj)?.label ?? f.subj}</span>
                <span class="tp">{f.pred}</span>
                <span>{f.val ?? (f.obj != null ? `#${f.obj}` : '—')}</span>
                <span>{(f.conf * 100).toFixed(0)}%</span>
                <span class="tdim">{f.src_kind}{f.model_id ? ` · ${f.model_id}` : ''}</span>
              </button>
            {/each}
          </div>
        {/if}
      {/if}

      {#if hoverFact && !selFact}
        <div class="hovercard panel">
          <span class="hp caps">{hoverFact.pred}{hoverFact.val ? ` · ${hoverFact.val}` : ''}</span>
          <span class="hm caps">{hoverFact.src_kind}{hoverFact.model_id ? ` · ${hoverFact.model_id}` : ''} · {(hoverFact.conf * 100).toFixed(0)}%</span>
          <span class="hm caps">{stamp(hoverFact.valid_from)}</span>
        </div>
      {/if}
    </main>

    <Inspector entity={selEntity} fact={selFact} onpurge={purge}
      onclose={() => { selFact = null; selEntity = null }} />
  </div>

  <TimeDial query={q} asOf={$bedrockAsOf} oldest={(st as { oldest?: number } | null)?.oldest ?? null}
    {delta}
    onwindow={(from, to) => { bedrockQuery.set({ ...q, window: { from, to } }); run({ ...q, window: { from, to } }) }}
    onasof={(ms) => { bedrockAsOf.set(ms); run() }} />
</div>

<style>
  .bd { position: absolute; inset: 0; z-index: var(--z-panel); display: flex; flex-direction: column;
    background: radial-gradient(120% 80% at 50% 0%, #0a1016 0%, #05070a 72%);
    animation: bin 280ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes bin { from { opacity: 0; } }
  .top { display: flex; align-items: center; gap: 12px; padding: 11px 20px;
    border-bottom: 1px solid var(--hairline); font-size: var(--fs-label);
    letter-spacing: var(--tracking); background: #04070a; }
  .eyebrow { color: var(--scarlet); } .cnt { color: var(--ink-dim); font-size: 11px; } .spacer { flex: 1; }
  .seg { display: flex; border: 1px solid var(--hairline); }
  .sb { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; background: none;
    border: none; border-right: 1px solid var(--hairline); color: var(--ink-dim); font-size: 11px;
    letter-spacing: 0.12em; cursor: crosshair; }
  .sb:last-child { border-right: none; }
  .sb.on { color: var(--cyan); background: rgba(56,208,227,0.09); }
  .sb .k { border: 1px solid var(--ink-ghost); padding: 0 3px; font-size: 10px; color: var(--ink-ghost); }
  .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none;
    cursor: crosshair; font-size: 11px; letter-spacing: var(--tracking); }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }

  .body { flex: 1; min-height: 0; display: grid; grid-template-columns: 340px 1fr 320px; }
  .stage { position: relative; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }

  .mid { flex: 1; display: flex; align-items: center; justify-content: center; gap: 14px;
    color: var(--ink-dim); letter-spacing: 0.16em; font-size: 10px; position: relative; }
  .mid.col { flex-direction: column; text-align: center; padding: 0 40px; }
  .mid .sub { font-size: 11px; color: var(--ink-ghost); max-width: 560px; line-height: 1.8; }
  .mid .big { font-size: 24px; color: var(--ink); letter-spacing: var(--tracking-wide); }
  .mid .warn { color: var(--amber); font-size: 12px; letter-spacing: 0.16em; }
  .pulse { animation: pl 1.2s ease-in-out infinite; } @keyframes pl { 50% { opacity: 0.4; } }
  .scan { position: absolute; left: 0; top: 0; bottom: 0; width: 120px;
    background: linear-gradient(90deg, transparent, rgba(56,208,227,0.10), transparent);
    animation: sweep 900ms linear infinite; }
  @keyframes sweep { from { transform: translateX(-140px); } to { transform: translateX(calc(100vw)); } }
  .go { padding: 11px 22px; border: 1px solid var(--cyan); background: none; color: var(--cyan);
    font-size: 11px; letter-spacing: 0.16em; cursor: crosshair; }
  .go:hover:not(:disabled) { background: var(--cyan); color: #04070a; }
  .go:disabled { opacity: 0.5; }
  .bar { position: relative; width: 280px; height: 4px; background: var(--hairline); }
  .bar .fill { position: absolute; inset: 0 auto 0 0; background: var(--cyan); transition: width 400ms; }

  .graph { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .graph svg { flex: 1; min-height: 0; }
  .edge { stroke: var(--cyan); stroke-width: 0.3; }
  .node { fill: #04070a; stroke: var(--cyan); stroke-width: 0.7; cursor: crosshair; }
  .node:hover { fill: var(--cyan); }
  .nlabel { fill: var(--ink-dim); font-size: 2.4px; text-anchor: middle; font-family: var(--font-mono); }
  .ghint { padding: 8px 12px; font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.14em;
    border-top: 1px solid var(--hairline); }

  .table { flex: 1; min-height: 0; overflow-y: auto; }
  .trow { display: grid; grid-template-columns: 118px 1fr 110px 110px 44px 110px; gap: 10px;
    width: 100%; text-align: left; padding: 5px 12px; background: none; border: none;
    border-bottom: 1px solid rgba(236,236,236,0.05); color: var(--ink-dim); font-size: 11px;
    letter-spacing: 0.05em; cursor: crosshair; }
  .trow.th { color: var(--ink-ghost); font-size: 10px; letter-spacing: 0.16em; position: sticky;
    top: 0; background: #05070a; cursor: default; }
  .trow:hover:not(.th) { background: rgba(56,208,227,0.05); }
  .trow.on { background: rgba(56,208,227,0.1); color: var(--ink); }
  .tp { color: var(--cyan); } .tdim { color: var(--ink-ghost); }

  .hovercard { position: absolute; right: 14px; bottom: 40px; padding: 8px 10px;
    display: flex; flex-direction: column; gap: 3px; pointer-events: none; }
  .hp { font-size: 11px; color: var(--ink); letter-spacing: 0.1em; }
  .hm { font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.12em; }

  @media (max-width: 1200px) { .body { grid-template-columns: 280px 1fr 260px; } }
</style>
