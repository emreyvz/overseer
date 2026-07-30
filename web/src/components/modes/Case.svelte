<script lang="ts">
  // Case files (brief §6 · osint §1). Analyst-assigned, case-scoped — no auto identity.
  import { onMount } from 'svelte'
  import { sfx } from '../../lib/audio'
  import { api, type CaseRow } from '../../lib/api'
  import { investigateCase, mode, stage, pickerView, triggerGlitch } from '../../lib/stores'
  import CaseInvestigation from '../cases/CaseInvestigation.svelte'

  let cases = $state<CaseRow[]>([])
  let name = $state('')
  let offline = $state(false)
  let pendingDelete = $state<CaseRow | null>(null)   // case awaiting a styled delete confirmation

  async function load() {
    try { cases = await api.cases(); offline = false } catch { offline = true }
  }
  async function add() {
    if (!name.trim()) return
    sfx('sonar')
    try { const { id } = await api.addCase(name.trim()); name = ''; await load(); open(id) } catch { offline = true }
  }
  function open(id: number) { sfx('ping', { volume: 0.3 }); investigateCase.set(id) }
  function del(e: MouseEvent, c: CaseRow) {
    e.stopPropagation()
    sfx('click'); pendingDelete = c   // open the in-app confirm instead of a browser popup
  }
  function cancelDelete() { sfx('click', { volume: 0.2 }); pendingDelete = null }
  async function confirmDelete() {
    const c = pendingDelete
    if (!c) return
    pendingDelete = null; sfx('click')
    try { await api.deleteCase(c.id); await load() } catch { offline = true }
  }
  function toMap() { sfx('whoosh'); triggerGlitch(160); mode.set('pov'); pickerView.set('map'); stage.set('select') }
  onMount(load)
  const threatTr = (t: string) => ({ low: 'LOW', medium: 'MEDIUM', high: 'HIGH' } as Record<string, string>)[t] ?? t.toUpperCase()
  const d = (ms: number) => new Date(ms).toLocaleDateString('en-GB')
</script>

