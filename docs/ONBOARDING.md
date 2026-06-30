# Onboarding a new repository

This guide walks through connecting a GitHub repository to Confluence so that Markdown files
under `docs/` are published automatically whenever they change on `main`.

The tool is a **one-way sync**: GitHub is the source of truth. Changes made directly in
Confluence will be overwritten on the next publish.

---

## Prerequisites

- A Confluence Cloud account with at least Space Admin access to the target space
- A GitHub repository containing Markdown documentation
- Permission to add repository secrets and variables in GitHub

---

## Step 1: Create a Confluence API token

1. Go to **Profile > Security > API tokens** in Atlassian account settings
2. Click **Create API token** and give it a label such as `github-publisher`
3. Copy the token value immediately -- you will not be able to see it again

For Confluence Data Center, generate a Personal Access Token from your profile page instead.

---

## Step 2: Configure GitHub secrets and variables

In your repository, go to **Settings > Secrets and variables > Actions**.

**Secrets** (encrypted; hidden from logs):

| Secret | Value |
|---|---|
| `CONFLUENCE_API_TOKEN` | The API token from Step 1 |
| `CONFLUENCE_CERT_PEM` | Base64-encoded PEM client certificate (DC with mTLS only) |

**Variables** (visible to workflow authors):

| Variable | Example | Notes |
|---|---|---|
| `CONFLUENCE_BASE_URL` | `https://your-org.atlassian.net` | No trailing slash |
| `CONFLUENCE_MODE` | `cloud` | Use `dc` for Data Center |
| `CONFLUENCE_EMAIL` | `your.name@example.com` | Cloud only; omit for DC |

---

## Step 3: Create the manifest

Add a `confluence-manifest.yaml` file at the root of your repository.

```yaml
version: 1

defaults:
  space_id: ENG              # Confluence space key
  parent_id: '123456'        # Page ID of the parent page in that space

pages:
  docs/architecture.md:
    title: Architecture Overview
    page_id: '234567'        # Existing Confluence page ID

  docs/runbook.md:
    title: Operations Runbook
    # No page_id: the page will be created automatically on first publish
```

**Finding a page ID:** Open the page in Confluence, click the three-dot menu (top-right),
then **Page information**. The page ID appears in the URL:
`…/pages/viewinfo.action?pageId=234567`

**Per-page overrides:**

```yaml
pages:
  docs/team/roadmap.md:
    title: Team Roadmap
    space_id: TEAM             # overrides the default space
    parent_id: '987654'        # overrides the default parent
```

---

## Step 4: Add the workflow files

Copy the example workflows into your `.github/workflows/` directory:

```
examples/workflows/publish.yml       ->  .github/workflows/publish-to-confluence.yml
examples/workflows/pr-preview.yml    ->  .github/workflows/confluence-pr-preview.yml
```

Both files reference `pipewell/confluence-publisher@v1`. No further code changes are needed
in your repository.

The workflow needs the following permissions so the action can write the manifest back after
creating new pages:

```yaml
permissions:
  contents: write
  pull-requests: write
```

---

## Step 5: First publish

Either push a change to any file listed in the manifest, or trigger the workflow manually:

1. Go to **Actions** in your repository
2. Select **Publish docs to Confluence**
3. Click **Run workflow** and tick **Sync all manifest entries**

The first run will create any pages where `page_id` is absent. The action then writes those
IDs back to `confluence-manifest.yaml` automatically. Subsequent runs use those IDs to update
the existing pages rather than creating new ones.

### How manifest write-back works

After publishing, the action commits the updated manifest directly to the branch using the
GitHub Contents API. This requires no extra configuration in most repositories.

If your repository has branch protection on `main` that requires pull requests, the direct
commit will be blocked. The action will then open a PR automatically. **Merge that PR
promptly** -- until it is merged, the next publish run will not have the new page IDs and
may attempt to re-create pages that already exist.

To avoid the PR fallback entirely, grant `github-actions[bot]` bypass permission on the
branch protection rule:

1. Go to **Settings > Branches** in your repository
2. Edit the protection rule for `main`
3. Under **Allow specified actors to bypass required pull requests**, add `github-actions[bot]`
4. Save

---

## Local testing

```bash
python -m venv venv
source venv/bin/activate
pip install pipewell-confluence-publisher
```

Copy `.env.example` to `.env`, fill in your credentials, then:

```bash
# Validate syntax without calling Confluence
confluence-publisher check

# Preview what would be published (no API calls)
confluence-publisher sync --dry-run

# Publish for real
confluence-publisher sync
```

---

## Supported Markdown features

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

## Conflict handling

If a Confluence page is manually edited after the last publish, the tool logs a warning and
overwrites with the GitHub content. GitHub is always the source of truth.

To make conflicts fail the build instead of just warning, pass `--strict-conflicts` to the
`sync` command or set `strict-conflicts: 'true'` on the action input. The page is still
overwritten; the non-zero exit code surfaces the conflict to the PR author.

---

## Failure and retry behaviour

All failures are hard errors -- the build exits non-zero. There are no silent failures.

If an attachment upload fails on a newly created page, the page stays live in Confluence with
a placeholder body. The `page_id` is committed to the manifest so the next push retries
the upload and body update without creating a duplicate page.

---

## Troubleshooting

**A pull request was opened for manifest write-back but the next publish failed**

The manifest PR has not been merged yet. The page IDs are not on `main`, so the action tried
to re-create pages that already exist in Confluence. Merge the manifest PR first, then re-run
the publish workflow. To prevent this in future, grant `github-actions[bot]` bypass permission
on the branch protection rule as described in Step 5.

**`page_id not found` on validate-manifest**

The Confluence page was deleted or moved. Either restore it in Confluence or remove the
`page_id` from the manifest entry so the page is recreated automatically on the next push.

**Conversion error: unsupported Markdown syntax**

The file contains syntax the converter does not support (e.g. `~~strikethrough~~` or raw HTML).
Rewrite the affected section using supported syntax and push again.

**Image not found on disk**

The path in the `![alt](path)` tag does not exist relative to the repository root. Verify the
path is correct and the file is committed.

**`mmdc` not found in CI**

The Mermaid CLI is only installed in the `publish` job. If you see this error on `check` runs,
confirm the `install-mermaid` action input is set to `'true'` for that job. If you see it on
the `publish` job, check the Install Mermaid CLI step in the workflow log.

**Credentials error (401 or 403)**

- Confirm `CONFLUENCE_BASE_URL` has no trailing slash
- Verify the API token has not expired
- For Cloud: ensure `CONFLUENCE_EMAIL` matches the Atlassian account that owns the token
- For DC: confirm the Personal Access Token has write access to the target space
