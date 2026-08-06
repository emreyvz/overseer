<script lang="ts">
  // One sentence, at the top of every perception screen, answering "what am I looking at".
  //
  // Every one of these screens opened straight into instrumentation. You got a queue, three
  // panels, a chart and a dozen abbreviations, with nothing anywhere saying what the screen was
  // FOR. The information was all present and the point of it was missing.
  //
  // Deliberately lower case and un-tracked: it should read like a person talking, which makes it
  // visibly different from the machine labels around it and stops it becoming more chrome to
  // decode. `hint` carries the single next action, when there is an obvious one.
  let { what, hint }: { what: string; hint?: string } = $props()
</script>

<p class="intro">
  <span class="w">{what}</span>
  {#if hint}<span class="h">{hint}</span>{/if}
  <!-- The markers are useless if nobody knows they are there. Saying it once per screen, in the
       one place an operator is already reading, is what turns them from decoration into a way
       out of "I cannot tell what any of this is without asking". -->
  <span class="k">Anything <span class="mk">underlined<span class="q">?</span></span> explains itself.</span>
</p>

<style>
  .intro { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin: 0;
    padding: 9px 22px; border-bottom: 1px solid var(--hairline);
    background: rgba(56,208,227,0.035); }
  .w { font-size: 12px; color: var(--ink); line-height: 1.6; letter-spacing: 0; }
  .h { font-size: 11px; color: var(--ink-ghost); line-height: 1.6; letter-spacing: 0; }
  .k { margin-left: auto; font-size: 10px; color: var(--ink-ghost); letter-spacing: 0; }
  .mk { border-bottom: 1px dotted color-mix(in srgb, var(--cyan) 60%, transparent); color: var(--cyan); }
  .q { font-size: 0.72em; vertical-align: super; margin-left: 2px; }
</style>
