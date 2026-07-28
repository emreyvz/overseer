<script lang="ts">
  // Full-screen "person / vehicle of interest" dossier — a cinematic detail screen that
  // replaces the side panel. Shows the subject, live cross-camera tracking, a movement trail,
  // chronology and appearance. Opens with a staggered reveal; ESC / scrim / × animate it out.
  import { onDestroy, onMount } from 'svelte'
  import { cameras } from '../../lib/stores'
  import { annotations, annotate } from '../../lib/annotations'
  import { trUpper } from '../../lib/lexicon'
  import { sfx } from '../../lib/audio'
  import LiveThumb from '../LiveThumb.svelte'
  import type { RosterEntry } from '../../lib/types'

  let { entry, now, onclose }: { entry: RosterEntry; now: number; onclose: () => void } = $props()

  const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
  const LIVE_MS = 8000
  let cutout = $state(false)
  let closing = $state(false)

  let a = $derived($annotations[entry.id] ?? {})
  let live = $derived(now - entry.last_ts < LIVE_MS)
  let title = $derived(entry.cls === 'vehicle' ? 'VEHICLE OF INTEREST' : 'PERSON OF INTEREST')
  const camId = (name?: string | null) => $cameras.find((c) => c.name === name)?.id
  let curCamId = $derived(camId(entry.cam))
  let trailCams = $derived((entry.trail ?? []).map((t) => ({ ...t, id: camId(t.cam) })))
  const photo = $derived(entry.snapshot ? `${API}${entry.snapshot}?t=${entry.last_ts}` : '')
  const heroSrc = $derived(cutout ? `${API}/api/roster/${entry.id}/cutout?t=${entry.last_ts}` : photo)
  const apprLine = $derived(
    [entry.attrs?.upper_color, entry.cls === 'person' ? entry.attrs?.height : undefined]
      .filter(Boolean).map((s) => trUpper(String(s))).join(' · '))

  const clock24 = (ms: number) => { const d = new Date(ms); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':') }
  const hhmm = (ms: number) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
  function ago(ms: number): string {
    const s = Math.max(0, Math.round((now - ms) / 1000))
    if (s < 5) return 'just now'
    if (s < 60) return `${s}s ago`
    const m = Math.round(s / 60)
    return m < 60 ? `${m}m ago` : `${Math.round(m / 60)}h ago`
  }
  function duration(ms: number): string {
    const m = Math.round(ms / 60000)
    if (m < 1) return '<1m'
    return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`
  }

  function close() {
    if (closing) return
    sfx('click'); closing = true
    setTimeout(onclose, 360)
  }
  function onkey(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); close() } }
  onMount(() => { sfx('sonar'); window.addEventListener('keydown', onkey, true) })
  onDestroy(() => window.removeEventListener('keydown', onkey, true))
</script>

<div class="poi" class:closing role="dialog" aria-modal="true" aria-label={title}>
  <button class="scrim" onclick={close} aria-label="Close dossier"></button>

  <div class="sheet" class:closing>
    <header class="top caps">
      <span class="eyebrow">◈ {title}</span>
      <span class="idc">{a.alias || entry.id}</span>
      <span class="spacer"></span>
      <span class="live" class:on={live}>{live ? '● LIVE' : '○ IDLE'}</span>
      <button class="x" onclick={close} aria-label="Close">×</button>
    </header>

    <div class="body">
      <!-- HERO -->
      <section class="hero">
        <div class="frame" class:livef={live}>
          {#if entry.snapshot}
            <img src={heroSrc} alt="" />
            <span class="corner tl"></span><span class="corner tr"></span>
            <span class="corner bl"></span><span class="corner br"></span>
            <button class="cut caps" class:on={cutout} onclick={() => (cutout = !cutout)}>{cutout ? '◧ BG' : '◨ CUT'}</button>
          {:else}<div class="noimg caps">NO IMAGE</div>{/if}
        </div>
        <div class="chips">
          <span class="chip caps">{trUpper(entry.cls)}</span>
          {#if entry.attrs?.subtype}<span class="chip caps">{trUpper(entry.attrs.subtype)}</span>{/if}
          {#if entry.attrs?.make}<span class="chip caps">{trUpper(entry.attrs.make)} ~est</span>{/if}
          {#if entry.attrs?.upper_color}<span class="chip caps">{trUpper(entry.attrs.upper_color)}</span>{/if}
          {#if entry.plate}<span class="chip plate caps">▤ {entry.plate}</span>{/if}
        </div>
      </section>

      <!-- DETAIL COLUMN -->
      <section class="detail">
        <!-- LIVE TRACKING -->
        <div class="panel p1">
          <div class="ph caps"><span>◇ LIVE TRACKING</span><span class="ph-cam">{entry.cam ?? '—'}</span></div>
          <div class="track">
            <div class="tcam" class:livef={live}>
              {#if curCamId}
                <LiveThumb id={curCamId} fps={4} />
              {:else if photo}<img class="fallback" src={photo} alt="" />{/if}
              <span class="tstat caps" class:on={live}>{live ? '● TRACKING' : `LAST · ${ago(entry.last_ts)}`}</span>
            </div>
            <div class="tside">
              <div class="tref">
                {#if photo}<img src={photo} alt="" />{/if}
                <span class="caps">TARGET REF</span>
              </div>
              <div class="tmeta caps">
                <div><span class="k">ON CAMERA</span><span class="v">{entry.cam ?? '—'}</span></div>
                <div><span class="k">STATUS</span><span class="v" class:hot={live}>{live ? 'IN VIEW' : 'OUT OF VIEW'}</span></div>
                <div><span class="k">LAST SEEN</span><span class="v">{ago(entry.last_ts)}</span></div>
              </div>
            </div>
          </div>
        </div>

        <!-- CHRONOLOGY -->
        <div class="panel p2">
          <div class="ph caps">◇ CHRONOLOGY</div>
          <div class="rows">
            <div class="row caps"><span class="k">FIRST SEEN</span><span class="v">{entry.first_cam ?? '—'} · {clock24(entry.first_ts)}</span></div>
            <div class="row caps"><span class="k">LAST SEEN</span><span class="v">{entry.cam ?? '—'} · {ago(entry.last_ts)}</span></div>
            <div class="row caps"><span class="k">TRACKED FOR</span><span class="v">{duration(entry.last_ts - entry.first_ts)} · {hhmm(entry.first_ts)}–{hhmm(entry.last_ts)}</span></div>
            <div class="row caps"><span class="k">SIGHTINGS</span><span class="v">{entry.obs}×</span></div>
          </div>
        </div>

        <!-- MOVEMENT TRAIL + live camera gallery -->
        {#if trailCams.length}
          <div class="panel p3">
            <div class="ph caps"><span>◇ MOVEMENT</span><span class="ph-cam">{trailCams.length} CAM{trailCams.length > 1 ? 'S' : ''}</span></div>
            <div class="gallery">
              {#each trailCams as t, i (t.cam)}
                <div class="gcam" class:cur={t.cam === entry.cam}>
                  <div class="gthumb">
                    {#if t.id}<LiveThumb id={t.id} fps={2} />{:else}<div class="noimg caps">OFFLINE</div>{/if}
                    <span class="gseq caps">{i + 1}</span>
                    {#if t.cam === entry.cam && live}<span class="gdot"></span>{/if}
                  </div>
                  <div class="gcap caps"><span class="gname">{t.cam}</span><span class="gtime">{clock24(t.first)} · {t.count}×</span></div>
                </div>
              {/each}
            </div>
          </div>
        {/if}

        <!-- APPEARANCE + NOTES -->
        <div class="panel p4">
          <div class="ph caps">◇ DOSSIER</div>
          <div class="rows">
            <div class="row caps"><span class="k">ID</span><span class="v">{entry.id}</span></div>
            {#if apprLine}<div class="row caps"><span class="k">APPEARANCE</span><span class="v">{apprLine}</span></div>{/if}
            {#if a.owner}<div class="row caps"><span class="k">OWNER</span><span class="v">{trUpper(a.owner)}</span></div>{/if}
          </div>
          <textarea class="notes" placeholder="Add an intelligence note…" value={a.notes ?? ''}
            oninput={(ev) => annotate(entry.id, { notes: (ev.target as HTMLTextAreaElement).value })}></textarea>
        </div>
      </section>
    </div>
  </div>
</div>

<style>
  .poi { position: fixed; inset: 0; z-index: var(--z-boot); display: flex; align-items: center; justify-content: center; }
  .scrim { position: absolute; inset: 0; border: 0; cursor: pointer; background: rgba(2,3,5,0.72);
    backdrop-filter: blur(6px); animation: scrimin 300ms ease both; }
  .poi.closing .scrim { animation: scrimout 320ms ease both; }
  @keyframes scrimin { from { opacity: 0; backdrop-filter: blur(0); } }
  @keyframes scrimout { to { opacity: 0; } }

  .sheet { position: relative; width: min(1180px, 94vw); height: min(88vh, 860px); background: #070a0d;
    border: 1px solid var(--scarlet); box-shadow: 0 30px 90px rgba(0,0,0,0.7), inset 0 0 60px rgba(0,0,0,0.5);
    display: flex; flex-direction: column; overflow: hidden;
    animation: sheetin 420ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  .poi.closing .sheet { animation: sheetout 340ms cubic-bezier(0.4, 0, 1, 1) both; }
  @keyframes sheetin { from { transform: scale(0.94) translateY(14px); opacity: 0; } }
  @keyframes sheetout { to { transform: scale(0.97) translateY(8px); opacity: 0; } }
  /* faint scan grid for the command-center feel */
  .sheet::before { content: ''; position: absolute; inset: 0; pointer-events: none; opacity: 0.5;
    background: linear-gradient(transparent 96%, rgba(255,255,255,0.03) 100%) 0 0 / 100% 4px; }

  .top { display: flex; align-items: center; gap: 12px; padding: 12px 18px; border-bottom: 1px solid var(--hairline);
    font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .eyebrow { color: var(--scarlet); }
  .idc { color: var(--ink); font-weight: 700; letter-spacing: 0.16em; }
  .spacer { flex: 1; }
  .live { font-size: 9px; color: var(--ink-ghost); letter-spacing: 0.12em; }
  .live.on { color: var(--cyan); animation: pulse 1.6s ease-in-out infinite; }
  .x { font-size: 20px; line-height: 1; color: var(--ink-dim); background: none; border: 0; cursor: pointer; padding: 0 4px; }
  .x:hover { color: var(--scarlet); }

  .body { flex: 1; display: grid; grid-template-columns: 340px 1fr; gap: 0; min-height: 0; }
  .hero { padding: 18px; border-right: 1px solid var(--hairline); display: flex; flex-direction: column; gap: 14px;
    animation: rise 460ms cubic-bezier(0.16, 1, 0.3, 1) both; animation-delay: 80ms; }
  .frame { position: relative; aspect-ratio: 3 / 4; background: repeating-conic-gradient(#0d1114 0% 25%, #0a0d10 0% 50%) 50% / 20px 20px;
    overflow: hidden; }
  .frame img { width: 100%; height: 100%; object-fit: cover; filter: saturate(0.7) contrast(1.06); }
  .frame.livef { box-shadow: inset 0 0 0 1px var(--cyan), inset 0 0 26px rgba(56,189,248,0.18); }
  .frame.livef::after { content: ''; position: absolute; inset: 0; animation: pulse 1.9s ease-in-out infinite;
    box-shadow: inset 0 0 22px rgba(56,189,248,0.22); pointer-events: none; }
  .corner { position: absolute; width: 14px; height: 14px; border: 2px solid var(--ink); opacity: 0.9; }
  .tl { top: 6px; left: 6px; border-right: 0; border-bottom: 0; } .tr { top: 6px; right: 6px; border-left: 0; border-bottom: 0; }
  .bl { bottom: 6px; left: 6px; border-right: 0; border-top: 0; } .br { bottom: 6px; right: 6px; border-left: 0; border-top: 0; }
  .noimg { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: var(--fs-micro); }
  .cut { position: absolute; bottom: 8px; right: 8px; padding: 3px 8px; border: 1px solid var(--ink-dim);
    background: rgba(5,7,10,0.82); color: var(--ink-dim); font-size: 8px; letter-spacing: var(--tracking); cursor: pointer; }
  .cut.on { border-color: var(--scarlet); color: var(--scarlet); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { padding: 4px 9px; border: 1px solid var(--hairline); color: var(--ink); font-size: 8px; letter-spacing: 0.1em; }
  .chip.plate { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 40%, transparent); font-weight: 700; }

  .detail { padding: 16px 18px; overflow: auto; display: flex; flex-direction: column; gap: 14px; }
  .panel { border: 1px solid var(--hairline); padding: 10px 12px; animation: rise 460ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  .p1 { animation-delay: 130ms; } .p2 { animation-delay: 190ms; } .p3 { animation-delay: 250ms; } .p4 { animation-delay: 310ms; }
  @keyframes rise { from { transform: translateY(14px); opacity: 0; } }
  .ph { display: flex; justify-content: space-between; align-items: center; font-size: 9px; color: var(--ink-dim);
    letter-spacing: 0.14em; border-bottom: 1px solid var(--hairline); padding-bottom: 6px; margin-bottom: 8px; }
  .ph-cam { color: var(--ink); }

  /* live tracking */
  .track { display: grid; grid-template-columns: 1.5fr 1fr; gap: 10px; }
  .tcam { position: relative; aspect-ratio: 16 / 10; background: #05070a; overflow: hidden; border: 1px solid var(--hairline); }
  .tcam.livef { border-color: color-mix(in srgb, var(--cyan) 45%, transparent); }
  .tcam :global(.img), .tcam .fallback { width: 100%; height: 100%; object-fit: cover; }
  .tstat { position: absolute; left: 6px; bottom: 6px; font-size: 8px; color: var(--ink-dim); letter-spacing: 0.1em;
    background: rgba(5,7,10,0.7); padding: 2px 6px; }
  .tstat.on { color: var(--cyan); }
  .tside { display: flex; flex-direction: column; gap: 8px; }
  .tref { position: relative; aspect-ratio: 3 / 4; background: #05070a; border: 1px solid var(--hairline); overflow: hidden; }
  .tref img { width: 100%; height: 100%; object-fit: cover; }
  .tref span { position: absolute; bottom: 4px; left: 4px; font-size: 7px; color: var(--ink-dim); background: rgba(5,7,10,0.7); padding: 1px 4px; }
  .tmeta { display: flex; flex-direction: column; gap: 3px; }
  .tmeta > div { display: flex; justify-content: space-between; font-size: 8px; }

  .rows { display: flex; flex-direction: column; gap: 4px; }
  .row { display: flex; justify-content: space-between; gap: 10px; font-size: 9px; }
  .k, .tmeta .k { color: var(--ink-dim); } .v, .tmeta .v { color: var(--ink); text-align: right; }
  .v.hot { color: var(--cyan); }

  /* movement gallery of live camera thumbnails */
  .gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(116px, 1fr)); gap: 10px; }
  .gcam { display: flex; flex-direction: column; gap: 4px; }
  .gthumb { position: relative; aspect-ratio: 16 / 10; background: #05070a; border: 1px solid var(--hairline); overflow: hidden; }
  .gcam.cur .gthumb { border-color: var(--cyan); box-shadow: 0 0 0 1px var(--cyan); }
  .gthumb :global(.img) { width: 100%; height: 100%; object-fit: cover; }
  .gseq { position: absolute; top: 3px; left: 3px; font-size: 8px; color: #fff; background: rgba(5,7,10,0.75); padding: 1px 5px; }
  .gdot { position: absolute; top: 5px; right: 5px; width: 7px; height: 7px; border-radius: 50%; background: var(--cyan);
    box-shadow: 0 0 6px var(--cyan); animation: pulse 1.2s ease-in-out infinite; }
  .gcap { display: flex; flex-direction: column; }
  .gname { font-size: 8px; color: var(--ink); letter-spacing: 0.06em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .gtime { font-size: 7px; color: var(--ink-dim); }

  .notes { margin-top: 8px; width: 100%; min-height: 58px; resize: vertical; background: #05070a; border: 1px solid var(--hairline);
    color: var(--ink); font-family: inherit; font-size: 10px; padding: 6px; }
  .notes:focus { border-color: var(--scarlet); outline: none; }
  @keyframes pulse { 50% { opacity: 0.45; } }

  @media (max-width: 720px) {
    .body { grid-template-columns: 1fr; }
    .hero { border-right: 0; border-bottom: 1px solid var(--hairline); }
  }
</style>
