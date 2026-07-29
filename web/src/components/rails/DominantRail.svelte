<script lang="ts">
  // Focused incident panel (top-right). The latest unacknowledged alert drops in
  // as a single detailed card — where, when, threat no, details — with a jump to
  // the camera it happened on. De-dup lives in pushAlert, so it never re-drops.
  import { alerts, cameras, activeCam, mode, stage, flashBanner, investigateCase } from '../../lib/stores'
  import { sendCommand } from '../../lib/ws'
  import { SIM } from '../../lib/sim'
  import { sfx } from '../../lib/audio'
  import { LEX } from '../../lib/lexicon'
  import LiveThumb from '../LiveThumb.svelte'
  import ReplayOverlay from '../ReplayOverlay.svelte'
  import { api } from '../../lib/api'
  import { aiStatus, aiOn } from '../../lib/ai'
  import { recordFeedback } from '../../lib/feedback'
  import type { Alert } from '../../lib/types'

  const camIdFor = (a: Alert) => ($cameras.find((c) => c.name === a.cam) ?? $cameras.find((c) => c.id === a.cam))?.id
  let analyzing = $state(false)
  let advising = $state(false)
  async function analyze(a: Alert) {
    if (analyzing) return
    analyzing = true; sfx('sonar')
    try {
      const { explanation, disabled } = await api.aiExplain({ type: a.type, summary: a.summary, cam: a.cam })
      if (explanation) alerts.update((l) => l.map((x) => (x.threat === a.threat ? { ...x, reason: explanation } : x)))
      else flashBanner(disabled ? 'EXPLAIN DISABLED' : 'ASSISTANT OFFLINE', true, 1200)
    } catch { flashBanner('ANALYZE FAILED', true, 1200) }
    analyzing = false
  }
  // idea 7: recommended operator action for this alert.
  async function advise(a: Alert) {
    if (advising) return
    advising = true; sfx('sonar')
    try {
      const { action, disabled } = await api.aiAdvise({ type: a.type, summary: a.summary, cam: a.cam })
      if (action) alerts.update((l) => l.map((x) => (x.threat === a.threat ? { ...x, action } : x)))
      else flashBanner(disabled ? 'ADVISE DISABLED' : 'ASSISTANT OFFLINE', true, 1200)
    } catch { flashBanner('ADVISE FAILED', true, 1200) }
    advising = false
  }
  function markFalse(a: Alert) { sfx('click'); recordFeedback(a.type, a.cam, 'false'); flashBanner('MARKED FALSE · LEARNING', false, 1300); dismiss(a) }

  const SNAP = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
  const hhmmss = (ts: number) => {
    const d = new Date(ts)
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')
  }

  // Priority queue (catalog 64): the featured incident is the highest-severity
  // un-acked alert, most-recent breaking ties — a critical never waits behind a newer
  // info alert. The pending counts give the operator the depth of the queue at a glance.
  const SEV_RANK: Record<string, number> = { critical: 3, warning: 2, info: 1 }
  let pending = $derived($alerts.filter((a) => !a.ack))
  let active = $derived(
    [...pending].sort((a, b) => (SEV_RANK[b.severity] ?? 0) - (SEV_RANK[a.severity] ?? 0) || b.ts - a.ts)[0] ?? null,
  ) // the incident to feature
  let queue = $derived({
    critical: pending.filter((a) => a.severity === 'critical').length,
    warning: pending.filter((a) => a.severity === 'warning').length,
    info: pending.filter((a) => a.severity === 'info').length,
  })
  let history = $derived($alerts.filter((a) => a.ack).slice(0, 8))
  let showHist = $state(false) // recent list collapsed by default
  let replay = $state<string | null>(null) // incident clip being played
  let replayThreat = $state<string | null>(null) // threat id behind the clip
  // Live alert behind the clip, so the overlay caption updates when ANALYZE lands.
  let replayAlert = $derived($alerts.find((a) => a.threat === replayThreat) ?? null)
  let vLoading = $state(true)
  let vError = $state(false)

  // Open the annotated replay: play the clip and, if the assistant is on, fetch the
  // 'why this fired' explanation so it captions the video. Fully graceful when AI is off.
  function openReplay(a: Alert) {
    sfx('click'); replay = `${SNAP}${a.clip}`; replayThreat = a.threat ?? null; vLoading = true; vError = false
    if (!a.reason && aiOn($aiStatus, 'explain')) analyze(a)
  }

  function dismiss(a: Alert) { sfx('click'); alerts.update((l) => l.map((x) => (x.threat === a.threat ? { ...x, ack: true } : x))) }
  // open an investigation case for this alert instead of jumping straight to the live feed
  let opening = $state(false)
  async function investigate(a: Alert) {
    if (opening) return
    opening = true; sfx('sonar')
    try {
      const { id } = await api.caseFromAlert({ type: a.type, cam: a.cam, severity: a.severity, ts: a.ts, summary: a.summary, snapshot: a.snapshot, clip: a.clip })
      mode.set('case'); stage.set('live'); investigateCase.set(id)
      dismiss(a)
    } catch { flashBanner('COULD NOT OPEN CASE', true, 1400) }
    opening = false
  }
  function navigate(a: Alert) {
    const cam = $cameras.find((c) => c.name === a.cam) ?? $cameras.find((c) => c.id === a.cam)
    if (!cam) { flashBanner('CAMERA NOT AVAILABLE', true, 1400); dismiss(a); return }
    sfx('glitch')
    if (cam.id !== $activeCam) {
      activeCam.set(cam.id)
      if (!SIM) sendCommand(`connect:${cam.name}`)
      flashBanner(`VECTORING → ${cam.name}`, false, 1200)
    } else {
      flashBanner(`ON SCENE · ${cam.name}`, false, 1200)
    }
    mode.set('pov'); stage.set('live')
    dismiss(a)
  }
