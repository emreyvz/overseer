<script lang="ts">
  import { onMount } from 'svelte'
  import {
    stage, pickerView, mode, commandOpen, zoneEditor, alertRules, shuttingDown, objectRegister, storageScreen,
    selectedDetection, dossierOpen, activeCam, cameras, triggerGlitch, flashBanner, enrollOpen, watchlistOpen, aiOpen, type Mode,
  } from './lib/stores'
  import { connectWs, sendCommand } from './lib/ws'
  import { SIM, startSim } from './lib/sim'
  import { sfx, startAmbience, toggleMute } from './lib/audio'
  import { startZoneEngine } from './lib/zones'
  import { startAnomalyEngine } from './lib/anomaly'
  import { refreshAiStatus } from './lib/ai'
  import { LEX } from './lib/lexicon'
  import type { Camera } from './lib/types'

  import Boot from './components/Boot.svelte'
  import Shutdown from './components/Shutdown.svelte'
  import CameraSelect from './components/CameraSelect.svelte'
  import CameraAcquire from './components/CameraAcquire.svelte'
  import MapReturn from './components/MapReturn.svelte'
  import Shell from './components/Shell.svelte'
  import PostFx from './components/PostFx.svelte'
  import CommandBar from './components/hud/CommandBar.svelte'
  import Topology from './components/modes/Topology.svelte'
  import Forensic from './components/modes/Forensic.svelte'
  import Archive from './components/modes/Archive.svelte'
  import Roster from './components/modes/Roster.svelte'
  import ZoneEditor from './components/pov/ZoneEditor.svelte'
  import ObjectRegister from './components/pov/ObjectRegister.svelte'
  import AlertRules from './components/AlertRules.svelte'
  import StorageScreen from './components/StorageScreen.svelte'
  import EnrollModal from './components/pov/EnrollModal.svelte'
  import Watchlist from './components/Watchlist.svelte'
  import AiConsole from './components/AiConsole.svelte'

  const MODE_KEYS: Record<string, Mode> = { a: 'forensic', r: 'archive' }

  onMount(() => { startZoneEngine(); startAnomalyEngine(); if (SIM) startSim(); else { connectWs(); refreshAiStatus() } })

  function onBegin() { startAmbience(); triggerGlitch(240); stage.set('select') }

  function pickCam(cam: Camera | null) {
    triggerGlitch(220)
    if (cam) {
      activeCam.set(cam.id)
      if (!SIM) { sendCommand(`connect:${cam.name}`); flashBanner(LEX.conn.connecting, false, 1200) }
    }
    stage.set('live')
  }

  // Map → camera: play the Overseer revolver acquisition, connecting in parallel.
  let acquireTarget = $state<Camera | null>(null)
  function startAcquire(cam: Camera | null) {
    if (!cam) { pickCam(null); return }
    activeCam.set(cam.id)
    if (!SIM) sendCommand(`connect:${cam.name}`) // begin loading now so the feed is ready as the reel settles
    acquireTarget = cam
  }
  function finishAcquire() { acquireTarget = null; stage.set('live') } // no glitch — the grow-to-fullscreen carries straight into POV

  // Camera → map: recede from the feed into the map (reverse of the acquisition),
  // shrinking toward the camera's own position on the map.
  let returningCam = $state<Camera | null>(null)
  let returnPos = $state({ tx: 50, ty: 50 })
  function returnToMap() {
    const cur = $cameras.find((c) => c.id === $activeCam) ?? null
    if (cur?.coords) {
      const placed = $cameras.filter((c) => c.coords)
      const lats = placed.map((c) => c.coords![0]), lngs = placed.map((c) => c.coords![1])
      const minLa = Math.min(...lats), maxLa = Math.max(...lats), minLo = Math.min(...lngs), maxLo = Math.max(...lngs)
      const nx = (cur.coords[1] - minLo) / ((maxLo - minLo) || 1)
      const ny = 1 - (cur.coords[0] - minLa) / ((maxLa - minLa) || 1)
      returnPos = { tx: 12 + nx * 76, ty: 14 + ny * 72 } // rough viewport mapping (map fits with margins)
    } else {
      returnPos = { tx: 50, ty: 50 }
    }
    pickerView.set('map')
    returningCam = !SIM ? cur : null
    stage.set('select')
  }

  function toMode(next: Mode) { if ($mode === next) return; triggerGlitch(180); sfx('whoosh'); mode.set(next) }

  function switchCam(n: number) {
    const cam = $cameras[n - 1]
    if (!cam) return
    sfx('glitch'); flashBanner(LEX.vector, false, 800)
    activeCam.set(cam.id) // PovView plays the cinematic pan-switch on change
    if (!SIM) sendCommand(`connect:${cam.name}`)
    if ($mode !== 'pov') mode.set('pov')
  }

  function onKey(e: KeyboardEvent) {
    // Never hijack typing: when a field is focused, let it handle the key (ESC blurs).
    const t = e.target as HTMLElement | null
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) {
      if (e.key === 'Escape') t.blur()
      return
    }
    if ($commandOpen) return
    const k = e.key.toLowerCase()
    if (k === ' ' || k === ':') { e.preventDefault(); commandOpen.set(true); sfx('click'); return }

    if (k === 'i') { aiOpen.set(true); sfx('click'); return }
    // The map / picker screen also honours the shortcuts + search + bottom bar.
    if ($stage === 'select') {
      if (k === 'escape') { if ($aiOpen) { aiOpen.set(false); return } if ($enrollOpen) { enrollOpen.set(null); return } if ($watchlistOpen) { watchlistOpen.set(false); return } return }
      if (k === 'g') { pickerView.set('map'); return }
      if (k === 'c') { pickerView.set('grid'); return }
      if (k === 'a') { sfx('whoosh'); triggerGlitch(160); stage.set('live'); mode.set('forensic'); return }
      if (k === 'r') { sfx('whoosh'); triggerGlitch(160); stage.set('live'); mode.set('archive'); return }
      if (k === 'w') { watchlistOpen.set(true); return }
      if (k === 'f11') { e.preventDefault(); toggleFullscreen(); return }
      return
    }
    if ($stage !== 'live') return
    if (k === 'escape') {
      if ($aiOpen) { aiOpen.set(false); return }
      if ($enrollOpen) { enrollOpen.set(null); return }
      if ($watchlistOpen) { watchlistOpen.set(false); return }
      if ($dossierOpen) { dossierOpen.set(false); return }
      if ($selectedDetection) { selectedDetection.set(null); return }
      if ($zoneEditor) { zoneEditor.set(false); return }
      if ($objectRegister) { objectRegister.set(false); return }
      if ($storageScreen) { storageScreen.set(false); return }
      if ($alertRules) { alertRules.set(false); return }
      if ($mode !== 'pov') { toMode('pov'); return }
      returnToMap(); return // recede from the camera back into the map
    }
    if (k === 'f11') { e.preventDefault(); toggleFullscreen(); return }
    if (k === 'x' && e.ctrlKey) { toggleMute(); return }
    if (k === 's' && !e.ctrlKey) { sfx('shutter'); flashBanner('SNAPSHOT CAPTURED', false, 1000); sendCommand('snapshot'); return }
    if (k === 'c') { sfx('whoosh'); triggerGlitch(180); pickerView.set('grid'); stage.set('select'); return }
    if (k === 'z') { zoneEditor.set(true); return }
    if (k === 'o') { objectRegister.set(true); return }
    if (k === 'w') { watchlistOpen.set(true); return }
    if (/^[1-9]$/.test(k)) { switchCam(Number(k)); return }
    if (k in MODE_KEYS) { toMode(MODE_KEYS[k]); return }
  }

  function toggleFullscreen() {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.()
    else document.exitFullscreen?.()
  }
