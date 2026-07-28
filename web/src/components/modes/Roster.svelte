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
    clock = setInterval(() => (now = Date.now()), 1000)   // drives LIVE + relative times
  })
  onDestroy(() => { if (timer) clearInterval(timer); if (clock) clearInterval(clock) })

  let shown = $derived(filter === 'all' ? entries : entries.filter((e) => e.cls === filter))
  let nPeople = $derived(entries.filter((e) => e.cls === 'person').length)
  let nVehicles = $derived(entries.filter((e) => e.cls === 'vehicle').length)

  const LIVE_MS = 8000
  const isLive = (e: RosterEntry) => now - e.last_ts < LIVE_MS
  const photo = (e: RosterEntry) => (e.snapshot ? API + e.snapshot : '')
  // compact combined line for the small gallery cards
  const attrLine = (e: RosterEntry) =>
    [e.attrs?.make, e.attrs?.subtype, e.attrs?.upper_color, e.cls === 'person' ? e.attrs?.height : undefined]
      .filter(Boolean).map((s) => trUpper(String(s))).join(' · ')
  // appearance only (colour + height) — make/type get their own rows in the profile
  const apprLine = (e: RosterEntry) =>
    [e.attrs?.upper_color, e.cls === 'person' ? e.attrs?.height : undefined]
      .filter(Boolean).map((s) => trUpper(String(s))).join(' · ')
  const hhmm = (ms: number) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
  const clock24 = (ms: number) => { const d = new Date(ms); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':') }
  function ago(ms: number): string {
    const s = Math.max(0, Math.round((now - ms) / 1000))
    if (s < 5) return 'just now'
    if (s < 60) return `${s}s ago`
    const m = Math.round(s / 60)
    if (m < 60) return `${m}m ago`
    return `${Math.round(m / 60)}h ago`
  }
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
    {@const s = selected}
    {@const a = $annotations[s.id] ?? {}}
    {@const live = isLive(s)}
    <aside class="profile" class:livepanel={live}>
      <div class="ptab caps">
        <span>/// DOSSIER</span>
        <span class="live" class:on={live}>{live ? '● LIVE' : '○ IDLE'}</span>
        <button class="px" onclick={() => (selected = null)} aria-label="close">×</button>
      </div>

      <div class="pph" class:livef={live}>
        {#if s.snapshot}
          <img src={cutout ? `${API}/api/roster/${s.id}/cutout?t=${s.last_ts}` : `${photo(s)}?t=${s.last_ts}`} alt="" />
          <button class="cut caps" class:on={cutout} onclick={() => (cutout = !cutout)}>{cutout ? '◧ SHOW BG' : '◨ REMOVE BG'}</button>
          <span class="pid caps">{a.alias || s.id}</span>
        {:else}<div class="noimg big caps">NO IMG</div>{/if}
      </div>

      <div class="status caps" class:on={live}>
        {#if live}<span class="dot"></span> TRACKING · {s.cam ?? '—'}
        {:else}LAST SEEN · {s.cam ?? '—'} · {ago(s.last_ts)}{/if}
      </div>

      <div class="grp g1">
        <div class="prow caps"><span class="kk">ID</span><span class="vv">{s.id}</span></div>
        <div class="prow caps"><span class="kk">CLASS</span><span class="vv">{trUpper(s.cls)}</span></div>
        {#if s.attrs?.make}<div class="prow caps"><span class="kk">MAKE</span><span class="vv">{trUpper(s.attrs.make)}<span class="est"> ~est</span></span></div>{/if}
        {#if s.attrs?.subtype}<div class="prow caps"><span class="kk">TYPE</span><span class="vv">{trUpper(s.attrs.subtype)}</span></div>{/if}
        {#if s.plate}<div class="prow caps"><span class="kk">PLATE</span><span class="vv plate">{s.plate}</span></div>{/if}
        {#if apprLine(s)}<div class="prow caps"><span class="kk">APPEARANCE</span><span class="vv">{apprLine(s)}</span></div>{/if}
      </div>

      <div class="grp g2">
        <div class="gh caps">◇ CHRONOLOGY</div>
        <div class="prow caps"><span class="kk">FIRST SEEN</span><span class="vv">{s.first_cam ?? '—'} · {clock24(s.first_ts)}</span></div>
        <div class="prow caps"><span class="kk">LAST SEEN</span><span class="vv">{s.cam ?? '—'} · {ago(s.last_ts)}</span></div>
        <div class="prow caps"><span class="kk">SIGHTINGS</span><span class="vv">{s.obs}× over {hhmm(s.first_ts)}–{hhmm(s.last_ts)}</span></div>
      </div>

      {#if s.trail && s.trail.length}
        <div class="grp g3">
          <div class="gh caps">◇ MOVEMENT · {s.trail.length} CAM{s.trail.length > 1 ? 'S' : ''}</div>
          <ol class="trail">
            {#each s.trail as t, i (t.cam)}
              <li class="tstep caps" class:head={i === s.trail.length - 1}>
                <span class="tdot"></span>
                <span class="tcam">{t.cam}</span>
                <span class="tmeta">{clock24(t.first)}{#if t.last - t.first > 60000} – {clock24(t.last)}{/if} · {t.count}×</span>
              </li>
            {/each}
          </ol>
        </div>
      {/if}

      <div class="grp g4">
        <div class="gh caps">◇ NOTES</div>
        <textarea class="notes" placeholder="Add a note about this one…" value={a.notes ?? ''}
          oninput={(ev) => annotate(s.id, { notes: (ev.target as HTMLTextAreaElement).value })}></textarea>
      </div>
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

  .profile { position: fixed; top: 0; right: 0; width: 320px; height: 100%; background: #070a0d;
    border-left: 1px solid var(--scarlet); box-shadow: -14px 0 36px rgba(0,0,0,0.65); padding: 16px 16px 28px;
    display: flex; flex-direction: column; gap: 10px; overflow: auto; animation: slidein 260ms cubic-bezier(0.16, 1, 0.3, 1); }
  .profile.livepanel { border-left-color: var(--cyan); }
  @keyframes slidein { from { transform: translateX(28px); opacity: 0; } }
  .ptab { display: flex; align-items: center; gap: 8px; font-size: var(--fs-label);
    letter-spacing: var(--tracking); border-bottom: 1px solid var(--hairline); padding-bottom: 6px; }
  .ptab > span:first-child { flex: 1; }
  .live { font-size: 8px; letter-spacing: 0.12em; color: var(--ink-ghost); }
  .live.on { color: var(--cyan); animation: livepulse 1.6s ease-in-out infinite; }
  @keyframes livepulse { 50% { opacity: 0.4; } }
  .px { font-size: 16px; color: var(--ink-dim); background: none; border: 0; cursor: pointer; }
  .px:hover { color: var(--scarlet); }
  .pph { position: relative; }
  .pph img { display: block; width: 100%; aspect-ratio: 3 / 4; object-fit: cover;
    background: repeating-conic-gradient(#0d1114 0% 25%, #0a0d10 0% 50%) 50% / 18px 18px; }
  .pph.livef::after { content: ''; position: absolute; inset: 0; border: 1px solid var(--cyan);
    box-shadow: inset 0 0 22px rgba(56,189,248,0.18); pointer-events: none; animation: livepulse 1.8s ease-in-out infinite; }
  .pid { position: absolute; top: 6px; left: 6px; font-size: 9px; letter-spacing: 0.14em; color: #fff;
    background: rgba(5,7,10,0.72); padding: 2px 6px; text-shadow: 0 0 4px #000; }
  .cut { position: absolute; bottom: 6px; left: 6px; padding: 3px 8px; border: 1px solid var(--ink-dim);
    background: rgba(5,7,10,0.8); color: var(--ink-dim); font-size: 8px; letter-spacing: var(--tracking); cursor: pointer; }
  .cut.on { border-color: var(--scarlet); color: var(--scarlet); }
  .status { display: flex; align-items: center; gap: 6px; font-size: 8px; letter-spacing: 0.12em;
    color: var(--ink-dim); border: 1px solid var(--hairline); padding: 5px 8px; }
  .status.on { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 40%, transparent); }
  .status .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 6px var(--cyan);
    animation: livepulse 1.2s ease-in-out infinite; }
  /* staggered reveal so the dossier assembles section by section */
  .grp { display: flex; flex-direction: column; gap: 3px; animation: grpin 320ms both cubic-bezier(0.16, 1, 0.3, 1); }
  .g1 { animation-delay: 40ms; } .g2 { animation-delay: 100ms; }
  .g3 { animation-delay: 160ms; } .g4 { animation-delay: 220ms; }
  @keyframes grpin { from { transform: translateY(8px); opacity: 0; } }
  .gh { font-size: 8px; color: var(--ink-dim); letter-spacing: 0.14em; border-bottom: 1px solid var(--hairline);
    padding-bottom: 3px; margin-bottom: 2px; }
  .prow { display: flex; justify-content: space-between; gap: 10px; font-size: 9px; }
  .prow .kk { color: var(--ink-dim); } .prow .vv { color: var(--ink); text-align: right; }
  .prow .plate { color: var(--cyan); letter-spacing: 0.1em; font-weight: 700; }
  .prow .est { color: var(--ink-dim); letter-spacing: 0.06em; }
  /* movement timeline across cameras */
  .trail { list-style: none; margin: 2px 0 0; padding: 0 0 0 4px; }
  .tstep { position: relative; display: flex; flex-direction: column; padding: 0 0 9px 14px;
    border-left: 1px solid var(--hairline); }
  .tstep:last-child { border-left-color: transparent; }
  .tdot { position: absolute; left: -4px; top: 2px; width: 7px; height: 7px; border-radius: 50%;
    background: #0a0d10; border: 1px solid var(--ink-dim); }
  .tstep.head .tdot { background: var(--cyan); border-color: var(--cyan); box-shadow: 0 0 6px var(--cyan); }
  .tcam { font-size: 9px; color: var(--ink); letter-spacing: 0.08em; }
  .tmeta { font-size: 8px; color: var(--ink-dim); }
  .notes { min-height: 64px; resize: vertical; background: #05070a; border: 1px solid var(--hairline);
    color: var(--ink); font-family: inherit; font-size: 10px; padding: 6px; }
  .notes:focus { border-color: var(--scarlet); outline: none; }
</style>
