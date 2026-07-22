<script lang="ts">
  // Pet registry + find-my-pet (feature 2). Appearance-based visual re-identification.
  import { petRegistry, mode, forensicSeed, selectedDetection } from '../lib/stores'
  import { sfx } from '../lib/audio'
  import { pets, addPet, removePet, setLost } from '../lib/pets'

  let name = $state(''), owner = $state(''), species = $state('dog'), color = $state('')
  let sel = $derived($selectedDetection)

  function useSelected() {
    if (sel && sel.cls === 'animal') { sfx('click'); species = 'animal'; color = sel.attrs?.upper_color ?? '' }
    else sfx('error')
  }
  function add() {
    if (!name.trim()) { sfx('error'); return }
    sfx('sonar'); addPet({ name: name.trim(), owner: owner.trim(), species, color: color.trim(), lost: false })
    name = ''; owner = ''; color = ''
  }
  function find(id: string, sp: string, col: string) {
    sfx('sonar'); setLost(id, true)
    forensicSeed.set([sp, col, 'animal'].filter(Boolean).join(' '))
    petRegistry.set(false); mode.set('forensic')
  }
</script>

<button class="scrim" aria-label="close" onpointerdown={() => petRegistry.set(false)}></button>
<aside class="pr panel caps">
  <header class="tab"><span>/// PET REGISTRY <span class="sub">· VISUAL RE-ID</span></span><button class="x" onclick={() => petRegistry.set(false)} aria-label="close">×</button></header>

  <div class="body">
    <div class="new">
      <div class="cl">REGISTER PET</div>
      <label>NAME<input bind:value={name} placeholder="e.g. LUNA" spellcheck="false" /></label>
      <label>OWNER<input bind:value={owner} placeholder="OWNER / CASE" spellcheck="false" /></label>
      <div class="two">
        <label>SPECIES
          <select bind:value={species}><option value="dog">DOG</option><option value="cat">CAT</option><option value="bird">BIRD</option><option value="animal">OTHER</option></select>
        </label>
        <label>COLOR<input bind:value={color} placeholder="BROWN" spellcheck="false" /></label>
      </div>
      {#if sel?.cls === 'animal'}<button class="use" onclick={useSelected}>USE SELECTED ANIMAL ({sel.id})</button>{/if}
      <button class="create" onclick={add}>+ REGISTER PET</button>
    </div>

    <div class="list">
      <div class="cl">REGISTERED · {$pets.length}</div>
      {#each $pets as p}
        <div class="pet" class:lost={p.lost}>
          <div class="pline"><span class="pn">{p.name}</span><span class="pmeta">{p.species}{p.color ? ` · ${p.color}` : ''}{p.owner ? ` · ${p.owner}` : ''}</span></div>
          <div class="pact">
            {#if p.lost}<span class="lostbadge">LOST</span>{/if}
            <button class="mini" onclick={() => find(p.id, p.species, p.color)}>FIND ▸</button>
            <button class="mini" onclick={() => { sfx('click'); setLost(p.id, !p.lost) }}>{p.lost ? 'FOUND' : 'MARK LOST'}</button>
            <button class="mini del" onclick={() => { sfx('click'); removePet(p.id) }} aria-label="delete">×</button>
          </div>
        </div>
      {/each}
      {#if $pets.length === 0}<div class="mt">NO PETS · REGISTER ONE, THEN FIND ▸ SEARCHES ALL FEEDS</div>{/if}
    </div>
  </div>
</aside>

<style>
  .scrim { position: fixed; inset: 0; z-index: var(--z-cmd); background: rgba(0,0,0,0.45); }
  .pr { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: min(600px, 92vw); max-height: 84vh; z-index: calc(var(--z-cmd) + 1); display: flex; flex-direction: column; }
  .tab { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: #000; border-bottom: 1px solid var(--hairline); font-size: var(--fs-banner); letter-spacing: var(--tracking); color: var(--ink); }
  .tab .sub { font-size: var(--fs-micro); color: var(--ink-ghost); }
  .x { font-size: 18px; color: inherit; }
  .body { display: grid; grid-template-columns: 1fr 1fr; overflow: hidden; }
  .new, .list { padding: 12px; overflow-y: auto; }
  .list { border-left: 1px solid var(--hairline); }
  .cl { font-size: var(--fs-label); color: var(--scarlet); letter-spacing: var(--tracking); margin-bottom: 8px; }
  label { display: flex; flex-direction: column; gap: 3px; font-size: 8px; color: var(--ink-ghost); margin-bottom: 8px; }
  .two { display: flex; gap: 8px; } .two label { flex: 1; }
  input, select { background: #000; border: 1px solid var(--hairline); color: var(--ink); font-family: var(--font-mono); font-size: var(--fs-micro); padding: 5px 7px; text-transform: uppercase; }
  input:focus, select:focus { outline: none; border-color: var(--scarlet); }
  .use { width: 100%; padding: 5px; margin-bottom: 8px; border: 1px solid var(--cyan); color: var(--cyan); font-size: 8px; letter-spacing: var(--tracking); }
  .use:hover { background: var(--cyan); color: #000; }
  .create { width: 100%; padding: 8px; border: 1px solid var(--scarlet); color: var(--ink); font-size: var(--fs-label); letter-spacing: var(--tracking); }
  .create:hover { background: var(--scarlet); color: #fff; }

  .pet { padding: 6px 4px; border-bottom: 1px solid var(--hairline); }
  .pet.lost { background: rgba(225,6,0,0.06); }
  .pline { display: flex; flex-direction: column; gap: 1px; margin-bottom: 4px; }
  .pn { color: var(--ink); font-size: var(--fs-micro); } .pmeta { color: var(--ink-ghost); font-size: 8px; }
  .pact { display: flex; align-items: center; gap: 6px; }
  .lostbadge { background: var(--scarlet); color: #fff; font-size: 8px; padding: 1px 5px; letter-spacing: 0.1em; }
  .mini { padding: 2px 8px; border: 1px solid var(--ink-ghost); font-size: 8px; letter-spacing: 0.08em; color: var(--ink-dim); }
  .mini:hover { border-color: var(--ink); color: var(--ink); }
  .mini.del:hover { border-color: var(--scarlet); color: var(--scarlet); }
  .mt { color: var(--ink-ghost); font-size: var(--fs-micro); }
</style>
