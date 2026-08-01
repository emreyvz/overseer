<!-- Always-available way to summon the AI Operator, so it is never more than one click (or the I
     key) away. Sits out of the way bottom-right; hidden while the Operator itself is open. -->
<script lang="ts">
  import { operatorOpen } from '../lib/stores'
  import { sfx } from '../lib/audio'
  function open() { sfx('click'); operatorOpen.set(true) }
</script>

<button class="launch" onclick={open} title="AI Operator (I)" aria-label="Open AI Operator">
  <span class="orb"></span><span class="lbl caps">OPERATOR</span>
</button>

<style>
  .launch { position: fixed; right: 18px; bottom: 22px; z-index: 300; display: flex; align-items: center; gap: 9px;
    background: rgba(10,12,14,0.9); border: 1px solid rgba(47,169,113,0.4); color: var(--ink-dim);
    padding: 9px 13px; cursor: pointer; font: inherit; letter-spacing: 0.16em; backdrop-filter: blur(3px); }
  .launch:hover { color: var(--ink); border-color: #2fa971; }
  .orb { width: 10px; height: 10px; border-radius: 50%; background: #2fa971; box-shadow: 0 0 8px #2fa971; animation: pulse 2.4s ease-in-out infinite; flex: 0 0 auto; }
  .lbl { font-size: 10px; }
  @keyframes pulse { 0%, 100% { opacity: 0.55; } 50% { opacity: 1; } }
  @media (prefers-reduced-motion: reduce) { .orb { animation: none; } }
</style>
