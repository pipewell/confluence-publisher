# confluence-publisher

[![PyPI version](https://img.shields.io/pypi/v/pipewell-confluence-publisher.svg)](https://pypi.org/project/pipewell-confluence-publisher/)
[![Python versions](https://img.shields.io/pypi/pyversions/pipewell-confluence-publisher.svg)](https://pypi.org/project/pipewell-confluence-publisher/)
[![Licence: MIT](https://img.shields.io/badge/Licence-MIT-blue.svg)](LICENSE)

A one-way publishing pipeline that syncs GitHub Markdown files to Confluence pages.

**GitHub is the source of truth. Confluence is the generated presentation layer.**

---

## Quick start

1. Add a `confluence-manifest.yaml` to your repository root.
2. Copy an example workflow from `examples/workflows/` into `.github/workflows/`.
3. Add the three required secrets/variables to your repository settings.
4. Push or trigger manually — pages appear in Confluence within minutes.

See [docs/ONBOARDING.md](https://github.com/pipewell/confluence-publisher/blob/main/docs/ONBOARDING.md) for a full step-by-step guide.

```yaml
# confluence-manifest.yaml
defaults:
  space_id: ENG
  parent_id: '123456'

pages:
  docs/architecture.md:
    title: Architecture Overview
    page_id: '234567'         # existing Confluence page ID
  docs/runbook.md:
    title: Operations Runbook # no page_id — auto-created on first publish
```

```yaml
# .github/workflows/publish-to-confluence.yml (minimal)
- uses: pipewell/confluence-publisher@v1
  with:
    confluence-base-url: ${{ vars.CONFLUENCE_BASE_URL }}
    confluence-api-token: ${{ secrets.CONFLUENCE_API_TOKEN }}
    confluence-email: ${{ vars.CONFLUENCE_EMAIL }}
```

Using a fine-grained/scoped Atlassian API token? See [docs/ONBOARDING.md](https://github.com/pipewell/confluence-publisher/blob/main/docs/ONBOARDING.md#fine-grained-scoped-api-tokens) — these are rejected on the direct tenant domain and need `confluence-cloud-id` set instead.

---

## What it does

- Triggers on push to `main` when files under `docs/**/*.md` change
- Converts Markdown to Confluence Storage Format using a custom `mistletoe` renderer
- Manages page identity via a checked-in YAML manifest (page IDs, not titles)
- Auto-creates pages when `page_id` is absent; writes the new ID back via a `[skip ci]` commit
- Uploads local images and Mermaid diagrams as page attachments
- Detects edit conflicts (Confluence version ahead of last published) and warns or fails
- Stores the Git commit SHA in the Confluence version message for traceability
- Adds an auto-generated banner to each page (source file, commit SHA, tool attribution) -- configurable via `defaults.attribution` in the manifest or `--no-attribution`
- Best-effort preserves inline comment anchors across republish, when the commented text is unchanged (see "Known limitations")
- Treats missing images, Mermaid render failures, and upload errors as hard build failures

---

## Failure semantics

All failures exit non-zero. There are no silent failures.

| Failure | Behaviour |
|---|---|
| Markdown conversion error | Build fails; page not published |
| Local image not found on disk | Build fails; page not published (pre-flight check) |
| `mmdc` not installed with Mermaid diagrams present | Build fails; page not published |
| Mermaid render failure | Build fails; page not published |
| Confluence API error on create or update | Build fails; manifest unchanged |
| Attachment upload failure on a new page | Build fails; placeholder body stays in Confluence; `page_id` written to manifest; next push retries via the update path (no duplicate page created) |
| Attachment upload failure on an existing page | Build fails; page body not updated; manifest hash unchanged; next push retries |
| Manual edit conflict | Warning logged; page overwritten with GitHub content. `--strict-conflicts` makes this a build error while still overwriting |

The "Commit manifest state" step uses `if: always()` so `page_id` values are committed
to the repo even when the publish step exits non-zero, preserving the retry guarantee
across CI runs.

---

## Supported Markdown

| Feature | Support |
|---|---|
| Headings H1-H6 | Full |
| Bold, italic, inline code | Full |
| Fenced code blocks with language label | Full |
| Tables | Full |
| Ordered and unordered lists (including nested) | Full |
| Blockquotes | Full |
| Horizontal rules | Full |
| External links | Full |
| Internal links between managed `.md` files | Resolved to Confluence page links |
| Local images | Uploaded as page attachments |
| External images | Rendered inline |
| Mermaid diagrams | Rendered to PNG via `mmdc` (requires Node in CI) |
| Strikethrough | Not supported -- raises a conversion error |
| Raw HTML | Not supported -- raises a conversion error |

---

## Scope

This tool is intentionally narrow:

- **One-way only.** It does not sync Confluence edits back to GitHub.
- **Not a real-time mirror.** It runs on push and on a nightly validation schedule.
- **Not a general-purpose renderer.** It targets the Confluence Storage Format features needed for engineering documentation.

---

## Known limitations

- **Inline comments can lose their anchor on republish, depending on what changed.** Confluence anchors an inline comment by embedding markup directly in the page body around the highlighted text, and every publish replaces the full page body with freshly-converted Markdown. Since v1.3.0, the tool re-anchors a comment automatically **if the exact commented text still appears, unchanged and unambiguous, in the new body** -- confirmed working via live testing against real Confluence pages. If the commented text was reworded, moved into a context that makes it ambiguous (appears more than once), or the comment was on formatted text (e.g. specifically on bold/italic, not the whole line), the anchor still doesn't survive -- the comment thread is typically detached rather than deleted, but no longer points at any specific text. Footer/page-level comments are unaffected either way, since they're stored separately from the body and never touched by this tool. Background in [#11](https://github.com/pipewell/confluence-publisher/issues/11).

---

## Documents

| Document | Purpose |
|---|---|
| [ONBOARDING](https://github.com/pipewell/confluence-publisher/blob/main/docs/ONBOARDING.md) | Step-by-step guide for connecting a new repository |
| [MANIFEST_SPEC](https://github.com/pipewell/confluence-publisher/blob/main/docs/MANIFEST_SPEC.md) | Full manifest format reference |
| [ARCHITECTURE](https://github.com/pipewell/confluence-publisher/blob/main/docs/ARCHITECTURE.md) | System design and component breakdown |
| [DECISIONS](https://github.com/pipewell/confluence-publisher/blob/main/docs/DECISIONS.md) | Decision log with rationale |
| [BRD](https://github.com/pipewell/confluence-publisher/blob/main/docs/BRD.md) | Business requirements and success metrics |
| [TRD](https://github.com/pipewell/confluence-publisher/blob/main/docs/TRD.md) | Technical requirements and API reference |
| [DELIVERY_PLAN](https://github.com/pipewell/confluence-publisher/blob/main/docs/DELIVERY_PLAN.md) | Phased delivery history |
