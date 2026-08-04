---
title: Contributing
order: 12
intro: "Overseer is developed in the open. Bug reports, features and documentation are all welcome."
---

## Report a bug

Open an issue on [GitHub]({{ site.repo }}) with:

- what you expected and what happened,
- steps to reproduce,
- your OS, GPU and Python / Node versions.

## Propose a feature

Open an issue describing the use case. Direction is shaped by real needs, so context helps.

## Contribute code

1. Fork and branch off `main`.
2. Keep changes focused; match the surrounding style.
3. Run the frontend type-check (`npm run check` in `web/`) before opening a PR.
4. Open a pull request describing the change and how you verified it.

## Improve the docs

This site is Markdown + [Eleventy](https://www.11ty.dev/). To add a page:

```text
website/src/docs/your-page.md
---
title: Your Page
order: 12
---
Your content in Markdown.
```

It appears in the sidebar automatically, ordered by `order`. Build locally with `npm run dev` in `website/`.

<div class="callout"><div class="c-title">Thank you</div><p>Every issue, fix and doc improvement makes the platform better.</p></div>
