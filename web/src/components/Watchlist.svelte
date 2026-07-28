<script lang="ts">
  // Watchlist browser — find enrolled entities later. Filter by kind / threat,
  // jump to a forensic search for one, re-classify its threat, or remove it.
  import { watchlist, removeEntity, setThreat, type EntityKind, type ThreatLevel } from '../lib/watchlist'
  import { watchlistOpen, forensicSeed, mode, stage, selectedDetection, activeCam, cameras, flashBanner, matchHighlight } from '../lib/stores'
  import { sendCommand } from '../lib/ws'
  import { SIM } from '../lib/sim'
  import { api } from '../lib/api'
  import { matchBanner } from '../lib/match'
  import { matchMinScore } from '../lib/feedback'
  import { sfx } from '../lib/audio'

  const KIND_ICON: Record<EntityKind, string> = { person: '👤', vehicle: '🚗', pet: '🐾', object: '⬢' }
  const THREAT_NEXT: Record<ThreatLevel, ThreatLevel> = { safe: 'watch', watch: 'threat', threat: 'safe' }
  const hhmm = (ts: number) => { const d = new Date(ts); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }

  let kindF = $state<'' | EntityKind>('')
  let threatF = $state<'' | ThreatLevel>('')
  let shown = $derived($watchlist.filter((e) => (!kindF || e.kind === kindF) && (!threatF || e.threat === threatF)))

  let scanning = $state('')
  // Visual match: find the entity in a live feed by its IMAGE (appearance), not a
  // colour name. Falls back to the forensic attribute scan if it has no image.
  async function locate(e: (typeof $watchlist)[number]) {
    if (!e.image || SIM) {
      sfx('sonar')
      forensicSeed.set([e.color, e.height, e.kind === 'pet' ? 'animal' : e.kind].filter(Boolean).join(' '))
      selectedDetection.set(null); watchlistOpen.set(false); mode.set('forensic'); stage.set('live')
      return
    }
    scanning = e.id; sfx('sonar')
    const minWait = new Promise((r) => setTimeout(r, 1700)) // keep the scan screen up ≥1.7s
    try {
      const [res] = await Promise.all([api.visualMatch(e.image, e.kind, matchMinScore(e.kind)), minWait])
      const best = res.matches?.[0]
      if (best) {
        const cam = $cameras.find((c) => c.id === best.camId)
        activeCam.set(best.camId)
        if (!SIM && cam) sendCommand(`connect:${cam.name}`)
        selectedDetection.set({
          id: `WL.${e.id}`, cls: (e.kind === 'pet' ? 'animal' : e.kind) as any, bbox: best.bbox,
          conf: best.score, severity: e.threat === 'threat' ? 'critical' : 'info',
          klass: 'TARGET', caseAlias: e.name,
        })
        matchHighlight.set({ camId: best.camId, bbox: best.bbox, ts: Date.now() })
        sfx('ping', { critical: true, volume: 0.5 })   // "found" cue (bypasses the 2-sound cap)
        watchlistOpen.set(false); mode.set('pov'); stage.set('live')
        flashBanner(matchBanner(best.cam, best.score, best.ambiguous), false, 1600)
      } else {
        flashBanner('NOT FOUND IN ANY LIVE FEED', true, 1600)
      }
    } catch {
      await minWait
      flashBanner('MATCH FAILED', true, 1400)
    }
    scanning = ''
  }
  let scanEntity = $derived($watchlist.find((x) => x.id === scanning) ?? null)
  function close() { sfx('click'); watchlistOpen.set(false) }
</script>

