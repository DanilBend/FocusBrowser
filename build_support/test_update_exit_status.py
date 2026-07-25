#!/usr/bin/env python3
"""Static regression check for Focus updater installer exit-code handling."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = Path(
    os.environ.get("FOCUS_ACTIVE_SOURCE_ROOT", ROOT / "build" / "src")
).resolve()
OVERRIDES = ROOT / "source_overrides"

SUCCESS_CODES = {
    "FIRST_INSTALL_SUCCESS": 0,
    "INSTALL_REPAIRED": 1,
    "NEW_VERSION_UPDATED": 2,
    "EXISTING_VERSION_LAUNCHED": 3,
    "IN_USE_UPDATED": 30,
}

MIRRORED_FILES = (
    "chrome/browser/win/winsparkle_glue.cc",
    "chrome/installer/focus_update_helper/update_installer.cc",
    "chrome/installer/focus_update_helper/BUILD.gn",
    "chrome/installer/focus_update_helper/update_install_status_unittest.cc",
    "chrome/installer/util/update_install_status.h",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    constants = (
        ACTIVE / "chrome/installer/util/util_constants.h"
    ).read_text(encoding="utf-8")
    header = (
        ACTIVE / "chrome/installer/util/update_install_status.h"
    ).read_text(encoding="utf-8")

    for name, expected in SUCCESS_CODES.items():
        match = re.search(rf"\b{name}\s*=\s*(\d+)\b", constants)
        if not match or int(match.group(1)) != expected:
            raise AssertionError(
                f"{name} no longer has expected installer code {expected}"
            )
        if f"case {name}:" not in header:
            raise AssertionError(f"updater predicate does not accept {name}")

    for rejected in ("HIGHER_VERSION_EXISTS", "INSTALL_FAILED",
                     "INSUFFICIENT_RIGHTS"):
        if f"case {rejected}:" in header:
            raise AssertionError(f"updater predicate incorrectly accepts {rejected}")

    for relative in MIRRORED_FILES:
        active = ACTIVE / relative
        override = OVERRIDES / relative
        if not active.is_file() or not override.is_file():
            raise AssertionError(f"missing active/override pair: {relative}")
        if digest(active) != digest(override):
            raise AssertionError(f"active/override mismatch: {relative}")

    for relative in (
        "chrome/browser/win/winsparkle_glue.cc",
        "chrome/installer/focus_update_helper/update_installer.cc",
    ):
        source = (ACTIVE / relative).read_text(encoding="utf-8")
        if "IsSuccessfulUpdateInstallerExitCode(exit_code)" not in source:
            raise AssertionError(f"shared updater predicate not used: {relative}")
        if "exit_code != 0" in source:
            raise AssertionError(f"stale zero-only exit check remains: {relative}")

    print(
        "PASS: updater accepts installer success codes 0,1,2,3,30; "
        "failure codes remain rejected; 5 active/override pairs match"
    )


if __name__ == "__main__":
    main()
