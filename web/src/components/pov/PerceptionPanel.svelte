<script lang="ts">
  // The way IN to the perception suite.
  //
  // These five engines previously had no visible entry point at all: a checkbox buried in the
  // modules rail and a keyboard shortcut you had to already know. This is one button that says
  // what each of them does, whether it is ready, and opens it. The shortcuts still work as
  // accelerators, but nothing requires them any more.
  import { FEATURES, READY_LABEL, isOn, readiness, toggle } from '../../lib/perception'
  import { perceptionPanel } from '../../lib/stores'
  import { sfx } from '../../lib/audio'

  // What each engine does used to be COLLAPSED behind a chevron, so the panel's one job — telling
  // you what these things are — was the one thing it hid by default. It is always visible now.
  const ready = $derived($readiness)

  function flip(key: string) {
    if (!key) return
    sfx('click', { volume: 0.25 })
    toggle(key)
  }
  function open(f: (typeof FEATURES)[number]) {
    sfx('sonar')
    if (f.key && !isOn(f.key)) toggle(f.key)
    f.open()
    perceptionPanel.set(false)
  }
</script>

<div class="pp panel" role="dialog" aria-label="Perception">
  <header class="ph caps">
    <span class="eyebrow">◈ PERCEPTION</span>
    <span class="sub">WHAT THIS CAMERA CAN WORK OUT ON ITS OWN</span>
    <button class="x" onclick={() => perceptionPanel.set(false)} aria-label="close">✕</button>
  </header>

  <div class="list">
    {#each FEATURES as f (f.name)}
      {@const r = ready[f.key] ?? { state: 'off', note: '' }}
      <div class="row" class:on={isOn(f.key)}>
        <div class="top">
          {#if f.key}
            <button class="sw" class:lit={isOn(f.key)} onclick={() => flip(f.key)}
              aria-label={`toggle ${f.name}`} title={isOn(f.key) ? 'Switch off' : 'Switch on'}>
              <span class="dot"></span>
            </button>
          {:else}
            <span class="sw static"><span class="dot mode"></span></span>
          {/if}
          <span class="name caps">{f.name}</span>
          <span class="state caps s-{r.state}">{READY_LABEL[r.state]}</span>
          <span class="accel caps">{f.accel}</span>
        </div>

        <div class="detail">
          <p class="what">{f.what}</p>
          <p class="note">{r.note}</p>
          <p class="next">Next · {f.next}</p>
        </div>

        <button class="go caps" onclick={() => open(f)}>{f.openLabel()}<span class="arw">▸</span></button>
      </div>
    {/each}
  </div>

  <footer class="pf">
    Each of these costs analysis speed, so run the ones you are actually looking at.
  </footer>
</div>

<style>
  /* Above the rails and clear of both of them. The readouts of these features were previously
     rendered underneath the auxiliary rail, which is why nobody could find them. */
  .pp { position: absolute; left: 200px; bottom: 52px; z-index: calc(var(--z-panel) + 3);
    width: 452px; max-height: 80vh; overflow-y: auto; display: flex; flex-direction: column;
    animation: pin 220ms cubic-bezier(0.16, 1, 0.3, 1); }
  /* No `both` fill: with it, an entry animation that has not started yet holds its
     `from` state, and an element whose `from` is opacity 0 stays invisible forever. */
  @keyframes pin { from { opacity: 0; transform: translateY(10px); } }

  .ph { display: flex; align-items: center; gap: 9px; padding: 11px 13px 9px; flex-wrap: wrap;
    border-bottom: 1px solid var(--hairline); }
  .eyebrow { font-size: 11px; color: var(--scarlet); letter-spacing: 0.16em; }
  .sub { font-size: 10px; color: var(--ink-ghost); letter-spacing: 0.12em; flex: 1 0 100%; order: 3; }
  .x { margin-left: auto; background: none; border: none; color: var(--ink-ghost); font-size: 13px;
    cursor: crosshair; }
  .x:hover { color: var(--scarlet); }

  .list { display: flex; flex-direction: column; }
  .row { padding: 10px 13px; border-bottom: 1px solid var(--hairline);
    display: flex; flex-direction: column; gap: 5px; }
  .row:last-child { border-bottom: none; }
  .top { display: flex; align-items: center; gap: 9px; }

  .sw { width: 26px; height: 14px; flex: 0 0 auto; padding: 0; background: none;
    border: 1px solid var(--ink-dim); cursor: crosshair; position: relative; }
  .sw.static { border-style: dashed; cursor: default; }
  .dot { position: absolute; left: 1px; top: 1px; width: 10px; height: 10px;
    background: var(--ink-dim); transition: left 140ms, background 140ms; }
  .sw.lit { border-color: var(--scarlet); }
  .sw.lit .dot { left: 13px; background: var(--scarlet); box-shadow: 0 0 6px var(--scarlet-glow); }
  .dot.mode { background: var(--cyan); }

  .name { flex: 1; text-align: left; color: var(--ink-dim); font-size: 13px; letter-spacing: 0.16em; }
  .row.on .name { color: var(--ink); }

  .state { font-size: 9px; letter-spacing: 0.12em; padding: 3px 6px; border: 1px solid transparent; }
  .s-ready { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 40%, transparent); }
  .s-learning { color: var(--amber); border-color: color-mix(in srgb, var(--amber) 40%, transparent); }
  .s-setup { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 40%, transparent); }
  .s-off { color: var(--ink-ghost); }
  .accel { font-size: 9px; color: var(--ink-ghost); border: 1px solid var(--hairline);
    padding: 2px 5px; min-width: 16px; text-align: center; }

  .detail { padding-left: 35px; display: flex; flex-direction: column; gap: 5px; }
  .what { margin: 0; font-size: 12px; color: var(--ink); line-height: 1.6; }
  .note { margin: 0; font-size: 11px; color: var(--ink-dim); line-height: 1.5; }
  .next { margin: 0; font-size: 11px; color: var(--cyan); line-height: 1.5; }

  /* The way in: bordered, cyan, with a verb and an arrow. It was previously an 8px grey chip that
     read as a caption, which is why the operator opened a screen without knowing they had. */
  .go { align-self: stretch; margin: 3px 0 0 35px; padding: 9px 12px;
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    border: 1px solid color-mix(in srgb, var(--cyan) 55%, transparent); background: none;
    color: var(--cyan); font-size: 10px; letter-spacing: 0.14em; cursor: crosshair;
    transition: background 140ms, color 140ms; }
  .go:hover { background: var(--cyan); color: #04070a;
    box-shadow: 0 0 16px color-mix(in srgb, var(--cyan) 35%, transparent); }
  .arw { flex: 0 0 auto; }

  .pf { padding: 10px 13px; font-size: 11px; color: var(--ink-ghost);
    line-height: 1.55; border-top: 1px solid var(--hairline); }
</style>
