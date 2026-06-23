# Manifest specification

---

## Purpose

The manifest (`confluence-manifest.yaml`) is the authoritative mapping between GitHub Markdown
file paths and Confluence pages. It is checked into the repository and is the only source of
page identity.

Page IDs, not titles, are used as stable identifiers. Titles can change; page IDs cannot.

---

## Format

```yaml
version: 1

defaults:
  space_id: "ENG"            # Confluence space key (DC) or space ID (Cloud)
  parent_id: "123456"        # Default parent page for top-level docs

pages:
  docs/architecture.md:
    page_id: "789012"
    title: Architecture Overview
    # space_id and parent_id inherited from defaults

  docs/runbooks/incident-response.md:
    page_id: "789013"
    parent_id: "789012"      # Override: nested under architecture page
    title: Incident Response Runbook

  docs/runbooks/deployment.md:
    page_id: "789014"
    parent_id: "789012"
    title: Deployment Runbook

  docs/new-feature.md:
    title: New Feature Design
    # No page_id: triggers auto-creation on first publish.
    # The assigned ID is written back here automatically.
```

---

## Fields

### Top-level

| Field | Required | Description |
|---|---|---|
| `version` | Yes | Schema version. Currently `1`. |
| `defaults.space_id` | Yes | Confluence space ID applied to all pages unless overridden. |
| `defaults.parent_id` | No | Default parent page ID. Applied where `parent_id` is not set. |

### Per-page

| Field | Required | Description |
|---|---|---|
| `title` | Yes | Confluence page title. Used on create and update. Renaming here renames the page. |
| `page_id` | No | Confluence page ID. If absent, the page is created automatically on first publish and the ID is written back. |
| `space_id` | No | Overrides `defaults.space_id` for this page. |
| `parent_id` | No | Overrides `defaults.parent_id`. Determines page hierarchy. |

### Written back by the tool

Do not edit these fields manually.

| Field | Description |
|---|---|
| `page_id` | Written back after auto-creation. Survives page renames in Confluence. |
| `last_published_hash` | SHA-256 of the last published Storage Format content body. Used for skip-if-unchanged. |
| `last_published_version` | Confluence version number at last publish. Used for edit-conflict detection. |
| `last_published_commit` | Git commit SHA at last publish. Informational only. |

---

## Rules

1. A file not in the manifest is ignored silently. The tool does not publish it and does not error.
2. A file listed in the manifest that does not exist on disk is a hard error (`--check` catches this).
3. A `page_id` that does not exist in Confluence is caught by `validate-manifest`.
4. The `title` field controls the Confluence page title on every publish. Renaming it here renames the Confluence page.
5. Do not assign the same `page_id` to two different files. The tool detects and rejects duplicate IDs at startup.

---

## Auto-create and attachment safety

When a page with no `page_id` contains images or Mermaid diagrams, the publisher uses a
two-step flow to avoid pages with broken attachment references:

1. Create the page with a safe placeholder body.
2. Write `page_id` to the manifest immediately (so a subsequent failure retries via update,
   not re-creation).
3. Upload attachments.
4. If all uploads succeed: update the page with the real body.
5. If any upload fails: leave the placeholder. The next push retries from step 3 onwards.

---

## Example

```yaml
version: 1

defaults:
  space_id: "ENG"
  parent_id: "100000"

pages:
  docs/architecture.md:
    page_id: "100001"
    title: Architecture

  docs/etl-pipeline.md:
    page_id: "100002"
    title: ETL Pipeline Overview

  docs/runbooks/redshift-load.md:
    page_id: "100003"
    parent_id: "100002"
    title: Redshift Load Runbook

  docs/new-design.md:
    title: New Design          # page_id written here after first publish
```
