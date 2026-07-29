<script lang="ts">
  // Identity merge center. Surfaces likely-duplicate roster entries side-by-side so the
  // operator can fold them into one canonical identity (MERGE) or mark them as different
  // (NOT SAME, remembered so it's never suggested again). Keeps the roster a registry of
  // real-world entities rather than detections.
  import { onDestroy, onMount } from 'svelte'
  import { api, type MergeCandidate } from '../../lib/api'
  import { sfx } from '../../lib/audio'

  let { onclose }: { onclose: () => void } = $props()
  const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'

  let cands = $state<MergeCandidate[]>([])
  let loading = $state(true)
  let busy = $state(false)

  async function load() {
    loading = true
    try { cands = (await api.mergeCandidates()).candidates } catch { cands = [] }
    loading = false
  }
  const src = (u?: string | null) => (u ? (u.startsWith('http') ? u : API + u) : '')

  async function merge(c: MergeCandidate) {
    if (busy) return
    busy = true; sfx('sonar')
    const keep = c.a.obs >= c.b.obs ? c.a : c.b       // keep the better-observed identity
    const drop = keep === c.a ? c.b : c.a
    try { await api.mergeRoster(keep.id, drop.id) } catch { /* offline */ }
    cands = cands.filter((x) => x !== c)
    busy = false
  }
  async function notSame(c: MergeCandidate) {
    if (busy) return
    busy = true; sfx('click')
    try { await api.mergeReject(c.a.id, c.b.id) } catch { /* offline */ }
    cands = cands.filter((x) => x !== c)
    busy = false
  }
  function onkey(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); onclose() } }
  onMount(() => { sfx('sonar'); load(); window.addEventListener('keydown', onkey, true) })
  onDestroy(() => window.removeEventListener('keydown', onkey, true))
</script>

<div class="mc" role="dialog" aria-label="Identity merge center">
  <header class="top caps">
    <span class="eyebrow">⧉ IDENTITY MERGE</span>
    <span class="cnt">{cands.length} POSSIBLE DUPLICATE{cands.length === 1 ? '' : 'S'}</span>
    <span class="spacer"></span>
    <button class="ref caps" onclick={load}>↻ RESCAN</button>
    <button class="x caps" onclick={onclose}>✕ CLOSE</button>
  </header>

  {#if loading}
    <div class="empty caps">SCANNING FOR DUPLICATES_</div>
  {:else if cands.length === 0}
    <div class="empty caps">NO DUPLICATES TO REVIEW · THE ROSTER LOOKS CLEAN</div>
  {:else}
    <div class="list">
      {#each cands as c (c.a.id + c.b.id)}
        <div class="pair">
          <div class="side">
            <div class="ph">{#if c.a.snapshot}<img src={src(c.a.snapshot)} alt="" />{:else}<div class="noimg caps">NO IMG</div>{/if}</div>
            <div class="meta caps"><span class="id">{c.a.id}</span><span class="sub">{c.a.cam ?? '—'} · {c.a.obs}×{#if c.a.plate} · ▤ {c.a.plate}{/if}</span></div>
          </div>

          <div class="mid">
            <div class="gauge caps">
              <span class="pct">{Math.round(c.similarity * 100)}%</span>
              <span class="rc">{c.reason === 'plate' ? 'SAME PLATE' : 'APPEARANCE'}</span>
              <span class="bar"><span class="fill" style={`width:${Math.round(c.similarity * 100)}%`}></span></span>
            </div>
            <button class="mrg caps" disabled={busy} onclick={() => merge(c)}>⧉ MERGE</button>
            <button class="not caps" disabled={busy} onclick={() => notSame(c)}>✕ NOT SAME</button>
          </div>

          <div class="side">
            <div class="ph">{#if c.b.snapshot}<img src={src(c.b.snapshot)} alt="" />{:else}<div class="noimg caps">NO IMG</div>{/if}</div>
            <div class="meta caps"><span class="id">{c.b.id}</span><span class="sub">{c.b.cam ?? '—'} · {c.b.obs}×{#if c.b.plate} · ▤ {c.b.plate}{/if}</span></div>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .mc { position: fixed; inset: 0; z-index: var(--z-boot); background: radial-gradient(120% 80% at 50% 0%, #0a1016 0%, #05070a 72%);
    color: var(--ink); display: flex; flex-direction: column; overflow: hidden; animation: mcin 300ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes mcin { from { opacity: 0; } }
  .top { display: flex; align-items: center; gap: 12px; padding: 13px 22px; border-bottom: 1px solid var(--hairline);
    font-size: var(--fs-label); letter-spacing: var(--tracking); background: #04070a; }
  .eyebrow { color: var(--scarlet); } .cnt { color: var(--ink-dim); font-size: 9px; } .spacer { flex: 1; }
  .ref, .x { padding: 6px 12px; border: 1px solid var(--ink-dim); color: var(--ink-dim); background: none; cursor: pointer; font-size: 9px; letter-spacing: var(--tracking); }
  .ref:hover { border-color: var(--cyan); color: var(--cyan); } .x:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .empty { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--ink-dim); letter-spacing: 0.16em; text-align: center; padding: 0 40px; }
  .list { flex: 1; min-height: 0; overflow: auto; padding: 18px; display: flex; flex-direction: column; gap: 12px; max-width: 760px; margin: 0 auto; width: 100%; }
  .pair { display: grid; grid-template-columns: 1fr 150px 1fr; gap: 14px; align-items: center; border: 1px solid var(--hairline);
    background: rgba(7,11,14,0.6); padding: 12px; animation: rise 320ms both cubic-bezier(0.16, 1, 0.3, 1); }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
  .side { display: flex; flex-direction: column; gap: 6px; }
  .ph { aspect-ratio: 3/4; max-height: 160px; background: #05070a; border: 1px solid var(--hairline); overflow: hidden; }
  .ph img { width: 100%; height: 100%; object-fit: cover; }
  .noimg { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--ink-ghost); font-size: 9px; }
  .meta { display: flex; flex-direction: column; gap: 2px; }
  .id { font-family: var(--font-display); font-size: 13px; color: var(--ink); letter-spacing: 0.1em; }
  .sub { font-size: 8px; color: var(--ink-dim); }
  .mid { display: flex; flex-direction: column; align-items: center; gap: 8px; }
  .gauge { display: flex; flex-direction: column; align-items: center; gap: 3px; }
  .pct { font-family: var(--font-display); font-size: 22px; color: var(--cyan); }
  .rc { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.16em; }
  .bar { width: 100px; height: 3px; background: var(--hairline); } .fill { display: block; height: 100%; background: var(--cyan); }
  .mrg { width: 100%; padding: 8px; border: 1px solid var(--cyan); background: none; color: var(--cyan); cursor: pointer; font-size: 10px; letter-spacing: 0.14em; }
  .mrg:hover:not(:disabled) { background: var(--cyan); color: #04070a; }
  .not { width: 100%; padding: 7px; border: 1px solid var(--hairline); background: none; color: var(--ink-dim); cursor: pointer; font-size: 9px; letter-spacing: 0.12em; }
  .not:hover:not(:disabled) { border-color: var(--scarlet); color: var(--scarlet); }
  .mrg:disabled, .not:disabled { opacity: 0.5; cursor: default; }
</style>
