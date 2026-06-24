# Architecture

---

## System diagram

```
GitHub Repository
│
├── docs/**/*.md                 (source files — engineers write here)
├── confluence-manifest.yaml     (page identity + published state)
└── .github/workflows/
    └── publish-to-confluence.yml
              │
              │  on: push to main (paths filter) | workflow_dispatch | cron
              ▼
    GitHub Actions Runner
              │
              │  confluence-publisher check | sync | validate-manifest
              ▼
    ┌──────────────────────────────────────────────────────┐
    │                confluence-publisher CLI               │
    │                                                      │
    │  1. Load manifest                                    │
    │  2. Resolve changed files against manifest           │
    │  3. For each file:                                   │
    │     a. Parse Markdown to AST (mistletoe)             │
    │     b. Walk AST to Confluence Storage Format         │
    │     c. Pre-flight: verify all local images exist     │
    │     d. Hash rendered content                         │
    │     e. Skip if hash matches last_published_hash      │
    │     f. Check for manual edit conflict                │
    │     g. Upload images and Mermaid PNGs as attachments │
    │     h. Create or update page via REST API            │
    │     i. Write back page_id and hash to manifest       │
    │  4. Exit 0 (all succeeded) or 1 (any error)          │
    └──────────────────────────────────────────────────────┘
              │
              │  Confluence REST API (Cloud v2 or DC v1)
              ▼
    Confluence
    └── Target space
        └── Pages identified by page_id (never by title)
```

---

## Components

### 1. CLI (`confluence_publisher/cli.py`)

Entry point built with `click`. Three commands:

| Command | Description |
|---|---|
| `sync` | Main publish flow. Converts and publishes changed (or all) pages |
| `check` | Validates manifest and syntax without making any API calls |
| `validate-manifest` | Calls Confluence API to confirm all `page_id` values still exist |

Key flags: `--dry-run`, `--changed-files`, `--strict-conflicts`.

### 2. Manifest loader (`confluence_publisher/manifest.py`)

Reads and writes `confluence-manifest.yaml` at the repository root.

```python
@dataclass
class PageEntry:
    page_id: str | None
    space_id: str | None
    parent_id: str | None
    title: str
    last_published_hash: str | None      # written back after each successful publish
    last_published_version: int | None   # used for edit-conflict detection
    last_published_commit: str | None    # informational
```

The manifest is the only persistent state. After a successful publish the tool writes back
`last_published_hash`, `last_published_version`, and `last_published_commit`, then commits
the update with `[skip ci]` in the message.

### 3. Converter (`confluence_publisher/converter.py`)

Uses `mistletoe` with a custom `ConfluenceRenderer` that walks the AST and emits Confluence
Storage Format XML.

```
MarkdownDocument
  Heading          ->  <h1>, <h2>, ..., <h6>
  Paragraph        ->  <p>
  Bold / Italic    ->  <strong>, <em>
  InlineCode       ->  <code>
  CodeFence        ->  <ac:structured-macro ac:name="code">
                         <ac:parameter ac:name="language">python</ac:parameter>
                         <ac:plain-text-body><![CDATA[...]]></ac:plain-text-body>
                       </ac:structured-macro>
  Table            ->  <table><tbody><tr><th>/<td>...</td></tr></tbody></table>
  Image (local)    ->  <ac:image><ri:attachment ri:filename="..."/></ac:image>
  Image (remote)   ->  <ac:image><ri:url ri:value="..."/></ac:image>
  CodeFence mermaid ->  <ac:image><ri:attachment ri:filename="mermaid-{n}.png"/></ac:image>
  Link (external)  ->  <a href="...">
  Link (internal)  ->  <ac:link><ri:page ri:content-id="..."/></ac:link>
  UnknownNode      ->  raises ConversionError (build fails)
```

`convert()` returns a `ConversionResult`:
- `body`: the rendered Storage Format (used for content hash and deduplication)
- `full_body`: info banner prepended to `body` (what actually gets published)
- `images`: list of local image paths to upload as attachments
- `mermaid_blocks`: list of Mermaid source strings to render to PNG

XML safety:
- `_escape_attr()` escapes `&`, `<`, `>`, and `"` in attribute values
- `_escape_cdata()` splits `]]>` sequences so they cannot prematurely close CDATA sections

### 4. Confluence client (`confluence_publisher/confluence_client.py`)

Thin wrapper around `requests`. Supports both Cloud (REST API v2, Basic Auth) and Data Center
(REST API v1, Bearer PAT, optional mTLS client certificate). Mode is selected via
`CONFLUENCE_MODE` configuration.

All API calls go through `_request()`, which:
- Sets a 30-second timeout by default (callers can override, e.g. attachment uploads use 60s)
- Raises `RetryableError` on 429 and 5xx responses, triggering the `tenacity` retry decorator
  (exponential backoff, up to 5 attempts)

Attachment uploads route through `_request()` for the same retry coverage. `Content-Type: None`
in the per-request headers lets `requests` set the correct `multipart/form-data` boundary
without conflicting with the session's default `application/json`.