<section class="case">
  <div class="hdr caps">/// CASE FILES {#if offline}<span class="off">· OFFLINE</span>{/if}
    <button class="mapbtn caps" onclick={toMap} title="Back to the map">◄ MAP</button>
    <span class="hint">ESC · POV</span></div>

  <div class="new">
    <span class="lead caps hot">NEW CASE_</span>
    <input bind:value={name} class="type" placeholder="CASE NAME" onkeydown={(e) => e.key === 'Enter' && add()} spellcheck="false" />
    <button class="go caps" onclick={add}>CREATE</button>
  </div>

  <div class="grid">
    {#each cases as c}
      <button class="file panel" onclick={() => open(c.id)}>
        <header class="tab caps" class:hot={c.threat === 'high'}>/// {c.name}</header>
        <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
        <span class="del caps" role="button" tabindex="0" title="Delete case"
          onclick={(e) => del(e, c)} onkeydown={(e) => { if (e.key === 'Enter') del(e as unknown as MouseEvent, c) }}>✕</span>
        <div class="rows caps">
          <div class="r"><span class="k">STATUS</span><span class="v st-{c.status ?? 'open'}">{(c.status ?? 'open').toUpperCase()}</span></div>
          <div class="r"><span class="k">THREAT</span><span class="chip" class:chip--alarm={c.threat === 'high'} class:chip--invert={c.threat !== 'high'}>{threatTr(c.threat)}</span></div>
          <div class="r"><span class="k">OPENED</span><span class="v">{d(c.created)}</span></div>
        </div>
        <span class="openhint caps">OPEN INVESTIGATION ▸</span>
      </button>
    {/each}
    {#if cases.length === 0}
      <div class="empty caps">NO CASES · AN ALERT OPENS A CASE, OR CREATE ONE ABOVE</div>
    {/if}
  </div>
</section>

{#if pendingDelete}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="delscrim" role="presentation" onclick={cancelDelete}>
    <div class="delbox" role="dialog" aria-modal="true" tabindex="-1" onclick={(e) => e.stopPropagation()}>
      <div class="delhdr caps"><span class="hot">⚠ DELETE CASE</span></div>
      <div class="delbody">
        <div class="delname caps">/// {pendingDelete.name}</div>
        <div class="delwarn">This permanently deletes the case file and its findings. This cannot be undone.</div>
      </div>
      <div class="delbtns">
        <button class="cancel caps" onclick={cancelDelete}>CANCEL</button>
        <button class="confirm caps" onclick={confirmDelete}>✕ DELETE</button>
      </div>
    </div>
  </div>
{/if}

{#if $investigateCase != null}
  <CaseInvestigation caseId={$investigateCase} onclose={() => investigateCase.set(null)} />
{/if}

<style>
  .case { position: absolute; inset: 0; z-index: var(--z-boot); background: #050607; color: var(--ink); padding: 22px 30px; overflow: auto; }
  .hdr { font-size: var(--fs-title); letter-spacing: var(--tracking); margin-bottom: 16px; }
  .hdr .hint { float: right; font-size: var(--fs-micro); color: var(--ink-ghost); }
  .hdr .mapbtn { float: right; margin-left: 12px; padding: 4px 12px; border: 1px solid var(--ink-dim); background: none;
    color: var(--ink-dim); cursor: pointer; font-size: var(--fs-micro); letter-spacing: var(--tracking); vertical-align: middle; }
  .hdr .mapbtn:hover { border-color: var(--cyan); color: var(--cyan); }
  .hdr .off { font-size: var(--fs-micro); color: var(--scarlet); }

  .new { display: flex; align-items: center; gap: 12px; background: #000; border: 1px solid var(--ink); padding: 10px 14px; margin-bottom: 16px; }
  .lead { font-size: var(--fs-label); }
  input { flex: 1; background: none; border: none; outline: none; color: var(--ink); font-family: var(--font-type);
    font-size: var(--fs-banner); letter-spacing: var(--tracking); text-transform: uppercase; caret-color: var(--scarlet); }
  input::placeholder { color: var(--ink-ghost); }
  .go { padding: 4px 14px; border: 1px solid var(--ink); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .go:hover { background: var(--scarlet); border-color: var(--scarlet); color: #fff; }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; }
  .empty { grid-column: 1 / -1; color: var(--ink-ghost); font-size: var(--fs-label); padding: 30px 0; text-align: center; }
  .file { padding: 0; text-align: left; cursor: pointer; transition: border-color 140ms; position: relative; }
  .file:hover { border-color: var(--scarlet); }
  .del { position: absolute; top: 5px; right: 7px; z-index: 2; width: 18px; height: 18px; display: flex; align-items: center;
    justify-content: center; color: var(--ink-ghost); font-size: 11px; cursor: pointer; opacity: 0; transition: opacity 120ms; }
  .file:hover .del { opacity: 1; }
  .del:hover { color: var(--scarlet); }
  .tab { padding: 6px 10px; background: #000; border-bottom: 1px solid var(--hairline); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .tab.hot { background: var(--scarlet); color: #fff; }
  .rows { padding: 10px; display: flex; flex-direction: column; gap: 6px; }
  .r { display: flex; justify-content: space-between; align-items: center; font-size: var(--fs-label); }
  .k { color: var(--ink-dim); } .v { color: var(--ink); }
  .v.st-open { color: var(--cyan); } .v.st-investigating { color: var(--amber, #d8a200); }
  .v.st-resolved, .v.st-archived { color: var(--ink-dim); }
  .openhint { display: block; padding: 6px 10px; border-top: 1px solid var(--hairline); font-size: 8px; color: var(--ink-ghost); letter-spacing: 0.14em; }
  .file:hover .openhint { color: var(--scarlet); }

  /* in-app delete confirmation (replaces the native browser popup) */
  .delscrim { position: fixed; inset: 0; z-index: var(--z-cmd); background: rgba(2,3,4,0.82); display: grid; place-items: center; animation: dfade 150ms ease; }
  @keyframes dfade { from { opacity: 0; } }
  .delbox { width: min(400px, 92vw); background: #070809; border: 1px solid var(--scarlet); box-shadow: 0 0 44px rgba(225,6,0,0.22); animation: dpop 180ms cubic-bezier(0.16,1,0.3,1) both; }
  @keyframes dpop { from { transform: translateY(-12px) scale(0.97); opacity: 0; } }
  .delhdr { padding: 10px 14px; border-bottom: 1px solid var(--hairline); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .delhdr .hot { color: var(--scarlet); }
  .delbody { padding: 16px 14px 14px; }
  .delname { font-size: var(--fs-banner); color: var(--ink); letter-spacing: var(--tracking); margin-bottom: 10px; word-break: break-word; }
  .delwarn { font-size: var(--fs-micro); color: var(--ink-dim); line-height: 1.5; }
  .delbtns { display: flex; gap: 8px; padding: 0 14px 14px; justify-content: flex-end; }
  .delbtns button { padding: 6px 16px; font-size: var(--fs-label); letter-spacing: var(--tracking); background: none; cursor: pointer; }
  .delbtns .cancel { border: 1px solid var(--ink-dim); color: var(--ink-dim); }
  .delbtns .cancel:hover { border-color: var(--ink); color: var(--ink); }
  .delbtns .confirm { border: 1px solid var(--scarlet); color: var(--scarlet); }
  .delbtns .confirm:hover { background: var(--scarlet); color: #fff; }
</style>
