<script lang="ts">
  // Session roster — an anonymous, deduped gallery of every person and vehicle seen this
  // session, with a photo each (and plates for vehicles). Click one for a profile: larger
  // photo (optionally background-removed), plate, attributes, seen-times and editable notes.
  import { onDestroy, onMount } from 'svelte'
  import { api } from '../../lib/api'
  import { annotations } from '../../lib/annotations'
  import { trUpper } from '../../lib/lexicon'
  import { sfx } from '../../lib/audio'
  import PoiDossier from '../roster/PoiDossier.svelte'
  import type { RosterEntry } from '../../lib/types'

  const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
  let entries = $state<RosterEntry[]>([])
  let filter = $state<'all' | 'person' | 'vehicle'>('all')
  let selected = $state<RosterEntry | null>(null)
  let timer: ReturnType<typeof setInterval> | undefined
  let clock: ReturnType<typeof setInterval> | undefined
  let now = $state(Date.now())

  async function refresh() {
    try {
      entries = await api.roster()
      if (selected) selected = entries.find((e) => e.id === selected!.id) ?? selected
    } catch { /* keep the last good list */ }
  }
  onMount(() => {
    refresh(); timer = setInterval(refresh, 3000)
    clock = setInterval(() => (now = Date.now()), 1000)   // drives LIVE + relative times in the dossier
  })
  onDestroy(() => { if (timer) clearInterval(timer); if (clock) clearInterval(clock) })

  let shown = $derived(filter === 'all' ? entries : entries.filter((e) => e.cls === filter))
  let nPeople = $derived(entries.filter((e) => e.cls === 'person').length)
  let nVehicles = $derived(entries.filter((e) => e.cls === 'vehicle').length)

  const photo = (e: RosterEntry) => (e.snapshot ? API + e.snapshot : '')
  // compact combined line for the small gallery cards
  const attrLine = (e: RosterEntry) =>
    [e.attrs?.make, e.attrs?.subtype, e.attrs?.upper_color, e.cls === 'person' ? e.attrs?.height : undefined]
      .filter(Boolean).map((s) => trUpper(String(s))).join(' · ')
  function open(e: RosterEntry) { sfx('ping', { volume: 0.25 }); selected = e }
  const FILTERS: [typeof filter, string][] = [['all', 'ALL'], ['person', '👤 PEOPLE'], ['vehicle', '🚗 VEHICLES']]
</script>

<section class="roster">
  <div class="hdr caps"><span class="hot">///</span> ROSTER · {nPeople} PEOPLE · {nVehicles} VEHICLES <span class="hint">ESC</span></div>
  <div class="filters">
    {#each FILTERS as [k, label]}
      <button class="chip caps" class:on={filter === k} onclick={() => (filter = k)}>{label}</button>
    {/each}
  </div>

  {#if shown.length === 0}
    <div class="empty caps">SCANNING ALL CAMERAS · THE ROSTER FILLS AUTOMATICALLY AS PEOPLE AND VEHICLES ARE SEEN</div>
  {:else}
    <div class="grid">
      {#each shown as e (e.id)}
        {@const a = $annotations[e.id] ?? {}}
        <button class="card" class:sel={selected?.id === e.id} onclick={() => open(e)}>
          <div class="ph">
            {#if photo(e)}<img src={photo(e)} alt="" />{:else}<div class="noimg caps">NO IMG</div>{/if}
            <span class="badge caps">{e.cls === 'vehicle' ? '🚗' : '👤'}</span>
          </div>
          <div class="meta">
            <span class="nm caps">{a.alias || e.id}</span>
            {#if e.plate}<span class="plate caps">▤ {e.plate}</span>{/if}
            {#if attrLine(e)}<span class="at caps">{attrLine(e)}</span>{/if}
          </div>
        </button>
      {/each}
    </div>
  {/if}

  {#if selected}
    <PoiDossier entry={selected} {now} onclose={() => (selected = null)} />
  {/if}
</section>

<style>
  .roster { position: absolute; inset: 0; z-index: var(--z-boot); background: #050607; color: var(--ink);
    overflow: auto; padding: 22px 30px; }
  .hdr { font-size: var(--fs-banner); letter-spacing: var(--tracking); border-bottom: 1px solid var(--hairline); padding-bottom: 10px; }
  .hdr .hot { color: var(--scarlet); } .hdr .hint { float: right; color: var(--ink-dim); font-size: var(--fs-micro); }
  .filters { display: flex; gap: 8px; margin: 14px 0; }
  .chip { padding: 4px 12px; border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim);
    font-size: var(--fs-label); letter-spacing: var(--tracking); cursor: pointer; }
  .chip.on { border-color: var(--ink); color: var(--ink); }
  .empty { color: var(--ink-dim); padding: 40px 0; text-align: center; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; padding-bottom: 40px; }
  .card { background: #0a0d10; border: 1px solid var(--hairline); padding: 0; cursor: pointer; text-align: left;
    display: flex; flex-direction: column; transition: border-color 140ms; }
  .card:hover, .card.sel { border-color: var(--scarlet); }
  .ph { position: relative; aspect-ratio: 3 / 4; background: #05070a; overflow: hidden; }
  .ph img { width: 100%; height: 100%; object-fit: cover; filter: saturate(0.6) contrast(1.05); }
  .noimg { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: var(--fs-micro); }
  .badge { position: absolute; top: 4px; right: 4px; font-size: 12px; text-shadow: 0 0 4px #000; }
  .meta { padding: 6px 8px; display: flex; flex-direction: column; gap: 2px; }
  .nm { font-size: var(--fs-micro); color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .plate { font-size: var(--fs-micro); color: var(--cyan); letter-spacing: 0.12em; font-weight: 700; }
  .at { font-size: 8px; color: var(--ink-dim); }
</style>
