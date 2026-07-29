<script lang="ts">
  // Investigation workspace for a single case. An alert opens here instead of jumping to the
  // live feed: the incident, a player, a timeline of the surrounding scene events (click to
  // review each), an AI narrative summary, evidence, a status lifecycle and notes.
  import { onMount, onDestroy } from 'svelte'
  import { api, type CaseDetail, type CaseEventRow } from '../../lib/api'
  import { cameras, activeCam, mode, stage, pickerView, spatialOpen, flashBanner, triggerGlitch } from '../../lib/stores'
  import { sendCommand } from '../../lib/ws'
  import { SIM } from '../../lib/sim'
  import { sfx } from '../../lib/audio'

  let { caseId, onclose }: { caseId: number; onclose: () => void } = $props()
  const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
  const STATUSES = ['open', 'investigating', 'review', 'resolved', 'archived']

  let d = $state<CaseDetail | null>(null)
  let loading = $state(true)
  let focus = $state<CaseEventRow | null>(null)
  let notesTimer: ReturnType<typeof setTimeout> | undefined

  async function load() {
    loading = true
    try {
      d = await api.caseDetail(caseId)
      focus = d?.events.find((e) => e.kind === 'alert') ?? d?.events[0] ?? null
    } catch { d = null }
    loading = false
  }
  $effect(() => { void caseId; load() })
  onMount(() => { sfx('sonar'); window.addEventListener('keydown', onkey, true) })
  onDestroy(() => { window.removeEventListener('keydown', onkey, true); if (notesTimer) clearTimeout(notesTimer) })
  function onkey(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); onclose() } }

  const src = (u?: string | null) => (u ? (u.startsWith('http') ? u : API + u) : '')
  const clock = (ms: number) => { const t = new Date(ms); return [t.getHours(), t.getMinutes(), t.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':') }
  const dmy = (ms: number) => new Date(ms).toLocaleDateString('en-GB')
  const evImg = $derived((d?.events ?? []).filter((e) => e.snapshot))

  async function setStatus(s: string) {
    if (!d || d.status === s) return
    sfx('click'); d = { ...d, status: s }
    try { await api.caseStatus(caseId, s) } catch { /* offline */ }
  }
  function onNotes(v: string) {
    if (!d) return
    d = { ...d, notes: v }
    if (notesTimer) clearTimeout(notesTimer)
    notesTimer = setTimeout(() => api.updateCase(caseId, { notes: v }).catch(() => {}), 600)
  }
  function pick(e: CaseEventRow) { sfx('ping', { volume: 0.25 }); focus = e }
  function observe(cam: string) {
    const c = $cameras.find((x) => x.name === cam)
    if (!c) return
    sfx('glitch'); triggerGlitch(200); activeCam.set(c.id)
    if (!SIM) sendCommand(`connect:${c.name}`)
    flashBanner(`OBSERVING ${c.name}`, false, 1200); mode.set('pov')
  }
  const sevClass = (s: string) => (s === 'critical' ? 'crit' : s === 'warning' ? 'warn' : 'info')
  // leave the investigation and go straight back to the world map
  function toMap() { sfx('whoosh'); triggerGlitch(160); onclose(); mode.set('pov'); pickerView.set('map'); stage.set('select') }
  // resolve a camera NAME (as the timeline carries) to its source id, for the 3D scene view
  const spatialId = (name?: string | null): string | null => (name ? ($cameras.find((c) => c.name === name)?.id ?? null) : null)
</script>

