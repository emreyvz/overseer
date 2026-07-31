<script lang="ts">
  // Smart suggestions advisor: a triage cockpit, not a list. A prioritised QUEUE on the left
  // (highest-impact coverage gaps first); the SELECTED suggestion fills the stage on the right
  // with its own live camera, the evidence behind it (the camera's DNA, how often the behaviour
  // was seen, its reputation) and one-click actions. A coverage gauge fills as gaps are closed.
  // Two kinds of recommendation:
  //   · alert coverage: a behaviour a camera keeps seeing with no rule yet; CREATE ALERT adds it.
  //   · camera improvement: a health advisory drawn from the reputation signals.
  import { onDestroy, onMount } from 'svelte'
  import { api, type Suggestion, type CameraDna } from '../../lib/api'
  import { cameras, activeCam, mode, stage, zoneEditor, triggerGlitch } from '../../lib/stores'
  import { sendCommand } from '../../lib/ws'
  import { SIM } from '../../lib/sim'
  import { sfx } from '../../lib/audio'
  import LiveThumb from '../LiveThumb.svelte'

  // behaviours defined BY a zone: for these we also offer "draw the zone", opening the POV zone
  // editor on that very camera so the operator marks the area the alert should watch.
  const ZONE_BEHAVIORS = new Set(['LOITERING', 'LINE_CROSS', 'RESTRICTED', 'TAILGATING'])
  const SEV_W: Record<string, number> = { critical: 3, warning: 2, info: 1 }

  let { onclose }: { onclose: () => void } = $props()

  let items = $state<Suggestion[]>([])
  let dna = $state<Record<string, CameraDna>>({})   // by camera NAME
  let loading = $state(true)
  let busy = $state<string | null>(null)
  let done = $state<string[]>([])                    // titles of alert rules added this session
  let selKey = $state<string | null>(null)           // selected suggestion, tracked by title

  // impact score: severity dominates, recency/frequency breaks ties
  const score = (s: Suggestion): number => (SEV_W[s.rule?.severity ?? 'info'] ?? 1) * 1000 + (s.count ?? 0)
  const alertQ = $derived(items.filter((s) => s.kind === 'alert').sort((a, b) => score(b) - score(a)))
  const camQ = $derived(items.filter((s) => s.kind === 'camera'))
  const flat = $derived([...alertQ, ...camQ])
  const selected = $derived(flat.find((s) => s.title === selKey) ?? flat[0] ?? null)

  // coverage: of every alert-coverage gap we found, how many are now closed
  const totalAlerts = $derived(items.filter((s) => s.kind === 'alert').length + done.length)
  const coverage = $derived(totalAlerts ? Math.round((100 * done.length) / totalAlerts) : 100)
  const C = 2 * Math.PI * 26                          // ring circumference (r = 26)

  const camId = (name: string): string | null => ($cameras.find((c) => c.name === name)?.id ?? null)
  const tags = (name: string): string[] => dna[name]?.dna ?? []
  const rep = (name: string): number | null => (dna[name] ? dna[name].reputation : null)
  const impact = (s: Suggestion): string =>
    s.kind !== 'alert' ? 'ADVISORY' : s.rule?.severity === 'critical' ? 'HIGH' : (s.count ?? 0) >= 8 ? 'HIGH' : 'MEDIUM'

  async function load() {
    loading = true
    try { items = (await api.suggestions()).suggestions } catch { items = [] }
    try {
      const d = (await api.cameraDna()).cameras
      dna = Object.fromEntries(d.map((c) => [String(c.name), c]))
    } catch { /* dna optional */ }
    selKey = flat[0]?.title ?? null
    loading = false
  }

  function select(s: Suggestion) { if (s.title !== selKey) { sfx('click', { volume: 0.2 }); selKey = s.title } }

  async function accept(s: Suggestion | null) {
    if (!s || busy || !s.rule || s.kind !== 'alert') return
    busy = s.title; sfx('sonar')
    try {
      await api.addAlertRule(s.rule)
      done = [...done, s.title]
      triggerGlitch(120)
      // auto-advance to the next open gap before this card leaves the queue
      const i = flat.findIndex((x) => x.title === s.title)
      const nextOpen = flat.slice(i + 1).find((x) => !done.includes(x.title)) ?? flat.slice(0, i).find((x) => !done.includes(x.title))
      setTimeout(() => {
        items = items.filter((x) => x.title !== s.title)
        selKey = nextOpen?.title ?? flat.find((x) => x.title !== s.title)?.title ?? null
      }, 750)
    } catch { /* offline: leave the card so the operator can retry */ }
    busy = null
  }

  const isZone = (s: Suggestion | null): boolean => !!s?.rule && ZONE_BEHAVIORS.has(s.rule.event_type)
  // observe the suggestion's camera live and open the zone editor there
  function drawZone(s: Suggestion | null) {
    const id = s && camId(s.cam)
    if (!s || !id) return
    sfx('whoosh'); triggerGlitch(160)
    activeCam.set(id)
    if (!SIM) sendCommand(`connect:${s.cam}`)
    mode.set('pov'); stage.set('live'); zoneEditor.set(true)
    onclose()
  }

  function step(dir: number) {
    if (!flat.length) return
    const i = Math.max(0, flat.findIndex((s) => s.title === selKey))
    selKey = flat[(i + dir + flat.length) % flat.length].title
    sfx('click', { volume: 0.15 })
  }

  function onkey(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.stopPropagation(); onclose(); return }
    if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); step(1) }
    else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); step(-1) }
    else if (e.key === 'Enter') { e.preventDefault(); accept(selected) }
    else if (e.key === 'z' && isZone(selected)) { e.preventDefault(); drawZone(selected) }
  }
  onMount(() => { sfx('sonar'); load(); window.addEventListener('keydown', onkey, true) })
  onDestroy(() => window.removeEventListener('keydown', onkey, true))
