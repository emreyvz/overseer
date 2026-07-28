<script lang="ts">
  // Session roster — an anonymous, deduped gallery of every person and vehicle seen this
  // session, with a photo each (and plates for vehicles). Click one for a profile: larger
  // photo (optionally background-removed), plate, attributes, seen-times and editable notes.
  import { onDestroy, onMount } from 'svelte'
  import { api } from '../../lib/api'
  import { annotations, annotate } from '../../lib/annotations'
  import { trUpper } from '../../lib/lexicon'
  import { sfx } from '../../lib/audio'
  import type { RosterEntry } from '../../lib/types'

  const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
  let entries = $state<RosterEntry[]>([])
  let filter = $state<'all' | 'person' | 'vehicle'>('all')
  let selected = $state<RosterEntry | null>(null)
  let cutout = $state(false)
  let timer: ReturnType<typeof setInterval> | undefined

  async function refresh() {
    try {
      entries = await api.roster()
      if (selected) selected = entries.find((e) => e.id === selected!.id) ?? selected
    } catch { /* keep the last good list */ }
  }
  onMount(() => { refresh(); timer = setInterval(refresh, 3000) })
  onDestroy(() => { if (timer) clearInterval(timer) })

  let shown = $derived(filter === 'all' ? entries : entries.filter((e) => e.cls === filter))
  let nPeople = $derived(entries.filter((e) => e.cls === 'person').length)
  let nVehicles = $derived(entries.filter((e) => e.cls === 'vehicle').length)

  const photo = (e: RosterEntry) => (e.snapshot ? API + e.snapshot : '')
  const attrLine = (e: RosterEntry) =>
    [e.attrs?.subtype, e.attrs?.upper_color, e.cls === 'person' ? e.attrs?.height : undefined]
      .filter(Boolean).map((s) => trUpper(String(s))).join(' · ')
  const hhmm = (ms: number) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
  function open(e: RosterEntry) { sfx('ping', { volume: 0.25 }); selected = e; cutout = false }
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
    <div class="empty caps">NO ONE LOGGED YET · OPEN A CAMERA AND THE ROSTER FILLS AS PEOPLE AND VEHICLES ARE SEEN</div>
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
    {@const a = $annotations[selected.id] ?? {}}
    <aside class="profile">
      <div class="ptab caps"><span>/// PROFILE</span><button class="px" onclick={() => (selected = null)} aria-label="close">×</button></div>
      <div class="pph">
        {#if selected.snapshot}
          <img src={cutout ? `${API}/api/roster/${selected.id}/cutout?t=${selected.last_ts}` : photo(selected)} alt="" />
          <button class="cut caps" class:on={cutout} onclick={() => (cutout = !cutout)}>{cutout ? '◧ SHOW BG' : '◨ REMOVE BG'}</button>
        {:else}<div class="noimg big caps">NO IMG</div>{/if}
      </div>
      <div class="prow caps"><span class="kk">ID</span><span class="vv">{selected.id}</span></div>
      <div class="prow caps"><span class="kk">CLASS</span><span class="vv">{trUpper(selected.cls)}</span></div>
      {#if selected.attrs?.subtype}<div class="prow caps"><span class="kk">TYPE</span><span class="vv">{trUpper(selected.attrs.subtype)}</span></div>{/if}
      {#if selected.plate}<div class="prow caps"><span class="kk">PLATE</span><span class="vv plate">{selected.plate}</span></div>{/if}
      {#if attrLine(selected)}<div class="prow caps"><span class="kk">ATTR</span><span class="vv">{attrLine(selected)}</span></div>{/if}
      {#if selected.cam}<div class="prow caps"><span class="kk">CAMERA</span><span class="vv">{selected.cam}</span></div>{/if}
      <div class="prow caps"><span class="kk">SEEN</span><span class="vv">{hhmm(selected.first_ts)}–{hhmm(selected.last_ts)} · {selected.obs}×</span></div>
      <div class="psep"></div>
      <div class="nl caps">NOTES</div>
      <textarea class="notes" placeholder="Add a note about this one…" value={a.notes ?? ''}
        oninput={(ev) => annotate(selected!.id, { notes: (ev.target as HTMLTextAreaElement).value })}></textarea>
    </aside>
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
  .noimg.big { aspect-ratio: 3 / 4; }
  .badge { position: absolute; top: 4px; right: 4px; font-size: 12px; text-shadow: 0 0 4px #000; }
  .meta { padding: 6px 8px; display: flex; flex-direction: column; gap: 2px; }
  .nm { font-size: var(--fs-micro); color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .plate { font-size: var(--fs-micro); color: var(--cyan); letter-spacing: 0.12em; font-weight: 700; }
  .at { font-size: 8px; color: var(--ink-dim); }

  .profile { position: fixed; top: 0; right: 0; width: 300px; height: 100%; background: #070a0d;
    border-left: 1px solid var(--scarlet); box-shadow: -12px 0 30px rgba(0,0,0,0.6); padding: 16px 16px 24px;
    display: flex; flex-direction: column; gap: 8px; overflow: auto; animation: slidein 200ms var(--ease); }
  @keyframes slidein { from { transform: translateX(20px); opacity: 0; } }
  .ptab { display: flex; justify-content: space-between; align-items: center; font-size: var(--fs-label);
    letter-spacing: var(--tracking); border-bottom: 1px solid var(--hairline); padding-bottom: 6px; }
  .px { font-size: 16px; color: var(--ink-dim); background: none; border: 0; cursor: pointer; }
  .px:hover { color: var(--scarlet); }
  .pph { position: relative; }
  .pph img { width: 100%; aspect-ratio: 3 / 4; object-fit: cover;
    background: repeating-conic-gradient(#0d1114 0% 25%, #0a0d10 0% 50%) 50% / 18px 18px; }
  .cut { position: absolute; bottom: 6px; left: 6px; padding: 3px 8px; border: 1px solid var(--ink-dim);
    background: rgba(5,7,10,0.8); color: var(--ink-dim); font-size: 8px; letter-spacing: var(--tracking); cursor: pointer; }
  .cut.on { border-color: var(--scarlet); color: var(--scarlet); }
  .prow { display: flex; justify-content: space-between; gap: 10px; font-size: 9px; }
  .prow .kk { color: var(--ink-dim); } .prow .vv { color: var(--ink); }
  .prow .plate { color: var(--cyan); }
  .psep { height: 1px; background: var(--hairline); margin: 4px 0; }
  .nl { font-size: 8px; color: var(--ink-dim); }
  .notes { min-height: 70px; resize: vertical; background: #05070a; border: 1px solid var(--hairline);
    color: var(--ink); font-family: inherit; font-size: 10px; padding: 6px; }
  .notes:focus { border-color: var(--scarlet); outline: none; }
</style>