<section class="wl">
  <div class="hdr caps"><span class="hot">///</span> WATCHLIST · {$watchlist.length} <span class="hint">ESC</span></div>

  <div class="filters">
    <div class="grp">
      <span class="gl caps">KIND</span>
      <button class="chip caps" class:on={kindF === ''} onclick={() => (kindF = '')}>ALL</button>
      {#each ['person', 'vehicle', 'pet', 'object'] as k}
        <button class="chip caps" class:on={kindF === k} onclick={() => (kindF = k as EntityKind)}>{KIND_ICON[k as EntityKind]} {k}</button>
      {/each}
    </div>
    <div class="grp">
      <span class="gl caps">THREAT</span>
      <button class="chip caps" class:on={threatF === ''} onclick={() => (threatF = '')}>ALL</button>
      {#each ['safe', 'watch', 'threat'] as t}
        <button class="chip caps thr-{t}" class:on={threatF === t} onclick={() => (threatF = t as ThreatLevel)}>{t}</button>
      {/each}
    </div>
  </div>

  {#if shown.length === 0}
    <div class="empty caps">NO ENROLLED ENTITIES<br /><span>SELECT A TARGET IN POV → ⊕ ENROLL</span></div>
  {:else}
    <div class="grid">
      {#each shown as e (e.id)}
        <div class="card thr-{e.threat}">
          <div class="thumb">{#if e.image}<img src={e.image} alt="" />{:else}<div class="noimg caps">NO IMG</div>{/if}
            <span class="kb caps">{KIND_ICON[e.kind]}</span>
            <button class="tb caps thr-{e.threat}" onclick={() => { setThreat(e.id, THREAT_NEXT[e.threat]); sfx('click', { volume: 0.2 }) }} title="re-classify">{e.threat}</button>
          </div>
          <div class="meta caps"><span class="nm">{e.name}</span><span class="sub">{e.cam ?? '—'} · {hhmm(e.ts)}</span></div>
          <div class="acts">
            <button class="go caps" disabled={scanning === e.id} onclick={() => locate(e)}>{scanning === e.id ? '◎ SCANNING…' : '◎ LOCATE'}</button>
            <button class="rm caps" onclick={() => { removeEntity(e.id); sfx('click') }}>✕</button>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</section>

{#if scanEntity}
  <div class="scanov">
    <div class="scancard">
      {#if scanEntity.image}<img src={scanEntity.image} alt="" />{/if}
      <div class="scanring"></div>
    </div>
    <div class="scantxt caps">VISUAL SCAN · MATCHING <b>{scanEntity.name}</b> ACROSS ALL FEEDS<span class="sd"></span></div>
    <div class="scansweep"></div>
  </div>
{/if}

<style>
  /* visual-scan overlay */
  .scanov { position: fixed; inset: 0; z-index: var(--z-cmd); background: rgba(2,3,4,0.9); overflow: hidden;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 22px; animation: sfin 200ms; }
  @keyframes sfin { from { opacity: 0; } }
  .scancard { position: relative; width: 180px; aspect-ratio: 3/4; border: 1px solid var(--cyan); box-shadow: 0 0 30px rgba(56,208,227,0.3); overflow: hidden; }
  .scancard img { width: 100%; height: 100%; object-fit: cover; filter: saturate(0.7) contrast(1.05); }
  .scanring { position: absolute; inset: 0; border: 2px solid transparent; border-top-color: var(--scarlet);
    animation: scanr 900ms linear infinite; }
  @keyframes scanr { to { transform: rotate(360deg); } }
  .scantxt { font-size: var(--fs-label); letter-spacing: var(--tracking-wide); color: var(--cyan); text-shadow: 0 0 10px rgba(56,208,227,0.5); }
  .scantxt b { color: var(--ink); }
  .sd::after { content: '…'; animation: sblink 1.1s steps(3) infinite; }
  @keyframes sblink { 50% { opacity: 0.3; } }
  .scansweep { position: absolute; left: 0; right: 0; height: 2px; background: linear-gradient(90deg, transparent, var(--cyan), transparent);
    box-shadow: 0 0 16px var(--cyan); animation: ssweep 1.4s linear infinite; }
  @keyframes ssweep { from { top: 0; } to { top: 100%; } }

  .wl { position: absolute; inset: 0; z-index: var(--z-boot); background: #050607; color: var(--ink); overflow: auto; padding: 22px 30px; }
  .hdr { font-size: var(--fs-title); letter-spacing: var(--tracking-wide); margin-bottom: 16px; }
  .hdr .hot { color: var(--scarlet); } .hdr .hint { float: right; font-size: var(--fs-micro); color: var(--ink-ghost); }
  .filters { display: flex; flex-wrap: wrap; gap: 18px; margin-bottom: 16px; }
  .grp { display: flex; align-items: center; gap: 5px; }
  .gl { font-size: var(--fs-micro); color: var(--ink-ghost); margin-right: 4px; }
  .chip { padding: 3px 9px; border: 1px solid var(--hairline); font-size: var(--fs-micro); letter-spacing: 0.08em; color: var(--ink-dim); cursor: crosshair; }
  .chip.on { background: var(--ink); color: var(--scarlet-ink); border-color: var(--ink); }
  .chip.thr-threat.on { background: var(--scarlet); border-color: var(--scarlet); color: #fff; }
  .chip.thr-watch.on { background: #d8a200; border-color: #d8a200; color: #000; }
  .chip.thr-safe.on { background: var(--cyan); border-color: var(--cyan); color: #04070a; }
  .empty { margin-top: 12vh; text-align: center; color: var(--ink-dim); font-size: var(--fs-title); letter-spacing: var(--tracking); }
  .empty span { font-size: var(--fs-micro); color: var(--ink-ghost); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 14px; }
  .card { border: 1px solid var(--hairline); background: #07090b; }
  .card.thr-threat { border-color: var(--scarlet); }
  .thumb { position: relative; aspect-ratio: 3/4; overflow: hidden; background: #05070a; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; filter: saturate(0.6) contrast(1.05); }
  .noimg { position: absolute; inset: 0; display: grid; place-content: center; color: var(--ink-ghost); font-size: var(--fs-micro); }
  .kb { position: absolute; top: 5px; left: 5px; font-size: 13px; text-shadow: 0 0 4px #000; }
  .tb { position: absolute; top: 5px; right: 5px; padding: 2px 6px; font-size: 8px; letter-spacing: 0.08em; border: 1px solid var(--ink); background: rgba(0,0,0,0.6); color: var(--ink); cursor: pointer; }
  .tb.thr-threat { border-color: var(--scarlet); color: var(--scarlet); }
  .tb.thr-watch { border-color: #d8a200; color: #d8a200; }
  .tb.thr-safe { border-color: var(--cyan); color: var(--cyan); }
  .meta { display: flex; flex-direction: column; gap: 1px; padding: 6px 8px; }
  .meta .nm { font-size: var(--fs-label); color: var(--ink); letter-spacing: var(--tracking); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .meta .sub { font-size: var(--fs-micro); color: var(--ink-ghost); }
  .acts { display: flex; gap: 4px; padding: 0 8px 8px; }
  .go { flex: 1; padding: 4px; border: 1px solid var(--ink-dim); color: var(--ink-dim); font-size: 8px; letter-spacing: var(--tracking); }
  .go:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .rm { padding: 4px 8px; border: 1px solid var(--hairline); color: var(--ink-ghost); font-size: 8px; }
  .rm:hover { border-color: var(--scarlet); color: var(--scarlet); }
</style>
