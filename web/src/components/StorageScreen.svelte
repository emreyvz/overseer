<script lang="ts">
  // Recording & storage management (feature 23).
  import { onMount } from 'svelte'
  import { storageScreen, system, flashBanner } from '../lib/stores'
  import { api } from '../lib/api'
  import { sfx } from '../lib/audio'

  let data = $state<{ recordings: number; sizeGB: number; oldest: number; snapshots?: number; snapshotsMB?: number; clips?: number; clipsMB?: number; recent: { kind: string; start: number; end: number; sizeMB: number; mode: string }[] }>({ recordings: 0, sizeGB: 0, oldest: 0, recent: [] })
  let recs = $state<{ id: number; kind: string; mode: string; start: number; end: number; sizeMB: number; url: string | null }[]>([])
  let play = $state<string | null>(null)
  let offline = $state(false)
  const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'
  let busy = $state(false)
  let pLoading = $state(true)
  async function refresh() { try { data = await api.storage(); recs = await api.recordings() } catch { offline = true } }
  onMount(refresh)
  const d = (ms: number) => (ms ? new Date(ms).toLocaleString('en-GB') : '—')

  async function delRec(id: number) { sfx('click'); recs = recs.filter((r) => r.id !== id); try { await api.deleteRecording(id) } catch {} await refresh() }
  async function cleanup(what: 'snapshots' | 'clips' | 'recordings', label: string) {
    if (busy) return
    busy = true; sfx('sonar')
    if (what === 'recordings') recs = []          // clear the list immediately
    try { const r = await api.storageCleanup(what); flashBanner(`CLEARED ${r.removed} ${label}`, false, 1500) } catch { flashBanner('CLEANUP FAILED', true, 1400) }
    await refresh(); busy = false
  }
  function openPlay(url: string) { pLoading = true; play = url }
</script>

