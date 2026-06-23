# Technical Requirements Document

---

## System overview

A GitHub Action triggers a Python CLI on push to `main` when files matching `docs/**/*.md`
change. The CLI converts changed Markdown files into Confluence Storage Format and creates or
updates the corresponding Confluence page via the REST API.

---

## Functional requirements

### FR-01: Trigger

- The GitHub Action triggers on `push` to the default branch with `paths: ["docs/**/*.md"]`.
- Only files that changed in the push are processed (not all managed files).
- Full re-sync is triggerable manually via `workflow_dispatch` with `sync_all: true`.
- A nightly `validate-manifest` run executes Monday to Friday at 07:00 UTC via cron schedule.

### FR-02: Page mapping

- Page identity is determined by a checked-in YAML manifest (`confluence-manifest.yaml`).
- Each entry maps a file path to a Confluence `page_id`, `space_id`, `parent_id`, and `title`.
- Page IDs are used as identifiers, not titles. Title lookups are not permitted as they break on renames.
- A file not in the manifest is silently ignored. A file in the manifest that does not exist on
  disk is a hard error caught by `--check`.

### FR-03: Conversion

- Markdown is parsed into an AST using `mistletoe`.
- A custom `ConfluenceRenderer` converts the AST to Confluence Storage Format (XHTML-based).
- Supported syntax: headings (H1-H6), paragraphs, bold, italic, inline code, ordered and
  unordered lists (including nested), fenced code blocks with language labels, blockquotes,
  horizontal rules, tables, external and internal hyperlinks, local images, Mermaid diagrams.
- The build fails on any unsupported syntax node encountered during conversion. No silent
  fallbacks. Unsupported nodes raise a `ConversionError` with the source file and node type.

### FR-04: Publishing

- Before updating, retrieve the current page version from the Confluence API.
- Submit `version.number + 1` in the update payload.
- Store the Git commit SHA in the `version.message` field.
- Prepend a machine-readable info banner to every published page:

  ```xml
  <ac:structured-macro ac:name="info">
    <ac:rich-text-body>
      <p>This page is auto-generated from GitHub. Manual edits will be overwritten on next publish.<br/>
      Source: <code>docs/architecture.md</code> @ <code>a1b2c3d</code></p>
    </ac:rich-text-body>
  </ac:structured-macro>
  ```

### FR-05: Change detection

- Compute a SHA-256 hash of the rendered Confluence Storage Format content body before publishing.
- Compare against `last_published_hash` stored in the manifest.
- Skip the API update if hashes match (content unchanged). Log skipped pages.
- The hash is computed from the content body only, excluding the info banner, so banner-only
  changes do not trigger spurious re-publishes.

### FR-06: Edit conflict detection

- On each update, compare the page's current Confluence version with `last_published_version`
  from the manifest.
- If the Confluence page has been edited since the last publish (version higher than expected),
  log a warning and proceed with overwrite (GitHub is always the source of truth).
- `--strict-conflicts` promotes this to a build failure. The page is still overwritten; the
  non-zero exit code surfaces the conflict so the team can investigate.

### FR-07: Operational modes

| Mode | Flag | Behaviour |
|---|---|---|
| Dry run | `--dry-run` | Converts and logs all changes; no API calls |
| Check | `--check` | Validates manifest and syntax; fails on errors; no publish |
| Validate manifest | `--validate-manifest` | Calls Confluence API to confirm all page IDs exist |
| Sync (default) | _(none)_ | Full convert and publish |

### FR-08: Images

- Local image files referenced in Markdown (`![alt](path/to/image.png)`) are uploaded as
  Confluence page attachments before the page body is submitted.
- A pre-flight check verifies all local images exist on disk before any API call is made.
  Missing images are a hard build error.
- The rendered Storage Format references attachments by filename, not the original path.
- Remote image URLs are passed through unchanged.

### FR-09: Mermaid diagrams

- Fenced code blocks with language label `mermaid` are rendered to PNG using the `mmdc` CLI.
- The PNG is uploaded as a page attachment and referenced inline via an attachment macro.
- If `mmdc` is not installed, or if rendering fails, the build errors rather than producing
  a page with broken diagram references.
- `MMDC_PUPPETEER_CFG` environment variable passes `--puppeteerConfigFile` to `mmdc`,
  used on Linux CI runners to set `--no-sandbox`.

### FR-10: Internal links

- Links between managed Markdown files (`[text](../other.md)`) are rewritten to
  `<ac:link><ri:page ri:content-id="..."/>` using the manifest's `page_id` map.
- Links to files not in the manifest are passed through as plain hyperlinks with a warning.

### FR-11: Auto-create with attachments (two-step flow)

When a new page has images or Mermaid blocks:

