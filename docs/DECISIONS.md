# Decision log

Decisions made are recorded with their rationale. Open questions are flagged below.

---

## D-01: One-way publishing only

**Decision:** GitHub is the source of truth. Confluence is read-only for managed pages.

**Rationale:** Bidirectional sync requires conflict resolution, which is complex and fragile.
The primary value is making GitHub docs accessible to non-engineers, not enabling
Confluence-first editing.

---

## D-02: Page IDs over title matching

**Decision:** The manifest uses Confluence `page_id` as the stable identifier, not page titles.

**Rationale:** Title-based lookup breaks when pages are renamed. Page IDs are permanent for
the lifetime of the page. This is the key insight that makes the manifest approach reliable.

---

## D-03: Fail on unsupported syntax, not silent fallback

**Decision:** Any Markdown node the converter does not support raises a `ConversionError`
and fails the build.

**Rationale:** Silent degradation produces broken Confluence pages that look superficially
correct. Engineers discover the problem when stakeholders read the doc, not in CI. A build
failure in PR is much cheaper to deal with.

---

## D-04: `mistletoe` as the Markdown parser

**Decision:** Use `mistletoe` for AST-based Markdown parsing and custom rendering.

**Rationale:** `mistletoe` is designed to accept custom renderers, which is exactly what
conversion to Confluence Storage Format requires. Alternatives (`markdown2`, `python-markdown`)
are string-output-oriented and make AST walking awkward. CommonMark spec compliance is a bonus.

---

## D-05: Confluence Storage Format, not ADF

**Decision:** Target Confluence Storage Format (XHTML-based) rather than Atlassian Document
Format (ADF).

**Rationale:** The REST API `page` endpoint accepts Storage Format directly via `body.storage`.
ADF is the newer format used by the Confluence editor, but requires a different body
representation and the conversion tooling is less mature. Storage Format is stable and
well-documented across both DC and Cloud.

---

## D-06: Mermaid rendered to PNG, not macro

**Decision:** Mermaid diagrams are rendered to PNG by `mmdc` in CI and uploaded as page
attachments.

**Rationale:** Confluence Cloud does not render Mermaid natively. The paid "Mermaid Diagrams"
macro introduces a third-party dependency outside our control. Rendering to PNG in CI is
deterministic and requires no Confluence configuration.

**Trade-off:** Diagrams are static images; they cannot be edited in Confluence. This is
acceptable because the source remains in GitHub.

---

## D-07: Dual-mode client supporting DC and Cloud

**Decision:** `ConfluenceClient` accepts a `mode` parameter (`"dc"` or `"cloud"`). Auth setup
and API path construction branch on this value; all publisher logic above the client is
mode-agnostic. Switching from DC to Cloud requires only a configuration change.

**Rationale:** The organisation is migrating from Confluence Data Center to Cloud. Building a
single abstraction now avoids a code rewrite at migration time. The two modes differ only in
API base path, authentication header, and whether a client certificate is needed.

DC auth: `Authorization: Bearer <PAT>` with an optional PEM client certificate decoded from
`CONFLUENCE_CERT_PEM` (base64 env var) and written via `tempfile.mkstemp(suffix=".pem")` at
startup. Dynamic path to avoid collisions on shared runners. Cleaned up on exit via
`atexit.register(os.unlink, pem_path)`.

Cloud auth: Basic Auth `{email}:{api_token}` Base64-encoded. No client certificate.

---

## D-08: Rate limiting via `tenacity`

**Decision:** Use `tenacity` with exponential backoff for 429 and 5xx retry. No fixed
`--delay-between-pages` flag.

**Parameters:**
- `wait_exponential(multiplier=1, min=4, max=300)`
- `stop_after_attempt(5)` -- after 5 failures the page is skipped, logged as an error,
  and the build exits non-zero
- Retry on: `RequestException`, `ConnectionError`, `ReadTimeout`, `SSLError`, HTTP 429 and 5xx

Attachment uploads route through the same `_request()` retry path as all other API calls.

---

## D-09: Fixed manifest path at repository root

**Decision:** The manifest is always `confluence-manifest.yaml` at the repository root.
No configurable path.

**Rationale:** Opinionated convention removes a configuration decision for every adopting team.
Anyone looking at a repo can immediately find the manifest without consulting docs.

---

## D-10: Info macro banner on all published pages

**Decision:** Every published page opens with an `ac:structured-macro ac:name="info"` block
containing the source file path and Git commit SHA.

**Rationale:** The blue Info panel is visually distinct and available in both DC and Cloud.
Plain text is too easy to miss; the Note (yellow) macro feels alarmist for routine doc pages.
The Info macro makes it unambiguous that the page is managed and that manual edits will be
overwritten.

