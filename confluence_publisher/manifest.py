from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class PageEntry:
    file_path: str
    page_id: str
    space_id: str
    title: str
    parent_id: str | None = None
    last_published_hash: str | None = None
    last_published_version: int | None = None
    last_published_commit: str | None = None


@dataclass
class Manifest:
    path: Path
    version: int
    defaults: dict[str, Any]
    pages: dict[str, PageEntry]


def load_manifest(repo_root: Path) -> Manifest:
    manifest_path = repo_root / "confluence-manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with manifest_path.open() as f:
        data = yaml.safe_load(f)

    defaults = data.get("defaults", {})
    pages_raw = data.get("pages", {}) or {}
    pages: dict[str, PageEntry] = {}
    seen_ids: dict[str, str] = {}

    for file_path, entry_data in pages_raw.items():
        page_id = entry_data.get("page_id")
        title = entry_data.get("title")
        if not title:
            raise ValueError(f"Missing 'title' for '{file_path}' in manifest")

        if page_id:
            if page_id in seen_ids:
                raise ValueError(
                    f"Duplicate page_id '{page_id}': '{file_path}' and '{seen_ids[page_id]}'"
                )
            seen_ids[page_id] = file_path

        space_id = entry_data.get("space_id", defaults.get("space_id"))
        if not space_id:
            raise ValueError(
                f"Missing 'space_id' for '{file_path}': set defaults.space_id "
                "or a per-page space_id override"
            )

        pages[file_path] = PageEntry(
            file_path=file_path,
            page_id=page_id,
            space_id=space_id,
            title=title,
            parent_id=entry_data.get("parent_id", defaults.get("parent_id")),
            last_published_hash=entry_data.get("last_published_hash"),
            last_published_version=entry_data.get("last_published_version"),
            last_published_commit=entry_data.get("last_published_commit"),
        )

    return Manifest(
        path=manifest_path,
        version=data.get("version", 1),
        defaults=defaults,
        pages=pages,
    )


def save_manifest(manifest: Manifest) -> None:
    with manifest.path.open() as f:
        data = yaml.safe_load(f)

    for file_path, entry in manifest.pages.items():
        page_data = (data.get("pages") or {}).get(file_path)
        if page_data is None:
            continue
        if entry.page_id is not None:
            page_data["page_id"] = entry.page_id  # persist auto-created page IDs
        if entry.last_published_hash is not None:
            page_data["last_published_hash"] = entry.last_published_hash
        if entry.last_published_version is not None:
            page_data["last_published_version"] = entry.last_published_version
        if entry.last_published_commit is not None:
            page_data["last_published_commit"] = entry.last_published_commit

    with manifest.path.open("w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