</script>

<div class="ss" role="dialog" aria-label="Smart suggestions">
  <header class="top caps">
    <span class="eyebrow">◈ SMART SUGGESTIONS</span>
    <span class="cnt">{items.length} OPEN</span>
    <span class="spacer"></span>
    <!-- coverage gauge: fills as alert-coverage gaps are closed -->
    <div class="gauge" title="Alert-coverage gaps closed this session">
      <svg viewBox="0 0 60 60">
        <circle class="track" cx="30" cy="30" r="26" />
        <circle class="prog" cx="30" cy="30" r="26"
          stroke-dasharray={C} stroke-dashoffset={C * (1 - coverage / 100)} />
      </svg>
      <span class="gval">{coverage}%</span>
    </div>
    <span class="glabel caps">COVERAGE</span>
    <button class="ref caps" onclick={load}>↻ RESCAN</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  {#if loading}
    <div class="empty caps"><span class="pulse">READING THE RECORD_</span></div>
  {:else if items.length === 0}
    <div class="empty caps">
      <div class="okring">✓</div>
      <div>NOTHING TO SUGGEST · COVERAGE LOOKS HEALTHY</div>
    </div>
  {:else}
    <div class="body">
      <!-- QUEUE: prioritised, keyboard-navigable -->
      <aside class="queue">
        {#if alertQ.length}
          <div class="qgrp caps"><span class="qd alert"></span>ALERT COVERAGE<span class="qgn">{alertQ.length}</span></div>
          {#each alertQ as s (s.title)}
            <button class="qrow" class:on={selected?.title === s.title} class:added={done.includes(s.title)} onclick={() => select(s)}>
              <span class="pdot sev-{s.rule?.severity ?? 'info'}"></span>
              <span class="qmid">
                <span class="qttl">{s.title}</span>
                <span class="qcam caps">◉ {s.cam}</span>
              </span>
              {#if done.includes(s.title)}<span class="qok">✓</span>
              {:else if s.count}<span class="qn caps">{s.count}×</span>{/if}
            </button>
          {/each}
        {/if}
        {#if camQ.length}
          <div class="qgrp caps"><span class="qd cam"></span>CAMERA IMPROVEMENTS<span class="qgn">{camQ.length}</span></div>
          {#each camQ as s (s.title)}
            <button class="qrow" class:on={selected?.title === s.title} onclick={() => select(s)}>
              <span class="pdot cam"></span>
              <span class="qmid">
                <span class="qttl">{s.title}</span>
                <span class="qcam caps">◉ {s.cam}</span>
              </span>
            </button>
          {/each}
        {/if}
      </aside>

      <!-- STAGE: the selected suggestion in full -->
      <main class="stage">
        {#if selected}
          {#key selected.title}
          <div class="hero">
            {#if camId(selected.cam)}<LiveThumb id={camId(selected.cam)!} fps={6} />{:else}<div class="nolive caps">NO FEED</div>{/if}
            <span class="camtag caps">◉ {selected.cam}</span>
            <span class="impact caps imp-{impact(selected).toLowerCase()}">{impact(selected)} IMPACT</span>
            {#if selected.count}<span class="seen caps">{selected.count}× SEEN</span>{/if}
            {#if selected.kind === 'alert'}
              <span class="sev caps {selected.rule?.severity}">{selected.rule?.severity === 'critical' ? '● CRITICAL' : '● WARNING'}</span>
            {/if}
          </div>

          <div class="detail">
            <div class="kind caps">{selected.kind === 'alert' ? 'ALERT COVERAGE GAP' : 'CAMERA ADVISORY'}</div>
            <h2 class="stitle">{selected.title}</h2>
            <p class="swhy">{selected.why}</p>

            <div class="evidence">
              {#if rep(selected.cam) !== null}
                <div class="ev">
                  <div class="evk caps">CAMERA REPUTATION</div>
                  <div class="bar"><span class="fill" style={`width:${Math.round((rep(selected.cam) as number) * 100)}%`}></span></div>
                  <div class="evv caps">{Math.round((rep(selected.cam) as number) * 100)}%</div>
                </div>
              {/if}
              {#if tags(selected.cam).length}
                <div class="ev">
                  <div class="evk caps">WHAT HAPPENS HERE</div>
                  <div class="chips">{#each tags(selected.cam) as t}<span class="chip caps">{t}</span>{/each}</div>
                </div>
              {/if}
            </div>

            <div class="actions">
              {#if selected.kind === 'alert'}
                {#if done.includes(selected.title)}
                  <span class="ok caps">✓ RULE ADDED · WATCHING NOW</span>
                {:else}
                  <button class="go caps" disabled={busy === selected.title} onclick={() => accept(selected)}>
                    {busy === selected.title ? 'ADDING_' : '+ CREATE ALERT'}
                  </button>
                  {#if isZone(selected) && camId(selected.cam)}
                    <button class="zone caps" onclick={() => drawZone(selected)} title="Draw the watch zone on this camera">◱ DRAW ZONE</button>
                  {/if}
                {/if}
              {:else}
                <span class="advnote caps">HEALTH ADVISORY · NO RULE TO ADD</span>
                {#if camId(selected.cam)}
                  <button class="zone caps" onclick={() => drawZone(selected)} title="Open this camera">◉ OPEN CAMERA</button>
                {/if}
              {/if}
            </div>
            <div class="hint caps">↑↓ NAVIGATE · ⏎ CREATE · {isZone(selected) ? 'Z DRAW ZONE · ' : ''}ESC CLOSE</div>
          </div>
          {/key}
        {:else}
          <div class="empty caps">SELECT A RECOMMENDATION</div>
        {/if}
      </main>
    </div>
  {/if}
</div>

<style>
  .ss { position: fixed; inset: 0; z-index: var(--z-boot); background: radial-gradient(120% 80% at 50% 0%, #0a1016 0%, #05070a 72%);
    color: var(--ink); display: flex; flex-direction: column; overflow: hidden; animation: ssin 300ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes ssin { from { opacity: 0; } }
  .top { display: flex; align-items: center; gap: 12px; padding: 11px 22px; border-bottom: 1px solid var(--hairline);
    font-size: var(--fs-label); letter-spacing: var(--tracking); background: #04070a; z-index: 2; }
  .eyebrow { color: var(--scarlet); } .cnt { color: var(--ink-dim); font-size: 9px; } .spacer { flex: 1; }
  /* coverage gauge */
  .gauge { position: relative; width: 40px; height: 40px; }
  .gauge svg { width: 40px; height: 40px; transform: rotate(-90deg); }
  .gauge .track { fill: none; stroke: var(--hairline); stroke-width: 4; }
  .gauge .prog { fill: none; stroke: var(--cyan); stroke-width: 4; stroke-linecap: round;
    transition: stroke-dashoffset 700ms cubic-bezier(0.16, 1, 0.3, 1); filter: drop-shadow(0 0 4px color-mix(in srgb, var(--cyan) 60%, transparent)); }
  .gval { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 9px; color: var(--cyan); letter-spacing: 0; }
  .glabel { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.16em; margin-left: -4px; }
  .ref, .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: pointer; font-size: 9px; letter-spacing: var(--tracking); }
  .ref:hover { border-color: var(--cyan); color: var(--cyan); } .x:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .empty { flex: 1; display: flex; flex-direction: column; gap: 16px; align-items: center; justify-content: center; color: var(--ink-dim); letter-spacing: 0.16em; text-align: center; padding: 0 40px; }
  .okring { width: 54px; height: 54px; border: 1px solid color-mix(in srgb, var(--cyan) 50%, transparent); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--cyan); font-size: 22px; }
  .pulse { animation: pulse 1.2s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.4; } }

  /* master-detail body */
  .body { flex: 1; min-height: 0; display: grid; grid-template-columns: 320px 1fr; }
  .queue { border-right: 1px solid var(--hairline); overflow-y: auto; padding: 12px 10px 40px; background: rgba(4,7,10,0.4); }
  .qgrp { display: flex; align-items: center; gap: 7px; font-size: 8px; color: var(--ink-dim); letter-spacing: 0.16em; margin: 14px 6px 7px; }
  .qgrp:first-child { margin-top: 4px; }
  .qd { width: 6px; height: 6px; border-radius: 50%; } .qd.alert { background: var(--scarlet); box-shadow: 0 0 6px var(--scarlet); } .qd.cam { background: var(--cyan); box-shadow: 0 0 6px var(--cyan); }
  .qgn { margin-left: auto; color: var(--ink-ghost); }
  .qrow { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left; padding: 9px 10px; margin-bottom: 3px; background: none;
    border: 1px solid transparent; border-left: 2px solid transparent; cursor: pointer; transition: background 140ms, border-color 140ms; }
  .qrow:hover { background: rgba(56,208,227,0.05); }
  .qrow.on { background: rgba(56,208,227,0.09); border-color: var(--hairline); border-left-color: var(--cyan); }
  .qrow.added { opacity: 0.45; }
  .pdot { width: 7px; height: 7px; border-radius: 50%; flex: 0 0 auto; }
  .pdot.sev-critical { background: var(--scarlet); box-shadow: 0 0 6px var(--scarlet); }
  .pdot.sev-warning { background: #e8a13a; box-shadow: 0 0 6px #e8a13a; }
  .pdot.sev-info, .pdot.cam { background: var(--cyan); }
  .qmid { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
  .qttl { font-size: 11px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .qrow.on .qttl { color: var(--ink); } .qrow:not(.on) .qttl { color: var(--ink-dim); }
  .qcam { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.1em; }
  .qn { font-size: 8px; color: var(--cyan); letter-spacing: 0.06em; flex: 0 0 auto; }
  .qok { color: var(--cyan); font-size: 11px; flex: 0 0 auto; }

  /* stage */
  .stage { min-width: 0; overflow-y: auto; display: flex; flex-direction: column; }
  .hero { position: relative; aspect-ratio: 16 / 9; max-height: 46vh; background: #05070a; overflow: hidden; border-bottom: 1px solid var(--hairline); animation: fade 340ms both; }
  @keyframes fade { from { opacity: 0; } }
  .nolive { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: 10px; letter-spacing: 0.14em; }
  .camtag { position: absolute; top: 10px; left: 12px; font-size: 9px; color: var(--ink); letter-spacing: 0.14em; text-shadow: 0 0 5px #000; }
  .impact { position: absolute; top: 10px; right: 12px; font-size: 8px; letter-spacing: 0.14em; padding: 3px 7px; background: rgba(4,7,10,0.72); text-shadow: 0 0 5px #000; }
  .imp-high { color: var(--scarlet); } .imp-medium { color: #e8a13a; } .imp-advisory { color: var(--ink-ghost); }
  .seen { position: absolute; bottom: 10px; left: 12px; font-size: 9px; color: var(--cyan); background: rgba(4,7,10,0.72); padding: 3px 8px; letter-spacing: 0.1em; }
  .sev { position: absolute; bottom: 10px; right: 12px; font-size: 9px; letter-spacing: 0.12em; text-shadow: 0 0 5px #000; }
  .sev.critical { color: var(--scarlet); } .sev.warning { color: #e8a13a; }
  .detail { padding: 20px 26px 22px; display: flex; flex-direction: column; gap: 11px; max-width: 760px; animation: rise 380ms both cubic-bezier(0.16, 1, 0.3, 1); }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
  .kind { font-size: 8px; color: var(--scarlet); letter-spacing: 0.2em; }
  .stitle { font-size: 22px; font-weight: 500; color: var(--ink); margin: 0; letter-spacing: 0.01em; }
  .swhy { font-size: 12px; color: var(--ink-dim); line-height: 1.6; margin: 0; max-width: 640px; }
  .evidence { display: flex; flex-direction: column; gap: 12px; margin: 4px 0 2px; }
  .ev { display: flex; flex-direction: column; gap: 5px; }
  .evk { font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.16em; }
  .bar { position: relative; height: 5px; background: var(--hairline); overflow: hidden; max-width: 340px; }
  .bar .fill { position: absolute; inset: 0 auto 0 0; background: var(--cyan); box-shadow: 0 0 8px color-mix(in srgb, var(--cyan) 70%, transparent); transition: width 500ms; }
  .evv { font-size: 8px; color: var(--cyan); letter-spacing: 0.06em; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { font-size: 8px; color: var(--ink-dim); border: 1px solid var(--hairline); padding: 3px 7px; letter-spacing: 0.08em; }
  .actions { display: flex; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
  .go { padding: 11px 22px; border: 1px solid var(--cyan); background: none; color: var(--cyan); cursor: pointer; font-size: 11px; letter-spacing: 0.16em; transition: background 140ms, color 140ms; }
  .go:hover:not(:disabled) { background: var(--cyan); color: #04070a; box-shadow: 0 0 18px color-mix(in srgb, var(--cyan) 40%, transparent); }
  .go:disabled { opacity: 0.5; cursor: default; }
  .zone { padding: 11px 18px; border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim); cursor: pointer; font-size: 11px; letter-spacing: 0.16em; white-space: nowrap; }
  .zone:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .ok { color: var(--cyan); font-size: 11px; letter-spacing: 0.16em; padding: 11px 0; }
  .advnote { color: var(--ink-ghost); font-size: 10px; letter-spacing: 0.14em; padding: 11px 0; }
  .hint { margin-top: 12px; font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.14em; }

  @media (max-width: 720px) {
    .body { grid-template-columns: 1fr; grid-template-rows: 40% 1fr; }
    .queue { border-right: none; border-bottom: 1px solid var(--hairline); }
  }
</style>
