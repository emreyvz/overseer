<script lang="ts">
  import { modules, toggleModule, xrayOn, narrateOn } from '../../lib/stores'
  import { sfx } from '../../lib/audio'
  import { LEX } from '../../lib/lexicon'
  const toggleStore = (s: typeof xrayOn) => { sfx('click', { volume: 0.25 }); s.update((v) => !v) }

  let groups = $derived.by(() => {
    const map = new Map<string, typeof $modules>()
    for (const m of $modules) { if (!map.has(m.group)) map.set(m.group, []); map.get(m.group)!.push(m) }
    return [...map.entries()]
  })
  function toggle(key: string) {
    sfx('click', { volume: 0.25 })
    toggleModule(key)  // flips the store, persists locally + gates backend load for DETECTION classes
  }
</script>

<div class="rail panel">
  <div class="head caps">{LEX.modules}</div>
  {#each groups as [group, items]}
    <div class="grp caps">{group}</div>
    {#each items as m}
      <button class="tog caps" class:on={m.on} onclick={() => toggle(m.key)}>
        <span class="dot"></span><span class="lbl">{m.label}</span>
      </button>
    {/each}
  {/each}
  <!-- experiential toggles, driven by their own stores -->
  <div class="grp caps">VISION</div>
  <button class="tog caps" class:on={$xrayOn} onclick={() => toggleStore(xrayOn)} title="Keep tracking a subject behind cover">
    <span class="dot"></span><span class="lbl">OCCLUSION X-RAY</span>
  </button>
  <div class="grp caps">AUDIO</div>
  <button class="tog caps" class:on={$narrateOn} onclick={() => toggleStore(narrateOn)} title="Describe the scene aloud (needs a vision model)">
    <span class="dot"></span><span class="lbl">LIVE NARRATION</span>
  </button>
</div>

<style>
  .rail { position: absolute; left: 20px; top: 50%; transform: translateY(-50%); z-index: var(--z-panel);
    width: 164px; max-height: 78vh; overflow-y: auto; padding: 8px 10px; }
  .head { font-size: var(--fs-micro); color: var(--scarlet); letter-spacing: var(--tracking); margin-bottom: 6px; white-space: nowrap; }
  .grp { font-size: 8px; color: var(--ink); opacity: 0.55; letter-spacing: 0.18em; margin: 9px 0 3px; white-space: nowrap; }
  .tog { display: flex; align-items: center; gap: 8px; width: 100%; padding: 3px 2px; font-size: var(--fs-micro); color: var(--ink-dim); white-space: nowrap; }
  .tog:hover { color: var(--ink); }
  .tog.on { color: var(--ink); }
  .dot { width: 8px; height: 8px; border: 1px solid var(--ink-dim); flex: 0 0 auto; }
  .tog.on .dot { background: var(--scarlet); border-color: var(--scarlet); }
  .lbl { opacity: 1; }
</style>
