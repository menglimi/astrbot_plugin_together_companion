# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / f"{PROJECT_ROOT.name}.zip"

EXCLUDED_TOP_LEVEL = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "apps",
    "data",
    "scripts",
    "tests",
}
EXCLUDED_DIRECTORY_NAMES = {
    ".cache",
    ".gradle",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "artifacts",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "logs",
    "node_modules",
    "tmp",
    "temp",
    "venv",
}
EXCLUDED_FILE_NAMES = {
    ".coverage",
    ".DS_Store",
    ".gitignore",
    ".gitattributes",
    "Thumbs.db",
}
EXCLUDED_SUFFIXES = {
    ".bak",
    ".log",
    ".orig",
    ".pyc",
    ".pyo",
    ".rej",
    ".swp",
    ".swo",
    ".tmp",
    ".zip",
}


def _is_release_file(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if not relative.parts or relative.parts[0] in EXCLUDED_TOP_LEVEL:
        return False
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in relative.parts[:-1]):
        return False
    if relative.name in EXCLUDED_FILE_NAMES:
        return False
    return relative.suffix.lower() not in EXCLUDED_SUFFIXES


def iter_release_files(root: Path) -> Iterable[Path]:
    root = root.resolve()
    files = (
        path
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and _is_release_file(path, root)
    )
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def build_release(root: Path = PROJECT_ROOT, output: Path = DEFAULT_OUTPUT) -> Path:
    root = root.resolve()
    output = output.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"插件目录不存在：{root}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    files = list(iter_release_files(root))
    if not files:
        raise RuntimeError(f"插件目录中没有可发布文件：{root}")

    try:
        with ZipFile(
            temporary,
            mode="w",
            compression=ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for path in files:
                archive.write(path, arcname=path.relative_to(root).as_posix())
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)

    return output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建 AstrBot 插件发布 ZIP")
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="插件源码目录（默认：当前脚本的父目录）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="输出 ZIP 路径",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output = build_release(args.root, args.output)
    print(f"已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
