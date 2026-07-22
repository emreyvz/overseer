<script lang="ts">
  // Alert-rule creator (item 13). Simple, on-brand: "if >N in ZONE → CROWD, red on the right".
  import { alertRules } from '../lib/stores'
  import { sfx } from '../lib/audio'
  import { zones, rules, addRule, delRule, type RuleEvent } from '../lib/zones'

  const EVENTS: { v: RuleEvent; label: string; needsN: boolean; kind: 'area' | 'line' }[] = [
    { v: 'restricted', label: 'RESTRICTED ENTRY', needsN: false, kind: 'area' },
    { v: 'crowd', label: 'CROWD OVER N', needsN: true, kind: 'area' },
    { v: 'loiter', label: 'LOITERING > N SEC', needsN: true, kind: 'area' },
    { v: 'queue', label: 'QUEUE > N', needsN: true, kind: 'area' },
    { v: 'line', label: 'LINE CROSSED', needsN: false, kind: 'line' },
  ]
  let name = $state(''), ev = $state<RuleEvent>('crowd'), zoneId = $state(''), threshold = $state(5)
  let sev = $state<'info' | 'warning' | 'critical'>('warning')
  let evDef = $derived(EVENTS.find((e) => e.v === ev)!)
  let zoneOpts = $derived($zones.filter((z) => z.kind === evDef.kind))

  function create() {
    if (!zoneId) { sfx('error'); return }
    sfx('sonar')
    addRule({ name: name.trim() || evDef.label, event: ev, zoneId, threshold: Number(threshold) || 0, severity: sev })
    name = ''
  }
  const zoneName = (id: string) => $zones.find((z) => z.id === id)?.name ?? '—'
  const sevLabel = { info: 'INFO', warning: 'WARNING', critical: 'CRITICAL' }
</script>

<button class="scrim" aria-label="close" onpointerdown={() => alertRules.set(false)}></button>
<aside class="ar panel caps">
  <header class="tab"><span>/// ALERT RULES</span><button class="x" onclick={() => alertRules.set(false)} aria-label="close">×</button></header>

  <div class="body">
    <div class="new">
      <div class="cl">NEW RULE</div>
      <label>NAME<input bind:value={name} placeholder="e.g. LOBBY CROWD" spellcheck="false" /></label>
      <label>EVENT
        <select bind:value={ev}>{#each EVENTS as e}<option value={e.v}>{e.label}</option>{/each}</select>
      </label>
      <div class="two">
        <label>ZONE
          <select bind:value={zoneId}>
            <option value="">— SELECT —</option>
            {#each zoneOpts as z}<option value={z.id}>{z.name}</option>{/each}
          </select>
        </label>
        {#if evDef.needsN}<label>THRESHOLD<input type="number" min="1" bind:value={threshold} /></label>{/if}
      </div>
      <label>SEVERITY
        <select bind:value={sev}><option value="info">INFO</option><option value="warning">WARNING</option><option value="critical">CRITICAL</option></select>
      </label>
      {#if zoneOpts.length === 0}<div class="hintline">NO {evDef.kind.toUpperCase()} ZONES · DRAW ONE FIRST ( Z )</div>{/if}
      <button class="create" onclick={create}>+ CREATE RULE</button>
    </div>

    <div class="list">
      <div class="cl">ACTIVE RULES · {$rules.length}</div>
      {#each $rules as r}
        <div class="rule sev-{r.severity}">
          <span class="rn">{r.name}</span>
          <span class="rz">{zoneName(r.zoneId)}{r.event === 'crowd' ? ` · >${r.threshold}` : ''}</span>
          <span class="rs">{sevLabel[r.severity]}</span>
          <button class="del" onclick={() => { sfx('click'); delRule(r.id) }} aria-label="delete">×</button>
        </div>
      {/each}
      {#if $rules.length === 0}<div class="mt">NO RULES</div>{/if}
    </div>
  </div>
</aside>

<style>
  .scrim { position: fixed; inset: 0; z-index: var(--z-cmd); background: rgba(0,0,0,0.45); }
  .ar { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: min(560px, 92vw); max-height: 84vh;
    z-index: calc(var(--z-cmd) + 1); display: flex; flex-direction: column; }
  .tab { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: #000;
    border-bottom: 1px solid var(--hairline); font-size: var(--fs-banner); letter-spacing: var(--tracking); color: var(--ink); }
  .x { font-size: 18px; color: inherit; }
  .body { display: grid; grid-template-columns: 1fr 1fr; gap: 0; overflow: hidden; }
  .new, .list { padding: 12px; overflow-y: auto; }
  .list { border-left: 1px solid var(--hairline); }
  .cl { font-size: var(--fs-label); color: var(--scarlet); letter-spacing: var(--tracking); margin-bottom: 8px; }
  label { display: flex; flex-direction: column; gap: 3px; font-size: 8px; color: var(--ink-ghost); margin-bottom: 8px; }
  .two { display: flex; gap: 8px; } .two label { flex: 1; }
  input, select { background: #000; border: 1px solid var(--hairline); color: var(--ink); font-family: var(--font-mono);
    font-size: var(--fs-micro); padding: 5px 7px; text-transform: uppercase; }
  input:focus, select:focus { outline: none; border-color: var(--scarlet); }
  .hintline { font-size: 8px; color: var(--scarlet); margin-bottom: 8px; }
  .create { width: 100%; padding: 8px; border: 1px solid var(--scarlet); color: var(--ink); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .create:hover { background: var(--scarlet); color: #fff; }

  .rule { display: grid; grid-template-columns: 1fr auto auto; gap: 6px; align-items: center; padding: 6px 4px;
    border-left: 2px solid var(--ink-ghost); margin-bottom: 4px; font-size: var(--fs-micro); }
  .rule.sev-critical { border-left-color: var(--scarlet); } .rule.sev-warning { border-left-color: var(--scarlet); }
  .rn { color: var(--ink); grid-column: 1 / -1; }
  .rz { color: var(--ink-ghost); font-size: 8px; } .rs { color: var(--ink-dim); font-size: 8px; }
  .rule.sev-critical .rs, .rule.sev-warning .rs { color: var(--scarlet); }
  .del { color: var(--ink-ghost); font-size: 13px; } .del:hover { color: var(--scarlet); }
  .mt { color: var(--ink-ghost); font-size: var(--fs-micro); }
</style>
