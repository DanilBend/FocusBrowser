#!/usr/bin/env python3
"""Safely plan or acquire the exact Chromium source used by Focus Browser.

The default mode is a read-only preflight. Network and filesystem mutations
are possible only when ``--execute-acquisition`` is supplied explicitly.
This tool is intentionally macOS-only and does not build, patch, sign, package,
publish, delete, or alter the global Xcode selection.
"""

import argparse
import configparser
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path


CHROMIUM_VERSION = "150.0.7871.128"
CHROMIUM_TAG = CHROMIUM_VERSION
CHROMIUM_COMMIT = "81891e5ca708047763816c778216799ef14c66cb"
CHROMIUM_URL = "https://chromium.googlesource.com/chromium/src.git"

DEPOT_TOOLS_COMMIT = "93919990d65a94fd62a5b1bae4e2909df6996e4a"
DEPOT_TOOLS_URL = (
    "https://chromium.googlesource.com/chromium/tools/depot_tools.git"
)
EXCLUDED_ANGLE_TEST_DEP = "src/third_party/angle/third_party/VK-GL-CTS/src"
GCLIENT_SPEC_SHA256 = "c2ab1fe66688245018194e7845ba97102efbf9f0d40eddf87712ec7f46ce26af"

GIB = 1024 ** 3
HARD_FLOOR_GIB = 30
POST_SYNC_DESTINATION_GIB = 85
# Leave a 30 GiB acquisition allowance above the required post-sync reserve.
PRE_SYNC_DESTINATION_GIB = POST_SYNC_DESTINATION_GIB + HARD_FLOOR_GIB
POLL_SECONDS = 1.0

MAC_ARCHITECTURES = frozenset(("arm64", "x86_64"))
REPO_ROOT = Path(__file__).resolve().parents[2]
COMPLETE_MARKER = ".focus-chromium-acquisition.json"
DEPENDENCY_COMPLETE_MARKER = ".focus-project-dependencies.json"
DEPS_INI = REPO_ROOT / "focus-chromium" / "deps.ini"
DEPS_INI_SHA256 = "158806c990d70174a6f401ae488d03246d867e0272b753bfbcb7c1757633b9ea"
MAX_DEPENDENCY_BYTES = 512 * 1024 * 1024
PRE_DEPENDENCY_CACHE_GIB = HARD_FLOOR_GIB + 1

SHARED_PROJECT_DEPENDENCIES = (
    {
        "name": "search_engines_data",
        "url": (
            "https://gist.githubusercontent.com/wukko/"
            "2a591364dda346e10219e4adabd568b1/raw/"
            "e75ae3c4a1ce940ef7627916a48bc40882d24d40/"
            "nonfree-search-engines-data.tar.gz"
        ),
        "filename": "nonfree-search-engines-data.tar.gz",
        "sha256": "00a87050fa3f941d04d67fb5763991e0b8ea399a88b505ab0e56dd263f06864c",
    },
    {
        "name": "onboarding",
        "url": (
            "https://github.com/DanilBend/FocusBrowser/releases/download/"
            "build-deps-onboarding-202607132006-focus1/"
            "onboarding-page-202607132006-focus1.tar.gz"
        ),
        "filename": "onboarding-page-202607132006-focus1.tar.gz",
        "sha256": "ddb5f5e375412dc987581103d8c64a59144097a084ab3c49166a95afeea230d7",
    },
    {
        "name": "ublock_origin",
        "url": (
            "https://github.com/imputnet/uBlock/releases/download/1.72.2/"
            "uBlock0_1.72.2.chromium.zip"
        ),
        "filename": "ublock-origin-1.72.2.zip",
        "sha256": "6ea10a863eb343ddcc317fdda9c65ccb2799c74d0de06ad75aded04d38d63dca",
    },
)

