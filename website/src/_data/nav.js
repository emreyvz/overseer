// Site navigation. `groups` drive the left sidebar (full IA); `header` is the condensed
// top-bar set. Add a page here and it appears everywhere automatically.
module.exports = {
  header: [
    { label: "Features", url: "/features/" },
    { label: "Models", url: "/models/" },
    { label: "Pipeline", url: "/pipeline/" },
    { label: "Architecture", url: "/architecture/" },
    { label: "Docs", url: "/docs/" },
    { label: "API", url: "/api/" },
  ],
  groups: [
    {
      title: "Overview",
      items: [
        { label: "Home", url: "/", key: "home" },
        { label: "Features", url: "/features/", key: "features" },
        { label: "Technology", url: "/technology/", key: "technology" },
      ],
    },
    {
      title: "Core Engine",
      items: [
        { label: "Models", url: "/models/", key: "models" },
        { label: "Pipeline", url: "/pipeline/", key: "pipeline" },
        { label: "Architecture", url: "/architecture/", key: "architecture" },
      ],
    },
    {
      title: "Develop",
      items: [
        { label: "Documentation", url: "/docs/", key: "docs" },
        { label: "API Reference", url: "/api/", key: "api" },
        { label: "Examples", url: "/examples/", key: "examples" },
      ],
    },
    {
      title: "Resources",
      items: [
        { label: "Gallery", url: "/gallery/", key: "gallery" },
        { label: "Roadmap", url: "/roadmap/", key: "roadmap" },
        { label: "FAQ", url: "/faq/", key: "faq" },
        { label: "Changelog", url: "/changelog/", key: "changelog" },
      ],
    },
  ],
};