Public methods:
- `get_page(page_id)` -- fetch current version number and body
- `create_page(title, space_key, parent_id, body)` -- POST; returns new `page_id`
- `update_page(page_id, title, body, version, commit_sha)` -- versioned PUT
- `upload_attachment(page_id, filename, data, mime_type)` -- POST multipart
- `page_exists(page_id)` -- returns bool; used by `validate-manifest`

### 5. Publisher (`confluence_publisher/publisher.py`)

Orchestrates the per-page flow for a given list of changed files:

```
For each file:
  1. Look up manifest entry
  2. Convert Markdown to ConversionResult
  3. Pre-flight: verify local images exist on disk
  4. If new page (no page_id):
       a. Create with placeholder body (if attachments present) or full body
       b. Save page_id to manifest immediately
       c. Upload attachments
       d. If uploads succeed: update page with full body
       e. If uploads fail: leave placeholder; next run retries via update path
  5. If existing page:
       a. Compute content hash; skip if unchanged
       b. Check for edit conflict
       c. Upload attachments before updating body
       d. If uploads fail: skip body update (avoids broken references)
       e. Update page body
       f. Write back hash, version, commit SHA to manifest entry
```

Two top-level functions:
- `publish_pages()` -- runs the sync flow; returns a `PublishSummary`
- `check_pages()` -- validates conversion and image existence without API calls

### 6. GitHub Action (`action.yml`)

Composite action at the repository root. Consumers use `uses: hollowpipe/confluence-publisher@v1`.

Inputs: `operation`, `confluence-base-url`, `confluence-mode`, `confluence-email`,
`confluence-api-token`, `confluence-cert-pem`, `changed-files`, `dry-run`,
`strict-conflicts`, `install-mermaid`, `python-version`.

The action auto-installs `confluence-publisher` from the same ref it was called on
(`$GITHUB_ACTION_REF`), so pinning to `@v1.0.4` uses that exact version's code.

### 7. Workflow (`.github/workflows/publish-to-confluence.yml`)

Three jobs:

```
on:
  push:       branches: [main], paths: ["docs/**/*.md"]
  schedule:   cron: '0 7 * * 1-5'   (Mon-Fri 07:00 UTC)
  workflow_dispatch:  inputs: sync_all, validate_only

jobs:
  check:                                  (push and non-validate dispatch only)
    - checkout
    - install
    - confluence-publisher check

  publish:                                (runs after check passes)
    - checkout (fetch-depth: 2)
    - install
    - npm install -g @mermaid-js/mermaid-cli
    - resolve changed files (or all if sync_all=true)
    - confluence-publisher sync --changed-files f1 --changed-files f2 ...
    - commit manifest state   (if: always())

  validate:                               (schedule and validate_only dispatch only)
    - checkout
    - install
    - confluence-publisher validate-manifest
```

The "Commit manifest state" step runs with `if: always()` so `page_id` values written during
a partially-failed run are committed and available for the retry on the next push.

---

## Conversion: node support reference

| Markdown | Confluence Storage Format |
|---|---|
| `# Heading` | `<h1>` through `<h6>` |
| `**bold**` | `<strong>` |
| `_italic_` | `<em>` |
| `` `inline code` `` | `<code>` |
| `- item` / `1. item` | `<ul><li>` / `<ol><li>` |
| `> blockquote` | `<blockquote>` |
| `---` | `<hr/>` |
| ` ```python ` | `<ac:structured-macro ac:name="code">` |
| `\| table \|` | `<table>` |
| `[text](https://...)` | `<a href="...">` |
| `[text](other.md)` | `<ac:link><ri:page ri:content-id="..."/>` |
| `![alt](local.png)` | `<ac:image><ri:attachment ri:filename="local.png"/>` |
| `![alt](https://...)` | `<ac:image><ri:url ri:value="..."/>` |
| ` ```mermaid ` | `<ac:image><ri:attachment ri:filename="mermaid-{n}.png"/>` |

---

## File layout

```
confluence-publisher/
├── action.yml                        # reusable composite GitHub Action
├── confluence-manifest.yaml          # checked in; updated by the tool
├── pyproject.toml
├── confluence_publisher/
│   ├── __init__.py
│   ├── cli.py
│   ├── manifest.py
│   ├── converter.py
│   ├── confluence_client.py
│   └── publisher.py
├── tests/
│   ├── test_converter.py
│   ├── test_manifest.py
│   ├── test_publisher.py
│   ├── test_confluence_client.py
│   └── fixtures/
│       └── sample.md
├── examples/
│   └── workflows/
│       ├── publish.yml               # drop-in workflow template
│       └── pr-preview.yml            # PR dry-run preview with comment
├── docs/                             # project planning and reference
└── .github/
    └── workflows/
        └── publish-to-confluence.yml # this repository's own workflow
```

---

## Security

- The API token lives in GitHub Actions secrets only and is never logged or echoed.
- The generated info banner on every page makes provenance explicit to Confluence readers.
- The tool never reads Confluence content back into the build artefact beyond the version
  number and current page body (used only for hash comparison and conflict detection).
- No user-supplied content is interpolated into shell commands. All API calls use `requests`,
  not `subprocess`. The only subprocess call is `mmdc` for Mermaid rendering, which receives
  only local file paths as arguments.
