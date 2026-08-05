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

  let expanded = $state<string | null>(null)

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
          <button class="name caps" onclick={() => (expanded = expanded === f.name ? null : f.name)}>
            {f.name}
            <span class="chev">{expanded === f.name ? '▾' : '▸'}</span>
          </button>
          <span class="state caps s-{r.state}">{READY_LABEL[r.state]}</span>
          <span class="accel caps">{f.accel}</span>
        </div>

        <div class="note caps">{r.note}</div>

        {#if expanded === f.name}
          <div class="detail">
            <p class="what">{f.what}</p>
            <p class="next caps">NEXT · {f.next}</p>
          </div>
        {/if}

        <button class="go caps" onclick={() => open(f)}>{f.openLabel} →</button>
      </div>
    {/each}
  </div>

  <footer class="pf caps">
    PRESS A NAME TO READ WHAT IT DOES · THESE COST ANALYSIS SPEED, SO RUN WHAT YOU ARE LOOKING AT
  </footer>
</div>

<style>
  /* Above the rails and clear of both of them. The readouts of these features were previously
     rendered underneath the auxiliary rail, which is why nobody could find them. */
  .pp { position: absolute; left: 200px; bottom: 52px; z-index: calc(var(--z-panel) + 3);
    width: 380px; max-height: 74vh; overflow-y: auto; display: flex; flex-direction: column;
    animation: pin 220ms cubic-bezier(0.16, 1, 0.3, 1); }
  /* No `both` fill: with it, an entry animation that has not started yet holds its
     `from` state, and an element whose `from` is opacity 0 stays invisible forever. */
  @keyframes pin { from { opacity: 0; transform: translateY(10px); } }

  .ph { display: flex; align-items: center; gap: 9px; padding: 11px 13px 9px; flex-wrap: wrap;
    border-bottom: 1px solid var(--hairline); }
  .eyebrow { font-size: 10px; color: var(--scarlet); letter-spacing: 0.16em; }
  .sub { font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.14em; flex: 1 0 100%; order: 3; }
  .x { margin-left: auto; background: none; border: none; color: var(--ink-ghost); font-size: 11px;
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

  .name { flex: 1; text-align: left; background: none; border: none; padding: 0;
    color: var(--ink-dim); font-size: 11px; letter-spacing: 0.14em; cursor: crosshair; }
  .row.on .name { color: var(--ink); }
  .name:hover { color: var(--cyan); }
  .chev { color: var(--ink-ghost); margin-left: 5px; }

  .state { font-size: 7px; letter-spacing: 0.12em; padding: 2px 5px; border: 1px solid transparent; }
  .s-ready { color: var(--jade); border-color: color-mix(in srgb, var(--jade) 40%, transparent); }
  .s-learning { color: var(--amber); border-color: color-mix(in srgb, var(--amber) 40%, transparent); }
  .s-setup { color: var(--cyan); border-color: color-mix(in srgb, var(--cyan) 40%, transparent); }
  .s-off { color: var(--ink-ghost); }
  .accel { font-size: 7px; color: var(--ink-ghost); border: 1px solid var(--hairline);
    padding: 1px 4px; min-width: 14px; text-align: center; }

  .note { font-size: 8px; color: var(--ink-dim); letter-spacing: 0.1em; padding-left: 35px; }
  .detail { padding-left: 35px; display: flex; flex-direction: column; gap: 6px;
    animation: din 200ms var(--ease); }
  @keyframes din { from { opacity: 0; } }
  .what { margin: 0; font-size: 10px; color: var(--ink); line-height: 1.7; }
  .next { margin: 0; font-size: 8px; color: var(--cyan); letter-spacing: 0.1em; line-height: 1.6; }

  .go { align-self: flex-start; margin-left: 35px; padding: 5px 10px; border: 1px solid var(--hairline);
    background: none; color: var(--ink-dim); font-size: 8px; letter-spacing: 0.14em; cursor: crosshair; }
  .go:hover { border-color: var(--cyan); color: var(--cyan); }

  .pf { padding: 9px 13px; font-size: 7px; color: var(--ink-ghost); letter-spacing: 0.12em;
    line-height: 1.6; border-top: 1px solid var(--hairline); }
</style>
