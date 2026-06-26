from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from confluence_publisher.manifest import load_manifest, Manifest
from confluence_publisher.publisher import check_pages, publish_pages, PublishSummary, _render_mermaid


MANIFEST_DATA = {
    "version": 1,
    "defaults": {"space_id": "TEST"},
    "pages": {
        "docs/arch.md": {"page_id": "111", "title": "Architecture"},
        "docs/runbook.md": {"page_id": "222", "title": "Runbook"},
    },
}


def make_repo(tmp_path: Path, files: dict[str, str] | None = None) -> tuple[Path, Manifest]:
    (tmp_path / "confluence-manifest.yaml").write_text(
        yaml.dump(MANIFEST_DATA, sort_keys=False)
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    default_files = {
        "docs/arch.md": "# Architecture\n\nContent here.\n",
        "docs/runbook.md": "# Runbook\n\nSteps here.\n",
    }
    for path, content in (files or default_files).items():
        (tmp_path / path).write_text(content)
    return tmp_path, load_manifest(tmp_path)


def make_client(version: int = 5) -> MagicMock:
    client = MagicMock()
    client.get_page.return_value = {"version": version, "body": "<p>old</p>"}
    client.update_page.return_value = {"id": "111"}
    client.create_page.return_value = "999"
    return client


# --- Publish flow ---

def test_publish_calls_update_page(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client()
    summary = publish_pages(manifest, ["docs/arch.md"], client, "abc1234", root)
    assert summary.succeeded
    assert len(summary.published) == 1
    client.update_page.assert_called_once()


def test_publish_passes_version_plus_one(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client(version=5)
    publish_pages(manifest, ["docs/arch.md"], client, "abc", root)
    _, kwargs = client.update_page.call_args
    assert kwargs["version"] == 6


def test_publish_passes_commit_sha(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client()
    publish_pages(manifest, ["docs/arch.md"], client, "sha999", root)
    _, kwargs = client.update_page.call_args
    assert kwargs["commit_sha"] == "sha999"


def test_skip_when_hash_unchanged(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client()
    publish_pages(manifest, ["docs/arch.md"], client, "sha1", root)
    client.reset_mock()
    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha2", root)
    client.update_page.assert_not_called()
    assert len(summary.skipped) == 1


def test_republish_when_content_changes(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client()
    publish_pages(manifest, ["docs/arch.md"], client, "sha1", root)
    client.reset_mock()
    (tmp_path / "docs/arch.md").write_text("# New Content\n")
    client.get_page.return_value = {"version": 6, "body": "<p>old</p>"}
    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha2", root)
    client.update_page.assert_called_once()
    assert len(summary.published) == 1


def test_edit_conflict_logs_warning(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client(version=5)
    publish_pages(manifest, ["docs/arch.md"], client, "sha1", root)
    client.get_page.return_value = {"version": 8, "body": "<p>manual edit</p>"}
    (tmp_path / "docs/arch.md").write_text("# Updated\n")
    manifest2 = load_manifest(tmp_path)
    summary = publish_pages(manifest2, ["docs/arch.md"], client, "sha2", root)
    conflict = [r for r in summary.results if r.status == "conflict_warned"]
    assert len(conflict) == 1
    assert "8" in conflict[0].message


def test_file_not_in_manifest_is_ignored(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client()
    summary = publish_pages(manifest, ["docs/unknown.md"], client, "sha", root)
    client.update_page.assert_not_called()
    assert len(summary.results) == 0


def test_file_not_on_disk_is_error(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client()
    (tmp_path / "docs/arch.md").unlink()
    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", root)
    assert not summary.succeeded
    assert "does not exist" in summary.errors[0].message


def test_conversion_error_is_error(tmp_path):
    root, manifest = make_repo(
        tmp_path, files={"docs/arch.md": "~~strikethrough~~\n", "docs/runbook.md": "text"}
    )
    client = make_client()
    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", root)
    assert not summary.succeeded
    client.update_page.assert_not_called()


def test_api_error_is_error(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client()
    client.get_page.side_effect = Exception("connection refused")
    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", root)
    assert not summary.succeeded
    assert "connection refused" in summary.errors[0].message


# --- Auto page creation ---

def test_auto_create_when_no_page_id(tmp_path):
    data = {
        "version": 1,
        "defaults": {"space_id": "TEST", "parent_id": ""},
        "pages": {"docs/arch.md": {"title": "Architecture"}},
    }
    (tmp_path / "confluence-manifest.yaml").write_text(yaml.dump(data))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/arch.md").write_text("# Arch\n\nContent.\n")
    manifest = load_manifest(tmp_path)
    client = make_client()
    client.create_page.return_value = "999"

    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", tmp_path)

    assert summary.succeeded
    assert len(summary.published) == 1
    assert summary.published[0].message == "created"
    client.create_page.assert_called_once()
    client.update_page.assert_not_called()
    # page_id written back to manifest entry
    assert manifest.pages["docs/arch.md"].page_id == "999"


def test_auto_create_page_id_persisted_in_manifest(tmp_path):
    data = {
        "version": 1,
        "defaults": {"space_id": "TEST"},
        "pages": {"docs/arch.md": {"title": "Architecture"}},
    }
    (tmp_path / "confluence-manifest.yaml").write_text(yaml.dump(data))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/arch.md").write_text("# Arch\n")
    manifest = load_manifest(tmp_path)
    client = make_client()
    client.create_page.return_value = "777"

    publish_pages(manifest, ["docs/arch.md"], client, "sha", tmp_path)

    saved = yaml.safe_load((tmp_path / "confluence-manifest.yaml").read_text())
    assert saved["pages"]["docs/arch.md"]["page_id"] == "777"


def test_auto_create_passes_space_and_parent(tmp_path):
    data = {
        "version": 1,
        "defaults": {"space_id": "MYSPACE", "parent_id": "55"},
        "pages": {"docs/arch.md": {"title": "Architecture"}},
    }
    (tmp_path / "confluence-manifest.yaml").write_text(yaml.dump(data))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/arch.md").write_text("# Arch\n")
    manifest = load_manifest(tmp_path)
    client = make_client()

    publish_pages(manifest, ["docs/arch.md"], client, "sha", tmp_path)

    _, kwargs = client.create_page.call_args
    assert kwargs["space_key"] == "MYSPACE"
    assert kwargs["parent_id"] == "55"
    assert kwargs["title"] == "Architecture"


def test_auto_create_with_images_uses_placeholder_then_updates(tmp_path):
    """New pages with local images: create with placeholder, upload, then update body."""
    data = {
        "version": 1,
        "defaults": {"space_id": "TEST"},
        "pages": {"docs/arch.md": {"title": "Architecture"}},
    }
    (tmp_path / "confluence-manifest.yaml").write_text(yaml.dump(data))
    (tmp_path / "docs").mkdir()
    img_dir = tmp_path / "docs" / "images"
    img_dir.mkdir()
    (img_dir / "fig.png").write_bytes(b"\x89PNG")
    (tmp_path / "docs/arch.md").write_text("# Arch\n\n![fig](images/fig.png)\n")
    manifest = load_manifest(tmp_path)
    client = make_client()
    client.create_page.return_value = "999"

    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", tmp_path)

    assert summary.succeeded
    # create_page must use a safe placeholder body (no attachment references)
    _, create_kwargs = client.create_page.call_args
    assert "ri:attachment" not in create_kwargs["body"]
    assert "in progress" in create_kwargs["body"].lower() or "momentarily" in create_kwargs["body"].lower()
    # attachment uploaded
    client.upload_attachment.assert_called_once()
    # update_page called with full body containing the attachment ref
    client.update_page.assert_called_once()
    _, update_kwargs = client.update_page.call_args
    assert "ri:attachment" in update_kwargs["body"]
    assert update_kwargs["version"] == 2


def test_auto_create_no_attachments_single_step(tmp_path):
    """New pages with no attachments publish in a single create call."""
    data = {
        "version": 1,
        "defaults": {"space_id": "TEST"},
        "pages": {"docs/arch.md": {"title": "Architecture"}},
    }
    (tmp_path / "confluence-manifest.yaml").write_text(yaml.dump(data))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/arch.md").write_text("# Arch\n\nPlain text only.\n")
    manifest = load_manifest(tmp_path)
    client = make_client()
    client.create_page.return_value = "999"

    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", tmp_path)

    assert summary.succeeded
    client.create_page.assert_called_once()
    # No extra update_page call needed — full body was passed to create_page
    client.update_page.assert_not_called()
    _, create_kwargs = client.create_page.call_args
    assert "<h1>Arch</h1>" in create_kwargs["body"]


def test_same_run_create_updates_page_id_map(tmp_path):
    """A page created earlier in the same run must be resolvable as an ac:link by later pages.

    page_id_map was built before the run started, so newly-created page IDs must be
    added to the map as they are assigned -- otherwise the second page publishes a
    plain Markdown href and the link stays broken until the next full sync.
    """
    data = {
        "version": 1,
        "defaults": {"space_id": "TEST"},
        "pages": {
            "docs/new-page.md": {"title": "New Page"},
            "docs/linker.md": {"title": "Linker"},
        },
    }
    (tmp_path / "confluence-manifest.yaml").write_text(yaml.dump(data))
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "new-page.md").write_text("# New Page\n")
    # linker.md references new-page.md which has no page_id yet at the start of the run
    (docs / "linker.md").write_text("[go to new](new-page.md)\n")
    manifest = load_manifest(tmp_path)
    client = make_client()
    # create_page returns "888" for new-page, then "889" for linker (if it also needs creating)
    client.create_page.side_effect = ["888", "889"]

    # Process new-page first, then linker -- simulates the order in changed_files
    summary = publish_pages(
        manifest,
        ["docs/new-page.md", "docs/linker.md"],
        client,
        "sha",
        tmp_path,
    )

    assert summary.succeeded
    # The linker page body must contain an ac:link pointing to the new page ID "888"
    calls = client.create_page.call_args_list
    linker_body = calls[1][1]["body"]
    assert 'ri:content-id="888"' in linker_body, (
        "Link to same-run created page was not rewritten -- page_id_map not updated after create"
    )


def test_auto_create_attachment_failure_leaves_placeholder(tmp_path):
    """Upload failure on new page: page_id saved, no hash written, update_page not called."""
    data = {
        "version": 1,
        "defaults": {"space_id": "TEST"},
        "pages": {"docs/arch.md": {"title": "Architecture"}},
    }
    (tmp_path / "confluence-manifest.yaml").write_text(yaml.dump(data))
    (tmp_path / "docs").mkdir()
    img_dir = tmp_path / "docs" / "images"
    img_dir.mkdir()
    (img_dir / "fig.png").write_bytes(b"\x89PNG")
    (tmp_path / "docs/arch.md").write_text("# Arch\n\n![fig](images/fig.png)\n")
    manifest = load_manifest(tmp_path)
    client = make_client()
    client.create_page.return_value = "999"
    client.upload_attachment.side_effect = Exception("API error")

    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", tmp_path)

    assert not summary.succeeded
    # page_id recorded so next run goes through update path, not recreation
    assert manifest.pages["docs/arch.md"].page_id == "999"
    # Hash NOT saved — next run will retry the full publish
    assert manifest.pages["docs/arch.md"].last_published_hash is None
    # Full body NOT pushed — page stays with safe placeholder
    client.update_page.assert_not_called()


def test_auto_create_attachment_failure_retries_via_update_path_on_next_run(tmp_path):
    """Full lifecycle: failed create (attachment error) -> page_id persisted ->
    next run succeeds via the update path without recreating the page."""
    data = {
        "version": 1,
        "defaults": {"space_id": "TEST"},
        "pages": {"docs/arch.md": {"title": "Architecture"}},
    }
    (tmp_path / "confluence-manifest.yaml").write_text(yaml.dump(data))
    (tmp_path / "docs").mkdir()
    img_dir = tmp_path / "docs" / "images"
    img_dir.mkdir()
    (img_dir / "fig.png").write_bytes(b"\x89PNG")
    (tmp_path / "docs/arch.md").write_text("# Arch\n\n![fig](images/fig.png)\n")

    # --- Run 1: attachment upload fails mid-create ---
    client = make_client()
    client.create_page.return_value = "999"
    client.upload_attachment.side_effect = Exception("transient API error")

    summary1 = publish_pages(load_manifest(tmp_path), ["docs/arch.md"], client, "sha1", tmp_path)

    assert not summary1.succeeded
    # Manifest on disk must carry the page_id even though the run failed
    saved = yaml.safe_load((tmp_path / "confluence-manifest.yaml").read_text())
    assert saved["pages"]["docs/arch.md"].get("page_id") == "999"
    assert saved["pages"]["docs/arch.md"].get("last_published_hash") is None

    # --- Run 2: attachment upload succeeds (transient error resolved) ---
    client2 = make_client()
    client2.get_page.return_value = {"version": 1, "body": "<p>placeholder</p>"}

    summary2 = publish_pages(load_manifest(tmp_path), ["docs/arch.md"], client2, "sha2", tmp_path)

    assert summary2.succeeded
    # Must go through update path, not create a second page
    client2.create_page.assert_not_called()
    client2.update_page.assert_called_once()
    client2.upload_attachment.assert_called_once()
    # Hash now saved
    saved2 = yaml.safe_load((tmp_path / "confluence-manifest.yaml").read_text())
    assert saved2["pages"]["docs/arch.md"]["last_published_hash"] is not None


def test_auto_create_dry_run_does_not_call_api(tmp_path):
    data = {
        "version": 1,
        "defaults": {"space_id": "TEST"},
        "pages": {"docs/arch.md": {"title": "Architecture"}},
    }
    (tmp_path / "confluence-manifest.yaml").write_text(yaml.dump(data))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/arch.md").write_text("# Arch\n")
    manifest = load_manifest(tmp_path)
    client = make_client()

    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", tmp_path, dry_run=True)

    client.create_page.assert_not_called()
    assert len(summary.published) == 1
    assert "would create" in summary.published[0].message


# --- Image upload ---

def test_images_uploaded_on_publish(tmp_path):
    root, manifest = make_repo(tmp_path)
    img_dir = tmp_path / "docs" / "images"
    img_dir.mkdir()
    (img_dir / "fig.png").write_bytes(b"\x89PNG")
    (tmp_path / "docs/arch.md").write_text("![fig](images/fig.png)\n")
    client = make_client()

    publish_pages(manifest, ["docs/arch.md"], client, "sha", root)

    client.upload_attachment.assert_called_once()
    _, kwargs = client.upload_attachment.call_args
    assert kwargs["filename"] == "fig.png"
    assert kwargs["data"] == b"\x89PNG"


def test_mermaid_uploaded_on_publish(tmp_path):
    root, manifest = make_repo(tmp_path)
    (tmp_path / "docs/arch.md").write_text("```mermaid\ngraph TD\n  A --> B\n```\n")
    client = make_client()

    with patch("confluence_publisher.publisher.shutil.which", return_value="/usr/bin/mmdc"):
        with patch("confluence_publisher.publisher._render_mermaid", return_value=b"\x89PNG") as mock_render:
            summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", root)

    assert summary.succeeded
    mock_render.assert_called_once_with("graph TD\n  A --> B\n", 0)
    client.upload_attachment.assert_called_once()
    _, kwargs = client.upload_attachment.call_args
    assert kwargs["filename"] == "mermaid-0.png"
    assert kwargs["mime_type"] == "image/png"


def test_mermaid_missing_mmdc_is_error(tmp_path):
    root, manifest = make_repo(tmp_path)
    (tmp_path / "docs/arch.md").write_text("```mermaid\ngraph TD\n```\n")
    client = make_client()

    with patch("confluence_publisher.publisher.shutil.which", return_value=None):
        summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", root)

    assert not summary.succeeded
    assert any("mmdc not found" in r.message for r in summary.errors)
    client.upload_attachment.assert_not_called()
    # Page body must not be updated with a broken attachment reference
    client.update_page.assert_not_called()


def test_missing_image_fails_publish(tmp_path):
    root, manifest = make_repo(tmp_path)
    (tmp_path / "docs/arch.md").write_text("![missing](images/missing.png)\n")
    client = make_client()

    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", root)

    assert not summary.succeeded
    assert any("not found" in r.message for r in summary.errors)
    # Pre-flight check must prevent the page body from being published
    client.update_page.assert_not_called()


# --- Strict conflicts ---

def test_strict_conflicts_fails_build(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client(version=5)
    publish_pages(manifest, ["docs/arch.md"], client, "sha1", root)
    # Simulate manual Confluence edit (version jumped)
    client.get_page.return_value = {"version": 8, "body": "<p>manual</p>"}
    (tmp_path / "docs/arch.md").write_text("# Updated\n")
    manifest2 = load_manifest(tmp_path)

    summary = publish_pages(
        manifest2, ["docs/arch.md"], client, "sha2", root, strict_conflicts=True
    )

    assert not summary.succeeded
    assert any(r.status == "error" and "Conflict" in r.message for r in summary.results)


def test_strict_conflicts_still_updates_page(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client(version=5)
    publish_pages(manifest, ["docs/arch.md"], client, "sha1", root)
    client.get_page.return_value = {"version": 8, "body": "<p>manual</p>"}
    (tmp_path / "docs/arch.md").write_text("# Updated\n")
    manifest2 = load_manifest(tmp_path)

    publish_pages(manifest2, ["docs/arch.md"], client, "sha2", root, strict_conflicts=True)

    # Page is still updated despite the conflict error
    client.update_page.assert_called()


def test_no_conflict_no_strict_error(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client(version=5)
    publish_pages(manifest, ["docs/arch.md"], client, "sha1", root)
    # No manual edit — version is exactly what we published
    client.get_page.return_value = {"version": 6, "body": "<p>old</p>"}
    (tmp_path / "docs/arch.md").write_text("# Updated\n")
    manifest2 = load_manifest(tmp_path)

    summary = publish_pages(
        manifest2, ["docs/arch.md"], client, "sha2", root, strict_conflicts=True
    )

    assert summary.succeeded


# --- Dry run ---

def test_dry_run_does_not_call_api(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client()
    summary = publish_pages(manifest, ["docs/arch.md"], client, "sha", root, dry_run=True)
    client.update_page.assert_not_called()
    assert len(summary.published) == 1
    assert summary.published[0].message == "dry-run"


def test_dry_run_does_not_save_manifest(tmp_path):
    root, manifest = make_repo(tmp_path)
    client = make_client()
    with patch("confluence_publisher.publisher.save_manifest") as mock_save:
        publish_pages(manifest, ["docs/arch.md"], client, "sha", root, dry_run=True)
        mock_save.assert_not_called()


# --- _render_mermaid ---

def test_render_mermaid_returns_none_when_mmdc_missing():
    with patch("confluence_publisher.publisher.shutil.which", return_value=None):
        result = _render_mermaid("graph TD\n  A --> B", 0)
    assert result is None


def test_render_mermaid_calls_mmdc(tmp_path):
    with patch("confluence_publisher.publisher.shutil.which", return_value="/usr/bin/mmdc"):
        with patch("confluence_publisher.publisher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            # Write a fake PNG so read_bytes() works
            with patch("confluence_publisher.publisher.tempfile.TemporaryDirectory") as mock_tmpdir:
                mock_tmpdir.return_value.__enter__ = lambda s: str(tmp_path)
                mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
                (tmp_path / "diagram.png").write_bytes(b"\x89PNG")
                result = _render_mermaid("graph TD", 0)

    assert result == b"\x89PNG"
    cmd = mock_run.call_args[0][0]
    assert "mmdc" in cmd
    assert "--backgroundColor" in cmd


def test_render_mermaid_returns_none_on_mmdc_failure(tmp_path):
    with patch("confluence_publisher.publisher.shutil.which", return_value="/usr/bin/mmdc"):
        with patch("confluence_publisher.publisher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr=b"error")
            with patch("confluence_publisher.publisher.tempfile.TemporaryDirectory") as mock_tmpdir:
                mock_tmpdir.return_value.__enter__ = lambda s: str(tmp_path)
                mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
                result = _render_mermaid("graph TD", 0)

    assert result is None


# --- check_pages ---

def test_check_pages_valid(tmp_path):
    root, manifest = make_repo(tmp_path)
    errors = check_pages(manifest, root)
    assert errors == []


def test_check_pages_missing_file(tmp_path):
    root, manifest = make_repo(tmp_path)
    (tmp_path / "docs/arch.md").unlink()
    errors = check_pages(manifest, root)
    assert any("not found" in e for e in errors)


def test_check_pages_unsupported_syntax(tmp_path):
    root, manifest = make_repo(
        tmp_path, files={"docs/arch.md": "~~strikethrough~~", "docs/runbook.md": "text"}
    )
    errors = check_pages(manifest, root)
    assert any("Strikethrough" in e for e in errors)


def test_check_pages_missing_image(tmp_path):
    root, manifest = make_repo(
        tmp_path,
        files={"docs/arch.md": "![fig](images/fig.png)\n", "docs/runbook.md": "text"},
    )
    errors = check_pages(manifest, root)
    assert any("image" in e.lower() for e in errors)
    assert any("fig.png" in e for e in errors)


def test_check_pages_present_image_is_ok(tmp_path):
    root, manifest = make_repo(
        tmp_path,
        files={"docs/arch.md": "![fig](images/fig.png)\n", "docs/runbook.md": "text"},
    )
    img_dir = tmp_path / "docs" / "images"
    img_dir.mkdir()
    (img_dir / "fig.png").write_bytes(b"\x89PNG")
    errors = check_pages(manifest, root)
    assert errors == []


def test_check_pages_no_page_id_is_ok(tmp_path):
    data = {
        "version": 1,
        "defaults": {"space_id": "TEST"},
        "pages": {"docs/arch.md": {"title": "Arch"}},
    }
    (tmp_path / "confluence-manifest.yaml").write_text(yaml.dump(data))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/arch.md").write_text("# Arch\n")
    manifest = load_manifest(tmp_path)
    errors = check_pages(manifest, tmp_path)
    assert errors == []
