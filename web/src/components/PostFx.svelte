<script lang="ts">
  // CSS-based post-FX: scanlines · vignette · grain · glitch pulse (brief §4).
  // (A WebGL lens/pincushion pass can be layered later via lib/hud/postfx.ts.)
  import { glitch } from '../lib/stores'
</script>

<div class="fx" class:glitch={$glitch} aria-hidden="true">
  <div class="scan"></div>
  <div class="grain"></div>
  <div class="vig"></div>
</div>

<style>
  .fx { position: fixed; inset: 0; pointer-events: none; z-index: 105; }

  .scan {
    position: absolute; inset: 0;
    background: repeating-linear-gradient(0deg, rgba(0,0,0,0.22) 0 1px, transparent 1px 3px);
    opacity: 0.5; mix-blend-mode: multiply; animation: drift 8s linear infinite;
  }
  @keyframes drift { to { background-position: 0 6px; } }

  .grain {
    position: absolute; inset: -50%;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/></filter><rect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/></svg>");
    opacity: 0.045; animation: grain 0.6s steps(4) infinite;
  }
  @keyframes grain {
    0%{transform:translate(0,0)}25%{transform:translate(-4%,3%)}
    50%{transform:translate(3%,-3%)}75%{transform:translate(-2%,-4%)}100%{transform:translate(0,0)}
  }

  .vig {
    position: absolute; inset: 0;
    background: radial-gradient(ellipse at center, transparent 52%, rgba(0,0,0,0.55) 100%);
  }

  /* Glitch pulse — RGB split + jitter */
  .fx.glitch { animation: shake var(--t-glitch) steps(3) 1; }
  .fx.glitch .scan { opacity: 0.85; }
  .fx.glitch::before, .fx.glitch::after {
    content: ''; position: absolute; inset: 0; mix-blend-mode: screen; opacity: 0.5;
  }
  .fx.glitch::before { background: rgba(225,6,0,0.35); transform: translateX(3px); }
  .fx.glitch::after { background: rgba(56,208,227,0.28); transform: translateX(-3px); }
  @keyframes shake {
    0%{transform:translate(0,0)}33%{transform:translate(-2px,1px)}
    66%{transform:translate(2px,-1px)}100%{transform:translate(0,0)}
  }
</style>
