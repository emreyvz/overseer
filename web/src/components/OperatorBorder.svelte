<!-- A thin, glowing inset border shown while the AI Operator is driving the system, so the operator
     can see it is being navigated by the AI. Green = navigation / query, scarlet = alarm / critical
     action. Non-interactive; sits above everything. -->
<script lang="ts">
  import { operatorActive } from '../lib/operator'
</script>

{#if $operatorActive}
  <div class="op-border {$operatorActive}" aria-hidden="true">
    <span class="tag caps">{$operatorActive === 'alert' ? 'AI · ACTION' : 'AI · OPERATING'}</span>
  </div>
{/if}

<style>
  .op-border {
    position: fixed; inset: 0; z-index: 400; pointer-events: none;
    border: 1.5px solid var(--c); box-shadow: inset 0 0 22px 2px color-mix(in srgb, var(--c) 42%, transparent);
    animation: op-pulse 1.15s ease-in-out infinite;
  }
  .op-border.nav { --c: #1fa971; }        /* dark green — routine navigation / query */
  .op-border.alert { --c: var(--scarlet); } /* scarlet — alarm / critical action */
  .tag {
    position: absolute; top: 10px; left: 50%; transform: translateX(-50%);
    font-size: 10px; letter-spacing: 0.28em; color: var(--c);
    background: rgba(0, 0, 0, 0.55); padding: 3px 10px; border: 1px solid color-mix(in srgb, var(--c) 55%, transparent);
  }
  @keyframes op-pulse {
    0%, 100% { opacity: 0.55; }
    50% { opacity: 1; }
  }
  @media (prefers-reduced-motion: reduce) { .op-border { animation: none; opacity: 0.85; } }
</style>
