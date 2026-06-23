# Delivery plan

## Approach

Four phases, each producing a working and usable tool. No phase leaves the tool in a broken
intermediate state. All four phases are now shipped.

---

## Phase 1: Core text publishing -- Shipped

**Goal:** Publish pre-created Confluence pages from text-heavy Markdown. Engineers can merge
docs and see them appear in Confluence within minutes.

**Scope:**
- GitHub Action trigger on push to `main` with `paths: ["docs/**/*.md"]`
- `--dry-run`, `--check`, and `--validate-manifest` modes
- Manifest-based page mapping (page IDs required in Phase 1; auto-creation added in Phase 2)
- Conversion: headings, paragraphs, bold, italic, inline code, ordered/unordered lists,
  fenced code blocks with language labels, blockquotes, external hyperlinks
- Info macro banner on every published page
- Git commit SHA stored in Confluence version message
- Content hash check (skip if unchanged)
- Edit conflict detection (warn; do not block)
- Exponential backoff on 429 and 5xx responses via `tenacity`
- Exit non-zero on any conversion or API error
- `--check` mode runs in PR via a separate workflow step

---

## Phase 2: Images, internal links, and page creation -- Shipped

**Goal:** Handle the most common doc patterns that Phase 1 rejects.

**Scope:**
- Local image upload as Confluence attachment before page update
- Pre-flight image existence check (hard error before any API call)
- Internal Markdown links rewritten to Confluence page links using the manifest
- Links to non-manifest files passed through as plain hyperlinks with a warning
- Automated page creation for manifest entries with no existing `page_id`
- `page_id` written back to the manifest via `[skip ci]` commit after first publish
- Two-step auto-create flow for pages with attachments: placeholder body on create,
  upload attachments, then update with real body; `page_id` saved immediately so
  a failed upload retry goes through the update path rather than re-creating the page

---

## Phase 3: Tables, Mermaid, and conflict hardening -- Shipped

**Goal:** Support the remaining common Markdown features; tighten safety around overwrites.

**Scope:**
- Table conversion to `<table>` in Storage Format
- Mermaid fenced blocks rendered to PNG via `mmdc`, uploaded as page attachment
- `MMDC_PUPPETEER_CFG` environment variable for Linux CI sandbox configuration
- Edit conflict detection promoted from warning to configurable build failure via
  `--strict-conflicts` (page is still overwritten; exit code signals the conflict)
- `validate-manifest` run on a nightly schedule (Monday to Friday, 07:00 UTC)

---

## Phase 4: Reusable organisation-level action -- Shipped

**Goal:** Package the tool so other teams can adopt it with minimal configuration.

**Scope:**
- Composite GitHub Action (`action.yml`) at the repository root
- Consumers use `uses: donolu/confluence-publisher@v1`; no code changes needed to adopt
- Per-repo configuration via `confluence-manifest.yaml` only
- Example workflows in `examples/workflows/`: full publish workflow and PR dry-run preview
- `docs/ONBOARDING.md` covers prerequisites, manifest setup, secret configuration,
  first publish, local testing, and troubleshooting

---

## Timeline summary

| Phase | Scope | Status |
|---|---|---|
| 1 | Core text publishing | Shipped |
| 2 | Images, links, page creation | Shipped |
| 3 | Tables, Mermaid, conflict hardening | Shipped |
| 4 | Reusable org-level action | Shipped |

---

## Pilot acceptance criteria

The following criteria were set for the SearchAudit pilot before broader rollout:

- [x] All `.md` files under `docs/` are listed in the manifest
- [x] A push to `main` that changes a doc triggers the Action and updates Confluence within 10 minutes
- [x] A PR that introduces unsupported Markdown syntax fails the `--check` step
- [x] Manually editing a published Confluence page triggers a warning on the next publish
- [x] Running `--dry-run` against the full manifest produces no errors
- [x] `validate-manifest` runs nightly and confirms all page IDs are reachable
