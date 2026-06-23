from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .confluence_client import ConfluenceClient
from .converter import ConversionError, ConversionResult, content_hash, convert
from .manifest import Manifest, save_manifest

logger = logging.getLogger(__name__)


@dataclass
class PageResult:
    file_path: str
    status: str  # "published" | "skipped" | "conflict_warned" | "error"
    message: str = ""


@dataclass
class PublishSummary:
    results: list[PageResult] = field(default_factory=list)

    @property
    def published(self) -> list[PageResult]:
        return [r for r in self.results if r.status == "published"]

    @property
    def skipped(self) -> list[PageResult]:
        return [r for r in self.results if r.status == "skipped"]

    @property
    def errors(self) -> list[PageResult]:
        return [r for r in self.results if r.status == "error"]

    @property
    def succeeded(self) -> bool:
        return len(self.errors) == 0


def _render_mermaid(source: str, index: int) -> bytes | None:
    """Render a Mermaid diagram to PNG via mmdc. Returns None if mmdc is not installed."""
    if not shutil.which("mmdc"):
        logger.warning(
            "mmdc not found — mermaid diagram %d will not render. "
            "Install @mermaid-js/mermaid-cli to enable rendering.",
            index,
        )
        return None
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "diagram.mmd"
        out = Path(tmpdir) / "diagram.png"
        src.write_text(source, encoding="utf-8")
        cmd = ["mmdc", "-i", str(src), "-o", str(out), "--backgroundColor", "white"]
        puppeteer_cfg = os.environ.get("MMDC_PUPPETEER_CFG")
        if puppeteer_cfg:
            cmd += ["--puppeteerConfigFile", puppeteer_cfg]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode != 0:
            logger.warning(
                "mmdc failed for diagram %d: %s",
                index,
                proc.stderr.decode(errors="replace"),
            )
            return None
        return out.read_bytes()


def _upload_images(
    client: ConfluenceClient,
    page_id: str,
    images: list[str],
    repo_root: Path,
) -> list[str]:
    """Upload local images as page attachments. Returns a list of error messages."""
    errors: list[str] = []
    for rel_path in images:
        abs_path = repo_root / rel_path
        if not abs_path.exists():
            errors.append(f"Local image not found after pre-flight check: {rel_path}")
            continue
        mime, _ = mimetypes.guess_type(str(abs_path))
        try:
            client.upload_attachment(
                page_id=page_id,
                filename=abs_path.name,
                data=abs_path.read_bytes(),
                mime_type=mime or "application/octet-stream",
            )
            logger.info("Uploaded attachment '%s' to page %s", abs_path.name, page_id)
        except Exception as exc:
            errors.append(f"Failed to upload image '{rel_path}': {exc}")
    return errors


def _upload_mermaid(
    client: ConfluenceClient,
    page_id: str,
    mermaid_blocks: list[str],
) -> list[str]:
    """Render Mermaid diagrams to PNG and upload as page attachments. Returns error messages."""
    if not mermaid_blocks:
        return []
    errors: list[str] = []
    if not shutil.which("mmdc"):
        return [
            f"mermaid-{idx}.png: mmdc not found — install @mermaid-js/mermaid-cli to render diagrams"
            for idx in range(len(mermaid_blocks))
        ]
    for idx, source in enumerate(mermaid_blocks):
        png = _render_mermaid(source, idx)
        if png is None:
            errors.append(f"mermaid-{idx}.png: mmdc failed to render — check diagram syntax")
            continue
        try:
            client.upload_attachment(
                page_id=page_id,
                filename=f"mermaid-{idx}.png",
                data=png,
                mime_type="image/png",
            )
            logger.info("Uploaded mermaid diagram %d to page %s", idx, page_id)
        except Exception as exc:
            errors.append(f"mermaid-{idx}.png: upload failed: {exc}")
    return errors


