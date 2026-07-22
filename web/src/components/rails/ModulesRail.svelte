<script lang="ts">
  import { modules } from '../../lib/stores'
  import { sfx } from '../../lib/audio'
  import { LEX } from '../../lib/lexicon'

  let groups = $derived.by(() => {
    const map = new Map<string, typeof $modules>()
    for (const m of $modules) { if (!map.has(m.group)) map.set(m.group, []); map.get(m.group)!.push(m) }
    return [...map.entries()]
  })
  function toggle(key: string) {
    sfx('click', { volume: 0.25 })
    modules.update((list) => list.map((m) => (m.key === key ? { ...m, on: !m.on } : m)))
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
</div>

<style>
  .rail { position: absolute; left: 20px; top: 50%; transform: translateY(-50%); z-index: var(--z-panel);
    width: 164px; max-height: 78vh; overflow-y: auto; padding: 8px 10px; }
  .head { font-size: var(--fs-micro); color: var(--scarlet); letter-spacing: var(--tracking); margin-bottom: 6px; white-space: nowrap; }
  .grp { font-size: 8px; color: var(--ink-ghost); margin: 8px 0 3px; white-space: nowrap; }
  .tog { display: flex; align-items: center; gap: 8px; width: 100%; padding: 3px 2px; font-size: var(--fs-micro); color: var(--ink-ghost); white-space: nowrap; }
  .tog:hover { color: var(--ink-dim); }
  .tog.on { color: var(--ink); }
  .dot { width: 8px; height: 8px; border: 1px solid var(--ink-ghost); flex: 0 0 auto; }
  .tog.on .dot { background: var(--scarlet); border-color: var(--scarlet); }
  .lbl { opacity: 1; }
</style>
