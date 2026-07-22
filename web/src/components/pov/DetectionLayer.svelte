<script lang="ts">
  import { untrack } from 'svelte'
  import { detections, selectedDetection, dossierOpen, flashBanner, forensicSeed, mode, cameras, activeCam, enrollOpen } from '../../lib/stores'
  import { sfx } from '../../lib/audio'
  import { trUpper } from '../../lib/lexicon'
  import { annotations } from '../../lib/annotations'
  import { matchVerdict } from '../../lib/match'
  import { recordMatchFeedback } from '../../lib/feedback'
  import type { Detection } from '../../lib/types'

  type Marker = 'ring' | 'cross' | 'tri' | 'tri-solid' | 'double'
  function markerOf(d: Detection): Marker {
    if (d.klass === 'TARGET') return 'double'
    if (d.severity === 'critical') return 'tri-solid'
    if (d.klass === 'ANOMALY') return 'cross'
    if (d.klass === 'WARNING') return 'tri'
    return 'ring'
  }
  const isAlarm = (d: Detection) => d.severity !== 'info'
  const pc = (n: number) => `${(n * 100).toFixed(2)}%`
  const attrLine = (d: Detection) => [d.attrs?.upper_color, d.attrs?.height].filter(Boolean).map((s) => trUpper(String(s))).join(' · ')
  // Overseer-style behaviour assessment (anonymized — no identity, OSINT-legal)
  function assess(d: Detection, threat?: string) {
    if (d.severity === 'critical' || threat === 'high') return { p: 'THREAT', c: 'TRACK', alarm: true }
    if (d.severity === 'warning' || threat === 'medium' || d.klass === 'ANOMALY') return { p: 'ELEVATED', c: 'OBSERVE', alarm: false }
    return { p: 'NON-THREAT', c: 'DISREGARD', alarm: false }
  }
  // Predicted next camera (feature 8): heuristic from exit edge + nearest camera in that direction.
  function predictNext(cx: number): string | null {
    if (cx <= 0.66 && cx >= 0.34) return null
    const cur = $cameras.find((c) => c.id === $activeCam)
    if (!cur?.coords) return null
    const east = cx > 0.5
    const cands = $cameras.filter((c) => c.id !== $activeCam && c.coords)
      .map((c) => ({ c, d: c.coords![1] - cur.coords![1] }))
    const dirCands = cands.filter((x) => (east ? x.d > 0 : x.d < 0)).sort((a, b) => Math.abs(a.d) - Math.abs(b.d))
    const near = dirCands[0] ?? cands.sort((a, b) => Math.abs(a.d) - Math.abs(b.d))[0]
    return near ? `${near.c.name} ${east ? '→' : '←'}` : null
  }

  // Persistent tracked target with re-acquisition (re-id). Holds last position
  // across YOLO gaps; when the tracked id is lost, re-binds to a re-entering
  // detection matching by class + appearance + proximity, keeping one identity.
  type Tgt = { key: string; id: string; bbox: [number, number, number, number]; d: Detection; lost: boolean; lostSince: number }
  let target = $state<Tgt | null>(null)
  const center = (b: [number, number, number, number]): [number, number] => [b[0] + b[2] / 2, b[1] + b[3] / 2]

  // Score candidates for re-identifying a lost target; return the best over threshold.
  // Works for people (appearance colour cues) AND vehicles/animals (class + proximity
  // when no colour attrs are available), so any lost target can be re-acquired.
  function reacquire(t: Tgt, dets: Detection[]): Detection | null {
    const [cx, cy] = center(t.bbox)
    const ta = t.d.attrs ?? {}
    let best: Detection | null = null
    let bestScore = 0.55 // minimum confidence to accept a match (avoids false re-ids)
    for (const d of dets) {
      if (d.id === t.id || d.cls !== t.d.cls) continue // same object-class is required
      const da = d.attrs ?? {}
      const [dx, dy] = center(d.bbox)
      const dist = Math.hypot(dx - cx, dy - cy)
      const haveColor = !!(ta.upper_color && da.upper_color)
      if (!haveColor && dist > 0.2) continue // no appearance cue → only re-grab near the loss point
      let score = 0
      if (haveColor) score += ta.upper_color === da.upper_color ? 0.6 : -0.5
      if (ta.height && da.height && ta.height === da.height) score += 0.2
      score += Math.max(0, 0.6 * (1 - dist / 0.45)) // proximity: full when near, fades by ~45% away
      if (score > bestScore) { bestScore = score; best = d }
    }
    return best
  }

  $effect(() => {
    const sel = $selectedDetection
    const dets = $detections
    untrack(() => {
      if (!sel) { target = null; return }
      const cur = target
      if (!cur || cur.key !== sel.id) {
        // fresh selection → seed a new identity from it
        const live0 = dets.find((x) => x.id === sel.id)
        target = { key: sel.id, id: sel.id, bbox: (live0 ?? sel).bbox, d: live0 ?? sel, lost: !live0, lostSince: live0 ? 0 : Date.now() }
        return
      }
      const live = dets.find((x) => x.id === cur.id)
      if (live) { target = { ...cur, id: live.id, bbox: live.bbox, d: live, lost: false, lostSince: 0 }; return }
      const cand = reacquire(cur, dets) // lost → try to re-identify a re-entering object
      if (cand) {
        if (cur.lost) { sfx('ping', { volume: 0.35 }); flashBanner('TARGET RE-ACQUIRED', false, 900) }
        target = { ...cur, id: cand.id, bbox: cand.bbox, d: cand, lost: false, lostSince: 0 }
        return
      }
      target = { ...cur, lost: true, lostSince: cur.lostSince || Date.now() } // hold last position
    })
  })

  function pick(d: Detection) { sfx('ping', { volume: 0.3 }); selectedDetection.set(d) }
  function openFile() { sfx('click'); dossierOpen.set(true) }
  function findSimilar(d: Detection) {
    sfx('sonar')
    forensicSeed.set([d.attrs?.upper_color, d.attrs?.height, d.cls].filter(Boolean).join(' '))
    target = null; selectedDetection.set(null); dossierOpen.set(false); mode.set('forensic')
  }
  // Close the tracking panel; clears both local state and the selection so it stays closed.
  function deselect() { sfx('click'); target = null; selectedDetection.set(null); dossierOpen.set(false) }
  // Operator says this located target is not the right match (catalog 12/13). The rejection
  // is remembered per class and quietly tightens future searches for that class.
  function rejectMatch(d: Detection) {
    sfx('error'); recordMatchFeedback(d.cls, 'false')
    flashBanner('MATCH REJECTED · SEARCH TIGHTENED', false, 1500)
    deselect()
  }
  function addCase(e: MouseEvent, d: Detection) { e.preventDefault(); sfx('sonar'); selectedDetection.set(d); flashBanner('ADDED TO CASE', false, 1200) }
