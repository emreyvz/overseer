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
    refresh(); loadPlates(); timer = setInterval(refresh, 3000)
    clock = setInterval(() => (now = Date.now()), 1000)   // drives LIVE + relative times in the dossier
  })
  onDestroy(() => { if (timer) clearInterval(timer); if (clock) clearInterval(clock) })

  let query = $state('')
  let sort = $state<'recent' | 'seen'>('recent')
  let bolo = $state(false)
  let shown = $derived.by(() => {
    const q = query.trim().toLowerCase()
    const anns = $annotations
    return entries
      .filter((e) => filter === 'all' || e.cls === filter)
      .filter((e) => !bolo || e.watched)
      .filter((e) => {
        if (!q) return true
        const a = anns[e.id] ?? {}
        const hay = [e.id, a.alias, e.plate, e.attrs?.make, e.attrs?.subtype, e.attrs?.upper_color, e.cam, e.first_cam]
          .filter(Boolean).join(' ').toLowerCase()
        return hay.includes(q)
      })
      .slice()
      .sort((a, b) => (sort === 'seen' ? b.obs - a.obs : b.last_ts - a.last_ts))
  })
  let nPeople = $derived(entries.filter((e) => e.cls === 'person').length)
  let nVehicles = $derived(entries.filter((e) => e.cls === 'vehicle').length)
  let nWatched = $derived(entries.filter((e) => e.watched).length)

  // plate watchlist (BOLO for plates) — reading any of these on any camera fires an alert
  let platesWatched = $state<string[]>([])
  let platePanel = $state(false)
  let plateInput = $state('')
  async function loadPlates() { try { platesWatched = (await api.watchedPlates()).plates } catch { /* offline */ } }
  async function addPlate() {
    const p = plateInput.trim()
    if (!p) return
    sfx('sonar'); plateInput = ''
    try { platesWatched = (await api.watchPlate(p, true)).plates } catch { /* offline */ }
  }
  async function removePlate(p: string) {
    sfx('click')
    try { platesWatched = (await api.watchPlate(p, false)).plates } catch { /* offline */ }
  }

  const photo = (e: RosterEntry) => (e.snapshot ? API + e.snapshot : '')
  const clipSrc = (e: RosterEntry) => (e.clip ? API + e.clip : '')
  // play a card's sighting clip on hover only (autoplaying every card at once would be heavy)
  function hoverPlay(ev: PointerEvent) { const v = (ev.currentTarget as HTMLElement).querySelector('video'); if (v) v.play().catch(() => {}) }
  function hoverStop(ev: PointerEvent) { const v = (ev.currentTarget as HTMLElement).querySelector('video'); if (v) { v.pause(); v.currentTime = 0 } }
  // compact combined line for the small gallery cards
  const attrLine = (e: RosterEntry) =>
    [e.attrs?.make, e.attrs?.subtype, e.attrs?.upper_color, e.cls === 'person' ? e.attrs?.height : undefined]
      .filter(Boolean).map((s) => trUpper(String(s))).join(' · ')
  function open(e: RosterEntry) { sfx('ping', { volume: 0.25 }); selected = e }
  const FILTERS: [typeof filter, string][] = [['all', 'ALL'], ['person', '👤 PEOPLE'], ['vehicle', '🚗 VEHICLES']]
</script>

