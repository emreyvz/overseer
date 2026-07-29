<script lang="ts">
  // Overseer signal acquisition (map → camera). Two centered columns over the
  // dimmed map: LEFT a revolver of camera thumbnails+names rolling downward,
  // RIGHT the large view of the currently-cycling camera. On lock, the right
  // view grows smoothly to fullscreen → POV, with no fade-to-black. The real
  // feed connects in parallel (started by the caller) so it is ready on lock.
  import { onMount, onDestroy, untrack } from 'svelte'
  import { sfx } from '../lib/audio'
  import type { Camera } from '../lib/types'
  import LiveThumb from './LiveThumb.svelte'

  let { target, cameras, oncomplete }: { target: Camera; cameras: Camera[]; oncomplete: () => void } = $props()

  const reel: Camera[] = untrack(() => {
    const others = cameras.filter((c) => c.id !== target.id)
    const lead = others.length ? Math.min(5, Math.max(2, others.length + 1)) : 2
    const r: Camera[] = []
    for (let k = 0; k < lead; k++) r.push(others.length ? others[k % others.length] : target)
    r.push(target)
    return r
  })
  const LAST = reel.length - 1
  const CELL = 92 // px between revolver cells
  const SNAP = (import.meta.env.VITE_SNAP_BASE as string | undefined) ?? 'http://127.0.0.1:8787/snap'

  let pos = $state(0)
  let landed = $state(false)

  // Right-hand big view: reload the current camera's snapshot on every reel step
  // so the operator visibly sees each feed cycle past (not just the final one).
  let bigSrc = $state('')
  let wantId = ''
  $effect(() => {
    const id = reel[pos].id
    wantId = id
    const im = new Image()
    im.onload = () => { if (wantId === id) bigSrc = im.src }
    im.src = `${SNAP}/${id}?t=${Date.now()}`
  })
  const timers: ReturnType<typeof setTimeout>[] = []
  const timing = (i: number) => 80 + Math.round(300 * Math.pow(i / LAST, 2.2)) // slot-machine deceleration

  onMount(() => {
    sfx('sonar')
    let i = 0
    const tick = () => {
      pos = i
      if (i >= LAST) { land(); return }
      sfx('click', { volume: 0.13 })
      i++
      timers.push(setTimeout(tick, timing(i)))
    }
    timers.push(setTimeout(tick, 240))
  })
  function land() {
    sfx('ping')
    timers.push(setTimeout(() => (landed = true), 140)) // settle, then grow to fullscreen
    timers.push(setTimeout(() => oncomplete(), 940))
  }
  onDestroy(() => timers.forEach(clearTimeout))
</script>

<div class="acq" class:landed>
  <!-- LEFT: revolver of thumbnails + names, rolling downward -->
  <div class="left">
    <div class="lhdr caps"><span class="hot">///</span> ACQUIRING</div>
    <div class="lview">
      {#each reel as c, i}
        {@const d = pos - i}
        {#if Math.abs(d) <= 2}
          <div class="lcell" class:cur={d === 0}
            style={`transform: translateY(calc(-50% + ${d * CELL}px)); opacity:${d === 0 ? 1 : 0.32};`}>
            <div class="lt"><LiveThumb id={c.id} fps={3} /></div>
            <div class="ln caps">{c.name}</div>
          </div>
        {/if}
      {/each}
    </div>
  </div>

  <!-- RIGHT: big view of the current camera; grows to fullscreen on lock -->
  <div class="big">
    {#if bigSrc}<img class="bimg" src={bigSrc} alt="" />{:else}<div class="bthumb"><LiveThumb id={reel[pos].id} fps={6} /></div>{/if}
    <div class="bframe"></div>
    <div class="bmeta caps"><span class="bname">/// {reel[pos].name}</span><span class="bstat">{landed ? 'LOCK' : 'CYCLING'}</span></div>
  </div>
</div>

<style>
  /* dimmed veil — the map stays visible behind, no hard black */
  .acq { position: absolute; inset: 0; z-index: var(--z-cmd);
    background: radial-gradient(ellipse at 62% 50%, rgba(3,5,7,0.42), rgba(0,0,0,0.72)); animation: fin 220ms var(--ease); }
  @keyframes fin { from { opacity: 0; } }
  .acq.landed { background: transparent; transition: background 500ms; }

  /* LEFT column */
  .left { position: fixed; top: 13vh; bottom: 13vh; left: 6vw; width: 24vw; display: flex; flex-direction: column;
    transition: opacity 320ms var(--ease), transform 320ms var(--ease); }
  .acq.landed .left { opacity: 0; transform: translateX(-24px); pointer-events: none; }
  .lhdr { font-size: var(--fs-title); letter-spacing: var(--tracking-wide); color: var(--ink); margin-bottom: 10px; }
  .lhdr .hot { color: var(--scarlet); }
  .lview { position: relative; flex: 1; overflow: hidden; }
  .lcell { position: absolute; top: 50%; left: 0; right: 0; display: flex; align-items: center; gap: 10px;
    transition: transform 200ms cubic-bezier(0.2, 0.7, 0.2, 1), opacity 200ms; will-change: transform, opacity; }
  .lcell .lt { position: relative; width: 92px; height: 52px; flex: none; border: 1px solid var(--hairline); overflow: hidden; }
  .lcell.cur .lt { border-color: var(--scarlet); box-shadow: 0 0 16px var(--scarlet-glow); }
  .lcell .ln { font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .lcell.cur .ln { color: var(--ink); }
  /* RIGHT big view → grows to fullscreen on lock (explicit edges for a smooth transition) */
  .big { position: fixed; top: 13vh; bottom: 13vh; left: 34vw; right: 6vw; overflow: hidden; background: #05070a;
    border: 1px solid var(--hairline); box-shadow: 0 0 40px rgba(0,0,0,0.6);
    transition: top 760ms var(--ease), bottom 760ms var(--ease), left 760ms var(--ease), right 760ms var(--ease), border-color 500ms, box-shadow 500ms; }
  .acq.landed .big { top: 0; bottom: 0; left: 0; right: 0; border-color: transparent; box-shadow: none; }
  .bthumb { position: absolute; inset: 0; }
  .bimg { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; filter: saturate(0.55) contrast(1.05) brightness(0.92); }
  .bframe { position: absolute; inset: 0; pointer-events: none;
    background: radial-gradient(ellipse at center, transparent 60%, rgba(0,0,0,0.5) 100%); transition: opacity 500ms; }
  .acq.landed .bframe { opacity: 0; }
  .bmeta { position: absolute; left: 12px; right: 12px; bottom: 10px; display: flex; justify-content: space-between;
    font-size: var(--fs-label); letter-spacing: var(--tracking); color: var(--ink); transition: opacity 300ms; }
  .bmeta .bstat { color: var(--scarlet); }
  .acq.landed .bmeta { opacity: 0; }
</style>
