#!/usr/bin/env python3
"""Prepare an existing Chromium checkout for a native Focus macOS build.

This tool is deliberately offline.  It never retrieves source, dependencies,
or resources and it never invokes GN, Ninja, signing, packaging, or publishing.
The mutating command requires an explicit confirmation flag.
"""

import argparse
import configparser
import hashlib
import json
import logging
import os
import platform
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from typing import Callable, Optional


MACOS_DIR = Path(__file__).resolve().parent
REPO_ROOT = MACOS_DIR.parent.parent
UTILS_DIR = REPO_ROOT / "focus-chromium" / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))
if str(MACOS_DIR) not in sys.path:
    sys.path.insert(0, str(MACOS_DIR))

import domain_substitution  # pylint: disable=wrong-import-position
import focus_version  # pylint: disable=wrong-import-position
import i18n_apply  # pylint: disable=wrong-import-position
import name_substitution  # pylint: disable=wrong-import-position
import focus_macos  # pylint: disable=wrong-import-position


SYSTEM_PATCH = Path("/usr/bin/patch")
SYSTEM_GIT = Path("/usr/bin/git")
DEPS_INI = REPO_ROOT / "focus-chromium" / "deps.ini"
DEPS_INI_SHA256 = "158806c990d70174a6f401ae488d03246d867e0272b753bfbcb7c1757633b9ea"
DOMAIN_REGEX_SHA256 = "cf128b0f182692dbf90553aaedd0d3ebc1982076dd94ad94f344bb3677455d2c"
DOMAIN_LIST_SHA256 = "e9661a754d4c15778cecabc1e9cbbb40a3876de5018d9d49d3d98e998acffd1d"
DOMAIN_LIST_ENTRY_COUNT = 17297
MACOS_DOMAIN_REGULAR_TARGET_COUNT = 17139
MACOS_DOMAIN_MISSING_TARGET_COUNT = 158
MACOS_DOMAIN_MISSING_MANIFEST_BYTES = 8222
MACOS_DOMAIN_MISSING_MANIFEST_SHA256 = (
    "3fa3788f6857ea3a4dcdcad9585ef2cf1925c66de2de28e717be72f9210b999c"
)
RESOURCE_LIST_SHA256 = "551a013984b4491c2c039cec2d09792939c6d8696e4eda23b26fd85a7b342dbd"
GENERATE_LIST_SHA256 = "02da891cb3b867e9bc806b9ab3b433fd3d8c01024fac41d5fa60c78d11b6aca9"
RESOURCE_BODY_COUNT = 58
RESOURCE_BODY_SHA256 = "bdb150572f6d9f19ac5cdbd16172d311da68f063dbcda858679237f896404903"
MAC_ICON_DESTINATION = "chrome/app/theme/chromium/mac/app.icns"
MAC_ICON_BUILD_TOKEN = "app/theme/$branding_path_component/mac/app.icns"
CHROME_BUILD_GN_SHA256 = "3851bd31f3f9bc123395dbd966557885d62911f4e1359bca47390bfc942653e4"
INSTALLER_MAC_BUILD_GN = "chrome/installer/mac/BUILD.gn"
INSTALLER_MAC_BUILD_GN_SHA256 = "e620eb87d619dc384c050e041bc9d524037f7ff3f5255f39b5e034025351bd4d"
PREPARATION_RECEIPT = "out/FocusMacPreparation.json"
PRUNING_LIST = REPO_ROOT / "focus-chromium" / "pruning.list"
PRUNING_LIST_SHA256 = "bd08456aebb271572261a9c387cc4c8d4944264cfd8044c3f165b82e3a31b5d1"
PRUNING_ENTRY_COUNT = 13800
PRUNING_ALREADY_ABSENT_LIST = MACOS_DIR / "pruning-already-absent.list"
PRUNING_ALREADY_ABSENT_COUNT = 125
PRUNING_ALREADY_ABSENT_SHA256 = (
    "183dd1c796cbbbb9ab11a74bb8a7d9b8c761999de65a8757a9a2a32883cb479c"
)
PRUNING_EXPECTED_REMOVAL_COUNT = PRUNING_ENTRY_COUNT - PRUNING_ALREADY_ABSENT_COUNT
GIB = 1024 ** 3
HARD_DISK_FLOOR_GIB = 30
HARD_DISK_FLOOR_BYTES = HARD_DISK_FLOOR_GIB * GIB
ACQUISITION_MARKER = ".focus-chromium-acquisition.json"
ACQUISITION_CHROMIUM_COMMIT = "81891e5ca708047763816c778216799ef14c66cb"
ACQUISITION_DEPOT_TOOLS_COMMIT = "93919990d65a94fd62a5b1bae4e2909df6996e4a"
ACQUISITION_GCLIENT_SPEC_SHA256 = (
    "c2ab1fe66688245018194e7845ba97102efbf9f0d40eddf87712ec7f46ce26af"
)
MAX_ACQUISITION_MARKER_BYTES = 1024 * 1024
TOOL_BOOTSTRAP_MARKER = ".focus-macos-tool-bootstrap.json"
MAX_TOOL_BOOTSTRAP_MARKER_BYTES = 1024 * 1024
DEPENDENCY_CACHE_MARKER = ".focus-project-dependencies.json"
MAX_DEPENDENCY_CACHE_MARKER_BYTES = 1024 * 1024
BOOTSTRAP_POST_FREE_GIB = 70
TOOL_BOOTSTRAP_KEYS = frozenset(
    (
        "schema",
        "hooks_complete",
        "chromium_commit",
        "depot_tools_commit",
        "source_root",
        "developer_dir",
        "acquisition_marker_sha256",
        "gclient_command",
        "gn_version",
        "tool_sha256",
        "post_hooks_free_bytes",
        "build_executed",
    )
)

SHARED_DEPENDENCY_CONTRACTS = OrderedDict(
    (
        (
            "search_engines_data",
            {
                "download_filename": "nonfree-search-engines-data.tar.gz",
                "sha256": "00a87050fa3f941d04d67fb5763991e0b8ea399a88b505ab0e56dd263f06864c",
                "output_path": "third_party/search_engines_data/resources_internal",
                "strip_leading_dirs": None,
                "kind": "tar",
            },
        ),
        (
            "onboarding",
            {
                "download_filename": "onboarding-page-202607132006-focus1.tar.gz",
                "sha256": "ddb5f5e375412dc987581103d8c64a59144097a084ab3c49166a95afeea230d7",
                "output_path": "components/focus_onboarding",
                "strip_leading_dirs": None,
                "kind": "tar",
                "omitted_symlink_count": 10,
                "omitted_symlink_sha256": (
                    "d612dad748de693b7fd2bfbc5c7edf9781d75612fc96ae0bfe6dd92484dad42e"
                ),
            },
        ),
        (
            "ublock_origin",
            {
                "download_filename": "ublock-origin-1.72.2.zip",
                "sha256": "6ea10a863eb343ddcc317fdda9c65ccb2799c74d0de06ad75aded04d38d63dca",
                "output_path": "third_party/ublock",
                "strip_leading_dirs": "uBlock0.chromium",
                "kind": "zip",
            },
        ),
    )
)

MAC_HOST_DEPENDENCY_CONTRACTS = OrderedDict(
    (
        (
            "chromium_node_arm64",
            {
                "download_filename": "node-darwin-arm64-150.0.7871.128.tar.gz",
                "sha256": "b1be502d1635330ebf51d85f8d32a0d3dd92b35c6700def56ae6f903906ea825",
                "output_path": "third_party/node/mac_arm64",
                "strip_leading_dirs": None,
                "kind": "tar",
            },
        ),
        (
            "chromium_node_x64",
            {
                "download_filename": "node-darwin-x64-150.0.7871.128.tar.gz",
                "sha256": "a25cd3ef35d8b4b5a59498a5a62b5b12cc271dc420ee809abaa76110d12c156e",
                "output_path": "third_party/node/mac",
                "strip_leading_dirs": None,
                "kind": "tar",
            },
        ),
        (
            "chromium_node_modules",
            {
                "download_filename": "chromium-node-modules-150.0.7871.128.tar.gz",
                "sha256": "6781ef493aa77be4ca4824dc1d5f5157a2fbc56dacafe20914da4469f7a01b87",
                "output_path": "third_party/node/node_modules",
                "strip_leading_dirs": None,
                "kind": "tar",
            },
        ),
        (
            "esbuild_darwin_arm64",
            {
                "download_filename": "esbuild-darwin-arm64-0.25.9.tgz",
                "sha256": "dd1abc1f869ab57c5e1b76ddef546d53c473a0d06aecb77fe10af084c47ac7e6",
                "output_path": (
                    "components/focus_onboarding/node_modules/"
                    "@esbuild/darwin-arm64"
                ),
                "strip_leading_dirs": "package",
                "kind": "tar",
            },
        ),
        (
            "esbuild_darwin_x64",
            {
                "download_filename": "esbuild-darwin-x64-0.25.9.tgz",
                "sha256": "14a33c598fb04937a75efa88c5f58e2317bfd821e36b1e222bd040ff34828738",
                "output_path": (
                    "components/focus_onboarding/node_modules/@esbuild/darwin-x64"
                ),
                "strip_leading_dirs": "package",
                "kind": "tar",
            },
        ),
        (
            "rollup_darwin_arm64",
            {
                "download_filename": "rollup-darwin-arm64-4.50.1.tgz",
                "sha256": "4fcf015726b2b857fae02a87e74c61db6021d578b5a93066871f585f4c2d449b",
                "output_path": (
                    "components/focus_onboarding/node_modules/"
                    "@rollup/rollup-darwin-arm64"
                ),
                "strip_leading_dirs": "package",
                "kind": "tar",
            },
        ),
        (
            "rollup_darwin_x64",
            {
                "download_filename": "rollup-darwin-x64-4.50.1.tgz",
                "sha256": "b3ca6f5e10f3ccd532b1dfc070b5845c2194e024e40ccaa30ec34f68e3f79da0",
                "output_path": (
                    "components/focus_onboarding/node_modules/"
                    "@rollup/rollup-darwin-x64"
                ),
                "strip_leading_dirs": "package",
                "kind": "tar",
            },
        ),
    )
)

DEPENDENCY_CONTRACTS = OrderedDict(
    tuple(SHARED_DEPENDENCY_CONTRACTS.items())
    + tuple(MAC_HOST_DEPENDENCY_CONTRACTS.items())
)
DEPENDENCY_OWNERSHIP_ROOTS = (
    "third_party/search_engines_data/resources_internal",
    "components/focus_onboarding",
    "third_party/ublock",
    "third_party/node/mac_arm64",
    "third_party/node/mac",
    "third_party/node/node_modules",
)
DEPENDENCY_INSTALL_REGULAR_FILES = 13212
DEPENDENCY_INSTALL_LOGICAL_BYTES = 527357876
DEPENDENCY_INSTALL_SHA256 = (
    "fae7c86705a88ebf63e8f320f0cea0191ae9119f94e78025ce832651bf00aa78"
)
ONBOARDING_GENERATOR = "components/focus_onboarding/util/generate-i18n.mts"
ONBOARDING_GENERATOR_SHA256 = (
    "05997f7204a7f720e71821d3523c189c4bd0ee8f98cb386ad61c4f555eb24fc6"
)
ONBOARDING_STRINGS_OUTPUT = "components/focus_onboarding/src/lib/strings.ts"
ONBOARDING_STRINGS_BASELINE_BYTES = 22871
ONBOARDING_STRINGS_BASELINE_SHA256 = (
    "6c4b2ff902172bd71d666c0c34b4d37aa9e2ba858e0bd648198b45fb3fc5f683"
)
ONBOARDING_NODE_VERSION = "v24.12.0"
ONBOARDING_NODE_RELATIVE_BY_HOST = {
    "arm64": "third_party/node/mac_arm64/node-darwin-arm64/bin/node",
    "x86_64": "third_party/node/mac/node-darwin-x64/bin/node",
}
ONBOARDING_NODE_SHA256_BY_HOST = {
    "arm64": "90ee1d271eec831fd38d16c78c19cc36809548ac5cd034a6e0c10c4389c881ef",
    "x86_64": "cdd4fee89f17b91fb473a03d50ebbdef4f955740a79f8a9d8382db432198b0b7",
}
RESUME_PATCH_FAILURE_APPLIED = 98
RESUME_PATCH_FAILURE_STATUS_COUNT = 4673
RESUME_PATCH_FAILURE_STATUS_SHA256 = (
    "5619bcc2e95f36fd8177f6f23c9bc0784812bd94bf784af7f92bf540867568bb"
)
RESUME_SECOND_PATCH_FAILURE_APPLIED = 138
RESUME_SECOND_PATCH_FAILURE_STATUS_COUNT = 4780
RESUME_SECOND_PATCH_FAILURE_STATUS_SHA256 = (
    "0a1830a8cf89875186597053e6c12130aef2b8b32e43ceed060830b94a43f2b5"
)
RESUME_SECOND_DEPENDENCY_REGULAR_FILES = 13214
RESUME_SECOND_DEPENDENCY_LOGICAL_BYTES = 527367518
RESUME_SECOND_DEPENDENCY_SHA256 = (
    "38ebf05e4f17c4e8c2545bf9a93b446c0e182404d8e86617f0f811b60d8da0db"
)
RESUME_FULL_PATCH_SET_APPLIED = 324
RESUME_FULL_PATCH_SET_STATUS_COUNT = 5293
RESUME_FULL_PATCH_SET_STATUS_SHA256 = (
    "7225019e77e7eecddeaeaece124ccbf30957fa2a965b9020c56ec60d8664639e"
)
RESUME_FULL_DEPENDENCY_REGULAR_FILES = 13217
RESUME_FULL_DEPENDENCY_LOGICAL_BYTES = 527368134
RESUME_FULL_DEPENDENCY_SHA256 = (
    "b6d7bc835bed4516a353590dc51da263acb2fa92a8970c35e8353856d6c35eeb"
)
RESUME_AUDITED_PATCH_CHECKPOINTS = (
    RESUME_PATCH_FAILURE_APPLIED,
    RESUME_SECOND_PATCH_FAILURE_APPLIED,
    RESUME_FULL_PATCH_SET_APPLIED,
)
RESUME_PATCH_FAILURE_IGNORED_COUNT = 23138
RESUME_PATCH_FAILURE_IGNORED_REGULAR_FILES = 23080
RESUME_PATCH_FAILURE_IGNORED_SYMLINKS = 58
RESUME_PATCH_FAILURE_IGNORED_LOGICAL_BYTES = 7424830215
RESUME_PATCH_FAILURE_IGNORED_SYMLINK_TARGET_BYTES = 901
RESUME_PATCH_FAILURE_IGNORED_PATH_LIST_BYTES = 2189663
RESUME_PATCH_FAILURE_IGNORED_PATH_LIST_SHA256 = (
    "06d548e12b44c52bd401fa39dbab91ae038d187c2d43a309f7120d4ad5599361"
)
RESUME_PATCH_FAILURE_IGNORED_SHA256 = (
    "728c63f94903b4c4892fdfde6a097548dc2d4b360dce09137a599b495bbc4f92"
)
POST_VERSION_STATUS_COUNT = 16077
POST_VERSION_STATUS_SHA256 = (
    "14aab1de3e2f927acba39c388ac0870034473d92c567b5d5847610b26ada711a"
)
POST_VERSION_IGNORED_COUNT = 23143
POST_VERSION_IGNORED_REGULAR_FILES = 23085
POST_VERSION_IGNORED_SYMLINKS = 58
POST_VERSION_IGNORED_LOGICAL_BYTES = 7424890959
POST_VERSION_IGNORED_SYMLINK_TARGET_BYTES = 901
POST_VERSION_IGNORED_PATH_LIST_BYTES = 2189900
POST_VERSION_IGNORED_PATH_LIST_SHA256 = (
    "a5c1f5655f70637a5451994e1db6219c0f57e48fe38c4c03621ca6dc9bb68635"
)
POST_VERSION_IGNORED_SHA256 = (
    "278c39af67b4674790f9dafae8b02f752095c006aec1df0ea0639327fe002f18"
)
POST_VERSION_DEPENDENCY_REGULAR_FILES = 13274
POST_VERSION_DEPENDENCY_LOGICAL_BYTES = 527681961
POST_VERSION_DEPENDENCY_SHA256 = (
    "43cc9cc434db94e24508c5801954e2ef3cd24fa78b3f45c5157fd36dca3f6930"
)
POST_VERSION_CHROME_VERSION_BYTES = 98
POST_VERSION_CHROME_VERSION_SHA256 = (
    "8536f0e864abdb194deb1145c6b496b4f194ba0072f6e47144939d4a0fda34c7"
)
POST_VERSION_RESOURCE_INVENTORY_COUNT = 58
POST_VERSION_RESOURCE_INVENTORY_BYTES = 7568
POST_VERSION_RESOURCE_INVENTORY_SHA256 = (
    "e92041179473b41b20c4eb21c8adae2d5fd89bf4f561305ebf672358b3e43562"
)
POST_VERSION_UPSTREAM_ICNS_BYTES = 85977
POST_VERSION_UPSTREAM_ICNS_SHA256 = (
    "a1c2b17191234ee4ab1259d4fb5056ef340cc64345c1a7d2b504c632812ff062"
)
PREPARATION_RECEIPT_SCHEMA = 3


class PreparationError(RuntimeError):
    """Raised when an offline source-preparation contract is not satisfied."""


def require_disk_floor(paths, phase, disk_usage=None):
    """Require 30 GiB free at every supplied existing filesystem path."""
    usage = disk_usage or shutil.disk_usage
    measurements = OrderedDict()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            raise PreparationError(
                "disk-floor path does not exist before {}: {}".format(phase, path)
            )
        free = usage(str(path)).free
        measurements[str(path.resolve())] = {
            "free_bytes": free,
            "free_gib": round(free / GIB, 3),
        }
        if free < HARD_DISK_FLOOR_BYTES:
            raise PreparationError(
                "disk floor failed before {} at {}: {:.3f} GiB free, {} GiB required".format(
                    phase, path, free / GIB, HARD_DISK_FLOOR_GIB
                )
            )
    return {
        "phase": phase,
        "required_free_gib": HARD_DISK_FLOOR_GIB,
        "filesystems": measurements,
    }


def _json_object_without_duplicates(pairs):
    """Reject duplicate JSON keys in an acquisition provenance marker."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise PreparationError("duplicate acquisition marker key: {}".format(key))
        result[key] = value
    return result


def sha256_file(path):
    """Hash a regular file without invoking external tools."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fixed_git_environment():
    """Return a minimal locale-stable environment for read-only system Git."""
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }


