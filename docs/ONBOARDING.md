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
| `CONFLUENCE_CLOUD_ID` | `d14306f1-5802-4283-834c-8a799a89321a` | Cloud only; see "Fine-grained/scoped API tokens" below. Leave unset unless your token needs it |

### Fine-grained/scoped API tokens

Some Atlassian orgs issue fine-grained (scoped) API tokens instead of classic
unrestricted ones -- or block classic tokens entirely via an org-wide policy. Scoped
tokens are rejected with a `401 Unauthorized` when called against your site's direct
domain (`https://your-org.atlassian.net/...`); they only work routed through
Atlassian's API gateway by tenant ID instead of domain name.

If publishing fails with a 401 despite correct credentials and space permissions, set
`CONFLUENCE_CLOUD_ID` to your site's cloud ID:

```bash
curl -s https://your-org.atlassian.net/_edge/tenant_info
```

This is an unauthenticated endpoint; the response is `{"cloudId": "..."}`. Once set,
all Cloud API calls route through `https://api.atlassian.com/ex/confluence/{cloudId}/...`
instead of the direct domain.

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
GitHub Contents API. This requires the workflow's token to have write access -- if your
repository (or org) defaults GITHUB_TOKEN to read-only, add a `permissions:` block to the
job:

```yaml
jobs:
  publish:
    permissions:
      contents: write
      pull-requests: write
```

Without this, both the direct commit and the PR fallback below fail with
`Write access to repository not granted`.

If your repository has branch protection on `main` that requires pull requests, the direct
commit will be blocked even with write access. The action will then open a PR automatically,
on a branch named `manifest-writeback-<timestamp>` by default. **Merge that PR
promptly** -- until it is merged, the next publish run will not have the new page IDs and
may attempt to re-create pages that already exist.

If your org enforces a branch-naming ruleset, that fallback branch name may itself be
rejected (`Branch name must match a given regex pattern`). Set `writeback-branch-prefix` to
something that satisfies your pattern, e.g.:

```yaml
    with:
      writeback-branch-prefix: 'chore/no-ticket-manifest-writeback-'
```

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

## Attribution banner

Every published page gets a small auto-generated info banner noting it's managed from GitHub,
the source file, and the commit SHA -- plus a "Published with confluence-publisher" line
crediting the tool, by default.

To omit that last line, set `attribution: false` under `defaults` in the manifest, or force it
off per-run with `--no-attribution` on the `sync` command / `no-attribution: 'true'` on the
action input (the CLI flag always wins toward off, regardless of the manifest setting).

---

## Inline comment preservation

Confluence anchors an inline comment to specific text by embedding a marker directly in the
page body. Since every publish replaces the full body, this used to orphan every inline
comment unconditionally (still true if you're on an older version).

As of v1.3.0, a comment survives a republish automatically **if the exact commented text is
still present, unchanged, and appears exactly once** in the newly-published content. No
configuration needed -- this is always on, since it can only help (worst case is today's
behaviour: the thread survives in Confluence but is detached from the text).

It won't survive if:
- the commented text was reworded or removed
- the same text now appears more than once on the page (ambiguous which occurrence to anchor)
- the comment was on formatted text specifically (e.g. just the bold word in a sentence, not
  the whole line) rather than plain text

Footer/page-level comments are unaffected either way -- they're stored separately from the
body and this tool never touches them.

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

**`Write access to repository not granted` in the "Write back manifest" step**

The workflow's token has read-only access. Add `permissions: contents: write` (and
`pull-requests: write` for the PR fallback) to the job -- see "How manifest write-back works"
above.

**`Branch name must match a given regex pattern` in the "Write back manifest" step**

Your org enforces a branch-naming ruleset that the default `manifest-writeback-<timestamp>`
fallback branch name doesn't satisfy. Set `writeback-branch-prefix` to a prefix that matches
your pattern -- see "How manifest write-back works" above. This failure only blocks the
write-back step; the actual Confluence publish already succeeded, so no content is lost --
but the manifest may need `page_id` values recorded manually for any newly created pages
until write-back succeeds.

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
- For Cloud: if the token is fine-grained/scoped, set `CONFLUENCE_CLOUD_ID` (see "Fine-grained/scoped API tokens" above) -- scoped tokens 401 on the direct domain regardless of permissions
- For DC: confirm the Personal Access Token has write access to the target space
