<script lang="ts">
  import { untrack, onMount, onDestroy } from 'svelte'
  import { detections, selectedDetection, dossierOpen, flashBanner, forensicSeed, mode, cameras, activeCam, enrollOpen } from '../../lib/stores'
  import { predictedDetections } from '../../lib/motion'
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
  let hovering = $state(false)     // pointer over the tracking card → keep it open
  let dismissing = $state(false)   // playing the elegant fade-out before removal
  const REACQUIRE_MS = 3000        // only try to re-id within this window after a loss
  const LOST_DISMISS_MS = 2600     // lost this long AND not hovered → the card fades away
  const center = (b: [number, number, number, number]): [number, number] => [b[0] + b[2] / 2, b[1] + b[3] / 2]

  // Reaper: once a target has been genuinely lost for a moment and the operator is not
  // hovering the card, retire it with a graceful fade instead of leaving a stale card that
  // could otherwise drift onto someone else.
  let reaper: ReturnType<typeof setInterval> | undefined
  onMount(() => {
    reaper = setInterval(() => {
      const t = target
      if (!t || !t.lost || hovering || dismissing) return
      if (Date.now() - (t.lostSince || Date.now()) > LOST_DISMISS_MS) {
        dismissing = true
        setTimeout(() => {
          if (target?.lost && !hovering) { target = null; selectedDetection.set(null); dossierOpen.set(false) }
          dismissing = false
        }, 420)
      }
    }, 300)
  })
  onDestroy(() => { if (reaper) clearInterval(reaper) })

  // Re-identify a briefly-lost target — CONSERVATIVELY, so the card never latches onto a
  // different person. A candidate must be the same class, reappear near the loss point, and
  // MATCH on appearance (colour). Proximity alone is never enough (that is how the old code
  // grabbed a passing stranger), and a wrong colour disqualifies outright.
  function reacquire(t: Tgt, dets: Detection[]): Detection | null {
    const [cx, cy] = center(t.bbox)
    const ta = t.d.attrs ?? {}
    if (!ta.upper_color) return null // no appearance cue on the target → don't guess a re-id
    let best: Detection | null = null
    let bestScore = 0.7 // strict acceptance threshold
    for (const d of dets) {
      if (d.id === t.id || d.cls !== t.d.cls) continue
      const da = d.attrs ?? {}
      if (!da.upper_color) continue // candidate must also carry an appearance cue
      const [dx, dy] = center(d.bbox)
      const dist = Math.hypot(dx - cx, dy - cy)
      if (dist > 0.28) continue // must reappear near where it was lost
      let score = ta.upper_color === da.upper_color ? 0.55 : -1.0 // wrong colour disqualifies
      if (ta.lower_color && da.lower_color) score += ta.lower_color === da.lower_color ? 0.2 : -0.3
      if (ta.height && da.height && ta.height === da.height) score += 0.15
      score += Math.max(0, 0.35 * (1 - dist / 0.28)) // proximity bonus, near the loss point only
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
      if (live) { dismissing = false; target = { ...cur, id: live.id, bbox: live.bbox, d: live, lost: false, lostSince: 0 }; return }
      const lostSince = cur.lostSince || Date.now()
      // only attempt a re-id briefly after the loss; after that let the card retire
      const cand = (Date.now() - lostSince < REACQUIRE_MS) ? reacquire(cur, dets) : null
      if (cand) {
        if (cur.lost) { sfx('ping', { volume: 0.35 }); flashBanner('TARGET RE-ACQUIRED', false, 900) }
        dismissing = false
        target = { ...cur, id: cand.id, bbox: cand.bbox, d: cand, lost: false, lostSince: 0 }
        return
      }
      target = { ...cur, lost: true, lostSince } // hold last position (until the reaper retires it)
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
  function deselect() { sfx('click'); target = null; dismissing = false; hovering = false; selectedDetection.set(null); dossierOpen.set(false) }
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
  {#each $predictedDetections as d (d.id)}
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
        <span class="tag caps" class:hot={isAlarm(d)}>{a.alias || d.klass}{#if d.speed !== undefined} · {d.speed === 0 ? 'STOPPED' : `${d.speed} KM/H`}{/if}</span>
      {/if}
    </div>
  {/each}

  {#if target}
    {@const t = target}
    {@const tb = $predictedDetections.find((x) => x.id === t.id)?.bbox ?? t.bbox}
    {@const a = $annotations[t.key] ?? {}}
    {@const alarm = isAlarm(t.d) || a.threat === 'high'}
    {@const asx = assess(t.d, a.threat)}
    {@const nxt = predictNext(tb[0] + tb[2] / 2)}
    <div class="tgt" class:lost={t.lost} class:dismissing
      style={`left:${pc(tb[0])};top:${pc(tb[1])};width:${pc(tb[2])};height:${pc(tb[3])}`}>
      <span class="lock"></span>
      <div class="track panel" class:alarm class:flip={tb[0] + tb[2] / 2 > 0.62}
        role="group" onmouseenter={() => (hovering = true)} onmouseleave={() => (hovering = false)}>
        <div class="ttab caps"><span>/// {a.alias || t.d.klass}{#if t.lost} · LOST{/if}</span><button class="tx" onpointerdown={(e) => { e.stopPropagation(); deselect() }} aria-label="close">×</button></div>
        <div class="trow caps"><span class="tk">{t.key}</span>
          {#if t.d.klass === 'TARGET'}{@const v = matchVerdict(t.d.conf)}<span class="cf vc vc-{v.tone}">{v.label} {v.bars}</span>
          {:else}<span class="cf">{Math.round(t.d.conf * 100)}%</span>{/if}</div>
        <div class="trow caps"><span class="kk">CLASS</span><span class="vv">{trUpper(t.d.cls)}</span></div>
        {#if t.d.make}<div class="trow caps"><span class="kk">MAKE</span><span class="vv">{trUpper(t.d.make)}<span class="est"> ~est</span></span></div>{/if}
        {#if t.d.subtype}<div class="trow caps"><span class="kk">TYPE</span><span class="vv">{trUpper(t.d.subtype)}</span></div>{/if}
        {#if t.d.speed !== undefined}<div class="trow caps"><span class="kk">SPEED</span><span class="vv">{t.d.speed === 0 ? 'STOPPED' : `${t.d.speed} KM/H`}{#if t.d.speed !== 0}<span class="est"> ~est</span>{/if}</span></div>{/if}
        {#if t.d.plate}<div class="trow caps"><span class="kk">PLATE</span><span class="vv plate">{t.d.plate}<span class="est"> ~est</span></span></div>{/if}
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
  /* No CSS position transition: boxes are dead-reckoned every animation frame (lib/motion),
     so they track the object in real time instead of tweening in from where it just was.
     A one-shot fade-in (fires only when a new track first appears) softens the pop-in. */
  .det { position: absolute; pointer-events: auto; cursor: crosshair; background: none;
    animation: detIn 160ms ease; }
  @keyframes detIn { from { opacity: 0; } }

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

  /* persistent tracked-target overlay — dead-reckoned every frame (real-time), holds on gaps */
  .tgt { position: absolute; z-index: 6; pointer-events: none; }
  .tgt.lost { opacity: 0.7; }
  /* elegant fade-out when a genuinely lost target is retired (and not being hovered) */
  .tgt.dismissing { opacity: 0; transform: scale(1.03);
    transition: opacity 400ms ease, transform 400ms ease; }
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
  .plate { font-weight: 700; letter-spacing: 0.14em; }
  .est { color: var(--ink-dim); font-weight: 400; letter-spacing: 0.06em; }
  .tnote { font-size: 8px; color: var(--ink-dim); font-style: italic; }
  .asep { height: 1px; background: var(--hairline); margin: 3px 0; }
  .tbtns { display: flex; gap: 6px; margin-top: 4px; }
  .tfile { padding: 3px 6px; border: 1px solid var(--ink-dim); font-size: 8px; letter-spacing: var(--tracking); color: var(--ink-dim); background: none; cursor: pointer; }
  .tfile:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .tfile.rej:hover { border-color: var(--amber, #d8a200); color: var(--amber, #d8a200); }
</style>
