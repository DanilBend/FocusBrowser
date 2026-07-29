#!/usr/bin/env python3
"""Run the reviewed low-space native Focus Browser macOS build stages.

Every command is a dry run unless ``--execute`` is present.  The stages are
deliberately separate so a 16 GiB Mac can build arm64, preserve the thin app,
reclaim only that measured output, and then build x86_64.  This tool never
publishes, notarizes, uses a Developer ID, changes xcode-select, or targets a
non-macOS platform.
"""

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


MACOS_DIR = Path(__file__).resolve().parent
if str(MACOS_DIR) not in sys.path:
    sys.path.insert(0, str(MACOS_DIR))

import acquire_chromium  # pylint: disable=wrong-import-position
import focus_macos  # pylint: disable=wrong-import-position
import package_local_dmg  # pylint: disable=wrong-import-position
import prepare_source  # pylint: disable=wrong-import-position


GIB = 1024 ** 3
SOFT_FLOOR_GIB = 35
HARD_FLOOR_GIB = 30
BOOTSTRAP_POST_GIB = 70
BUILD_JOBS = 4
POLL_SECONDS = 2.0

APP_NAME = "Focus Browser.app"
PACKAGING_NAME = "Focus Browser Packaging"
TOOL_RECEIPT = ".focus-macos-tool-bootstrap.json"
PREPARATION_RECEIPT = "out/FocusMacPreparation.json"
ARM_OUT = "out/FocusMacArm64"
X64_OUT = "out/FocusMacX64"
STAGING_ROOT = "out/FocusMacStaging"
STAGED_ARM_APP = STAGING_ROOT + "/arm64/" + APP_NAME
STAGE_RECEIPT = STAGING_ROOT + "/arm64-receipt.json"
RECLAIM_RECEIPT = STAGING_ROOT + "/arm64-reclaim-complete.json"
UNSIGNED_ROOT = "out/FocusMacUnsignedUniversal"
SIGNED_ROOT = "out/FocusMacSignedUniversal"
SLICE_RECEIPT_NAME = "FocusMacBuild.json"

DAWN_NINJA_RELATIVE = "third_party/dawn/third_party/ninja/ninja"
NINJA_VERSION = "1.12.1"
NINJA_CIPD_VERSION = "version:3@1.12.1.chromium.4"
NINJA_SHA256_BY_HOST = {
    "arm64": "6c03e94e3ee141301a7e5151227508ac8cec05c12d79ed9240062a86a0e2d14f",
    "x86_64": "49876d36b01735eb8a1b6e8a02c435761e3964807930fdef3ba30c9f66f809f6",
}
NINJA_CIPD_INSTANCE_BY_HOST = {
    "arm64": "xem0_6s7Lt77xBhJ_IHxFsjQR7JYkGvswGG-nsrwSv0C",
    "x86_64": "mGbmncDR78ysAZaXITtasuAzLcxiloLxNzPgeS6pURkC",
}
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

