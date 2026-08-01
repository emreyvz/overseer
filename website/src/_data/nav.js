// Site navigation. `groups` drive the left sidebar; `header` is the condensed top-bar set.
// Models, Pipeline and the API reference live inside Architecture; add a page here to surface it.
module.exports = {
  header: [
    { label: "Features", url: "/features/" },
    { label: "Technology", url: "/technology/" },
    { label: "Architecture", url: "/architecture/" },
    { label: "Docs", url: "/docs/" },
    { label: "Examples", url: "/examples/" },
  ],
  groups: [
    {
      title: "Overview",
      items: [
        { label: "Home", url: "/", key: "home" },
        { label: "Features", url: "/features/", key: "features" },
        { label: "Technology", url: "/technology/", key: "technology" },
        { label: "Architecture", url: "/architecture/", key: "architecture" },
      ],
    },
    {
      title: "Develop",
      items: [
        { label: "Documentation", url: "/docs/", key: "docs" },
        { label: "Examples", url: "/examples/", key: "examples" },
      ],
    },
    {
      title: "Resources",
      items: [
        { label: "Gallery", url: "/gallery/", key: "gallery" },
        { label: "FAQ", url: "/faq/", key: "faq" },
        { label: "Changelog", url: "/changelog/", key: "changelog" },
      ],
    },
  ],
};
