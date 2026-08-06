<script lang="ts">
  // A term you can ask about, in place.
  //
  // The perception screens were written in the vocabulary of the people who built them. An
  // operator met DECIDEDNESS, PROMINENCE, TRANSIENT and VALID TIME with nothing to click and no
  // hint that an explanation existed anywhere, so the only way to learn what a screen was saying
  // was to ask a person. That is a design failure, not a training problem.
  //
  // This wraps any term in a marked, focusable affordance. The dotted underline is the promise:
  // wherever you see one, one sentence of plain English is a hover or a tap away. Consistency is
  // the whole point — an operator learns the convention once and then never has to wonder again.
  //
  // `plain` swaps the leading label for the plain-language wording and demotes the technical term
  // into the popover, which is the right way round for terms that teach nothing on sight.
  import { lookup } from '../lib/glossary'

  // `bare` renders the marker alone, for labels that already read as English and only need a way
  // to ask for the detail behind them. Without it the marker would double up on its own label.
  let {
    term,
    plain = false,
    bare = false,
    children,
  }: { term: string; plain?: boolean; bare?: boolean; children?: any } = $props()

  const entry = $derived(lookup(term))
  const label = $derived(plain && entry?.plain ? entry.plain : term)

  let host = $state<HTMLElement | null>(null)
  let shown = $state(false)
  // Fixed-position coordinates, because these live inside scrolling panels with `overflow:hidden`
  // and an absolutely-positioned bubble would be clipped by its own parent.
  let x = $state(0)
  let y = $state(0)
  let flip = $state(false)

  // The bubble is moved to <body> on mount. `position: fixed` is only fixed to the VIEWPORT when
  // no ancestor is transformed; any ancestor with a transform (or a filling animation that
  // animates one) becomes its containing block instead. The Eardrum drawer slides up with a
  // translate, so a bubble left in place was positioned against the drawer and ran off the
  // bottom of the screen — the same trap that hid the fog readout under the rails. Living on
  // <body> makes Explain safe to drop absolutely anywhere, which is the point of it.
  function portal(node: HTMLElement) {
    document.body.appendChild(node)
    return { destroy: () => node.remove() }
  }

  function place() {
    if (!host) return
    const r = host.getBoundingClientRect()
    const W = 268
    x = Math.min(Math.max(10, r.left + r.width / 2 - W / 2), window.innerWidth - W - 10)
    // Flip below when there is not enough room above for the tallest bubble a long sentence
    // produces (measured at ~145px), otherwise it would run off the top of the screen.
    const H = 150
    flip = r.top < 190
    y = flip ? r.bottom + 8 : r.top - 8
    // Clamp into the viewport regardless. A marker inside a scrolling panel can sit past the
    // fold, and an explanation the operator cannot read is worse than no marker at all.
    y = flip
      ? Math.min(y, window.innerHeight - H - 10)
      : Math.min(Math.max(y, H + 10), window.innerHeight - 10)
  }

  function show() { place(); shown = true }
  function hide() { shown = false }
</script>

{#if entry}
  <span
    class="ex"
    class:bare
    bind:this={host}
    role="button"
    tabindex="0"
    aria-label={`${label}: ${entry.what}`}
    onmouseenter={show}
    onmouseleave={hide}
    onfocus={show}
    onblur={hide}
    onclick={(e) => { e.stopPropagation(); shown ? hide() : show() }}
    onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); shown ? hide() : show() } }}
  >{#if bare}{:else if children}{@render children()}{:else}{label}{/if}<span class="q">?</span></span>

  {#if shown}
    <span class="bub" use:portal class:below={flip}
      style={`left:${x}px; ${flip ? 'top' : 'bottom'}:${flip ? y : window.innerHeight - y}px`}>
      <span class="bt">{label}</span>
      <span class="bw">{entry.what}</span>
      {#if plain && entry.plain}<span class="bn">called “{term}” in the technical readouts</span>{/if}
    </span>
  {/if}
{:else}
  <!-- No entry: render the label untouched rather than promising an explanation that is not there. -->
  {#if children}{@render children()}{:else}{label}{/if}
{/if}

<style>
  .ex { border-bottom: 1px dotted color-mix(in srgb, currentColor 55%, transparent);
    cursor: help; position: relative; }
  .ex:hover, .ex:focus-visible { color: var(--cyan); border-bottom-color: var(--cyan); outline: none; }
  /* A small raised mark, so the affordance survives on a line where everything is already
     underlined or coloured. It is the thing an operator learns to look for. */
  .q { font-size: 0.72em; vertical-align: super; margin-left: 2px; opacity: 0.55; letter-spacing: 0; }
  .ex:hover .q, .ex:focus-visible .q { opacity: 1; }
  /* Standing alone the marker needs to be a target in its own right, not a 4px superscript. */
  .ex.bare { border-bottom: none; padding: 0 3px; }
  .ex.bare .q { font-size: 1em; vertical-align: baseline; margin-left: 0;
    border: 1px solid color-mix(in srgb, currentColor 40%, transparent); padding: 0 3px; }

  /* Above the command palette: a transient explanation must never be the thing that is covered. */
  .bub { position: fixed; z-index: calc(var(--z-cmd) + 1); width: 268px; display: flex; flex-direction: column;
    gap: 5px; padding: 10px 12px; background: #070b10; border: 1px solid var(--cyan);
    box-shadow: 0 10px 34px rgba(0,0,0,0.72); pointer-events: none;
    animation: exin 130ms cubic-bezier(0.16, 1, 0.3, 1) both; }
  @keyframes exin { from { opacity: 0; transform: translateY(3px); } }
  .bt { font-size: 8px; color: var(--cyan); letter-spacing: 0.18em; text-transform: uppercase; }
  .bw { font-size: 11px; color: var(--ink); line-height: 1.6; letter-spacing: 0; text-transform: none; }
  .bn { font-size: 9px; color: var(--ink-ghost); line-height: 1.5; letter-spacing: 0; text-transform: none; }
</style>