MAC_HOST_DEPENDENCIES = (
    {
        "name": "chromium_node_arm64",
        "url": (
            "https://storage.googleapis.com/chromium-nodejs/"
            "6661e9b9bd7df6b45daf506c82d06d303597cb27"
        ),
        "filename": "node-darwin-arm64-150.0.7871.128.tar.gz",
        "sha256": "b1be502d1635330ebf51d85f8d32a0d3dd92b35c6700def56ae6f903906ea825",
    },
    {
        "name": "chromium_node_x64",
        "url": (
            "https://storage.googleapis.com/chromium-nodejs/"
            "9202c05a8e7c67cb2bb2fec1e50fb6188d26f281"
        ),
        "filename": "node-darwin-x64-150.0.7871.128.tar.gz",
        "sha256": "a25cd3ef35d8b4b5a59498a5a62b5b12cc271dc420ee809abaa76110d12c156e",
    },
    {
        "name": "chromium_node_modules",
        "url": (
            "https://storage.googleapis.com/chromium-nodejs/"
            "38df23cf794887ca7c81d57bf30f66c38c144e28"
        ),
        "filename": "chromium-node-modules-150.0.7871.128.tar.gz",
        "sha256": "6781ef493aa77be4ca4824dc1d5f5157a2fbc56dacafe20914da4469f7a01b87",
    },
    {
        "name": "esbuild_darwin_arm64",
        "url": (
            "https://registry.npmjs.org/@esbuild/darwin-arm64/-/"
            "darwin-arm64-0.25.9.tgz"
        ),
        "filename": "esbuild-darwin-arm64-0.25.9.tgz",
        "sha256": "dd1abc1f869ab57c5e1b76ddef546d53c473a0d06aecb77fe10af084c47ac7e6",
    },
    {
        "name": "esbuild_darwin_x64",
        "url": (
            "https://registry.npmjs.org/@esbuild/darwin-x64/-/"
            "darwin-x64-0.25.9.tgz"
        ),
        "filename": "esbuild-darwin-x64-0.25.9.tgz",
        "sha256": "14a33c598fb04937a75efa88c5f58e2317bfd821e36b1e222bd040ff34828738",
    },
    {
        "name": "rollup_darwin_arm64",
        "url": (
            "https://registry.npmjs.org/@rollup/rollup-darwin-arm64/-/"
            "rollup-darwin-arm64-4.50.1.tgz"
        ),
        "filename": "rollup-darwin-arm64-4.50.1.tgz",
        "sha256": "4fcf015726b2b857fae02a87e74c61db6021d578b5a93066871f585f4c2d449b",
    },
    {
        "name": "rollup_darwin_x64",
        "url": (
            "https://registry.npmjs.org/@rollup/rollup-darwin-x64/-/"
            "rollup-darwin-x64-4.50.1.tgz"
        ),
        "filename": "rollup-darwin-x64-4.50.1.tgz",
        "sha256": "b3ca6f5e10f3ccd532b1dfc070b5845c2194e024e40ccaa30ec34f68e3f79da0",
    },
)

PROJECT_DEPENDENCIES = SHARED_PROJECT_DEPENDENCIES + MAC_HOST_DEPENDENCIES


class AcquisitionError(RuntimeError):
    """Raised when an acquisition safety or integrity contract is not met."""


def gibibytes(byte_count):
    """Return a stable GiB value suitable for JSON reports."""
    return round(byte_count / GIB, 3)


