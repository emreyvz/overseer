<script lang="ts">
  // Smart suggestions advisor — a visual, evidence-backed briefing. Each recommendation is shown
  // ON its camera (a live thumbnail), with the camera's own DNA tags ("what happens here"), so
  // the operator sees WHERE and WHY, not just a line of text. Two kinds:
  //   · alert coverage — a behaviour a camera keeps seeing with no rule yet; one-click CREATE
  //     ALERT adds the rule (and the suggestion resolves).
  //   · camera improvement — a health advisory from the reputation signals.
  import { onDestroy, onMount } from 'svelte'
  import { api, type Suggestion, type CameraDna } from '../../lib/api'
  import { cameras, activeCam, mode, stage, zoneEditor, triggerGlitch } from '../../lib/stores'
  import { sendCommand } from '../../lib/ws'
  import { SIM } from '../../lib/sim'
  import { sfx } from '../../lib/audio'
  import LiveThumb from '../LiveThumb.svelte'

  // behaviours that are defined BY a zone — for these we also offer "draw the zone", opening the
  // POV zone editor on that very camera so the operator marks the area the alert should watch.
  const ZONE_BEHAVIORS = new Set(['LOITERING', 'LINE_CROSS', 'RESTRICTED', 'TAILGATING'])

  let { onclose }: { onclose: () => void } = $props()

  let items = $state<Suggestion[]>([])
  let dna = $state<Record<string, CameraDna>>({})   // by camera NAME
  let loading = $state(true)
  let busy = $state<string | null>(null)
  let done = $state<string[]>([])

  const alerts = $derived(items.filter((s) => s.kind === 'alert'))
  const cams = $derived(items.filter((s) => s.kind === 'camera'))

  const camId = (name: string): string | null => ($cameras.find((c) => c.name === name)?.id ?? null)
  const tags = (name: string): string[] => dna[name]?.dna ?? []
  const rep = (name: string): number | null => (dna[name] ? dna[name].reputation : null)

  async function load() {
    loading = true
    try { items = (await api.suggestions()).suggestions } catch { items = [] }
    try {
      const d = (await api.cameraDna()).cameras
      dna = Object.fromEntries(d.map((c) => [String(c.name), c]))
    } catch { /* dna optional */ }
    loading = false
  }

  async function accept(s: Suggestion) {
    if (busy || !s.rule) return
    busy = s.title; sfx('sonar')
    try {
      await api.addAlertRule(s.rule)
      done = [...done, s.title]
      setTimeout(() => { items = items.filter((x) => x !== s) }, 900)
    } catch { /* offline — leave the card so the operator can retry */ }
    busy = null
  }

  const isZone = (s: Suggestion) => !!s.rule && ZONE_BEHAVIORS.has(s.rule.event_type)
  // observe the suggestion's camera live and open the zone editor there, so the operator can
  // draw exactly where this behaviour should be watched.
  function drawZone(s: Suggestion) {
    const id = camId(s.cam)
    if (!id) return
    sfx('whoosh'); triggerGlitch(160)
    activeCam.set(id)
    if (!SIM) sendCommand(`connect:${s.cam}`)
    mode.set('pov'); stage.set('live'); zoneEditor.set(true)
    onclose()
  }

  function onkey(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); onclose() } }
  onMount(() => { sfx('sonar'); load(); window.addEventListener('keydown', onkey, true) })
  onDestroy(() => window.removeEventListener('keydown', onkey, true))
</script>