<div class="inv" role="dialog" aria-label="Investigation">
  <header class="top caps">
    <span class="eyebrow">◈ INVESTIGATION</span>
    <span class="cname">{d?.name ?? `CASE ${caseId}`}</span>
    {#if d}<span class="threat t-{d.threat}">{d.threat}</span>{/if}
    <span class="spacer"></span>
    <div class="status caps">
      {#each STATUSES as s}
        <button class="st" class:on={d?.status === s} onclick={() => setStatus(s)}>{s}</button>
      {/each}
    </div>
    <button class="map caps" onclick={toMap} title="Back to the map">◄ MAP</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  {#if loading}
    <div class="empty caps">LOADING INVESTIGATION_</div>
  {:else if !d}
    <div class="empty caps">CASE NOT FOUND</div>
  {:else}
    <div class="body">
      <!-- LEFT: player + AI narrative + evidence -->
      <section class="stage">
        <div class="player">
          {#if focus?.clip}
            <!-- svelte-ignore a11y_media_has_caption -->
            <video src={src(focus.clip)} autoplay loop controls></video>
          {:else if focus?.snapshot}
            <img src={src(focus.snapshot)} alt="" />
          {:else}<div class="noimg caps">NO FOOTAGE FOR THIS MOMENT</div>{/if}
          {#if spatialId(focus?.cam)}
            <button class="spatial3d caps" onclick={() => spatialOpen.set(spatialId(focus?.cam))} title="Reconstruct this camera's scene in 3D">⛶ 3D SCENE</button>
          {/if}
          {#if focus}
            <div class="pcap caps"><span class="ptype">{focus.type}</span><span class="pmeta">{focus.cam} · {clock(focus.ts)}</span></div>
          {/if}
        </div>

        <div class="panel ai">
          <div class="ph caps"><span>◇ AI RECONSTRUCTION</span></div>
          {#if d.aiSummary}
            <p class="narr">{d.aiSummary}</p>
          {:else}
            <p class="narr off">Enable the AI assistant to get a natural-language reconstruction of this incident — what happened, who was involved and why it matters. The timeline on the right is the raw sequence.</p>
          {/if}
        </div>

        {#if evImg.length}
          <div class="panel">
            <div class="ph caps"><span>◇ EVIDENCE</span><span class="phc">{evImg.length}</span></div>
            <div class="evidence">
              {#each evImg as e, i (e.ts + i)}
                <button class="ev" class:on={focus === e} onclick={() => pick(e)}>
                  <img src={src(e.snapshot)} alt="" />
                  <span class="evt caps">{clock(e.ts)}</span>
                </button>
              {/each}
            </div>
          </div>
        {/if}

        <div class="panel">
          <div class="ph caps">◇ NOTES</div>
          <textarea class="notes" placeholder="Investigation notes…" value={d.notes}
            oninput={(ev) => onNotes((ev.target as HTMLTextAreaElement).value)}></textarea>
        </div>
      </section>

      <!-- RIGHT: incident summary + timeline -->
      <aside class="side">
        <div class="panel">
          <div class="ph caps">◇ INCIDENT</div>
          <div class="rows caps">
            <div class="r"><span class="k">OPENED</span><span class="v">{clock(d.created)} · {dmy(d.created)}</span></div>
            <div class="r"><span class="k">CAMERAS</span><span class="v">{d.cameras.join(', ') || '—'}</span></div>
            <div class="r"><span class="k">EVENTS</span><span class="v">{d.events.length}</span></div>
          </div>
        </div>

        {#if d.subjects?.length}
          <div class="panel">
            <div class="ph caps"><span>◇ AT THE SCENE</span><span class="phc">{d.subjects.length}</span></div>
            <div class="subs">
              {#each d.subjects as s (s.id)}
                <div class="sub">
                  <div class="sface">{#if s.snapshot}<img src={src(s.snapshot)} alt="" />{:else}<div class="snone caps">{s.cls === 'vehicle' ? '🚗' : '👤'}</div>{/if}</div>
                  <div class="smeta caps">
                    <span class="sid">{s.id}{#if s.plate} · ▤ {s.plate}{/if}</span>
                    <span class="sseen">SEEN {s.seen}× HERE</span>
                    {#if s.associates.length}
                      <span class="slinks" title="Frequently seen together">↔ {s.associates.map((a) => a.id).join(' · ')}</span>
                    {:else}
                      <span class="slinks off">NO KNOWN LINKS</span>
                    {/if}
                  </div>
                </div>
              {/each}
            </div>
          </div>
        {/if}

        <div class="panel tlpanel">
          <div class="ph caps"><span>◇ TIMELINE</span><span class="phc">{d.events.length}</span></div>
          <ol class="timeline">
            {#each d.events as e, i (e.ts + '' + i)}
              <li>
                <button class="tev sev-{sevClass(e.severity)}" class:on={focus === e} class:alert={e.kind === 'alert'}
                  onclick={() => pick(e)} ondblclick={() => observe(e.cam)}>
                  <span class="tdot"></span>
                  <span class="tbody">
                    <span class="trow"><span class="ttype">{e.type || e.summary}</span><span class="ttime">{clock(e.ts)}</span></span>
                    <span class="tsub">{e.cam}{#if e.summary && e.summary !== e.type} · {e.summary}{/if}</span>
                  </span>
                  {#if e.snapshot}<img class="tthumb" src={src(e.snapshot)} alt="" />{/if}
                </button>
              </li>
            {/each}
          </ol>
        </div>
      </aside>
    </div>
  {/if}
</div>

<style>
  .inv { position: absolute; inset: 0; z-index: var(--z-boot); background: radial-gradient(120% 80% at 50% 0%, #0a1016 0%, #05070a 72%);
    color: var(--ink); display: flex; flex-direction: column; overflow: hidden; animation: invin 340ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes invin { from { opacity: 0; clip-path: inset(0 0 8% 0); } }
  .top { display: flex; align-items: center; gap: 12px; padding: 13px 22px; border-bottom: 1px solid var(--hairline);
    font-size: var(--fs-label); letter-spacing: var(--tracking); background: #04070a; }
  .eyebrow { color: var(--scarlet); }
  .cname { color: var(--ink); font-weight: 700; letter-spacing: 0.12em; }
  .threat { padding: 2px 8px; font-size: 8px; letter-spacing: 0.14em; border: 1px solid var(--hairline); }
  .threat.t-high { background: var(--scarlet); color: #fff; border-color: var(--scarlet); }
  .threat.t-medium { color: var(--amber, #d8a200); border-color: color-mix(in srgb, var(--amber, #d8a200) 45%, transparent); }
  .spacer { flex: 1; }
  .status { display: flex; gap: 1px; background: var(--hairline); border: 1px solid var(--hairline); }
  .st { padding: 5px 10px; background: #0a0d10; color: var(--ink-dim); border: 0; font-size: 8px; letter-spacing: 0.1em; cursor: pointer; }
  .st:hover { color: var(--ink); } .st.on { background: var(--cyan); color: #04070a; }
  .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: pointer; font-size: 9px; letter-spacing: var(--tracking); }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .map { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: pointer; font-size: 9px; letter-spacing: var(--tracking); }
  .map:hover { border-color: var(--cyan); color: var(--cyan); }
  .empty { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--ink-dim); letter-spacing: 0.2em; }

  .body { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 14px; padding: 14px; }
  .stage { display: flex; flex-direction: column; gap: 14px; min-height: 0; overflow: auto; }
  .side { display: flex; flex-direction: column; gap: 14px; min-height: 0; overflow: auto; }
  .panel { border: 1px solid var(--hairline); background: rgba(7,11,14,0.6); }
  .ph { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: #04070a;
    border-bottom: 1px solid var(--hairline); font-size: 9px; color: var(--scarlet); letter-spacing: var(--tracking); }
  .phc { color: var(--ink-dim); }

  .player { position: relative; aspect-ratio: 16/9; background: #05070a; border: 1px solid var(--hairline); overflow: hidden; }
  .player video, .player img { width: 100%; height: 100%; object-fit: contain; background: #000; }
  .spatial3d { position: absolute; top: 8px; right: 8px; z-index: 3; padding: 4px 10px; border: 1px solid var(--ink-dim);
    background: rgba(0,0,0,0.55); color: var(--ink-dim); font-size: 9px; letter-spacing: var(--tracking); cursor: pointer; }
  .spatial3d:hover { border-color: var(--cyan); color: var(--cyan); }
  .noimg { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: 10px; letter-spacing: 0.16em; }
  .pcap { position: absolute; left: 0; right: 0; bottom: 0; display: flex; justify-content: space-between; padding: 8px 12px;
    background: linear-gradient(transparent, rgba(4,7,10,0.85)); font-size: 9px; }
  .ptype { color: var(--ink); letter-spacing: 0.1em; } .pmeta { color: var(--ink-dim); }

  .ai .narr { margin: 0; padding: 12px 14px; font-size: 12px; line-height: 1.6; color: var(--ink); letter-spacing: 0.01em; }
  .ai .narr.off { color: var(--ink-dim); font-size: 11px; }

  .evidence { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; padding: 10px; }
  .ev { position: relative; aspect-ratio: 4/3; background: #05070a; border: 1px solid var(--hairline); overflow: hidden; cursor: pointer; padding: 0; }
  .ev.on { border-color: var(--cyan); box-shadow: 0 0 0 1px var(--cyan); }
  .ev img { width: 100%; height: 100%; object-fit: cover; }
  .evt { position: absolute; bottom: 2px; left: 3px; font-size: 7px; color: #fff; background: rgba(5,7,10,0.7); padding: 1px 4px; }

  .notes { width: 100%; min-height: 70px; resize: vertical; background: #05070a; border: 0; color: var(--ink);
    font-family: var(--font-mono); font-size: 10px; padding: 8px; }
  .notes:focus { outline: none; }

  .rows { padding: 9px 12px; display: flex; flex-direction: column; gap: 5px; }
  .r { display: flex; justify-content: space-between; gap: 10px; font-size: 9px; }
  .r .k { color: var(--ink-dim); } .r .v { color: var(--ink); text-align: right; }

  /* AT THE SCENE — roster subjects present at the incident + who they're linked to */
  .subs { display: flex; flex-direction: column; gap: 6px; padding: 8px; max-height: 190px; overflow-y: auto; }
  .sub { display: flex; gap: 8px; align-items: center; }
  .sface { width: 38px; height: 38px; flex: none; background: #05070a; border: 1px solid var(--hairline); overflow: hidden; }
  .sface img { width: 100%; height: 100%; object-fit: cover; }
  .snone { display: flex; align-items: center; justify-content: center; height: 100%; font-size: 15px; }
  .smeta { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
  .sid { font-size: 9px; color: var(--ink); letter-spacing: 0.06em; }
  .sseen { font-size: 7px; color: var(--ink-dim); }
  .slinks { font-size: 7px; color: var(--cyan); letter-spacing: 0.04em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .slinks.off { color: var(--ink-ghost); }
  .tlpanel { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .timeline { list-style: none; margin: 0; padding: 6px 8px; overflow-y: auto; }
  .tev { position: relative; display: flex; align-items: center; gap: 8px; width: 100%; padding: 7px 8px 7px 16px;
    background: none; border: 0; border-left: 1px solid var(--hairline); cursor: pointer; text-align: left; }
  .tev .tdot { position: absolute; left: -4px; top: 12px; width: 7px; height: 7px; border-radius: 50%; background: #0a0d10; border: 1px solid var(--ink-dim); }
  .tev.sev-crit .tdot { background: var(--scarlet); border-color: var(--scarlet); box-shadow: 0 0 6px var(--scarlet-glow); }
  .tev.sev-warn .tdot { background: var(--amber, #d8a200); border-color: var(--amber, #d8a200); }
  .tev.alert { background: rgba(225,6,0,0.05); }
  .tev.on { background: rgba(56,208,227,0.08); }
  .tev:hover { background: rgba(56,208,227,0.05); }
  .tbody { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  .trow { display: flex; justify-content: space-between; gap: 8px; }
  .ttype { font-size: 9px; color: var(--ink); letter-spacing: 0.06em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ttime { font-size: 8px; color: var(--ink-dim); }
  .tsub { font-size: 8px; color: var(--ink-ghost); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tthumb { width: 42px; height: 30px; object-fit: cover; border: 1px solid var(--hairline); flex: 0 0 auto; }

  @media (max-width: 900px) { .body { grid-template-columns: 1fr; } }
</style>