<button class="scrim" aria-label="close" onpointerdown={() => storageScreen.set(false)}></button>
<aside class="st panel caps">
  <header class="tab"><span>/// STORAGE {#if offline}<span class="off">· OFFLINE</span>{/if}</span><button class="x" onclick={() => storageScreen.set(false)} aria-label="close">×</button></header>
  <div class="stats">
    <div class="stat"><span class="v">{data.sizeGB.toFixed(2)}</span><span class="l">GB USED</span></div>
    <div class="stat"><span class="v">{data.recordings}</span><span class="l">RECORDINGS</span></div>
    <div class="stat"><span class="v">{data.clips ?? 0}</span><span class="l">ALERT CLIPS</span></div>
    <div class="stat"><span class="v">{data.snapshots ?? 0}</span><span class="l">SNAPSHOTS</span></div>
  </div>
  <div class="ph">RECORDINGS · REPLAY · OLDEST {d(data.oldest)}</div>
  <div class="list">
    {#each recs as r}
      <div class="row rec" class:playable={r.url}>
        <button class="rplay" onclick={() => { if (r.url) openPlay(API + r.url) }}>
          <span class="k">{r.kind} · {r.mode}{#if r.url}<span class="pl"> ▶</span>{/if}</span><span class="t">{d(r.start)}</span><span class="s">{r.sizeMB} MB</span>
        </button>
        <button class="del" onclick={() => delRec(r.id)} aria-label="delete" title="delete">✕</button>
      </div>
    {/each}
    {#if recs.length === 0}<div class="mt">NO RECORDINGS YET · ALERT CLIPS AND SNAPSHOTS APPEAR HERE</div>{/if}
  </div>

  <div class="cleanup">
    <div class="cl-h">◈ FREE UP SPACE · SAFE TO CLEAR</div>
    <div class="cl-btns">
      <button class="cl" disabled={busy} onclick={() => cleanup('snapshots', 'SNAPSHOTS')}>CLEAR SNAPSHOTS</button>
      <button class="cl" disabled={busy} onclick={() => cleanup('clips', 'CLIPS')}>CLEAR CLIPS</button>
      <button class="cl warn" disabled={busy} onclick={() => cleanup('recordings', 'RECORDINGS')}>CLEAR ALL RECORDINGS</button>
    </div>
  </div>
</aside>

{#if play}
  <button class="player" aria-label="close" onpointerdown={() => (play = null)}>
    <div class="pstage" onpointerdown={(e) => e.stopPropagation()} role="presentation">
      <!-- svelte-ignore a11y_media_has_caption -->
      <video src={play} controls autoplay class:ready={!pLoading} oncanplay={() => (pLoading = false)}></video>
      {#if pLoading}
        <div class="pload"><div class="pspin"></div><div class="pmsg caps">LOADING RECORDING<span class="pdot"></span></div></div>
      {/if}
    </div>
    <span class="plx caps">◈ REPLAY · CLICK OUTSIDE TO CLOSE</span>
  </button>
{/if}

<style>
  .scrim { position: fixed; inset: 0; z-index: var(--z-cmd); background: rgba(0,0,0,0.45); }
  .st { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: min(460px, 92vw); max-height: 42vh; z-index: calc(var(--z-cmd) + 1); display: flex; flex-direction: column; }
  .tab { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: #000; border-bottom: 1px solid var(--hairline); font-size: var(--fs-banner); letter-spacing: var(--tracking); color: var(--ink); }
  .off { font-size: var(--fs-micro); color: var(--scarlet); } .x { font-size: 18px; color: inherit; }
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--hairline); }
  .stat { background: #0a0b0c; padding: 12px; display: flex; flex-direction: column; align-items: center; gap: 3px; }
  .stat .v { font-family: var(--font-display); font-size: 22px; color: var(--ink); }
  .stat .l { font-size: 8px; color: var(--ink-ghost); letter-spacing: var(--tracking); }
  .ph { font-size: var(--fs-micro); color: var(--scarlet); letter-spacing: var(--tracking); padding: 8px 12px; }
  .list { flex: 1; min-height: 0; overflow-y: auto; padding: 0 12px 12px; }
  .row { display: flex; align-items: center; gap: 8px; padding: 2px 0; border-bottom: 1px solid var(--hairline); }
  .rplay { flex: 1; display: grid; grid-template-columns: 1fr auto auto; gap: 10px; padding: 4px 0; font-size: var(--fs-micro); text-align: left; color: inherit; cursor: default; background: none; border: none; }
  .rplay .k { color: var(--ink); } .rplay .t { color: var(--ink-ghost); } .rplay .s { color: var(--ink-dim); }
  .rec.playable .rplay { cursor: crosshair; } .rec.playable:hover { background: #14161a; }
  .del { flex: none; padding: 2px 7px; border: 1px solid var(--hairline); color: var(--ink-ghost); font-size: 9px; cursor: pointer; background: none; }
  .del:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .pl { color: var(--scarlet); }
  .cleanup { border-top: 1px solid var(--hairline); padding: 10px 12px; }
  .cl-h { font-size: 8px; color: var(--ink-ghost); letter-spacing: var(--tracking); margin-bottom: 6px; }
  .cl-btns { display: flex; flex-wrap: wrap; gap: 6px; }
  .cl { padding: 5px 10px; border: 1px solid var(--ink-dim); color: var(--ink-dim); font-size: 8px; letter-spacing: var(--tracking); background: none; cursor: pointer; }
  .cl:hover:not(:disabled) { border-color: var(--ink); color: var(--ink); }
  .cl.warn:hover:not(:disabled) { border-color: var(--scarlet); color: var(--scarlet); }
  .cl:disabled { opacity: 0.5; }
  .player { position: fixed; inset: 0; z-index: calc(var(--z-cmd) + 5); background: rgba(0,0,0,0.92); display: grid; place-content: center; border: none; cursor: pointer; padding: 0; }
  .pstage { position: relative; }
  .player video { display: block; max-width: 90vw; max-height: 82vh; border: 1px solid var(--hairline); background: #000; opacity: 0; transition: opacity 250ms; }
  .player video.ready { opacity: 1; }
  .pload { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px;
    min-width: 320px; min-height: 200px; background: radial-gradient(ellipse at center, #0a0d10, #000); border: 1px solid var(--hairline); }
  .pspin { width: 40px; height: 40px; border: 2px solid var(--hairline); border-top-color: var(--scarlet); border-radius: 50%; animation: pspin 900ms linear infinite; }
  @keyframes pspin { to { transform: rotate(360deg); } }
  .pmsg { font-size: var(--fs-label); letter-spacing: var(--tracking-wide); color: var(--ink-dim); }
  .pdot::after { content: '…'; animation: pblink 1.1s steps(3) infinite; }
  @keyframes pblink { 50% { opacity: 0.3; } }
  .plx { position: absolute; bottom: 26px; left: 50%; transform: translateX(-50%); font-size: var(--fs-micro); color: var(--ink-dim); letter-spacing: var(--tracking); }
  .mt { color: var(--ink-ghost); font-size: var(--fs-micro); padding: 8px 0; }
</style>
