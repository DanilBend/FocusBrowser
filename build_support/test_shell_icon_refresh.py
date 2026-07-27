#!/usr/bin/env python3
"""Static regression checks for the Windows icon-cache refresh paths.

The browser executable keeps a stable install path across updates.  Explorer
may therefore retain the previous release's icon unless setup notifies the
Shell after the executable has actually been replaced.  These checks keep the
two install paths ordered correctly without requiring an interactive Explorer
session in CI.
"""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = Path(
    os.environ.get("FOCUS_ACTIVE_SOURCE_ROOT", ROOT / "build" / "src")
).resolve()
PATCH = ROOT / "patches" / "focus" / "windows" / (
    "refresh-shell-icon-after-update.patch"
)

UPDATE_ITEM = "SHChangeNotify(SHCNE_UPDATEITEM"
ASSOCIATIONS = "SHChangeNotify(SHCNE_ASSOCCHANGED"


def extract_braced_block(source: str, marker: str) -> str:
    """Return the brace-delimited block beginning at ``marker``."""
    marker_at = source.find(marker)
    if marker_at < 0:
        raise AssertionError(f"missing source marker: {marker}")
    open_at = source.find("{", marker_at + len(marker))
    if open_at < 0:
        raise AssertionError(f"missing opening brace after: {marker}")

    depth = 0
    for index in range(open_at, len(source)):
        character = source[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[marker_at : index + 1]
    raise AssertionError(f"unterminated source block after: {marker}")


def assert_notification_pair(block: str, label: str) -> None:
    """Require one path invalidation followed by one association invalidation."""
    if block.count(UPDATE_ITEM) != 1:
        raise AssertionError(f"{label}: expected exactly one UPDATEITEM notify")
    if block.count(ASSOCIATIONS) != 1:
        raise AssertionError(f"{label}: expected exactly one ASSOCCHANGED notify")

    update_at = block.index(UPDATE_ITEM)
    associations_at = block.index(ASSOCIATIONS)
    if update_at >= associations_at:
        raise AssertionError(
            f"{label}: executable-path invalidation must precede ASSOCCHANGED"
        )
    if "SHCNF_PATHW | SHCNF_FLUSHNOWAIT" not in block[update_at:associations_at]:
        raise AssertionError(f"{label}: UPDATEITEM must be a nonblocking path notify")
    if "SHCNF_IDLIST | SHCNF_FLUSHNOWAIT" not in block[associations_at:]:
        raise AssertionError(
            f"{label}: ASSOCCHANGED must be a nonblocking association notify"
        )


def check_deferred_rename_path(setup_main: str) -> None:
    rename = extract_braced_block(
        setup_main, "installer::InstallStatus RenameChromeExecutables("
    )
    success = extract_braced_block(rename, "if (install_list->Do())")
    assert_notification_pair(success, "deferred executable rename")

    if "chrome_exe.value().c_str()" not in success:
        raise AssertionError("deferred executable rename: wrong refreshed path")
    if success.index(ASSOCIATIONS) >= success.index(
        "installer::LaunchDeleteOldVersionsProcess"
    ):
        raise AssertionError(
            "deferred executable rename: refresh must precede old-version cleanup"
        )

    # A failed transaction rolls back to the old executable and must not tell
    # Explorer that a new icon is available.
    if rename.count(UPDATE_ITEM) != 1 or rename.count(ASSOCIATIONS) != 1:
        raise AssertionError(
            "deferred executable rename: notifications escaped the success branch"
        )


def check_non_in_use_install_path(install: str) -> None:
    install_or_update = extract_braced_block(
        install, "InstallStatus InstallOrUpdateProduct("
    )
    non_in_use = extract_braced_block(install_or_update, "if (!is_in_use)")
    assert_notification_pair(non_in_use, "non-in-use install")

    if "installer_state.target_path().Append(kChromeExe)" not in non_in_use:
        raise AssertionError("non-in-use install: wrong refreshed executable path")

    # IN_USE_UPDATED/IN_USE_DOWNGRADE still point at the running old binary.
    # Their refresh belongs exclusively to RenameChromeExecutables().
    if install_or_update.count(UPDATE_ITEM) != 1 or install_or_update.count(
        ASSOCIATIONS
    ) != 1:
        raise AssertionError(
            "install path: Shell notifications must remain inside !is_in_use"
        )
    if install_or_update.index("const bool is_in_use") >= install_or_update.index(
        "if (!is_in_use)"
    ):
        raise AssertionError("install path: is_in_use must be computed before refresh")
    if install_or_update.index("if (!is_in_use)") >= install_or_update.index(
        UPDATE_ITEM
    ):
        raise AssertionError("install path: refresh is not guarded by !is_in_use")


def check_canonical_patch(patch: str) -> None:
    setup_marker = "--- a/chrome/installer/setup/setup_main.cc"
    install_marker = "--- a/chrome/installer/setup/install.cc"
    if setup_marker not in patch or install_marker not in patch:
        raise AssertionError("canonical icon-refresh patch is missing a source file")

    setup_part, install_part = patch.split(install_marker, 1)
    setup_part = setup_part.split(setup_marker, 1)[1]
    assert_notification_pair(setup_part, "canonical deferred rename patch")
    assert_notification_pair(install_part, "canonical non-in-use patch")
    if "if (install_list->Do())" not in setup_part:
        raise AssertionError("canonical rename refresh is not in the success hunk")
    if setup_part.index(ASSOCIATIONS) >= setup_part.index(
        "installer::LaunchDeleteOldVersionsProcess"
    ):
        raise AssertionError("canonical rename refresh runs after old-version cleanup")
    if "if (!is_in_use)" not in install_part:
        raise AssertionError("canonical direct refresh is not guarded by !is_in_use")
    if "installer_state.target_path().Append(kChromeExe)" not in install_part:
        raise AssertionError("canonical direct refresh uses the wrong executable path")


def main() -> None:
    patch = PATCH.read_text(encoding="utf-8")
    check_canonical_patch(patch)

    setup_path = ACTIVE / "chrome" / "installer" / "setup" / "setup_main.cc"
    install_path = ACTIVE / "chrome" / "installer" / "setup" / "install.cc"
    if setup_path.is_file() and install_path.is_file():
        check_deferred_rename_path(setup_path.read_text(encoding="utf-8"))
        check_non_in_use_install_path(install_path.read_text(encoding="utf-8"))

    for required in (
        "--- a/chrome/installer/setup/setup_main.cc",
        "--- a/chrome/installer/setup/install.cc",
        "if (!is_in_use)",
        UPDATE_ITEM,
        ASSOCIATIONS,
    ):
        if required not in patch:
            raise AssertionError(f"canonical icon-refresh patch is incomplete: {required}")

    series = (ROOT / "patches" / "series").read_text(encoding="utf-8")
    migration = "focus/windows/setup-chromium-version-migration.patch"
    refresh = "focus/windows/refresh-shell-icon-after-update.patch"
    if series.count(refresh) != 1 or series.index(refresh) <= series.index(migration):
        raise AssertionError(
            "icon-refresh patch must occur once after setup version migration"
        )

    print(
        "PASS: Shell icon refresh follows successful executable replacement; "
        "the direct install refresh is limited to !is_in_use; canonical patch "
        "ordering is valid"
    )


if __name__ == "__main__":
    main()