</script>

<svelte:window onkeydown={onKey} />

{#if $stage === 'boot'}
  <Boot onbegin={onBegin} />
{:else if $stage === 'select'}
  {#if $pickerView === 'map'}<Topology onpick={startAcquire} />{:else}<CameraSelect onpick={pickCam} />{/if}
  {#if acquireTarget}<CameraAcquire target={acquireTarget} cameras={$cameras} oncomplete={finishAcquire} />{/if}
  {#if returningCam}<MapReturn cam={returningCam} tx={returnPos.tx} ty={returnPos.ty} oncomplete={() => (returningCam = null)} />{/if}
  {#if $watchlistOpen}<Watchlist />{/if}
  {#if $enrollOpen}<EnrollModal />{/if}
  {#if $pickerView === 'map' && !acquireTarget && !returningCam}
    <nav class="selnav caps">
      <button class="mode on" onclick={() => pickerView.set('map')}>MAP<span class="key">G</span></button>
      <button class="mode" onclick={() => pickerView.set('grid')}>SOURCES<span class="key">C</span></button>
      <button class="mode" onclick={() => { triggerGlitch(160); stage.set('live'); mode.set('forensic') }}>FORENSIC<span class="key">A</span></button>
      <button class="mode" onclick={() => watchlistOpen.set(true)}>WATCHLIST<span class="key">W</span></button>
      <button class="mode" onclick={() => aiOpen.set(true)}>ASSISTANT<span class="key">I</span></button>
      <button class="mode" onclick={() => { triggerGlitch(160); stage.set('live'); mode.set('archive') }}>ARCHIVE<span class="key">R</span></button>
      <button class="mode cmd" onclick={() => commandOpen.set(true)}>COMMAND<span class="key">SPACE</span></button>
    </nav>
  {/if}
  <CommandBar />
{:else}
  <Shell />
  {#if $mode === 'topology'}<Topology />{/if}
  {#if $mode === 'forensic'}<Forensic />{/if}
  {#if $mode === 'archive'}<Archive />{/if}
  {#if $mode === 'roster'}<Roster />{/if}
  {#if $zoneEditor}<ZoneEditor />{/if}
  {#if $objectRegister}<ObjectRegister />{/if}
  {#if $alertRules}<AlertRules />{/if}
  {#if $storageScreen}<StorageScreen />{/if}
  {#if $watchlistOpen}<Watchlist />{/if}
  {#if $enrollOpen}<EnrollModal />{/if}
  <CommandBar />
{/if}

{#if $aiOpen}<AiConsole />{/if}
{#if $shuttingDown}<Shutdown />{/if}
<PostFx />

<style>
  /* bottom nav on the map/picker screen — mirrors the in-camera bar */
  .selnav { position: fixed; left: 50%; bottom: 30px; transform: translateX(-50%); z-index: 105;
    display: flex; gap: 18px; font-size: 13px; color: var(--ink-dim); letter-spacing: var(--tracking); }
  .selnav .mode { display: inline-flex; align-items: center; gap: 7px; background: rgba(0,0,0,0.5); border: none; cursor: pointer; color: inherit; font: inherit; letter-spacing: inherit; padding: 4px 8px; }
  .selnav .mode .key { color: var(--ink); border: 1px solid var(--ink-dim); padding: 1px 6px; font-size: 11px; min-width: 18px; text-align: center; }
  .selnav .mode:hover { color: var(--ink); }
  .selnav .mode.on { color: var(--cyan); }
  .selnav .mode.on .key { color: var(--cyan); border-color: var(--cyan); }
  .selnav .mode.cmd .key { color: var(--scarlet); border-color: var(--scarlet); }
</style>
