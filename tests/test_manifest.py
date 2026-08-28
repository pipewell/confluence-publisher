from pathlib import Path

import pytest
import yaml

from confluence_publisher.manifest import load_manifest, save_manifest

VALID_MANIFEST = {
    "version": 1,
    "defaults": {"space_id": "TEST", "parent_id": "100"},
    "pages": {
        "docs/arch.md": {
            "page_id": "111",
            "title": "Architecture",
        },
        "docs/runbook.md": {
            "page_id": "222",
            "title": "Runbook",
            "parent_id": "111",
        },
    },
}


def write_manifest(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "confluence-manifest.yaml"
    p.write_text(yaml.dump(data, sort_keys=False))
    return tmp_path


def test_load_valid_manifest(tmp_path):
    write_manifest(tmp_path, VALID_MANIFEST)
    m = load_manifest(tmp_path)
    assert m.version == 1
    assert len(m.pages) == 2
    assert m.pages["docs/arch.md"].page_id == "111"
    assert m.pages["docs/arch.md"].space_id == "TEST"
    assert m.pages["docs/arch.md"].parent_id == "100"  # from defaults
    assert m.pages["docs/runbook.md"].parent_id == "111"  # overridden


def test_load_missing_manifest(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path)


def test_missing_title_raises(tmp_path):
    data = {**VALID_MANIFEST, "pages": {"docs/x.md": {"page_id": "1"}}}
    write_manifest(tmp_path, data)
    with pytest.raises(ValueError, match="Missing 'title'"):
        load_manifest(tmp_path)


def test_duplicate_page_id_raises(tmp_path):
    data = {
        "version": 1,
        "defaults": {"space_id": "S"},
        "pages": {
            "docs/a.md": {"page_id": "999", "title": "A"},
            "docs/b.md": {"page_id": "999", "title": "B"},
        },
    }
    write_manifest(tmp_path, data)
    with pytest.raises(ValueError, match="Duplicate page_id"):
        load_manifest(tmp_path)


def test_save_manifest_writes_back_state(tmp_path):
    write_manifest(tmp_path, VALID_MANIFEST)
    m = load_manifest(tmp_path)

    entry = m.pages["docs/arch.md"]
    entry.last_published_hash = "abc123"
    entry.last_published_version = 7
    entry.last_published_commit = "deadbeef"

    save_manifest(m)

    with (tmp_path / "confluence-manifest.yaml").open() as f:
        written = yaml.safe_load(f)

    page_data = written["pages"]["docs/arch.md"]
    assert page_data["last_published_hash"] == "abc123"
    assert page_data["last_published_version"] == 7
    assert page_data["last_published_commit"] == "deadbeef"
    assert page_data["page_id"] == "111"  # existing fields preserved


def test_save_manifest_does_not_add_missing_pages(tmp_path):
    write_manifest(tmp_path, VALID_MANIFEST)
    m = load_manifest(tmp_path)
    m.pages["docs/ghost.md"] = m.pages["docs/arch.md"]  # add a page not in YAML
    m.pages["docs/ghost.md"].file_path = "docs/ghost.md"

    save_manifest(m)  # should not raise or add the ghost page

    with (tmp_path / "confluence-manifest.yaml").open() as f:
        written = yaml.safe_load(f)
    assert "docs/ghost.md" not in written["pages"]


def test_space_id_inherited_from_defaults(tmp_path):
    write_manifest(tmp_path, VALID_MANIFEST)
    m = load_manifest(tmp_path)
    assert m.pages["docs/runbook.md"].space_id == "TEST"


def test_entry_without_page_id_is_allowed(tmp_path):
    data = {
        "version": 1,
        "defaults": {"space_id": "S"},
        "pages": {"docs/new.md": {"title": "New Page"}},
    }
    write_manifest(tmp_path, data)
    m = load_manifest(tmp_path)
    assert m.pages["docs/new.md"].page_id is None


def test_missing_space_id_raises(tmp_path):
    data = {
        "version": 1,
        "defaults": {},
        "pages": {"docs/new.md": {"title": "New Page"}},
    }
    write_manifest(tmp_path, data)
    with pytest.raises(ValueError, match="Missing 'space_id'"):
        load_manifest(tmp_path)


def test_per_page_space_id_satisfies_missing_default(tmp_path):
    data = {
        "version": 1,
        "defaults": {},
        "pages": {"docs/new.md": {"title": "New Page", "space_id": "OVERRIDE"}},
    }
    write_manifest(tmp_path, data)
    m = load_manifest(tmp_path)
    assert m.pages["docs/new.md"].space_id == "OVERRIDE"