**Storage Format:**
```xml
<ac:structured-macro ac:name="info">
  <ac:rich-text-body>
    <p>This page is auto-generated from GitHub. Manual edits will be overwritten on next publish.<br/>
    Source: <code>docs/architecture.md</code> @ <code>a1b2c3d</code></p>
  </ac:rich-text-body>
</ac:structured-macro>
```

The content hash (`last_published_hash`) is computed from the body only, excluding the banner,
so banner-only changes do not trigger spurious re-publishes.

---

## D-11: Manifest state writeback via commit to `main`

**Decision:** After a successful publish run, the tool commits the updated
`confluence-manifest.yaml` (with `last_published_hash`, `last_published_version`, and
`last_published_commit` written back) directly to the default branch from within the
GitHub Action.

**Rationale:** Single file, single source of truth. Simpler to audit than a sidecar file.
Avoids fetching live Confluence content on every run.

**Implementation notes:**
- The `contents: write` permission is set in the workflow `permissions` block.
- The commit message includes `[skip ci]` to prevent the Action from re-triggering on its
  own writeback commit.
- The commit author is `confluence-publisher-bot <noreply@github.com>` so it is
  distinguishable in git log.
- The "Commit manifest state" step uses `if: always()` so `page_id` values written during
  a partially-failed run (e.g. attachment upload failure after page creation) are still
  committed and available for the retry on the next push.

---

## Open questions

### OQ-04: Pages removed from the manifest

**Question:** If a file is removed from the manifest or deleted from GitHub, what happens to
the Confluence page?

**Options:**
- A: Do nothing. Manual cleanup required. *(current behaviour)*
- B: Archive the Confluence page (move to an archive space or parent)
- C: Delete the Confluence page

**Recommendation:** Option A. Deletion is irreversible and risks removing content that
stakeholders depend on. Entries removed from the manifest are flagged as warnings by
`validate-manifest`. **Not blocking.**

---

### OQ-05: PR preview comment

**Question:** Should the `--check` run in PRs post a comment summarising what will be
published (pages changed, any warnings)?

**Options:**
- A: No comment. Build pass/fail is sufficient. *(current behaviour)*
- B: Post a summary comment via `gh pr comment` (pages to be updated, warnings, skipped pages)
- C: Post a full diff of Confluence Storage Format (too verbose)

**Recommendation:** Option B when bandwidth allows. The `examples/workflows/pr-preview.yml`
workflow already posts a dry-run summary as a PR comment. **Not blocking.**

---

### OQ-06: Reduce or eliminate manifest write-back via Confluence-native state

**Question:** Can page identity and content-hash tracking move out of the git-committed
manifest and into Confluence itself, reducing or removing the operational fragility that
D-11's write-back introduces (target-repo branch protection, `GITHUB_TOKEN`/PAT permission
requirements, PR-fallback branch-naming conflicts, and similar friction discovered across
several real rollouts)?

**Context:** A comparable internal system for publishing GitHub docs elsewhere writes nothing
back to its source repos at all, because its publishing target doesn't need a stable ID
remembered anywhere the way a Confluence page does -- the file path is identity enough.
Comparing this tool's design against that one made it clear that essentially all of the
write-back-adjacent operational pain this tool has produced traces back to one requirement:
D-11's need to persist `page_id`/`last_published_hash`/`last_published_version` in a
git-committed file after every run.

**Options:**
- A: Keep write-back as the only mechanism. *(current behaviour, D-11)*
- B: Store `last_published_hash` (and related metadata) as a Confluence content property on
  the page itself, keyed by the already-known `page_id`, instead of in the manifest. Cuts
  write-back frequency to "only when a page is first created," not every content change.
  Doesn't touch how page identity is resolved.
- C: Additionally resolve page identity via a Confluence label applied at page-creation time
  and looked up via CQL search on later runs, instead of requiring a manifest-recorded
  `page_id`. This would enable a manifest-free `auto_discover` mode for files that don't need
  explicit title/space/parent overrides -- no write-back at all for those files, ever.

**Recommendation:** Option B is low-risk and independently valuable regardless of C -- content
properties are fetched by page ID, which is already known, so there's no new lookup-reliability
question to resolve. Option C is a bigger change gated on an unverified assumption: whether CQL
label search is reliably available via the API paths this tool already uses for both DC and
Cloud, and whether search-index lag immediately after page creation risks duplicate-page
creation (the same class of bug D-11's writeback already had to solve once, for a different
reason). That assumption needs live verification against a real tenant before committing to C,
not an assumption. **Not blocking** -- scoped in detail in
[issue #34](https://github.com/pipewell/confluence-publisher/issues/34).
