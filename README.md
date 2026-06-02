# Orchard — Project Page

Static project page for **Orchard: An Open-Source Agentic Modeling Framework**
([arXiv:2605.15040](https://arxiv.org/abs/2605.15040)).

Self-contained HTML/CSS/JS — no build step, no dependencies.

```
site/
├── index.html              # the whole page
├── .nojekyll               # let GitHub Pages serve the assets/ folder as-is
└── assets/
    ├── css/style.css
    ├── js/main.js          # BibTeX copy button + scroll reveal
    └── img/
        ├── favicon.svg
        └── orchard-overview.png   # teaser figure, shown above the abstract
```

> Note: the teaser image was renamed from `Orchard Overview.png` to
> `orchard-overview.png` — spaces in filenames break URLs on GitHub Pages.

## Preview locally

```bash
cd site
python3 -m http.server 8000
# open http://localhost:8000
```

## Deploy to GitHub Pages

- **Option A — `/docs` or root of `main`:** push this folder, then in
  *Settings → Pages* set the source branch/folder.
- **Option B — `gh-pages` branch:** copy the contents of `site/` to the root of a
  `gh-pages` branch and push.

The `.nojekyll` file is included so Pages serves the `assets/` directory verbatim.

## ⚠️ Verify before publishing

Two links are best-guesses extracted from the PDF and are marked with a
`data-verify` attribute in `index.html` — **confirm or replace them**:

| Link | Current value | Where |
|------|---------------|-------|
| Code | `https://github.com/microsoft/Orchard` | hero buttons + footer |
| Data | `https://huggingface.co/datasets/microsoft/Orchard` | hero buttons + footer |

Find them quickly: `grep -n data-verify index.html`.

The arXiv paper link is verified. Author names, the abstract, and all benchmark
numbers are transcribed from the published abstract; the BibTeX `author` field
reflects the paper's author order.

**Author links** point to each author's direct Google Scholar profile
(`…/citations?user=XXXX`), looked up individually. The common-name matches worth a
quick sanity-check are **Hao Cheng** (`d9s3sbQAAAAJ`), **Xiao Yu** (`QblBy88AAAAJ`),
and **Rui Yang** (`QHSUy3MAAAAJ`); disambiguated by affiliation/topic but please
confirm. They're the `<a>` tags inside the three `<ul class="authors">` lists in
`index.html`.
