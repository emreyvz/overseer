// Eleventy config for the Overseer documentation site.
// Static output only (GitHub Pages friendly). Nunjucks templating, data-driven pages,
// zero client-side framework. New content = drop a data entry or a Markdown file in.
const pkg = require("./package.json");

module.exports = function (eleventyConfig) {
  // --- passthrough (copied verbatim into _site) --------------------------------
  eleventyConfig.addPassthroughCopy({ "src/assets": "assets" });
  eleventyConfig.addPassthroughCopy({ "src/static": "." }); // robots.txt, .nojekyll, etc.

  // --- watch targets ------------------------------------------------------------
  eleventyConfig.addWatchTarget("src/assets/css/");
  eleventyConfig.addWatchTarget("src/assets/js/");

  // --- filters ------------------------------------------------------------------
  // Absolute URL for sitemap / Open Graph (site.url already has no trailing slash).
  eleventyConfig.addFilter("absoluteUrl", (path, base) => {
    try { return new URL(path, base).toString(); }
    catch { return (base || "") + (path || ""); }
  });
  eleventyConfig.addFilter("isoDate", (d) =>
    d ? new Date(d).toISOString() : new Date(0).toISOString());
  eleventyConfig.addFilter("readableDate", (d) =>
    d ? new Date(d).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) : "");
  eleventyConfig.addFilter("slug", (s) =>
    String(s).toLowerCase().replace(/[^\w]+/g, "-").replace(/^-+|-+$/g, ""));
  // Turn a heading string into an id (for anchor links / TOC).
  eleventyConfig.addFilter("anchor", (s) =>
    String(s).toLowerCase().replace(/[^\w]+/g, "-").replace(/^-+|-+$/g, ""));
  eleventyConfig.addFilter("startsWith", (s, p) => String(s).startsWith(p));
  eleventyConfig.addFilter("find", (arr, key, val) =>
    (arr || []).find((x) => x[key] === val));
  eleventyConfig.addFilter("where", (arr, key, val) =>
    (arr || []).filter((x) => x[key] === val));

  // --- shortcodes ---------------------------------------------------------------
  eleventyConfig.addShortcode("year", () => `${new Date().getFullYear()}`);

  // --- collections --------------------------------------------------------------
  // Docs pages ordered by front-matter `order`.
  eleventyConfig.addCollection("docs", (api) =>
    api.getFilteredByTag("docs").sort((a, b) => (a.data.order || 0) - (b.data.order || 0)));

  return {
    dir: {
      input: "src",
      output: "_site",
      includes: "_includes",
      data: "_data",
    },
    // GitHub Pages project site is served under /overseer/. All internal links use the
    // `| url` filter so they pick this up automatically; a custom domain can set it to "/".
    pathPrefix: "/overseer/",
    templateFormats: ["njk", "md", "html", "11ty.js"],
    markdownTemplateEngine: "njk",
    htmlTemplateEngine: "njk",
  };
};
