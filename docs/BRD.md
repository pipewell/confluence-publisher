# Business Requirements Document

**Project:** confluence-publisher

---

## Background

Technical documentation currently lives in GitHub as Markdown files. Non-technical
stakeholders, product managers, and leadership primarily use Confluence as their knowledge
management surface. This creates a split: engineers maintain docs in GitHub, but the wider
organisation either ignores them or maintains a parallel (and inevitably stale) copy in
Confluence.

This project closes that gap with a one-way publishing pipeline. Engineers continue working
in GitHub. Confluence receives a rendered, always-up-to-date copy automatically.

---

## Goals

1. Eliminate manually maintained duplicate documentation.
2. Make engineering documentation accessible to non-GitHub users without any extra effort from engineers.
3. Establish GitHub as the single source of truth for technical docs.
4. Reduce time spent copying, reformatting, and updating Confluence pages by hand.

---

## Non-goals

- Bidirectional sync (Confluence edits do not flow back to GitHub).
- Replacing Confluence for non-engineering teams.
- Migrating existing Confluence pages not originated from GitHub.
- Real-time or sub-minute publishing cadence.

---

## Stakeholders

| Role | Interest |
|---|---|
| Engineering teams | Write docs in GitHub; want them visible in Confluence without extra work |
| Product and programme managers | Read docs in Confluence; want them current |
| Platform and DevOps | Own the GitHub Actions infrastructure and Confluence service account |
| Engineering leadership | Visibility into technical decisions without switching tools |

---

## Success metrics

| Metric | Target |
|---|---|
| Time from merge to Confluence update | Under 10 minutes |
| Conversion accuracy on supported syntax | 100% -- no silent failures |
| Manual Confluence page maintenance eliminated | All pages covered by the manifest |
| Build failures due to unsupported syntax | Caught in PR, not post-merge |
| Stale pages (Confluence content older than GitHub by more than one commit) | Zero for managed pages |

---

## Constraints

- Confluence Cloud only for the initial release. Data Center support is included from day one to
  enable a smooth migration when the organisation moves to Cloud.
- Authentication via API token (Confluence service account). OAuth and Forge are out of scope.
- One-way only. Any manual edits to managed Confluence pages will be detected and overwritten,
  with a configurable warning or build failure.
- The tool must be adoptable by other teams with minimal configuration: space ID, parent page ID,
  and manifest file.

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Markdown features used in docs that the converter does not support | High | Medium | Lint in PR via `--check` mode; block merge on conversion errors |
| Manual Confluence edits overwritten without notice | Medium | High | Detect version mismatch before overwrite; surface as warning or build error in strict mode |
| Confluence API rate limiting on large syncs | Medium | Medium | Exponential backoff with jitter via `tenacity`; serialised per-page updates |
| Page manifest falling out of sync with Confluence | Low | High | `validate-manifest` mode run on nightly schedule |
| Mermaid diagrams not rendering in Confluence | High | Low | Render to PNG in CI via `mmdc`; attach as image |