</script>

<div class="dom">
  {#if active}
    {#key active.threat}
      <div class="incident sev-{active.severity}">
        <div class="ihdr caps"><span class="hot">◤ ALERT{#if (active.hits ?? 1) > 1}<span class="rep"> ×{active.hits}</span>{/if}</span><span class="tno">{active.threat}</span></div>
        {#if pending.length > 1}
          <div class="queue caps" title="PENDING ALERTS BY PRIORITY">
            <span class="qlead">QUEUE</span>
            {#if queue.critical}<span class="qc qk">{queue.critical} CRIT</span>{/if}
            {#if queue.warning}<span class="qc qw">{queue.warning} WARN</span>{/if}
            {#if queue.info}<span class="qc qi">{queue.info} INFO</span>{/if}
          </div>
        {/if}
        {#if active.snapshot}
          <img class="snap" src={`${SNAP}${active.snapshot}`} alt="" />
        {:else}
          {@const cid = camIdFor(active)}
          {#if cid}<div class="snap snapthumb"><LiveThumb id={cid} fps={3} /></div>{/if}
        {/if}
        <div class="event caps">{active.type}</div>
        <div class="row caps"><span class="k">WHERE</span><span class="v">{active.cam || '—'}</span></div>
        <div class="row caps"><span class="k">WHEN</span><span class="v">{hhmmss(active.ts)}</span></div>
        <div class="row caps"><span class="k">THREAT</span><span class="v">{active.threat}</span></div>
        <div class="detail caps">{active.summary}</div>
        {#if active.reason}<div class="aireason caps">◇ {active.reason}</div>{/if}
        {#if active.action}<div class="aiaction caps">▸ {active.action}</div>{/if}
        <div class="btns">
          <button class="go caps" onclick={() => investigate(active)} disabled={opening}>⊕ {opening ? 'OPENING…' : 'OPEN CASE'}</button>
          <button class="rp caps" onclick={() => navigate(active)}>▸ LIVE</button>
          {#if active.clip}<button class="rp caps" onclick={() => openReplay(active)}>▶ REPLAY</button>{/if}
        </div>
        <div class="btns2">
          {#if aiOn($aiStatus, 'explain')}<button class="an caps" onclick={() => analyze(active)} disabled={analyzing}>◇ {analyzing ? 'ANALYZING…' : 'ANALYZE'}</button>{/if}
          {#if aiOn($aiStatus, 'advise')}<button class="an caps" onclick={() => advise(active)} disabled={advising}>▸ {advising ? 'ADVISING…' : 'ACTION'}</button>{/if}
          <button class="fa caps" onclick={() => markFalse(active)}>✕ FALSE</button>
          <button class="dz caps" onclick={() => dismiss(active)}>DISMISS</button>
        </div>
      </div>
    {/key}
  {:else}
    <div class="panel calm caps"><span class="ok">◆</span> {LEX.dominant} · NO ACTIVE THREAT</div>
  {/if}

  {#if history.length}
    <div class="hist panel">
      <button class="hhdr caps" onclick={() => { showHist = !showHist; sfx('click', { volume: 0.2 }) }}>
        <span>{showHist ? '▾' : '▸'} RECENT · {history.length}</span>
      </button>
      {#if showHist}
        {#each history as a (a.threat)}
          <div class="hrow caps sev-{a.severity}"><span class="ht">[{hhmmss(a.ts)}]</span> {a.type} · {a.cam}</div>
        {/each}
      {/if}
    </div>
  {/if}
</div>

{#if replay}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="rscrim" onclick={() => (replay = null)} role="presentation">
    <div class="rbox" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" tabindex="-1">
      <div class="rhdr caps"><span class="hot">▶</span> ALERT REPLAY <button class="rx" onclick={() => (replay = null)} aria-label="close">×</button></div>
      <div class="rstage">
        <!-- svelte-ignore a11y_media_has_caption -->
        <video src={replay} autoplay loop controls class:ready={!vLoading}
          oncanplay={() => (vLoading = false)} onerror={() => { vLoading = false; vError = true }}></video>
        {#if !vLoading && !vError && replayAlert?.mark}
          <ReplayOverlay mark={replayAlert.mark} label={replayAlert.type}
            threat={replayAlert.threat} reason={replayAlert.reason}
            alarm={replayAlert.severity === 'critical'} />
        {/if}
        {#if vLoading || vError}
          <div class="rload">
            {#if vError}
              <div class="rerr caps">CLIP UNAVAILABLE</div>
            {:else}
              <div class="rspin"></div>
              <div class="rmsg caps">BUFFERING INCIDENT<span class="rdot"></span></div>
            {/if}
          </div>
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .dom { position: absolute; right: 20px; top: 72px; width: 300px; z-index: var(--z-panel); display: flex; flex-direction: column; gap: 8px; }

  .incident { background: rgba(9,10,12,0.96); border: 1px solid var(--scarlet); padding: 10px; display: flex; flex-direction: column; gap: 5px;
    box-shadow: 0 0 26px rgba(225,6,0,0.28); animation: drop 320ms var(--ease); }
  @keyframes drop { 0% { opacity: 0; transform: translateY(-22px) scale(0.98); } 60% { transform: translateY(3px); } 100% { opacity: 1; transform: none; } }
  .incident.sev-info { border-color: var(--ink); box-shadow: 0 0 20px rgba(0,0,0,0.5); }
  .ihdr { display: flex; justify-content: space-between; font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--scarlet); }
  .incident.sev-info .ihdr { color: var(--ink); }
  .ihdr .tno { color: var(--ink-dim); }
  .snap { width: 100%; aspect-ratio: 16/9; object-fit: cover; border: 1px solid var(--hairline); filter: saturate(0.6) contrast(1.05); }
  .snapthumb { position: relative; overflow: hidden; background: #05070a; }
  .event { font-family: var(--font-display); font-weight: 700; font-size: 15px; letter-spacing: var(--tracking); color: var(--ink); margin-top: 2px; }
  .row { display: flex; justify-content: space-between; gap: 10px; font-size: var(--fs-micro); }
  .row .k { color: var(--ink-dim); } .row .v { color: var(--ink); text-align: right; }
  .detail { font-size: var(--fs-micro); color: var(--ink-dim); line-height: 1.4; border-top: 1px solid var(--hairline); padding-top: 4px; }
  .rep { color: var(--ink); }
  .queue { display: flex; align-items: center; gap: 7px; font-size: var(--fs-micro); letter-spacing: var(--tracking); margin: -2px 0 4px; }
  .queue .qlead { color: var(--ink-ghost); }
  .qc { color: var(--ink-dim); }
  .qc.qk { color: var(--scarlet); font-weight: 700; }
  .qc.qw { color: var(--ink); }
  .aireason { font-size: var(--fs-micro); color: var(--cyan); line-height: 1.4; border-left: 2px solid var(--cyan); padding: 3px 0 3px 6px; }
  .aiaction { font-size: var(--fs-micro); color: var(--ink); line-height: 1.4; border-left: 2px solid var(--ink-dim); padding: 3px 0 3px 6px; }
  .btns { display: flex; gap: 6px; margin-top: 4px; }
  .btns2 { display: flex; gap: 6px; }
  .an { flex: 1; padding: 5px; border: 1px solid var(--cyan); color: var(--cyan); font-size: var(--fs-micro); letter-spacing: var(--tracking); background: none; }
  .an:hover:not(:disabled) { background: var(--cyan); color: #04070a; } .an:disabled { opacity: 0.5; }
  .fa { padding: 5px 8px; border: 1px solid var(--ink-dim); color: var(--ink-dim); font-size: var(--fs-micro); letter-spacing: var(--tracking); background: none; }
  .fa:hover { border-color: var(--amber, #d8a200); color: var(--amber, #d8a200); }
  .go { flex: 1; padding: 6px; border: 1px solid var(--scarlet); color: var(--scarlet); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .go:hover { background: var(--scarlet); color: #fff; }
  .rp { padding: 6px 8px; border: 1px solid var(--cyan); color: var(--cyan); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .rp:hover { background: var(--cyan); color: #04070a; }
  .dz { padding: 6px 10px; border: 1px solid var(--ink-dim); color: var(--ink-dim); font-size: var(--fs-micro); letter-spacing: var(--tracking); }
  .dz:hover { border-color: var(--ink); color: var(--ink); }

  .rscrim { position: fixed; inset: 0; z-index: var(--z-cmd); background: rgba(2,3,4,0.8); display: grid; place-items: center; }
  .rbox { width: min(720px, 92vw); background: #070809; border: 1px solid var(--ink); box-shadow: 0 0 40px rgba(0,0,0,0.7); }
  .rhdr { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--hairline); font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink); }
  .rhdr .hot { color: var(--scarlet); } .rhdr .rx { margin-left: auto; font-size: 15px; color: var(--ink-dim); cursor: pointer; } .rhdr .rx:hover { color: var(--scarlet); }
  .rstage { position: relative; background: #000; aspect-ratio: 16 / 9; }
  /* fill so the normalized incident overlay maps 1:1 onto the clip (mild stretch OK) */
  .rbox video { display: block; width: 100%; height: 100%; object-fit: fill; background: #000; opacity: 0; transition: opacity 300ms; }
  .rbox video.ready { opacity: 1; }
  .rload { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px;
    background: radial-gradient(ellipse at center, #0a0d10, #000); }
  .rspin { width: 42px; height: 42px; border: 2px solid var(--hairline); border-top-color: var(--scarlet); border-radius: 50%; animation: rspin 900ms linear infinite; }
  @keyframes rspin { to { transform: rotate(360deg); } }
  .rmsg { font-size: var(--fs-label); letter-spacing: var(--tracking-wide); color: var(--ink-dim); }
  .rdot::after { content: '…'; animation: rblink 1.1s steps(3) infinite; }
  @keyframes rblink { 50% { opacity: 0.3; } }
  .rerr { font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--scarlet); }
  .rbox video.ready + .rload { display: none; }

  .calm { padding: 8px 10px; font-size: var(--fs-micro); color: var(--ink-dim); letter-spacing: var(--tracking); }
  .calm .ok { color: var(--cyan); }

  .hist { padding: 6px 8px; display: flex; flex-direction: column; gap: 2px; max-height: 24vh; overflow-y: auto; }
  .hhdr { display: block; width: 100%; text-align: left; background: none; border: none; cursor: pointer;
    font-size: var(--fs-micro); color: var(--ink-dim); letter-spacing: var(--tracking); padding: 2px 0; }
  .hhdr:hover { color: var(--ink); }
  .hrow { font-size: var(--fs-micro); color: var(--ink-dim); line-height: 1.5; border-left: 2px solid var(--ink-ghost); padding-left: 6px; opacity: 0.75; }
  .hrow .ht { color: var(--ink-ghost); }
  .hrow.sev-critical, .hrow.sev-warning { border-left-color: var(--scarlet); }
</style>
