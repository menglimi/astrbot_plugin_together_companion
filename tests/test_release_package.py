# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from scripts.build_release import build_release


ROOT = Path(__file__).resolve().parents[1]


def test_extension_page_is_registered_and_uses_local_icons() -> None:
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
    page = (ROOT / "pages" / "一起房间" / "index.html").read_text(encoding="utf-8")
    page_icons = ROOT / "pages" / "一起房间" / "lucide.min.js"

    assert "pages:\n  - name: 一起房间\n    title: 一起房间" in metadata
    assert '<script src="./lucide.min.js"></script>' in page
    assert "unpkg.com/lucide" not in page
    assert page_icons.read_bytes() == (ROOT / "web" / "lucide.min.js").read_bytes()


def test_release_zip_is_portable_and_excludes_development_files(tmp_path: Path) -> None:
    output = build_release(ROOT, tmp_path / "plugin.zip")

    with ZipFile(output) as archive:
        names = archive.namelist()

    assert "pages/一起房间/index.html" in names
    assert "pages/一起房间/lucide.min.js" in names
    assert {
        "web/index.html",
        "web/app.css",
        "web/app.js",
        "web/lucide.min.js",
    }.issubset(names)
    assert all("\\" not in name for name in names)
    assert all(not PurePosixPath(name).is_absolute() for name in names)
    assert all(".." not in PurePosixPath(name).parts for name in names)
    assert all(PurePosixPath(name).parts[0] not in {"apps", "scripts", "tests"} for name in names)
    assert all(
        not ({"__pycache__", ".pytest_cache", ".cache", "build", "dist"} & set(PurePosixPath(name).parts))
        for name in names
    )
    assert all(not name.lower().endswith(".zip") for name in names)
    assert all(not name.lower().endswith(".tmp") for name in names)
