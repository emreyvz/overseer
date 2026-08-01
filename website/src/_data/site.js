// Global site metadata. Single source of truth for SEO, Open Graph, header and footer.
module.exports = {
  name: "Overseer",
  short: "Overseer",
  tagline: "AI-powered computer vision & spatial intelligence platform",
  description:
    "Overseer turns ordinary camera feeds into structured spatial intelligence: real-time " +
    "detection and tracking, monocular depth, 3D reconstruction, cross-camera re-identification " +
    "and event analytics, running on your own hardware, online or fully offline.",
  // Origin only (no trailing slash). The `| url` filter adds the /overseer/ path prefix,
  // so absolute URLs are built as `{{ site.url }}{{ page.url | url }}`.
  url: "https://emreyvz.github.io",
  repo: "https://github.com/emreyvz/overseer",
  repoShort: "emreyvz/overseer",
  edit: "https://github.com/emreyvz/overseer/edit/main/website/src",
  author: "Overseer contributors",
  lang: "en",
  locale: "en_US",
  ogImage: "/assets/img/og.svg",
  themeColor: "#ffffff",
  release: {
    version: "0.9.0",
    codename: "Spatial Foresight",
    date: "2026-07-31",
  },
  // Headline stats surfaced on the home page (placeholders, easy to update).
  stats: [
    { value: "13", label: "Pipeline stages" },
    { value: "8", label: "AI models" },
    { value: "22", label: "Platform features" },
    { value: "100%", label: "Runs offline" },
  ],
};
