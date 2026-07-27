#!/usr/bin/env python3
"""Static regression checks for branded Windows profile-icon migration."""

from __future__ import annotations

import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = Path(
    os.environ.get("FOCUS_ACTIVE_SOURCE_ROOT", ROOT / "build" / "src")
).resolve()
PATCH_NAME = "focus/windows/refresh-profile-icon-after-branding-change.patch"
PATCH = ROOT / "patches" / PATCH_NAME
SOURCE_RELATIVE = Path(
    "chrome/browser/profiles/profile_shortcut_manager_win.cc"
)
CURRENT_VERSION = 11


def check_version(source: str, label: str) -> None:
    versions = re.findall(
        r"const int kCurrentProfileIconVersion = (\d+);", source
    )
    if versions != [str(CURRENT_VERSION)]:
        raise AssertionError(
            f"{label}: expected one profile icon version {CURRENT_VERSION}, "
            f"found {versions}"
        )


def check_regeneration_path(source: str, label: str) -> None:
    required = (
        "GetInteger(prefs::kProfileIconVersion) <",
        "kCurrentProfileIconVersion",
        "CreateOrUpdateProfileIcon(profile_path);",
        "SetInteger(prefs::kProfileIconVersion,",
    )
    for marker in required:
        if marker not in source:
            raise AssertionError(f"{label}: missing regeneration marker: {marker}")


def check_patch(patch: str) -> None:
    source_path = SOURCE_RELATIVE.as_posix()
    if patch.count(f"--- a/{source_path}") != 1:
        raise AssertionError("canonical patch must modify the profile icon source once")
    if patch.count(f"+++ b/{source_path}") != 1:
        raise AssertionError("canonical patch has an invalid destination path")
    if "-const int kCurrentProfileIconVersion = 10;" not in patch:
        raise AssertionError("canonical patch is missing the previous icon version")
    if f"+const int kCurrentProfileIconVersion = {CURRENT_VERSION};" not in patch:
        raise AssertionError("canonical patch is missing the new icon version")


def main() -> None:
    patch = PATCH.read_text(encoding="utf-8")
    check_patch(patch)

    source_path = ACTIVE / SOURCE_RELATIVE
    if source_path.is_file():
        source = source_path.read_text(encoding="utf-8")
        check_version(source, "active source")
        check_regeneration_path(source, "active source")

    series = (ROOT / "patches" / "series").read_text(encoding="utf-8")
    shell_refresh = "focus/windows/refresh-shell-icon-after-update.patch"
    if series.count(PATCH_NAME) != 1:
        raise AssertionError("profile-icon migration patch must occur once in series")
    if series.index(PATCH_NAME) <= series.index(shell_refresh):
        raise AssertionError(
            "profile-icon migration must follow the executable icon-cache refresh"
        )

    print(
        "PASS: Windows profile icon version is 11; branded profile icons will "
        "regenerate after update; canonical patch ordering is valid"
    )


if __name__ == "__main__":
    main()
