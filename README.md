# confluence-publisher

A one-way publishing pipeline that syncs GitHub Markdown files to Confluence pages.

GitHub is the source of truth. Confluence is the generated presentation layer.

## Status

Production-ready. Install and use via the reusable GitHub Action or the CLI directly.

## Quick start

Add a `confluence-manifest.yaml` to your repo and copy one of the example workflows from
`examples/workflows/`. See [docs/ONBOARDING.md](docs/ONBOARDING.md) for a step-by-step guide.

```yaml
# confluence-manifest.yaml
defaults:
  space_id: ENG
  parent_id: '123456'
pages:
  docs/architecture.md:
    title: Architecture Overview
    page_id: '234567'
  docs/runbook.md:
    title: Operations Runbook   # no page_id — auto-created on first publish
```

```yaml
# .github/workflows/publish.yml
- uses: donolu/confluence-publisher@v1
  with:
    confluence-base-url: ${{ vars.CONFLUENCE_BASE_URL }}
    confluence-api-token: ${{ secrets.CONFLUENCE_API_TOKEN }}
    confluence-email: ${{ vars.CONFLUENCE_EMAIL }}
```

## What it does

- Triggered by GitHub Actions on push to `main` when files under `docs/**/*.md` change
- Converts Markdown to Confluence Storage Format using a custom `mistletoe` renderer
- Page identity is managed via a checked-in YAML manifest (page IDs, not titles)
- Auto-creates pages when `page_id` is absent; writes the new ID back via `[skip ci]` commit
- Uploads local images and Mermaid diagrams as page attachments
- Detects edit conflicts (Confluence version > last published version) and logs or fails
- Updates are versioned; the Git commit SHA is stored in the Confluence version message
- Missing images, Mermaid render failures, and upload errors all fail the build

## Supported Markdown

Headings, bold/italic/code, fenced code blocks (with syntax highlighting), tables,
ordered/unordered lists, blockquotes, horizontal rules, external and internal links,
local and external images, Mermaid diagrams (requires `mmdc` in CI).

Not supported: strikethrough, raw HTML (both raise `ConversionError` at check time).

## Documents

| Document | Purpose |
|---|---|
| [docs/ONBOARDING.md](docs/ONBOARDING.md) | Step-by-step onboarding guide for new repos |
| [docs/BRD.md](docs/BRD.md) | Business requirements, goals, and success metrics |
| [docs/TRD.md](docs/TRD.md) | Technical requirements, constraints, and API notes |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, component breakdown, conversion pipeline |
| [docs/DELIVERY_PLAN.md](docs/DELIVERY_PLAN.md) | Phased roadmap with scope per phase |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decision log and open questions |
| [docs/MANIFEST_SPEC.md](docs/MANIFEST_SPEC.md) | Page mapping manifest specification |

## Scope Boundary

This tool is not:
- A bidirectional sync
- A real-time mirror
- A general Markdown renderer for arbitrary Confluence content
