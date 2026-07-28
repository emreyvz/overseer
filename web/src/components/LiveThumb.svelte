<script lang="ts">
  // Robust live preview: polls /snap/{id} (single JPEG) and swaps without flicker.
  // Falls back to NoSignal static when no frame is available (item 1,4,5,7,12).
  import { onMount, onDestroy } from 'svelte'
  import NoSignal from './NoSignal.svelte'

  let { id, fps = 3, effect = true, onstatus, download }:
    { id: string; fps?: number; effect?: boolean; onstatus?: (ok: boolean) => void
      download?: { status: string; progress: number } } = $props()
  const SNAP = (import.meta.env.VITE_SNAP_BASE as string | undefined) ?? 'http://127.0.0.1:8787/snap'
  let downloading = $derived(download?.status === 'downloading')

  let src = $state('')
  let ok = $state(false)
  let timer: ReturnType<typeof setInterval> | undefined

  function tick() {
    const img = new Image()
    img.onload = () => { src = img.src; ok = true; onstatus?.(true) }
    img.onerror = () => { ok = false; onstatus?.(false) }
    img.src = `${SNAP}/${id}?t=${Date.now()}`
  }
  onMount(() => {
    tick()
    timer = setInterval(tick, Math.max(200, 1000 / fps))
    return () => clearInterval(timer)
  })
  onDestroy(() => clearInterval(timer))
</script>

<div class="lt">
  {#if !ok && !downloading}<NoSignal />{/if}
  {#if src}<img class="img" {src} alt="" style:opacity={ok ? 1 : 0} />{/if}
  {#if downloading && !ok}
    <div class="dl">
      <span class="spin"></span>
      <span class="caps">DOWNLOADING {Math.round(download?.progress ?? 0)}%</span>
    </div>
  {/if}
  {#if effect}<div class="fx"></div>{/if}
</div>

<style>
  .lt { position: absolute; inset: 0; overflow: hidden; background: #05070a; }
  .img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover;
    filter: saturate(0.5) contrast(1.06) brightness(0.9); transition: opacity 200ms; }
  .fx { position: absolute; inset: 0; pointer-events: none;
    background:
      repeating-linear-gradient(0deg, rgba(0,0,0,0.28) 0 1px, transparent 1px 3px),
      radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.6) 100%),
      linear-gradient(rgba(56,208,227,0.04), rgba(56,208,227,0.04)); }
  .dl { position: absolute; inset: 0; display: flex; flex-direction: column; gap: 8px;
    align-items: center; justify-content: center; color: #7fe3ef; background: #05070a;
    font-size: 11px; letter-spacing: 0.12em; }
  .spin { width: 18px; height: 18px; border: 2px solid rgba(127,227,239,0.25);
    border-top-color: #7fe3ef; border-radius: 50%; animation: sp 0.8s linear infinite; }
  @keyframes sp { to { transform: rotate(360deg); } }
</style>