</script>

<div class="layer">
  {#each $detections as d (d.id)}
    {@const m = markerOf(d)}
    {@const a = $annotations[d.id] ?? {}}
    <div
      class="det" class:alarm={isAlarm(d) || a.threat === 'high'} class:sel={$selectedDetection?.id === d.id}
      role="button" tabindex="-1"
      style={`left:${pc(d.bbox[0])};top:${pc(d.bbox[1])};width:${pc(d.bbox[2])};height:${pc(d.bbox[3])}`}
      onclick={() => pick(d)} onkeydown={(e) => { if (e.key === 'Enter') pick(d) }} oncontextmenu={(e) => addCase(e, d)}>
      <span class="cnr a"></span><span class="cnr b"></span><span class="cnr c"></span><span class="cnr d"></span>
      <span class="marker m-{m}">
        {#if m === 'ring'}
          <svg viewBox="0 0 72 72"><circle class="dash" cx="36" cy="36" r="26"/><circle class="tick" cx="36" cy="36" r="19"/></svg>
        {:else if m === 'double'}
          <svg viewBox="0 0 72 72"><circle class="dash hot" cx="36" cy="36" r="28"/><circle class="dash hot" cx="36" cy="36" r="20"/></svg>
        {:else if m === 'cross'}
          <svg viewBox="0 0 72 72"><circle class="ring hot" cx="36" cy="36" r="24"/>
            <line class="hot" x1="36" y1="8" x2="36" y2="26"/><line class="hot" x1="36" y1="46" x2="36" y2="64"/>
            <line class="hot" x1="8" y1="36" x2="26" y2="36"/><line class="hot" x1="46" y1="36" x2="64" y2="36"/></svg>
        {:else if m === 'tri'}
          <svg viewBox="0 0 72 72"><polygon class="hollow" points="36,20 52,46 20,46"/></svg>
        {:else}
          <svg viewBox="0 0 72 72"><polygon class="solid" points="36,18 54,48 18,48"/></svg>
        {/if}
      </span>
      {#if $selectedDetection?.id !== d.id}
        <span class="tag caps" class:hot={isAlarm(d)}>{a.alias || d.klass}</span>
      {/if}
    </div>
  {/each}

  {#if target}
    {@const t = target}
    {@const a = $annotations[t.key] ?? {}}
    {@const alarm = isAlarm(t.d) || a.threat === 'high'}
    {@const asx = assess(t.d, a.threat)}
    {@const nxt = predictNext(t.bbox[0] + t.bbox[2] / 2)}
    <div class="tgt" class:lost={t.lost}
      style={`left:${pc(t.bbox[0])};top:${pc(t.bbox[1])};width:${pc(t.bbox[2])};height:${pc(t.bbox[3])}`}>
      <span class="lock"></span>
      <div class="track panel" class:alarm class:flip={t.bbox[0] + t.bbox[2] / 2 > 0.62}>
        <div class="ttab caps"><span>/// {a.alias || t.d.klass}{#if t.lost} · LOST{/if}</span><button class="tx" onpointerdown={(e) => { e.stopPropagation(); deselect() }} aria-label="close">×</button></div>
        <div class="trow caps"><span class="tk">{t.key}</span>
          {#if t.d.klass === 'TARGET'}{@const v = matchVerdict(t.d.conf)}<span class="cf vc vc-{v.tone}">{v.label} {v.bars}</span>
          {:else}<span class="cf">{Math.round(t.d.conf * 100)}%</span>{/if}</div>
        <div class="trow caps"><span class="kk">CLASS</span><span class="vv">{trUpper(t.d.cls)}</span></div>
        {#if attrLine(t.d)}<div class="trow caps"><span class="kk">ATTR</span><span class="vv">{attrLine(t.d)}</span></div>{/if}
        {#if a.owner}<div class="trow caps"><span class="kk">OWNER</span><span class="vv">{trUpper(a.owner)}</span></div>{/if}
        {#if a.notes}<div class="tnote caps">“{a.notes}”</div>{/if}
        <div class="asep"></div>
        <div class="trow caps"><span class="kk">PROJECTION</span><span class="chip" class:chip--alarm={asx.alarm} class:chip--invert={!asx.alarm}>{asx.p}</span></div>
        <div class="trow caps"><span class="kk">CONCLUSION</span><span class="chip" class:chip--alarm={asx.alarm} class:chip--invert={!asx.alarm}>{asx.c}</span></div>
        {#if nxt}<div class="trow caps"><span class="kk">NEXT</span><span class="vv" style="color:var(--cyan)">{nxt}</span></div>{/if}
        <div class="tbtns">
          <button class="tfile caps" onclick={() => enrollOpen.set(t.d)}>⊕ ENROLL</button>
          <button class="tfile caps" onclick={openFile}>FILE ▸</button>
          <button class="tfile caps" onclick={() => findSimilar(t.d)}>≈ SIMILAR</button>
          {#if t.d.klass === 'TARGET'}<button class="tfile rej caps" onclick={() => rejectMatch(t.d)} title="Not the right match — tighten future searches">✕ NOT THIS</button>{/if}
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .layer { position: absolute; inset: 0; z-index: var(--z-overlay); pointer-events: none; }
  .det { position: absolute; pointer-events: auto; cursor: crosshair; background: none;
    transition: left 140ms linear, top 140ms linear, width 140ms linear, height 140ms linear; }

  .cnr { position: absolute; width: 9px; height: 9px; border: 1.5px solid var(--ink); opacity: 0.85; }
  .a { top: -1px; left: -1px; border-right: 0; border-bottom: 0; }
  .b { top: -1px; right: -1px; border-left: 0; border-bottom: 0; }
  .c { bottom: -1px; left: -1px; border-right: 0; border-top: 0; }
  .d { bottom: -1px; right: -1px; border-left: 0; border-top: 0; }
  .det.alarm .cnr { border-color: var(--scarlet); }
  .det.sel .cnr { opacity: 0; }

  .marker { position: absolute; left: 50%; top: 50%; width: 60px; height: 60px; transform: translate(-50%, -50%); }
  .marker svg { width: 100%; height: 100%; overflow: visible; }
  .marker circle, .marker line, .marker polygon { fill: none; stroke: var(--ink); stroke-width: 1.4; vector-effect: non-scaling-stroke; }
  .marker .hot { stroke: var(--scarlet); }
  .marker .dash { stroke-dasharray: 6 5; animation: spin 9s linear infinite; transform-origin: 50% 50%; }
  .marker .tick { stroke-dasharray: 2 8; opacity: 0.7; }
  .marker .hollow { stroke: var(--scarlet); stroke-width: 2; }
  .marker .solid { fill: var(--scarlet); stroke: var(--scarlet); filter: drop-shadow(0 0 5px var(--scarlet-glow)); }
  .m-tri-solid, .m-tri { top: 0; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .tag { position: absolute; top: -16px; left: 0; font-size: var(--fs-micro); letter-spacing: 0.12em; color: var(--ink); text-shadow: 0 0 4px #000; }
  .tag.hot { color: var(--scarlet); }

  /* persistent tracked-target overlay — follows the object smoothly, holds on gaps */
  .tgt { position: absolute; z-index: 6; pointer-events: none;
    transition: left 160ms linear, top 160ms linear, width 160ms linear, height 160ms linear; }
  .tgt.lost { opacity: 0.7; }
  .lock { position: absolute; inset: -6px; border: 1.5px solid #fff; box-shadow: 0 0 10px rgba(255,255,255,0.4); animation: lockpulse 1.6s ease-in-out infinite; }
  @keyframes lockpulse { 50% { box-shadow: 0 0 16px rgba(255,255,255,0.7); } }

  .track { position: absolute; left: calc(100% + 14px); top: -6px; min-width: 168px; padding: 6px 8px; pointer-events: auto;
    display: flex; flex-direction: column; gap: 3px; box-shadow: 0 0 18px rgba(0,0,0,0.7); animation: appear 160ms var(--ease); }
  .track::before { content: ''; position: absolute; left: -14px; top: 10px; width: 14px; height: 1px; background: #fff; }
  /* flip to the left when the target sits in the right of the frame (keeps close on-screen) */
  .track.flip { left: auto; right: calc(100% + 14px); }
  .track.flip::before { left: auto; right: -14px; }
  .track.alarm { border-color: var(--scarlet); }
  @keyframes appear { from { opacity: 0; transform: translateX(-6px); } }
  .ttab { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink); border-bottom: 1px solid var(--hairline); padding-bottom: 3px; }
  .track.alarm .ttab { color: var(--scarlet); }
  .tx { font-size: 15px; line-height: 1; color: var(--ink-dim); padding: 2px 6px; margin: -2px -4px -2px 4px; cursor: pointer; }
  .tx:hover { color: var(--scarlet); background: rgba(255,255,255,0.06); }
  .trow { display: flex; justify-content: space-between; gap: 10px; font-size: 9px; }
  .trow .tk { color: var(--ink); } .trow .cf { color: var(--ink-dim); }
  .vc { font-weight: 700; letter-spacing: 0.06em; }
  .vc-lock { color: var(--ink); } .vc-firm { color: var(--ink-dim); } .vc-review { color: var(--amber, #d8a200); }
  .trow .kk { color: var(--ink-dim); } .trow .vv { color: var(--ink); }
  .tnote { font-size: 8px; color: var(--ink-dim); font-style: italic; }
  .asep { height: 1px; background: var(--hairline); margin: 3px 0; }
  .tbtns { display: flex; gap: 6px; margin-top: 4px; }
  .tfile { padding: 3px 6px; border: 1px solid var(--ink-dim); font-size: 8px; letter-spacing: var(--tracking); color: var(--ink-dim); background: none; cursor: pointer; }
  .tfile:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .tfile.rej:hover { border-color: var(--amber, #d8a200); color: var(--amber, #d8a200); }
</style>