CHROME_BUILD_GN_SHA256 = (
    "3851bd31f3f9bc123395dbd966557885d62911f4e1359bca47390bfc942653e4"
)
INSTALLER_BUILD_GN_SHA256 = (
    "e620eb87d619dc384c050e041bc9d524037f7ff3f5255f39b5e034025351bd4d"
)
SIGN_CHROME_SHA256 = (
    "44846dccd82fbfcaeca36ff180d49ab943d8d2190a58f33e6863d6692aa17696"
)
MAC_SIGNING_SOURCES_GNI_SHA256 = (
    "6ad40408f0461b4804d83872f3000030b673edd4337a984d5a2c592f36c201c5"
)
ENSURE_BOOTSTRAP_SHA256 = (
    "a88ab230f6d92fea7588747a21854981442ba5866026fe48f792a2e43c5a986c"
)
MAX_RECEIPT_BYTES = 1024 * 1024
TOOL_RECEIPT_KEYS = frozenset(
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


class PipelineError(RuntimeError):
    """Raised when a staged build safety or provenance contract fails."""


def sha256_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PipelineError("expected a regular file: {}".format(path))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, value):
    """Create one receipt atomically without replacing prior evidence."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise PipelineError("refusing to replace receipt: {}".format(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise PipelineError("temporary receipt already exists: {}".format(temporary))
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    except Exception:
        if temporary.is_file() and not temporary.is_symlink():
            temporary.unlink()
        raise
    return {"path": str(path), "sha256": sha256_file(path)}


def load_json(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PipelineError("missing regular {}: {}".format(label, path))
    if path.stat().st_size <= 0 or path.stat().st_size > MAX_RECEIPT_BYTES:
        raise PipelineError("{} size is invalid: {}".format(label, path))

    def object_without_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise PipelineError("duplicate {} key: {}".format(label, key))
            value[key] = item
        return value

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_without_duplicates,
        )
    except (OSError, ValueError, TypeError, UnicodeDecodeError) as exc:
        raise PipelineError("invalid {}: {}".format(label, path)) from exc
    if not isinstance(value, dict):
        raise PipelineError("{} root must be an object".format(label))
    return value


def resolve_source(value):
    supplied = Path(value).expanduser().absolute()
    if supplied.is_symlink():
        raise PipelineError("source root must not be a symlink")
    try:
        source, _ = focus_macos.resolve_source_root(str(supplied))
    except focus_macos.ContractError as exc:
        raise PipelineError(str(exc)) from exc
    return source


def in_source(source, relative, label, must_exist=False, directory=False):
    """Resolve one fixed relative path without following an in-tree symlink."""
    if not isinstance(relative, str) or not relative or relative.startswith("/"):
        raise PipelineError("invalid {} relative path".format(label))
    parts = Path(relative).parts
    if ".." in parts or "." in parts:
        raise PipelineError("unsafe {} relative path: {}".format(label, relative))
    cursor = source
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PipelineError("{} traverses a symlink: {}".format(label, cursor))
    try:
        cursor.resolve(strict=False).relative_to(source)
    except ValueError as exc:
        raise PipelineError("{} escapes source root".format(label)) from exc
    if must_exist:
        good = cursor.is_dir() if directory else cursor.is_file()
        if not good:
            raise PipelineError("missing {}: {}".format(label, cursor))
    return cursor


def resolve_absent_dmg(value):
    """Resolve an absent absolute DMG under a real, non-symlink directory chain."""
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise PipelineError("DMG output must be an absolute path")
    if raw.suffix != ".dmg" or raw.name == ".dmg":
        raise PipelineError("DMG output must end in .dmg")
    cursor = raw.parent
    while True:
        if cursor.is_symlink():
            raise PipelineError("DMG output ancestor must not be a symlink: {}".format(cursor))
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    if not raw.parent.is_dir():
        raise PipelineError("DMG output parent must be a real existing directory")
    output = raw.parent.resolve(strict=True) / raw.name
    if os.path.lexists(str(output)):
        raise PipelineError("DMG output already exists: {}".format(output))
    return output


def acquisition_contract(source):
    """Bind every later stage to the exact completed acquisition marker."""
    marker_path = source.parent / acquire_chromium.COMPLETE_MARKER
    marker = load_json(marker_path, "acquisition marker")
    if marker.get("status") != "acquisition_complete":
        raise PipelineError("acquisition is not complete")
    if (
        marker.get("execution_requested") is not True
        or marker.get("network_performed") is not True
        or marker.get("destination") != str(source.parent)
    ):
        raise PipelineError("acquisition execution/destination contract mismatch")
    pins = marker.get("pins")
    expected_pins = {
        "chromium_version": acquire_chromium.CHROMIUM_VERSION,
        "chromium_tag": acquire_chromium.CHROMIUM_TAG,
        "chromium_commit": acquire_chromium.CHROMIUM_COMMIT,
        "depot_tools_commit": acquire_chromium.DEPOT_TOOLS_COMMIT,
    }
    if pins != expected_pins:
        raise PipelineError("acquisition pins do not match the build contract")
    verification = marker.get("verification", {})
    if not isinstance(verification, dict):
        raise PipelineError("acquisition verification must be an object")
    if Path(verification.get("source_root", "")).resolve() != source:
        raise PipelineError("acquisition marker source_root mismatch")
    if verification.get("chromium_version") != acquire_chromium.CHROMIUM_VERSION:
        raise PipelineError("acquisition marker Chromium version mismatch")
    if verification.get("chromium_commit") != acquire_chromium.CHROMIUM_COMMIT:
        raise PipelineError("acquisition marker Chromium commit mismatch")
    if verification.get("depot_tools_commit") != acquire_chromium.DEPOT_TOOLS_COMMIT:
        raise PipelineError("acquisition marker depot_tools commit mismatch")
    gclient = marker.get("gclient")
    if not isinstance(gclient, dict) or (
        gclient.get("target_os") != ["mac"]
        or gclient.get("target_os_only") is not True
        or gclient.get("hooks_during_acquisition") is not False
        or gclient.get("git_cache") is not False
        or gclient.get("spec_sha256") != acquire_chromium.GCLIENT_SPEC_SHA256
    ):
        raise PipelineError("low-space macOS gclient acquisition contract mismatch")
    return marker_path, marker


def tool_paths(source):
    checkout = source.parent
    depot = checkout / "depot_tools"
    paths = {
        "gclient": depot / "gclient",
        "gn": depot / "gn",
        "autoninja": depot / "autoninja",
    }
    for label, path in paths.items():
        if path.is_symlink() or not path.is_file() or not os.access(str(path), os.X_OK):
            raise PipelineError("missing executable {}: {}".format(label, path))
    return paths


def ensure_bootstrap_path(source):
    path = source.parent / "depot_tools" / "ensure_bootstrap"
    if path.is_symlink() or not path.is_file() or not os.access(str(path), os.X_OK):
        raise PipelineError("missing executable pinned depot_tools ensure_bootstrap")
    if sha256_file(path) != ENSURE_BOOTSTRAP_SHA256:
        raise PipelineError("depot_tools ensure_bootstrap hash mismatch")
    return path


def safe_environment(source, developer_dir, inherited=None, build_ninja=None):
    inherited = os.environ if inherited is None else inherited
    result = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
    inherited_home = inherited.get("HOME")
    if inherited_home:
        home_path = Path(inherited_home)
        if (
            not home_path.is_absolute()
            or any(ord(character) < 0x20 for character in inherited_home)
            or home_path.is_symlink()
            or not home_path.is_dir()
        ):
            raise PipelineError("inherited HOME is not a safe real absolute directory")
        result["HOME"] = inherited_home
    depot = source.parent / "depot_tools"
    path_entries = [str(depot)]
    if build_ninja is not None:
        build_ninja = Path(build_ninja)
        expected_ninja = in_source(source, DAWN_NINJA_RELATIVE, "pinned Dawn Ninja")
        if build_ninja != expected_ninja:
            raise PipelineError("build Ninja path does not match pinned Dawn Ninja")
        path_entries.append(str(build_ninja.parent))
    path_entries.append(SYSTEM_PATH)
    result.update(
        {
            "DEVELOPER_DIR": str(developer_dir),
            "PATH": os.pathsep.join(path_entries),
            "DEPOT_TOOLS_UPDATE": "0",
            "DEPOT_TOOLS_METRICS": "0",
            "GCLIENT_FILE": str(source.parent / ".gclient"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "NINJA_SUMMARIZE_BUILD": "1",
        }
    )
    return result


def free_bytes(path):
    return shutil.disk_usage(str(path)).free


def require_free(path, minimum_gib, label):
    observed = free_bytes(path)
    required = int(minimum_gib * GIB)
    if observed < required:
        raise PipelineError(
            "{} disk gate failed: {:.2f} GiB free, {:.2f} GiB required".format(
                label, observed / GIB, minimum_gib
            )
        )
    return observed


def _stop_process(process, force=False):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGINT)
    except ProcessLookupError:
        return


def run_monitored(
    command, cwd, environ, poll_seconds=POLL_SECONDS, watched_paths=None
):
    """Run one argv command and stop before filesystem pressure becomes unsafe."""
    if not isinstance(command, list) or not command or not all(
        isinstance(item, str) and item for item in command
    ):
        raise PipelineError("command must be a non-empty argv list")
    forbidden = {"sudo", "xcode-select", "notarytool", "altool"}
    if Path(command[0]).name in forbidden:
        raise PipelineError("forbidden build program: {}".format(command[0]))
    watched = tuple(Path(path) for path in (watched_paths or (cwd,)))
    if not watched or any(not path.exists() for path in watched):
        raise PipelineError("every watched disk path must exist")
    for path in watched:
        require_free(path, SOFT_FLOOR_GIB, "pre-command {}".format(path))
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=environ,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    stopped = None
    while process.poll() is None:
        available = min(free_bytes(path) for path in watched)
        if available < HARD_FLOOR_GIB * GIB:
            stopped = "hard"
            _stop_process(process, force=True)
            break
        if available < SOFT_FLOOR_GIB * GIB:
            stopped = "soft"
            _stop_process(process, force=False)
            break
        time.sleep(poll_seconds)
    try:
        returncode = process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        _stop_process(process, force=True)
        returncode = process.wait(timeout=10)
    if stopped:
        raise PipelineError(
            "{} disk floor crossed; build process stopped (return {})".format(
                stopped, returncode
            )
        )
    if returncode:
        raise PipelineError(
            "command failed with exit {}: {}".format(returncode, " ".join(command))
        )
    for path in watched:
        require_free(path, SOFT_FLOOR_GIB, "post-command {}".format(path))


def capture(command, cwd, environ, stderr_is_output=False):
    result = subprocess.run(
        command,
        cwd=str(cwd),
        env=environ,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PipelineError("command failed: {}\n{}".format(" ".join(command), detail))
    return (result.stderr if stderr_is_output else result.stdout).strip()


def ninja_contract(source):
    """Bind local builds to Dawn's already-synced, pinned host Ninja."""
    machine = platform.machine().lower()
    if machine == "aarch64":
        machine = "arm64"
    elif machine == "amd64":
        machine = "x86_64"
    if machine not in NINJA_SHA256_BY_HOST:
        raise PipelineError("unsupported Mac host architecture for Ninja: {}".format(machine))
    path = in_source(source, DAWN_NINJA_RELATIVE, "pinned Dawn Ninja", must_exist=True)
    if path.is_symlink() or not path.is_file() or not os.access(str(path), os.X_OK):
        raise PipelineError("pinned Dawn Ninja is not a regular executable")
    observed_hash = sha256_file(path)
    if observed_hash != NINJA_SHA256_BY_HOST[machine]:
        raise PipelineError("pinned Dawn Ninja hash mismatch")
    environment = {"PATH": SYSTEM_PATH, "LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
    architectures = capture(["/usr/bin/lipo", "-archs", str(path)], source, environment).split()
    if architectures != [machine]:
        raise PipelineError("pinned Dawn Ninja architecture mismatch")
    version = capture([str(path), "--version"], source, environment)
    if version != NINJA_VERSION:
        raise PipelineError("pinned Dawn Ninja version mismatch")
    cipd_platform = "mac-arm64" if machine == "arm64" else "mac-amd64"
    return {
        "path": str(path),
        "relative_path": DAWN_NINJA_RELATIVE,
        "architecture": machine,
        "sha256": observed_hash,
        "version": version,
        "cipd_package": "infra/3pp/tools/ninja/{}".format(cipd_platform),
        "cipd_version": NINJA_CIPD_VERSION,
        "cipd_instance": NINJA_CIPD_INSTANCE_BY_HOST[machine],
    }


def developer_contract(value):
    try:
        return focus_macos.validate_xcode_toolchain(value)
    except focus_macos.ContractError as exc:
        raise PipelineError(str(exc)) from exc


def preparation_contract(source, allow_reclaimed_arm=False):
    receipt_path = in_source(
        source, PREPARATION_RECEIPT, "preparation receipt", must_exist=True
    )
    receipt = load_json(receipt_path, "preparation receipt")
    if (
        receipt.get("schema") != 1
        or receipt.get("chromium_version") != focus_macos.PINNED_CHROMIUM_VERSION
    ):
        raise PipelineError("preparation Chromium version mismatch")
    if receipt.get("network_operations") != 0 or receipt.get("offline") is not True:
        raise PipelineError("preparation receipt is not offline")
    if (
        receipt.get("build_executed") is not False
        or receipt.get("signing_executed") is not False
        or receipt.get("packaging_executed") is not False
    ):
        raise PipelineError("preparation receipt reports a later stage")
    acquisition_path, _ = acquisition_contract(source)
    embedded_acquisition = receipt.get("acquisition")
    if not isinstance(embedded_acquisition, dict) or (
        embedded_acquisition.get("path") != str(acquisition_path)
        or embedded_acquisition.get("sha256") != sha256_file(acquisition_path)
        or embedded_acquisition.get("source_root") != str(source)
    ):
        raise PipelineError("preparation acquisition provenance mismatch")
    embedded_tools = receipt.get("tool_bootstrap")
    if not isinstance(embedded_tools, dict) or (
        embedded_tools.get("path") != str(source.parent / TOOL_RECEIPT)
        or embedded_tools.get("sha256") != sha256_file(source.parent / TOOL_RECEIPT)
        or embedded_tools.get("source_root") != str(source)
    ):
        raise PipelineError("preparation tool-bootstrap provenance mismatch")
    patch_contract = receipt.get("patch_contract", {})
    expected_patch = {
        "common_filtered_count": focus_macos.EXPECTED_FULL_PATCH_BODY_COUNT,
        "common_filtered_order_sha256": focus_macos.FILTERED_COMMON_SERIES_SHA256,
        "common_full_body_sha256": focus_macos.EXPECTED_FULL_PATCH_BODY_SHA256,
        "platform": focus_macos.validate_platform_patch_series(),
    }
    if patch_contract != expected_patch:
        raise PipelineError("preparation patch contract mismatch")
    dependency = receipt.get("dependency_contract")
    expected_archives = {
        name: contract["sha256"]
        for name, contract in prepare_source.DEPENDENCY_CONTRACTS.items()
    }
    if not isinstance(dependency, dict) or set(dependency) != {
        "manifest_sha256",
        "archives",
        "cache_marker",
        "install_inventory",
        "post_prepare_tree",
    } or dependency.get("manifest_sha256") != prepare_source.DEPS_INI_SHA256 or dependency.get(
        "archives"
    ) != expected_archives:
        raise PipelineError("preparation dependency contract mismatch")
    cache_marker = dependency.get("cache_marker")
    if not isinstance(cache_marker, dict) or set(cache_marker) != {
        "path",
        "sha256",
        "archive_count",
        "total_bytes",
        "archives",
    }:
        raise PipelineError("preparation dependency cache-marker schema mismatch")
    marker_path = Path(cache_marker.get("path", ""))
    if not marker_path.is_absolute() or marker_path.name != prepare_source.DEPENDENCY_CACHE_MARKER:
        raise PipelineError("preparation dependency cache-marker path mismatch")
    try:
        current_cache_marker = prepare_source.validate_dependency_cache_marker(
            marker_path.parent, prepare_source.DEPENDENCY_CONTRACTS
        )
    except prepare_source.PreparationError as exc:
        raise PipelineError(str(exc)) from exc
    if cache_marker != current_cache_marker:
        raise PipelineError("preparation dependency cache-marker provenance mismatch")
    expected_install = {
        "ownership_roots": list(prepare_source.DEPENDENCY_OWNERSHIP_ROOTS),
        "regular_files": prepare_source.DEPENDENCY_INSTALL_REGULAR_FILES,
        "logical_bytes": prepare_source.DEPENDENCY_INSTALL_LOGICAL_BYTES,
        "sha256": prepare_source.DEPENDENCY_INSTALL_SHA256,
        "installed_symlinks": 0,
        "installed_special_files": 0,
        "components": list(prepare_source.DEPENDENCY_CONTRACTS),
        "omitted_symlinks": {
            "onboarding": {
                "count": prepare_source.SHARED_DEPENDENCY_CONTRACTS["onboarding"][
                    "omitted_symlink_count"
                ],
                "sha256": prepare_source.SHARED_DEPENDENCY_CONTRACTS["onboarding"][
                    "omitted_symlink_sha256"
                ],
            }
        },
    }
    if dependency.get("install_inventory") != expected_install:
        raise PipelineError("preparation dependency install inventory mismatch")
    post_prepare_tree = dependency.get("post_prepare_tree")
    expected_post_keys = {
        "ownership_roots",
        "regular_files",
        "logical_bytes",
        "sha256",
        "installed_symlinks",
        "installed_special_files",
    }
    if (
        not isinstance(post_prepare_tree, dict)
        or set(post_prepare_tree) != expected_post_keys
        or post_prepare_tree.get("ownership_roots")
        != list(prepare_source.DEPENDENCY_OWNERSHIP_ROOTS)
        or type(post_prepare_tree.get("regular_files")) is not int
        or post_prepare_tree.get("regular_files", 0) <= 0
        or type(post_prepare_tree.get("logical_bytes")) is not int
        or post_prepare_tree.get("logical_bytes", 0) <= 0
        or not isinstance(post_prepare_tree.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", post_prepare_tree.get("sha256", ""))
        or post_prepare_tree.get("installed_symlinks") != 0
        or post_prepare_tree.get("installed_special_files") != 0
    ):
        raise PipelineError("preparation post-transform dependency tree schema mismatch")
    try:
        installed = prepare_source.installed_dependency_tree(
            source, prepare_source.DEPENDENCY_CONTRACTS
        )
    except prepare_source.PreparationError as exc:
        raise PipelineError(str(exc)) from exc
    if installed != post_prepare_tree:
        raise PipelineError("installed dependency tree changed after preparation")
    localized = receipt.get("localized_strings_contract")
    if not isinstance(localized, dict) or set(localized) != {
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
        raise PipelineError("localized strings preparation contract schema mismatch")
    try:
        current_node = prepare_source.onboarding_node_contract(source)
    except prepare_source.PreparationError as exc:
        raise PipelineError(str(exc)) from exc
    generator = in_source(
        source,
        prepare_source.ONBOARDING_GENERATOR,
        "onboarding strings generator",
        must_exist=True,
    )
    output = in_source(
        source,
        prepare_source.ONBOARDING_STRINGS_OUTPUT,
        "generated onboarding strings",
        must_exist=True,
    )
    if (
        localized.get("generator") != prepare_source.ONBOARDING_GENERATOR
        or localized.get("generator_sha256")
        != prepare_source.ONBOARDING_GENERATOR_SHA256
        or sha256_file(generator) != prepare_source.ONBOARDING_GENERATOR_SHA256
        or localized.get("node") != current_node
        or localized.get("output") != prepare_source.ONBOARDING_STRINGS_OUTPUT
        or localized.get("baseline_bytes")
        != prepare_source.ONBOARDING_STRINGS_BASELINE_BYTES
        or localized.get("baseline_sha256")
        != prepare_source.ONBOARDING_STRINGS_BASELINE_SHA256
        or localized.get("output_bytes")
        != prepare_source.ONBOARDING_STRINGS_BASELINE_BYTES
        or localized.get("output_sha256")
        != prepare_source.ONBOARDING_STRINGS_BASELINE_SHA256
        or localized.get("output_bytes") != output.stat().st_size
        or localized.get("output_sha256") != sha256_file(output)
        or localized.get("runs") != 2
        or localized.get("byte_identical") is not True
        or localized.get("network_operations") != 0
    ):
        raise PipelineError("localized strings preparation contract mismatch")
    if receipt.get("pruning_contract") != {
        "manifest_sha256": prepare_source.PRUNING_LIST_SHA256,
        "listed_files": prepare_source.PRUNING_ENTRY_COUNT,
        "files_removed": prepare_source.PRUNING_EXPECTED_REMOVAL_COUNT,
        "already_absent_files": prepare_source.PRUNING_ALREADY_ABSENT_COUNT,
        "already_absent_sha256": prepare_source.PRUNING_ALREADY_ABSENT_SHA256,
        "contingent_paths_pruned": False,
        "directory_pruning_executed": False,
    }:
        raise PipelineError("preparation pruning contract mismatch")
    if receipt.get("overlay_contract") != {
        "count": focus_macos.EXPECTED_FULL_OVERLAY_BODY_COUNT,
        "sha256": focus_macos.EXPECTED_FULL_OVERLAY_BODY_SHA256,
    }:
        raise PipelineError("preparation overlay contract mismatch")
    if receipt.get("resource_contract") != {
        "count": prepare_source.RESOURCE_BODY_COUNT,
        "sha256": prepare_source.RESOURCE_BODY_SHA256,
    }:
        raise PipelineError("preparation resource contract mismatch")
    if receipt.get("icns_sha256") != focus_macos.FOCUS_ICNS_SHA256:
        raise PipelineError("preparation ICNS contract mismatch")
    post = receipt.get("post_prepare_sha256")
    expected_labels = {
        "chrome/BUILD.gn": "chrome/BUILD.gn",
        prepare_source.INSTALLER_MAC_BUILD_GN: prepare_source.INSTALLER_MAC_BUILD_GN,
        "chrome/app/theme/chromium/BRANDING": "chrome/app/theme/chromium/BRANDING",
        "chrome/VERSION": "chrome/VERSION",
        prepare_source.MAC_ICON_DESTINATION: prepare_source.MAC_ICON_DESTINATION,
        "onboarding/strings.ts": prepare_source.ONBOARDING_STRINGS_OUTPUT,
        "args_gn/arm64": ARM_OUT + "/args.gn",
        "args_gn/x64": X64_OUT + "/args.gn",
    }
    if not isinstance(post, dict) or set(post) != set(expected_labels):
        raise PipelineError("preparation post-hash inventory mismatch")
    for label, relative in expected_labels.items():
        current = in_source(source, relative, "prepared {}".format(label))
        if current.is_file() and not current.is_symlink():
            observed = sha256_file(current)
        elif label == "args_gn/arm64" and allow_reclaimed_arm:
            reclaim = load_json(
                in_source(source, RECLAIM_RECEIPT, "arm64 reclaim receipt", must_exist=True),
                "arm64 reclaim receipt",
            )
            observed = reclaim.get("arm_args_gn_sha256")
        else:
            raise PipelineError("prepared receipt input is missing: {}".format(current))
        if observed != post[label]:
            raise PipelineError("prepared input hash changed: {}".format(label))
    return receipt_path, receipt


def tool_receipt_contract(source, developer_dir=None):
    path = source.parent / TOOL_RECEIPT
    receipt = load_json(path, "tool bootstrap receipt")
    if set(receipt) != TOOL_RECEIPT_KEYS:
        raise PipelineError("tool bootstrap receipt schema mismatch")
    if receipt.get("schema") != 1 or receipt.get("hooks_complete") is not True:
        raise PipelineError("tool bootstrap receipt is incomplete")
    if receipt.get("chromium_commit") != acquire_chromium.CHROMIUM_COMMIT:
        raise PipelineError("tool bootstrap Chromium commit mismatch")
    if receipt.get("depot_tools_commit") != acquire_chromium.DEPOT_TOOLS_COMMIT:
        raise PipelineError("tool bootstrap depot_tools commit mismatch")
    if receipt.get("source_root") != str(source):
        raise PipelineError("tool bootstrap source_root mismatch")
    if developer_dir is not None and receipt.get("developer_dir") != str(developer_dir):
        raise PipelineError("tool bootstrap Xcode Developer directory mismatch")
    if receipt.get("build_executed") is not False:
        raise PipelineError("tool bootstrap receipt already reports a build")
    marker_hash = sha256_file(source.parent / acquire_chromium.COMPLETE_MARKER)
    if receipt.get("acquisition_marker_sha256") != marker_hash:
        raise PipelineError("tool bootstrap acquisition marker mismatch")
    expected_command = [str(source.parent / "depot_tools" / "gclient"), "runhooks"]
    if receipt.get("gclient_command") != expected_command:
        raise PipelineError("tool bootstrap command mismatch")
    tool_hashes = receipt.get("tool_sha256")
    tools = tool_paths(source)
    if not isinstance(tool_hashes, dict) or tool_hashes != {
        name: sha256_file(path) for name, path in tools.items()
    }:
        raise PipelineError("bootstrapped tool hashes changed")
    return path, receipt


def verify_pristine_bootstrap_source(source, developer_dir):
    """Prove exact clean Git revisions and pinned upstream Mac inputs."""
    environment = safe_environment(source, developer_dir)
    git = "/usr/bin/git"
    if not Path(git).is_file() or not os.access(git, os.X_OK):
        raise PipelineError("fixed system Git is unavailable")
    expected_heads = (
        (source, acquire_chromium.CHROMIUM_COMMIT, "Chromium"),
        (source.parent / "depot_tools", acquire_chromium.DEPOT_TOOLS_COMMIT, "depot_tools"),
    )
    for repository, expected, label in expected_heads:
        observed = capture([git, "-C", str(repository), "rev-parse", "HEAD"], source, environment)
        if observed != expected:
            raise PipelineError("{} HEAD changed before hooks".format(label))
        status = capture(
            [git, "-C", str(repository), "status", "--porcelain", "--untracked-files=no"],
            source,
            environment,
        )
        if status:
            raise PipelineError("{} tracked files changed before hooks".format(label))
    prepare_source.validate_upstream_source_contracts(source)
    signing_files = {
        "sign_chrome.py": (
            source / "chrome/installer/mac/sign_chrome.py",
            SIGN_CHROME_SHA256,
        ),
        "mac_signing_sources.gni": (
            source / "chrome/installer/mac/mac_signing_sources.gni",
            MAC_SIGNING_SOURCES_GNI_SHA256,
        ),
    }
    for label, (path, expected) in signing_files.items():
        if sha256_file(path) != expected:
            raise PipelineError("upstream {} hash mismatch".format(label))
    return {
        "chromium_commit": acquire_chromium.CHROMIUM_COMMIT,
        "depot_tools_commit": acquire_chromium.DEPOT_TOOLS_COMMIT,
        "tracked_worktrees_clean": True,
    }


def slice_receipt_contract(source, out, architecture):
    """Bind staging/merging to a completed slice build from this checkout."""
    receipt_path = Path(out) / SLICE_RECEIPT_NAME
    receipt = load_json(receipt_path, "{} build receipt".format(architecture))
    expected_arch = "arm64" if architecture == "arm64" else "x86_64"
    if (
        receipt.get("schema") != 1
        or receipt.get("architecture") != architecture
        or receipt.get("mach_o_architecture") != expected_arch
        or receipt.get("source_root") != str(source)
        or receipt.get("build_complete") is not True
    ):
        raise PipelineError("{} build receipt contract mismatch".format(architecture))
    if receipt.get("app", {}).get("architectures") != [expected_arch]:
        raise PipelineError("{} build receipt architecture mismatch".format(architecture))
    args_path = Path(out) / "args.gn"
    if receipt.get("args_gn_sha256") != sha256_file(args_path):
        raise PipelineError("{} args.gn changed after build".format(architecture))
    if receipt.get("preparation_receipt_sha256") != sha256_file(
        in_source(source, PREPARATION_RECEIPT, "preparation receipt", must_exist=True)
    ):
        raise PipelineError("{} preparation receipt mismatch".format(architecture))
    if receipt.get("ninja") != ninja_contract(source):
        raise PipelineError("{} Ninja provenance mismatch".format(architecture))
    return receipt_path, receipt


def reclaim_contract(source):
    """Require completed arm64 staging and exact output reclamation evidence."""
    receipt_path = in_source(
        source, RECLAIM_RECEIPT, "arm64 reclaim receipt", must_exist=True
    )
    receipt = load_json(receipt_path, "arm64 reclaim receipt")
    expected_keys = {
        "schema",
        "reclaim_complete",
        "source_root",
        "staged_app",
        "tree_sha256",
        "reclaimed_out",
        "reclaimed_out_bytes",
        "arm_args_gn_sha256",
        "stage_receipt_sha256",
    }
    if set(receipt) != expected_keys or (
        receipt.get("schema") != 1
        or receipt.get("reclaim_complete") is not True
        or receipt.get("source_root") != str(source)
        or receipt.get("staged_app") != str(in_source(source, STAGED_ARM_APP, "staged arm64 app"))
        or receipt.get("reclaimed_out") != str(in_source(source, ARM_OUT, "arm64 output"))
    ):
        raise PipelineError("arm64 reclaim receipt contract mismatch")
    arm_out = in_source(source, ARM_OUT, "arm64 output")
    if os.path.lexists(str(arm_out)):
        raise PipelineError("arm64 output still exists after claimed reclamation")
    staged = in_source(
        source, STAGED_ARM_APP, "staged arm64 app", must_exist=True, directory=True
    )
    app_report(staged, ("arm64",))
    if tree_digest(staged) != receipt.get("tree_sha256"):
        raise PipelineError("staged arm64 app changed after reclamation")
    stage_path = in_source(source, STAGE_RECEIPT, "arm64 stage receipt", must_exist=True)
    if receipt.get("stage_receipt_sha256") != sha256_file(stage_path):
        raise PipelineError("arm64 stage receipt changed after reclamation")
    if not isinstance(receipt.get("reclaimed_out_bytes"), int) or receipt[
        "reclaimed_out_bytes"
    ] <= 0:
        raise PipelineError("invalid reclaimed arm64 output size")
    return receipt_path, receipt


def app_report(app, expected_architectures):
    """Validate bundle identity and the exact architecture set."""
    app = Path(app)
    if app.is_symlink() or not app.is_dir() or app.name != APP_NAME:
        raise PipelineError("missing real {}: {}".format(APP_NAME, app))
    info = app / "Contents" / "Info.plist"
    if info.is_symlink() or not info.is_file():
        raise PipelineError("missing app Info.plist")
    with info.open("rb") as stream:
        values = plistlib.load(stream)
    if values.get("CFBundleIdentifier") != focus_macos.BUNDLE_ID:
        raise PipelineError("unexpected app bundle identifier")
    executable_name = values.get("CFBundleExecutable")
    if not isinstance(executable_name, str) or Path(executable_name).name != executable_name:
        raise PipelineError("unsafe CFBundleExecutable")
    executable = app / "Contents" / "MacOS" / executable_name
    if executable.is_symlink() or not executable.is_file():
        raise PipelineError("missing app executable")
    result = subprocess.run(
        ["/usr/bin/lipo", "-archs", str(executable)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        raise PipelineError("lipo failed for {}".format(executable))
    observed = frozenset(result.stdout.split())
    expected = frozenset(expected_architectures)
    if observed != expected:
        raise PipelineError(
            "app architectures mismatch: expected {}, got {}".format(
                sorted(expected), sorted(observed)
            )
        )
    return {
        "app": str(app),
        "bundle_id": focus_macos.BUNDLE_ID,
        "executable": str(executable),
        "architectures": sorted(observed),
    }


def physical_size(path):
    """Return allocated bytes without following symbolic links."""
    root = Path(path)
    if root.is_symlink() or not root.exists():
        raise PipelineError("cannot size missing or symlinked path: {}".format(root))
    total = root.lstat().st_blocks * 512
    if root.is_dir():
        for node in root.rglob("*"):
            total += node.lstat().st_blocks * 512
    return total


def tree_digest(root):
    """Hash names, modes, link targets, and regular-file bodies deterministically."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise PipelineError("tree digest root must be a real directory")
    digest = hashlib.sha256()
    for node in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = node.relative_to(root).as_posix()
        mode = node.lstat().st_mode
        if node.is_symlink():
            kind = "L"
            body = os.readlink(str(node)).encode("utf-8")
        elif node.is_file():
            kind = "F"
            body_digest = hashlib.sha256()
            with node.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    body_digest.update(chunk)
            body = body_digest.digest()
        elif node.is_dir():
            kind = "D"
            body = b""
        else:
            raise PipelineError("special file in app tree: {}".format(node))
        digest.update("{}\0{}\0{:o}\0".format(kind, relative, mode & 0o7777).encode())
        digest.update(body)
        digest.update(b"\n")
    return digest.hexdigest()


def bootstrap_plan(source, developer_dir):
    acquisition_path, _ = acquisition_contract(source)
    if (source.parent / TOOL_RECEIPT).exists():
        raise PipelineError("tool bootstrap receipt already exists")
    if in_source(source, PREPARATION_RECEIPT, "preparation receipt").exists():
        raise PipelineError("tools must be bootstrapped before source preparation")
    tools = tool_paths(source)
    ensure_bootstrap = ensure_bootstrap_path(source)
    require_free(source, BOOTSTRAP_POST_GIB, "tool bootstrap start")
    command = [str(tools["gclient"]), "runhooks"]
    return {
        "stage": "bootstrap-tools",
        "bootstrap_command": [str(ensure_bootstrap)],
        "command": command,
        "cwd": str(source.parent),
        "acquisition_marker": str(acquisition_path),
        "developer_dir": str(developer_dir),
    }


def execute_bootstrap(source, developer_dir, plan):
    environment = safe_environment(source, developer_dir)
    verify_pristine_bootstrap_source(source, developer_dir)
    run_monitored(
        plan["bootstrap_command"],
        source.parent,
        environment,
        watched_paths=(source,),
    )
    run_monitored(plan["command"], source.parent, environment, watched_paths=(source,))
    verify_pristine_bootstrap_source(source, developer_dir)
    tools = tool_paths(source)
    gn_version = capture([str(tools["gn"]), "--version"], source, environment)
    free = require_free(source, BOOTSTRAP_POST_GIB, "post-hooks")
    receipt = {
        "schema": 1,
        "hooks_complete": True,
        "chromium_commit": acquire_chromium.CHROMIUM_COMMIT,
        "depot_tools_commit": acquire_chromium.DEPOT_TOOLS_COMMIT,
        "source_root": str(source),
        "developer_dir": str(developer_dir),
        "acquisition_marker_sha256": sha256_file(source.parent / acquire_chromium.COMPLETE_MARKER),
        "gclient_command": plan["command"],
        "gn_version": gn_version,
        "tool_sha256": {name: sha256_file(path) for name, path in tools.items()},
        "post_hooks_free_bytes": free,
        "build_executed": False,
    }
    return atomic_json(source.parent / TOOL_RECEIPT, receipt)


def build_plan(source, developer_dir, architecture):
    acquisition_contract(source)
    tool_receipt_contract(source, developer_dir)
    if architecture == "x64":
        reclaim_contract(source)
    preparation_contract(source, allow_reclaimed_arm=(architecture == "x64"))
    tools = tool_paths(source)
    ninja = ninja_contract(source)
    if architecture == "arm64":
        out_relative = ARM_OUT
    elif architecture == "x64":
        out_relative = X64_OUT
    else:
        raise PipelineError("unsupported build architecture")
    out = in_source(source, out_relative, "build output", must_exist=True, directory=True)
    args_gn = out / "args.gn"
    if args_gn.is_symlink() or not args_gn.is_file():
        raise PipelineError("missing prepared args.gn: {}".format(args_gn))
    app = out / APP_NAME
    if app.exists() or app.is_symlink():
        raise PipelineError("refusing to overwrite an existing app: {}".format(app))
    build_receipt = out / SLICE_RECEIPT_NAME
    if build_receipt.exists() or build_receipt.is_symlink():
        raise PipelineError("refusing to overwrite build receipt: {}".format(build_receipt))
    commands = [
        [str(tools["gn"]), "gen", out_relative, "--fail-on-unused-args"],
        [
            str(tools["autoninja"]),
            "-j{}".format(BUILD_JOBS),
            "-C",
            out_relative,
            "chrome",
            "chrome/installer/mac:copies",
        ],
    ]
    return {
        "stage": "build-{}".format(architecture),
        "architecture": architecture,
        "out": str(out),
        "commands": commands,
        "receipt": str(build_receipt),
        "developer_dir": str(developer_dir),
        "ninja": ninja,
    }


def execute_build(source, developer_dir, plan):
    if plan["architecture"] == "arm64":
        require_free(source, BOOTSTRAP_POST_GIB, "arm64 build start")
    else:
        _, reclaim_receipt = reclaim_contract(source)
        first_out = reclaim_receipt["reclaimed_out_bytes"]
        projected = max(int(first_out * 1.2), first_out + 5 * GIB)
        required = SOFT_FLOOR_GIB + projected / GIB
        require_free(source, required, "x86_64 projected build start")
    current_ninja = ninja_contract(source)
    if plan.get("ninja") != current_ninja:
        raise PipelineError("build plan Ninja provenance changed before execution")
    environment = safe_environment(
        source, developer_dir, build_ninja=Path(current_ninja["path"])
    )
    for command in plan["commands"]:
        run_monitored(command, source, environment)
    expected = ("arm64",) if plan["architecture"] == "arm64" else ("x86_64",)
    report = app_report(Path(plan["out"]) / APP_NAME, expected)
    packaging = Path(plan["out"]) / PACKAGING_NAME
    if packaging.is_symlink() or not packaging.is_dir():
        raise PipelineError("missing generated Chromium signing package")
    sign_script = packaging / "sign_chrome.py"
    if sha256_file(sign_script) != SIGN_CHROME_SHA256:
        raise PipelineError("generated sign_chrome.py hash mismatch")
    report["out_allocated_bytes"] = physical_size(plan["out"])
    report["packaging"] = str(packaging)
    receipt = {
        "schema": 1,
        "architecture": plan["architecture"],
        "mach_o_architecture": expected[0],
        "source_root": str(source),
        "app": report,
        "args_gn_sha256": sha256_file(Path(plan["out"]) / "args.gn"),
        "preparation_receipt_sha256": sha256_file(
            in_source(source, PREPARATION_RECEIPT, "preparation receipt", must_exist=True)
        ),
        "tool_receipt_sha256": sha256_file(source.parent / TOOL_RECEIPT),
        "ninja": current_ninja,
        "sign_chrome_sha256": SIGN_CHROME_SHA256,
        "build_complete": True,
    }
    return atomic_json(plan["receipt"], receipt)


def stage_arm_plan(source):
    acquisition_contract(source)
    tool_receipt_contract(source)
    preparation_contract(source)
    arm_out = in_source(source, ARM_OUT, "arm64 output", must_exist=True, directory=True)
    build_receipt_path, _ = slice_receipt_contract(source, arm_out, "arm64")
    app_report(arm_out / APP_NAME, ("arm64",))
    staged = in_source(source, STAGED_ARM_APP, "staged arm64 app")
    receipt = in_source(source, STAGE_RECEIPT, "stage receipt")
    reclaim = in_source(source, RECLAIM_RECEIPT, "reclaim receipt")
    partial_root = in_source(source, STAGING_ROOT + "/.arm64.part", "staging partial")
    if (
        staged.exists()
        or staged.is_symlink()
        or receipt.exists()
        or receipt.is_symlink()
        or reclaim.exists()
        or reclaim.is_symlink()
        or partial_root.exists()
        or partial_root.is_symlink()
    ):
        raise PipelineError("arm64 staging destination already exists")
    return {
        "stage": "stage-arm64",
        "source_app": str(arm_out / APP_NAME),
        "staged_app": str(staged),
        "arm_out": str(arm_out),
        "receipt": str(receipt),
        "reclaim_receipt": str(reclaim),
        "build_receipt": str(build_receipt_path),
        "partial_root": str(partial_root),
        "partial_app": str(partial_root / APP_NAME),
        "ditto_command": [
            "/usr/bin/ditto",
            str(arm_out / APP_NAME),
            str(partial_root / APP_NAME),
        ],
    }


def execute_stage_arm(source, plan, allow_reclaim):
    if not allow_reclaim:
        raise PipelineError("stage-arm64 execution requires --allow-reclaim-arm64-out")
    require_free(source, SOFT_FLOOR_GIB, "arm64 staging")
    source_app = Path(plan["source_app"])
    staged_app = Path(plan["staged_app"])
    partial_root = Path(plan["partial_root"])
    partial_app = Path(plan["partial_app"])
    partial_root.mkdir(parents=True, exist_ok=False)
    try:
        tool_receipt = load_json(source.parent / TOOL_RECEIPT, "tool bootstrap receipt")
        environment = safe_environment(source, Path(tool_receipt["developer_dir"]))
        run_monitored(
            plan["ditto_command"],
            source,
            environment,
            watched_paths=(source,),
        )
        app_report(partial_app, ("arm64",))
        source_digest = tree_digest(source_app)
        staged_digest = tree_digest(partial_app)
        if source_digest != staged_digest:
            raise PipelineError("staged arm64 app differs from build output")
        staged_app.parent.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(partial_root), str(staged_app.parent))
    except Exception:
        if partial_root.is_dir() and not partial_root.is_symlink():
            shutil.rmtree(str(partial_root))
        raise
    app_report(staged_app, ("arm64",))
    staged_digest = tree_digest(staged_app)
    if tree_digest(source_app) != staged_digest:
        raise PipelineError("staged arm64 app differs from build output")
    arm_out = Path(plan["arm_out"])
    expected_out = in_source(source, ARM_OUT, "arm64 output")
    if arm_out != expected_out or arm_out.is_symlink() or not arm_out.is_dir():
        raise PipelineError("refusing unsafe arm64 output reclamation")
    out_bytes = physical_size(arm_out)
    build_receipt = load_json(plan["build_receipt"], "arm64 build receipt")
    arm_args_hash = build_receipt.get("args_gn_sha256")
    if arm_args_hash != sha256_file(arm_out / "args.gn"):
        raise PipelineError("arm64 args.gn changed before reclamation")
    stage_value = {
        "schema": 1,
        "architecture": "arm64",
        "source_root": str(source),
        "staged_app": str(staged_app),
        "tree_sha256": staged_digest,
        "app_allocated_bytes": physical_size(staged_app),
        "reclaim_requested_out": str(arm_out),
        "reclaim_requested_bytes": out_bytes,
        "arm_args_gn_sha256": arm_args_hash,
        "build_receipt_sha256": sha256_file(plan["build_receipt"]),
    }
    stage_report = atomic_json(Path(plan["receipt"]), stage_value)
    # Re-validate all evidence immediately before the only recursive deletion.
    load_json(stage_report["path"], "arm64 stage receipt")
    if tree_digest(source_app) != staged_digest:
        raise PipelineError("arm64 build output changed before reclamation")
    app_report(staged_app, ("arm64",))
    if tree_digest(staged_app) != staged_digest:
        raise PipelineError("staged arm64 app changed before reclamation")
    shutil.rmtree(str(arm_out))
    if os.path.lexists(str(arm_out)):
        raise PipelineError("arm64 output reclamation did not complete")
    require_free(source, SOFT_FLOOR_GIB, "post-arm64 reclamation")
    reclaim_value = {
        "schema": 1,
        "reclaim_complete": True,
        "source_root": str(source),
        "staged_app": str(staged_app),
        "tree_sha256": staged_digest,
        "reclaimed_out": str(arm_out),
        "reclaimed_out_bytes": out_bytes,
        "arm_args_gn_sha256": arm_args_hash,
        "stage_receipt_sha256": sha256_file(stage_report["path"]),
    }
    return atomic_json(Path(plan["reclaim_receipt"]), reclaim_value)


def merge_plan(source, developer_dir, dmg_output):
    acquisition_contract(source)
    tool_receipt_contract(source, developer_dir)
    _, reclaim_receipt = reclaim_contract(source)
    preparation_contract(source, allow_reclaimed_arm=True)
    arm_app = in_source(
        source, STAGED_ARM_APP, "staged arm64 app", must_exist=True, directory=True
    )
    if tree_digest(arm_app) != reclaim_receipt.get("tree_sha256"):
        raise PipelineError("staged arm64 app no longer matches its receipt")
    app_report(arm_app, ("arm64",))
    x64_out = in_source(source, X64_OUT, "x86_64 output", must_exist=True, directory=True)
    slice_receipt_contract(source, x64_out, "x64")
    x64_app = x64_out / APP_NAME
    app_report(x64_app, ("x86_64",))
    packaging = x64_out / PACKAGING_NAME
    if packaging.is_symlink() or not packaging.is_dir():
        raise PipelineError("missing x86_64 signing package")
    source_signing_contracts = {
        "chrome/installer/mac/BUILD.gn": INSTALLER_BUILD_GN_SHA256,
        "chrome/installer/mac/sign_chrome.py": SIGN_CHROME_SHA256,
        "chrome/installer/mac/mac_signing_sources.gni": MAC_SIGNING_SOURCES_GNI_SHA256,
    }
    for relative, expected_hash in source_signing_contracts.items():
        path = in_source(source, relative, "Chromium signing source", must_exist=True)
        if sha256_file(path) != expected_hash:
            raise PipelineError("Chromium signing source hash mismatch: {}".format(relative))
    if sha256_file(packaging / "sign_chrome.py") != SIGN_CHROME_SHA256:
        raise PipelineError("x86_64 sign_chrome.py hash mismatch")
    universalizer = in_source(
        source,
        focus_macos.CHROMIUM_UNIVERSALIZER,
        "Chromium universalizer",
        must_exist=True,
    )
    if sha256_file(universalizer) != focus_macos.PINNED_CHROMIUM_UNIVERSALIZER_SHA256:
        raise PipelineError("Chromium universalizer hash mismatch")
    unsigned_root = in_source(source, UNSIGNED_ROOT, "unsigned universal root")
    signed_root = in_source(source, SIGNED_ROOT, "signed universal root")
    if unsigned_root.exists() or unsigned_root.is_symlink():
        raise PipelineError("unsigned universal output already exists")
    if signed_root.exists() or signed_root.is_symlink():
        raise PipelineError("signed universal output already exists")
    output = resolve_absent_dmg(dmg_output)
    unsigned_app = unsigned_root / APP_NAME
    commands = {
        "copy_packaging": ["/usr/bin/ditto", str(packaging), str(unsigned_root / PACKAGING_NAME)],
        "universalize": [
            "/usr/bin/python3",
            str(universalizer),
            str(x64_app),
            str(arm_app),
            str(unsigned_app),
        ],
        "sign": [
            "/usr/bin/python3",
            str(unsigned_root / PACKAGING_NAME / "sign_chrome.py"),
            "--identity",
            "-",
            "--development",
            "--no-embed-development-provisioning-profile",
            "--notarize",
            "none",
            "--disable-packaging",
            "--input",
            str(unsigned_root),
            "--output",
            str(signed_root),
        ],
        "package": [
            "/usr/bin/python3",
            str(MACOS_DIR / "package_local_dmg.py"),
            "--app",
            str(signed_root / APP_NAME),
            "--output",
            str(output),
            "--require-universal",
            "--json",
        ],
    }
    return {
        "stage": "merge-sign-package",
        "arm_app": str(arm_app),
        "x64_app": str(x64_app),
        "unsigned_root": str(unsigned_root),
        "signed_root": str(signed_root),
        "dmg_output": str(output),
        "commands": commands,
        "developer_dir": str(developer_dir),
    }


def execute_merge(source, developer_dir, plan):
    arm_size = physical_size(plan["arm_app"])
    x64_size = physical_size(plan["x64_app"])
    # Universalization creates one combined app and signing creates another.
    merge_required = SOFT_FLOOR_GIB + 2 + 2.2 * (arm_size + x64_size) / GIB
    require_free(source, merge_required, "universal merge")
    unsigned_root = Path(plan["unsigned_root"])
    unsigned_root.mkdir(parents=True)
    environment = safe_environment(source, developer_dir)
    for name in ("copy_packaging", "universalize"):
        run_monitored(plan["commands"][name], source, environment)
    unsigned_app = unsigned_root / APP_NAME
    app_report(unsigned_app, ("arm64", "x86_64"))
    copied_sign = unsigned_root / PACKAGING_NAME / "sign_chrome.py"
    if sha256_file(copied_sign) != SIGN_CHROME_SHA256:
        raise PipelineError("copied Chromium signing script hash mismatch")
    run_monitored(plan["commands"]["sign"], source, environment)
    signed_app = Path(plan["signed_root"]) / APP_NAME
    app_report(signed_app, ("arm64", "x86_64"))
    capture(
        [
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            str(signed_app),
        ],
        source,
        environment,
    )
    signature_detail = capture(
        ["/usr/bin/codesign", "-dv", "--verbose=4", str(signed_app)],
        source,
        environment,
        stderr_is_output=True,
    )
    if "Signature=adhoc" not in signature_detail.splitlines():
        raise PipelineError("signed app is not ad-hoc signed")
    universal_size = physical_size(signed_app)
    output = Path(plan["dmg_output"])
    package_required = SOFT_FLOOR_GIB + 5 + (3 * universal_size) / GIB
    require_free(source, package_required, "DMG packaging source")
    require_free(output.parent, package_required, "DMG packaging output")
    run_monitored(
        plan["commands"]["package"],
        source,
        environment,
        watched_paths=(source, output.parent),
    )
    if output.is_symlink() or not output.is_file() or output.stat().st_size <= 0:
        raise PipelineError("DMG packager did not create the expected regular output")
    app_identity = package_local_dmg.validate_app(signed_app)
    if app_identity["architectures"] != ["arm64", "x86_64"]:
        raise PipelineError("packaged app is no longer universal")
    report = {
        "app": str(signed_app),
        "output": str(output),
        "bundle_id": app_identity["bundle_id"],
        "executable": app_identity["executable"],
        "architectures": app_identity["architectures"],
        "require_universal": True,
        "format": "UDZO",
        "size_bytes": output.stat().st_size,
        "sha256": package_local_dmg.sha256_file(output),
        "signature": "ad-hoc; nested signature and mounted DMG verified",
        "signing_performed": True,
        "notarization_performed": False,
        "local_only": True,
    }
    report["signed_app"] = str(signed_app)
    report["signed_app_tree_sha256"] = tree_digest(signed_app)
    report["notarized"] = False
    report["developer_id_signed"] = False
    return report


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)
    for name in (
        "bootstrap-tools",
        "build-arm64",
        "stage-arm64",
        "build-x64",
        "merge-sign-package",
    ):
        child = subparsers.add_parser(name)
        child.add_argument("--source-root", required=True)
        child.add_argument("--execute", action="store_true")
        child.add_argument("--json", action="store_true")
        if name not in ("stage-arm64",):
            child.add_argument("--developer-dir", required=True)
        if name == "stage-arm64":
            child.add_argument("--allow-reclaim-arm64-out", action="store_true")
        if name == "merge-sign-package":
            child.add_argument("--dmg-output", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        source = resolve_source(args.source_root)
        developer_dir = None
        if hasattr(args, "developer_dir"):
            developer_dir = Path(args.developer_dir).expanduser().resolve(strict=True)
            developer_contract(developer_dir)
        if args.command == "bootstrap-tools":
            plan = bootstrap_plan(source, developer_dir)
            result = execute_bootstrap(source, developer_dir, plan) if args.execute else plan
        elif args.command == "build-arm64":
            plan = build_plan(source, developer_dir, "arm64")
            result = execute_build(source, developer_dir, plan) if args.execute else plan
        elif args.command == "stage-arm64":
            plan = stage_arm_plan(source)
            result = (
                execute_stage_arm(source, plan, args.allow_reclaim_arm64_out)
                if args.execute
                else plan
            )
        elif args.command == "build-x64":
            plan = build_plan(source, developer_dir, "x64")
            result = execute_build(source, developer_dir, plan) if args.execute else plan
        else:
            plan = merge_plan(source, developer_dir, args.dmg_output)
            result = execute_merge(source, developer_dir, plan) if args.execute else plan
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print("{}: {}".format(args.command, "executed" if args.execute else "dry-run"))
        return 0
    except (
        PipelineError,
        focus_macos.ContractError,
        package_local_dmg.PackageError,
        OSError,
        ValueError,
        plistlib.InvalidFileException,
    ) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