<section class="roster">
  <div class="hdr caps"><span class="hot">///</span> ROSTER · {nPeople} PEOPLE · {nVehicles} VEHICLES <span class="hint">ESC</span></div>
  <div class="toolbar">
    <div class="filters">
      {#each FILTERS as [k, label]}
        <button class="chip caps" class:on={filter === k} onclick={() => (filter = k)}>{label}</button>
      {/each}
      {#if nWatched > 0}<button class="chip caps bolo" class:on={bolo} onclick={() => (bolo = !bolo)}>◉ BOLO {nWatched}</button>{/if}
    </div>
    <div class="search">
      <span class="mag caps">⌕</span>
      <input class="q caps" bind:value={query} placeholder="SEARCH ID · PLATE · MAKE · COLOR · CAMERA" spellcheck="false" />
      {#if query}<button class="clr" onclick={() => (query = '')} aria-label="clear">×</button>{/if}
    </div>
    <div class="sort caps">
      <button class="schip" class:on={sort === 'recent'} onclick={() => (sort = 'recent')}>RECENT</button>
      <button class="schip" class:on={sort === 'seen'} onclick={() => (sort = 'seen')}>MOST SEEN</button>
    </div>
    <button class="chip caps platebtn" class:on={platePanel || platesWatched.length > 0} onclick={() => (platePanel = !platePanel)}>▤ PLATE BOLO{#if platesWatched.length} {platesWatched.length}{/if}</button>
  </div>

  {#if platePanel}
    <div class="platewatch">
      <div class="pwh caps"><span class="hot">◉</span> PLATE WATCHLIST · ANY CAMERA READ FIRES AN ALERT</div>
      <div class="pwadd">
        <input class="pwin caps" bind:value={plateInput} placeholder="PLATE e.g. 34ABC123" spellcheck="false"
          onkeydown={(e) => e.key === 'Enter' && addPlate()} />
        <button class="pwgo caps" onclick={addPlate}>WATCH</button>
      </div>
      <div class="pwlist">
        {#each platesWatched as p (p)}
          <span class="pwtag caps">{p}<button class="pwx" onclick={() => removePlate(p)} aria-label="remove">×</button></span>
        {/each}
        {#if platesWatched.length === 0}<span class="pwnone caps">NO PLATES WATCHED</span>{/if}
      </div>
    </div>
  {/if}

  {#if shown.length === 0}
    <div class="empty caps">SCANNING ALL CAMERAS · THE ROSTER FILLS AUTOMATICALLY AS PEOPLE AND VEHICLES ARE SEEN</div>
  {:else}
    <div class="grid">
      {#each shown as e (e.id)}
        {@const a = $annotations[e.id] ?? {}}
        <button class="card" class:sel={selected?.id === e.id} class:watched={e.watched} onclick={() => open(e)}
          onpointerenter={hoverPlay} onpointerleave={hoverStop}>
          <div class="ph">
            {#if photo(e)}<img src={photo(e)} alt="" />{:else}<div class="noimg caps">NO IMG</div>{/if}
            {#if clipSrc(e)}<!-- svelte-ignore a11y_media_has_caption --><video class="clipv" src={clipSrc(e)} muted loop playsinline preload="none"></video>{/if}
            {#if clipSrc(e)}<span class="vtag caps">▶</span>{/if}
            <span class="badge caps">{e.cls === 'vehicle' ? '🚗' : '👤'}</span>
            {#if e.watched}<span class="bolo caps">◉ BOLO</span>{/if}
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
    <!-- keyed on the subject id so navigating via the relationship network remounts the profile,
         replaying its cinematic entrance as an animated transition (a refresh keeps the same id) -->
    {#key selected.id}
      <PoiDossier entry={selected} {now} onclose={() => (selected = null)}
        onopen={async (id) => {
          const e = entries.find((x) => x.id === id)
          if (e) { selected = e; return }
          try { selected = await api.rosterGet(id) } catch { /* gone from the roster */ }
        }} />
    {/key}
  {/if}
</section>

<style>
  .roster { position: absolute; inset: 0; z-index: var(--z-boot); background: #050607; color: var(--ink);
    overflow: auto; padding: 22px 30px; }
  .hdr { font-size: var(--fs-banner); letter-spacing: var(--tracking); border-bottom: 1px solid var(--hairline); padding-bottom: 10px; }
  .hdr .hot { color: var(--scarlet); } .hdr .hint { float: right; color: var(--ink-dim); font-size: var(--fs-micro); }
  .toolbar { display: flex; align-items: center; gap: 12px; margin: 14px 0; flex-wrap: wrap; }
  .filters { display: flex; gap: 8px; }
  .chip { padding: 4px 12px; border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim);
    font-size: var(--fs-label); letter-spacing: var(--tracking); cursor: pointer; }
  .chip.on { border-color: var(--ink); color: var(--ink); }
  .chip.bolo { color: var(--scarlet); border-color: color-mix(in srgb, var(--scarlet) 45%, transparent); }
  .chip.bolo.on { background: var(--scarlet); color: #fff; border-color: var(--scarlet); }
  .search { flex: 1; min-width: 200px; display: flex; align-items: center; gap: 8px; border: 1px solid var(--hairline);
    background: #0a0d10; padding: 5px 10px; }
  .search .mag { color: var(--ink-dim); font-size: 13px; }
  .q { flex: 1; background: none; border: 0; outline: none; color: var(--ink); font-family: var(--font-mono);
    font-size: var(--fs-label); letter-spacing: 0.1em; }
  .q::placeholder { color: var(--ink-ghost); }
  .clr { background: none; border: 0; color: var(--ink-dim); font-size: 15px; cursor: pointer; padding: 0 2px; }
  .clr:hover { color: var(--scarlet); }
  .sort { display: flex; gap: 1px; background: var(--hairline); border: 1px solid var(--hairline); }
  .schip { padding: 5px 12px; background: #0a0d10; color: var(--ink-dim); border: 0; font-size: 9px; letter-spacing: var(--tracking); cursor: pointer; }
  .schip.on { background: #14161a; color: var(--ink); }
  .platebtn { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 40%, transparent); }
  .platebtn.on { border-color: var(--cyan); color: var(--cyan); background: rgba(56,208,227,0.08); }
  .platewatch { border: 1px solid var(--hairline); background: #0a0d10; padding: 12px 14px; margin: 0 0 16px;
    display: flex; flex-direction: column; gap: 10px; animation: pwin 200ms var(--ease); }
  @keyframes pwin { from { opacity: 0; transform: translateY(-6px); } }
  .pwh { font-size: 9px; letter-spacing: 0.16em; color: var(--ink-dim); } .pwh .hot { color: var(--scarlet); }
  .pwadd { display: flex; gap: 8px; }
  .pwin { flex: 1; background: #000; border: 1px solid var(--hairline); color: var(--ink); font-family: var(--font-mono);
    font-size: var(--fs-label); letter-spacing: 0.16em; padding: 7px 10px; outline: none; }
  .pwin:focus { border-color: var(--cyan); } .pwin::placeholder { color: var(--ink-ghost); }
  .pwgo { padding: 7px 16px; border: 1px solid var(--scarlet); color: var(--ink); font-size: 9px; letter-spacing: var(--tracking); cursor: pointer; background: none; }
  .pwgo:hover { background: var(--scarlet); color: #fff; }
  .pwlist { display: flex; flex-wrap: wrap; gap: 6px; }
  .pwtag { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border: 1px solid color-mix(in srgb, var(--cyan) 40%, transparent);
    color: var(--cyan); font-size: 9px; letter-spacing: 0.12em; font-weight: 700; }
  .pwx { background: none; border: 0; color: var(--ink-dim); font-size: 13px; line-height: 1; cursor: pointer; padding: 0; }
  .pwx:hover { color: var(--scarlet); }
  .pwnone { color: var(--ink-ghost); font-size: 9px; }
  .empty { color: var(--ink-dim); padding: 40px 0; text-align: center; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; padding-bottom: 40px; }
  .card { background: #0a0d10; border: 1px solid var(--hairline); padding: 0; cursor: pointer; text-align: left;
    display: flex; flex-direction: column; transition: border-color 140ms; }
  .card:hover, .card.sel { border-color: var(--scarlet); }
  .ph { position: relative; aspect-ratio: 3 / 4; background: #05070a; overflow: hidden; }
  .ph img { width: 100%; height: 100%; object-fit: cover; filter: saturate(0.6) contrast(1.05); }
  /* sighting clip fades in over the still on hover — the card comes alive as a short video */
  .clipv { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity 180ms; }
  .card:hover .clipv { opacity: 1; }
  .vtag { position: absolute; bottom: 4px; left: 4px; font-size: 8px; color: var(--cyan); text-shadow: 0 0 4px #000; opacity: 0.8; }
  .card:hover .vtag { opacity: 0; }
  .noimg { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: var(--fs-micro); }
  .badge { position: absolute; top: 4px; right: 4px; font-size: 12px; text-shadow: 0 0 4px #000; }
  .card.watched { border-color: var(--scarlet); }
  .bolo { position: absolute; top: 4px; left: 4px; padding: 2px 6px; background: var(--scarlet); color: #fff;
    font-size: 7px; letter-spacing: 0.12em; box-shadow: 0 0 8px var(--scarlet-glow); }
  .meta { padding: 6px 8px; display: flex; flex-direction: column; gap: 2px; }
  .nm { font-size: var(--fs-micro); color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .plate { font-size: var(--fs-micro); color: var(--cyan); letter-spacing: 0.12em; font-weight: 700; }
  .at { font-size: 8px; color: var(--ink-dim); }
</style>