def publish_pages(
    manifest: Manifest,
    changed_files: list[str],
    client: Optional[ConfluenceClient],
    commit_sha: str,
    repo_root: Path,
    dry_run: bool = False,
    strict_conflicts: bool = False,
) -> PublishSummary:
    summary = PublishSummary()

    # Build lookup map for internal link rewriting
    page_id_map = {
        fp: entry.page_id
        for fp, entry in manifest.pages.items()
        if entry.page_id
    }

    for file_path in changed_files:
        entry = manifest.pages.get(file_path)
        if entry is None:
            logger.debug("'%s' not in manifest, skipping", file_path)
            continue

        full_path = repo_root / file_path
        if not full_path.exists():
            summary.results.append(PageResult(
                file_path=file_path,
                status="error",
                message=f"File does not exist on disk: {full_path}",
            ))
            continue

        try:
            text = full_path.read_text(encoding="utf-8")
            result = convert(text, file_path, commit_sha, page_id_map=page_id_map)
        except ConversionError as exc:
            summary.results.append(PageResult(
                file_path=file_path,
                status="error",
                message=str(exc),
            ))
            continue

        # Pre-flight: verify all local images exist before making any API call.
        # Failing here prevents publishing a page body with broken image references.
        if not dry_run:
            missing_images = [p for p in result.images if not (repo_root / p).exists()]
            if missing_images:
                for p in missing_images:
                    summary.results.append(PageResult(
                        file_path=file_path,
                        status="error",
                        message=f"Local image not found on disk: {p}",
                    ))
                continue

        # --- Auto-create new pages ---
        if not entry.page_id:
            if dry_run:
                logger.info("[dry-run] Would create page for '%s'", file_path)
                summary.results.append(PageResult(
                    file_path=file_path,
                    status="published",
                    message="dry-run (would create)",
                ))
                continue

            has_attachments = bool(result.images or result.mermaid_blocks)
            space_key = entry.space_id or manifest.defaults.get("space_id", "")
            parent_id = entry.parent_id or manifest.defaults.get("parent_id", "") or ""

            # When attachments are needed, create with a safe placeholder body so
            # the page never contains unresolvable attachment references. The body
            # is replaced in a second API call once all attachments are uploaded.
            initial_body = (
                "<p><em>Page content is being published from GitHub. "
                "This message will be replaced momentarily.</em></p>"
                if has_attachments
                else result.full_body
            )
            try:
                page_id = client.create_page(
                    title=entry.title,
                    space_key=space_key,
                    parent_id=parent_id,
                    body=initial_body,
                )
            except Exception as exc:
                summary.results.append(PageResult(
                    file_path=file_path,
                    status="error",
                    message=f"Failed to create page: {exc}",
                ))
                continue

            # Save page_id before touching attachments so reruns skip creation.
            entry.page_id = page_id

            if has_attachments:
                upload_errors = (
                    _upload_images(client, page_id, result.images, repo_root)
                    + _upload_mermaid(client, page_id, result.mermaid_blocks)
                )
                if upload_errors:
                    for msg in upload_errors:
                        logger.error("Attachment error on '%s': %s", file_path, msg)
                        summary.results.append(PageResult(
                            file_path=file_path,
                            status="error",
                            message=msg,
                        ))
                    # Page exists with safe placeholder; next run retries via update path.
                    continue

                try:
                    client.update_page(
                        page_id=page_id,
                        title=entry.title,
                        body=result.full_body,
                        version=2,  # page was created at v1
                        commit_sha=commit_sha,
                    )
                except Exception as exc:
                    summary.results.append(PageResult(
                        file_path=file_path,
                        status="error",
                        message=f"Failed to update new page body after attachment upload: {exc}",
                    ))
                    continue

                entry.last_published_version = 2
            else:
                entry.last_published_version = 1

            entry.last_published_hash = content_hash(result.body)
            entry.last_published_commit = commit_sha

            logger.info("Created '%s' -> page %s", file_path, page_id)
            summary.results.append(PageResult(
                file_path=file_path,
                status="published",
                message="created",
            ))
            continue

        # --- Update existing page ---
        new_hash = content_hash(result.body)
        if entry.last_published_hash == new_hash:
            logger.info("'%s' unchanged, skipping", file_path)
            summary.results.append(PageResult(file_path=file_path, status="skipped"))
            continue

        if dry_run:
            logger.info("[dry-run] Would publish '%s'", file_path)
            summary.results.append(PageResult(
                file_path=file_path,
                status="published",
                message="dry-run",
            ))
            continue

        try:
            current = client.get_page(entry.page_id)
        except Exception as exc:
            summary.results.append(PageResult(
                file_path=file_path,
                status="error",
                message=f"Failed to fetch page {entry.page_id}: {exc}",
            ))
            continue

        current_version = current["version"]

        if (
            entry.last_published_version is not None
            and current_version > entry.last_published_version
        ):
            conflict_msg = (
                f"Confluence version {current_version} > "
                f"last published {entry.last_published_version}"
            )
            if strict_conflicts:
                logger.error(
                    "Conflict on '%s' (%s) — overwriting with GitHub content.",
                    file_path, conflict_msg,
                )
                summary.results.append(PageResult(
                    file_path=file_path,
                    status="error",
                    message=f"Conflict: {conflict_msg}",
                ))
            else:
                logger.warning(
                    "Manual edit detected on '%s' (%s) — overwriting with GitHub content.",
                    file_path, conflict_msg,
                )
                summary.results.append(PageResult(
                    file_path=file_path,
                    status="conflict_warned",
                    message=conflict_msg,
                ))

        # Upload attachments before updating the page body so references resolve immediately
        upload_errors = (
            _upload_images(client, entry.page_id, result.images, repo_root)
            + _upload_mermaid(client, entry.page_id, result.mermaid_blocks)
        )
        if upload_errors:
            for msg in upload_errors:
                logger.error("Attachment error on '%s': %s", file_path, msg)
                summary.results.append(PageResult(
                    file_path=file_path,
                    status="error",
                    message=msg,
                ))
            # Don't update the page body with broken attachment references
            continue

        new_version = current_version + 1
        try:
            client.update_page(
                page_id=entry.page_id,
                title=entry.title,
                body=result.full_body,
                version=new_version,
                commit_sha=commit_sha,
            )
        except Exception as exc:
            summary.results.append(PageResult(
                file_path=file_path,
                status="error",
                message=f"Failed to update page {entry.page_id}: {exc}",
            ))
            continue

        entry.last_published_hash = new_hash
        entry.last_published_version = new_version
        entry.last_published_commit = commit_sha

        if not any(r.file_path == file_path for r in summary.results):
            summary.results.append(PageResult(file_path=file_path, status="published"))

        logger.info("Published '%s' -> page %s (v%d)", file_path, entry.page_id, new_version)

    if not dry_run:
        save_manifest(manifest)

    return summary


def check_pages(manifest: Manifest, repo_root: Path) -> list[str]:
    """Validate manifest entries and conversion without calling the API.
    Returns a list of error messages; empty list means all clear.
    """
    errors: list[str] = []
    page_id_map = {fp: e.page_id for fp, e in manifest.pages.items() if e.page_id}

    for file_path, entry in manifest.pages.items():
        full_path = repo_root / file_path
        if not full_path.exists():
            errors.append(f"'{file_path}': file not found on disk")
            continue
        try:
            text = full_path.read_text(encoding="utf-8")
            result = convert(text, file_path, commit_sha="<check>", page_id_map=page_id_map)
        except ConversionError as exc:
            errors.append(str(exc))
            continue

        for img_path in result.images:
            if not (repo_root / img_path).exists():
                errors.append(f"'{file_path}': local image not found on disk: {img_path}")

    return errors