1. `create_page()` is called with a safe placeholder body containing no attachment macros.
   The `page_id` is written to the manifest immediately after creation.
2. Attachments are uploaded.
3. If all uploads succeed, `update_page(version=2)` replaces the placeholder with the real body.
4. If any upload fails, the placeholder remains and the build exits non-zero. The `page_id`
   is already in the manifest so the next push goes through the update path without re-creating
   the page.

---

## Non-functional requirements

### NFR-01: Performance

- Full sync of up to 200 pages must complete within 15 minutes.
- Incremental sync (changed files only) must complete within 5 minutes for up to 20 changed files.

### NFR-02: Rate limiting and retry

- All Confluence API calls implement exponential backoff with jitter on 429 and 5xx responses
  via `tenacity`. Maximum retry attempts: 5. After 5 failures the page is skipped and logged
  as an error; the build continues for remaining pages and exits non-zero.
- Attachment uploads go through the same `_request()` retry path as all other API calls.

### NFR-03: Serialisation

- Page updates are serialised (not parallelised) to avoid version number conflicts.
- Concurrency limit is configurable; default is 3 concurrent in-flight requests.

### NFR-04: Secrets management

- `CONFLUENCE_API_TOKEN`: GitHub Actions secret. Bearer PAT on DC; API token on Cloud.
- `CONFLUENCE_CERT_PEM`: GitHub Actions secret (DC only). Base64-encoded PEM client certificate.
- `CONFLUENCE_EMAIL`: repository variable (Cloud only).
- `CONFLUENCE_BASE_URL` and `CONFLUENCE_MODE` (`dc` or `cloud`): repository variables.
- No credentials are logged or embedded in any output.
- On DC, the PEM content is decoded and written to a path produced by `tempfile.mkstemp(suffix=".pem")`
  at startup. The path is dynamic to avoid collisions on shared runners. The file descriptor is
  closed immediately after writing and the path is deleted on process exit via
  `atexit.register(os.unlink, pem_path)`.

### NFR-05: Portability

- The tool is configurable per-repository via a single manifest file (`confluence-manifest.yaml`).
- No hardcoded space IDs, parent page IDs, or organisation-specific values.

### NFR-06: Python version

- Python 3.10+. This is a standalone tool; it is not constrained by any consumer project's runtime.

---

## External dependencies

| Dependency | Version | Purpose |
|---|---|---|
| `mistletoe` | `>=1.3` | Markdown AST parsing and custom rendering |
| `requests` | `>=2.31` | Confluence REST API calls |
| `PyYAML` | `>=6.0` | Manifest and config file parsing |
| `click` | `>=8.1` | CLI interface |
| `tenacity` | `>=8.0` | Exponential backoff and retry on 429/5xx |
| `@mermaid-js/mermaid-cli` (`mmdc`) | latest | Mermaid to PNG rendering (Node dependency, installed in CI) |

---

## Confluence API reference

The client supports both Confluence Data Center and Cloud. The two differ in API path and
authentication scheme; the Storage Format body representation is identical in both.

### Data Center

| Concern | Detail |
|---|---|
| Base path | `/rest/api/content/` |
| Get page | `GET /rest/api/content/{id}?expand=version,body.storage` |
| Update page | `PUT /rest/api/content/{id}` |
| Create page | `POST /rest/api/content/` |
| Attachments | `POST /rest/api/content/{id}/child/attachment` |
| Authentication | `Authorization: Bearer <PAT>` |
| TLS | Optional client certificate (PEM) for mTLS on restricted networks |
| Space identifier | Space key (string, e.g. `"ENG"`) |
| Version field | `version.number` (integer; submit current + 1 on update) |

### Cloud

| Concern | Detail |
|---|---|
| Base path | `/wiki/api/v2/` |
| Get page | `GET /wiki/api/v2/pages/{id}` |
| Update page | `PUT /wiki/api/v2/pages/{id}` |
| Create page | `POST /wiki/api/v2/pages/` |
| Attachments | `POST /wiki/api/v2/pages/{id}/attachments` |
| Authentication | Basic Auth: `{email}:{api_token}` Base64-encoded |
| TLS | Standard HTTPS; no client certificate |
| Space identifier | Space ID (numeric string or UUID) |
| Version field | `version.number` (same semantics as DC) |

### Abstraction

`ConfluenceClient` accepts a `mode: "dc" | "cloud"` parameter from configuration. Auth setup
and API path construction branch on this value. All publisher logic above the client layer is
mode-agnostic. Switching from DC to Cloud requires only a configuration change.

---

## Out of scope

- OAuth 2.0 or Forge authentication
- Automated page deletion when files are removed from the manifest
- Confluence comment preservation
- Macro support beyond what is expressible in Storage Format
