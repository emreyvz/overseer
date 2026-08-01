<script lang="ts">
  import PovView from './pov/PovView.svelte'
  import HeatmapOverlay from './pov/HeatmapOverlay.svelte'
  import ZoneOverlay from './pov/ZoneOverlay.svelte'
  import OOIOverlay from './pov/OOIOverlay.svelte'
  import Dossier from './pov/Dossier.svelte'
  import CornerBrackets from './hud/CornerBrackets.svelte'
  import TrackletIds from './hud/TrackletIds.svelte'
  import MicroHud from './hud/MicroHud.svelte'
  import StatusBanner from './hud/StatusBanner.svelte'
  import ModulesRail from './rails/ModulesRail.svelte'
  import DominantRail from './rails/DominantRail.svelte'
  import AuxiliaryRail from './rails/AuxiliaryRail.svelte'
  import TimelineDrawer from './TimelineDrawer.svelte'
  import { mode, stage, pickerView, watchlistOpen, operatorOpen, suggestionsOpen, commandOpen, triggerGlitch } from '../lib/stores'
  import { sfx } from '../lib/audio'

  // Bottom nav — clickable, active-highlighted. Kept lean; heavier tools live in ⌘.
  const NAV = [
    { label: 'POV', key: '1-9', mode: 'pov', act: () => mode.set('pov') },
    { label: 'SOURCES', key: 'C', mode: '', act: () => { pickerView.set('grid'); stage.set('select') } },
    { label: 'FORENSIC', key: 'A', mode: 'forensic', act: () => mode.set('forensic') },
    { label: 'CASES', key: 'K', mode: 'case', act: () => mode.set('case') },
    { label: 'WATCHLIST', key: 'W', mode: '', act: () => watchlistOpen.set(true) },
    { label: 'OPERATOR', key: 'I', mode: '', act: () => operatorOpen.set(true) },
    { label: 'SUGGEST', key: 'G', mode: '', act: () => suggestionsOpen.set(true) },
    { label: 'ARCHIVE', key: 'R', mode: 'archive', act: () => mode.set('archive') },
  ]
  function go(item: (typeof NAV)[number]) { if (item.mode && item.mode === $mode) return; sfx('whoosh'); triggerGlitch(150); item.act() }
</script>

<main class="shell">
  <PovView />
  <HeatmapOverlay />
  <ZoneOverlay />
  <OOIOverlay />
  <CornerBrackets />
  <TrackletIds />
  <MicroHud />
  <ModulesRail />
  <DominantRail />
  <AuxiliaryRail />
  <Dossier />
  <StatusBanner />
  <TimelineDrawer />

  <nav class="modes caps">
    {#each NAV as item}
      <button class="mode" class:on={item.mode && item.mode === $mode} onclick={() => go(item)}>
        {item.label}<span class="key">{item.key}</span>
      </button>
    {/each}
    <button class="mode cmd" onclick={() => commandOpen.set(true)}>COMMAND<span class="key">SPACE</span></button>
  </nav>
</main>

<style>
  .shell { position: absolute; inset: 0; z-index: var(--z-video); }
  .modes { position: absolute; left: 50%; bottom: 46px; transform: translateX(-50%); z-index: var(--z-panel);
    display: flex; gap: 18px; font-size: 13px; color: var(--ink-dim); letter-spacing: var(--tracking); }
  .mode { display: inline-flex; align-items: center; gap: 7px; background: none; border: none; cursor: pointer; color: inherit; font: inherit; letter-spacing: inherit; }
  .mode .key { color: var(--ink); border: 1px solid var(--ink-dim); padding: 1px 6px; font-size: 11px; min-width: 18px; text-align: center; }
  .mode:hover { color: var(--ink); }
  .mode.on { color: var(--scarlet); }
  .mode.on .key { color: var(--scarlet); border-color: var(--scarlet); }
  .mode.cmd { color: var(--ink); }
  .mode.cmd .key { color: var(--scarlet); border-color: var(--scarlet); }
</style>
