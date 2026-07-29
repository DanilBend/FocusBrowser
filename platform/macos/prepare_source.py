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
import os
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
DEPS_INI = REPO_ROOT / "focus-chromium" / "deps.ini"
DEPS_INI_SHA256 = "158806c990d70174a6f401ae488d03246d867e0272b753bfbcb7c1757633b9ea"
DOMAIN_REGEX_SHA256 = "cf128b0f182692dbf90553aaedd0d3ebc1982076dd94ad94f344bb3677455d2c"
DOMAIN_LIST_SHA256 = "e9661a754d4c15778cecabc1e9cbbb40a3876de5018d9d49d3d98e998acffd1d"
RESOURCE_LIST_SHA256 = "e1d545a3dfd4e91f561a3800524f7e098665dfcf35f8619735e09412906c713a"
GENERATE_LIST_SHA256 = "02da891cb3b867e9bc806b9ab3b433fd3d8c01024fac41d5fa60c78d11b6aca9"
RESOURCE_BODY_COUNT = 60
RESOURCE_BODY_SHA256 = "9437baf40f66604ba984bd2cb4c8aba9180ca39dd6e12d8444b19af1ae096d7a"
MAC_ICON_DESTINATION = "chrome/app/theme/chromium/mac/app.icns"
MAC_ICON_BUILD_TOKEN = "app/theme/$branding_path_component/mac/app.icns"
CHROME_BUILD_GN_SHA256 = "3851bd31f3f9bc123395dbd966557885d62911f4e1359bca47390bfc942653e4"
INSTALLER_MAC_BUILD_GN = "chrome/installer/mac/BUILD.gn"
INSTALLER_MAC_BUILD_GN_SHA256 = "e620eb87d619dc384c050e041bc9d524037f7ff3f5255f39b5e034025351bd4d"
PREPARATION_RECEIPT = "out/FocusMacPreparation.json"
PRUNING_LIST = REPO_ROOT / "focus-chromium" / "pruning.list"
PRUNING_LIST_SHA256 = "bd08456aebb271572261a9c387cc4c8d4944264cfd8044c3f165b82e3a31b5d1"
PRUNING_ENTRY_COUNT = 13800
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

DEPENDENCY_CONTRACTS = OrderedDict(
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
    """Pin deps.ini and the three project dependencies used by the shared build."""
    if DEPS_INI.is_symlink() or not DEPS_INI.is_file():
        raise PreparationError("missing regular dependency manifest: {}".format(DEPS_INI))
    actual_hash = sha256_file(DEPS_INI)
    if actual_hash != DEPS_INI_SHA256:
        raise PreparationError(
            "deps.ini hash mismatch: expected {}, got {}".format(DEPS_INI_SHA256, actual_hash)
        )
    parser = configparser.ConfigParser()
    parser.read(DEPS_INI, encoding="utf-8")
    if parser.sections() != list(DEPENDENCY_CONTRACTS):
        raise PreparationError("deps.ini component order/inventory changed")
    for name, expected in DEPENDENCY_CONTRACTS.items():
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


def _stripped_archive_path(name, prefix, label):
    normalized = safe_relative(name.rstrip("/"), label)
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
                    continue
                value = relative.as_posix()
                if value in seen:
                    raise PreparationError("duplicate archive destination: {}".format(value))
                seen.add(value)
                entries.append((value, member.filename, member.file_size, mode & 0o777))
    elif contract["kind"] == "tar":
        with tarfile.open(archive, "r:*") as stream:
            for member in stream.getmembers():
                if member.isdir():
                    _stripped_archive_path(member.name, prefix, "tar member")
                    continue
                if not member.isfile():
                    raise PreparationError(
                        "tar member is not a regular file: {}".format(member.name)
                    )
                relative = _stripped_archive_path(member.name, prefix, "tar member")
                if relative is None:
                    continue
                value = relative.as_posix()
                if value in seen:
                    raise PreparationError("duplicate archive destination: {}".format(value))
                seen.add(value)
                entries.append((value, member.name, member.size, member.mode & 0o777))
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


def merge_staged_dependencies(source_root, stage_root, contracts):
    """Copy staged dependency regular files into their declared source paths."""
    source_root = require_real_directory(source_root, "Chromium source")
    plan = []
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
            destination = reject_symlink_ancestors(
                source_root, destination_relative, include_leaf=False
            )
            if destination.is_symlink() or (destination.exists() and not destination.is_file()):
                raise PreparationError("unsafe dependency destination: {}".format(destination))
            plan.append((name, source, destination))
    for _, source, destination in plan:
        atomic_copy(source, destination)
    return {"files_copied": len(plan), "components": list(contracts)}


def build_prune_plan(
    source_root,
    manifest=PRUNING_LIST,
    expected_hash=PRUNING_LIST_SHA256,
    expected_count=PRUNING_ENTRY_COUNT,
):
    """Prevalidate the exact file-only binary pruning plan."""
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
    entries = []
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
        target = require_regular_in_tree(source_root, relative, "pruning target")
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
            }
        )
    if len(entries) != expected_count:
        raise PreparationError(
            "pruning entry count mismatch: expected {}, got {}".format(
                expected_count, len(entries)
            )
        )
    return entries


