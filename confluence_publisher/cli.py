from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import click

from .confluence_client import ConfluenceClient
from .manifest import load_manifest
from .publisher import check_pages, publish_pages

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _build_client() -> ConfluenceClient:
    base_url = os.environ.get("CONFLUENCE_BASE_URL", "").rstrip("/")
    token = os.environ.get("CONFLUENCE_API_TOKEN", "")
    mode = os.environ.get("CONFLUENCE_MODE", "dc").lower()
    email = os.environ.get("CONFLUENCE_EMAIL")
    cert_pem_b64 = os.environ.get("CONFLUENCE_CERT_PEM")

    missing = [k for k, v in [
        ("CONFLUENCE_BASE_URL", base_url),
        ("CONFLUENCE_API_TOKEN", token),
    ] if not v]
    if missing:
        raise click.ClickException(f"Missing required env vars: {', '.join(missing)}")

    return ConfluenceClient(
        base_url=base_url,
        token=token,
        mode=mode,
        email=email,
        cert_pem_b64=cert_pem_b64,
    )


def _get_commit_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return os.environ.get("GITHUB_SHA", "unknown")[:7]


@click.group()
def main():
    pass


@main.command()
@click.option(
    "--changed-files",
    multiple=True,
    help="Specific files to publish (relative paths). Publishes all manifest entries if omitted.",
)
@click.option("--dry-run", is_flag=True, help="Convert and log without calling the Confluence API.")
@click.option(
    "--strict-conflicts",
    is_flag=True,
    help=(
        "Exit non-zero when a Confluence page has been manually edited since last publish. "
        "The page is still overwritten with GitHub content (GitHub is always source of truth), "
        "but the build fails so the team is notified of the overwrite."
    ),
)
@click.option("--repo-root", default=".", show_default=True, help="Repository root directory.")
def sync(changed_files: tuple[str, ...], dry_run: bool, strict_conflicts: bool, repo_root: str):
    """Publish changed Markdown files to Confluence."""
    root = Path(repo_root).resolve()
    manifest = load_manifest(root)

    files = list(changed_files) if changed_files else list(manifest.pages.keys())

    if not files:
        logger.info("No files to publish.")
        return

    commit_sha = _get_commit_sha()
    client = None if dry_run else _build_client()

    summary = publish_pages(
        manifest=manifest,
        changed_files=files,
        client=client,
        commit_sha=commit_sha,
        repo_root=root,
        dry_run=dry_run,
        strict_conflicts=strict_conflicts,
    )

    for result in summary.results:
        if result.status == "error":
            click.echo(f"ERROR  {result.file_path}: {result.message}", err=True)
        elif result.status == "conflict_warned":
            click.echo(f"WARN   {result.file_path}: {result.message}", err=True)
        elif result.status == "skipped":
            click.echo(f"SKIP   {result.file_path}")
        else:
            suffix = f" ({result.message})" if result.message else ""
            click.echo(f"OK     {result.file_path}{suffix}")

    click.echo(
        f"\n{len(summary.published)} published, "
        f"{len(summary.skipped)} skipped, "
        f"{len(summary.errors)} errors."
    )

    if not summary.succeeded:
        sys.exit(1)


@main.command()
@click.option("--repo-root", default=".", show_default=True)
def check(repo_root: str):
    """Validate manifest and conversion without publishing. Safe to run in PRs."""
    root = Path(repo_root).resolve()
    manifest = load_manifest(root)
    errors = check_pages(manifest, root)

    if errors:
        for err in errors:
            click.echo(f"ERROR  {err}", err=True)
        click.echo(f"\n{len(errors)} error(s) found.", err=True)
        sys.exit(1)
    else:
        click.echo(f"OK  {len(manifest.pages)} page(s) checked, no errors.")


@main.command("validate-manifest")
@click.option("--repo-root", default=".", show_default=True)
def validate_manifest(repo_root: str):
    """Confirm all page_ids in the manifest exist in Confluence."""
    root = Path(repo_root).resolve()
    manifest = load_manifest(root)
    client = _build_client()

    errors: list[str] = []
    for file_path, entry in manifest.pages.items():
        if not entry.page_id:
            errors.append(f"'{file_path}': no page_id set")
            continue
        if not client.page_exists(entry.page_id):
            errors.append(f"'{file_path}': page_id '{entry.page_id}' not found in Confluence")
        else:
            click.echo(f"OK  {file_path} -> {entry.page_id}")

    if errors:
        for err in errors:
            click.echo(f"ERROR  {err}", err=True)
        sys.exit(1)