def path_is_within(path, parent):
    """Python 3.8-compatible equivalent of Path.is_relative_to()."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _existing_ancestors(path):
    """Yield existing path components from the filesystem root downward."""
    existing = []
    current = path
    while not os.path.lexists(str(current)):
        if current == current.parent:
            break
        current = current.parent
    if not os.path.lexists(str(current)):
        raise AcquisitionError("no existing ancestor for {}".format(path))
    while True:
        existing.append(current)
        if current == current.parent:
            break
        current = current.parent
    return list(reversed(existing))


def validate_new_leaf(raw_path, label):
    """Validate an absent leaf under a real, writable, non-symlink parent."""
    text = str(raw_path)
    path = Path(text)
    if not path.is_absolute():
        raise AcquisitionError("{} must be an absolute path".format(label))
    if any(ord(character) < 32 for character in text):
        raise AcquisitionError("{} contains a control character".format(label))
    if " " in text:
        raise AcquisitionError(
            "{} must not contain spaces (Chromium checkout requirement)".format(label)
        )
    if ".." in path.parts:
        raise AcquisitionError("{} must not contain '..'".format(label))
    if len(path.parts) < 4:
        raise AcquisitionError("{} is too broad: {}".format(label, path))
    if os.path.lexists(text):
        raise AcquisitionError(
            "{} already exists; refusing a reusable or partial target: {}".format(
                label, path
            )
        )

    parent = path.parent
    if not parent.is_dir():
        raise AcquisitionError(
            "{} parent must already exist and be a directory: {}".format(label, parent)
        )
    for ancestor in _existing_ancestors(parent):
        mode = ancestor.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise AcquisitionError(
                "{} ancestor must not be a symlink: {}".format(label, ancestor)
            )
    if not os.access(str(parent), os.W_OK | os.X_OK):
        raise AcquisitionError("{} parent is not writable: {}".format(label, parent))

    canonical = path.resolve(strict=False)
    if canonical != path:
        raise AcquisitionError(
            "{} does not resolve to itself (possible path escape): {} -> {}".format(
                label, path, canonical
            )
        )
    if path_is_within(canonical, REPO_ROOT):
        raise AcquisitionError(
            "{} must be outside the Focus Browser git worktree: {}".format(label, path)
        )
    return canonical


def validate_distinct_paths(left, right):
    """Reject equal or nested acquisition locations."""
    if left == right:
        raise AcquisitionError("paths must be different")
    if path_is_within(left, right) or path_is_within(right, left):
        raise AcquisitionError("paths must not contain one another")


def validate_all_distinct_paths(named_paths):
    """Reject equality or nesting across every external acquisition root."""
    items = list(named_paths.items())
    for index, (left_name, left) in enumerate(items):
        for right_name, right in items[index + 1:]:
            try:
                validate_distinct_paths(left, right)
            except AcquisitionError as error:
                raise AcquisitionError(
                    "{} and {} conflict: {}".format(left_name, right_name, error)
                ) from error


def sha256_file(path):
    """Hash a regular file without following a symlink."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise AcquisitionError("expected a regular file to hash: {}".format(path))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_dependency_manifest():
    """Pin the shared manifest plus the exact Mac host build inputs."""
    if DEPS_INI.is_symlink() or not DEPS_INI.is_file():
        raise AcquisitionError("missing real project dependency manifest: {}".format(DEPS_INI))
    observed_manifest_hash = sha256_file(DEPS_INI)
    if observed_manifest_hash != DEPS_INI_SHA256:
        raise AcquisitionError(
            "deps.ini hash mismatch: {} != {}".format(
                observed_manifest_hash, DEPS_INI_SHA256
            )
        )
    parser = configparser.ConfigParser()
    parser.read(DEPS_INI, encoding="utf-8")
    expected_names = [item["name"] for item in SHARED_PROJECT_DEPENDENCIES]
    if parser.sections() != expected_names:
        raise AcquisitionError("deps.ini component inventory or order changed")
    combined = SHARED_PROJECT_DEPENDENCIES + MAC_HOST_DEPENDENCIES
    seen_names = set()
    seen_filenames = set()
    for expected in combined:
        if not isinstance(expected, dict) or set(expected) != {
            "name",
            "url",
            "filename",
            "sha256",
        }:
            raise AcquisitionError("project dependency entry schema mismatch")
        name = expected["name"]
        filename_value = expected["filename"]
        if (
            not isinstance(name, str)
            or not name
            or name in seen_names
            or not isinstance(filename_value, str)
            or not filename_value
            or filename_value in seen_filenames
        ):
            raise AcquisitionError("duplicate or invalid project dependency identity")
        seen_names.add(name)
        seen_filenames.add(filename_value)
        filename = Path(filename_value)
        if filename.name != filename_value or len(filename.parts) != 1:
            raise AcquisitionError("unsafe dependency cache filename")
        if not isinstance(expected["url"], str) or not expected["url"].startswith(
            "https://"
        ):
            raise AcquisitionError("dependency URL must use HTTPS")
        if not isinstance(expected["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected["sha256"]
        ):
            raise AcquisitionError("dependency SHA-256 must be lowercase hexadecimal")

    for expected in SHARED_PROJECT_DEPENDENCIES:
        section = parser[expected["name"]]
        observed = {
            "url": section.get("url"),
            "filename": section.get("download_filename"),
            "sha256": section.get("sha256"),
        }
        for key in ("url", "filename", "sha256"):
            if observed[key] != expected[key]:
                raise AcquisitionError(
                    "deps.ini {} {} mismatch".format(expected["name"], key)
                )
    return {
        "path": str(DEPS_INI),
        "sha256": observed_manifest_hash,
        "shared_entries": [dict(item) for item in SHARED_PROJECT_DEPENDENCIES],
        "mac_host_entries": [dict(item) for item in MAC_HOST_DEPENDENCIES],
        "entries": [dict(item) for item in combined],
    }


def validate_host(environ=None, platform_name=None, machine=None):
    """Reject every non-macOS host and any inherited iOS SDK environment."""
    environ = os.environ if environ is None else environ
    platform_name = sys.platform if platform_name is None else platform_name
    machine = platform.machine() if machine is None else machine
    if platform_name != "darwin":
        raise AcquisitionError("Chromium acquisition is supported only on macOS")
    normalized_machine = machine.lower()
    if normalized_machine not in MAC_ARCHITECTURES:
        raise AcquisitionError(
            "unsupported Mac architecture: {} (expected arm64 or x86_64)".format(machine)
        )
    sdkroot = environ.get("SDKROOT", "").lower()
    if any(token in sdkroot for token in ("iphone", "appletv", "watch", "xros")):
        raise AcquisitionError("iOS-family SDKROOT is forbidden for macOS acquisition")
    return normalized_machine


def free_bytes(path):
    """Return free bytes for an existing path."""
    return shutil.disk_usage(str(path)).free


def ensure_initial_capacity(destination_parent, dependency_cache_parent=None):
    """Require conservative capacity before any directory is created."""
    destination_free = free_bytes(destination_parent)
    if destination_free < PRE_SYNC_DESTINATION_GIB * GIB:
        raise AcquisitionError(
            "destination volume has {:.3f} GiB free; pre-sync gate requires {} GiB".format(
                gibibytes(destination_free), PRE_SYNC_DESTINATION_GIB
            )
        )
    report = {
        "destination_free_gib": gibibytes(destination_free),
        "destination_required_gib": PRE_SYNC_DESTINATION_GIB,
    }
    if dependency_cache_parent is not None:
        dependency_free = free_bytes(dependency_cache_parent)
        if dependency_free < PRE_DEPENDENCY_CACHE_GIB * GIB:
            raise AcquisitionError(
                "dependency cache volume has {:.3f} GiB free; gate requires {} GiB".format(
                    gibibytes(dependency_free), PRE_DEPENDENCY_CACHE_GIB
                )
            )
        report.update(
            {
                "dependency_cache_free_gib": gibibytes(dependency_free),
                "dependency_cache_required_gib": PRE_DEPENDENCY_CACHE_GIB,
            }
        )
    return report


def ensure_hard_floor(paths):
    """Fail if any watched filesystem is below the non-negotiable floor."""
    measurements = {}
    for path in paths:
        available = free_bytes(path)
        measurements[str(path)] = gibibytes(available)
        if available < HARD_FLOOR_GIB * GIB:
            raise AcquisitionError(
                "hard disk floor breached at {}: {:.3f} GiB < {} GiB".format(
                    path, gibibytes(available), HARD_FLOOR_GIB
                )
            )
    return measurements


def ensure_post_sync_capacity(destination, dependency_cache=None):
    """Require build headroom after the complete source sync."""
    destination_free = free_bytes(destination)
    if destination_free < POST_SYNC_DESTINATION_GIB * GIB:
        raise AcquisitionError(
            "post-sync destination has {:.3f} GiB free; {} GiB is required before build".format(
                gibibytes(destination_free), POST_SYNC_DESTINATION_GIB
            )
        )
    report = {
        "destination_free_gib": gibibytes(destination_free),
    }
    if dependency_cache is not None:
        dependency_free = free_bytes(dependency_cache)
        if dependency_free < HARD_FLOOR_GIB * GIB:
            raise AcquisitionError(
                "post-sync dependency cache has {:.3f} GiB free; hard floor is {} GiB".format(
                    gibibytes(dependency_free), HARD_FLOOR_GIB
                )
            )
        report["dependency_cache_free_gib"] = gibibytes(dependency_free)
    return report


def gclient_spec():
    """Return the complete pinned, macOS-only gclient configuration."""
    spec = """solutions = [
  {{
    "name": "src",
    "url": {url!r},
    "custom_deps": {{
      {excluded_angle!r}: None,
    }},
    "custom_vars": {{
      "checkout_configuration": "small",
      "non_git_source": "False",
    }},
  }},
]
target_os = ["mac"]
target_os_only = True
""".format(url=CHROMIUM_URL, excluded_angle=EXCLUDED_ANGLE_TEST_DEP)
    observed = hashlib.sha256(spec.encode("utf-8")).hexdigest()
    if observed != GCLIENT_SPEC_SHA256:
        raise AcquisitionError(
            "internal gclient spec hash mismatch: {} != {}".format(
                observed, GCLIENT_SPEC_SHA256
            )
        )
    return spec


def build_commands(git_path, destination):
    """Build the fixed argv-only command sequence; never invoke a shell."""
    depot_tools = destination / "depot_tools"
    gclient = depot_tools / "gclient"
    spec = gclient_spec()
    commands = [
        [git_path, "init", "--quiet", str(depot_tools)],
        [
            git_path,
            "-C",
            str(depot_tools),
            "remote",
            "add",
            "origin",
            DEPOT_TOOLS_URL,
        ],
        [
            git_path,
            "-C",
            str(depot_tools),
            "fetch",
            "--depth=1",
            "origin",
            DEPOT_TOOLS_COMMIT,
        ],
        [
            git_path,
            "-C",
            str(depot_tools),
            "checkout",
            "--detach",
            DEPOT_TOOLS_COMMIT,
        ],
        [str(gclient), "config", "--spec", spec],
        [
            str(gclient),
            "sync",
            "--no-history",
            "--nohooks",
            "--revision",
            "src@{}".format(CHROMIUM_COMMIT),
        ],
    ]
    validate_commands(commands, destination)
    return commands


def build_dependency_commands(curl_path, dependency_cache, manifest):
    """Plan bounded HTTPS downloads into .part files; never unpack them."""
    commands = []
    for dependency in manifest["entries"]:
        partial = dependency_cache / (dependency["filename"] + ".part")
        commands.append(
            [
                curl_path,
                "--fail",
                "--location",
                "--proto",
                "=https",
                "--tlsv1.2",
                "--retry",
                "3",
                "--connect-timeout",
                "20",
                "--max-filesize",
                str(MAX_DEPENDENCY_BYTES),
                "--output",
                str(partial),
                dependency["url"],
            ]
        )
    validate_dependency_commands(commands, dependency_cache, manifest)
    return commands


def validate_dependency_commands(commands, dependency_cache, manifest):
    """Reject unpacking, source writes, non-HTTPS URLs, and unbounded output."""
    if len(commands) != len(manifest["entries"]):
        raise AcquisitionError("dependency command count mismatch")
    forbidden = ("tar", "unzip", "ditto", "rm", "sudo", "xcode-select")
    for command, dependency in zip(commands, manifest["entries"]):
        if Path(command[0]).name != "curl":
            raise AcquisitionError("project dependencies must be retrieved with curl")
        if any(Path(argument).name in forbidden for argument in command):
            raise AcquisitionError("dependency command contains a forbidden program")
        if "--max-filesize" not in command or "=https" not in command:
            raise AcquisitionError("dependency download must be HTTPS and size-bounded")
        expected_partial = str(
            dependency_cache / (dependency["filename"] + ".part")
        )
        if expected_partial not in command or dependency["url"] != command[-1]:
            raise AcquisitionError("dependency command path/URL mismatch")


def finalize_dependency_downloads(dependency_cache, manifest):
    """Hash .part files and atomically expose only verified archives."""
    verified = []
    for dependency in manifest["entries"]:
        partial = dependency_cache / (dependency["filename"] + ".part")
        final = dependency_cache / dependency["filename"]
        if final.exists() or final.is_symlink():
            raise AcquisitionError("refusing to overwrite dependency: {}".format(final))
        if partial.is_symlink() or not partial.is_file():
            raise AcquisitionError("dependency download is missing: {}".format(partial))
        size = partial.stat().st_size
        if size <= 0 or size > MAX_DEPENDENCY_BYTES:
            raise AcquisitionError(
                "dependency size is outside the safe range: {} bytes".format(size)
            )
        observed = sha256_file(partial)
        if observed != dependency["sha256"]:
            raise AcquisitionError(
                "dependency hash mismatch for {}: {} != {}".format(
                    dependency["name"], observed, dependency["sha256"]
                )
            )
        os.replace(str(partial), str(final))
        verified.append(
            {
                "name": dependency["name"],
                "path": str(final),
                "bytes": size,
                "sha256": observed,
            }
        )
    marker_report = {
        "deps_ini_sha256": manifest["sha256"],
        "archives": verified,
        "unpacked": False,
        "source_mutated": False,
    }
    marker = dependency_cache / DEPENDENCY_COMPLETE_MARKER
    temporary = dependency_cache / (DEPENDENCY_COMPLETE_MARKER + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(marker_report, indent=2, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(marker))
    return {"marker": str(marker), **marker_report}


def validate_commands(commands, destination):
    """Statically reject global mutations and non-macOS acquisition tokens."""
    forbidden_programs = {"sudo", "xcode-select", "rm", "rmdir"}
    forbidden_tokens = {
        "--global",
        "target_os=ios",
        "target_os=win",
        "target_os=android",
        "cache_dir",
        "git_cache_path",
        "--git-cache",
    }
    flattened = []
    for command in commands:
        if not isinstance(command, list) or not command:
            raise AcquisitionError("every command must be a non-empty argv list")
        program = Path(command[0]).name
        if program in forbidden_programs:
            raise AcquisitionError("forbidden program in acquisition plan: {}".format(program))
        for argument in command:
            if not isinstance(argument, str):
                raise AcquisitionError("command arguments must be strings")
            flattened.append(argument.lower())
    joined = "\n".join(flattened)
    for token in forbidden_tokens:
        if token in joined:
            raise AcquisitionError("forbidden global/platform token: {}".format(token))
    if 'target_os = ["mac"]' not in joined:
        raise AcquisitionError("gclient plan is missing target_os = [\"mac\"]")
    if "target_os_only = true" not in joined:
        raise AcquisitionError("gclient plan is missing target_os_only = True")
    if str(destination).lower() not in joined:
        raise AcquisitionError("command plan must contain the explicit destination")


def safe_environment(destination, inherited=None):
    """Create a child-only depot_tools environment without SDK contamination."""
    inherited = os.environ if inherited is None else inherited
    result = dict(inherited)
    for variable in (
        "SDKROOT",
        "PLATFORM_NAME",
        "EFFECTIVE_PLATFORM_NAME",
        "IPHONEOS_DEPLOYMENT_TARGET",
        "TVOS_DEPLOYMENT_TARGET",
        "WATCHOS_DEPLOYMENT_TARGET",
        "XROS_DEPLOYMENT_TARGET",
        "GIT_CACHE_PATH",
    ):
        result.pop(variable, None)
    depot_tools = destination / "depot_tools"
    result.update(
        {
            "PATH": str(depot_tools) + os.pathsep + inherited.get("PATH", ""),
            "DEPOT_TOOLS_UPDATE": "0",
            "DEPOT_TOOLS_METRICS": "0",
            "GCLIENT_FILE": str(destination / ".gclient"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return result


def _stop_process_group(process):
    """Stop the complete acquisition process group after a disk breach."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
        process.wait()


def run_monitored(command, cwd, environ, watched_paths, poll_seconds=POLL_SECONDS):
    """Run one argv command while enforcing the 30 GiB floor."""
    ensure_hard_floor(watched_paths)
    process = subprocess.Popen(  # pylint: disable=consider-using-with
        command,
        cwd=str(cwd),
        env=environ,
        start_new_session=True,
    )
    while process.poll() is None:
        try:
            ensure_hard_floor(watched_paths)
        except AcquisitionError:
            _stop_process_group(process)
            raise
        time.sleep(poll_seconds)
    if process.returncode:
        raise AcquisitionError(
            "command failed with exit code {}: {}".format(
                process.returncode, " ".join(command[:4])
            )
        )
    ensure_hard_floor(watched_paths)


def capture(command, cwd, environ):
    """Run a small local verification command and return stripped stdout."""
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=environ,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _validate_real_directory(path, containment_root=None):
    if not path.is_dir() or path.is_symlink():
        raise AcquisitionError("expected a real directory: {}".format(path))
    canonical = path.resolve(strict=True)
    if containment_root is not None and not path_is_within(
        canonical, containment_root.resolve(strict=True)
    ):
        raise AcquisitionError("directory escaped acquisition root: {}".format(path))
    return canonical


def chromium_version(source_root):
    """Read chrome/VERSION without evaluating source-controlled code."""
    version_file = source_root / "chrome" / "VERSION"
    if not version_file.is_file() or version_file.is_symlink():
        raise AcquisitionError("missing real Chromium version file: {}".format(version_file))
    values = {}
    for line in version_file.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in ("MAJOR", "MINOR", "BUILD", "PATCH") and value.isdigit():
            values[key] = value
    required = ("MAJOR", "MINOR", "BUILD", "PATCH")
    if any(key not in values for key in required):
        raise AcquisitionError("invalid Chromium version file")
    return ".".join(values[key] for key in required)


def verify_checkout(git_path, destination, environ):
    """Verify exact immutable revisions and the generated macOS-only config."""
    depot_tools = destination / "depot_tools"
    source_root = destination / "src"
    _validate_real_directory(destination)
    _validate_real_directory(depot_tools, destination)
    _validate_real_directory(source_root, destination)

    depot_head = capture(
        [git_path, "-C", str(depot_tools), "rev-parse", "HEAD"],
        destination,
        environ,
    )
    chromium_head = capture(
        [git_path, "-C", str(source_root), "rev-parse", "HEAD"],
        destination,
        environ,
    )
    if depot_head != DEPOT_TOOLS_COMMIT:
        raise AcquisitionError(
            "depot_tools HEAD mismatch: {} != {}".format(
                depot_head, DEPOT_TOOLS_COMMIT
            )
        )
    if chromium_head != CHROMIUM_COMMIT:
        raise AcquisitionError(
            "Chromium HEAD mismatch: {} != {}".format(chromium_head, CHROMIUM_COMMIT)
        )
    actual_version = chromium_version(source_root)
    if actual_version != CHROMIUM_VERSION:
        raise AcquisitionError(
            "Chromium version mismatch: {} != {}".format(
                actual_version, CHROMIUM_VERSION
            )
        )

    config_path = destination / ".gclient"
    if not config_path.is_file() or config_path.is_symlink():
        raise AcquisitionError("missing real .gclient configuration")
    config = config_path.read_text(encoding="utf-8")
    required_fragments = (
        CHROMIUM_URL,
        "{!r}: None".format(EXCLUDED_ANGLE_TEST_DEP),
        '"checkout_configuration": "small"',
        '"non_git_source": "False"',
        'target_os = ["mac"]',
        "target_os_only = True",
    )
    for fragment in required_fragments:
        if fragment not in config:
            raise AcquisitionError(".gclient contract missing: {}".format(fragment))
    forbidden = (
        "target_os = [\"ios\"]",
        "target_os = [\"win\"]",
        "android",
        "cache_dir",
    )
    if any(token in config.lower() for token in forbidden):
        raise AcquisitionError(".gclient contains a non-macOS target")
    return {
        "chromium_version": actual_version,
        "chromium_commit": chromium_head,
        "depot_tools_commit": depot_head,
        "source_root": str(source_root),
        "gclient_config": str(config_path),
    }


def _write_complete_marker(destination, report):
    """Atomically mark a fully verified acquisition; never mark a partial one."""
    marker = destination / COMPLETE_MARKER
    temporary = destination / (COMPLETE_MARKER + ".tmp")
    data = json.dumps(report, indent=2, sort_keys=True) + "\n"
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(marker))
    return marker


def preflight(
    destination,
    dependency_cache=None,
    include_project_dependencies=False,
    environ=None,
    platform_name=None,
    machine=None,
):
    """Build a complete read-only acquisition report."""
    environ = os.environ if environ is None else environ
    host_arch = validate_host(environ, platform_name, machine)
    destination = validate_new_leaf(destination, "destination")
    named_paths = {"destination": destination}
    resolved_dependency_cache = None
    dependency_manifest = None
    if include_project_dependencies:
        if dependency_cache is None:
            raise AcquisitionError(
                "--dependency-cache is required with --fetch-project-dependencies"
            )
        resolved_dependency_cache = validate_new_leaf(
            dependency_cache, "dependency cache"
        )
        named_paths["dependency cache"] = resolved_dependency_cache
        dependency_manifest = validate_dependency_manifest()
    elif dependency_cache is not None:
        raise AcquisitionError(
            "--dependency-cache is accepted only with --fetch-project-dependencies"
        )
    validate_all_distinct_paths(named_paths)
    capacity = ensure_initial_capacity(
        destination.parent,
        resolved_dependency_cache.parent if resolved_dependency_cache else None,
    )
    git_path = shutil.which("git", path=environ.get("PATH"))
    if not git_path:
        raise AcquisitionError("git was not found in PATH")
    git_path = str(Path(git_path).resolve(strict=True))
    commands = build_commands(git_path, destination)
    dependency_commands = []
    if include_project_dependencies:
        curl_path = shutil.which("curl", path=environ.get("PATH"))
        if not curl_path:
            raise AcquisitionError("curl was not found in PATH")
        curl_path = str(Path(curl_path).resolve(strict=True))
        dependency_commands = build_dependency_commands(
            curl_path, resolved_dependency_cache, dependency_manifest
        )
    report = {
        "status": "preflight_only",
        "execution_requested": False,
        "network_performed": False,
        "filesystem_mutated": False,
        "host": {"platform": "macOS", "architecture": host_arch},
        "destination": str(destination),
        "pins": {
            "chromium_version": CHROMIUM_VERSION,
            "chromium_tag": CHROMIUM_TAG,
            "chromium_commit": CHROMIUM_COMMIT,
            "depot_tools_commit": DEPOT_TOOLS_COMMIT,
        },
        "gclient": {
            "target_os": ["mac"],
            "target_os_only": True,
            "no_history": True,
            "hooks_during_acquisition": False,
            "git_cache": False,
            "spec_sha256": GCLIENT_SPEC_SHA256,
        },
        "disk": {
            **capacity,
            "hard_floor_gib": HARD_FLOOR_GIB,
            "post_sync_destination_required_gib": POST_SYNC_DESTINATION_GIB,
            "runtime_monitoring": True,
        },
        "global_xcode_select_mutation": False,
        "commands": commands,
    }
    report["project_dependencies"] = {
        "enabled": include_project_dependencies,
        "cache": (
            str(resolved_dependency_cache) if resolved_dependency_cache else None
        ),
        "manifest": dependency_manifest,
        "commands": dependency_commands,
        "unpack_planned": False,
        "source_mutation_planned": False,
        "windows_downloads_ini_used": False,
    }
    return report


def execute(report, inherited_environ=None):
    """Execute a previously validated plan and verify its complete result."""
    inherited_environ = os.environ if inherited_environ is None else inherited_environ
    validate_host(inherited_environ)
    destination = Path(report["destination"])
    dependencies = report["project_dependencies"]
    dependency_cache = (
        Path(dependencies["cache"]) if dependencies["enabled"] else None
    )
    # Repeat all no-existing-target checks immediately before the first write.
    destination = validate_new_leaf(destination, "destination")
    named_paths = {"destination": destination}
    if dependency_cache is not None:
        dependency_cache = validate_new_leaf(dependency_cache, "dependency cache")
        named_paths["dependency cache"] = dependency_cache
    validate_all_distinct_paths(named_paths)
    ensure_initial_capacity(
        destination.parent,
        dependency_cache.parent if dependency_cache else None,
    )

    git_path = shutil.which("git", path=inherited_environ.get("PATH"))
    if not git_path:
        raise AcquisitionError("git was not found in PATH at execution time")
    git_path = str(Path(git_path).resolve(strict=True))
    expected_commands = build_commands(git_path, destination)
    if report["commands"] != expected_commands:
        raise AcquisitionError("acquisition command plan changed after preflight")

    dependency_manifest = None
    if dependency_cache is not None:
        dependency_manifest = validate_dependency_manifest()
        if dependencies["manifest"] != dependency_manifest:
            raise AcquisitionError("project dependency manifest changed after preflight")
        curl_path = shutil.which("curl", path=inherited_environ.get("PATH"))
        if not curl_path:
            raise AcquisitionError("curl was not found in PATH at execution time")
        curl_path = str(Path(curl_path).resolve(strict=True))
        expected_dependency_commands = build_dependency_commands(
            curl_path, dependency_cache, dependency_manifest
        )
        if dependencies["commands"] != expected_dependency_commands:
            raise AcquisitionError("dependency command plan changed after preflight")

    destination.mkdir(mode=0o700)
    if dependency_cache is not None:
        dependency_cache.mkdir(mode=0o700)
    watched_paths = tuple(named_paths.values())
    ensure_hard_floor(watched_paths)
    environ = safe_environment(destination, inherited_environ)

    for command in report["commands"]:
        run_monitored(command, destination, environ, watched_paths)

    dependency_verification = None
    if dependency_cache is not None:
        for command in dependencies["commands"]:
            run_monitored(command, destination, environ, watched_paths)
        dependency_verification = finalize_dependency_downloads(
            dependency_cache, dependency_manifest
        )

    verification = verify_checkout(git_path, destination, environ)
    post_sync = ensure_post_sync_capacity(destination, dependency_cache)
    completed = dict(report)
    completed.update(
        {
            "status": "acquisition_complete",
            "execution_requested": True,
            "network_performed": True,
            "filesystem_mutated": True,
            "verification": verification,
            "project_dependency_verification": dependency_verification,
            "post_sync_disk": post_sync,
        }
    )
    marker = _write_complete_marker(destination, completed)
    completed["complete_marker"] = str(marker)
    return completed


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Plan a pinned macOS Chromium checkout. The default is read-only; "
            "network acquisition requires --execute-acquisition."
        )
    )
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="absolute absent checkout root (will contain src and depot_tools)",
    )
    parser.add_argument(
        "--dependency-cache",
        type=Path,
        help=(
            "absolute absent verified archive cache; valid only with "
            "--fetch-project-dependencies"
        ),
    )
    parser.add_argument(
        "--fetch-project-dependencies",
        action="store_true",
        help=(
            "plan retrieval of all ten pinned shared and Mac-host archives; "
            "never unpack them or mutate Chromium source"
        ),
    )
    parser.add_argument(
        "--execute-acquisition",
        action="store_true",
        help="explicitly permit network and filesystem acquisition",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        report = preflight(
            args.destination,
            dependency_cache=args.dependency_cache,
            include_project_dependencies=args.fetch_project_dependencies,
        )
        if args.execute_acquisition:
            report = execute(report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (AcquisitionError, OSError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {"status": "blocked", "error": str(error)},
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
