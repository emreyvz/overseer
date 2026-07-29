<script lang="ts">
  // Smart suggestions advisor. Proactive, evidence-backed recommendations the system derives
  // from what it has actually observed: alert rules for behaviours a camera keeps seeing but
  // has no rule for (one-click ACCEPT creates the rule), and camera-health improvements from
  // the reputation signals (advisory only). Nothing is invented — every card states its "why".
  import { onDestroy, onMount } from 'svelte'
  import { api, type Suggestion } from '../../lib/api'
  import { sfx } from '../../lib/audio'

  let { onclose }: { onclose: () => void } = $props()

  let items = $state<Suggestion[]>([])
  let loading = $state(true)
  let busy = $state<string | null>(null)
  let done = $state<string[]>([])   // titles of accepted alerts, kept for the "✓ ADDED" toast

  const alerts = $derived(items.filter((s) => s.kind === 'alert'))
  const cams = $derived(items.filter((s) => s.kind === 'camera'))

  async function load() {
    loading = true
    try { items = (await api.suggestions()).suggestions } catch { items = [] }
    loading = false
  }

  async function accept(s: Suggestion) {
    if (busy || !s.rule) return
    busy = s.title; sfx('sonar')
    try {
      await api.addAlertRule(s.rule)
      done = [...done, s.title]
      // the rule now exists, so this suggestion is resolved — drop it after the toast beat
      setTimeout(() => { items = items.filter((x) => x !== s) }, 900)
    } catch { /* offline — leave the card so the operator can retry */ }
    busy = null
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
    <div class="empty caps">READING THE RECORD_</div>
  {:else if items.length === 0}
    <div class="empty caps">NOTHING TO SUGGEST · COVERAGE LOOKS HEALTHY</div>
  {:else}
    <div class="scroll">
      {#if alerts.length}
        <section>
          <h3 class="grp caps"><span class="dot alert"></span>ALERT COVERAGE · <span class="gc">{alerts.length}</span></h3>
          <div class="list">
            {#each alerts as s (s.title)}
              <div class="card alert" class:added={done.includes(s.title)}>
                <div class="body">
                  <div class="hd caps">
                    <span class="sev {s.rule?.severity}">{s.rule?.severity === 'critical' ? '● CRITICAL' : '● WARNING'}</span>
                    <span class="cam">{s.cam}</span>
                    {#if s.count}<span class="n">{s.count}× SEEN</span>{/if}
                  </div>
                  <div class="ttl">{s.title}</div>
                  <div class="why">{s.why}</div>
                </div>
                {#if done.includes(s.title)}
                  <span class="ok caps">✓ RULE ADDED</span>
                {:else}
                  <button class="go caps" disabled={busy === s.title} onclick={() => accept(s)}>
                    {busy === s.title ? 'ADDING_' : '+ CREATE ALERT'}
                  </button>
                {/if}
              </div>
            {/each}
          </div>
        </section>
      {/if}

      {#if cams.length}
        <section>
          <h3 class="grp caps"><span class="dot cam"></span>CAMERA IMPROVEMENTS · <span class="gc">{cams.length}</span></h3>
          <div class="list">
            {#each cams as s (s.title)}
              <div class="card cam">
                <div class="body">
                  <div class="hd caps"><span class="cam">{s.cam}</span><span class="adv">ADVISORY</span></div>
                  <div class="ttl">{s.title}</div>
                  <div class="why">{s.why}</div>
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
    font-size: var(--fs-label); letter-spacing: var(--tracking); background: #04070a; }
  .eyebrow { color: var(--scarlet); } .cnt { color: var(--ink-dim); font-size: 9px; } .spacer { flex: 1; }
  .ref, .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: pointer; font-size: 9px; letter-spacing: var(--tracking); }
  .ref:hover { border-color: var(--cyan); color: var(--cyan); } .x:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .empty { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--ink-dim); letter-spacing: 0.16em; text-align: center; padding: 0 40px; }
  .scroll { flex: 1; min-height: 0; overflow: auto; padding: 20px 18px 40px; max-width: 720px; margin: 0 auto; width: 100%; }
  section { margin-bottom: 22px; }
  .grp { display: flex; align-items: center; gap: 8px; font-size: 9px; color: var(--ink-dim); letter-spacing: 0.18em; margin: 0 0 10px; }
  .gc { color: var(--cyan); } .dot { width: 6px; height: 6px; border-radius: 50%; }
  .dot.alert { background: var(--scarlet); box-shadow: 0 0 8px var(--scarlet); }
  .dot.cam { background: var(--cyan); box-shadow: 0 0 8px var(--cyan); }
  .list { display: flex; flex-direction: column; gap: 10px; }
  .card { display: flex; align-items: center; gap: 14px; border: 1px solid var(--hairline); background: rgba(7,11,14,0.6);
    padding: 13px 15px; animation: rise 320ms both cubic-bezier(0.16, 1, 0.3, 1); border-left-width: 2px; }
  .card.alert { border-left-color: var(--scarlet); } .card.cam { border-left-color: var(--cyan); }
  .card.added { opacity: 0.55; border-left-color: var(--ink-dim); }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
  .body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
  .hd { display: flex; align-items: center; gap: 10px; font-size: 8px; letter-spacing: 0.14em; }
  .sev.critical { color: var(--scarlet); } .sev.warning { color: #e8a13a; }
  .cam { color: var(--ink); font-size: 8px; letter-spacing: 0.16em; }
  .n { color: var(--ink-ghost); } .adv { color: var(--ink-ghost); margin-left: auto; }
  .ttl { font-size: 13px; color: var(--ink); letter-spacing: 0.01em; }
  .why { font-size: 10px; color: var(--ink-dim); line-height: 1.45; }
  .go { flex-shrink: 0; padding: 9px 14px; border: 1px solid var(--cyan); background: none; color: var(--cyan);
    cursor: pointer; font-size: 9px; letter-spacing: 0.14em; white-space: nowrap; }
  .go:hover:not(:disabled) { background: var(--cyan); color: #04070a; }
  .go:disabled { opacity: 0.5; cursor: default; }
  .ok { flex-shrink: 0; color: var(--cyan); font-size: 9px; letter-spacing: 0.14em; white-space: nowrap; }
</style>