def apply_prune_plan(source_root, plan):
    """Delete only prevalidated listed files; retain all contingent directories."""
    source_root = require_real_directory(source_root, "Chromium source")
    checked = []
    for entry in plan:
        relative = safe_relative(entry["relative"], "pruning entry")
        target = require_regular_in_tree(source_root, relative, "pruning target")
        metadata = target.lstat()
        identity = (metadata.st_dev, metadata.st_ino, metadata.st_size)
        expected = (entry["device"], entry["inode"], entry["size"])
        if identity != expected:
            raise PreparationError("pruning target changed after preflight: {}".format(target))
        checked.append(target)
    for target in checked:
        target.unlink()
    return {
        "manifest_sha256": PRUNING_LIST_SHA256,
        "listed_files": len(checked),
        "files_removed": len(checked),
        "contingent_paths_pruned": False,
        "directory_pruning_executed": False,
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
    """Build one noninteractive BSD patch command with exact fuzz zero."""
    command = [
        str(patch_bin),
        "-f",
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


def apply_patch_plan(source_root, patch_plan, patch_bin=SYSTEM_PATCH, runner=subprocess.run):
    """Check then apply every patch in order with system patch and fuzz=0."""
    source_root = require_real_directory(source_root, "Chromium source")
    patch_bin = validate_patch_tool(patch_bin)
    applied = []
    for position, patch_path in enumerate(patch_plan, 1):
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
                        patch_path, phase, position, len(patch_plan), detail
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


def validate_domain_targets(source_root):
    """Require every listed domain-substitution input to be an in-tree file."""
    regex_path = _validate_pinned_file(
        REPO_ROOT / "focus-chromium" / "domain_regex.list",
        DOMAIN_REGEX_SHA256,
        "domain regex list",
    )
    files_path = _validate_pinned_file(
        REPO_ROOT / "focus-chromium" / "domain_substitution.list",
        DOMAIN_LIST_SHA256,
        "domain substitution list",
    )
    count = 0
    for number, line in enumerate(files_path.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if not value:
            continue
        relative = safe_relative(value, "domain list line {}".format(number))
        require_regular_in_tree(source_root, relative, "domain target")
        count += 1
    return regex_path, files_path, count


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
    regex_path, files_path, domain_count = validate_domain_targets(source_root)
    domain_substitution.apply_substitution(regex_path, files_path, Path(source_root), None)
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
        "domain_targets": domain_count,
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


def copy_common_resources(source_root, resource_plan):
    """Replace only declared existing Chromium resource files."""
    source_root = require_real_directory(source_root, "Chromium source")
    plan = []
    for source, relative in resource_plan:
        destination = require_regular_in_tree(
            source_root, relative, "Chromium resource destination"
        )
        plan.append((source, destination))
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
    mac_contract = focus_macos.validate_chromium_macos_build_contract(source_root)
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
    return OrderedDict(
        (
            ("chrome/BUILD.gn", CHROME_BUILD_GN_SHA256),
            (INSTALLER_MAC_BUILD_GN, INSTALLER_MAC_BUILD_GN_SHA256),
            (
                focus_macos.CHROMIUM_MAC_SDK_GNI,
                mac_contract["pinned_files"][focus_macos.CHROMIUM_MAC_SDK_GNI],
            ),
            (
                focus_macos.CHROMIUM_UNIVERSALIZER,
                mac_contract["pinned_files"][focus_macos.CHROMIUM_UNIVERSALIZER],
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


def write_preparation_receipt(source_root, preflight_report, args_paths):
    """Write the deterministic post-preparation provenance receipt once."""
    source_root = require_real_directory(source_root, "Chromium source")
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
    receipt = OrderedDict(
        (
            ("schema", 1),
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
            (
                "dependency_contract",
                {
                    "manifest_sha256": DEPS_INI_SHA256,
                    "archives": {
                        name: contract["sha256"]
                        for name, contract in DEPENDENCY_CONTRACTS.items()
                    },
                },
            ),
            (
                "pruning_contract",
                {
                    "manifest_sha256": PRUNING_LIST_SHA256,
                    "listed_files": PRUNING_ENTRY_COUNT,
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
    with receipt_path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
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
    archives = {}
    archive_files = 0
    for name, contract in contracts.items():
        archive = cache / contract["download_filename"]
        entries = inspect_archive(archive, contract)
        archives[name] = str(archive)
        archive_files += len(entries)
    patch_plan = build_patch_plan()
    validate_patch_tool()
    prune_plan = build_prune_plan(source)
    overlay_files, cleanup_paths, _ = build_overlay_plan()
    resource_plan = parse_resource_plan()
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
        "dependency_archive_files": archive_files,
        "pruning": {
            "manifest_sha256": PRUNING_LIST_SHA256,
            "listed_files": len(prune_plan),
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
        "resources": len(resource_plan),
        "icon_destination": MAC_ICON_DESTINATION,
        "args_gn": {key: value[0] + "/args.gn" for key, value in gn_plan.items()},
        "archives": archives,
        "ready": True,
    }


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

    prune_plan = build_prune_plan(source)
    gate("file-only binary pruning")
    pruning_report = apply_prune_plan(source, prune_plan)
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
    gate("preparation receipt write")
    receipt = write_preparation_receipt(source, report, args_paths)
    gate("post-preparation completion")
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
    for command in ("preflight", "prepare"):
        child = subparsers.add_parser(command)
        child.add_argument("--source-root", required=True)
        child.add_argument("--cache", required=True)
        child.add_argument("--json", action="store_true")
        if command == "prepare":
            child.add_argument(
                "--confirm-source-mutation",
                action="store_true",
                help="required acknowledgement that the Chromium checkout will be modified",
            )
            child.add_argument("--workers", type=int)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            if not args.confirm_source_mutation:
                raise PreparationError("prepare requires --confirm-source-mutation")
            if args.workers is not None and args.workers < 1:
                raise PreparationError("--workers must be positive")
            report = prepare(args.source_root, args.cache, workers=args.workers)
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
