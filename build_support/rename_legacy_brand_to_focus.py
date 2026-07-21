#!/usr/bin/env python3
"""Mechanically remove the legacy internal brand from source trees."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess


OLD = bytes.fromhex("68656c69756d")
TOKEN = re.compile(OLD, re.IGNORECASE)


def replacement(match: re.Match[bytes]) -> bytes:
    value = match.group(0)
    if value.isupper():
        return b"FOCUS"
    if value[:1].isupper():
        return b"Focus"
    return b"focus"


def rewrite_file(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if not TOKEN.search(data):
        return False
    # Avoid changing structured binary assets. Source text can contain NUL only
    # when it is UTF-16; no project source files with this brand use UTF-16.
    if b"\0" in data[:8192]:
        return False
    updated = TOKEN.sub(replacement, data)
    if updated != data:
        path.write_bytes(updated)
        return True
    return False


def renamed_name(name: str) -> str:
    raw = name.encode("utf-8")
    return TOKEN.sub(replacement, raw).decode("utf-8")


def merge_directory(source: Path, destination: Path) -> None:
    for child in source.iterdir():
        target = destination / child.name
        if target.exists():
            if child.is_dir() and target.is_dir():
                merge_directory(child, target)
                continue
            raise FileExistsError(f"cannot merge {child} into existing {target}")
        child.rename(target)
    source.rmdir()


def process_tree(root: Path, excludes: tuple[str, ...]) -> tuple[int, int]:
    root = root.resolve()
    # Include dot-directories such as .github, while never touching Git's
    # internal object database and logs.
    glob_args: list[str] = ["--hidden", "-g", "!.git/**"]
    for item in excludes:
        clean_item = item.rstrip("/\\")
        glob_args.extend(("-g", f"!{clean_item}/**"))

    def run_rg(arguments: list[str]) -> list[Path]:
        result = subprocess.run(
            ["rg", *arguments, *glob_args], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        return [root / line.decode("utf-8")
                for line in result.stdout.splitlines() if line]

    matching_files = run_rg(["-l", "-i", OLD.decode("ascii"), "."])
    files = run_rg(["--files"])
    directories: set[Path] = set()
    for path in files:
        directories.update(parent for parent in path.parents if parent != root)

    changed_files = sum(rewrite_file(path) for path in matching_files)
    renamed_paths = 0
    for path in sorted(set(files) | directories, key=lambda item: len(item.parts),
                       reverse=True):
        new_name = renamed_name(path.name)
        if new_name == path.name or not path.exists():
            continue
        destination = path.with_name(new_name)
        if destination.exists():
            if path.is_dir() and destination.is_dir():
                merge_directory(path, destination)
            else:
                raise FileExistsError(
                    f"cannot rename {path} to existing {destination}")
        else:
            path.rename(destination)
        renamed_paths += 1

    return changed_files, renamed_paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()
    changed, renamed = process_tree(args.root, tuple(args.exclude))
    print(f"rewritten_files={changed} renamed_paths={renamed}")


if __name__ == "__main__":
    main()