def run_read_only_git(source_root, arguments):
    """Run fixed system Git without optional locks or user/system configuration."""
    source_root = require_real_directory(source_root, "Chromium source")
    if (
        SYSTEM_GIT.is_symlink()
        or not SYSTEM_GIT.is_file()
        or not os.access(SYSTEM_GIT, os.X_OK)
    ):
        raise PreparationError("fixed system Git is unavailable")
    result = subprocess.run(
        [str(SYSTEM_GIT), "--no-optional-locks", *arguments],
        cwd=source_root,
        env=fixed_git_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[-2000:]
        raise PreparationError("read-only Git command failed: {}".format(detail))
    return result.stdout


def validate_pinned_git_head(source_root):
    """Bind a mutated worktree to the exact pinned Chromium commit."""
    source_root = require_real_directory(source_root, "Chromium source")
    top = run_read_only_git(source_root, ["rev-parse", "--show-toplevel"])
    try:
        observed_top = Path(top.decode("utf-8").strip()).resolve(strict=True)
    except (UnicodeDecodeError, FileNotFoundError) as exc:
        raise PreparationError("Chromium Git top-level is invalid") from exc
    if observed_top != source_root:
        raise PreparationError("Chromium source is not the Git top-level")
    head = run_read_only_git(source_root, ["rev-parse", "HEAD"])
    try:
        observed_head = head.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise PreparationError("Chromium Git HEAD is not ASCII") from exc
    if observed_head != ACQUISITION_CHROMIUM_COMMIT:
        raise PreparationError(
            "Chromium Git HEAD mismatch: expected {}, got {}".format(
                ACQUISITION_CHROMIUM_COMMIT, observed_head
            )
        )
    return observed_head


def working_tree_inventory(source_root):
    """Hash every changed/deleted/untracked path in a canonical Git inventory."""
    source_root = require_real_directory(source_root, "Chromium source")
    raw = run_read_only_git(
        source_root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    )
    fields = raw.split(b"\0")
    if not fields or fields[-1] != b"":
        raise PreparationError("Git status did not end with a NUL terminator")
    records = []
    status_counts = OrderedDict()
    for field in fields[:-1]:
        if len(field) < 4 or field[2:3] != b" ":
            raise PreparationError("unsupported Git status record")
        try:
            status_value = field[:2].decode("ascii")
            relative = field[3:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PreparationError("Git status contains a non-UTF-8 path") from exc
        if any(value in "RC" for value in status_value):
            raise PreparationError("rename/copy Git status is forbidden during resume")
        if any(ord(character) < 0x20 for character in relative):
            raise PreparationError("Git status path contains a control character")
        relative = safe_relative(relative, "Git status")
        target = reject_symlink_ancestors(source_root, relative)
        if target.is_symlink():
            raise PreparationError("Git status path is a symlink: {}".format(target))
        if target.is_file():
            metadata = target.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise PreparationError(
                    "Git status path is not a regular file: {}".format(target)
                )
            line = "{}\t{}\t{:04o}\t{}\t{}\n".format(
                status_value,
                relative,
                stat.S_IMODE(metadata.st_mode),
                metadata.st_size,
                sha256_file(target),
            )
        elif not target.exists():
            line = "{}\t{}\tABSENT\n".format(status_value, relative)
        else:
            raise PreparationError(
                "Git status path is not a regular file or absence: {}".format(target)
            )
        records.append((relative, line))
        status_counts[status_value] = status_counts.get(status_value, 0) + 1
    if len(records) != len({relative for relative, _ in records}):
        raise PreparationError("Git status contains a duplicate path")
    body = "".join(line for _, line in sorted(records)).encode("utf-8")
    return {
        "records": len(records),
        "sha256": hashlib.sha256(body).hexdigest(),
        "status_counts": dict(status_counts),
    }


def ignored_working_tree_inventory(source_root):
    """Hash every ignored regular file and symlink produced by pinned hooks."""
    source_root = require_real_directory(source_root, "Chromium source")
    raw = run_read_only_git(
        source_root,
        ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
    )
    fields = raw.split(b"\0")
    if not fields or fields[-1] != b"":
        raise PreparationError("Git ignored inventory lacks a NUL terminator")
    records = []
    regular_files = 0
    symlinks = 0
    logical_bytes = 0
    symlink_target_bytes = 0
    for field in fields[:-1]:
        raw_relative = field
        try:
            relative = raw_relative.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PreparationError("Git ignored inventory has a non-UTF-8 path") from exc
        if any(ord(character) < 0x20 for character in relative):
            raise PreparationError("Git ignored path contains a control character")
        relative = safe_relative(relative, "Git ignored")
        target = reject_symlink_ancestors(
            source_root, relative, include_leaf=False
        )
        try:
            before = target.lstat()
        except FileNotFoundError as exc:
            raise PreparationError(
                "Git ignored path disappeared during inventory: {}".format(target)
            ) from exc
        mode = stat.S_IMODE(before.st_mode)
        if stat.S_ISREG(before.st_mode):
            digest = sha256_file(target)
            after = target.lstat()
            identity_before = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
            )
            identity_after = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
            )
            if identity_before != identity_after:
                raise PreparationError(
                    "Git ignored file changed during inventory: {}".format(target)
                )
            regular_files += 1
            logical_bytes += before.st_size
            line = b"\0".join(
                (
                    b"REG",
                    raw_relative,
                    "{:04o}".format(mode).encode("ascii"),
                    str(before.st_size).encode("ascii"),
                    digest.encode("ascii"),
                )
            ) + b"\n"
        elif stat.S_ISLNK(before.st_mode):
            link_target = os.readlink(os.fsencode(target))
            after = target.lstat()
            if (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise PreparationError(
                    "Git ignored symlink changed during inventory: {}".format(target)
                )
            symlinks += 1
            symlink_target_bytes += len(link_target)
            line = b"\0".join(
                (
                    b"SYMLINK",
                    raw_relative,
                    "{:04o}".format(mode).encode("ascii"),
                    str(len(link_target)).encode("ascii"),
                    link_target,
                )
            ) + b"\n"
        else:
            raise PreparationError(
                "Git ignored path is special or a directory: {}".format(target)
            )
        records.append((raw_relative, line))
    if len(records) != len({relative for relative, _ in records}):
        raise PreparationError("Git ignored inventory contains a duplicate path")
    body = b"".join(line for _, line in sorted(records))
    return {
        "records": len(records),
        "regular_files": regular_files,
        "symlinks": symlinks,
        "logical_bytes": logical_bytes,
        "symlink_target_bytes": symlink_target_bytes,
        "path_list_bytes": len(raw),
        "path_list_sha256": hashlib.sha256(raw).hexdigest(),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def expected_ignored_working_tree_inventory():
    """Return the exact ignored hook-output checkpoint accepted for recovery."""
    return {
        "records": RESUME_PATCH_FAILURE_IGNORED_COUNT,
        "regular_files": RESUME_PATCH_FAILURE_IGNORED_REGULAR_FILES,
        "symlinks": RESUME_PATCH_FAILURE_IGNORED_SYMLINKS,
        "logical_bytes": RESUME_PATCH_FAILURE_IGNORED_LOGICAL_BYTES,
        "symlink_target_bytes": RESUME_PATCH_FAILURE_IGNORED_SYMLINK_TARGET_BYTES,
        "path_list_bytes": RESUME_PATCH_FAILURE_IGNORED_PATH_LIST_BYTES,
        "path_list_sha256": RESUME_PATCH_FAILURE_IGNORED_PATH_LIST_SHA256,
        "sha256": RESUME_PATCH_FAILURE_IGNORED_SHA256,
    }


def expected_post_version_working_tree():
    """Return the exact failed resource-phase working-tree checkpoint."""
    return {
        "records": POST_VERSION_STATUS_COUNT,
        "sha256": POST_VERSION_STATUS_SHA256,
        "status_counts": {" D": 3189, " M": 10991, "??": 1897},
    }


def expected_post_version_ignored_tree():
    """Return ignored hook/dependency/overlay state at the failed phase."""
    return {
        "records": POST_VERSION_IGNORED_COUNT,
        "regular_files": POST_VERSION_IGNORED_REGULAR_FILES,
        "symlinks": POST_VERSION_IGNORED_SYMLINKS,
        "logical_bytes": POST_VERSION_IGNORED_LOGICAL_BYTES,
        "symlink_target_bytes": POST_VERSION_IGNORED_SYMLINK_TARGET_BYTES,
        "path_list_bytes": POST_VERSION_IGNORED_PATH_LIST_BYTES,
        "path_list_sha256": POST_VERSION_IGNORED_PATH_LIST_SHA256,
        "sha256": POST_VERSION_IGNORED_SHA256,
    }


def expected_post_version_dependency_tree():
    """Return exact dependency roots after transformations and overlay."""
    return {
        "ownership_roots": list(DEPENDENCY_OWNERSHIP_ROOTS),
        "regular_files": POST_VERSION_DEPENDENCY_REGULAR_FILES,
        "logical_bytes": POST_VERSION_DEPENDENCY_LOGICAL_BYTES,
        "sha256": POST_VERSION_DEPENDENCY_SHA256,
        "installed_symlinks": 0,
        "installed_special_files": 0,
    }


def expected_post_version_resource_inventory():
    """Return the exact pre-copy inventory for all 58 resource targets."""
    return {
        "manifest_entries": POST_VERSION_RESOURCE_INVENTORY_COUNT,
        "copy_targets": POST_VERSION_RESOURCE_INVENTORY_COUNT,
        "inventory_bytes": POST_VERSION_RESOURCE_INVENTORY_BYTES,
        "inventory_sha256": POST_VERSION_RESOURCE_INVENTORY_SHA256,
    }


def validate_recovery_checkpoint_report(
    report,
    source_root=None,
    path_projector: Optional[Callable[[Path], Path]] = None,
):
    """Validate explicit provenance for the one audited split recovery."""
    if report is None:
        return None
    required = {
        "phase",
        "git_head",
        "working_tree",
        "ignored_tree",
        "dependency_tree",
        "pruning",
        "overlay",
        "artifacts",
        "resources",
    }
    if not isinstance(report, dict) or set(report) != required:
        raise PreparationError("recovery checkpoint schema mismatch")
    if (
        report["phase"] != "post_version_pre_resources"
        or report["git_head"] != ACQUISITION_CHROMIUM_COMMIT
        or report["working_tree"] != expected_post_version_working_tree()
        or report["ignored_tree"] != expected_post_version_ignored_tree()
        or report["dependency_tree"] != expected_post_version_dependency_tree()
        or report["pruning"]
        != {
            "manifest_sha256": PRUNING_LIST_SHA256,
            "listed_files": PRUNING_ENTRY_COUNT,
            "all_targets_absent": True,
            "absent_files": PRUNING_ENTRY_COUNT,
            "symlink_targets": 0,
        }
        or report["overlay"]
        != {
            "overlay_files_matching": focus_macos.EXPECTED_FULL_OVERLAY_BODY_COUNT,
            "cleanup_targets_absent": 20,
        }
        or report["resources"] != expected_post_version_resource_inventory()
    ):
        raise PreparationError("recovery checkpoint contract mismatch")
    artifacts = report["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "chrome_version_sha256",
        "focus_version",
        "onboarding_baseline_sha256",
        "upstream_icns_sha256",
        "onboarding_node",
        "args_gn_absent",
        "receipt_absent",
    }:
        raise PreparationError("recovery artifact checkpoint schema mismatch")
    if (
        artifacts["chrome_version_sha256"] != POST_VERSION_CHROME_VERSION_SHA256
        or artifacts["focus_version"] != "1.0.5.0"
        or artifacts["onboarding_baseline_sha256"]
        != ONBOARDING_STRINGS_BASELINE_SHA256
        or artifacts["upstream_icns_sha256"] != POST_VERSION_UPSTREAM_ICNS_SHA256
        or artifacts["args_gn_absent"] is not True
        or artifacts["receipt_absent"] is not True
    ):
        raise PreparationError("recovery artifact checkpoint mismatch")
    if source_root is not None and artifacts["onboarding_node"] != (
        onboarding_node_contract(source_root, path_projector=path_projector)
    ):
        raise PreparationError("recovery onboarding Node checkpoint mismatch")
    return report


def validate_recovery_execution_link(
    execution_report, recovery_checkpoint, path_projector=None
):
    """Bind the split recovery checkpoint to its exact patch-prefix history."""
    validate_preparation_execution_report(
        execution_report, path_projector=path_projector
    )
    validate_recovery_checkpoint_report(recovery_checkpoint)
    if recovery_checkpoint is not None and execution_report != (
        expected_resume_execution_report(
            RESUME_FULL_PATCH_SET_APPLIED, path_projector=path_projector
        )
    ):
        raise PreparationError(
            "post-version recovery requires the exact full-prefix execution report"
        )
    return True


def safe_relative(value, label):
    """Return a normalized POSIX path and reject traversal/alias spellings."""
    if not isinstance(value, str) or value != value.strip() or "\\" in value:
        raise PreparationError("unsafe {} path: {!r}".format(label, value))
    pure = PurePosixPath(value)
    if (
        value in ("", ".")
        or pure.is_absolute()
        or ".." in pure.parts
        or any(part in ("", ".") for part in pure.parts)
    ):
        raise PreparationError("unsafe {} path: {!r}".format(label, value))
    normalized = pure.as_posix()
    if normalized != value:
        raise PreparationError("non-canonical {} path: {!r}".format(label, value))
    return normalized


def require_real_directory(path, label):
    """Require a directory whose supplied path is not itself a symlink."""
    candidate = Path(path).absolute()
    if candidate.is_symlink() or not candidate.is_dir():
        raise PreparationError("{} must be a real directory: {}".format(label, candidate))
    return candidate.resolve()


def reject_symlink_ancestors(root, relative, include_leaf=True):
    """Reject an existing symlink anywhere below root along relative."""
    root = Path(root).resolve()
    pure = PurePosixPath(safe_relative(relative, "tree"))
    cursor = root
    parts = pure.parts if include_leaf else pure.parts[:-1]
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PreparationError("path traverses a symlink: {}".format(cursor))
        if cursor.exists() and not cursor.is_dir() and part != pure.parts[-1]:
            raise PreparationError("path parent is not a directory: {}".format(cursor))
    return root.joinpath(*pure.parts)


def require_regular_in_tree(root, relative, label):
    """Require a non-symlink regular file contained by a real root."""
    root = require_real_directory(root, "{} root".format(label))
    candidate = reject_symlink_ancestors(root, relative)
    if candidate.is_symlink() or not candidate.is_file():
        raise PreparationError("missing regular {}: {}".format(label, candidate))
    try:
        candidate.resolve().relative_to(root)
    except ValueError as exc:
        raise PreparationError("{} escaped its root: {}".format(label, candidate)) from exc
    return candidate


def _project_validated_absolute_path(
    path: Path,
    path_projector: Optional[Callable[[Path], Path]],
    label: str,
) -> str:
    """Project one already-validated physical path without resolving its alias."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise PreparationError("{} physical path is not absolute".format(label))
    if path_projector is None:
        return str(path)
    if not callable(path_projector):
        raise PreparationError("{} path projector is not callable".format(label))
    projected = path_projector(path)
    if not isinstance(projected, Path):
        raise PreparationError("{} path projector must return Path".format(label))
    if not projected.is_absolute() or ".." in projected.parts:
        raise PreparationError(
            "{} path projector returned a non-canonical absolute path".format(label)
        )
    return str(projected)


def validate_acquisition_marker(source_root):
    """Bind preparation to the exact completed pinned acquisition checkout."""
    source_root = require_real_directory(source_root, "Chromium source")
    marker = source_root.parent / ACQUISITION_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise PreparationError(
            "missing regular Chromium acquisition marker: {}".format(marker)
        )
    if marker.stat().st_size <= 0 or marker.stat().st_size > MAX_ACQUISITION_MARKER_BYTES:
        raise PreparationError("Chromium acquisition marker size is invalid")
    try:
        payload = json.loads(
            marker.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError("invalid Chromium acquisition marker JSON") from exc
    if not isinstance(payload, dict):
        raise PreparationError("Chromium acquisition marker must contain a JSON object")
    if payload.get("status") != "acquisition_complete":
        raise PreparationError("Chromium acquisition status is not complete")

    expected_pins = {
        "chromium_version": focus_macos.PINNED_CHROMIUM_VERSION,
        "chromium_tag": focus_macos.PINNED_CHROMIUM_VERSION,
        "chromium_commit": ACQUISITION_CHROMIUM_COMMIT,
        "depot_tools_commit": ACQUISITION_DEPOT_TOOLS_COMMIT,
    }
    pins = payload.get("pins")
    if pins != expected_pins:
        raise PreparationError("Chromium acquisition pins do not match exactly")

    verification = payload.get("verification")
    if not isinstance(verification, dict):
        raise PreparationError("Chromium acquisition marker has no verification")
    expected_verification = {
        "chromium_version": focus_macos.PINNED_CHROMIUM_VERSION,
        "chromium_commit": ACQUISITION_CHROMIUM_COMMIT,
        "depot_tools_commit": ACQUISITION_DEPOT_TOOLS_COMMIT,
        "source_root": str(source_root),
    }
    for name, expected in expected_verification.items():
        if verification.get(name) != expected:
            raise PreparationError(
                "Chromium acquisition verification mismatch for {}".format(name)
            )
    if payload.get("destination") != str(source_root.parent):
        raise PreparationError("Chromium acquisition destination/source_root mismatch")

    gclient = payload.get("gclient")
    if not isinstance(gclient, dict) or (
        gclient.get("target_os") != ["mac"]
        or gclient.get("target_os_only") is not True
        or gclient.get("hooks_during_acquisition") is not False
        or gclient.get("spec_sha256") != ACQUISITION_GCLIENT_SPEC_SHA256
    ):
        raise PreparationError("Chromium acquisition gclient contract mismatch")
    if payload.get("execution_requested") is not True:
        raise PreparationError("Chromium acquisition was not executed")
    return {
        "path": str(marker),
        "sha256": sha256_file(marker),
        "status": payload["status"],
        "chromium_commit": verification["chromium_commit"],
        "depot_tools_commit": verification["depot_tools_commit"],
        "source_root": verification["source_root"],
    }


def validate_tool_bootstrap_marker(source_root, acquisition):
    """Require the exact completed hook/tool bootstrap before source mutation."""
    source_root = require_real_directory(source_root, "Chromium source")
    marker = source_root.parent / TOOL_BOOTSTRAP_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise PreparationError(
            "missing regular macOS tool bootstrap marker: {}".format(marker)
        )
    marker_size = marker.stat().st_size
    if marker_size <= 0 or marker_size > MAX_TOOL_BOOTSTRAP_MARKER_BYTES:
        raise PreparationError("macOS tool bootstrap marker size is invalid")
    try:
        payload = json.loads(
            marker.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError("invalid macOS tool bootstrap marker JSON") from exc
    if not isinstance(payload, dict) or set(payload) != TOOL_BOOTSTRAP_KEYS:
        raise PreparationError("macOS tool bootstrap marker schema mismatch")
    if payload["schema"] != 1 or payload["hooks_complete"] is not True:
        raise PreparationError("macOS tool bootstrap is incomplete")
    if payload["source_root"] != str(source_root):
        raise PreparationError("macOS tool bootstrap source_root mismatch")
    developer_value = payload["developer_dir"]
    if not isinstance(developer_value, str) or not developer_value:
        raise PreparationError("macOS tool bootstrap developer_dir is invalid")
    developer_dir = Path(developer_value)
    if (
        not developer_dir.is_absolute()
        or developer_dir.name != "Developer"
        or developer_dir.parent.name != "Contents"
        or developer_dir.parent.parent.suffix != ".app"
        or not developer_dir.parent.parent.name.startswith("Xcode")
    ):
        raise PreparationError(
            "macOS tool bootstrap developer_dir must be an absolute "
            "Xcode.app/Contents/Developer path"
        )
    try:
        resolved_developer = developer_dir.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PreparationError(
            "macOS tool bootstrap developer_dir does not exist"
        ) from exc
    if (
        developer_dir.is_symlink()
        or not developer_dir.is_dir()
        or resolved_developer != developer_dir
    ):
        raise PreparationError(
            "macOS tool bootstrap developer_dir must be a real canonical directory"
        )
    if payload["chromium_commit"] != ACQUISITION_CHROMIUM_COMMIT:
        raise PreparationError("macOS tool bootstrap Chromium commit mismatch")
    if payload["depot_tools_commit"] != ACQUISITION_DEPOT_TOOLS_COMMIT:
        raise PreparationError("macOS tool bootstrap depot_tools commit mismatch")
    if payload["acquisition_marker_sha256"] != acquisition["sha256"]:
        raise PreparationError("macOS tool bootstrap acquisition marker mismatch")
    expected_command = [str(source_root.parent / "depot_tools" / "gclient"), "runhooks"]
    if payload["gclient_command"] != expected_command:
        raise PreparationError("macOS tool bootstrap gclient command mismatch")
    tool_hashes = payload["tool_sha256"]
    expected_tool_names = ("gclient", "gn", "autoninja")
    if not isinstance(tool_hashes, dict) or set(tool_hashes) != set(expected_tool_names):
        raise PreparationError("macOS tool bootstrap tool_sha256 schema mismatch")
    depot_tools = require_real_directory(
        source_root.parent / "depot_tools", "bootstrapped depot_tools"
    )
    for name in expected_tool_names:
        expected_hash = tool_hashes[name]
        if not isinstance(expected_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_hash
        ):
            raise PreparationError(
                "macOS tool bootstrap {} SHA-256 is invalid".format(name)
            )
        tool = require_regular_in_tree(depot_tools, name, "bootstrapped tool")
        if not os.access(tool, os.X_OK):
            raise PreparationError(
                "macOS bootstrapped tool is not a regular executable: {}".format(tool)
            )
        if sha256_file(tool) != expected_hash:
            raise PreparationError(
                "macOS bootstrapped tool hash changed: {}".format(name)
            )
    gn_version = payload["gn_version"]
    if (
        not isinstance(gn_version, str)
        or not gn_version
        or len(gn_version) > 128
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in gn_version)
    ):
        raise PreparationError("macOS tool bootstrap GN version is invalid")
    post_hooks_free = payload["post_hooks_free_bytes"]
    if (
        type(post_hooks_free) is not int
        or post_hooks_free < BOOTSTRAP_POST_FREE_GIB * GIB
    ):
        raise PreparationError("macOS tool bootstrap post-hooks disk gate is invalid")
    if payload["build_executed"] is not False:
        raise PreparationError("macOS tool bootstrap already reports a build")
    return {
        "path": str(marker),
        "sha256": sha256_file(marker),
        "schema": payload["schema"],
        "hooks_complete": payload["hooks_complete"],
        "source_root": payload["source_root"],
        "developer_dir": str(resolved_developer),
        "chromium_commit": payload["chromium_commit"],
        "depot_tools_commit": payload["depot_tools_commit"],
        "acquisition_marker_sha256": payload["acquisition_marker_sha256"],
        "gn_version": gn_version,
        "tool_sha256": tool_hashes,
        "post_hooks_free_bytes": post_hooks_free,
        "build_executed": payload["build_executed"],
    }


def validate_dependency_manifest():
    """Pin deps.ini plus the Mac-only host archives needed by the build."""
    if DEPS_INI.is_symlink() or not DEPS_INI.is_file():
        raise PreparationError("missing regular dependency manifest: {}".format(DEPS_INI))
    actual_hash = sha256_file(DEPS_INI)
    if actual_hash != DEPS_INI_SHA256:
        raise PreparationError(
            "deps.ini hash mismatch: expected {}, got {}".format(DEPS_INI_SHA256, actual_hash)
        )
    parser = configparser.ConfigParser()
    parser.read(DEPS_INI, encoding="utf-8")
    if parser.sections() != list(SHARED_DEPENDENCY_CONTRACTS):
        raise PreparationError("deps.ini component order/inventory changed")
    for name, expected in SHARED_DEPENDENCY_CONTRACTS.items():
        section = parser[name]
        observed = {
            "download_filename": section.get("download_filename"),
            "sha256": section.get("sha256"),
            "output_path": safe_relative(
                section.get("output_path", "").lstrip("./"),
                "dependency output",
            ),
            "strip_leading_dirs": section.get("strip_leading_dirs"),
        }
        for key in ("download_filename", "sha256", "output_path", "strip_leading_dirs"):
            if observed[key] != expected[key]:
                raise PreparationError(
                    "dependency {} {} changed: expected {!r}, got {!r}".format(
                        name, key, expected[key], observed[key]
                    )
                )
    return DEPENDENCY_CONTRACTS


def validate_offline_cache(cache_root, contracts=None):
    """Verify every declared dependency in an existing cache; never retrieve it."""
    cache = require_real_directory(cache_root, "offline cache")
    contracts = contracts or validate_dependency_manifest()
    report = []
    for name, contract in contracts.items():
        filename = safe_relative(contract["download_filename"], "cache filename")
        if len(PurePosixPath(filename).parts) != 1:
            raise PreparationError(
                "cache filename must not contain directories: {}".format(filename)
            )
        archive = require_regular_in_tree(cache, filename, "cached dependency")
        actual_hash = sha256_file(archive)
        if actual_hash != contract["sha256"]:
            raise PreparationError(
                "cached dependency hash mismatch for {}: expected {}, got {}".format(
                    name, contract["sha256"], actual_hash
                )
            )
        report.append(
            {
                "name": name,
                "archive": str(archive),
                "bytes": archive.stat().st_size,
                "sha256": actual_hash,
            }
        )
    return cache, report


def validate_dependency_cache_marker(
    cache_root,
    contracts=None,
    path_projector: Optional[Callable[[Path], Path]] = None,
):
    """Bind the additive Mac cache to the exact complete 10-archive inventory."""
    cache = require_real_directory(cache_root, "offline cache")
    contracts = contracts or validate_dependency_manifest()
    marker = cache / DEPENDENCY_CACHE_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise PreparationError("missing regular dependency cache marker: {}".format(marker))
    if marker.stat().st_size <= 0 or marker.stat().st_size > MAX_DEPENDENCY_CACHE_MARKER_BYTES:
        raise PreparationError("dependency cache marker size is invalid")
    try:
        payload = json.loads(
            marker.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError("invalid dependency cache marker JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "archives",
        "deps_ini_sha256",
        "source_mutated",
        "unpacked",
    }:
        raise PreparationError("dependency cache marker schema mismatch")
    if (
        payload["deps_ini_sha256"] != DEPS_INI_SHA256
        or payload["source_mutated"] is not False
        or payload["unpacked"] is not False
    ):
        raise PreparationError("dependency cache marker safety contract mismatch")
    validated_archives = []
    total_bytes = 0
    allowed_entries = {DEPENDENCY_CACHE_MARKER}
    for name, contract in contracts.items():
        archive = require_regular_in_tree(
            cache, contract["download_filename"], "cached dependency"
        )
        allowed_entries.add(contract["download_filename"])
        size = archive.stat().st_size
        total_bytes += size
        observed_hash = sha256_file(archive)
        if observed_hash != contract["sha256"]:
            raise PreparationError(
                "dependency cache archive hash changed: {}".format(archive)
            )
        validated_archives.append(
            {
                "bytes": size,
                "name": name,
                "path": archive,
                "sha256": contract["sha256"],
            }
        )
    observed_entries = set()
    for entry in cache.iterdir():
        if entry.is_symlink() or not entry.is_file():
            raise PreparationError(
                "dependency cache contains a non-regular entry: {}".format(entry)
            )
        observed_entries.add(entry.name)
    if observed_entries != allowed_entries:
        raise PreparationError("dependency cache contains an unexpected or partial file")
    marker_hash = sha256_file(marker)
    expected_archives = [
        {
            **archive,
            "path": _project_validated_absolute_path(
                archive["path"], path_projector, "dependency cache archive"
            ),
        }
        for archive in validated_archives
    ]
    if payload["archives"] != expected_archives:
        raise PreparationError("dependency cache marker archive inventory mismatch")
    return {
        "path": _project_validated_absolute_path(
            marker, path_projector, "dependency cache marker"
        ),
        "sha256": marker_hash,
        "archive_count": len(expected_archives),
        "total_bytes": total_bytes,
        "archives": {
            name: contract["sha256"] for name, contract in contracts.items()
        },
    }


def _stripped_archive_path(name, prefix, label):
    raw = name.rstrip("/")
    # Reproducible tar tools conventionally prefix every member with exactly
    # "./" and include a root directory marker "./". Normalize only those
    # leading current-directory components before applying the strict path
    # grammar; absolute paths and any ".." remain forbidden.
    while raw.startswith("./"):
        raw = raw[2:]
    if raw in ("", "."):
        return None
    normalized = safe_relative(raw, label)
    pure = PurePosixPath(normalized)
    if prefix is None:
        return pure
    prefix_path = PurePosixPath(safe_relative(prefix, "archive strip prefix"))
    try:
        relative = pure.relative_to(prefix_path)
    except ValueError as exc:
        raise PreparationError(
            "archive member is outside required prefix {}: {}".format(prefix, name)
        ) from exc
    if not relative.parts:
        return None
    return relative


def _normalized_link_target(member_path, linkname):
    """Resolve an archive link target without permitting an archive-root escape."""
    if not isinstance(linkname, str) or not linkname or "\\" in linkname:
        raise PreparationError("tar link target is invalid")
    link = PurePosixPath(linkname)
    if link.is_absolute():
        raise PreparationError("tar link target must be relative")
    combined = posixpath.normpath(
        posixpath.join(PurePosixPath(member_path).parent.as_posix(), linkname)
    )
    if combined in ("", ".") or combined == ".." or combined.startswith("../"):
        raise PreparationError("tar link target escapes archive root")
    return safe_relative(combined, "tar link target")


def _tar_member_inventory(stream):
    """Build a duplicate-free normalized inventory for safe link resolution."""
    inventory = {}
    for member in stream.getmembers():
        relative = _stripped_archive_path(member.name, None, "tar member")
        if relative is None:
            if not member.isdir():
                raise PreparationError("tar non-directory resolves to archive root")
            continue
        name = relative.as_posix()
        if name in inventory:
            raise PreparationError("duplicate normalized tar member: {}".format(name))
        inventory[name] = member
    return inventory


def _resolve_materialized_tar_member(
    member_path, inventory, prefix=None, stack=None
):
    """Resolve an approved in-node_modules link to its regular-file payload."""
    stack = set() if stack is None else set(stack)
    if member_path in stack:
        raise PreparationError("tar link cycle at {}".format(member_path))
    stack.add(member_path)
    member = inventory.get(member_path)
    if member is None:
        raise PreparationError("tar link target is missing: {}".format(member_path))
    if member.isfile():
        return member
    destination = _stripped_archive_path(
        member_path, prefix, "tar link destination"
    )
    if destination is None:
        raise PreparationError("tar link destination resolves to archive root")
    if member.issym():
        if not (
            destination.parts
            and destination.parts[0] == "node_modules"
            and ".bin" in destination.parts
        ):
            raise PreparationError(
                "tar symbolic link is outside approved node_modules/.bin: {}".format(
                    member.name
                )
            )
        target = _normalized_link_target(member_path, member.linkname)
    elif member.islnk():
        if not (destination.parts and destination.parts[0] == "node_modules"):
            raise PreparationError(
                "tar hard link is outside node_modules: {}".format(member.name)
            )
        target_path = _stripped_archive_path(
            member.linkname, None, "tar hard-link target"
        )
        if target_path is None:
            raise PreparationError("tar hard link targets archive root")
        target = target_path.as_posix()
    else:
        raise PreparationError("tar member is not materializable: {}".format(member.name))
    logical_target = _stripped_archive_path(target, prefix, "tar link target")
    if logical_target is None or not (
        logical_target.parts and logical_target.parts[0] == "node_modules"
    ):
        raise PreparationError("tar link target leaves node_modules: {}".format(target))
    return _resolve_materialized_tar_member(
        target, inventory, prefix=prefix, stack=stack
    )


def inspect_archive(archive, contract):
    """Return safe regular archive members and reject links/special files."""
    archive = Path(archive)
    prefix = contract.get("strip_leading_dirs")
    entries = []
    seen = set()
    if contract["kind"] == "zip":
        with zipfile.ZipFile(archive, "r") as stream:
            for member in stream.infolist():
                if not member.filename:
                    raise PreparationError("ZIP contains an empty member name")
                relative = _stripped_archive_path(member.filename, prefix, "ZIP member")
                mode = member.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if member.is_dir():
                    if kind not in (0, stat.S_IFDIR):
                        raise PreparationError(
                            "ZIP directory has an unsafe file type: {}".format(member.filename)
                        )
                    continue
                if kind not in (0, stat.S_IFREG):
                    raise PreparationError(
                        "ZIP member is not a regular file: {}".format(member.filename)
                    )
                if relative is None:
                    raise PreparationError("ZIP regular file resolves to archive root")
                value = relative.as_posix()
                if value in seen:
                    raise PreparationError("duplicate archive destination: {}".format(value))
                seen.add(value)
                entries.append((value, member.filename, member.file_size, mode & 0o777))
    elif contract["kind"] == "tar":
        with tarfile.open(archive, "r:*") as stream:
            inventory = _tar_member_inventory(stream)
            omitted_symlinks = []
            for member in stream.getmembers():
                if member.isdir():
                    _stripped_archive_path(member.name, prefix, "tar member")
                    continue
                relative = _stripped_archive_path(member.name, prefix, "tar member")
                if relative is None:
                    raise PreparationError("tar regular file resolves to archive root")
                normalized_member = _stripped_archive_path(
                    member.name, None, "tar member"
                ).as_posix()
                payload_member = _resolve_materialized_tar_member(
                    normalized_member, inventory, prefix=prefix
                )
                # npm's node_modules/.bin symbolic links are developer shims.
                # Copying their target bytes changes relative-import semantics;
                # the Chromium GN action invokes Vite's real script directly.
                # Validate each link and target above, then intentionally omit it.
                if member.issym():
                    omitted_symlinks.append(
                        "{}\t{}".format(relative.as_posix(), member.linkname)
                    )
                    continue
                value = relative.as_posix()
                if value in seen:
                    raise PreparationError("duplicate archive destination: {}".format(value))
                seen.add(value)
                entries.append(
                    (
                        value,
                        payload_member.name,
                        payload_member.size,
                        payload_member.mode & 0o777,
                    )
                )
            expected_omitted_count = contract.get("omitted_symlink_count", 0)
            expected_omitted_hash = contract.get(
                "omitted_symlink_sha256", hashlib.sha256(b"").hexdigest()
            )
            if (
                type(expected_omitted_count) is not int
                or expected_omitted_count < 0
                or not isinstance(expected_omitted_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_omitted_hash)
            ):
                raise PreparationError("tar omitted-symlink contract is invalid")
            omitted_body = "".join(
                "{}\n".format(value) for value in omitted_symlinks
            ).encode("utf-8")
            omitted_hash = hashlib.sha256(omitted_body).hexdigest()
            if (
                len(omitted_symlinks) != expected_omitted_count
                or omitted_hash != expected_omitted_hash
            ):
                raise PreparationError(
                    "tar omitted-symlink inventory mismatch: expected {} / {}, "
                    "got {} / {}".format(
                        expected_omitted_count,
                        expected_omitted_hash,
                        len(omitted_symlinks),
                        omitted_hash,
                    )
                )
    else:
        raise PreparationError("unsupported dependency archive kind: {}".format(contract["kind"]))
    if not entries:
        raise PreparationError("dependency archive contains no regular files: {}".format(archive))
    return entries


def extract_archive_to_stage(archive, contract, destination):
    """Extract only members previously accepted by inspect_archive."""
    entries = inspect_archive(archive, contract)
    destination.mkdir(parents=True, exist_ok=False)
    if contract["kind"] == "zip":
        with zipfile.ZipFile(archive, "r") as stream:
            for relative, member_name, expected_size, mode in entries:
                target = destination / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream.open(member_name, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                if target.stat().st_size != expected_size:
                    raise PreparationError("short ZIP extraction for {}".format(member_name))
                target.chmod(mode or 0o644)
    else:
        with tarfile.open(archive, "r:*") as stream:
            for relative, member_name, expected_size, mode in entries:
                target = destination / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = stream.extractfile(member_name)
                if source is None:
                    raise PreparationError("could not read tar member {}".format(member_name))
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                if target.stat().st_size != expected_size:
                    raise PreparationError("short tar extraction for {}".format(member_name))
                target.chmod(mode or 0o644)
    return entries


def atomic_copy(source, destination):
    """Atomically replace one regular destination without following symlinks."""
    source = Path(source)
    destination = Path(destination)
    if source.is_symlink() or not source.is_file():
        raise PreparationError("copy source is not a regular file: {}".format(source))
    if destination.is_symlink():
        raise PreparationError("refusing to overwrite destination symlink: {}".format(destination))
    if destination.exists() and not destination.is_file():
        raise PreparationError("copy destination is not a regular file: {}".format(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".focus-copy-", dir=str(destination.parent))
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(source.stat().st_mode & 0o777)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_publish_text(destination, text):
    """Publish complete UTF-8 text once without overwriting the final path."""
    destination = Path(destination)
    if destination.is_symlink() or destination.exists():
        raise PreparationError("refusing to overwrite published file: {}".format(destination))
    parent = require_real_directory(destination.parent, "publication directory")
    handle, temporary_name = tempfile.mkstemp(
        prefix=".{}-".format(destination.name), suffix=".tmp", dir=str(parent)
    )
    temporary = Path(temporary_name)
    published = False
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise PreparationError(
                "refusing to overwrite published file: {}".format(destination)
            ) from exc
        published = True
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        temporary.unlink()
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()
    if not published or destination.is_symlink() or not destination.is_file():
        raise PreparationError("atomic publication failed: {}".format(destination))
    return destination


def dependency_output_roots(contracts):
    """Return the stable, non-overlapping roots owned entirely by the archives."""
    declared = [
        PurePosixPath(safe_relative(value["output_path"], "dependency output"))
        for value in contracts.values()
    ]
    roots = []
    for candidate in declared:
        if any(candidate.parts[: len(root.parts)] == root.parts for root in roots):
            continue
        roots = [
            root
            for root in roots
            if root.parts[: len(candidate.parts)] != candidate.parts
        ]
        roots.append(candidate)
    return tuple(root.as_posix() for root in roots)


def require_empty_dependency_roots(source_root, contracts=None):
    """Require archive-owned roots to be absent or real and completely empty."""
    source_root = require_real_directory(source_root, "Chromium source")
    contracts = contracts or DEPENDENCY_CONTRACTS
    roots = dependency_output_roots(contracts)
    for relative_root in roots:
        root = reject_symlink_ancestors(source_root, relative_root)
        if not root.exists() and not root.is_symlink():
            continue
        if root.is_symlink() or not root.is_dir():
            raise PreparationError("unsafe dependency output root: {}".format(root))
        if any(root.iterdir()):
            raise PreparationError(
                "dependency output root is not empty before merge: {}".format(root)
            )
    return roots


def _dependency_inventory_report(roots, directories, files):
    implied_directories = set(roots)
    for relative in files:
        parent = PurePosixPath(relative).parent
        while parent.as_posix() not in ("", "."):
            implied_directories.add(parent.as_posix())
            if parent.as_posix() in roots:
                break
            parent = parent.parent
    if directories != implied_directories:
        raise PreparationError("installed dependency directory inventory mismatch")
    lines = []
    total_bytes = 0
    for relative in sorted(files):
        entry = files[relative]
        total_bytes += entry["bytes"]
        lines.append(
            "{}\0{:04o}\0{}\0{}\n".format(
                relative, entry["mode"], entry["bytes"], entry["sha256"]
            )
        )
    body = "".join(lines).encode("utf-8")
    return {
        "ownership_roots": list(roots),
        "regular_files": len(files),
        "logical_bytes": total_bytes,
        "sha256": hashlib.sha256(body).hexdigest(),
        "installed_symlinks": 0,
        "installed_special_files": 0,
    }


def installed_dependency_tree(source_root, contracts=None):
    """Hash every directory and regular file under the archive-owned roots."""
    source_root = require_real_directory(source_root, "Chromium source")
    contracts = contracts or DEPENDENCY_CONTRACTS
    roots = dependency_output_roots(contracts)
    directories = set()
    files = {}
    for relative_root in roots:
        root = reject_symlink_ancestors(source_root, relative_root)
        if root.is_symlink() or not root.is_dir():
            raise PreparationError(
                "missing real installed dependency root: {}".format(root)
            )
        directories.add(relative_root)
        for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            for name in sorted(dirnames):
                path = current_path / name
                if path.is_symlink() or not path.is_dir():
                    raise PreparationError(
                        "installed dependency contains unsafe directory: {}".format(path)
                    )
                directories.add(path.relative_to(source_root).as_posix())
            for name in sorted(filenames):
                path = current_path / name
                if path.is_symlink() or not path.is_file():
                    raise PreparationError(
                        "installed dependency contains unsafe file: {}".format(path)
                    )
                relative = path.relative_to(source_root).as_posix()
                metadata = path.stat()
                files[relative] = {
                    "mode": stat.S_IMODE(metadata.st_mode),
                    "bytes": metadata.st_size,
                    "sha256": sha256_file(path),
                }
    return _dependency_inventory_report(roots, directories, files)


def expected_dependency_install_report():
    """Return the exact immutable report produced by the ten-archive merge."""
    return {
        "ownership_roots": list(DEPENDENCY_OWNERSHIP_ROOTS),
        "regular_files": DEPENDENCY_INSTALL_REGULAR_FILES,
        "logical_bytes": DEPENDENCY_INSTALL_LOGICAL_BYTES,
        "sha256": DEPENDENCY_INSTALL_SHA256,
        "installed_symlinks": 0,
        "installed_special_files": 0,
        "files_copied": DEPENDENCY_INSTALL_REGULAR_FILES,
        "components": list(DEPENDENCY_CONTRACTS),
        "omitted_symlinks": {
            "onboarding": {
                "count": SHARED_DEPENDENCY_CONTRACTS["onboarding"][
                    "omitted_symlink_count"
                ],
                "sha256": SHARED_DEPENDENCY_CONTRACTS["onboarding"][
                    "omitted_symlink_sha256"
                ],
            }
        },
    }


def expected_installed_dependency_tree_report():
    """Return the immutable six-root tree report without merge-only metadata."""
    install = expected_dependency_install_report()
    return {
        key: install[key]
        for key in (
            "ownership_roots",
            "regular_files",
            "logical_bytes",
            "sha256",
            "installed_symlinks",
            "installed_special_files",
        )
    }


def expected_resume_working_tree(applied_patches):
    """Return the exact canonical Git status checkpoint for an audited prefix."""
    if applied_patches == RESUME_PATCH_FAILURE_APPLIED:
        return {
            "records": RESUME_PATCH_FAILURE_STATUS_COUNT,
            "sha256": RESUME_PATCH_FAILURE_STATUS_SHA256,
            "status_counts": {" D": 3189, " M": 680, "??": 804},
        }
    if applied_patches == RESUME_SECOND_PATCH_FAILURE_APPLIED:
        return {
            "records": RESUME_SECOND_PATCH_FAILURE_STATUS_COUNT,
            "sha256": RESUME_SECOND_PATCH_FAILURE_STATUS_SHA256,
            "status_counts": {" D": 3189, " M": 766, "??": 825},
        }
    if applied_patches == RESUME_FULL_PATCH_SET_APPLIED:
        return {
            "records": RESUME_FULL_PATCH_SET_STATUS_COUNT,
            "sha256": RESUME_FULL_PATCH_SET_STATUS_SHA256,
            "status_counts": {" M": 1219, " D": 3189, "??": 885},
        }
    raise PreparationError(
        "resume accepts only audited patch checkpoints: {}".format(
            ", ".join(str(value) for value in RESUME_AUDITED_PATCH_CHECKPOINTS)
        )
    )


def expected_resume_dependency_tree(applied_patches):
    """Return the exact dependency-root tree at an audited patch prefix."""
    if applied_patches == RESUME_PATCH_FAILURE_APPLIED:
        return expected_installed_dependency_tree_report()
    if applied_patches == RESUME_SECOND_PATCH_FAILURE_APPLIED:
        return {
            "ownership_roots": list(DEPENDENCY_OWNERSHIP_ROOTS),
            "regular_files": RESUME_SECOND_DEPENDENCY_REGULAR_FILES,
            "logical_bytes": RESUME_SECOND_DEPENDENCY_LOGICAL_BYTES,
            "sha256": RESUME_SECOND_DEPENDENCY_SHA256,
            "installed_symlinks": 0,
            "installed_special_files": 0,
        }
    if applied_patches == RESUME_FULL_PATCH_SET_APPLIED:
        return {
            "ownership_roots": list(DEPENDENCY_OWNERSHIP_ROOTS),
            "regular_files": RESUME_FULL_DEPENDENCY_REGULAR_FILES,
            "logical_bytes": RESUME_FULL_DEPENDENCY_LOGICAL_BYTES,
            "sha256": RESUME_FULL_DEPENDENCY_SHA256,
            "installed_symlinks": 0,
            "installed_special_files": 0,
        }
    expected_resume_working_tree(applied_patches)
    raise AssertionError("unreachable")


def merge_staged_dependencies(source_root, stage_root, contracts):
    """Install a deterministic archive tree into initially empty owned roots."""
    source_root = require_real_directory(source_root, "Chromium source")
    roots = require_empty_dependency_roots(source_root, contracts)

    plan = []
    destinations = set()
    destination_parents = set()
    expected_directories = set(roots)
    expected_files = {}
    for name, contract in contracts.items():
        staged = require_real_directory(Path(stage_root) / name, "staged dependency")
        output = safe_relative(contract["output_path"], "dependency output")
        for source in sorted(staged.rglob("*")):
            if source.is_symlink():
                raise PreparationError("staged dependency contains symlink: {}".format(source))
            if source.is_dir():
                continue
            if not source.is_file():
                raise PreparationError("staged dependency contains special file: {}".format(source))
            relative = source.relative_to(staged).as_posix()
            destination_relative = PurePosixPath(output, relative).as_posix()
            candidate = PurePosixPath(destination_relative)
            if destination_relative in destinations or any(
                parent.as_posix() in destinations for parent in candidate.parents
            ) or destination_relative in destination_parents:
                raise PreparationError(
                    "duplicate or colliding dependency destination: {}".format(
                        destination_relative
                    )
                )
            destinations.add(destination_relative)
            destination_parents.update(
                parent.as_posix()
                for parent in candidate.parents
                if parent.as_posix() not in ("", ".")
            )
            destination = reject_symlink_ancestors(
                source_root, destination_relative, include_leaf=False
            )
            if destination.is_symlink() or destination.exists():
                raise PreparationError(
                    "dependency destination exists before merge: {}".format(destination)
                )
            metadata = source.stat()
            expected_files[destination_relative] = {
                "mode": stat.S_IMODE(metadata.st_mode),
                "bytes": metadata.st_size,
                "sha256": sha256_file(source),
            }
            parent = PurePosixPath(destination_relative).parent
            while parent.as_posix() not in ("", "."):
                expected_directories.add(parent.as_posix())
                if parent.as_posix() in roots:
                    break
                parent = parent.parent
            plan.append((name, source, destination))
    expected = _dependency_inventory_report(
        roots, expected_directories, expected_files
    )
    if list(contracts) == list(DEPENDENCY_CONTRACTS) and expected != {
        "ownership_roots": list(DEPENDENCY_OWNERSHIP_ROOTS),
        "regular_files": DEPENDENCY_INSTALL_REGULAR_FILES,
        "logical_bytes": DEPENDENCY_INSTALL_LOGICAL_BYTES,
        "sha256": DEPENDENCY_INSTALL_SHA256,
        "installed_symlinks": 0,
        "installed_special_files": 0,
    }:
        raise PreparationError("staged dependency inventory does not match pinned union")
    for _, source, destination in plan:
        atomic_copy(source, destination)
    observed = installed_dependency_tree(source_root, contracts)
    if observed != expected:
        raise PreparationError("installed dependency tree inventory mismatch")
    return {
        **observed,
        "files_copied": len(plan),
        "components": list(contracts),
        "omitted_symlinks": {
            "onboarding": {
                "count": SHARED_DEPENDENCY_CONTRACTS["onboarding"][
                    "omitted_symlink_count"
                ],
                "sha256": SHARED_DEPENDENCY_CONTRACTS["onboarding"][
                    "omitted_symlink_sha256"
                ],
            }
        },
    }


def onboarding_node_contract(
    source_root,
    path_projector: Optional[Callable[[Path], Path]] = None,
):
    """Validate the exact native Node used for deterministic source generation."""
    source_root = require_real_directory(source_root, "Chromium source")
    machine = platform.machine().lower()
    if machine == "aarch64":
        machine = "arm64"
    elif machine == "amd64":
        machine = "x86_64"
    if machine not in ONBOARDING_NODE_RELATIVE_BY_HOST:
        raise PreparationError(
            "unsupported Mac host architecture for onboarding Node: {}".format(machine)
        )
    relative = ONBOARDING_NODE_RELATIVE_BY_HOST[machine]
    node = require_regular_in_tree(source_root, relative, "onboarding Node")
    if not os.access(node, os.X_OK):
        raise PreparationError("onboarding Node is not executable")
    observed_hash = sha256_file(node)
    if observed_hash != ONBOARDING_NODE_SHA256_BY_HOST[machine]:
        raise PreparationError("onboarding Node hash mismatch")
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
    architecture = subprocess.run(
        ["/usr/bin/lipo", "-archs", str(node)],
        cwd=str(source_root),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if architecture.returncode or architecture.stdout.strip().split() != [machine]:
        raise PreparationError("onboarding Node architecture mismatch")
    version = subprocess.run(
        [str(node), "--version"],
        cwd=str(source_root),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if version.returncode or version.stdout.strip() != ONBOARDING_NODE_VERSION:
        raise PreparationError("onboarding Node version mismatch")
    return {
        "path": _project_validated_absolute_path(
            node, path_projector, "onboarding Node"
        ),
        "relative_path": relative,
        "architecture": machine,
        "version": ONBOARDING_NODE_VERSION,
        "sha256": observed_hash,
    }


def generate_onboarding_strings(source_root, runner=subprocess.run):
    """Generate strings.ts twice and require byte-identical offline output."""
    source_root = require_real_directory(source_root, "Chromium source")
    node = onboarding_node_contract(source_root)
    generator = require_regular_in_tree(
        source_root, ONBOARDING_GENERATOR, "onboarding i18n generator"
    )
    if sha256_file(generator) != ONBOARDING_GENERATOR_SHA256:
        raise PreparationError("onboarding i18n generator hash mismatch")
    output = reject_symlink_ancestors(
        source_root, ONBOARDING_STRINGS_OUTPUT, include_leaf=False
    )
    baseline = require_regular_in_tree(
        source_root, ONBOARDING_STRINGS_OUTPUT, "overlay onboarding strings baseline"
    )
    baseline_hash = sha256_file(baseline)
    if (
        baseline.stat().st_size != ONBOARDING_STRINGS_BASELINE_BYTES
        or baseline_hash != ONBOARDING_STRINGS_BASELINE_SHA256
    ):
        raise PreparationError("overlay onboarding strings baseline mismatch")
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "NO_COLOR": "1",
    }
    command = [node["path"], str(generator)]
    hashes = []
    for _ in range(2):
        result = runner(
            command,
            cwd=str(generator.parent.parent),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()[-2000:]
            raise PreparationError(
                "onboarding strings generation failed: {}".format(detail)
            )
        generated = require_regular_in_tree(
            source_root, ONBOARDING_STRINGS_OUTPUT, "generated onboarding strings"
        )
        if generated.stat().st_size <= 0:
            raise PreparationError("generated onboarding strings are empty")
        hashes.append(sha256_file(generated))
    if hashes != [baseline_hash, baseline_hash]:
        raise PreparationError(
            "onboarding strings generator differs from its pinned overlay baseline"
        )
    return {
        "generator": ONBOARDING_GENERATOR,
        "generator_sha256": ONBOARDING_GENERATOR_SHA256,
        "node": node,
        "output": ONBOARDING_STRINGS_OUTPUT,
        "baseline_bytes": ONBOARDING_STRINGS_BASELINE_BYTES,
        "baseline_sha256": ONBOARDING_STRINGS_BASELINE_SHA256,
        "output_bytes": output.stat().st_size,
        "output_sha256": hashes[0],
        "runs": 2,
        "byte_identical": True,
        "network_operations": 0,
    }


def load_expected_absent_pruning(path=PRUNING_ALREADY_ABSENT_LIST):
    """Load the exact host-conditional pruning entries absent from the Mac checkout."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PreparationError(
            "expected-absent pruning list is not a regular file: {}".format(path)
        )
    actual_hash = sha256_file(path)
    if actual_hash != PRUNING_ALREADY_ABSENT_SHA256:
        raise PreparationError(
            "expected-absent pruning hash mismatch: expected {}, got {}".format(
                PRUNING_ALREADY_ABSENT_SHA256, actual_hash
            )
        )
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line != line.strip() or line.startswith("#"):
            raise PreparationError(
                "invalid expected-absent pruning entry at {}:{}: {!r}".format(
                    path, number, line
                )
            )
        entries.append(safe_relative(line, "expected-absent pruning entry"))
    if len(entries) != PRUNING_ALREADY_ABSENT_COUNT:
        raise PreparationError(
            "expected-absent pruning count mismatch: expected {}, got {}".format(
                PRUNING_ALREADY_ABSENT_COUNT, len(entries)
            )
        )
    if len(entries) != len(set(entries)):
        raise PreparationError("duplicate expected-absent pruning entry")
    return tuple(entries)


def build_prune_plan(
    source_root,
    manifest=PRUNING_LIST,
    expected_hash=PRUNING_LIST_SHA256,
    expected_count=PRUNING_ENTRY_COUNT,
    allowed_missing=None,
    expected_absent_paths=None,
):
    """Prevalidate the exact file-only binary pruning plan and absence set."""
    source_root = require_real_directory(source_root, "Chromium source")
    manifest = Path(manifest)
    if manifest.is_symlink() or not manifest.is_file():
        raise PreparationError("pruning manifest is not a regular file: {}".format(manifest))
    actual_hash = sha256_file(manifest)
    if actual_hash != expected_hash:
        raise PreparationError(
            "pruning.list hash mismatch: expected {}, got {}".format(
                expected_hash, actual_hash
            )
        )
    allowed_missing = {
        safe_relative(value, "future archive pruning target")
        for value in (allowed_missing or ())
    }
    expected_absent = tuple(
        safe_relative(value, "expected absent pruning target")
        for value in (expected_absent_paths or ())
    )
    if len(expected_absent) != len(set(expected_absent)):
        raise PreparationError("duplicate expected absent pruning target")
    expected_absent_set = set(expected_absent)
    overlap = expected_absent_set.intersection(allowed_missing)
    if overlap:
        raise PreparationError(
            "pruning targets cannot be both future and already absent: {}".format(
                sorted(overlap)[0]
            )
        )
    entries = []
    absent = []
    seen = set()
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        if line != line.strip() or line.startswith("#"):
            raise PreparationError(
                "invalid pruning entry at {}:{}: {!r}".format(manifest, number, line)
            )
        relative = safe_relative(line, "pruning entry {}".format(number))
        if relative in seen:
            raise PreparationError("duplicate pruning entry: {}".format(relative))
        seen.add(relative)
        target = reject_symlink_ancestors(source_root, relative)
        if target.is_symlink():
            raise PreparationError("pruning target is a symlink: {}".format(target))
        if not target.exists():
            future_archive_file = relative in allowed_missing
            already_absent = relative in expected_absent_set
            if not future_archive_file and not already_absent:
                raise PreparationError(
                    "missing regular pruning target: {}".format(target)
                )
            if already_absent:
                absent.append(relative)
            entries.append(
                {
                    "relative": relative,
                    "path": target,
                    "future_archive_file": future_archive_file,
                    "already_absent": already_absent,
                    "device": None,
                    "inode": None,
                    "size": None,
                }
            )
            continue
        if not target.is_file():
            raise PreparationError("pruning target is not a regular file: {}".format(target))
        metadata = target.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise PreparationError("pruning target is not a regular file: {}".format(target))
        entries.append(
            {
                "relative": relative,
                "path": target,
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "size": metadata.st_size,
                "future_archive_file": False,
                "already_absent": False,
            }
        )
    if len(entries) != expected_count:
        raise PreparationError(
            "pruning entry count mismatch: expected {}, got {}".format(
                expected_count, len(entries)
            )
        )
    if tuple(absent) != expected_absent:
        raise PreparationError(
            "missing regular pruning targets do not match the exact expected absence set"
        )
    return entries


def apply_prune_plan(source_root, plan, expected_absent_paths=None):
    """Delete only prevalidated listed files; retain all contingent directories."""
    source_root = require_real_directory(source_root, "Chromium source")
    expected_absent = tuple(
        safe_relative(value, "expected absent pruning target")
        for value in (expected_absent_paths or ())
    )
    if len(expected_absent) != len(set(expected_absent)):
        raise PreparationError("duplicate expected absent pruning target")
    checked = []
    absent = []
    for entry in plan:
        if entry.get("future_archive_file"):
            raise PreparationError(
                "pruning plan still contains an unmaterialized archive file: {}".format(
                    entry.get("relative")
                )
            )
        relative = safe_relative(entry["relative"], "pruning entry")
        if entry.get("already_absent"):
            target = reject_symlink_ancestors(source_root, relative)
            if target.exists() or target.is_symlink():
                raise PreparationError(
                    "pinned-absent pruning target appeared after preflight: {}".format(
                        target
                    )
                )
            absent.append(relative)
            continue
        target = require_regular_in_tree(source_root, relative, "pruning target")
        metadata = target.lstat()
        identity = (metadata.st_dev, metadata.st_ino, metadata.st_size)
        expected = (entry["device"], entry["inode"], entry["size"])
        if identity != expected:
            raise PreparationError("pruning target changed after preflight: {}".format(target))
        checked.append(target)
    if tuple(absent) != expected_absent:
        raise PreparationError(
            "pruning plan absence set changed before deletion"
        )
    for target in checked:
        target.unlink()
    absent_hash = hashlib.sha256(
        "".join("{}\n".format(relative) for relative in absent).encode("utf-8")
    ).hexdigest()
    return {
        "manifest_sha256": PRUNING_LIST_SHA256,
        "listed_files": len(plan),
        "files_removed": len(checked),
        "already_absent_files": len(absent),
        "already_absent_sha256": absent_hash,
        "contingent_paths_pruned": False,
        "directory_pruning_executed": False,
    }


def validate_completed_pruning(source_root):
    """Prove every pinned pruning target is already absent after the failed run."""
    manifest = Path(PRUNING_LIST)
    if manifest.is_symlink() or not manifest.is_file():
        raise PreparationError("pruning manifest is not a regular file: {}".format(manifest))
    if sha256_file(manifest) != PRUNING_LIST_SHA256:
        raise PreparationError("pruning.list hash mismatch during resume")
    entries = []
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line != line.strip() or line.startswith("#"):
            raise PreparationError(
                "invalid pruning entry at {}:{}: {!r}".format(manifest, number, line)
            )
        entries.append(safe_relative(line, "pruning entry {}".format(number)))
    if len(entries) != PRUNING_ENTRY_COUNT or len(entries) != len(set(entries)):
        raise PreparationError("pruning inventory changed during resume")
    plan = build_prune_plan(source_root, expected_absent_paths=tuple(entries))
    if len(plan) != PRUNING_ENTRY_COUNT or any(
        not item.get("already_absent") or item.get("future_archive_file") for item in plan
    ):
        raise PreparationError("not every pruning target is absent during resume")
    return {
        "manifest_sha256": PRUNING_LIST_SHA256,
        "listed_files": PRUNING_ENTRY_COUNT,
        "all_targets_absent": True,
        "absent_files": PRUNING_ENTRY_COUNT,
        "symlink_targets": 0,
    }


def build_patch_plan():
    """Return the validated filtered common order followed by three Mac patches."""
    focus_macos.validate_repository_contract()
    common_entries = focus_macos.read_series(focus_macos.COMMON_SERIES)
    excluded = set(focus_macos.SHARED_SERIES_EXCLUSIONS)
    common_entries = [entry for entry in common_entries if entry not in excluded]
    if len(common_entries) != focus_macos.EXPECTED_FULL_PATCH_BODY_COUNT:
        raise PreparationError("filtered common patch count changed")
    common_root = focus_macos.COMMON_SERIES.parent
    plan = [
        focus_macos.require_regular_tree_file(common_root, entry, "common patch")
        for entry in common_entries
    ]
    for relative, _ in focus_macos.EXPECTED_PATCHES:
        plan.append(
            focus_macos.require_regular_tree_file(
                REPO_ROOT, relative.as_posix(), "macOS platform patch"
            )
        )
    if len(plan) != 324:
        raise PreparationError("complete macOS patch plan must contain 324 patches")
    return plan


def validate_patch_tool(patch_bin=SYSTEM_PATCH):
    """Require Apple's fixed system patch binary and prove it starts."""
    patch_bin = Path(patch_bin)
    if patch_bin != SYSTEM_PATCH:
        raise PreparationError(
            "only the fixed system patch binary is allowed: {}".format(SYSTEM_PATCH)
        )
    if patch_bin.is_symlink() or not patch_bin.is_file() or not os.access(patch_bin, os.X_OK):
        raise PreparationError("system patch is unavailable or unsafe: {}".format(patch_bin))
    result = subprocess.run(
        [str(patch_bin), "-v"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0 or "patch" not in (result.stdout + result.stderr).lower():
        raise PreparationError("system patch identity check failed")
    return patch_bin


def patch_command(patch_bin, patch_path, source_root, check_only):
    """Build a noninteractive BSD patch command with fuzz zero and deletions."""
    command = [
        str(patch_bin),
        "-f",
        "-E",
        "-p1",
        "-F",
        "0",
        "-V",
        "none",
        "-r",
        "/dev/null",
        "-i",
        str(patch_path),
        "-d",
        str(source_root),
    ]
    if check_only:
        command.insert(1, "-C")
    return command


def patch_has_paired_explicit_deletion(patch_path):
    """Return whether a real unified-diff file header deletes a path."""
    patch_path = Path(patch_path)
    if patch_path.is_symlink() or not patch_path.is_file():
        raise PreparationError("patch is not a regular file: {}".format(patch_path))
    try:
        operations = focus_macos.scan_common_patch_path_operations(patch_path)
    except focus_macos.ContractError as exc:
        raise PreparationError("invalid patch path operations: {}".format(exc)) from exc
    return bool(operations["deletions"])


def git_reverse_check_command(source_root, patch_path):
    """Build the fixed read-only Git check needed for explicit deletions."""
    if (
        SYSTEM_GIT.is_symlink()
        or not SYSTEM_GIT.is_file()
        or not os.access(SYSTEM_GIT, os.X_OK)
    ):
        raise PreparationError("fixed system Git is unavailable")
    return [
        str(SYSTEM_GIT),
        "-C",
        str(source_root),
        "apply",
        "--check",
        "--reverse",
        str(patch_path),
    ]


def check_patch_boundary(
    source_root,
    patch_path,
    reverse=False,
    patch_bin=SYSTEM_PATCH,
    runner=subprocess.run,
):
    """Check one exact forward/reverse patch boundary without modifying source."""
    source_root = require_real_directory(source_root, "Chromium source")
    patch_path = Path(patch_path)
    if patch_path.is_symlink() or not patch_path.is_file():
        raise PreparationError("patch is not a regular file: {}".format(patch_path))
    use_git_reverse = reverse and patch_has_paired_explicit_deletion(patch_path)
    if use_git_reverse:
        # Apple BSD patch cannot reverse-check a deleted old path once -E has
        # removed it. Fixed Git provides the required read-only boundary check.
        command = git_reverse_check_command(source_root, patch_path)
    else:
        patch_bin = validate_patch_tool(patch_bin)
        command = patch_command(patch_bin, patch_path, source_root, True)
        if reverse:
            command.insert(2, "-R")
    run_arguments = {
        "capture_output": True,
        "text": True,
        "check": False,
    }
    if use_git_reverse:
        run_arguments.update(
            {
                "cwd": source_root,
                "env": fixed_git_environment(),
                "stdin": subprocess.DEVNULL,
            }
        )
    result = runner(command, **run_arguments)
    if result.returncode != 0:
        direction = "reverse" if reverse else "forward"
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise PreparationError(
            "patch boundary {} check failed for {}: {}".format(
                direction, patch_path, detail
            )
        )
    return {
        "path": str(patch_path),
        "sha256": sha256_file(patch_path),
        "direction": "reverse" if reverse else "forward",
        "applicable": True,
    }


def patch_slice_inventory(patch_plan, start, stop):
    """Hash an exact ordered half-open slice of the validated patch plan."""
    if type(start) is not int or type(stop) is not int or not (0 <= start <= stop):
        raise PreparationError("invalid patch inventory slice")
    selected = list(patch_plan[start:stop])
    if len(selected) != stop - start:
        raise PreparationError("patch inventory slice exceeds the complete plan")
    records = []
    for position, path in enumerate(selected, start + 1):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise PreparationError("patch is not a regular file: {}".format(path))
        try:
            relative = path.resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError as exc:
            raise PreparationError("patch escaped the Focus repository") from exc
        records.append(
            "{}\t{}\t{}\n".format(position, relative, sha256_file(path))
        )
    body = "".join(records).encode("utf-8")
    return {
        "first_position": start + 1 if selected else None,
        "last_position": stop if selected else None,
        "count": len(selected),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def apply_patch_plan(
    source_root,
    patch_plan,
    patch_bin=SYSTEM_PATCH,
    runner=subprocess.run,
    base_position=0,
    total_patches=None,
):
    """Check then apply every patch in order with system patch and fuzz=0."""
    source_root = require_real_directory(source_root, "Chromium source")
    patch_bin = validate_patch_tool(patch_bin)
    if type(base_position) is not int or base_position < 0:
        raise PreparationError("patch base position must be a nonnegative integer")
    total_patches = total_patches if total_patches is not None else len(patch_plan)
    if (
        type(total_patches) is not int
        or total_patches < base_position + len(patch_plan)
    ):
        raise PreparationError("patch total is smaller than the numbered batch")
    applied = []
    for batch_position, patch_path in enumerate(patch_plan, 1):
        position = base_position + batch_position
        patch_path = Path(patch_path)
        if patch_path.is_symlink() or not patch_path.is_file():
            raise PreparationError("patch is not a regular file: {}".format(patch_path))
        for check_only in (True, False):
            result = runner(
                patch_command(patch_bin, patch_path, source_root, check_only),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                phase = "check" if check_only else "apply"
                detail = (result.stderr or result.stdout).strip()[-2000:]
                raise PreparationError(
                    "patch {} failed during {} ({}/{}): {}".format(
                        patch_path, phase, position, total_patches, detail
                    )
                )
        applied.append(str(patch_path))
    return applied


def _validate_pinned_file(path, expected_hash, label):
    if path.is_symlink() or not path.is_file():
        raise PreparationError("missing regular {}: {}".format(label, path))
    actual = sha256_file(path)
    if actual != expected_hash:
        raise PreparationError(
            "{} hash mismatch: expected {}, got {}".format(label, expected_hash, actual)
        )
    return path


def _read_pinned_file_snapshot(path, expected_hash, label):
    """Read one pinned regular file through a no-follow descriptor."""
    path = _validate_pinned_file(path, expected_hash, label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PreparationError("could not safely open {}: {}".format(label, path)) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PreparationError("{} is not regular: {}".format(label, path))
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            body = stream.read()
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(body) != before.st_size:
        raise PreparationError("{} changed while being read".format(label))
    actual_hash = hashlib.sha256(body).hexdigest()
    if actual_hash != expected_hash:
        raise PreparationError(
            "{} snapshot hash mismatch: expected {}, got {}".format(
                label, expected_hash, actual_hash
            )
        )
    return path, body


def validate_domain_targets(source_root):
    """Validate the exact macOS domain input and host-conditioned absence sets."""
    source_root = require_real_directory(source_root, "Chromium source")
    _, regex_body = _read_pinned_file_snapshot(
        REPO_ROOT / "focus-chromium" / "domain_regex.list",
        DOMAIN_REGEX_SHA256,
        "domain regex list",
    )
    _, files_body = _read_pinned_file_snapshot(
        REPO_ROOT / "focus-chromium" / "domain_substitution.list",
        DOMAIN_LIST_SHA256,
        "domain substitution list",
    )
    try:
        lines = files_body.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise PreparationError("domain substitution list is not UTF-8") from exc
    listed = []
    regular = []
    identity_lines = []
    regular_count = 0
    missing = []
    for number, line in enumerate(lines, 1):
        if not line:
            continue
        relative = safe_relative(line, "domain list line {}".format(number))
        listed.append(relative)
        target = reject_symlink_ancestors(
            source_root, relative, include_leaf=False
        )
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            missing.append(relative)
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise PreparationError("domain target is a symlink: {}".format(target))
        if stat.S_ISREG(metadata.st_mode):
            regular.append(relative)
            identity_lines.append(
                "{}\t{}\t{}\n".format(relative, metadata.st_dev, metadata.st_ino)
            )
            regular_count += 1
        else:
            raise PreparationError(
                "domain target is neither a regular file nor absent: {}".format(
                    target
                )
            )

    if len(listed) != DOMAIN_LIST_ENTRY_COUNT or len(set(listed)) != len(listed):
        raise PreparationError("domain target list count or uniqueness changed")
    missing_body = "".join("{}\n".format(path) for path in missing).encode("utf-8")
    missing_sha256 = hashlib.sha256(missing_body).hexdigest()
    regular_body = "".join("{}\n".format(path) for path in regular).encode("utf-8")
    identity_sha256 = hashlib.sha256(
        "".join(identity_lines).encode("utf-8")
    ).hexdigest()
    if (
        regular_count != MACOS_DOMAIN_REGULAR_TARGET_COUNT
        or len(missing) != MACOS_DOMAIN_MISSING_TARGET_COUNT
        or len(missing_body) != MACOS_DOMAIN_MISSING_MANIFEST_BYTES
        or missing_sha256 != MACOS_DOMAIN_MISSING_MANIFEST_SHA256
    ):
        raise PreparationError(
            "macOS domain target inventory changed: regular={}, missing={}, "
            "missing_bytes={}, missing_sha256={}".format(
                regular_count,
                len(missing),
                len(missing_body),
                missing_sha256,
            )
        )
    return (
        {
            "listed": len(listed),
            "regular": regular_count,
            "expected_absent": len(missing),
            "expected_absent_bytes": len(missing_body),
            "expected_absent_sha256": missing_sha256,
        },
        regex_body,
        regular_body,
        identity_sha256,
    )


def validate_name_targets(source_root):
    """Reject symlinked GRD/GRDP/XTB inputs before name substitution."""
    require_regular_in_tree(source_root, "OWNERS", "Chromium OWNERS sentinel")
    files = list(
        name_substitution.get_substitutable_files(
            Path(source_root), ["grd", "grdp", "xtb"]
        )
    )
    for path in files:
        relative = path.relative_to(source_root).as_posix()
        require_regular_in_tree(source_root, relative, "name-substitution target")
    return len(files)


def validate_i18n_targets(source_root):
    """Resolve and validate every translation source and target XTB file."""
    source_entries = json.loads(i18n_apply.SOURCE_PATH.read_text(encoding="utf-8"))
    for entry in source_entries:
        relative = safe_relative(entry["source"], "i18n source")
        require_regular_in_tree(source_root, relative, "i18n source")
    index = i18n_apply.build_xtb_index(source_entries, Path(source_root))
    targets = set()
    for mapping in index.values():
        for path in mapping.values():
            relative = path.relative_to(source_root).as_posix()
            require_regular_in_tree(source_root, relative, "i18n XTB target")
            targets.add(relative)
    return len(source_entries), len(targets)


def apply_common_transformations(source_root, workers=None):
    """Run the shared domain, browser-name, and translation operations."""
    source_root = require_real_directory(source_root, "Chromium source")
    (
        domain_inventory,
        regex_body,
        regular_body,
        identity_before,
    ) = validate_domain_targets(source_root)
    domain_warnings = []

    class DomainWarningCollector(logging.Handler):
        def emit(self, record):
            domain_warnings.append(record.getMessage())

    with tempfile.TemporaryDirectory(prefix="focus-domain-substitution-") as temporary:
        temporary_root = Path(temporary)
        regex_path = temporary_root / "domain_regex.list"
        files_path = temporary_root / "domain_substitution.list"
        for path, body in ((regex_path, regex_body), (files_path, regular_body)):
            with path.open("xb") as stream:
                stream.write(body)
                stream.flush()
                os.fchmod(stream.fileno(), 0o400)
                os.fsync(stream.fileno())
        collector = DomainWarningCollector(level=logging.WARNING)
        logger = domain_substitution.get_logger()
        previous_logger_level = logger.level
        if previous_logger_level > logging.WARNING:
            logger.setLevel(logging.WARNING)
        logger.addHandler(collector)
        try:
            domain_substitution.apply_substitution(
                regex_path, files_path, source_root, None
            )
        finally:
            logger.removeHandler(collector)
            logger.setLevel(previous_logger_level)
    if domain_warnings:
        raise PreparationError(
            "domain substitution skipped a validated target: {}".format(
                domain_warnings[0]
            )
        )
    domain_inventory_after, _, _, identity_after = validate_domain_targets(source_root)
    if domain_inventory_after != domain_inventory or identity_after != identity_before:
        raise PreparationError("domain target inventory changed during substitution")
    name_count = validate_name_targets(source_root)
    name_substitution.replacement_sanity()
    name_substitution.do_substitution(
        Path(source_root),
        tarpath=None,
        workers=workers or min(32, os.cpu_count() or 1),
        dry_run=False,
    )
    i18n_source_count, xtb_count = validate_i18n_targets(source_root)
    i18n_apply.apply_translations(Path(source_root))
    return {
        "domain_targets": domain_inventory["listed"],
        "domain_regular_targets": domain_inventory["regular"],
        "domain_expected_absent_targets": domain_inventory["expected_absent"],
        "domain_expected_absent_bytes": domain_inventory["expected_absent_bytes"],
        "domain_expected_absent_sha256": domain_inventory[
            "expected_absent_sha256"
        ],
        "name_candidates": name_count,
        "i18n_source_entries": i18n_source_count,
        "i18n_xtb_targets": xtb_count,
    }


def build_overlay_plan():
    """Return only validated regular non-Windows overlay files and cleanup paths."""
    prefixes = focus_macos.read_exclude_prefixes()
    cleanup = focus_macos.validate_delete_manifest(prefixes)
    included = []
    for source in focus_macos.iter_overlay_regular_files(focus_macos.OVERLAY_ROOT):
        relative = source.relative_to(focus_macos.OVERLAY_ROOT).as_posix()
        if relative == "delete.txt" or focus_macos.is_overlay_excluded(relative, prefixes):
            continue
        pure = PurePosixPath(safe_relative(relative, "overlay"))
        if any(part.lower() in ("win", "windows") for part in pure.parts):
            raise PreparationError("Windows overlay escaped the macOS filter: {}".format(relative))
        included.append((source, relative))
    expected = focus_macos.validate_repository_contract()["overlay"]["included_count"]
    if len(included) != expected:
        raise PreparationError("filtered overlay inventory changed")
    return included, cleanup["planned"], prefixes


def apply_overlay(source_root, overlay_files, cleanup_paths):
    """Apply the filtered cleanup manifest and regular-file overlay safely."""
    source_root = require_real_directory(source_root, "Chromium source")
    cleanup_plan = []
    copy_plan = []
    for relative in cleanup_paths:
        target = reject_symlink_ancestors(source_root, relative, include_leaf=False)
        if target.exists() and target.is_dir() and not target.is_symlink():
            raise PreparationError("cleanup target is a directory: {}".format(target))
        cleanup_plan.append(target)
    for source, relative in overlay_files:
        if Path(source).is_symlink() or not Path(source).is_file():
            raise PreparationError("overlay source is not regular: {}".format(source))
        destination = reject_symlink_ancestors(source_root, relative, include_leaf=False)
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise PreparationError("unsafe overlay destination: {}".format(destination))
        copy_plan.append((Path(source), destination))
    removed = 0
    for target in cleanup_plan:
        if target.is_symlink() or target.is_file():
            target.unlink()
            removed += 1
    for source, destination in copy_plan:
        atomic_copy(source, destination)
    return {
        "cleanup_removed": removed,
        "cleanup_missing": len(cleanup_plan) - removed,
        "overlay_files_copied": len(copy_plan),
    }


def parse_resource_plan():
    """Validate common resource manifests and return safe source/destination pairs."""
    resources = REPO_ROOT / "focus-chromium" / "resources"
    generate_list = _validate_pinned_file(
        resources / "generate_resources.txt", GENERATE_LIST_SHA256, "resource generation list"
    )
    generated_entries = [
        line.strip()
        for line in generate_list.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if generated_entries:
        raise PreparationError(
            "common generated-resource manifest is no longer empty; "
            "generation semantics require review"
        )
    resource_list = _validate_pinned_file(
        resources / "focus_resources.txt", RESOURCE_LIST_SHA256, "common resource list"
    )
    plan = []
    destinations = set()
    body_inventory = []
    for number, line in enumerate(resource_list.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        parts = value.split()
        if len(parts) != 2:
            raise PreparationError("invalid resource line {}".format(number))
        source_relative = safe_relative(parts[0], "resource source")
        destination_relative = safe_relative(parts[1], "resource destination")
        if any(
            part.lower() in ("win", "windows")
            for part in PurePosixPath(destination_relative).parts
        ):
            raise PreparationError(
                "Windows resource is forbidden on macOS: {}".format(
                    destination_relative
                )
            )
        if destination_relative in destinations:
            raise PreparationError(
                "duplicate resource destination: {}".format(destination_relative)
            )
        destinations.add(destination_relative)
        source = require_regular_in_tree(resources, source_relative, "Focus resource")
        plan.append((source, destination_relative))
        body_inventory.append(
            "{}\0{}\0{}\n".format(
                source_relative, destination_relative, sha256_file(source)
            )
        )
    body_hash = hashlib.sha256("".join(body_inventory).encode("utf-8")).hexdigest()
    if len(plan) != RESOURCE_BODY_COUNT or body_hash != RESOURCE_BODY_SHA256:
        raise PreparationError(
            "common resource body inventory changed: expected {}/{}, got {}/{}".format(
                RESOURCE_BODY_COUNT,
                RESOURCE_BODY_SHA256,
                len(plan),
                body_hash,
            )
        )
    return plan


def validate_resource_destinations(source_root, resource_plan):
    """Resolve every pinned resource to an existing regular destination."""
    source_root = require_real_directory(source_root, "Chromium source")
    copy_plan = []
    for source, relative in resource_plan:
        target = reject_symlink_ancestors(
            source_root, relative, include_leaf=False
        )
        try:
            metadata = target.lstat()
        except FileNotFoundError as exc:
            raise PreparationError(
                "missing regular Chromium resource destination: {}".format(
                    target
                )
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PreparationError(
                "Chromium resource destination is not a regular file: {}".format(
                    target
                )
            )
        copy_plan.append((source, target))
    if len(copy_plan) != RESOURCE_BODY_COUNT:
        raise PreparationError(
            "resource destination count changed: expected {}, got {}".format(
                RESOURCE_BODY_COUNT, len(copy_plan)
            )
        )
    return copy_plan, {
        "manifest_entries": len(resource_plan),
        "copy_targets": len(copy_plan),
    }


def resource_destination_inventory(source_root, resource_plan):
    """Hash all current Mac-applicable resource destinations in plan order."""
    copy_plan, contract = validate_resource_destinations(source_root, resource_plan)
    body = "".join(
        "{}\0{}\0{}\n".format(
            destination.relative_to(Path(source_root).resolve()).as_posix(),
            destination.stat().st_size,
            sha256_file(destination),
        )
        for _, destination in copy_plan
    ).encode("utf-8")
    report = dict(contract)
    report.update(
        {
            "inventory_bytes": len(body),
            "inventory_sha256": hashlib.sha256(body).hexdigest(),
        }
    )
    return report


def copy_common_resources(source_root, resource_plan):
    """Replace only declared existing Chromium resources."""
    plan, _ = validate_resource_destinations(source_root, resource_plan)
    for source, destination in plan:
        atomic_copy(source, destination)
    return len(plan)


def append_focus_version_once(source_root):
    """Append the repository-derived four-part Focus version exactly once."""
    version_path = require_regular_in_tree(source_root, "chrome/VERSION", "Chromium VERSION")
    for path in (
        REPO_ROOT / "focus-chromium" / "version.txt",
        REPO_ROOT / "focus-chromium" / "chromium_version.txt",
        REPO_ROOT / "focus-chromium" / "revision.txt",
        REPO_ROOT / "revision.txt",
    ):
        if path.is_symlink() or not path.is_file():
            raise PreparationError("Focus version input is not a regular file: {}".format(path))
    version_parts = focus_version.get_version_parts(
        REPO_ROOT / "focus-chromium", REPO_ROOT
    )
    if tuple(version_parts) != (
        "FOCUS_MAJOR",
        "FOCUS_MINOR",
        "FOCUS_PATCH",
        "FOCUS_PLATFORM",
    ) or any(not value.isdigit() for value in version_parts.values()):
        raise PreparationError("invalid Focus version contract: {!r}".format(version_parts))
    focus_version.check_existing_version(version_path)
    with version_path.open("a", encoding="utf-8") as stream:
        for name, value in version_parts.items():
            focus_version.append_version(stream, name, value)
    return ".".join(version_parts.values())


def validate_icon_destination(source_root, require_upstream_hash=False):
    """Prove Chromium's mac bundle consumes the branding app.icns path."""
    build_file = require_regular_in_tree(source_root, "chrome/BUILD.gn", "Chromium chrome/BUILD.gn")
    if require_upstream_hash:
        actual_hash = sha256_file(build_file)
        if actual_hash != CHROME_BUILD_GN_SHA256:
            raise PreparationError(
                "Chromium chrome/BUILD.gn hash mismatch: expected {}, got {}".format(
                    CHROME_BUILD_GN_SHA256, actual_hash
                )
            )
    build_text = build_file.read_text(encoding="utf-8")
    if build_text.count(MAC_ICON_BUILD_TOKEN) != 1:
        raise PreparationError("Chromium mac app icon build token changed")
    return require_regular_in_tree(
        source_root, MAC_ICON_DESTINATION, "Chromium macOS branding icon"
    )


def validate_upstream_source_contracts(source_root):
    """Pin all four upstream Mac files before any source mutation."""
    focus_macos.validate_chromium_macos_build_contract(source_root)
    validate_icon_destination(source_root, require_upstream_hash=True)
    installer = require_regular_in_tree(
        source_root, INSTALLER_MAC_BUILD_GN, "Chromium macOS installer BUILD.gn"
    )
    installer_hash = sha256_file(installer)
    if installer_hash != INSTALLER_MAC_BUILD_GN_SHA256:
        raise PreparationError(
            "Chromium installer mac BUILD.gn hash mismatch: expected {}, got {}".format(
                INSTALLER_MAC_BUILD_GN_SHA256, installer_hash
            )
        )
    return expected_upstream_source_contracts()


def expected_upstream_source_contracts():
    """Return immutable upstream hashes recorded before source mutation."""
    return OrderedDict(
        (
            ("chrome/BUILD.gn", CHROME_BUILD_GN_SHA256),
            (INSTALLER_MAC_BUILD_GN, INSTALLER_MAC_BUILD_GN_SHA256),
            (
                focus_macos.CHROMIUM_MAC_SDK_GNI,
                focus_macos.PINNED_CHROMIUM_MAC_SDK_GNI_SHA256,
            ),
            (
                focus_macos.CHROMIUM_UNIVERSALIZER,
                focus_macos.PINNED_CHROMIUM_UNIVERSALIZER_SHA256,
            ),
        )
    )


def install_focus_icns(source_root):
    """Install the hash-pinned ICNS after the Chromium icon path is proven."""
    focus_macos.validate_icns_asset()
    destination = validate_icon_destination(source_root)
    branding = require_regular_in_tree(
        source_root, "chrome/app/theme/chromium/BRANDING", "Focus Chromium branding"
    ).read_text(encoding="utf-8")
    for line in (
        "PRODUCT_FULLNAME=Focus Browser",
        "PRODUCT_SHORTNAME=Focus Browser",
        "MAC_BUNDLE_ID={}".format(focus_macos.BUNDLE_ID),
    ):
        if branding.splitlines().count(line) != 1:
            raise PreparationError(
                "post-patch Chromium branding contract is missing {!r}".format(line)
            )
    atomic_copy(focus_macos.FOCUS_ICNS, destination)
    if sha256_file(destination) != focus_macos.FOCUS_ICNS_SHA256:
        raise PreparationError("installed Focus ICNS hash mismatch")
    return str(destination)


def args_gn_plan():
    """Return validated GN text for the two native slice directories."""
    profiles = focus_macos.validate_gn_profiles()["profiles"]
    return OrderedDict(
        (
            ("arm64", (focus_macos.DEFAULT_OUT_DIRS["arm64"], profiles["arm64"]["args_gn"])),
            ("x64", (focus_macos.DEFAULT_OUT_DIRS["x64"], profiles["x64"]["args_gn"])),
        )
    )


def write_args_gn(source_root, plan=None):
    """Write both args.gn files without replacing prior build configuration."""
    source_root = require_real_directory(source_root, "Chromium source")
    plan = plan or args_gn_plan()
    destinations = []
    for architecture, (out_dir, text) in plan.items():
        relative = PurePosixPath(safe_relative(out_dir, "GN output"), "args.gn").as_posix()
        destination = reject_symlink_ancestors(source_root, relative, include_leaf=False)
        if destination.exists() or destination.is_symlink():
            raise PreparationError("refusing to overwrite args.gn: {}".format(destination))
        if not text.endswith("\n"):
            raise PreparationError("{} args.gn text lacks final newline".format(architecture))
        destinations.append((architecture, destination, text))
    for _, destination, text in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(text)
    return {architecture: str(destination) for architecture, destination, _ in destinations}


def fresh_preparation_execution_report(total_patches=324):
    """Describe a normal preparation that starts from the pristine source."""
    return {
        "mode": "fresh",
        "initial_applied_patch_count": 0,
        "patches_applied_this_run": total_patches,
        "total_patches": total_patches,
        "resume_checkpoint": None,
    }


def _project_expected_patch_path(path, path_projector, label):
    """Project only a trusted patch-plan Path to an exact same-inode alias."""
    path = Path(path)
    if path_projector is None:
        return str(path)
    try:
        projected = Path(path_projector(path))
    except Exception as exc:
        raise PreparationError("{} projection failed".format(label)) from exc
    if (
        not projected.is_absolute()
        or Path(os.path.abspath(str(projected))) != projected
        or projected.name != path.name
        or projected.is_symlink()
    ):
        raise PreparationError("{} projection is not absolute and exact".format(label))
    try:
        physical = os.stat(str(path), follow_symlinks=True)
        logical = os.stat(str(projected), follow_symlinks=True)
    except OSError as exc:
        raise PreparationError("{} projection cannot be verified".format(label)) from exc
    if (physical.st_dev, physical.st_ino) != (logical.st_dev, logical.st_ino):
        raise PreparationError("{} projection changed patch identity".format(label))
    return str(projected)


def expected_resume_execution_report(applied_patches, path_projector=None):
    """Reconstruct one audited exact-prefix execution report from pins."""
    patch_plan = build_patch_plan()
    expected_resume_working_tree(applied_patches)
    last_path = patch_plan[applied_patches - 1]
    next_path = (
        patch_plan[applied_patches]
        if applied_patches < len(patch_plan)
        else None
    )
    return {
        "mode": "resume_exact_prefix",
        "initial_applied_patch_count": applied_patches,
        "patches_applied_this_run": len(patch_plan) - applied_patches,
        "total_patches": len(patch_plan),
        "resume_checkpoint": {
            "git_head": ACQUISITION_CHROMIUM_COMMIT,
            "working_tree": expected_resume_working_tree(applied_patches),
            "ignored_tree": expected_ignored_working_tree_inventory(),
            "dependency_tree": expected_resume_dependency_tree(applied_patches),
            "pruning": {
                "manifest_sha256": PRUNING_LIST_SHA256,
                "listed_files": PRUNING_ENTRY_COUNT,
                "all_targets_absent": True,
                "absent_files": PRUNING_ENTRY_COUNT,
                "symlink_targets": 0,
            },
            "applied_prefix": patch_slice_inventory(
                patch_plan, 0, applied_patches
            ),
            "last_applied_patch": {
                "position": applied_patches,
                "path": _project_expected_patch_path(
                    last_path, path_projector, "last applied patch"
                ),
                "sha256": sha256_file(last_path),
                "reverse_applicable": True,
            },
            "next_patch": (
                {
                    "position": applied_patches + 1,
                    "path": _project_expected_patch_path(
                        next_path, path_projector, "next patch"
                    ),
                    "sha256": sha256_file(next_path),
                    "forward_applicable": True,
                }
                if next_path is not None
                else None
            ),
        },
    }


def validate_preparation_execution_report(report, path_projector=None):
    """Validate honest fresh or exact-prefix preparation provenance."""
    required = {
        "mode",
        "initial_applied_patch_count",
        "patches_applied_this_run",
        "total_patches",
        "resume_checkpoint",
    }
    if not isinstance(report, dict) or set(report) != required:
        raise PreparationError("preparation execution report schema mismatch")
    if report["total_patches"] != 324 or (
        report["initial_applied_patch_count"] + report["patches_applied_this_run"]
        != report["total_patches"]
    ):
        raise PreparationError("preparation execution patch counts mismatch")
    if report["mode"] == "fresh":
        if report != fresh_preparation_execution_report():
            raise PreparationError("fresh preparation execution report mismatch")
        return report
    initial_applied = report["initial_applied_patch_count"]
    if (
        report["mode"] != "resume_exact_prefix"
        or initial_applied not in RESUME_AUDITED_PATCH_CHECKPOINTS
        or report["patches_applied_this_run"] != 324 - initial_applied
    ):
        raise PreparationError("resume preparation execution report mismatch")
    checkpoint = report["resume_checkpoint"]
    if not isinstance(checkpoint, dict) or set(checkpoint) != {
        "git_head",
        "working_tree",
        "ignored_tree",
        "dependency_tree",
        "pruning",
        "applied_prefix",
        "last_applied_patch",
        "next_patch",
    }:
        raise PreparationError("resume checkpoint schema mismatch")
    if checkpoint["git_head"] != ACQUISITION_CHROMIUM_COMMIT:
        raise PreparationError("resume checkpoint Git HEAD mismatch")
    working_tree = checkpoint["working_tree"]
    if working_tree != expected_resume_working_tree(initial_applied):
        raise PreparationError("resume checkpoint working-tree mismatch")
    if checkpoint["ignored_tree"] != expected_ignored_working_tree_inventory():
        raise PreparationError("resume checkpoint ignored-tree mismatch")
    expected_tree = expected_resume_dependency_tree(initial_applied)
    if checkpoint["dependency_tree"] != expected_tree:
        raise PreparationError("resume checkpoint dependency tree mismatch")
    if checkpoint["pruning"] != {
        "manifest_sha256": PRUNING_LIST_SHA256,
        "listed_files": PRUNING_ENTRY_COUNT,
        "all_targets_absent": True,
        "absent_files": PRUNING_ENTRY_COUNT,
        "symlink_targets": 0,
    }:
        raise PreparationError("resume checkpoint pruning mismatch")
    patch_plan = build_patch_plan()
    if checkpoint["applied_prefix"] != patch_slice_inventory(
        patch_plan, 0, initial_applied
    ):
        raise PreparationError("resume checkpoint patch-prefix mismatch")
    expected_last = {
        "position": initial_applied,
        "path": _project_expected_patch_path(
            patch_plan[initial_applied - 1],
            path_projector,
            "last applied patch",
        ),
        "sha256": sha256_file(patch_plan[initial_applied - 1]),
        "reverse_applicable": True,
    }
    expected_next = None
    if initial_applied < len(patch_plan):
        expected_next = {
            "position": initial_applied + 1,
            "path": _project_expected_patch_path(
                patch_plan[initial_applied], path_projector, "next patch"
            ),
            "sha256": sha256_file(patch_plan[initial_applied]),
            "forward_applicable": True,
        }
    if checkpoint["last_applied_patch"] != expected_last:
        raise PreparationError("resume checkpoint last patch mismatch")
    if checkpoint["next_patch"] != expected_next:
        raise PreparationError("resume checkpoint next patch mismatch")
    return report


def write_preparation_receipt(
    source_root,
    preflight_report,
    args_paths,
    pruning_report,
    dependency_report,
    localized_strings_report,
    execution_report=None,
    recovery_checkpoint=None,
):
    """Write the deterministic post-preparation provenance receipt once."""
    source_root = require_real_directory(source_root, "Chromium source")
    execution_report = execution_report or fresh_preparation_execution_report()
    validate_recovery_execution_link(execution_report, recovery_checkpoint)
    validate_recovery_checkpoint_report(recovery_checkpoint, source_root)
    expected_pruning = {
        "files_removed": PRUNING_EXPECTED_REMOVAL_COUNT,
        "already_absent_files": PRUNING_ALREADY_ABSENT_COUNT,
        "already_absent_sha256": PRUNING_ALREADY_ABSENT_SHA256,
    }
    if not isinstance(pruning_report, dict):
        raise PreparationError("pruning result must be an object")
    if {
        key: pruning_report.get(key) for key in expected_pruning
    } != expected_pruning:
        raise PreparationError("pruning result does not match the pinned Mac inventory")
    expected_dependency_install = expected_dependency_install_report()
    if dependency_report != expected_dependency_install:
        raise PreparationError("dependency install result does not match pinned inventory")
    if not isinstance(localized_strings_report, dict) or set(localized_strings_report) != {
        "generator",
        "generator_sha256",
        "node",
        "output",
        "baseline_bytes",
        "baseline_sha256",
        "output_bytes",
        "output_sha256",
        "runs",
        "byte_identical",
        "network_operations",
    }:
        raise PreparationError("localized strings generation report schema mismatch")
    if (
        localized_strings_report["generator"] != ONBOARDING_GENERATOR
        or localized_strings_report["generator_sha256"] != ONBOARDING_GENERATOR_SHA256
        or localized_strings_report["output"] != ONBOARDING_STRINGS_OUTPUT
        or localized_strings_report["baseline_bytes"]
        != ONBOARDING_STRINGS_BASELINE_BYTES
        or localized_strings_report["baseline_sha256"]
        != ONBOARDING_STRINGS_BASELINE_SHA256
        or localized_strings_report["output_bytes"]
        != ONBOARDING_STRINGS_BASELINE_BYTES
        or localized_strings_report["output_sha256"]
        != ONBOARDING_STRINGS_BASELINE_SHA256
        or type(localized_strings_report["output_bytes"]) is not int
        or localized_strings_report["output_bytes"] <= 0
        or not re.fullmatch(
            r"[0-9a-f]{64}", localized_strings_report["output_sha256"]
        )
        or localized_strings_report["runs"] != 2
        or localized_strings_report["byte_identical"] is not True
        or localized_strings_report["network_operations"] != 0
        or not isinstance(localized_strings_report["node"], dict)
    ):
        raise PreparationError("localized strings generation report mismatch")
    generated_strings = require_regular_in_tree(
        source_root, ONBOARDING_STRINGS_OUTPUT, "generated onboarding strings"
    )
    if (
        generated_strings.stat().st_size != localized_strings_report["output_bytes"]
        or sha256_file(generated_strings)
        != localized_strings_report["output_sha256"]
    ):
        raise PreparationError("generated onboarding strings changed before receipt")
    receipt_relative = safe_relative(PREPARATION_RECEIPT, "preparation receipt")
    receipt_path = reject_symlink_ancestors(
        source_root, receipt_relative, include_leaf=False
    )
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PreparationError("preparation receipt already exists: {}".format(receipt_path))

    post_paths = OrderedDict(
        (
            ("chrome/BUILD.gn", "chrome/BUILD.gn"),
            (INSTALLER_MAC_BUILD_GN, INSTALLER_MAC_BUILD_GN),
            (
                "chrome/app/theme/chromium/BRANDING",
                "chrome/app/theme/chromium/BRANDING",
            ),
            ("chrome/VERSION", "chrome/VERSION"),
            (MAC_ICON_DESTINATION, MAC_ICON_DESTINATION),
            ("onboarding/strings.ts", ONBOARDING_STRINGS_OUTPUT),
        )
    )
    for architecture, absolute in args_paths.items():
        path = Path(absolute)
        try:
            relative = path.resolve().relative_to(source_root).as_posix()
        except ValueError as exc:
            raise PreparationError("args.gn path escaped Chromium source") from exc
        post_paths["args_gn/{}".format(architecture)] = relative

    post_hashes = OrderedDict()
    for label, relative in post_paths.items():
        post_hashes[label] = sha256_file(
            require_regular_in_tree(source_root, relative, "receipt input")
        )

    platform_patches = focus_macos.validate_platform_patch_series()
    post_prepare_dependency_tree = installed_dependency_tree(
        source_root, DEPENDENCY_CONTRACTS
    )
    receipt = OrderedDict(
        (
            ("schema", PREPARATION_RECEIPT_SCHEMA),
            ("chromium_version", focus_macos.PINNED_CHROMIUM_VERSION),
            ("offline", True),
            ("network_operations", 0),
            ("acquisition", preflight_report["acquisition"]),
            ("tool_bootstrap", preflight_report["tool_bootstrap"]),
            ("upstream_baseline_sha256", preflight_report["upstream_baseline_sha256"]),
            (
                "patch_contract",
                {
                    "common_filtered_count": focus_macos.EXPECTED_FULL_PATCH_BODY_COUNT,
                    "common_filtered_order_sha256": focus_macos.FILTERED_COMMON_SERIES_SHA256,
                    "common_full_body_sha256": focus_macos.EXPECTED_FULL_PATCH_BODY_SHA256,
                    "platform": platform_patches,
                },
            ),
            ("preparation_execution", execution_report),
            ("recovery_checkpoint", recovery_checkpoint),
            (
                "dependency_contract",
                {
                    "manifest_sha256": DEPS_INI_SHA256,
                    "archives": {
                        name: contract["sha256"]
                        for name, contract in DEPENDENCY_CONTRACTS.items()
                    },
                    "cache_marker": preflight_report["dependency_cache_marker"],
                    "install_inventory": {
                        key: dependency_report[key]
                        for key in (
                            "ownership_roots",
                            "regular_files",
                            "logical_bytes",
                            "sha256",
                            "installed_symlinks",
                            "installed_special_files",
                            "components",
                            "omitted_symlinks",
                        )
                    },
                    "post_prepare_tree": post_prepare_dependency_tree,
                },
            ),
            (
                "pruning_contract",
                {
                    "manifest_sha256": PRUNING_LIST_SHA256,
                    "listed_files": PRUNING_ENTRY_COUNT,
                    "files_removed": pruning_report["files_removed"],
                    "already_absent_files": pruning_report["already_absent_files"],
                    "already_absent_sha256": pruning_report[
                        "already_absent_sha256"
                    ],
                    "contingent_paths_pruned": False,
                    "directory_pruning_executed": False,
                },
            ),
            (
                "overlay_contract",
                {
                    "count": focus_macos.EXPECTED_FULL_OVERLAY_BODY_COUNT,
                    "sha256": focus_macos.EXPECTED_FULL_OVERLAY_BODY_SHA256,
                },
            ),
            (
                "resource_contract",
                {"count": RESOURCE_BODY_COUNT, "sha256": RESOURCE_BODY_SHA256},
            ),
            ("icns_sha256", focus_macos.FOCUS_ICNS_SHA256),
            ("post_prepare_sha256", post_hashes),
            ("localized_strings_contract", localized_strings_report),
            ("build_executed", False),
            ("signing_executed", False),
            ("packaging_executed", False),
            ("hard_disk_floor_gib", HARD_DISK_FLOOR_GIB),
        )
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        receipt, ensure_ascii=False, indent=2, sort_keys=False
    ) + "\n"
    atomic_publish_text(receipt_path, serialized)
    return {
        "path": str(receipt_path),
        "sha256": sha256_file(receipt_path),
        "post_prepare_sha256": post_hashes,
    }


def preflight(source_root, cache_root):
    """Perform all checks that do not depend on already-applied patches."""
    source_input = Path(source_root).expanduser()
    if source_input.is_symlink():
        raise PreparationError("Chromium source argument must not be a symlink")
    source, version = focus_macos.resolve_source_root(str(source_input))
    acquisition = validate_acquisition_marker(source)
    tool_bootstrap = validate_tool_bootstrap_marker(source, acquisition)
    upstream_baseline = validate_upstream_source_contracts(source)
    repository = focus_macos.validate_repository_contract()
    contracts = validate_dependency_manifest()
    cache, cache_report = validate_offline_cache(cache_root, contracts)
    dependency_cache_marker = validate_dependency_cache_marker(cache, contracts)
    require_empty_dependency_roots(source, contracts)
    archives = {}
    archive_files = 0
    future_dependency_paths = set()
    for name, contract in contracts.items():
        archive = cache / contract["download_filename"]
        entries = inspect_archive(archive, contract)
        archives[name] = str(archive)
        archive_files += len(entries)
        output = PurePosixPath(contract["output_path"])
        future_dependency_paths.update(
            PurePosixPath(output, relative).as_posix()
            for relative, _, _, _ in entries
        )
    patch_plan = build_patch_plan()
    validate_patch_tool()
    expected_absent_pruning = load_expected_absent_pruning()
    prune_plan = build_prune_plan(
        source,
        allowed_missing=future_dependency_paths,
        expected_absent_paths=expected_absent_pruning,
    )
    pruning_present = sum(
        not entry["future_archive_file"] and not entry["already_absent"]
        for entry in prune_plan
    )
    pruning_future = sum(entry["future_archive_file"] for entry in prune_plan)
    overlay_files, cleanup_paths, _ = build_overlay_plan()
    resource_plan = parse_resource_plan()
    resource_destinations = resource_destination_inventory(source, resource_plan)
    gn_plan = args_gn_plan()
    for _, (out_dir, _) in gn_plan.items():
        args_path = source / Path(out_dir) / "args.gn"
        if args_path.exists() or args_path.is_symlink():
            raise PreparationError("args.gn already exists: {}".format(args_path))
    focus_version.check_existing_version(source / "chrome/VERSION")
    receipt_path = source / PREPARATION_RECEIPT
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PreparationError("preparation receipt already exists: {}".format(receipt_path))
    return {
        "source_root": str(source),
        "chromium_version": version,
        "acquisition": acquisition,
        "tool_bootstrap": tool_bootstrap,
        "offline": True,
        "network_operations": 0,
        "upstream_baseline_sha256": upstream_baseline,
        "dependencies": cache_report,
        "dependency_cache_marker": dependency_cache_marker,
        "dependency_archive_files": archive_files,
        "pruning": {
            "manifest_sha256": PRUNING_LIST_SHA256,
            "listed_files": len(prune_plan),
            "files_present": pruning_present,
            "future_archive_files": pruning_future,
            "already_absent_files": PRUNING_ALREADY_ABSENT_COUNT,
            "already_absent_sha256": PRUNING_ALREADY_ABSENT_SHA256,
            "contingent_paths_pruned": False,
            "directory_pruning_executed": False,
        },
        "patches": {
            "common_filtered": repository["shared_series"]["planned_entries"],
            "platform": len(repository["platform_patches"]),
            "total": len(patch_plan),
        },
        "overlay_files": len(overlay_files),
        "cleanup_paths": len(cleanup_paths),
        "resources": resource_destinations["copy_targets"],
        "resource_contract": resource_destinations,
        "icon_destination": MAC_ICON_DESTINATION,
        "args_gn": {key: value[0] + "/args.gn" for key, value in gn_plan.items()},
        "archives": archives,
        "ready": True,
    }


def resume_preflight_exact(source_root, cache_root, applied_patches):
    """Prove one exact audited patch-failure checkpoint without mutation."""
    if type(applied_patches) is not int:
        raise PreparationError("resume patch checkpoint must be an integer")
    expected_working_tree = expected_resume_working_tree(applied_patches)
    source_input = Path(source_root).expanduser()
    if source_input.is_symlink():
        raise PreparationError("Chromium source argument must not be a symlink")
    source, version = focus_macos.resolve_source_root(str(source_input))
    acquisition = validate_acquisition_marker(source)
    tool_bootstrap = validate_tool_bootstrap_marker(source, acquisition)
    git_head = validate_pinned_git_head(source)
    repository = focus_macos.validate_repository_contract()
    contracts = validate_dependency_manifest()
    cache, cache_report = validate_offline_cache(cache_root, contracts)
    dependency_cache_marker = validate_dependency_cache_marker(cache, contracts)

    receipt_path = reject_symlink_ancestors(
        source, safe_relative(PREPARATION_RECEIPT, "preparation receipt")
    )
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PreparationError("preparation receipt already exists during resume")
    for _, (out_dir, _) in args_gn_plan().items():
        relative = PurePosixPath(safe_relative(out_dir, "GN output"), "args.gn").as_posix()
        path = reject_symlink_ancestors(source, relative)
        if path.exists() or path.is_symlink():
            raise PreparationError("args.gn already exists during resume: {}".format(path))
    strings_path = reject_symlink_ancestors(source, ONBOARDING_STRINGS_OUTPUT)
    if strings_path.exists() or strings_path.is_symlink():
        raise PreparationError("onboarding strings already exist during resume")
    focus_version.check_existing_version(
        require_regular_in_tree(source, "chrome/VERSION", "Chromium VERSION")
    )

    dependency_tree = installed_dependency_tree(source, contracts)
    expected_tree = expected_resume_dependency_tree(applied_patches)
    if dependency_tree != expected_tree:
        raise PreparationError("installed dependency tree changed before resume")
    pruning_checkpoint = validate_completed_pruning(source)
    working_tree = working_tree_inventory(source)
    if working_tree != expected_working_tree:
        raise PreparationError(
            "resume working-tree checkpoint mismatch: expected {}/{}, got {}/{}".format(
                expected_working_tree["records"],
                expected_working_tree["sha256"],
                working_tree["records"],
                working_tree["sha256"],
            )
        )

    ignored_tree = ignored_working_tree_inventory(source)
    expected_ignored_tree = expected_ignored_working_tree_inventory()
    if ignored_tree != expected_ignored_tree:
        raise PreparationError(
            "resume ignored-tree checkpoint mismatch: expected {}/{}, got {}/{}".format(
                expected_ignored_tree["records"],
                expected_ignored_tree["sha256"],
                ignored_tree["records"],
                ignored_tree["sha256"],
            )
        )

    patch_plan = build_patch_plan()
    validate_patch_tool()
    last_path = patch_plan[applied_patches - 1]
    next_path = (
        patch_plan[applied_patches]
        if applied_patches < len(patch_plan)
        else None
    )
    check_patch_boundary(source, last_path, reverse=True)
    if next_path is not None:
        check_patch_boundary(source, next_path, reverse=False)
    checkpoint = {
        "git_head": git_head,
        "working_tree": working_tree,
        "ignored_tree": ignored_tree,
        "dependency_tree": dependency_tree,
        "pruning": pruning_checkpoint,
        "applied_prefix": patch_slice_inventory(
            patch_plan, 0, applied_patches
        ),
        "last_applied_patch": {
            "position": applied_patches,
            "path": str(last_path),
            "sha256": sha256_file(last_path),
            "reverse_applicable": True,
        },
        "next_patch": (
            {
                "position": applied_patches + 1,
                "path": str(next_path),
                "sha256": sha256_file(next_path),
                "forward_applicable": True,
            }
            if next_path is not None
            else None
        ),
    }
    execution = {
        "mode": "resume_exact_prefix",
        "initial_applied_patch_count": applied_patches,
        "patches_applied_this_run": len(patch_plan) - applied_patches,
        "total_patches": len(patch_plan),
        "resume_checkpoint": checkpoint,
    }
    validate_preparation_execution_report(execution)
    overlay_files, cleanup_paths, _ = build_overlay_plan()
    resource_plan = parse_resource_plan()
    resource_destinations = resource_destination_inventory(source, resource_plan)
    focus_macos.validate_icns_asset()
    return {
        "source_root": str(source),
        "chromium_version": version,
        "acquisition": acquisition,
        "tool_bootstrap": tool_bootstrap,
        "offline": True,
        "network_operations": 0,
        "upstream_baseline_sha256": expected_upstream_source_contracts(),
        "dependencies": cache_report,
        "dependency_cache_marker": dependency_cache_marker,
        "dependency_checkpoint": dependency_tree,
        "dependency_install": expected_dependency_install_report(),
        "pruning_checkpoint": pruning_checkpoint,
        "pruning": {
            "manifest_sha256": PRUNING_LIST_SHA256,
            "listed_files": PRUNING_ENTRY_COUNT,
            "files_removed": PRUNING_EXPECTED_REMOVAL_COUNT,
            "already_absent_files": PRUNING_ALREADY_ABSENT_COUNT,
            "already_absent_sha256": PRUNING_ALREADY_ABSENT_SHA256,
            "contingent_paths_pruned": False,
            "directory_pruning_executed": False,
        },
        "patches": {
            "common_filtered": repository["shared_series"]["planned_entries"],
            "platform": len(repository["platform_patches"]),
            "total": len(patch_plan),
            "initially_applied": applied_patches,
            "remaining": len(patch_plan) - applied_patches,
        },
        "preparation_execution": execution,
        "overlay_files": len(overlay_files),
        "cleanup_paths": len(cleanup_paths),
        "resources": resource_destinations["copy_targets"],
        "resource_contract": resource_destinations,
        "icon_destination": MAC_ICON_DESTINATION,
        "args_gn": {
            key: value[0] + "/args.gn" for key, value in args_gn_plan().items()
        },
        "resume_ready": True,
    }


def validate_applied_overlay_checkpoint(source_root, overlay_files, cleanup_paths):
    """Require every overlay byte applied and every cleanup target absent."""
    source_root = require_real_directory(source_root, "Chromium source")
    for source, relative in overlay_files:
        destination = require_regular_in_tree(
            source_root, relative, "applied overlay destination"
        )
        if (
            destination.stat().st_size != source.stat().st_size
            or sha256_file(destination) != sha256_file(source)
        ):
            raise PreparationError(
                "applied overlay destination differs: {}".format(destination)
            )
    for relative in cleanup_paths:
        target = reject_symlink_ancestors(
            source_root, relative, include_leaf=False
        )
        if target.exists() or target.is_symlink():
            raise PreparationError(
                "overlay cleanup target is still present: {}".format(target)
            )
    return {
        "overlay_files_matching": len(overlay_files),
        "cleanup_targets_absent": len(cleanup_paths),
    }


def validate_post_version_artifacts(source_root):
    """Prove that version/overlay completed and later phases never started."""
    source_root = require_real_directory(source_root, "Chromium source")
    version_path = require_regular_in_tree(
        source_root, "chrome/VERSION", "post-version Chromium VERSION"
    )
    if (
        version_path.stat().st_size != POST_VERSION_CHROME_VERSION_BYTES
        or sha256_file(version_path) != POST_VERSION_CHROME_VERSION_SHA256
    ):
        raise PreparationError("post-version Chromium VERSION checkpoint mismatch")
    version_text = version_path.read_text(encoding="utf-8")
    expected_focus_lines = (
        "FOCUS_MAJOR=1",
        "FOCUS_MINOR=0",
        "FOCUS_PATCH=5",
        "FOCUS_PLATFORM=0",
    )
    if any(version_text.splitlines().count(line) != 1 for line in expected_focus_lines):
        raise PreparationError("post-version Focus version lines mismatch")

    strings = require_regular_in_tree(
        source_root,
        ONBOARDING_STRINGS_OUTPUT,
        "post-overlay onboarding strings baseline",
    )
    if (
        strings.stat().st_size != ONBOARDING_STRINGS_BASELINE_BYTES
        or sha256_file(strings) != ONBOARDING_STRINGS_BASELINE_SHA256
    ):
        raise PreparationError("post-overlay onboarding strings baseline mismatch")
    generator = require_regular_in_tree(
        source_root, ONBOARDING_GENERATOR, "onboarding i18n generator"
    )
    if sha256_file(generator) != ONBOARDING_GENERATOR_SHA256:
        raise PreparationError("onboarding i18n generator hash mismatch")
    node = onboarding_node_contract(source_root)

    icon = validate_icon_destination(source_root)
    if (
        icon.stat().st_size != POST_VERSION_UPSTREAM_ICNS_BYTES
        or sha256_file(icon) != POST_VERSION_UPSTREAM_ICNS_SHA256
    ):
        raise PreparationError("Focus ICNS phase already started or icon changed")
    focus_macos.validate_icns_asset()

    branding = require_regular_in_tree(
        source_root, "chrome/app/theme/chromium/BRANDING", "Focus Chromium branding"
    ).read_text(encoding="utf-8")
    for line in (
        "PRODUCT_FULLNAME=Focus Browser",
        "PRODUCT_SHORTNAME=Focus Browser",
        "MAC_BUNDLE_ID={}".format(focus_macos.BUNDLE_ID),
    ):
        if branding.splitlines().count(line) != 1:
            raise PreparationError(
                "post-overlay Chromium branding is missing {!r}".format(line)
            )

    for _, (out_dir, _) in args_gn_plan().items():
        args_path = reject_symlink_ancestors(
            source_root,
            PurePosixPath(out_dir, "args.gn").as_posix(),
            include_leaf=False,
        )
        if args_path.exists() or args_path.is_symlink():
            raise PreparationError("args.gn phase already started: {}".format(args_path))
    receipt = reject_symlink_ancestors(
        source_root, PREPARATION_RECEIPT, include_leaf=False
    )
    if receipt.exists() or receipt.is_symlink():
        raise PreparationError("preparation receipt already exists: {}".format(receipt))
    return {
        "chrome_version_sha256": POST_VERSION_CHROME_VERSION_SHA256,
        "focus_version": "1.0.5.0",
        "onboarding_baseline_sha256": ONBOARDING_STRINGS_BASELINE_SHA256,
        "upstream_icns_sha256": POST_VERSION_UPSTREAM_ICNS_SHA256,
        "onboarding_node": node,
        "args_gn_absent": True,
        "receipt_absent": True,
    }


def resume_post_version_preflight(source_root, cache_root):
    """Prove the exact post-version/pre-resource failure without mutation."""
    source_input = Path(source_root).expanduser()
    if source_input.is_symlink():
        raise PreparationError("Chromium source argument must not be a symlink")
    source, version = focus_macos.resolve_source_root(str(source_input))
    acquisition = validate_acquisition_marker(source)
    tool_bootstrap = validate_tool_bootstrap_marker(source, acquisition)
    git_head = validate_pinned_git_head(source)
    repository = focus_macos.validate_repository_contract()
    contracts = validate_dependency_manifest()
    cache, cache_report = validate_offline_cache(cache_root, contracts)
    dependency_cache_marker = validate_dependency_cache_marker(cache, contracts)

    working_tree = working_tree_inventory(source)
    if working_tree != expected_post_version_working_tree():
        raise PreparationError("post-version working-tree checkpoint mismatch")
    ignored_tree = ignored_working_tree_inventory(source)
    if ignored_tree != expected_post_version_ignored_tree():
        raise PreparationError("post-version ignored-tree checkpoint mismatch")
    dependency_tree = installed_dependency_tree(source, contracts)
    if dependency_tree != expected_post_version_dependency_tree():
        raise PreparationError("post-version dependency-tree checkpoint mismatch")
    pruning_checkpoint = validate_completed_pruning(source)

    patch_plan = build_patch_plan()
    validate_patch_tool()
    check_patch_boundary(source, patch_plan[-1], reverse=True)
    execution = expected_resume_execution_report(RESUME_FULL_PATCH_SET_APPLIED)
    validate_preparation_execution_report(execution)

    overlay_files, cleanup_paths, _ = build_overlay_plan()
    overlay_checkpoint = validate_applied_overlay_checkpoint(
        source, overlay_files, cleanup_paths
    )
    artifacts = validate_post_version_artifacts(source)
    resource_plan = parse_resource_plan()
    resources = resource_destination_inventory(source, resource_plan)
    if resources != expected_post_version_resource_inventory():
        raise PreparationError("post-version resource inventory mismatch")

    return {
        "source_root": str(source),
        "chromium_version": version,
        "acquisition": acquisition,
        "tool_bootstrap": tool_bootstrap,
        "offline": True,
        "network_operations": 0,
        "upstream_baseline_sha256": expected_upstream_source_contracts(),
        "dependencies": cache_report,
        "dependency_cache_marker": dependency_cache_marker,
        "dependency_checkpoint": dependency_tree,
        "dependency_install": expected_dependency_install_report(),
        "pruning_checkpoint": pruning_checkpoint,
        "pruning": {
            "manifest_sha256": PRUNING_LIST_SHA256,
            "listed_files": PRUNING_ENTRY_COUNT,
            "files_removed": PRUNING_EXPECTED_REMOVAL_COUNT,
            "already_absent_files": PRUNING_ALREADY_ABSENT_COUNT,
            "already_absent_sha256": PRUNING_ALREADY_ABSENT_SHA256,
            "contingent_paths_pruned": False,
            "directory_pruning_executed": False,
        },
        "patches": {
            "common_filtered": repository["shared_series"]["planned_entries"],
            "platform": len(repository["platform_patches"]),
            "total": len(patch_plan),
            "initially_applied": RESUME_FULL_PATCH_SET_APPLIED,
            "remaining": 0,
        },
        "preparation_execution": execution,
        "recovery_checkpoint": {
            "phase": "post_version_pre_resources",
            "git_head": git_head,
            "working_tree": working_tree,
            "ignored_tree": ignored_tree,
            "dependency_tree": dependency_tree,
            "pruning": pruning_checkpoint,
            "overlay": overlay_checkpoint,
            "artifacts": artifacts,
            "resources": resources,
        },
        "resources": resources["copy_targets"],
        "resource_contract": resources,
        "icon_destination": MAC_ICON_DESTINATION,
        "args_gn": {
            key: value[0] + "/args.gn" for key, value in args_gn_plan().items()
        },
        "resume_ready": True,
    }


def create_finalizer_rollback_snapshot(source_root, resource_plan, snapshot_root):
    """Snapshot every existing file touched by the recovery finalizer."""
    source_root = require_real_directory(source_root, "Chromium source")
    snapshot_root = require_real_directory(snapshot_root, "finalizer rollback snapshot")
    copy_plan, _ = validate_resource_destinations(source_root, resource_plan)
    icon = validate_icon_destination(source_root)
    strings = require_regular_in_tree(
        source_root, ONBOARDING_STRINGS_OUTPUT, "onboarding strings rollback input"
    )
    targets = [destination for _, destination in copy_plan] + [icon, strings]
    relative_targets = [target.relative_to(source_root).as_posix() for target in targets]
    if len(relative_targets) != len(set(relative_targets)):
        raise PreparationError("finalizer rollback targets overlap")

    files = []
    for position, target in enumerate(targets, 1):
        backup = snapshot_root / "{:03d}.backup".format(position)
        atomic_copy(target, backup)
        expected_hash = sha256_file(target)
        if (
            backup.stat().st_size != target.stat().st_size
            or sha256_file(backup) != expected_hash
        ):
            raise PreparationError("finalizer rollback snapshot mismatch")
        files.append(
            {
                "relative": target.relative_to(source_root).as_posix(),
                "backup": str(backup),
                "bytes": target.stat().st_size,
                "sha256": expected_hash,
                "mode": stat.S_IMODE(target.stat().st_mode),
            }
        )

    args = []
    for architecture, (out_dir, _) in args_gn_plan().items():
        relative = PurePosixPath(out_dir, "args.gn").as_posix()
        path = reject_symlink_ancestors(
            source_root, relative, include_leaf=False
        )
        if path.exists() or path.is_symlink():
            raise PreparationError("args.gn appeared before finalizer snapshot")
        args.append(
            {
                "architecture": architecture,
                "relative": relative,
                "parent_existed": path.parent.exists(),
            }
        )
    receipt = reject_symlink_ancestors(
        source_root, PREPARATION_RECEIPT, include_leaf=False
    )
    if receipt.exists() or receipt.is_symlink():
        raise PreparationError("receipt appeared before finalizer snapshot")
    return {
        "files": files,
        "args": args,
        "receipt": PREPARATION_RECEIPT,
    }


def restore_finalizer_rollback_snapshot(source_root, resource_plan, snapshot):
    """Restore the exact retryable checkpoint after any finalizer exception."""
    source_root = require_real_directory(source_root, "Chromium source")
    receipt = reject_symlink_ancestors(
        source_root, snapshot["receipt"], include_leaf=False
    )
    if receipt.is_symlink() or (receipt.exists() and not receipt.is_file()):
        raise PreparationError("cannot safely remove failed finalizer receipt")
    if receipt.is_file():
        receipt.unlink()

    for entry in snapshot["args"]:
        path = reject_symlink_ancestors(
            source_root, entry["relative"], include_leaf=False
        )
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise PreparationError("cannot safely remove failed finalizer args.gn")
        if path.is_file():
            path.unlink()

    for entry in snapshot["files"]:
        target = reject_symlink_ancestors(
            source_root, entry["relative"], include_leaf=False
        )
        backup = Path(entry["backup"])
        if target.is_symlink() or not target.is_file():
            raise PreparationError("finalizer rollback target became unsafe")
        if backup.is_symlink() or not backup.is_file():
            raise PreparationError("finalizer rollback backup became unsafe")
        atomic_copy(backup, target)
        if (
            target.stat().st_size != entry["bytes"]
            or stat.S_IMODE(target.stat().st_mode) != entry["mode"]
            or sha256_file(target) != entry["sha256"]
        ):
            raise PreparationError("finalizer rollback file mismatch")

    for entry in snapshot["args"]:
        path = source_root / entry["relative"]
        if not entry["parent_existed"] and path.parent.is_dir():
            try:
                path.parent.rmdir()
            except OSError:
                pass

    if working_tree_inventory(source_root) != expected_post_version_working_tree():
        raise PreparationError("rollback did not restore post-version working tree")
    if ignored_working_tree_inventory(source_root) != expected_post_version_ignored_tree():
        raise PreparationError("rollback did not restore post-version ignored tree")
    if installed_dependency_tree(source_root, DEPENDENCY_CONTRACTS) != (
        expected_post_version_dependency_tree()
    ):
        raise PreparationError("rollback did not restore post-version dependencies")
    validate_post_version_artifacts(source_root)
    if resource_destination_inventory(source_root, resource_plan) != (
        expected_post_version_resource_inventory()
    ):
        raise PreparationError("rollback did not restore resource destinations")
    return True


def resume_post_version_failure(source_root, cache_root):
    """Finish only the phases after the exact failed resource checkpoint."""
    report = resume_post_version_preflight(source_root, cache_root)
    source = Path(report["source_root"])
    cache = require_real_directory(cache_root, "offline cache")
    watched_filesystems = (source, cache)
    disk_gates = []

    def gate(phase):
        disk_gates.append(require_disk_floor(watched_filesystems, phase))

    gate("post-version checkpoint revalidation")
    if working_tree_inventory(source) != expected_post_version_working_tree():
        raise PreparationError("post-version working tree changed after preflight")
    if ignored_working_tree_inventory(source) != expected_post_version_ignored_tree():
        raise PreparationError("post-version ignored tree changed after preflight")
    if installed_dependency_tree(source, DEPENDENCY_CONTRACTS) != (
        expected_post_version_dependency_tree()
    ):
        raise PreparationError("post-version dependency tree changed after preflight")
    validate_post_version_artifacts(source)
    resource_plan = parse_resource_plan()
    if resource_destination_inventory(source, resource_plan) != (
        expected_post_version_resource_inventory()
    ):
        raise PreparationError("resource destinations changed after preflight")

    snapshot_root = Path(
        tempfile.mkdtemp(prefix="focus-finalizer-rollback-")
    ).resolve()
    snapshot = None
    try:
        snapshot = create_finalizer_rollback_snapshot(
            source, resource_plan, snapshot_root
        )
    except BaseException:
        shutil.rmtree(snapshot_root, ignore_errors=True)
        raise
    try:
        gate("common resource copy")
        resource_count = copy_common_resources(source, resource_plan)
        gate("pinned ICNS install")
        icon = install_focus_icns(source)
        gate("arm64/x64 args.gn write")
        args_paths = write_args_gn(source)
        gate("deterministic onboarding strings generation")
        localized_strings = generate_onboarding_strings(source)
        gate("preparation completion")
        receipt = write_preparation_receipt(
            source,
            report,
            args_paths,
            report["pruning"],
            report["dependency_install"],
            localized_strings,
            execution_report=report["preparation_execution"],
            recovery_checkpoint=report["recovery_checkpoint"],
        )
    except BaseException as original_error:
        try:
            restore_finalizer_rollback_snapshot(source, resource_plan, snapshot)
        except BaseException as rollback_error:
            raise PreparationError(
                "finalizer failed and rollback could not restore the exact "
                "checkpoint; rollback snapshot retained at {}: "
                "original={!r}; rollback={!r}".format(
                    snapshot_root, original_error, rollback_error
                )
            ) from original_error
        shutil.rmtree(snapshot_root)
        raise
    else:
        shutil.rmtree(snapshot_root)
    report.update(
        {
            "prepared": True,
            "patches_applied": RESUME_FULL_PATCH_SET_APPLIED,
            "patches_applied_this_run": 0,
            "recovery_phase": "post_version_pre_resources",
            "resources_copied": resource_count,
            "icns_installed": icon,
            "args_gn_written": args_paths,
            "localized_strings": localized_strings,
            "preparation_receipt": receipt,
            "disk_gates": disk_gates,
            "hard_disk_floor_gib": HARD_DISK_FLOOR_GIB,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
        }
    )
    return report


def resume_patch_failure(source_root, cache_root, applied_patches, workers=None):
    """Continue only an exact, fully audited patch preparation failure."""
    report = resume_preflight_exact(source_root, cache_root, applied_patches)
    source = Path(report["source_root"])
    cache = require_real_directory(cache_root, "offline cache")
    watched_filesystems = (source, cache)
    disk_gates = []

    def gate(phase):
        measurement = require_disk_floor(watched_filesystems, phase)
        disk_gates.append(measurement)

    gate("resume checkpoint revalidation")
    patch_plan = build_patch_plan()
    initial_applied = report["preparation_execution"][
        "initial_applied_patch_count"
    ]
    current_inventory = working_tree_inventory(source)
    if current_inventory != report["preparation_execution"]["resume_checkpoint"][
        "working_tree"
    ]:
        raise PreparationError("working tree changed after resume preflight")
    current_ignored = ignored_working_tree_inventory(source)
    if current_ignored != report["preparation_execution"]["resume_checkpoint"][
        "ignored_tree"
    ]:
        raise PreparationError("ignored tree changed after resume preflight")
    remaining_plan = patch_plan[initial_applied:]
    if remaining_plan:
        check_patch_boundary(source, remaining_plan[0], reverse=False)
    gate("remaining {}-patch batch".format(len(remaining_plan)))
    applied = []
    if remaining_plan:
        applied = apply_patch_plan(
            source,
            remaining_plan,
            base_position=initial_applied,
            total_patches=len(patch_plan),
        )
    gate("domain/name/i18n transformations")
    transformations = apply_common_transformations(source, workers=workers)
    overlay_files, cleanup_paths, _ = build_overlay_plan()
    gate("filtered overlay and cleanup")
    overlay_report = apply_overlay(source, overlay_files, cleanup_paths)
    gate("Focus version append")
    version = append_focus_version_once(source)
    resource_plan = parse_resource_plan()
    gate("common resource copy")
    resource_count = copy_common_resources(source, resource_plan)
    gate("pinned ICNS install")
    icon = install_focus_icns(source)
    gate("arm64/x64 args.gn write")
    args_paths = write_args_gn(source)
    gate("deterministic onboarding strings generation")
    localized_strings = generate_onboarding_strings(source)
    gate("preparation completion")
    receipt = write_preparation_receipt(
        source,
        report,
        args_paths,
        report["pruning"],
        report["dependency_install"],
        localized_strings,
        execution_report=report["preparation_execution"],
    )
    report.update(
        {
            "prepared": True,
            "patches_applied": len(patch_plan),
            "patches_applied_this_run": len(applied),
            "transformations": transformations,
            "overlay": overlay_report,
            "focus_version": version,
            "resources_copied": resource_count,
            "icns_installed": icon,
            "args_gn_written": args_paths,
            "localized_strings": localized_strings,
            "preparation_receipt": receipt,
            "disk_gates": disk_gates,
            "hard_disk_floor_gib": HARD_DISK_FLOOR_GIB,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
        }
    )
    return report


def prepare(source_root, cache_root, workers=None):
    """Execute the validated offline preparation pipeline (but never build)."""
    report = preflight(source_root, cache_root)
    source = Path(report["source_root"])
    contracts = validate_dependency_manifest()
    cache, _ = validate_offline_cache(cache_root, contracts)
    watched_filesystems = (source, cache)
    disk_gates = []

    def gate(phase):
        measurement = require_disk_floor(watched_filesystems, phase)
        disk_gates.append(measurement)

    with tempfile.TemporaryDirectory(prefix="focus-macos-deps-") as temporary:
        stage = Path(temporary)
        gate("dependency staging")
        for name, contract in contracts.items():
            extract_archive_to_stage(
                cache / contract["download_filename"], contract, stage / name
            )
        gate("dependency merge")
        dependency_report = merge_staged_dependencies(source, stage, contracts)

    expected_absent_pruning = load_expected_absent_pruning()
    prune_plan = build_prune_plan(
        source,
        expected_absent_paths=expected_absent_pruning,
    )
    gate("file-only binary pruning")
    pruning_report = apply_prune_plan(
        source, prune_plan, expected_absent_paths=expected_absent_pruning
    )
    patch_plan = build_patch_plan()
    gate("324-patch batch")
    applied = apply_patch_plan(source, patch_plan)
    gate("domain/name/i18n transformations")
    transformations = apply_common_transformations(source, workers=workers)
    overlay_files, cleanup_paths, _ = build_overlay_plan()
    gate("filtered overlay and cleanup")
    overlay_report = apply_overlay(source, overlay_files, cleanup_paths)
    gate("Focus version append")
    version = append_focus_version_once(source)
    resource_plan = parse_resource_plan()
    gate("common resource copy")
    resource_count = copy_common_resources(source, resource_plan)
    gate("pinned ICNS install")
    icon = install_focus_icns(source)
    gate("arm64/x64 args.gn write")
    args_paths = write_args_gn(source)
    gate("deterministic onboarding strings generation")
    localized_strings = generate_onboarding_strings(source)
    gate("preparation completion")
    receipt = write_preparation_receipt(
        source,
        report,
        args_paths,
        pruning_report,
        dependency_report,
        localized_strings,
        execution_report=fresh_preparation_execution_report(len(patch_plan)),
    )
    report.update(
        {
            "prepared": True,
            "dependency_install": dependency_report,
            "pruning": pruning_report,
            "patches_applied": len(applied),
            "transformations": transformations,
            "overlay": overlay_report,
            "focus_version": version,
            "resources_copied": resource_count,
            "icns_installed": icon,
            "args_gn_written": args_paths,
            "localized_strings": localized_strings,
            "preparation_receipt": receipt,
            "disk_gates": disk_gates,
            "hard_disk_floor_gib": HARD_DISK_FLOOR_GIB,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
        }
    )
    return report


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "preflight",
        "prepare",
        "resume-preflight",
        "resume-patch-failure",
        "resume-finalize-preflight",
        "resume-finalize",
    ):
        child = subparsers.add_parser(command)
        child.add_argument("--source-root", required=True)
        child.add_argument("--cache", required=True)
        child.add_argument("--json", action="store_true")
        if command in ("prepare", "resume-patch-failure", "resume-finalize"):
            child.add_argument(
                "--confirm-source-mutation",
                action="store_true",
                help="required acknowledgement that the Chromium checkout will be modified",
            )
        if command in ("prepare", "resume-patch-failure"):
            child.add_argument("--workers", type=int)
        if command in ("resume-preflight", "resume-patch-failure"):
            child.add_argument(
                "--applied-patches",
                required=True,
                type=int,
                help="must equal an explicitly audited patch-failure checkpoint",
            )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in ("prepare", "resume-patch-failure", "resume-finalize"):
            if not args.confirm_source_mutation:
                raise PreparationError(
                    "{} requires --confirm-source-mutation".format(args.command)
                )
            workers = getattr(args, "workers", None)
            if workers is not None and workers < 1:
                raise PreparationError("--workers must be positive")
            if args.command == "resume-finalize":
                report = resume_post_version_failure(args.source_root, args.cache)
            elif args.command == "resume-patch-failure":
                report = resume_patch_failure(
                    args.source_root,
                    args.cache,
                    args.applied_patches,
                    workers=workers,
                )
            else:
                report = prepare(args.source_root, args.cache, workers=workers)
        elif args.command == "resume-finalize-preflight":
            report = resume_post_version_preflight(args.source_root, args.cache)
        elif args.command == "resume-preflight":
            report = resume_preflight_exact(
                args.source_root, args.cache, args.applied_patches
            )
        else:
            report = preflight(args.source_root, args.cache)
    except (PreparationError, focus_macos.ContractError, ValueError, OSError) as exc:
        parser.exit(2, "error: {}\n".format(exc))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        state = "complete" if report.get("prepared") else "ready"
        print("offline source preparation: {}".format(state))
        print("Chromium: {} ({})".format(report["chromium_version"], report["source_root"]))
        print("patches: {} (321 common + 3 macOS)".format(report["patches"]["total"]))
        print("network operations: 0; build/sign/package: not executed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
