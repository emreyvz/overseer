<script lang="ts">
  // A one-time explanation, anchored next to the thing it explains.
  //
  // Shown the first time an overlay appears and never again. Deliberately not a tooltip: a
  // tooltip requires you to already suspect there is something to hover, which is no help at
  // all when you do not know what you are looking at.
  import { COACH, markCoached } from '../../lib/perception'
  import { sfx } from '../../lib/audio'

  let { id, place = 'left' }: { id: string; place?: 'left' | 'right' | 'centre' } = $props()

  const card = $derived(COACH[id])

  function dismiss() {
    sfx('click', { volume: 0.2 })
    markCoached(id)
  }
</script>

{#if card}
  <div class="coach panel p-{place}" role="note">
    <div class="ct caps">{card.title}</div>
    <p class="cb">{card.body}</p>
    {#if card.tip}<p class="tip caps">{card.tip}</p>{/if}
    <button class="got caps" onclick={dismiss}>GOT IT</button>
  </div>
{/if}

<style>
  /* Sits above the rails: this is the one thing on screen that must not be hidden by chrome. */
  .coach { position: absolute; z-index: calc(var(--z-panel) + 2); width: 320px;
    padding: 13px 14px 11px; display: flex; flex-direction: column; gap: 8px;
    pointer-events: auto; border-color: color-mix(in srgb, var(--cyan) 45%, transparent);
    box-shadow: 0 0 24px rgba(0,0,0,0.6);
    animation: cin 300ms cubic-bezier(0.16, 1, 0.3, 1); }
  /* No `both` fill: with it, an entry animation that has not started yet holds its
     `from` state, and an element whose `from` is opacity 0 stays invisible forever. */
  @keyframes cin { from { opacity: 0; transform: translateY(10px); } }
  /* clear of the modules rail (left 20 + 164 wide) and the auxiliary rail (right 20 + 300) */
  .p-left { left: 200px; top: 96px; }
  .p-right { right: 336px; top: 96px; }
  .p-centre { left: 50%; transform: translateX(-50%); top: 96px; }

  .ct { font-size: 9px; color: var(--cyan); letter-spacing: 0.16em; line-height: 1.5; }
  .cb { margin: 0; font-size: 11px; color: var(--ink); line-height: 1.65; }
  .tip { margin: 0; font-size: 9px; color: var(--ink-dim); letter-spacing: 0.08em; line-height: 1.7;
    padding-top: 7px; border-top: 1px solid var(--hairline); }
  .got { align-self: flex-start; margin-top: 2px; padding: 6px 14px; border: 1px solid var(--cyan);
    background: none; color: var(--cyan); font-size: 9px; letter-spacing: 0.16em; cursor: crosshair; }
  .got:hover { background: var(--cyan); color: #04070a; }
</style>
