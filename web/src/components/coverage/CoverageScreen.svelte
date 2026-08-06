<script lang="ts">
  // FOG OF WAR — the coverage cockpit.
  //
  // A ranked ledger of everything this camera cannot see, and for each one: which of the four
  // channels is responsible, the evidence behind the claim, and what would actually fix it.
  // The export at the top is the point of the whole screen — a signed statement of coverage is
  // the artifact that answers "prove your system works", which no VMS can currently answer with
  // a number.
  import { onDestroy, onMount } from 'svelte'
  import { activeCam, blindSpots, cameras, coverage, spatialOpen, triggerGlitch } from '../../lib/stores'
  import { api } from '../../lib/api'
  import { fogHeight, fogTask, refresh, setFogHeight, setFogTask } from '../../lib/fog'
  import { sfx } from '../../lib/audio'
  import { SIM } from '../../lib/sim'
  import type { BlindKind, BlindSpot, DoriTask } from '../../lib/types'
  import LiveThumb from '../LiveThumb.svelte'
  import Explain from '../Explain.svelte'
  import ScreenIntro from '../ScreenIntro.svelte'

  let { onclose }: { onclose: () => void } = $props()

  const TASKS: DoriTask[] = ['detect', 'observe', 'recognise', 'identify']
  const HEIGHTS: [number, string][] = [[0.5, 'CROUCHING'], [1.0, 'CRAWLING'], [1.7, 'STANDING']]
  const KIND_GLYPH: Record<BlindKind, string> = {
    occlusion: '◧', resolution: '◔', radiometric: '◑', empirical: '✕', indeterminate: '?',
  }
  const KIND_LABEL: Record<BlindKind, string> = {
    occlusion: 'HIDDEN BEHIND SOMETHING', resolution: 'TOO FAR TO RESOLVE',
    radiometric: 'TOO DARK, BLOWN OR BLURRED', empirical: 'TRACKS KEEP DYING HERE',
    indeterminate: 'GLASS OR WATER · DEPTH IS UNRELIABLE',
  }
  // The four channels arrive from the backend under their engineering names. Nobody outside this
  // codebase knows what "optical" or "empirical" mean as a cause of blindness, so each is shown
  // as what it actually means and keeps its real name inside the explanation.
  const CHAN: Record<string, { label: string; term: string }> = {
    geometric: { label: 'HIDDEN BEHIND SOMETHING', term: 'occlusion' },
    optical: { label: 'TOO FAR TO MAKE ANYONE OUT', term: 'dori' },
    radiometric: { label: 'TOO DARK OR TOO BRIGHT', term: 'radiometric' },
    empirical: { label: 'PEOPLE VANISH HERE', term: 'empirical' },
  }
  const RC = 2 * Math.PI * 26

  let selId = $state<number | null>(null)
  let busy = $state(false)
  let building = $state(false)
  let exported = $state(false)
  let channelMute = $state<Record<string, boolean>>({})

  const cov = $derived($coverage)
  const cam = $derived($cameras.find((c) => c.id === $activeCam))
  const open = $derived($blindSpots.filter((s) => !s.dismissed))
  const dismissed = $derived($blindSpots.filter((s) => s.dismissed))
  const persistent = $derived(open.filter((s) => s.persistent))
  const transient = $derived(open.filter((s) => !s.persistent))
  const flat = $derived([...persistent, ...transient, ...dismissed])
  const sel = $derived(flat.find((s) => s.id === selId) ?? flat[0] ?? null)

  const pct = (n: number) => `${Math.round(n * 100)}%`
  const ago = (ts: number) => {
    if (!ts) return 'UNKNOWN'
    const d = Math.floor((Date.now() / 1000 - ts) / 86400)
    return d <= 0 ? 'TODAY' : d === 1 ? '1 DAY' : `${d} DAYS`
  }
  const area = (s: BlindSpot) => (s.area_m2 == null ? '—' : `~${s.area_m2.toFixed(0)} m2`)
  const dominant = (s: BlindSpot): BlindKind => {
    const e = Object.entries(s.channels) as [string, number][]
    e.sort((a, b) => b[1] - a[1])
    const k = e[0]?.[0] ?? 'occlusion'
    return (k === 'geometric' ? 'occlusion' : k === 'optical' ? 'resolution' : k === 'radiometric'
      ? 'radiometric' : 'empirical') as BlindKind
  }

  async function reload(force = false) {
    building = true
    await refresh(force)
    building = false
  }

  async function dismiss(s: BlindSpot | null) {
    if (!s || busy || s.id < 0) return
    busy = true
    try {
      await api.dismissBlindSpot(s.id, !s.dismissed)
      blindSpots.update((l) => l.map((x) => (x.id === s.id ? { ...x, dismissed: !x.dismissed } : x)))
      triggerGlitch(120)
    } catch { /* offline: leave the row */ }
    busy = false
  }

  async function exportReport() {
    if (!$activeCam || SIM) { exported = true; setTimeout(() => (exported = false), 2200); return }
    busy = true
    try {
      const r = await api.coverageReport($activeCam)
      const blob = new Blob([JSON.stringify(r, null, 2)], { type: 'application/json' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `coverage-${cam?.name ?? 'camera'}-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(a.href)
      exported = true
      setTimeout(() => (exported = false), 2200)
    } catch { /* offline */ }
    busy = false
  }

  function cycleTask() {
    const i = TASKS.indexOf(fogTask)
    setFogTask(TASKS[(i + 1) % TASKS.length])
    sfx('click', { volume: 0.2 })
  }
  function cycleHeight() {
    const i = HEIGHTS.findIndex(([h]) => h === fogHeight)
    setFogHeight(HEIGHTS[(i + 1) % HEIGHTS.length][0])
    sfx('click', { volume: 0.2 })
  }
  function step(dir: number) {
    if (!flat.length) return
    const i = Math.max(0, flat.findIndex((s) => s.id === selId))
    selId = flat[(i + dir + flat.length) % flat.length].id
    sfx('click', { volume: 0.15 })
  }

  // The px-per-metre demonstration: a person silhouette at the pixels actually available
  // beside the pixels the chosen task requires. Ends every argument about "is this good enough".
  const demo = $derived.by(() => {
    if (!cov || !sel) return null
    const band = cov.bands.find((b) => b.task === cov.task)
    if (!band) return null
    const cy = sel.polygon.reduce((a, p) => a + p[1], 0) / sel.polygon.length
    const nearer = cov.bands.filter((b) => b.y <= cy).sort((a, b) => b.y - a.y)[0]
    const have = nearer ? nearer.px_per_m : 12
    return { have: Math.round(have), need: Math.round(band.px_per_m), task: cov.task }
  })

  function onkey(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.stopPropagation(); onclose(); return }
    if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); step(1) }
    else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); step(-1) }
    else if (e.key === 't' || e.key === 'T') { e.preventDefault(); cycleTask() }
    else if (e.key === 'h' || e.key === 'H') { e.preventDefault(); cycleHeight() }
    else if (e.key === 'd' || e.key === 'D') { e.preventDefault(); dismiss(sel) }
    else if (/^[1-4]$/.test(e.key)) {
      const keys = ['geometric', 'optical', 'radiometric', 'empirical']
      const k = keys[Number(e.key) - 1]
      channelMute = { ...channelMute, [k]: !channelMute[k] }
    }
  }
  onMount(() => { sfx('sonar'); reload(true); window.addEventListener('keydown', onkey, true) })
  onDestroy(() => window.removeEventListener('keydown', onkey, true))
</script>

<div class="cs" role="dialog" aria-label="Coverage">
  <header class="top caps">
    <span class="eyebrow">◈ COVERAGE</span>
    <span class="cnt">◉ {cam?.name ?? 'CAM —'}</span>
    <span class="cnt">{open.length} BLIND SPOT{open.length === 1 ? '' : 'S'}</span>
    <span class="spacer"></span>
    {#if cov}
      <div class="gauge">
        <svg viewBox="0 0 60 60">
          <circle class="track" cx="30" cy="30" r="26" />
          <circle class="prog" cx="30" cy="30" r="26"
            stroke-dasharray={RC} stroke-dashoffset={RC * (1 - cov.percent / 100)} />
        </svg>
        <span class="gval">{Math.round(cov.percent)}%</span>
      </div>
      <span class="fieldlbl caps"><Explain term="coverage" plain /></span>
      <!-- The task name is rendered OUTSIDE the button so it can carry its own explanation:
           nesting one interactive element inside another steals the click and breaks keyboard
           traversal. The button next to it does the cycling. -->
      <span class="fieldlbl caps">COUNTED AS COVERED IF CLOSE ENOUGH TO</span>
      <span class="fieldval caps"><Explain term={cov.task} /></span>
      <button class="chip caps" onclick={cycleTask} title="Change what counts as covered">↻<span class="k">T</span></button>
      <span class="fieldlbl caps">FOR A</span>
      <span class="fieldval caps">{HEIGHTS.find(([h]) => h === fogHeight)?.[1] ?? 'STANDING'}</span>
      <button class="chip caps" onclick={cycleHeight} title="Change the assumed target height">↻<span class="k">H</span></button>
    {/if}
    <button class="ref caps" onclick={exportReport} disabled={busy}>{exported ? '✓ EXPORTED' : '⤓ EXPORT REPORT'}</button>
    <button class="ref caps" onclick={() => reload(true)}>↻ RESCAN</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  <ScreenIntro
    what="Every patch of ground this camera cannot usefully watch."
    hint="You get a ranked list of gaps, the reason for each, and what would fix it."
    look="The list on the left, worst first. Click one." />

  {#if building && !cov}
    <div class="empty caps"><span class="pulse">MAPPING THE UNSEEN_</span></div>
  {:else if !cov}
    <div class="empty caps">
      <div class="warnring">⛶</div>
      <div>NO SCENE GEOMETRY</div>
      <div class="sub">FOG OF WAR NEEDS THE 3D RECONSTRUCTION TO KNOW WHAT STANDS IN THE WAY.</div>
      <button class="go caps" onclick={() => { if ($activeCam) spatialOpen.set($activeCam) }}>⛶ BUILD SCENE</button>
    </div>
  {:else if !open.length}
    <div class="empty caps">
      <div class="okring">✓</div>
      <div>NOTHING HIDDEN IN THIS VIEW</div>
      <div class="sub">
        COVERAGE IS OF OBSERVED GROUND ONLY · {Math.round(cov.fov_deg)}° OF 360° IS OUTSIDE THIS CAMERA
      </div>
    </div>
  {:else}
    <div class="body">
      <aside class="queue">
        {#if persistent.length}
          <div class="qgrp caps">
            <span class="qd p"></span><Explain term="persistent" plain />
            <span class="qgn">{persistent.length}</span>
          </div>
          {#each persistent as s (s.id)}
            <button class="qrow" class:on={sel?.id === s.id} onclick={() => (selId = s.id)}>
              <span class="glyph">{KIND_GLYPH[s.kind]}</span>
              <span class="qmid">
                <span class="qttl">{s.name}</span>
                <span class="qsub caps">{area(s)} · {ago(s.first_seen)}</span>
              </span>
              {#if s.events}<span class="qn caps">{s.events} LOST</span>{/if}
            </button>
          {/each}
        {/if}
        {#if transient.length}
          <div class="qgrp caps">
            <span class="qd t"></span><Explain term="transient" plain />
            <span class="qgn">{transient.length}</span>
          </div>
          {#each transient as s (s.id)}
            <button class="qrow" class:on={sel?.id === s.id} onclick={() => (selId = s.id)}>
              <span class="glyph">{KIND_GLYPH[s.kind]}</span>
              <span class="qmid">
                <span class="qttl">{s.name}</span>
                <span class="qsub caps">{area(s)}</span>
              </span>
            </button>
          {/each}
        {/if}
        {#if dismissed.length}
          <div class="qgrp caps"><span class="qd d"></span>DISMISSED<span class="qgn">{dismissed.length}</span></div>
          {#each dismissed as s (s.id)}
            <button class="qrow off" class:on={sel?.id === s.id} onclick={() => (selId = s.id)}>
              <span class="glyph">{KIND_GLYPH[s.kind]}</span>
              <span class="qmid"><span class="qttl">{s.name}</span></span>
            </button>
          {/each}
        {/if}
      </aside>

      <main class="stage">
        {#if sel}
          {#key sel.id}
            <div class="hero">
              {#if $activeCam}<LiveThumb id={$activeCam} fps={6} />{:else}<div class="nolive caps">NO FEED</div>{/if}
              <svg class="mark" viewBox="0 0 100 100" preserveAspectRatio="none">
                <polygon class="spot" points={sel.polygon.map((p) => `${p[0] * 100},${p[1] * 100}`).join(' ')} />
              </svg>
              <span class="camtag caps">◉ {cam?.name ?? 'CAM —'}</span>
              <span class="kindtag caps k-{sel.kind}">{KIND_GLYPH[sel.kind]} {sel.kind}</span>
              {#if sel.persistent}<span class="ptag caps">PERSISTENT {ago(sel.first_seen)}</span>{/if}
            </div>

            <div class="detail">
              <div class="kind caps">{KIND_LABEL[sel.kind]}</div>
              <h2 class="stitle">{sel.name}</h2>
              <p class="swhy">
                {area(sel)} of ground that this camera cannot observe at the
                <b>{cov.task}</b> standard for a <b>{HEIGHTS.find(([h]) => h === fogHeight)?.[1].toLowerCase()}</b>
                target{sel.events ? `, where ${sel.events} track${sel.events === 1 ? ' has' : 's have'} already been lost` : ''}.
                {#if cov.scale_estimated}<span class="est">Areas and ranges are estimated from an uncalibrated depth field.</span>{/if}
              </p>

              <div class="panels">
                <section class="pnl">
                  <div class="pk caps">WHY IT IS BLIND</div>
                  <div class="pnote">Four separate reasons. The longest bar is the one to fix.</div>
                  {#each Object.entries(sel.channels) as [k, v]}
                    <button class="chan" class:muted={channelMute[k]} onclick={() => (channelMute = { ...channelMute, [k]: !channelMute[k] })}>
                      <span class="ck caps"><Explain term={CHAN[k]?.term ?? k}>{CHAN[k]?.label ?? k}</Explain></span>
                      <span class="cbar"><span class="cfill" class:lead={v >= 0.5} style={`width:${Math.round(v * 100)}%`}></span></span>
                      <span class="cv caps">{pct(v)}</span>
                    </button>
                  {/each}
                  <div class="hint caps">CLICK A REASON TO HIDE IT · OR PRESS 1-4</div>
                </section>

                <section class="pnl">
                  <div class="pk caps">HOW WE KNOW</div>
                  {#if sel.kind === 'resolution' || (demo && demo.have < demo.need)}
                    {#if demo}
                      <div class="demo">
                        <div class="dcol">
                          <div class="dbox" style={`height:${Math.max(6, Math.min(78, demo.have * 0.36))}px`}></div>
                          <div class="dlbl caps">YOU HAVE<br /><Explain term="px/m">{demo.have} PX/M</Explain></div>
                        </div>
                        <div class="dcol">
                          <div class="dbox need" style={`height:${Math.max(6, Math.min(78, demo.need * 0.36))}px`}></div>
                          <div class="dlbl caps">YOU NEED<br />{demo.need} PX/M</div>
                        </div>
                        <div class="dnote caps">A TARGET HERE IS {Math.round((demo.have / demo.need) * 100)}% OF WHAT {demo.task} REQUIRES</div>
                      </div>
                    {/if}
                  {:else if sel.kind === 'empirical'}
                    <div class="ev caps">
                      {sel.events} TRACK{sel.events === 1 ? '' : 'S'} ENDED HERE AWAY FROM THE FRAME EDGE.
                      <div class="evsub">A TRACK THAT DIES IN OPEN VIEW IS THE SYSTEM ADMITTING IT LOST SOMEONE.</div>
                    </div>
                  {:else if sel.kind === 'occlusion'}
                    <div class="ev caps">
                      GEOMETRIC SHADOW CAST BY A STANDING OBJECT.
                      <div class="evsub">
                        AT THIS MOUNT HEIGHT THE SHADOW ENDS WHERE THE SIGHTLINE CLEARS THE OBJECT'S TOP.
                        RAISING THE CAMERA SHORTENS EVERY SHADOW IN THE SCENE AT ONCE.
                      </div>
                    </div>
                  {:else}
                    <div class="ev caps">
                      PHOTOMETRIC QUALITY IS BELOW THE USABLE FLOOR IN THIS REGION.
                      <div class="evsub">LOW CONTRAST, CLIPPED HIGHLIGHTS OR PERSISTENT BLUR.</div>
                    </div>
                  {/if}
                </section>

                <section class="pnl">
                  <div class="pk caps">WHAT WOULD FIX IT</div>
                  {#each sel.remedies as r}
                    <div class="rem">
                      <span class="rdot"></span>
                      <span class="rtxt">{r.text}</span>
                      {#if r.recovers_m2}<span class="rgain caps">+{r.recovers_m2.toFixed(0)} m2</span>{/if}
                    </div>
                  {/each}
                </section>
              </div>

              <div class="actions">
                <button class="go caps" onclick={() => { if ($activeCam) spatialOpen.set($activeCam) }}>⛶ SEE IT IN 3D</button>
                <button class="alt caps" onclick={() => dismiss(sel)} disabled={busy || sel.id < 0}>
                  {sel.dismissed ? '↺ RESTORE' : '✕ DISMISS'}
                </button>
              </div>
              <div class="hint caps">↑↓ NAVIGATE · T TASK · H TARGET HEIGHT · D DISMISS · ESC CLOSE</div>
            </div>
          {/key}
        {:else}
          <div class="empty caps">SELECT A BLIND SPOT</div>
        {/if}
      </main>
    </div>
  {/if}
</div>

<style>
  .cs { position: fixed; inset: 0; z-index: var(--z-boot);
    background: radial-gradient(120% 80% at 50% 0%, #0a1016 0%, #05070a 72%);
    color: var(--ink); display: flex; flex-direction: column; overflow: hidden;
    animation: csin 300ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes csin { from { opacity: 0; } }
  .top { display: flex; align-items: center; gap: 12px; padding: 11px 22px;
    border-bottom: 1px solid var(--hairline); font-size: var(--fs-label);
    letter-spacing: var(--tracking); background: #04070a; z-index: 2; }
  .eyebrow { color: var(--scarlet); }
  .cnt { color: var(--ink-dim); font-size: 11px; }
  .spacer { flex: 1; }
  .gauge { position: relative; width: 40px; height: 40px; }
  .gauge svg { width: 40px; height: 40px; transform: rotate(-90deg); }
  .gauge .track { fill: none; stroke: var(--hairline); stroke-width: 4; }
  .gauge .prog { fill: none; stroke: var(--cyan); stroke-width: 4; stroke-linecap: round;
    transition: stroke-dashoffset 700ms cubic-bezier(0.16, 1, 0.3, 1); }
  .gval { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    font-size: 11px; color: var(--cyan); letter-spacing: 0; }
  /* The header reads as a sentence — "counted as covered if close enough to IDENTIFY for a
     STANDING target" — instead of three unlabelled chips you have to already understand. */
  .fieldlbl { font-size: 11px; color: var(--ink-ghost); letter-spacing: 0.14em; }
  .fieldval { font-size: 11px; color: var(--ink); letter-spacing: 0.14em; }
  .pnote { font-size: 11px; color: var(--ink-ghost); line-height: 1.55; letter-spacing: 0;
    margin: -2px 0 2px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 9px;
    border: 1px solid var(--hairline); color: var(--ink-dim); background: none; cursor: crosshair;
    font-size: 11px; letter-spacing: 0.12em; }
  .chip:hover { border-color: var(--cyan); color: var(--cyan); }
  .chip .k { border: 1px solid var(--ink-ghost); padding: 0 4px; font-size: 11px; color: var(--ink-ghost); }
  .ref, .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim);
    background: none; cursor: crosshair; font-size: 11px; letter-spacing: var(--tracking); }
  .ref:hover { border-color: var(--cyan); color: var(--cyan); }
  .ref:disabled { opacity: 0.45; }
  .x:hover { border-color: var(--scarlet); color: var(--scarlet); }

  .empty { flex: 1; display: flex; flex-direction: column; gap: 14px; align-items: center;
    justify-content: center; color: var(--ink-dim); letter-spacing: 0.16em; text-align: center;
    padding: 0 40px; }
  .empty .sub { font-size: 11px; color: var(--ink-ghost); max-width: 520px; line-height: 1.7; }
  .okring, .warnring { width: 54px; height: 54px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; font-size: 22px; }
  .okring { border: 1px solid color-mix(in srgb, var(--jade) 50%, transparent); color: var(--jade);
    animation: breathe 4s ease-in-out infinite; }
  @keyframes breathe { 50% { opacity: 0.55; } }
  .warnring { border: 1px solid color-mix(in srgb, var(--amber) 50%, transparent); color: var(--amber); }
  .pulse { animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.4; } }

  .body { flex: 1; min-height: 0; display: grid; grid-template-columns: 320px 1fr; }
  .queue { border-right: 1px solid var(--hairline); overflow-y: auto; padding: 12px 10px 40px;
    background: rgba(4,7,10,0.4); }
  .qgrp { display: flex; align-items: center; gap: 7px; font-size: 11px; color: var(--ink-dim);
    letter-spacing: 0.16em; margin: 14px 6px 7px; }
  .qgrp:first-child { margin-top: 4px; }
  .qd { width: 6px; height: 6px; border-radius: 50%; }
  .qd.p { background: var(--scarlet); box-shadow: 0 0 6px var(--scarlet); }
  .qd.t { background: var(--amber); box-shadow: 0 0 6px var(--amber); }
  .qd.d { background: var(--ink-ghost); }
  .qgn { margin-left: auto; color: var(--ink-ghost); }
  .qrow { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left;
    padding: 9px 10px; margin-bottom: 3px; background: none; border: 1px solid transparent;
    border-left: 2px solid transparent; cursor: crosshair; transition: background 140ms, border-color 140ms; }
  .qrow:hover { background: rgba(56,208,227,0.05); }
  .qrow.on { background: rgba(56,208,227,0.09); border-color: var(--hairline); border-left-color: var(--cyan); }
  .qrow.off { opacity: 0.4; }
  .glyph { font-size: 12px; color: var(--ink-dim); flex: 0 0 auto; width: 14px; text-align: center; }
  .qrow.on .glyph { color: var(--cyan); }
  .qmid { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
  .qttl { font-size: 11px; color: var(--ink-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .qrow.on .qttl { color: var(--ink); }
  .qsub { font-size: 11px; color: var(--ink-ghost); letter-spacing: 0.1em; }
  .qn { font-size: 11px; color: var(--scarlet); letter-spacing: 0.06em; flex: 0 0 auto; }

  .stage { min-width: 0; overflow-y: auto; display: flex; flex-direction: column; }
  .hero { position: relative; aspect-ratio: 16 / 9; max-height: 42vh; background: #05070a;
    overflow: hidden; border-bottom: 1px solid var(--hairline); animation: fade 340ms both; }
  @keyframes fade { from { opacity: 0; } }
  .nolive { display: flex; align-items: center; justify-content: center; height: 100%;
    color: var(--ink-ghost); font-size: 10px; letter-spacing: 0.14em; }
  .mark { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  .spot { fill: rgba(4,7,10,0.66); stroke: var(--cyan); stroke-width: 1.4;
    vector-effect: non-scaling-stroke; stroke-dasharray: 6 3; animation: ants 3s linear infinite, spulse 2.4s ease-in-out infinite; }
  @keyframes ants { to { stroke-dashoffset: -18; } }
  @keyframes spulse { 50% { fill-opacity: 0.75; } }
  .camtag { position: absolute; top: 10px; left: 12px; font-size: 11px; color: var(--ink);
    letter-spacing: 0.14em; text-shadow: 0 0 5px #000; }
  .kindtag { position: absolute; top: 10px; right: 12px; font-size: 11px; letter-spacing: 0.14em;
    padding: 3px 7px; background: rgba(4,7,10,0.72); }
  .k-occlusion { color: var(--cyan); } .k-resolution { color: var(--amber); }
  .k-radiometric { color: var(--amber); } .k-empirical { color: var(--scarlet); }
  .k-indeterminate { color: var(--ink-dim); }
  .ptag { position: absolute; bottom: 10px; left: 12px; font-size: 11px; color: var(--scarlet);
    background: rgba(4,7,10,0.72); padding: 3px 8px; letter-spacing: 0.1em; }

  .detail { padding: 20px 26px 24px; display: flex; flex-direction: column; gap: 11px;
    animation: rise 380ms both cubic-bezier(0.16, 1, 0.3, 1); }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
  .kind { font-size: 11px; color: var(--scarlet); letter-spacing: 0.2em; }
  .stitle { font-size: 22px; font-weight: 500; color: var(--ink); margin: 0; }
  .swhy { font-size: 12px; color: var(--ink-dim); line-height: 1.7; margin: 0; max-width: 760px; }
  .swhy b { color: var(--ink); font-weight: 500; }
  .est { display: block; margin-top: 4px; font-size: 10px; color: var(--ink-ghost); }

  .panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px;
    margin: 8px 0 4px; }
  .pnl { border: 1px solid var(--hairline); padding: 12px 13px; display: flex;
    flex-direction: column; gap: 8px; background: rgba(4,7,10,0.35); }
  .pk { font-size: 11px; color: var(--ink-ghost); letter-spacing: 0.18em; }
  /* The channel names are now full phrases rather than one-word jargon, so the label column has
     to be wide enough to hold "HIDDEN BEHIND SOMETHING" without wrapping mid-word. */
  .chan { display: grid; grid-template-columns: minmax(0, 1fr) 62px 30px; align-items: center;
    gap: 8px; background: none; border: none; padding: 3px 0; cursor: crosshair; text-align: left; }
  .chan.muted { opacity: 0.3; }
  .ck { font-size: 11px; color: var(--ink-dim); letter-spacing: 0.1em; line-height: 1.4; }
  .cbar { position: relative; height: 4px; background: var(--hairline); }
  .cfill { position: absolute; inset: 0 auto 0 0; background: var(--ink-dim); transition: width 400ms; }
  .cfill.lead { background: var(--cyan); box-shadow: 0 0 8px color-mix(in srgb, var(--cyan) 70%, transparent); }
  .cv { font-size: 11px; color: var(--ink-dim); text-align: right; }

  .demo { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-items: end; }
  .dcol { display: flex; flex-direction: column; align-items: center; gap: 6px; }
  .dbox { width: 22px; background: var(--ink-dim); }
  .dbox.need { background: var(--cyan); box-shadow: 0 0 10px color-mix(in srgb, var(--cyan) 50%, transparent); }
  .dlbl { font-size: 11px; color: var(--ink-ghost); letter-spacing: 0.1em; text-align: center; line-height: 1.5; }
  .dnote { grid-column: 1 / -1; font-size: 11px; color: var(--amber); letter-spacing: 0.1em;
    text-align: center; margin-top: 4px; }
  .ev { font-size: 11px; color: var(--ink-dim); letter-spacing: 0.1em; line-height: 1.7; }
  .evsub { color: var(--ink-ghost); margin-top: 5px; }

  .rem { display: flex; align-items: baseline; gap: 8px; font-size: 10px; color: var(--ink-dim); line-height: 1.6; }
  .rdot { width: 4px; height: 4px; background: var(--jade); flex: 0 0 auto; transform: translateY(-2px); }
  .rtxt { flex: 1; }
  .rgain { color: var(--jade); font-size: 11px; white-space: nowrap; }

  .actions { display: flex; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
  .go { padding: 11px 22px; border: 1px solid var(--cyan); background: none; color: var(--cyan);
    cursor: crosshair; font-size: 11px; letter-spacing: 0.16em; transition: background 140ms, color 140ms; }
  .go:hover { background: var(--cyan); color: #04070a; box-shadow: 0 0 18px color-mix(in srgb, var(--cyan) 40%, transparent); }
  .alt { padding: 11px 18px; border: 1px solid var(--ink-dim); background: none; color: var(--ink-dim);
    cursor: crosshair; font-size: 11px; letter-spacing: 0.16em; }
  .alt:hover:not(:disabled) { border-color: var(--scarlet); color: var(--scarlet); }
  .alt:disabled { opacity: 0.4; }
  .hint { font-size: 11px; color: var(--ink-ghost); letter-spacing: 0.14em; }

  @media (max-width: 720px) {
    .body { grid-template-columns: 1fr; grid-template-rows: 38% 1fr; }
    .queue { border-right: none; border-bottom: 1px solid var(--hairline); }
  }
</style>