<div class="ss" role="dialog" aria-label="Smart suggestions">
  <header class="top caps">
    <span class="eyebrow">◈ SMART SUGGESTIONS</span>
    <span class="cnt">{items.length} RECOMMENDATION{items.length === 1 ? '' : 'S'}</span>
    <span class="spacer"></span>
    <button class="ref caps" onclick={load}>↻ RESCAN</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  {#if loading}
    <div class="empty caps"><span class="pulse">READING THE RECORD_</span></div>
  {:else if items.length === 0}
    <div class="empty caps">NOTHING TO SUGGEST · COVERAGE LOOKS HEALTHY</div>
  {:else}
    <div class="scroll">
      {#if alerts.length}
        <section>
          <h3 class="grp caps"><span class="dot alert"></span>ALERT COVERAGE · <span class="gc">{alerts.length}</span>
            <span class="gsub">behaviours a camera keeps seeing with no rule yet</span></h3>
          <div class="grid">
            {#each alerts as s (s.title)}
              <div class="scard" class:added={done.includes(s.title)}>
                <div class="live">
                  {#if camId(s.cam)}<LiveThumb id={camId(s.cam)!} fps={4} />{:else}<div class="nolive caps">NO FEED</div>{/if}
                  <span class="camtag caps">◉ {s.cam}</span>
                  {#if s.count}<span class="cntbadge caps">{s.count}× SEEN</span>{/if}
                  <span class="sev {s.rule?.severity}">{s.rule?.severity === 'critical' ? '● CRITICAL' : '● WARNING'}</span>
                </div>
                <div class="info">
                  <div class="ttl">{s.title}</div>
                  <div class="why">{s.why}</div>
                  {#if tags(s.cam).length || rep(s.cam) !== null}
                    <div class="dna caps">
                      {#if rep(s.cam) !== null}<span class="rep">REP {Math.round((rep(s.cam) as number) * 100)}%</span>{/if}
                      {#each tags(s.cam) as t}<span class="tag">{t}</span>{/each}
                    </div>
                  {/if}
                  {#if done.includes(s.title)}
                    <span class="ok caps">✓ RULE ADDED</span>
                  {:else}
                    <div class="acts">
                      <button class="go caps" disabled={busy === s.title} onclick={() => accept(s)}>
                        {busy === s.title ? 'ADDING_' : '+ CREATE ALERT'}
                      </button>
                      {#if isZone(s) && camId(s.cam)}
                        <button class="zone caps" onclick={() => drawZone(s)} title="Draw the watch zone on this camera">◱ DRAW ZONE</button>
                      {/if}
                    </div>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        </section>
      {/if}

      {#if cams.length}
        <section>
          <h3 class="grp caps"><span class="dot cam"></span>CAMERA IMPROVEMENTS · <span class="gc">{cams.length}</span>
            <span class="gsub">health advisories from each camera's own signals</span></h3>
          <div class="grid">
            {#each cams as s (s.title)}
              <div class="scard">
                <div class="live">
                  {#if camId(s.cam)}<LiveThumb id={camId(s.cam)!} fps={4} />{:else}<div class="nolive caps">NO FEED</div>{/if}
                  <span class="camtag caps">◉ {s.cam}</span>
                  <span class="advtag caps">ADVISORY</span>
                </div>
                <div class="info">
                  <div class="ttl">{s.title}</div>
                  <div class="why">{s.why}</div>
                  {#if tags(s.cam).length}
                    <div class="dna caps">{#each tags(s.cam) as t}<span class="tag">{t}</span>{/each}</div>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        </section>
      {/if}
    </div>
  {/if}
</div>

<style>
  .ss { position: fixed; inset: 0; z-index: var(--z-boot); background: radial-gradient(120% 80% at 50% 0%, #0a1016 0%, #05070a 72%);
    color: var(--ink); display: flex; flex-direction: column; overflow: hidden; animation: ssin 300ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes ssin { from { opacity: 0; } }
  .top { display: flex; align-items: center; gap: 12px; padding: 13px 22px; border-bottom: 1px solid var(--hairline);
    font-size: var(--fs-label); letter-spacing: var(--tracking); background: #04070a; z-index: 2; }
  .eyebrow { color: var(--scarlet); } .cnt { color: var(--ink-dim); font-size: 9px; } .spacer { flex: 1; }
  .ref, .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: pointer; font-size: 9px; letter-spacing: var(--tracking); }
  .ref:hover { border-color: var(--cyan); color: var(--cyan); } .x:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .empty { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--ink-dim); letter-spacing: 0.16em; text-align: center; padding: 0 40px; }
  .pulse { animation: pulse 1.2s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.4; } }
  .scroll { flex: 1; min-height: 0; overflow: auto; padding: 20px 22px 44px; max-width: 1200px; margin: 0 auto; width: 100%; }
  section { margin-bottom: 26px; }
  .grp { display: flex; align-items: baseline; gap: 8px; font-size: 9px; color: var(--ink-dim); letter-spacing: 0.18em; margin: 0 0 12px; }
  .gc { color: var(--cyan); } .gsub { color: var(--ink-ghost); font-size: 8px; letter-spacing: 0.06em; text-transform: none; }
  .dot { width: 6px; height: 6px; border-radius: 50%; align-self: center; }
  .dot.alert { background: var(--scarlet); box-shadow: 0 0 8px var(--scarlet); }
  .dot.cam { background: var(--cyan); box-shadow: 0 0 8px var(--cyan); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
  .scard { border: 1px solid var(--hairline); background: rgba(7,11,14,0.6); overflow: hidden;
    animation: rise 340ms both cubic-bezier(0.16, 1, 0.3, 1); display: flex; flex-direction: column; transition: opacity 400ms; }
  .scard.added { opacity: 0.5; }
  @keyframes rise { from { opacity: 0; transform: translateY(10px); } }
  /* the live camera the suggestion is about */
  .live { position: relative; aspect-ratio: 16 / 9; background: #05070a; overflow: hidden; }
  .nolive { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: 9px; letter-spacing: 0.14em; }
  .camtag { position: absolute; top: 7px; left: 8px; font-size: 8px; color: var(--ink); letter-spacing: 0.14em; text-shadow: 0 0 5px #000; }
  .cntbadge { position: absolute; bottom: 7px; left: 8px; font-size: 8px; color: var(--cyan); background: rgba(4,7,10,0.7); padding: 2px 6px; letter-spacing: 0.1em; }
  .sev { position: absolute; top: 7px; right: 8px; font-size: 8px; letter-spacing: 0.12em; text-shadow: 0 0 5px #000; }
  .sev.critical { color: var(--scarlet); } .sev.warning { color: #e8a13a; }
  .advtag { position: absolute; bottom: 7px; right: 8px; font-size: 8px; color: var(--ink-ghost); background: rgba(4,7,10,0.7); padding: 2px 6px; letter-spacing: 0.12em; }
  .info { padding: 12px 13px 13px; display: flex; flex-direction: column; gap: 6px; flex: 1; }
  .ttl { font-size: 13px; color: var(--ink); }
  .why { font-size: 10px; color: var(--ink-dim); line-height: 1.45; flex: 1; }
  .dna { display: flex; flex-wrap: wrap; gap: 5px; }
  .rep { font-size: 7px; color: var(--cyan); border: 1px solid color-mix(in srgb, var(--cyan) 40%, transparent); padding: 2px 5px; letter-spacing: 0.1em; }
  .tag { font-size: 7px; color: var(--ink-dim); border: 1px solid var(--hairline); padding: 2px 5px; letter-spacing: 0.08em; }
  .acts { display: flex; gap: 8px; margin-top: 4px; }
  .go { flex: 1; padding: 9px; border: 1px solid var(--cyan); background: none; color: var(--cyan); cursor: pointer; font-size: 10px; letter-spacing: 0.14em; }
  .go:hover:not(:disabled) { background: var(--cyan); color: #04070a; }
  .go:disabled { opacity: 0.5; cursor: default; }
  .zone { padding: 9px 12px; border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim); cursor: pointer; font-size: 10px; letter-spacing: 0.14em; white-space: nowrap; }
  .zone:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .ok { margin-top: 4px; color: var(--cyan); font-size: 10px; letter-spacing: 0.14em; }
</style>
