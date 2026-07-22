<script lang="ts">
  // Stationary editor for the selected tracklet (opens via the tracking panel's FILE).
  import { selectedDetection, dossierOpen, flashBanner } from '../../lib/stores'
  import { sfx } from '../../lib/audio'
  import { trUpper } from '../../lib/lexicon'
  import { annotations, annotate } from '../../lib/annotations'

  let d = $derived($selectedDetection)
  let alias = $state(''), owner = $state(''), species = $state('')
  let notes = $state(''), threat = $state<'low' | 'medium' | 'high'>('low')
  let upper = $state(''), height = $state('')

  $effect(() => {
    const id = d?.id
    const a = id ? ($annotations[id] ?? {}) : {}
    alias = a.alias ?? ''; owner = a.owner ?? ''; species = a.species ?? ''
    notes = a.notes ?? ''; threat = a.threat ?? 'low'
    upper = a.upper_color ?? d?.attrs?.upper_color ?? ''
    height = a.height ?? d?.attrs?.height ?? ''
  })

  function close() { sfx('click'); dossierOpen.set(false) }
  function save() {
    if (!d) return
    sfx('sonar')
    annotate(d.id, { alias, owner, species, notes, threat, upper_color: upper, height })
    flashBanner('ANNOTATION SAVED', false, 1000)
    dossierOpen.set(false)
  }
</script>

{#if d && $dossierOpen}
  <aside class="dossier panel">
    <header class="tab caps" class:alarm={d.severity !== 'info' || threat === 'high'}>
      <span>/// {alias || d.klass} · FILE</span>
      <button class="x" onclick={close} aria-label="close">×</button>
    </header>

    <div class="rows caps">
      <div class="r"><span class="k">TRACKLET ID</span><span class="chip chip--invert">{d.id}</span></div>
      <div class="r"><span class="k">CLASS</span><span class="v">{d.klass} · {trUpper(d.cls)}</span></div>
      <div class="r"><span class="k">CONFIDENCE</span><span class="v">{Math.round(d.conf * 100)}%</span></div>
    </div>

    <div class="edit caps">
      <label>ALIAS<input bind:value={alias} spellcheck="false" /></label>
      <label>OWNER<input bind:value={owner} placeholder="LINK TO PERSON/CASE" spellcheck="false" /></label>
      {#if d.cls === 'animal'}<label>SPECIES<input bind:value={species} placeholder="DOG / CAT …" spellcheck="false" /></label>{/if}
      <div class="two">
        <label>UPPER<input bind:value={upper} spellcheck="false" /></label>
        <label>HEIGHT<input bind:value={height} spellcheck="false" /></label>
      </div>
      <label>THREAT
        <select bind:value={threat}><option value="low">LOW</option><option value="medium">MEDIUM</option><option value="high">HIGH</option></select>
      </label>
      <label>NOTES<textarea bind:value={notes} rows="2" spellcheck="false"></textarea></label>
      <button class="save caps" onclick={save}>SAVE ANNOTATION</button>
    </div>
  </aside>
{/if}

<style>
  .dossier { position: absolute; top: 76px; right: 62px; width: 310px; z-index: var(--z-panel); max-height: 82vh; overflow-y: auto; }
  .tab { display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0;
    padding: 6px 10px; background: #000; border-bottom: 1px solid var(--hairline);
    font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink); }
  .tab.alarm { background: var(--scarlet); color: #fff; }
  .x { font-size: 16px; line-height: 1; padding: 0 2px; color: inherit; }

  .rows { padding: 10px; display: flex; flex-direction: column; gap: 6px; }
  .r { display: flex; justify-content: space-between; align-items: center; gap: 10px; font-size: var(--fs-label); }
  .k { color: var(--ink-dim); } .v { color: var(--ink); }

  .edit { border-top: 1px solid var(--hairline); padding: 10px; display: flex; flex-direction: column; gap: 7px; }
  .edit label { display: flex; flex-direction: column; gap: 3px; font-size: 8px; color: var(--ink-ghost); }
  .edit .two { display: flex; gap: 8px; } .edit .two label { flex: 1; }
  .edit input, .edit select, .edit textarea { background: #000; border: 1px solid var(--hairline); color: var(--ink);
    font-family: var(--font-mono); font-size: var(--fs-micro); padding: 4px 6px; text-transform: uppercase; resize: none; }
  .edit input:focus, .edit select:focus, .edit textarea:focus { outline: none; border-color: var(--scarlet); }
  .save { margin-top: 2px; padding: 6px; border: 1px solid var(--scarlet); color: var(--ink); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .save:hover { background: var(--scarlet); color: #fff; }
</style>
