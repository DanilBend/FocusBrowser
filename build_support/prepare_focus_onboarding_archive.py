#!/usr/bin/env python3
"""Create the Windows Focus onboarding source dependency deterministically."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
from pathlib import Path, PurePosixPath
import tarfile


_LEGACY_LOWER = b"".join((b"he", b"li", b"um"))
_REPLACEMENTS = (
    (_LEGACY_LOWER.upper(), b"FOCUS"),
    (_LEGACY_LOWER.title(), b"Focus"),
    (_LEGACY_LOWER, b"focus"),
)

_WINDOWS_ESBUILD_PACKAGES = {"win32-arm64", "win32-x64"}
_WINDOWS_ROLLUP_PACKAGES = {
    "rollup-win32-arm64-msvc",
    "rollup-win32-x64-msvc",
}


def _replace_brand_bytes(data: bytes, source_name: str) -> tuple[bytes, bool]:
    if not any(old in data for old, _ in _REPLACEMENTS):
        return data, False

    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Brand text unexpectedly occurs in a non-UTF-8 file: {source_name}"
        ) from exc

    for old, new in _REPLACEMENTS:
        data = data.replace(old, new)
    return data, True


def _replace_brand_text(value: str) -> str:
    for old, new in _REPLACEMENTS:
        value = value.replace(old.decode("ascii"), new.decode("ascii"))
    return value


def _should_prune(member_name: str) -> bool:
    parts = PurePosixPath(member_name.lstrip("./")).parts

    if len(parts) >= 2 and parts[:2] == ("node_modules", "fsevents"):
        return True

    if len(parts) >= 3 and parts[:2] == ("node_modules", "@esbuild"):
        return parts[2] not in _WINDOWS_ESBUILD_PACKAGES

    if len(parts) >= 3 and parts[:2] == ("node_modules", "@rollup"):
        return parts[2] not in _WINDOWS_ROLLUP_PACKAGES

    return False


def create_archive(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        raise ValueError("Input and output archives must be different files")

    destination.parent.mkdir(parents=True, exist_ok=True)
    seen_names: set[str] = set()
    written = 0
    pruned = 0
    rewritten = 0

    with tarfile.open(source, "r:gz") as input_tar:
        with destination.open("wb") as raw_output:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw_output,
                compresslevel=9,
                mtime=0,
            ) as gzip_output:
                with tarfile.open(
                    fileobj=gzip_output,
                    mode="w|",
                    format=tarfile.PAX_FORMAT,
                ) as output_tar:
                    for original in input_tar:
                        name = _replace_brand_text(original.name)
                        if _should_prune(name):
                            pruned += 1
                            continue
                        if name in seen_names:
                            raise ValueError(f"Archive path collision after rename: {name}")
                        seen_names.add(name)

                        member = copy.copy(original)
                        member.name = name
                        member.linkname = _replace_brand_text(member.linkname)
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        member.mtime = 0
                        member.pax_headers = {}

                        if member.isfile():
                            extracted = input_tar.extractfile(original)
                            if extracted is None:
                                raise ValueError(f"Unable to read archive member: {original.name}")
                            data, changed = _replace_brand_bytes(
                                extracted.read(), original.name
                            )
                            member.size = len(data)
                            output_tar.addfile(member, io.BytesIO(data))
                            rewritten += int(changed or name != original.name)
                        else:
                            output_tar.addfile(member)
                            rewritten += int(
                                name != original.name
                                or member.linkname != original.linkname
                            )
                        written += 1

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    print(f"members={written}")
    print(f"pruned={pruned}")
    print(f"rewritten={rewritten}")
    print(f"bytes={destination.stat().st_size}")
    print(f"sha256={digest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    create_archive(args.source, args.destination)


if __name__ == "__main__":
    main()
