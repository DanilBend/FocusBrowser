#!/usr/bin/env python3
"""Fail-closed local release stages for the macOS Sparkle build.

This tool consumes completed Auto slice outputs.  It never invokes GN or a
compiling Ninja command; the seal stage runs the pinned Ninja in read-only
``-n`` mode.  It never publishes, notarizes, or uses a Developer ID.  Every
stage is a dry run unless ``--execute`` is supplied explicitly.
"""

import argparse
import ast
import base64
import ctypes
import errno
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path


MACOS_DIR = Path(__file__).resolve().parent
if str(MACOS_DIR) not in sys.path:
    sys.path.insert(0, str(MACOS_DIR))

import autoupdate_contract  # pylint: disable=wrong-import-position
import build_pipeline  # pylint: disable=wrong-import-position
import focus_macos  # pylint: disable=wrong-import-position
import package_local_dmg  # pylint: disable=wrong-import-position
import runtime_smoke  # pylint: disable=wrong-import-position


SCHEMA = 1
UPDATE_MODE = "autoupdate"
APP_NAME = "Focus Browser.app"
PACKAGING_NAME = "Focus Browser Packaging"
SIGNED_DISTRIBUTION = "stable"

ARM_OUT = "out/FocusMacArm64Auto"
X64_OUT = "out/FocusMacX64Auto"
STAGING_ROOT = "out/FocusMacAutoStaging"
UNSIGNED_ROOT = "out/FocusMacUnsignedUniversalAuto"
SIGNED_ROOT = "out/FocusMacSignedUniversalAuto"

STAGE_RECEIPT = "autoupdate-release-stage.json"
BUILD_SEAL_RECEIPT = "out/FocusMacAutoBuildSeal.json"
MERGE_RECEIPT = "autoupdate-release-merge.json"
SIGN_RECEIPT = "autoupdate-release-sign.json"
ACCEPT_RECEIPT = "autoupdate-release-accept.json"
PACKAGE_RECEIPT_SUFFIX = ".autoupdate-release.json"

DITTO = "/usr/bin/ditto"
LIPO = "/usr/bin/lipo"
OTOOL = "/usr/bin/otool"
CODESIGN = "/usr/bin/codesign"
SYSTEM_TOOLS = (DITTO, LIPO, OTOOL, CODESIGN)
COMMAND_TIMEOUT_SECONDS = 60 * 60
CAPTURE_TIMEOUT_SECONDS = 60
MAX_RECEIPT_BYTES = 1024 * 1024
PINNED_PYTHON_MANIFEST_SHA256 = (
    "89b36ecf6e9fe4a732a3ad8cadd8c4456bc9289d7410b4cf7de146218e81e3b5"
)
PINNED_PYTHON_ISOLATION_ARGS = (
    "-I",
    "-B",
    "-X",
    "pycache_prefix=/var/empty/focusbrowser-python-cache",
)
AUTO_PREPARATION_ADDENDUM = "out/FocusMacAutoPreparationAddendum.json"
PINNED_BASE_PREPARATION_SHA256 = (
    "d74956f9778f3a014c2750654374f65c62669fa35a31f6f4a350fe0580f6ab47"
)
PINNED_BASE_ACQUISITION_SHA256 = (
    "7b765d8da2281c5476568d7a6039cbc8057c7a80e2b158689f247772e47d3d43"
)
PINNED_BASE_TOOL_BOOTSTRAP_SHA256 = (
    "0111edb73af58339e3cd45f570309cab1cd9a0582dc9c34ed6a5e15fbf9e0e03"
)
PINNED_AUTO_VERSION_SHA256 = (
    "5022bb9910dc9125cf334f10734fe0ce4be7991d95abcfec60567cfbb42a5783"
)
PINNED_BASE_VERSION_SHA256 = (
    "8536f0e864abdb194deb1145c6b496b4f194ba0072f6e47144939d4a0fda34c7"
)
AUTO_PATCH_PATHS = (
    "platform/macos/patches/focus-macos-icon-precedence.patch",
    "platform/macos/patches/focus-sparkle-autoupdate.patch",
)
COMPATIBILITY_RECEIPTS = (
    "FocusMacAdHocRuntimeSigningCompatibility.json",
    "FocusMacFreshX64Preparation.json",
    "FocusMacGnCompatibility.json",
    "FocusMacHomeAliasCompatibility.json",
    "FocusMacScreenAIDisabledCompatibility.json",
    "FocusMacSwiftShaderDisabledSigningCompatibility.json",
    "FocusMacXcode27Compatibility.json",
    "FocusMacXcode27LinkeditStripCompatibility.json",
    "FocusMacXcode27SeatbeltCompatibility.json",
)
PINNED_COMPATIBILITY_RECEIPT_SHA256 = {
    "FocusMacAdHocRuntimeSigningCompatibility.json": (
        "e63b998b36dab8218447b03d83184270f6f9618a9d571280da4a2dfcf0342399"
    ),
    "FocusMacFreshX64Preparation.json": (
        "213687ac3331d07ed5b336e02814bba5787a5c67bff0d6e755171900864ad881"
    ),
    "FocusMacGnCompatibility.json": (
        "a7faf8a93e42208c83c094fb6196f4ed83d01a9b61591940c60b048aef065298"
    ),
    "FocusMacHomeAliasCompatibility.json": (
        "75f68ff9dd7dad49a2bfc033e324baa86d79de783e886d6bccef8ff604caebc9"
    ),
    "FocusMacScreenAIDisabledCompatibility.json": (
        "4248c5a9088d7c9aa0b003ea2a668513532930620466c2f4baf3b9e196ec2c2b"
    ),
    "FocusMacSwiftShaderDisabledSigningCompatibility.json": (
        "50177568468760e52c00c7be4301211c8201893c4755e8116b04908df2d76340"
    ),
    "FocusMacXcode27Compatibility.json": (
        "2f1dd6c5151e5ece2b63c7c52c879c92bbf8d1f3f83052fc15d7af8b08363a3b"
    ),
    "FocusMacXcode27LinkeditStripCompatibility.json": (
        "77ef380124643d5ae11d9775f7591d82809e0c8eacada1c16c915b0e55159c3a"
    ),
    "FocusMacXcode27SeatbeltCompatibility.json": (
        "b2f45ed0364da706c1d3f63816b85f5aae84b5c90476316b6b1d4c03f7ddbf2f"
    ),
}
NINJA_SEAL_TARGETS = ("chrome", "chrome/installer/mac:copies")

PINNED_COMMON_FLAGS_SHA256 = (
    "c27728f78d80d7eb6ab9f2d9136fb53ecd59fb69693d969c6092f9573602459b"
)
PINNED_AUTO_PROFILE_SHA256 = {
    "arm64": "f4b471ad6fc2370dc29e2649aca68936167d5e146366c4f849f1af49f26fc565",
    "x64": "daf788506f69a6a9b6cb8bbd67f00998c1f450a2f426f4e631233d0f0f01d4e9",
}
PINNED_CANONICAL_ASSIGNMENTS_SHA256 = {
    "arm64": "5a7e243819acefe8de437a3d52692e35ba414a0145d0c72a67cab4e865703a28",
    "x64": "9e83872364bc38b32b9fc3bd778dd052bb18128e425b8dc316d4299789e34217",
}
PINNED_SIGN_CHROME_SHA256 = (
    "44846dccd82fbfcaeca36ff180d49ab943d8d2190a58f33e6863d6692aa17696"
)
PINNED_ADHOC_RELEASE_SIGN_SHA256 = (
    "abfaefa22863ebb8730cfc2eb93076780fffc01dc5113bbef08e24ba9469a742"
)
PINNED_BUILD_PROPS_TEMPLATE_SHA256 = (
    "25f76334bf023249dff43160061c16104956692120bc180d2717592561656c15"
)
PINNED_APP_ENTITLEMENTS_TEMPLATE_SHA256 = (
    "bab6f068004a79a5c439958d4060ff74edaad6301ca1390fed2cb666af4d28b7"
)
SPARKLE_DEPENDENCY = "@rpath/Sparkle.framework/Versions/B/Sparkle"
FOCUS_FRAMEWORK_RPATH = "@loader_path/../../.."
RUNTIME_TIMEOUT_SECONDS = 60

# These are the complete, executable Python surface copied by Chromium's
# unbranded macOS signing target.  Every byte is pinned independently from the
# generated output directory so a modified build output cannot become code.
PINNED_SIGNING_PYTHON_SHA256 = {
    "signing/__init__.py": "1aa6c990dd10bf8cdddf7af3703066843d6fa335ff882d0e3a883aee11b2fab7",
    "signing/chromium_config.py": "68a3d6930fafcb00d1a10c33cb16592b4d197ae8893feb402a5ff88a41e38741",
    "signing/commands.py": "49598e2716b55cf51692f40bee99df24a0ea244a8eee3af73eda6b8cfca1aa41",
    "signing/config.py": "39f11e7874a60e648acb0d1cebc5db2d2838bc2b81e34fb0a2be1c4202106e7f",
    "signing/config_factory.py": "badd7c68fd7fa3cffbf2c8cfc91c226097100acddc5f1a1df0a0faad5a1d0c30",
    "signing/driver.py": "897d39ff5b069b63536342522a882beeecd42e9cb0fabdc3e90d9340b7794ddd",
    "signing/invoker.py": "b585435ea8f89683509c88e1386b9c036d7537bdd7a5e07f3803c665a21eb81b",
    "signing/model.py": "74ba82eadaa28c25a34a60fc923dc71acb8f3ce0d3bd091d95f9a68de4f82a9b",
    "signing/modification.py": "b1d696a20e9ea1cfa0b8e8e3139c5b6595bdf1fe10c55aeee303af1da4faa790",
    "signing/notarize.py": "fabfbb2e81224406d321f2d819f03764cc9329018c38e8fde2c3131b37dd80af",
    "signing/parts.py": "5822b9f15388158b79cbbb7325ae080c5a7cf99ec41de4ef210a741e836ec177",
    "signing/pipeline.py": "4e6976652c94ae6bf11f39554222f2f9e4698b1c05e85c8287c86395b0d8fe72",
    "signing/rebranding.py": "3c6eee7135d2ae4f20f36e7a9a99066e9885894035dd8127930a2c7996604359",
    "signing/signing.py": "e7500a429bb7a146382adb479dfde8c26c47a16b8b597517e369899a5422861c",
    "signing/standard_invoker.py": "9c419fd5b4419792497fec288b31ea729010680ae1146cfc8464e479175d0466",
}
SIGNING_MODULE_PATHS = {
    "signing" if relative == "signing/__init__.py" else "signing." + Path(relative).stem: relative
    for relative in PINNED_SIGNING_PYTHON_SHA256
}
SIGNING_MODULE_PATHS["signing.build_props_config"] = (
    "signing/build_props_config.py"
)
SIGNING_SNAPSHOT_DIRECTORY = ".focus-signing-snapshot"
SIGNING_SNAPSHOT_MANIFEST = "signing-module-manifest.json"
DESCRIPTOR_BOUND_WRAPPER = "<descriptor-bound-adhoc-release-wrapper>"
SIGNING_WRAPPER_BOOTSTRAP = """import hashlib
import os
import sys

def snapshot(value):
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
        value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
        getattr(value, 'st_flags', 0),
    )

descriptor = int(sys.argv[1])
expected_size = int(sys.argv[2])
expected_sha256 = sys.argv[3]
before = os.fstat(descriptor)
chunks = []
offset = 0
while offset < expected_size:
    block = os.pread(descriptor, min(65536, expected_size - offset), offset)
    if not block:
        raise SystemExit('descriptor-bound signing wrapper ended early')
    chunks.append(block)
    offset += len(block)
payload = b''.join(chunks)
if os.pread(descriptor, 1, expected_size):
    raise SystemExit('descriptor-bound signing wrapper grew')
if hashlib.sha256(payload).hexdigest() != expected_sha256:
    raise SystemExit('descriptor-bound signing wrapper digest mismatch')
if snapshot(before) != snapshot(os.fstat(descriptor)):
    raise SystemExit('descriptor-bound signing wrapper changed while reading')
origin = '<verified-signing-wrapper-memory>'
namespace = {'__name__': 'focus_adhoc_release_sign_snapshot', '__file__': origin}
exec(compile(payload, origin, 'exec', dont_inherit=True), namespace)
namespace['main'](sys.argv[4:])
"""
SIGNING_EXECUTION_CONTRACT = {
    "wrapper_via_inherited_read_descriptor": True,
    "wrapper_verified_and_loaded_from_memory": True,
    "preloaded_signing_modules_rejected": True,
    "signing_modules_verified_before_import": True,
    "signing_modules_loaded_from_verified_memory": True,
    "unlisted_signing_modules_forbidden": True,
}
PACKAGING_FILES = frozenset(
    {
        "app-entitlements.plist",
        "dmg_tool",
        "helper-gpu-entitlements.plist",
        "helper-renderer-entitlements.plist",
        "hfs_tool",
        "pkg-dmg",
        "pkg_postinstall.in",
        "pkg_preinstall.in",
        "rebrand_chrome.py",
        "sign_chrome.py",
        "universalizer.py",
        "signing/build_props_config.py",
        *PINNED_SIGNING_PYTHON_SHA256,
    }
)
PACKAGING_DIRECTORIES = frozenset((".", "signing"))
PACKAGING_EXECUTABLES = frozenset(
    (
        "dmg_tool",
        "hfs_tool",
        "pkg-dmg",
        "rebrand_chrome.py",
        "sign_chrome.py",
        "universalizer.py",
    )
)
PINNED_PACKAGING_SOURCE_SHA256 = {
    "helper-gpu-entitlements.plist": "958648f799e436860b51eaf55ec8f92d2c62da17001e23d96bc05ffc748f2a2a",
    "helper-renderer-entitlements.plist": "958648f799e436860b51eaf55ec8f92d2c62da17001e23d96bc05ffc748f2a2a",
    "pkg-dmg": "d5bd7b756047a1929b9bfaf6cda7b0b5fdf1b4292ea16484b30601700afa7598",
    "pkg_postinstall.in": "2823fd60d6d3e3bfe2ccf3f113da04d68203065576efe20c5de3833ac6ff614b",
    "pkg_preinstall.in": "8abab6417f08aee2e9096c56de0895106dd83a53a31888edf21b771d68179fdd",
    "rebrand_chrome.py": "85c6ff3447d815c7e7204308409d7ce19fd8a4ef166a2c495eeea97de365725e",
    "sign_chrome.py": PINNED_SIGN_CHROME_SHA256,
    "universalizer.py": "c514adedd2dbd04532d3ddd95ded3ec1bd129ba81570b1f68ddad2a21bed7ab0",
}
PACKAGING_SOURCE_PATHS = {
    "helper-gpu-entitlements.plist": "chrome/app/helper-gpu-entitlements.plist",
    "helper-renderer-entitlements.plist": "chrome/app/helper-renderer-entitlements.plist",
    "pkg-dmg": "chrome/installer/mac/pkg-dmg",
    "pkg_postinstall.in": "chrome/installer/mac/pkg_postinstall.in",
    "pkg_preinstall.in": "chrome/installer/mac/pkg_preinstall.in",
    "rebrand_chrome.py": "chrome/installer/mac/rebrand_chrome.py",
    "sign_chrome.py": "chrome/installer/mac/sign_chrome.py",
    "universalizer.py": "chrome/installer/mac/universalizer.py",
}

RECEIPT_KEYS = {
    "prepare-auto": {
        "schema",
        "stage",
        "update_mode",
        "source_root",
        "base_preparation",
        "base_acquisition",
        "base_tool_bootstrap",
        "compatibility_receipts",
        "repository_contract",
        "auto_patch_application",
        "direct_source",
        "profiles",
        "publication",
        "notarization",
        "developer_id",
    },
    "seal": {
        "schema",
        "stage",
        "update_mode",
        "source_root",
        "auto_preparation_addendum_sha256",
        "source_provenance",
        "profiles",
        "args",
        "apps",
        "app_trees",
        "signing_packaging",
        "packaging_python",
        "ninja",
        "no_work",
        "build_state",
        "publication",
        "notarization",
        "developer_id",
    },
    "stage": {
        "schema",
        "stage",
        "update_mode",
        "source_root",
        "build_seal_sha256",
        "profiles",
        "args",
        "apps",
        "thin_app_contracts",
        "signing_packaging",
        "packaging_python",
        "universalizer_sha256",
        "universalizer_input_order",
        "publication",
        "notarization",
        "developer_id",
    },
    "merge": {
        "schema",
        "stage",
        "update_mode",
        "source_root",
        "stage_receipt_sha256",
        "universalizer_sha256",
        "input_order",
        "app_tree",
        "autoupdate_contract_after_merge",
        "signing_packaging",
        "packaging_python",
        "publication",
        "notarization",
        "developer_id",
    },
    "sign": {
        "schema",
        "stage",
        "update_mode",
        "source_root",
        "merge_receipt_sha256",
        "signing_packaging_before",
        "signing_packaging_after",
        "signing_wrapper_before",
        "signing_wrapper_after",
        "signing_snapshot_plan",
        "signing_snapshot_before",
        "signing_snapshot_after",
        "signing_execution",
        "signing_driver",
        "signing_policy",
        "packaging_python",
        "signing_command",
        "app_tree",
        "autoupdate_contract_after_sign",
        "codesign_deep_strict",
        "identity",
        "publication",
        "notarization",
        "developer_id",
    },
    "accept": {
        "schema",
        "stage",
        "update_mode",
        "source_root",
        "sign_receipt_sha256",
        "sparkle_source_root",
        "packaging_python",
        "app_tree",
        "autoupdate_contract",
        "adhoc_signing_matrix",
        "runtime_acceptance",
        "runtime_timeout_seconds",
        "publication",
        "notarization",
        "developer_id",
    },
}

_ASSIGNMENT = re.compile(
    r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*'
    r'(true|false|-?[0-9]+|"(?:[^"\\]|\\.)*")$'
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AT_FDCWD = -2
_RENAME_EXCL = 0x00000004
_RENAME_NOREPLACE = 0x00000001


class ReleaseError(RuntimeError):
    """Raised when a release-stage invariant is not satisfied."""


class UncertainReleasePublicationError(ReleaseError):
    """A no-replace rename occurred, but its durability is uncertain."""

    def __init__(self, message, output, final_identity, retained_path=None):
        self.output = str(output)
        self.final_identity = tuple(final_identity)
        self.retained_path = str(retained_path or output)
        super().__init__(
            "{}; exact release output retained at {} with identity {}"
            .format(message, self.retained_path, self.final_identity)
        )


class CommittedReleasePublicationError(ReleaseError):
    """A release output is durable, but post-commit processing failed."""

    def __init__(self, message, output, final_identity, retained_path=None):
        self.output = str(output)
        self.final_identity = tuple(final_identity)
        self.retained_path = (
            str(retained_path) if retained_path is not None else None
        )
        state = (
            "exact release output remains committed at {}".format(
                self.retained_path
            )
            if self.retained_path is not None
            else "release output committed at {} but its current pathname state "
            "requires recovery".format(self.output)
        )
        super().__init__(
            "{}; {} with identity {}".format(
                message, state, self.final_identity
            )
        )


class CommittedOutputError(ReleaseError):
    """A verified final DMG is durable but post-commit reporting failed."""

    def __init__(self, message, output, size_bytes, sha256):
        self.output = str(output)
        self.size_bytes = size_bytes
        self.sha256 = sha256
        super().__init__(
            "{}; verified DMG remains committed at {} ({} bytes, sha256={})".format(
                message, self.output, self.size_bytes, self.sha256
            )
        )


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(assignments):
    payload = json.dumps(
        assignments, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    return hashlib.sha256(payload).hexdigest()


def parse_generated_args(path):
    """Parse the strict scalar subset emitted by GN formatting."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("args.gn must be a regular non-symlink file: {}".format(path))
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ReleaseError("args.gn is not UTF-8: {}".format(path)) from exc
    logical = []
    pending = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            raise ReleaseError(
                "inline comments are forbidden in generated args.gn at {}:{}".format(
                    path, number
                )
            )
        if pending is not None:
            logical.append((pending[0], pending[1] + line))
            pending = None
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\s*=", line):
            pending = (number, line)
        else:
            logical.append((number, line))
    if pending is not None:
        raise ReleaseError(
            "unterminated assignment in generated args.gn at {}:{}".format(
                path, pending[0]
            )
        )
    assignments = {}
    for number, line in logical:
        match = _ASSIGNMENT.fullmatch(line)
        if match is None:
            raise ReleaseError(
                "unsupported generated args.gn syntax at {}:{}: {!r}".format(
                    path, number, line
                )
            )
        name, value = match.groups()
        if name in assignments:
            raise ReleaseError(
                "duplicate GN assignment {!r} in {}".format(name, path)
            )
        assignments[name] = value
    if not assignments:
        raise ReleaseError("generated args.gn is empty: {}".format(path))
    return assignments


def canonical_profiles():
    """Bind release inputs to the exact repository Auto GN profiles."""
    report = focus_macos.validate_autoupdate_gn_profiles()
    if report.get("update_mode") != "sparkle":
        raise ReleaseError("focus_macos Auto profile update mode changed")
    common_hash = focus_macos.sha256_file(focus_macos.COMMON_FLAGS)
    if common_hash != PINNED_COMMON_FLAGS_SHA256:
        raise ReleaseError("common Focus GN flags SHA-256 changed")
    result = {
        "update_mode": UPDATE_MODE,
        "feed_url": focus_macos.AUTOUPDATE_FEED_URL,
        "public_key": focus_macos.AUTOUPDATE_PUBLIC_KEY,
        "common_flags": {
            "path": str(focus_macos.COMMON_FLAGS),
            "sha256": common_hash,
        },
        "slices": {},
    }
    for architecture in ("arm64", "x64"):
        profile_path = focus_macos.AUTOUPDATE_MACOS_FLAGS[architecture]
        profile_hash = focus_macos.sha256_file(profile_path)
        if profile_hash != PINNED_AUTO_PROFILE_SHA256[architecture]:
            raise ReleaseError(
                "{} Auto GN profile SHA-256 changed".format(architecture)
            )
        _text, _names, assignments = focus_macos.parse_gn_assignments(
            (focus_macos.COMMON_FLAGS, profile_path),
            expected_target_cpu=architecture,
            include_values=True,
        )
        digest = _canonical_digest(assignments)
        if digest != PINNED_CANONICAL_ASSIGNMENTS_SHA256[architecture]:
            raise ReleaseError(
                "{} canonical Auto GN assignment digest changed".format(
                    architecture
                )
            )
        result["slices"][architecture] = {
            "profile_path": str(profile_path),
            "profile_sha256": profile_hash,
            "canonical_assignments": assignments,
            "canonical_assignments_sha256": digest,
        }
    arm = dict(result["slices"]["arm64"]["canonical_assignments"])
    x64 = dict(result["slices"]["x64"]["canonical_assignments"])
    arm.pop("target_cpu", None)
    x64.pop("target_cpu", None)
    if arm != x64:
        raise ReleaseError("Auto GN slices differ outside target_cpu")
    return result


def validate_args(path, architecture, profiles):
    observed = parse_generated_args(path)
    expected = profiles["slices"][architecture]["canonical_assignments"]
    if observed != expected:
        missing = sorted(set(expected) - set(observed))
        extra = sorted(set(observed) - set(expected))
        changed = sorted(
            name
            for name in set(observed) & set(expected)
            if observed[name] != expected[name]
        )
        raise ReleaseError(
            "{} args.gn differs from canonical Auto profile; missing={}, extra={}, "
            "changed={}".format(architecture, missing, extra, changed)
        )
    return {
        "path": str(Path(path)),
        "sha256": sha256_file(path),
        "canonical_assignments_sha256": _canonical_digest(observed),
        "assignment_count": len(observed),
    }


def _require_real_directory(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ReleaseError("{} must be a real directory: {}".format(label, path))
    return path


def resolve_source_root(value):
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise ReleaseError("Chromium source root must not be a symlink")
    try:
        source = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ReleaseError("Chromium source root does not exist: {}".format(candidate)) from exc
    _require_real_directory(source, "Chromium source root")
    version_path = source / "chrome/VERSION"
    if version_path.is_symlink() or not version_path.is_file():
        raise ReleaseError("missing regular chrome/VERSION")
    values = {}
    for line in version_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        if "=" not in line:
            raise ReleaseError("malformed chrome/VERSION")
        key, value = line.split("=", 1)
        if key in values or not value.isdigit():
            raise ReleaseError("malformed chrome/VERSION field")
        values[key] = value
    expected = {
        "MAJOR": "150",
        "MINOR": "0",
        "BUILD": "7871",
        "PATCH": "128",
        "FOCUS_MAJOR": "1",
        "FOCUS_MINOR": "0",
        "FOCUS_PATCH": "6",
        "FOCUS_PLATFORM": "0",
    }
    if values != expected:
        raise ReleaseError("chrome/VERSION is not exact macOS 1.0.6.0 contract")
    return source


def release_paths(source):
    source = Path(source)
    return {
        "arm_out": source / ARM_OUT,
        "x64_out": source / X64_OUT,
        "arm_app": source / ARM_OUT / APP_NAME,
        "x64_app": source / X64_OUT / APP_NAME,
        "arm_args": source / ARM_OUT / "args.gn",
        "x64_args": source / X64_OUT / "args.gn",
        "x64_packaging": source / X64_OUT / PACKAGING_NAME,
        "staging": source / STAGING_ROOT,
        "staged_arm_app": source / STAGING_ROOT / "arm64" / APP_NAME,
        "staged_x64_app": source / STAGING_ROOT / "x64" / APP_NAME,
        "staged_arm_args": source / STAGING_ROOT / "arm64" / "args.gn",
        "staged_x64_args": source / STAGING_ROOT / "x64" / "args.gn",
        "staged_packaging": source / STAGING_ROOT / "x64" / PACKAGING_NAME,
        "unsigned": source / UNSIGNED_ROOT,
        "unsigned_app": source / UNSIGNED_ROOT / APP_NAME,
        "unsigned_packaging": source / UNSIGNED_ROOT / PACKAGING_NAME,
        "signed": source / SIGNED_ROOT,
        "signed_app": source / SIGNED_ROOT / SIGNED_DISTRIBUTION / APP_NAME,
        "universalizer": source / focus_macos.CHROMIUM_UNIVERSALIZER,
        "build_seal": source / BUILD_SEAL_RECEIPT,
        "auto_preparation_addendum": source / AUTO_PREPARATION_ADDENDUM,
    }


def _safe_environment():
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _run(command, pass_fds=()):
    try:
        stdout, stderr, returncode = runtime_smoke._execute_bounded(
            command,
            COMMAND_TIMEOUT_SECONDS,
            _safe_environment(),
            "release command {}".format(" ".join(command)),
            pass_fds=pass_fds,
        )
    except (OSError, subprocess.SubprocessError, runtime_smoke.RuntimeSmokeError) as exc:
        raise ReleaseError("command failed to start: {!r}: {}".format(command, exc)) from exc
    if returncode:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseError(
            "command failed ({}): {!r}: {}".format(
                returncode, command, detail or "no stderr"
            )
        )
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def _capture(command, stderr_is_output=False):
    try:
        stdout, stderr, returncode = runtime_smoke._execute_bounded(
            command,
            CAPTURE_TIMEOUT_SECONDS,
            _safe_environment(),
            "release inspection {}".format(" ".join(command)),
        )
    except (OSError, subprocess.SubprocessError, runtime_smoke.RuntimeSmokeError) as exc:
        raise ReleaseError("inspection command failed: {!r}: {}".format(command, exc)) from exc
    if returncode:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseError(
            "inspection command failed ({}): {!r}: {}".format(
                returncode, command, detail or "no stderr"
            )
        )
    try:
        payload = stderr if stderr_is_output else stdout
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReleaseError("inspection command emitted non-UTF-8 output") from exc


def _architectures(path):
    output = _capture([LIPO, "-archs", str(path)]).split()
    if not output or len(output) != len(set(output)):
        raise ReleaseError("lipo returned an invalid architecture list for {}".format(path))
    result = frozenset(output)
    if not result <= frozenset(("arm64", "x86_64")):
        raise ReleaseError("unexpected architecture for {}: {}".format(path, sorted(result)))
    return result


def inspect_thin_app(app, architecture):
    expected = "arm64" if architecture == "arm64" else "x86_64"
    try:
        structural = autoupdate_contract.validate_app_bundle(
            app,
            architecture_reader=lambda _path: {"arm64", "x86_64"},
            signature_verifier=lambda _app: True,
        )
    except autoupdate_contract.AutoupdateContractError as exc:
        raise ReleaseError(
            "{} thin app violates the Sparkle structure contract: {}".format(
                architecture, exc
            )
        ) from exc
    inventory = {}
    for label, record in structural["universal_products"].items():
        relative = record["relative_path"]
        path = Path(app) / Path(relative)
        observed = _architectures(path)
        wanted = (
            frozenset(("arm64", "x86_64"))
            if "Contents/Frameworks/Sparkle.framework/" in relative
            else frozenset((expected,))
        )
        if observed != wanted:
            raise ReleaseError(
                "{} slice architecture mismatch at {}: expected {}, got {}".format(
                    architecture, relative, sorted(wanted), sorted(observed)
                )
            )
        inventory[relative] = sorted(observed)
    return {
        "app": str(Path(app)),
        "architecture": expected,
        "products": inventory,
        "structure_passed": True,
    }


def _focus_framework_binary(app):
    """Resolve the real Focus framework executable without following it by name.

    Chromium names the real framework version after its full Chromium version
    (for example ``150.0.7871.128``); ``Versions/Current`` is only the public
    selector.  Release inspection must operate on the real executable rather
    than the framework's symlink facade.
    """
    executable_name = "Focus Browser Framework"
    framework = (
        Path(app)
        / "Contents/Frameworks/Focus Browser Framework.framework"
    )
    versions = framework / "Versions"
    framework_fd = None
    versions_fd = None
    version_fd = None
    executable_fd = None
    current_executable_fd = None
    try:
        framework_fd = os.open(str(framework), _directory_open_flags())
        framework_named = os.lstat(str(framework))
        framework_opened = os.fstat(framework_fd)
        if (
            not stat.S_ISDIR(framework_named.st_mode)
            or _stat_snapshot(framework_named) != _stat_snapshot(framework_opened)
        ):
            raise ReleaseError(
                "Focus Browser Framework must be a stable real directory"
            )

        versions_named = os.stat(
            "Versions", dir_fd=framework_fd, follow_symlinks=False
        )
        if not stat.S_ISDIR(versions_named.st_mode):
            raise ReleaseError(
                "Focus Browser Framework Versions must be a real directory"
            )
        versions_fd = os.open(
            "Versions", _directory_open_flags(), dir_fd=framework_fd
        )
        versions_opened = os.fstat(versions_fd)
        if _stat_snapshot(versions_named) != _stat_snapshot(versions_opened):
            raise ReleaseError(
                "Focus Browser Framework Versions changed while resolving"
            )

        entries = sorted(os.listdir(versions_fd))
        if len(entries) != len(set(entries)) or "Current" not in entries:
            raise ReleaseError(
                "Focus Browser Framework Versions must contain Current and "
                "exactly one real version"
            )
        version_names = [name for name in entries if name != "Current"]
        if len(version_names) != 1 or len(entries) != 2:
            raise ReleaseError(
                "Focus Browser Framework Versions must contain Current and "
                "exactly one real version"
            )
        version_name = version_names[0]
        if re.fullmatch(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*", version_name) is None:
            raise ReleaseError("unsafe Focus Browser Framework version name")

        current_named = os.stat(
            "Current", dir_fd=versions_fd, follow_symlinks=False
        )
        if not stat.S_ISLNK(current_named.st_mode):
            raise ReleaseError(
                "Focus Browser Framework Versions/Current must be a symlink"
            )
        current_target = os.readlink("Current", dir_fd=versions_fd)
        if current_target != version_name:
            raise ReleaseError(
                "Focus Browser Framework Versions/Current has an unsafe or "
                "unexpected target"
            )

        version_named = os.stat(
            version_name, dir_fd=versions_fd, follow_symlinks=False
        )
        if not stat.S_ISDIR(version_named.st_mode):
            raise ReleaseError(
                "Focus Browser Framework version must be a real directory"
            )
        version_fd = os.open(
            version_name, _directory_open_flags(), dir_fd=versions_fd
        )
        version_opened = os.fstat(version_fd)
        current_resolved = os.stat(
            "Current", dir_fd=versions_fd, follow_symlinks=True
        )
        if (
            _stat_snapshot(version_named) != _stat_snapshot(version_opened)
            or not _same_inode(version_opened, current_resolved)
        ):
            raise ReleaseError(
                "Focus Browser Framework Versions/Current does not select the "
                "real version directory"
            )

        executable_named = os.stat(
            executable_name, dir_fd=version_fd, follow_symlinks=False
        )
        if not stat.S_ISREG(executable_named.st_mode):
            raise ReleaseError(
                "Focus Browser Framework executable must be a real regular file"
            )
        executable_fd = os.open(
            executable_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=version_fd
        )
        executable_opened = os.fstat(executable_fd)
        current_executable_fd = os.open(
            "Current/{}".format(executable_name),
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=versions_fd,
        )
        current_executable_opened = os.fstat(current_executable_fd)
        if (
            _stat_snapshot(executable_named) != _stat_snapshot(executable_opened)
            or not stat.S_ISREG(executable_opened.st_mode)
            or not _same_inode(executable_opened, current_executable_opened)
        ):
            raise ReleaseError(
                "Focus Browser Framework executable changed while resolving"
            )

        if (
            sorted(os.listdir(versions_fd)) != entries
            or os.readlink("Current", dir_fd=versions_fd) != current_target
            or _stat_snapshot(current_named)
            != _stat_snapshot(
                os.stat("Current", dir_fd=versions_fd, follow_symlinks=False)
            )
            or _stat_snapshot(version_opened) != _stat_snapshot(os.fstat(version_fd))
            or _stat_snapshot(executable_opened)
            != _stat_snapshot(os.fstat(executable_fd))
        ):
            raise ReleaseError(
                "Focus Browser Framework layout changed while resolving"
            )
        return versions / version_name / executable_name
    except ReleaseError:
        raise
    except OSError as exc:
        raise ReleaseError(
            "cannot safely resolve the real Focus Browser Framework executable"
        ) from exc
    finally:
        for descriptor in (
            current_executable_fd,
            executable_fd,
            version_fd,
            versions_fd,
            framework_fd,
        ):
            if descriptor is not None:
                os.close(descriptor)


def validate_otool_contract(app):
    framework = _focus_framework_binary(app)
    architectures = {}
    for architecture in ("arm64", "x86_64"):
        dependencies = _capture(
            [OTOOL, "-arch", architecture, "-L", str(framework)]
        ).splitlines()[1:]
        dependency_names = [
            line.strip().split(" ", 1)[0]
            for line in dependencies
            if line.strip()
        ]
        sparkle = [
            name for name in dependency_names if "Sparkle.framework" in name
        ]
        if sparkle != [SPARKLE_DEPENDENCY]:
            raise ReleaseError(
                "Focus framework {} Sparkle dependency mismatch: {}".format(
                    architecture, sparkle
                )
            )
        load_commands = _capture(
            [OTOOL, "-arch", architecture, "-l", str(framework)]
        ).splitlines()
        rpaths = []
        for line in load_commands:
            match = re.match(
                r"^\s*path\s+(\S+)\s+\(offset\s+\d+\)\s*$", line
            )
            if match:
                rpaths.append(match.group(1))
        if rpaths != [FOCUS_FRAMEWORK_RPATH]:
            raise ReleaseError(
                "Focus framework {} rpath mismatch: expected sole {!r}, got {}".format(
                    architecture, FOCUS_FRAMEWORK_RPATH, rpaths
                )
            )
        architectures[architecture] = {
            "sparkle_dependency": SPARKLE_DEPENDENCY,
            "rpath": FOCUS_FRAMEWORK_RPATH,
        }
    return {
        "framework": str(framework),
        "architectures": architectures,
        "sparkle_dependency": SPARKLE_DEPENDENCY,
        "rpath": FOCUS_FRAMEWORK_RPATH,
    }


def validate_universal_app(app, signed, sparkle_source_root=None):
    contract_arguments = {}
    if not signed:
        contract_arguments["signature_verifier"] = lambda _app: True
    try:
        if signed and sparkle_source_root is not None:
            report = autoupdate_contract.validate_release_bundle(
                app,
                sparkle_source_root,
            )
        else:
            if sparkle_source_root is not None:
                contract_arguments["sparkle_source_root"] = sparkle_source_root
            report = autoupdate_contract.validate_app_bundle(
                app,
                **contract_arguments,
            )
    except autoupdate_contract.AutoupdateContractError as exc:
        raise ReleaseError("universal autoupdate contract failed: {}".format(exc)) from exc
    otool = validate_otool_contract(app)
    result = {
        "contract": report,
        "otool": otool,
        "signed": bool(signed),
    }
    try:
        if signed and sparkle_source_root is None:
            result["adhoc_signing"] = (
                autoupdate_contract.validate_adhoc_signing_contract(app)
            )
            result["macho_minimum_system_versions"] = (
                autoupdate_contract.validate_macho_minimum_system_versions(
                    app, report["universal_products"]
                )
            )
        elif not signed:
            result["macho_minimum_system_versions"] = (
                autoupdate_contract.validate_macho_minimum_system_versions(
                    app, report["universal_products"]
                )
            )
    except autoupdate_contract.AutoupdateContractError as exc:
        raise ReleaseError("universal release gate failed: {}".format(exc)) from exc
    if signed:
        _capture([CODESIGN, "--verify", "--deep", "--strict", "--verbose=2", str(app)])
        detail = _capture(
            [CODESIGN, "-dv", "--verbose=4", str(app)],
            stderr_is_output=True,
        )
        if "Signature=adhoc" not in detail.splitlines():
            raise ReleaseError("signed app is not ad-hoc signed")
        if "Developer ID Application:" in detail:
            raise ReleaseError("Developer ID signing is forbidden in this local pipeline")
        result["codesign"] = {
            "deep_strict": True,
            "identity": "adhoc",
            "developer_id": False,
        }
    return result


def _same_inode(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _stat_snapshot(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        getattr(value, "st_flags", 0),
    )


def _directory_open_flags():
    missing = [name for name in ("O_DIRECTORY", "O_NOFOLLOW") if not hasattr(os, name)]
    if missing:
        raise ReleaseError(
            "safe tree inspection requires {}".format(", ".join(missing))
        )
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _sha256_fd(descriptor):
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def _xattr_report(path=None, descriptor=None, follow_symlinks=False):
    if hasattr(os, "listxattr") and hasattr(os, "getxattr"):
        try:
            target = descriptor if descriptor is not None else str(path)
            arguments = {} if descriptor is not None else {
                "follow_symlinks": follow_symlinks
            }
            names = sorted(os.listxattr(target, **arguments))
            result = {}
            for name in names:
                value = os.getxattr(target, name, **arguments)
                result[name] = {
                    "size": len(value),
                    "sha256": hashlib.sha256(value).hexdigest(),
                }
            return result
        except OSError as exc:
            raise ReleaseError("cannot inspect filesystem metadata xattrs") from exc
    if sys.platform != "darwin":
        raise ReleaseError("filesystem xattr inspection is unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    nofollow = 0 if follow_symlinks else 0x0001

    def call_size(function, prefix, suffix):
        for _attempt in range(3):
            ctypes.set_errno(0)
            size = function(*(prefix + (None, 0) + suffix))
            if size < 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            if size == 0:
                return b""
            buffer = ctypes.create_string_buffer(size)
            ctypes.set_errno(0)
            observed = function(*(prefix + (buffer, size) + suffix))
            if observed >= 0:
                return bytes(buffer.raw[:observed])
            error = ctypes.get_errno()
            if error != errno.ERANGE:
                raise OSError(error, os.strerror(error))
        raise OSError(errno.ERANGE, "xattr changed repeatedly")

    try:
        if descriptor is not None:
            list_function = libc.flistxattr
            list_function.argtypes = [
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_size_t,
                ctypes.c_int,
            ]
            list_function.restype = ctypes.c_ssize_t
            raw_names = call_size(list_function, (descriptor,), (0,))
        else:
            list_function = libc.listxattr
            list_function.argtypes = [
                ctypes.c_char_p,
                ctypes.c_void_p,
                ctypes.c_size_t,
                ctypes.c_int,
            ]
            list_function.restype = ctypes.c_ssize_t
            raw_names = call_size(
                list_function, (os.fsencode(path),), (nofollow,)
            )
        names = [
            value.decode("utf-8", errors="strict")
            for value in raw_names.split(b"\0")
            if value
        ]
    except (AttributeError, OSError, UnicodeError) as exc:
        raise ReleaseError("cannot inspect filesystem metadata xattrs") from exc
    result = {}
    for name in sorted(names):
        try:
            if descriptor is not None:
                get_function = libc.fgetxattr
                get_function.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_uint32,
                    ctypes.c_int,
                ]
                get_function.restype = ctypes.c_ssize_t
                value = call_size(
                    get_function,
                    (descriptor, name.encode("utf-8")),
                    (0, 0),
                )
            else:
                get_function = libc.getxattr
                get_function.argtypes = [
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_uint32,
                    ctypes.c_int,
                ]
                get_function.restype = ctypes.c_ssize_t
                value = call_size(
                    get_function,
                    (os.fsencode(path), name.encode("utf-8")),
                    (0, nofollow),
                )
        except (AttributeError, OSError) as exc:
            raise ReleaseError("filesystem xattr changed during inspection") from exc
        result[name] = {
            "size": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    return result


def _acl_report(path=None, descriptor=None, follow_symlinks=False):
    """Return a stable digest of the macOS extended ACL, including absence."""
    if sys.platform != "darwin":
        raise ReleaseError("extended ACL inspection requires macOS")
    if (path is None) == (descriptor is None):
        raise ReleaseError("ACL inspection requires exactly one target")
    libc = ctypes.CDLL(None, use_errno=True)
    acl_type_extended = 0x100
    try:
        if descriptor is not None:
            getter = libc.acl_get_fd_np
            getter.argtypes = [ctypes.c_int, ctypes.c_int]
            getter.restype = ctypes.c_void_p
            acl = getter(descriptor, acl_type_extended)
        else:
            getter = libc.acl_get_file if follow_symlinks else libc.acl_get_link_np
            getter.argtypes = [ctypes.c_char_p, ctypes.c_int]
            getter.restype = ctypes.c_void_p
            acl = getter(os.fsencode(path), acl_type_extended)
    except AttributeError as exc:
        raise ReleaseError("extended ACL inspection is unavailable") from exc
    if not acl:
        error = ctypes.get_errno()
        if error == errno.ENOENT:
            raw = b""
        else:
            raise ReleaseError(
                "cannot inspect extended ACL: {}".format(os.strerror(error))
            )
    else:
        length = ctypes.c_ssize_t()
        try:
            to_text = libc.acl_to_text
            to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
            to_text.restype = ctypes.c_void_p
            text_pointer = to_text(acl, ctypes.byref(length))
            if not text_pointer or length.value < 0:
                error = ctypes.get_errno()
                raise ReleaseError(
                    "cannot serialize extended ACL: {}".format(
                        os.strerror(error or errno.EIO)
                    )
                )
            try:
                raw = ctypes.string_at(text_pointer, length.value)
            finally:
                libc.acl_free(ctypes.c_void_p(text_pointer))
        finally:
            libc.acl_free(ctypes.c_void_p(acl))
    return {
        "present": bool(raw),
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _safe_tree_metadata(observed, relative):
    mode = stat.S_IMODE(observed.st_mode)
    if observed.st_uid != os.geteuid():
        raise ReleaseError("tree entry is not owned by the current user: {}".format(relative))
    unsafe = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    if not stat.S_ISLNK(observed.st_mode):
        unsafe |= stat.S_IWGRP | stat.S_IWOTH
    if mode & unsafe:
        raise ReleaseError("tree entry has unsafe permissions: {}".format(relative))
    if stat.S_ISREG(observed.st_mode) and observed.st_nlink != 1:
        raise ReleaseError(
            "tree regular file has external hard-link aliases: {}".format(relative)
        )
    return {
        "mode": format(mode, "04o"),
        "uid": observed.st_uid,
        "gid": observed.st_gid,
        "flags": getattr(observed, "st_flags", 0),
        "nlink": observed.st_nlink,
    }


def _symlink_target_is_internal(relative, target):
    if not target or os.path.isabs(target) or "\x00" in target:
        return False
    parent = Path(relative).parent.as_posix()
    normalized = os.path.normpath(os.path.join(parent, target))
    return normalized not in ("..", ".") and not normalized.startswith("../")


def _tree_contract(root):
    """Hash an exact descriptor-pinned tree, including its root metadata."""
    root = _require_real_directory(root, "tree root")
    root_fd = os.open(str(root), _directory_open_flags())
    entries = []
    try:
        root_named = os.lstat(str(root))
        root_pinned = os.fstat(root_fd)
        if _stat_snapshot(root_named) != _stat_snapshot(root_pinned):
            raise ReleaseError("tree root changed during inspection")

        def walk(directory_fd, relative_directory):
            directory_stat = os.fstat(directory_fd)
            relative = relative_directory or "."
            metadata = _safe_tree_metadata(directory_stat, relative)
            entries.append(
                {
                    "kind": "directory",
                    "path": relative,
                    **metadata,
                    "xattrs": _xattr_report(descriptor=directory_fd),
                    "acl": _acl_report(descriptor=directory_fd),
                }
            )
            try:
                names = sorted(os.listdir(directory_fd))
            except OSError as exc:
                raise ReleaseError("cannot enumerate tree directory: {}".format(relative)) from exc
            for name in names:
                if name in ("", ".", "..") or "/" in name or "\x00" in name:
                    raise ReleaseError("unsafe tree entry name")
                child_relative = name if not relative_directory else relative_directory + "/" + name
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                metadata = _safe_tree_metadata(before, child_relative)
                if stat.S_ISDIR(before.st_mode):
                    child_fd = os.open(
                        name, _directory_open_flags(), dir_fd=directory_fd
                    )
                    try:
                        if _stat_snapshot(before) != _stat_snapshot(os.fstat(child_fd)):
                            raise ReleaseError(
                                "tree directory changed during inspection: {}".format(child_relative)
                            )
                        walk(child_fd, child_relative)
                        after = os.stat(
                            name, dir_fd=directory_fd, follow_symlinks=False
                        )
                        if _stat_snapshot(before) != _stat_snapshot(after):
                            raise ReleaseError(
                                "tree directory changed during inspection: {}".format(child_relative)
                            )
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(before.st_mode):
                    child_fd = os.open(
                        name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
                    )
                    try:
                        pinned = os.fstat(child_fd)
                        if not _same_inode(before, pinned):
                            raise ReleaseError(
                                "tree file changed during inspection: {}".format(child_relative)
                            )
                        entry = {
                            "kind": "file",
                            "path": child_relative,
                            **metadata,
                            "size": pinned.st_size,
                            "sha256": _sha256_fd(child_fd),
                            "xattrs": _xattr_report(descriptor=child_fd),
                            "acl": _acl_report(descriptor=child_fd),
                        }
                        after_pinned = os.fstat(child_fd)
                        after_named = os.stat(
                            name, dir_fd=directory_fd, follow_symlinks=False
                        )
                        if (
                            _stat_snapshot(pinned) != _stat_snapshot(after_pinned)
                            or _stat_snapshot(pinned) != _stat_snapshot(after_named)
                        ):
                            raise ReleaseError(
                                "tree file changed during inspection: {}".format(child_relative)
                            )
                        entries.append(entry)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISLNK(before.st_mode):
                    target = os.readlink(name, dir_fd=directory_fd)
                    if not _symlink_target_is_internal(child_relative, target):
                        raise ReleaseError(
                            "tree symlink escapes root: {}".format(child_relative)
                        )
                    path = root / child_relative
                    entry = {
                        "kind": "symlink",
                        "path": child_relative,
                        **metadata,
                        "target": target,
                        "xattrs": _xattr_report(path=path, follow_symlinks=False),
                        "acl": _acl_report(path=path, follow_symlinks=False),
                    }
                    after = os.stat(
                        name, dir_fd=directory_fd, follow_symlinks=False
                    )
                    if _stat_snapshot(before) != _stat_snapshot(after) or os.readlink(
                        name, dir_fd=directory_fd
                    ) != target:
                        raise ReleaseError(
                            "tree symlink changed during inspection: {}".format(child_relative)
                        )
                    entries.append(entry)
                else:
                    raise ReleaseError(
                        "unsupported filesystem entry in tree: {}".format(child_relative)
                    )

        walk(root_fd, "")
        final_pinned = os.fstat(root_fd)
        final_named = os.lstat(str(root))
        if (
            _stat_snapshot(root_pinned) != _stat_snapshot(final_pinned)
            or _stat_snapshot(root_pinned) != _stat_snapshot(final_named)
        ):
            raise ReleaseError("tree root changed during inspection")
    finally:
        os.close(root_fd)
    entries.sort(key=lambda item: item["path"])
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    path_payload = "\n".join(item["path"] for item in entries).encode("utf-8") + b"\n"
    xattr_payload = json.dumps(
        {item["path"]: item["xattrs"] for item in entries},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    acl_payload = json.dumps(
        {item["path"]: item["acl"] for item in entries},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    root_entry = next(item for item in entries if item["path"] == ".")
    return {
        "tree_sha256": hashlib.sha256(payload).hexdigest(),
        "paths_sha256": hashlib.sha256(path_payload).hexdigest(),
        "xattrs_sha256": hashlib.sha256(xattr_payload).hexdigest(),
        "acls_sha256": hashlib.sha256(acl_payload).hexdigest(),
        "entry_count": len(entries),
        "root_mode": root_entry["mode"],
        "owner_uid": root_entry["uid"],
    }


def _tree_sha256(root):
    return _tree_contract(root)["tree_sha256"]


def _require_tools():
    for tool in SYSTEM_TOOLS:
        if not Path(tool).is_file() or not os.access(tool, os.X_OK):
            raise ReleaseError("required system tool is unavailable: {}".format(tool))


def _driver_contract(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("missing generated x64 signing driver: {}".format(path))
    digest = sha256_file(path)
    if digest != PINNED_SIGN_CHROME_SHA256:
        raise ReleaseError("generated x64 sign_chrome.py SHA-256 changed")
    return {"path": str(path), "sha256": digest, "origin": X64_OUT}


def _adhoc_signing_wrapper_contract():
    path = MACOS_DIR / "adhoc_release_sign.py"
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("missing trusted ad-hoc release signing wrapper")
    record = _regular_file_record(path, 0o644)
    if record["sha256"] != PINNED_ADHOC_RELEASE_SIGN_SHA256:
        raise ReleaseError("ad-hoc release signing wrapper SHA-256 changed")
    return {
        "path": str(path.resolve()),
        "sha256": record["sha256"],
        "size": record["size"],
        "mode": record["mode"],
    }


def _signing_snapshot_manifest(packaging):
    files = packaging.get("files") if isinstance(packaging, dict) else None
    if not isinstance(files, dict):
        raise ReleaseError("signing packaging omitted its file inventory")
    modules = {}
    for module_name, relative in sorted(SIGNING_MODULE_PATHS.items()):
        record = files.get(relative)
        if not isinstance(record, dict):
            raise ReleaseError(
                "signing packaging omitted module: {}".format(relative)
            )
        digest = record.get("sha256")
        size = record.get("size")
        if (
            not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
            or type(size) is not int
            or size <= 0
        ):
            raise ReleaseError("signing packaging module record is invalid")
        modules[module_name] = {
            "path": relative,
            "sha256": digest,
            "size": size,
        }
    return {"schema": 1, "modules": modules}


def _canonical_signing_snapshot_manifest(manifest):
    return (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _signing_snapshot_plan(part, wrapper, packaging):
    root = Path(part) / SIGNING_SNAPSHOT_DIRECTORY
    manifest = _signing_snapshot_manifest(packaging)
    manifest_payload = _canonical_signing_snapshot_manifest(manifest)
    return {
        "schema": 1,
        "root": str(root),
        "wrapper": {
            "path": "adhoc_release_sign.py",
            "sha256": wrapper["sha256"],
            "size": wrapper["size"],
            "mode": "0400",
        },
        "manifest": {
            "path": SIGNING_SNAPSHOT_MANIFEST,
            "sha256": hashlib.sha256(manifest_payload).hexdigest(),
            "size": len(manifest_payload),
            "mode": "0400",
        },
        "modules": {
            module_name: {
                **record,
                "mode": "0400",
            }
            for module_name, record in manifest["modules"].items()
        },
        "execution": dict(SIGNING_EXECUTION_CONTRACT),
    }


def _copy_signing_snapshot_file(source, destination, expected):
    source = Path(source)
    destination = Path(destination)
    source_fd = os.open(str(source), os.O_RDONLY | os.O_NOFOLLOW)
    destination_fd = None
    try:
        opened = os.fstat(source_fd)
        named = os.lstat(str(source))
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _same_inode(opened, named)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or opened.st_size != expected["size"]
            or _sha256_fd(source_fd) != expected["sha256"]
        ):
            raise ReleaseError(
                "signing snapshot source changed: {}".format(source)
            )
        destination_fd = os.open(
            str(destination),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        offset = 0
        while offset < opened.st_size:
            block = os.pread(source_fd, min(1024 * 1024, opened.st_size - offset), offset)
            if not block:
                raise ReleaseError("signing snapshot source ended early")
            written = 0
            while written < len(block):
                count = os.write(destination_fd, block[written:])
                if count <= 0:
                    raise ReleaseError("signing snapshot write was short")
                written += count
            offset += len(block)
        os.fchmod(destination_fd, 0o400)
        os.fsync(destination_fd)
        if (
            _stat_snapshot(opened) != _stat_snapshot(os.fstat(source_fd))
            or _stat_snapshot(opened) != _stat_snapshot(os.lstat(str(source)))
            or _sha256_fd(source_fd) != expected["sha256"]
        ):
            raise ReleaseError("signing snapshot source changed while copying")
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)
    record = _regular_file_record(destination, 0o400)
    if (
        record["sha256"] != expected["sha256"]
        or record["size"] != expected["size"]
        or record["acl"]["present"]
        or record["flags"] != 0
    ):
        raise ReleaseError("signing snapshot destination contract mismatch")


def _write_signing_snapshot_manifest(path, payload, expected):
    descriptor = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise ReleaseError("signing snapshot manifest write was short")
            written += count
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    record = _regular_file_record(path, 0o400)
    if (
        record["sha256"] != expected["sha256"]
        or record["size"] != expected["size"]
        or record["acl"]["present"]
        or record["flags"] != 0
    ):
        raise ReleaseError("signing snapshot manifest contract mismatch")


def _require_signing_snapshot_directory(path, expected_mode):
    path = Path(path)
    descriptor = os.open(str(path), _directory_open_flags())
    try:
        opened = os.fstat(descriptor)
        named = os.lstat(str(path))
        if (
            not _same_inode(opened, named)
            or stat.S_IMODE(opened.st_mode) != expected_mode
            or opened.st_uid != os.geteuid()
            or getattr(opened, "st_flags", 0) != 0
            or _acl_report(descriptor=descriptor)["present"]
        ):
            raise ReleaseError("signing snapshot directory contract mismatch")
        return opened.st_dev, opened.st_ino
    finally:
        os.close(descriptor)


def _signing_snapshot_contract(plan):
    root = Path(plan["root"])
    _require_signing_snapshot_directory(root, 0o500)
    _require_signing_snapshot_directory(root / "signing", 0o500)
    if set(child.name for child in root.iterdir()) != {
        "adhoc_release_sign.py",
        SIGNING_SNAPSHOT_MANIFEST,
        "signing",
    }:
        raise ReleaseError("signing snapshot root inventory mismatch")
    expected_module_files = {
        Path(record["path"]).name for record in plan["modules"].values()
    }
    if set(child.name for child in (root / "signing").iterdir()) != expected_module_files:
        raise ReleaseError("signing snapshot module inventory mismatch")
    records = [plan["wrapper"], plan["manifest"], *plan["modules"].values()]
    for record in records:
        observed = _regular_file_record(root / record["path"], 0o400)
        if (
            observed["sha256"] != record["sha256"]
            or observed["size"] != record["size"]
            or observed["acl"]["present"]
            or observed["flags"] != 0
        ):
            raise ReleaseError("signing snapshot file contract mismatch")
    return {**plan, "tree": _tree_contract(root)}


def _validate_signing_snapshot_report(report, plan):
    if not isinstance(report, dict) or set(report) != set(plan) | {"tree"}:
        raise ReleaseError("signing snapshot receipt schema mismatch")
    if {key: value for key, value in report.items() if key != "tree"} != plan:
        raise ReleaseError("signing snapshot receipt content mismatch")
    tree = report.get("tree")
    if (
        not isinstance(tree, dict)
        or set(tree)
        != {
            "tree_sha256",
            "paths_sha256",
            "xattrs_sha256",
            "acls_sha256",
            "entry_count",
            "root_mode",
            "owner_uid",
        }
        or tree.get("entry_count") != len(plan["modules"]) + 4
        or tree.get("root_mode") != "0500"
        or tree.get("owner_uid") != os.geteuid()
        or any(
            not isinstance(tree.get(name), str)
            or not _SHA256.fullmatch(tree[name])
            for name in (
                "tree_sha256",
                "paths_sha256",
                "xattrs_sha256",
                "acls_sha256",
            )
        )
    ):
        raise ReleaseError("signing snapshot receipt tree mismatch")


def _create_signing_snapshot(part, wrapper, packaging, plan):
    expected = _signing_snapshot_plan(part, wrapper, packaging)
    if plan != expected:
        raise ReleaseError("signing snapshot inputs changed before creation")
    root = Path(plan["root"])
    _ensure_absent(root, "signing snapshot")
    root.mkdir(mode=0o700)
    signing = root / "signing"
    signing.mkdir(mode=0o700)
    try:
        _copy_signing_snapshot_file(
            wrapper["path"], root / plan["wrapper"]["path"], plan["wrapper"]
        )
        packaging_root = Path(packaging["path"])
        for module_name, record in sorted(plan["modules"].items()):
            del module_name
            _copy_signing_snapshot_file(
                packaging_root / record["path"], root / record["path"], record
            )
        manifest_payload = _canonical_signing_snapshot_manifest(
            _signing_snapshot_manifest(packaging)
        )
        _write_signing_snapshot_manifest(
            root / SIGNING_SNAPSHOT_MANIFEST,
            manifest_payload,
            plan["manifest"],
        )
        _fsync_directory(signing)
        os.chmod(str(signing), 0o500)
        _fsync_directory(root)
        os.chmod(str(root), 0o500)
        _fsync_directory(Path(part))
        return _signing_snapshot_contract(plan)
    except BaseException:
        try:
            if signing.exists() and not signing.is_symlink():
                os.chmod(str(signing), 0o700)
            if root.exists() and not root.is_symlink():
                os.chmod(str(root), 0o700)
        except OSError:
            pass
        raise


def _open_signing_snapshot_wrapper(plan):
    path = Path(plan["root"]) / plan["wrapper"]["path"]
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    opened = os.fstat(descriptor)
    named = os.lstat(str(path))
    if (
        not _same_inode(opened, named)
        or stat.S_IMODE(opened.st_mode) != 0o400
        or opened.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or opened.st_size != plan["wrapper"]["size"]
        or _sha256_fd(descriptor) != plan["wrapper"]["sha256"]
    ):
        os.close(descriptor)
        raise ReleaseError("descriptor-bound signing wrapper mismatch")
    return descriptor


def _remove_signing_snapshot(plan):
    root = Path(plan["root"])
    root_identity = _require_signing_snapshot_directory(root, 0o500)
    signing = root / "signing"
    _require_signing_snapshot_directory(signing, 0o500)
    os.chmod(str(signing), 0o700)
    os.chmod(str(root), 0o700)
    observed = os.lstat(str(root))
    if (observed.st_dev, observed.st_ino) != root_identity:
        raise ReleaseError("signing snapshot changed before cleanup")
    shutil.rmtree(str(root))
    _fsync_directory(root.parent)


def _adhoc_signing_policy():
    return {
        "identity": "-",
        "development": False,
        "provisioning_profile": False,
        "notarization": "none",
        "chromium_packaging": False,
        "run_spctl_assess": False,
        "inject_get_task_allow_entitlement": False,
    }


def _package_driver_contract():
    """Bind every repository module imported by the DMG subprocess."""
    report = {}
    for name in (
        "package_local_dmg.py",
        "autoupdate_contract.py",
        "focus_macos.py",
    ):
        path = MACOS_DIR / name
        if path.is_symlink() or not path.is_file():
            raise ReleaseError("missing trusted DMG driver module: {}".format(name))
        mode = stat.S_IMODE(os.lstat(str(path)).st_mode)
        report[name] = _regular_file_record(path, mode)
    return {
        "entrypoint": str(Path(package_local_dmg.__file__).resolve()),
        "modules": report,
    }


def _strict_json_object(payload, label):
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ReleaseError("{} contains a duplicate JSON key".format(label))
            value[key] = item
        return value

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
        )
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ReleaseError("{} is not one strict UTF-8 JSON object".format(label)) from exc
    if not isinstance(value, dict):
        raise ReleaseError("{} JSON root must be an object".format(label))
    return value


def _pinned_packaging_python(source):
    """Authenticate the full CIPD runtime and run it in isolated source mode."""
    source = Path(source)
    machine = "arm64"
    depot = source.parent / "depot_tools"
    wrapper = source.parent / build_pipeline.PACKAGING_PYTHON_WRAPPER_RELATIVE
    marker = depot / "python3_bin_reldir.txt"
    if (
        wrapper.is_symlink()
        or not wrapper.is_file()
        or not os.access(str(wrapper), os.X_OK)
        or sha256_file(wrapper)
        != build_pipeline.PACKAGING_PYTHON_WRAPPER_SHA256
    ):
        raise ReleaseError("packaging Python wrapper contract mismatch")
    if (
        marker.is_symlink()
        or not marker.is_file()
        or sha256_file(marker)
        != build_pipeline.PACKAGING_PYTHON_RELDIR_SHA256
        or marker.read_text(encoding="utf-8")
        != build_pipeline.PACKAGING_PYTHON_RELDIR
    ):
        raise ReleaseError("packaging Python relative-path marker mismatch")
    python_bin = depot / build_pipeline.PACKAGING_PYTHON_RELDIR
    install_root = python_bin.parent
    bootstrap_root = install_root.parent
    executable = python_bin / "python3.11"
    instance = build_pipeline.PACKAGING_PYTHON_CIPD_INSTANCE_BY_HOST[machine]
    cipd_slot = bootstrap_root / ".cipd/pkgs/0"
    instance_dir = cipd_slot / instance
    manifest_path = instance_dir / ".cipdpkg/manifest.json"
    for path, label in (
        (install_root, "packaging Python install root"),
        (bootstrap_root, "packaging Python bootstrap root"),
        (instance_dir, "packaging Python CIPD instance"),
    ):
        _require_real_directory(path, label)
    if (
        executable.is_symlink()
        or not executable.is_file()
        or not os.access(str(executable), os.X_OK)
        or sha256_file(executable)
        != build_pipeline.PACKAGING_PYTHON_SHA256_BY_HOST[machine]
    ):
        raise ReleaseError("packaging Python executable contract mismatch")
    description_path = cipd_slot / "description.json"
    try:
        description = json.loads(
            description_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("packaging Python CIPD description is invalid") from exc
    package_name = "infra/3pp/tools/cpython3/mac-arm64"
    if description != {"subdir": "python3", "package_name": package_name}:
        raise ReleaseError("packaging Python CIPD description mismatch")
    current = cipd_slot / "_current"
    if not current.is_symlink() or os.readlink(str(current)) != instance:
        raise ReleaseError("packaging Python CIPD current instance mismatch")
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or sha256_file(manifest_path) != PINNED_PYTHON_MANIFEST_SHA256
    ):
        raise ReleaseError("packaging Python CIPD manifest hash mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("packaging Python CIPD manifest is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {
            "format_version",
            "package_name",
            "version_file",
            "install_mode",
            "actual_install_mode",
            "files",
        }
        or manifest.get("format_version") != "1.1"
        or manifest.get("package_name") != package_name
        or manifest.get("version_file") != ".versions/cpython3.cipd_version"
        or manifest.get("install_mode") != "copy"
        or manifest.get("actual_install_mode") != "copy"
        or not isinstance(manifest.get("files"), list)
    ):
        raise ReleaseError("packaging Python CIPD manifest schema mismatch")
    expected_paths = set()
    expected_directories = {"."}
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) not in (
            {"name", "size", "hash"},
            {"name", "size", "hash", "executable"},
            {"name", "size", "symlink"},
        ):
            raise ReleaseError("packaging Python manifest entry schema mismatch")
        relative = item.get("name")
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or "\\" in relative
            or "\x00" in relative
            or any(part in ("", ".", "..") for part in relative.split("/"))
            or relative in expected_paths
        ):
            raise ReleaseError("packaging Python manifest path is unsafe")
        expected_paths.add(relative)
        parent = Path(relative).parent
        while parent.as_posix() not in (".", ""):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
        path = install_root / relative
        observed = os.lstat(str(path))
        if observed.st_uid != os.geteuid() or observed.st_nlink != 1:
            raise ReleaseError("packaging Python file ownership/link mismatch")
        if "symlink" in item:
            target = item.get("symlink")
            if (
                item.get("size") != 0
                or not stat.S_ISLNK(observed.st_mode)
                or os.readlink(str(path)) != target
                or not _symlink_target_is_internal(relative, target)
            ):
                raise ReleaseError("packaging Python symlink contract mismatch")
            continue
        if not stat.S_ISREG(observed.st_mode) or stat.S_ISLNK(observed.st_mode):
            raise ReleaseError("packaging Python manifest file is not regular")
        mode = stat.S_IMODE(observed.st_mode)
        expected_executable = item.get("executable") is True
        if (
            type(item.get("size")) is not int
            or observed.st_size != item["size"]
            or bool(mode & 0o111) != expected_executable
            or mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID)
        ):
            raise ReleaseError("packaging Python manifest metadata mismatch")
        try:
            encoded_digest = base64.urlsafe_b64decode(
                item["hash"] + "=" * (-len(item["hash"]) % 4)
            )
        except (ValueError, TypeError) as exc:
            raise ReleaseError("packaging Python manifest hash is invalid") from exc
        if len(encoded_digest) != 33 or encoded_digest[-1] != 2:
            raise ReleaseError("packaging Python manifest hash algorithm mismatch")
        expected_digest = encoded_digest[:-1].hex()
        if sha256_file(path) != expected_digest:
            raise ReleaseError("packaging Python manifest content mismatch")
    observed_paths = set()
    non_manifest_cache = []
    for current_root, directories, files in os.walk(
        str(install_root), topdown=True, followlinks=False
    ):
        directories.sort()
        files.sort()
        root_path = Path(current_root)
        relative_root = root_path.relative_to(install_root).as_posix()
        relative_root = "." if relative_root == "." else relative_root
        if (
            relative_root not in expected_directories
            and not re.search(r"(?:^|/)__pycache__$", relative_root)
        ):
            raise ReleaseError(
                "untrusted non-manifest packaging Python directory: {}".format(
                    relative_root
                )
            )
        for name in directories:
            path = root_path / name
            if path.is_symlink():
                files.append(name)
        for name in files:
            path = root_path / name
            relative = path.relative_to(install_root).as_posix()
            observed_paths.add(relative)
            if relative in expected_paths:
                continue
            observed = os.lstat(str(path))
            if (
                not re.search(r"(?:^|/)__pycache__/[^/]+\.pyc$", relative)
                or not stat.S_ISREG(observed.st_mode)
                or stat.S_ISLNK(observed.st_mode)
                or observed.st_uid != os.geteuid()
                or observed.st_nlink != 1
                or stat.S_IMODE(observed.st_mode) & 0o111
            ):
                raise ReleaseError(
                    "untrusted non-manifest packaging Python path: {}".format(
                        relative
                    )
                )
            non_manifest_cache.append(
                {
                    "path": relative,
                    "size": observed.st_size,
                    "sha256": sha256_file(path),
                }
            )
    if expected_paths - observed_paths:
        raise ReleaseError("packaging Python manifest inventory is incomplete")
    probe_script = (
        "import asyncio,json,platform,site,sys;"
        "print(json.dumps({'machine':platform.machine().lower(),"
        "'task_group':hasattr(asyncio,'TaskGroup'),"
        "'version':list(sys.version_info[:3]),"
        "'isolated':sys.flags.isolated,'no_user_site':sys.flags.no_user_site,"
        "'dont_write_bytecode':sys.flags.dont_write_bytecode,"
        "'enable_user_site':site.ENABLE_USER_SITE,"
        "'pycache_prefix':sys.pycache_prefix},sort_keys=True))"
    )
    try:
        identity = json.loads(
            _capture(
                [
                    str(executable),
                    *PINNED_PYTHON_ISOLATION_ARGS,
                    "-c",
                    probe_script,
                ]
            )
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReleaseError("packaging Python isolated identity is invalid") from exc
    expected_identity = {
        "machine": machine,
        "task_group": True,
        "version": list(build_pipeline.PACKAGING_PYTHON_VERSION),
        "isolated": 1,
        "no_user_site": 1,
        "dont_write_bytecode": 1,
        "enable_user_site": False,
        "pycache_prefix": "/var/empty/focusbrowser-python-cache",
    }
    if identity != expected_identity:
        raise ReleaseError("packaging Python isolated identity mismatch")
    cache_payload = json.dumps(
        non_manifest_cache, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    return {
        "path": str(executable),
        "wrapper": str(wrapper),
        "wrapper_sha256": build_pipeline.PACKAGING_PYTHON_WRAPPER_SHA256,
        "architecture": machine,
        "version": ".".join(
            str(value) for value in build_pipeline.PACKAGING_PYTHON_VERSION
        ),
        "sha256": build_pipeline.PACKAGING_PYTHON_SHA256_BY_HOST[machine],
        "cipd_package": package_name,
        "cipd_version": build_pipeline.PACKAGING_PYTHON_CIPD_VERSION,
        "cipd_instance": instance,
        "manifest": {
            "path": str(manifest_path),
            "sha256": PINNED_PYTHON_MANIFEST_SHA256,
            "file_count": len(expected_paths),
        },
        "runtime_tree": _tree_contract(install_root),
        "non_manifest_cache": {
            "read_isolated_by_pycache_prefix": True,
            "file_count": len(non_manifest_cache),
            "sha256": hashlib.sha256(cache_payload).hexdigest(),
        },
        "isolation_arguments": list(PINNED_PYTHON_ISOLATION_ARGS),
        "isolated_identity": identity,
        "asyncio_task_group": True,
    }


def _expected_build_props_ast(source):
    template = source / "chrome/installer/mac/signing/build_props_config.py.in"
    if template.is_symlink() or not template.is_file():
        raise ReleaseError("missing signing build-properties template")
    if sha256_file(template) != PINNED_BUILD_PROPS_TEMPLATE_SHA256:
        raise ReleaseError("signing build-properties template SHA-256 changed")
    text = template.read_text(encoding="utf-8")
    replacements = {
        "@GEN_HEADER@": "# generated by the pinned Focus macOS release contract",
        "@IS_CHROME_BRANDED@": "False",
        "@ENABLE_UPDATER@": "False",
        "@PRODUCT_FULLNAME@": "Focus Browser",
        "@MAJOR@": "150",
        "@MINOR@": "0",
        "@BUILD@": "7871",
        "@PATCH@": "128",
        "@MAC_BUNDLE_ID@": "com.focusbrowser.browser",
    }
    for token, value in replacements.items():
        text = text.replace(token, value)
    if re.search(r"@[A-Z0-9_]+@", text):
        raise ReleaseError("signing build-properties template has unknown substitutions")
    return ast.dump(ast.parse(text), include_attributes=False)


def _regular_file_record(path, expected_mode):
    path = Path(path)
    if path.is_symlink():
        raise ReleaseError("packaging entry must not be a symlink: {}".format(path))
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        pinned = os.fstat(descriptor)
        named = os.lstat(str(path))
        mode = stat.S_IMODE(pinned.st_mode)
        acl = _acl_report(descriptor=descriptor)
        xattrs = _xattr_report(descriptor=descriptor)
        if (
            not stat.S_ISREG(pinned.st_mode)
            or not _same_inode(pinned, named)
            or pinned.st_uid != os.geteuid()
            or pinned.st_nlink != 1
            or mode != expected_mode
            or mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
        ):
            raise ReleaseError("packaging file metadata mismatch: {}".format(path))
        report = {
            "mode": format(mode, "04o"),
            "uid": pinned.st_uid,
            "gid": pinned.st_gid,
            "flags": getattr(pinned, "st_flags", 0),
            "nlink": pinned.st_nlink,
            "size": pinned.st_size,
            "sha256": _sha256_fd(descriptor),
            "xattrs": xattrs,
            "acl": acl,
        }
        after = os.fstat(descriptor)
        renamed = os.lstat(str(path))
        if (
            _stat_snapshot(pinned) != _stat_snapshot(after)
            or _stat_snapshot(pinned) != _stat_snapshot(renamed)
        ):
            raise ReleaseError("packaging file changed during inspection: {}".format(path))
        return report
    finally:
        os.close(descriptor)


def _safe_file_binding(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("missing regular {}: {}".format(label, path))
    mode = stat.S_IMODE(os.lstat(str(path)).st_mode)
    try:
        report = _regular_file_record(path, mode)
    except ReleaseError as exc:
        raise ReleaseError("unsafe {}: {}".format(label, exc)) from exc
    return {"path": str(path), **report}


def _regular_output_contract(
    path, expected_identity=None, allow_retained_link=False
):
    path = Path(path)
    if path.is_symlink():
        raise ReleaseError("DMG output must not be a symlink")
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        pinned = os.fstat(descriptor)
        named = os.lstat(str(path))
        mode = stat.S_IMODE(pinned.st_mode)
        acl = _acl_report(descriptor=descriptor)
        xattrs = _xattr_report(descriptor=descriptor)
        unsafe = (
            stat.S_IWGRP
            | stat.S_IWOTH
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH
            | stat.S_ISUID
            | stat.S_ISGID
            | stat.S_ISVTX
        )
        if (
            not stat.S_ISREG(pinned.st_mode)
            or not _same_inode(pinned, named)
            or pinned.st_uid != os.geteuid()
            or pinned.st_size <= 0
            or pinned.st_nlink not in ((1, 2) if allow_retained_link else (1,))
            or getattr(pinned, "st_flags", 0) != 0
            or acl["present"]
            or mode & unsafe
            or (expected_identity is not None and (pinned.st_dev, pinned.st_ino) != tuple(expected_identity))
        ):
            raise ReleaseError("DMG output inode or metadata is unsafe")
        digest = _sha256_fd(descriptor)
        after = os.fstat(descriptor)
        after_named = os.lstat(str(path))
        if (
            _stat_snapshot(pinned) != _stat_snapshot(after)
            or _stat_snapshot(pinned) != _stat_snapshot(after_named)
        ):
            raise ReleaseError("DMG output changed during descriptor inspection")
        return {
            "path": str(path),
            "identity": [pinned.st_dev, pinned.st_ino],
            "size_bytes": pinned.st_size,
            "sha256": digest,
            "mode": format(mode, "04o"),
            "uid": pinned.st_uid,
            "gid": pinned.st_gid,
            "nlink": pinned.st_nlink,
            "flags": getattr(pinned, "st_flags", 0),
            "xattrs": xattrs,
            "acl": acl,
        }
    finally:
        os.close(descriptor)


def _packaging_contract(packaging, source):
    """Validate the entire executable signing package, not only its wrapper."""
    packaging = _require_real_directory(packaging, "signing packaging root")
    source = Path(source)
    tree_before = _tree_contract(packaging)
    directories = set()
    files = set()
    for current, child_directories, child_files in os.walk(
        str(packaging), topdown=True, followlinks=False
    ):
        child_directories.sort()
        child_files.sort()
        current_path = Path(current)
        relative_current = current_path.relative_to(packaging).as_posix()
        directories.add("." if relative_current == "." else relative_current)
        for name in child_directories:
            path = current_path / name
            if path.is_symlink():
                raise ReleaseError("signing packaging contains a directory symlink")
        for name in child_files:
            relative = (current_path / name).relative_to(packaging).as_posix()
            files.add(relative)
    if directories != set(PACKAGING_DIRECTORIES) or files != set(PACKAGING_FILES):
        raise ReleaseError(
            "signing packaging inventory mismatch; missing_files={}, extra_files={}, "
            "missing_directories={}, extra_directories={}".format(
                sorted(PACKAGING_FILES - files),
                sorted(files - PACKAGING_FILES),
                sorted(PACKAGING_DIRECTORIES - directories),
                sorted(directories - PACKAGING_DIRECTORIES),
            )
        )
    directory_report = {}
    for relative in sorted(directories):
        path = packaging if relative == "." else packaging / relative
        observed = os.lstat(str(path))
        if (
            not stat.S_ISDIR(observed.st_mode)
            or stat.S_ISLNK(observed.st_mode)
            or stat.S_IMODE(observed.st_mode) != 0o755
            or observed.st_uid != os.geteuid()
        ):
            raise ReleaseError("signing packaging directory metadata mismatch")
        directory_report[relative] = {
            "mode": "0755",
            "uid": observed.st_uid,
            "gid": observed.st_gid,
            "flags": getattr(observed, "st_flags", 0),
            "xattrs": _xattr_report(path=path, follow_symlinks=False),
            "acl": _acl_report(path=path, follow_symlinks=False),
            "nlink": observed.st_nlink,
        }
    file_report = {
        relative: _regular_file_record(
            packaging / relative,
            0o755 if relative in PACKAGING_EXECUTABLES else 0o644,
        )
        for relative in sorted(files)
    }
    for relative, expected_hash in PINNED_SIGNING_PYTHON_SHA256.items():
        source_path = source / "chrome/installer/mac" / relative
        if source_path.is_symlink() or not source_path.is_file():
            raise ReleaseError("missing trusted signing source: {}".format(relative))
        source_hash = sha256_file(source_path)
        if source_hash != expected_hash or file_report[relative]["sha256"] != expected_hash:
            raise ReleaseError("trusted signing Python SHA-256 mismatch: {}".format(relative))
    for relative, expected_hash in PINNED_PACKAGING_SOURCE_SHA256.items():
        source_path = source / PACKAGING_SOURCE_PATHS[relative]
        if source_path.is_symlink() or not source_path.is_file():
            raise ReleaseError("missing trusted packaging source: {}".format(relative))
        if sha256_file(source_path) != expected_hash or file_report[relative]["sha256"] != expected_hash:
            raise ReleaseError("trusted packaging source SHA-256 mismatch: {}".format(relative))
    app_entitlements_source = source / "chrome/app/app-entitlements.plist"
    if (
        app_entitlements_source.is_symlink()
        or not app_entitlements_source.is_file()
        or sha256_file(app_entitlements_source)
        != PINNED_APP_ENTITLEMENTS_TEMPLATE_SHA256
    ):
        raise ReleaseError("app entitlement template SHA-256 changed")
    try:
        with app_entitlements_source.open("rb") as stream:
            expected_entitlements = plistlib.load(stream)
        with (packaging / "app-entitlements.plist").open("rb") as stream:
            generated_entitlements = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise ReleaseError("generated app entitlements are invalid") from exc
    if (
        not isinstance(expected_entitlements, dict)
        or generated_entitlements != expected_entitlements
    ):
        raise ReleaseError("generated app entitlements changed")
    try:
        generated = (packaging / "signing/build_props_config.py").read_text(
            encoding="utf-8"
        )
        generated_ast = ast.dump(ast.parse(generated), include_attributes=False)
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ReleaseError("generated signing build properties are invalid") from exc
    if generated_ast != _expected_build_props_ast(source):
        raise ReleaseError("generated signing build properties changed")
    tree_after = _tree_contract(packaging)
    if tree_after != tree_before:
        raise ReleaseError("signing packaging changed during inspection")
    return {
        "path": str(packaging),
        "tree": tree_after,
        "directories": directory_report,
        "files": file_report,
        "trusted_python_sha256": PINNED_SIGNING_PYTHON_SHA256,
        "trusted_source_sha256": PINNED_PACKAGING_SOURCE_SHA256,
        "build_props_template_sha256": PINNED_BUILD_PROPS_TEMPLATE_SHA256,
        "app_entitlements_template_sha256": (
            PINNED_APP_ENTITLEMENTS_TEMPLATE_SHA256
        ),
    }


def _packaging_fingerprint(report):
    if not isinstance(report, dict):
        raise ReleaseError("signing packaging report is invalid")
    result = dict(report)
    result.pop("path", None)
    return result


def _require_same_packaging(left, right, label):
    if _packaging_fingerprint(left) != _packaging_fingerprint(right):
        raise ReleaseError("signing packaging changed {}".format(label))


def _universalizer_contract(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseError("missing pinned Chromium universalizer: {}".format(path))
    digest = sha256_file(path)
    if digest != focus_macos.PINNED_CHROMIUM_UNIVERSALIZER_SHA256:
        raise ReleaseError("Chromium universalizer SHA-256 changed")
    return {"path": str(path), "sha256": digest, "input_order": ["x64", "arm64"]}


def _ensure_absent(path, label):
    if os.path.lexists(str(path)):
        raise ReleaseError("refusing to overwrite {}: {}".format(label, path))


def _part_path(final):
    return Path(final).parent / ("." + Path(final).name + ".part")


def _fsync_directory(path):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(str(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_no_replace(source, destination):
    source = Path(source)
    destination = Path(destination)
    if source.parent != destination.parent:
        raise ReleaseError("atomic output rename must stay in one parent directory")
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if hasattr(libc, "renameatx_np"):
        function = libc.renameatx_np
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(
            _AT_FDCWD, source_bytes, _AT_FDCWD, destination_bytes, _RENAME_EXCL
        )
    elif hasattr(libc, "renameat2"):
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(
            _AT_FDCWD,
            source_bytes,
            _AT_FDCWD,
            destination_bytes,
            _RENAME_NOREPLACE,
        )
    else:
        raise ReleaseError("atomic no-replace rename is unavailable on this host")
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise ReleaseError("refusing to overwrite output: {}".format(destination))
        raise ReleaseError(
            "atomic output rename failed: {}".format(os.strerror(error))
        )


def _write_json(path, value):
    path = Path(path)
    _ensure_absent(path, "receipt")
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ReleaseError("receipt exceeds size limit")
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    created = os.fstat(descriptor)
    digest = None
    try:
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            opened = os.fstat(descriptor)
            named = os.lstat(str(path))
            if (
                not stat.S_ISREG(opened.st_mode)
                or not _same_inode(opened, named)
                or opened.st_uid != os.geteuid()
                or opened.st_nlink != 1
                or stat.S_IMODE(opened.st_mode) != 0o600
                or getattr(opened, "st_flags", 0) != 0
                or _acl_report(descriptor=descriptor)["present"]
            ):
                raise ReleaseError("receipt inode changed while writing")
            read_descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
            try:
                if not _same_inode(opened, os.fstat(read_descriptor)):
                    raise ReleaseError("receipt inode changed before hashing")
                digest = _sha256_fd(read_descriptor)
            finally:
                os.close(read_descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent)
        return {"path": str(path), "sha256": digest}
    except BaseException as original_error:
        if os.path.lexists(str(path)):
            current = os.lstat(str(path))
            if not _same_inode(created, current):
                raise ReleaseError(
                    "receipt write failed and its path was replaced; refusing cleanup"
                ) from original_error
            os.unlink(str(path))
            _fsync_directory(path.parent)
        raise


def _atomic_json(path, value):
    path = Path(path)
    part = _part_path(path)
    _ensure_absent(path, "receipt")
    _ensure_absent(part, "receipt transaction")
    _write_json(part, value)
    observed = os.lstat(str(part))
    identity = (observed.st_dev, observed.st_ino)
    committed = False
    try:
        try:
            _rename_no_replace(part, path)
        except BaseException as exc:
            if _published_entry_matches(part, path, identity, stat.S_ISREG):
                raise UncertainReleasePublicationError(
                    "receipt rename completed but its return was interrupted",
                    path,
                    identity,
                ) from exc
            raise
        try:
            _fsync_directory(path.parent)
        except BaseException as exc:
            if _published_entry_matches(part, path, identity, stat.S_ISREG):
                raise UncertainReleasePublicationError(
                    "receipt rename completed but parent durability failed: {!r}".format(
                        exc
                    ),
                    path,
                    identity,
                ) from exc
            raise
        committed = True
        digest = _receipt_sha256(path)
        if not _published_entry_matches(part, path, identity, stat.S_ISREG):
            raise CommittedReleasePublicationError(
                "committed receipt pathname changed during final verification",
                path,
                identity,
            )
        return {"path": str(path), "sha256": digest}
    except BaseException as exc:
        if committed and not isinstance(exc, CommittedReleasePublicationError):
            retained = (
                path
                if _published_entry_matches(part, path, identity, stat.S_ISREG)
                else None
            )
            raise CommittedReleasePublicationError(
                "post-commit receipt processing failed: {!r}".format(exc),
                path,
                identity,
                retained_path=retained,
            ) from exc
        raise
    finally:
        active_error = sys.exc_info()[1]
        if (
            not committed
            and _published_entry_matches(part, path, identity, stat.S_ISREG)
        ):
            if not isinstance(active_error, UncertainReleasePublicationError):
                raise UncertainReleasePublicationError(
                    "receipt publication completed before an interruption",
                    path,
                    identity,
                ) from active_error
        elif not committed and os.path.lexists(str(part)):
            current = os.lstat(str(part))
            if (
                not stat.S_ISREG(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or (current.st_dev, current.st_ino) != identity
            ):
                raise ReleaseError(
                    "refusing to clean a changed receipt transaction"
                )
            os.unlink(str(part))
            _fsync_directory(part.parent)


def _read_safe_receipt(path, label):
    path = Path(path)
    if path.is_symlink():
        raise ReleaseError("missing safe {}: {}".format(label, path))
    try:
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ReleaseError("missing safe {}: {}".format(label, path)) from exc
    try:
        opened = os.fstat(descriptor)
        named = os.lstat(str(path))
        mode = stat.S_IMODE(opened.st_mode)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _same_inode(opened, named)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or opened.st_size > MAX_RECEIPT_BYTES
            or getattr(opened, "st_flags", 0) != 0
            or _acl_report(descriptor=descriptor)["present"]
            or mode
            & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
        ):
            raise ReleaseError("missing safe {}: {}".format(label, path))
        chunks = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(remaining, 64 * 1024))
            if not block:
                raise ReleaseError("{} was truncated while reading".format(label))
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        after_named = os.lstat(str(path))
        if (
            _stat_snapshot(opened) != _stat_snapshot(after)
            or _stat_snapshot(opened) != _stat_snapshot(after_named)
        ):
            raise ReleaseError("{} changed while reading".format(label))
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _receipt_sha256(path):
    return hashlib.sha256(_read_safe_receipt(path, "receipt")).hexdigest()


def _load_receipt(path, stage):
    try:
        payload = _read_safe_receipt(path, "{} receipt".format(stage))
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("invalid {} receipt".format(stage)) from exc
    if not isinstance(value, dict):
        raise ReleaseError("{} receipt root must be an object".format(stage))
    if (
        value.get("schema") != SCHEMA
        or value.get("stage") != stage
        or value.get("update_mode") != UPDATE_MODE
    ):
        raise ReleaseError("{} receipt contract mismatch".format(stage))
    expected_keys = RECEIPT_KEYS.get(stage)
    if expected_keys is None or set(value) != expected_keys:
        raise ReleaseError("{} receipt schema keys mismatch".format(stage))
    if (
        value.get("publication") is not False
        or value.get("notarization") is not False
        or value.get("developer_id") is not False
    ):
        raise ReleaseError("{} receipt reports a forbidden release action".format(stage))
    return value


def _relocate_report(value, old_root, new_root):
    """Replace transaction-root paths before persisting a final receipt."""
    old = str(Path(old_root))
    new = str(Path(new_root))
    if isinstance(value, str):
        if value == old:
            return new
        if value.startswith(old + os.sep):
            return new + value[len(old):]
        return value
    if isinstance(value, list):
        return [_relocate_report(child, old_root, new_root) for child in value]
    if isinstance(value, dict):
        return {
            key: _relocate_report(child, old_root, new_root)
            for key, child in value.items()
        }
    return value


def _publish_directory(part, final):
    observed = os.lstat(str(_require_real_directory(part, "transaction output")))
    identity = (observed.st_dev, observed.st_ino)
    _ensure_absent(final, "final stage output")
    _fsync_directory(part)
    try:
        _rename_no_replace(part, final)
    except BaseException as exc:
        if _published_entry_matches(part, final, identity, stat.S_ISDIR):
            raise UncertainReleasePublicationError(
                "stage rename completed but its return was interrupted",
                final,
                identity,
            ) from exc
        raise
    try:
        _fsync_directory(Path(final).parent)
    except BaseException as exc:
        if _published_entry_matches(part, final, identity, stat.S_ISDIR):
            raise UncertainReleasePublicationError(
                "stage rename completed but parent durability failed: {!r}".format(
                    exc
                ),
                final,
                identity,
            ) from exc
        raise


def _published_entry_matches(part, final, identity, type_predicate):
    """Prove a consumed transaction leaf now names the exact final inode."""
    if os.path.lexists(str(part)):
        return False
    try:
        observed = os.lstat(str(final))
    except OSError:
        return False
    return type_predicate(observed.st_mode) and (
        observed.st_dev,
        observed.st_ino,
    ) == tuple(identity)


def _finish_directory_transaction(part, final, identity, committed):
    """Clean only precommit state; preserve and classify any published inode."""
    if committed:
        return
    active_error = sys.exc_info()[1]
    if _published_entry_matches(part, final, identity, stat.S_ISDIR):
        if not isinstance(
            active_error,
            (UncertainReleasePublicationError, CommittedReleasePublicationError),
        ):
            raise UncertainReleasePublicationError(
                "stage publication completed before an interruption",
                final,
                identity,
            ) from active_error
        return
    _cleanup_created_directory(part, identity)


def _cleanup_created_directory(path, identity):
    if not os.path.lexists(str(path)):
        return
    observed = os.lstat(str(path))
    if (
        not stat.S_ISDIR(observed.st_mode)
        or stat.S_ISLNK(observed.st_mode)
        or (observed.st_dev, observed.st_ino) != identity
        or stat.S_IMODE(observed.st_mode) != 0o700
        or observed.st_uid != os.geteuid()
        or getattr(observed, "st_flags", 0) != 0
        or _acl_report(path=path, follow_symlinks=False)["present"]
    ):
        raise ReleaseError("refusing to clean a changed transaction directory: {}".format(path))
    shutil.rmtree(str(path))
    _fsync_directory(Path(path).parent)


def _new_directory_transaction(final):
    part = _part_path(final)
    _ensure_absent(final, "stage output")
    _ensure_absent(part, "stage transaction")
    part.mkdir(mode=0o700)
    observed = os.lstat(str(part))
    if (
        not stat.S_ISDIR(observed.st_mode)
        or stat.S_ISLNK(observed.st_mode)
        or stat.S_IMODE(observed.st_mode) != 0o700
        or observed.st_uid != os.geteuid()
        or getattr(observed, "st_flags", 0) != 0
        or _acl_report(path=part, follow_symlinks=False)["present"]
    ):
        raise ReleaseError("transaction root is not private mode 0700")
    return part, (observed.st_dev, observed.st_ino)


def _pin_private_directory(path, identity):
    descriptor = os.open(str(path), _directory_open_flags())
    observed = os.fstat(descriptor)
    named = os.lstat(str(path))
    if (
        not _same_inode(observed, named)
        or (observed.st_dev, observed.st_ino) != tuple(identity)
        or stat.S_IMODE(observed.st_mode) != 0o700
        or observed.st_uid != os.geteuid()
        or getattr(observed, "st_flags", 0) != 0
        or _acl_report(descriptor=descriptor)["present"]
    ):
        os.close(descriptor)
        raise ReleaseError("private transaction root changed")
    return descriptor


def _require_private_directory(path, descriptor, identity):
    observed = os.fstat(descriptor)
    named = os.lstat(str(path))
    if (
        not _same_inode(observed, named)
        or (observed.st_dev, observed.st_ino) != tuple(identity)
        or stat.S_IMODE(observed.st_mode) != 0o700
        or observed.st_uid != os.geteuid()
        or getattr(observed, "st_flags", 0) != 0
        or _acl_report(descriptor=descriptor)["present"]
    ):
        raise ReleaseError("private transaction root changed")
    return observed


def _copy_command(source, destination):
    return [DITTO, "--rsrc", "--extattr", "--acl", str(source), str(destination)]


def _load_strict_json_file(path, label):
    payload = _read_safe_receipt(path, label)
    return _strict_json_object(payload, label)


def _auto_patch_target_bindings(source, patch_report):
    report_by_path = {item["path"]: item for item in patch_report}
    result = {}
    for relative in AUTO_PATCH_PATHS:
        expected = report_by_path.get(relative)
        if expected is None:
            raise ReleaseError("Auto patch is missing from the exact platform series")
        patch = focus_macos.REPO_ROOT / relative
        output = _capture(
            [
                "/usr/bin/git",
                "-C",
                str(source),
                "apply",
                "--reverse",
                "--check",
                str(patch),
            ]
        )
        if output.strip():
            raise ReleaseError("Auto patch reverse-check emitted unexpected output")
        targets = set()
        for old, new in focus_macos.validate_unified_diff_syntax(patch):
            target = new if new is not None else old
            if target is not None:
                targets.add(target)
        if not targets:
            raise ReleaseError("Auto patch has no current source targets")
        result[relative] = {
            "patch": _safe_file_binding(patch, "Auto patch"),
            "series": expected,
            "reverse_apply_check": True,
            "targets": {
                target: _safe_file_binding(
                    source / target, "Auto patch target {}".format(target)
                )
                for target in sorted(targets)
            },
        }
    return result


def _auto_preparation_contract(source):
    source = Path(source)
    paths = release_paths(source)
    base_path = source / build_pipeline.PREPARATION_RECEIPT
    if sha256_file(base_path) != PINNED_BASE_PREPARATION_SHA256:
        raise ReleaseError("historical base preparation receipt hash changed")
    base = _load_strict_json_file(base_path, "base preparation receipt")
    base_platform = base.get("patch_contract", {}).get("platform")
    current_platform = focus_macos.validate_platform_patch_series()
    if (
        base.get("schema") != 3
        or base.get("chromium_version") != "150.0.7871.128"
        or base.get("offline") is not True
        or base.get("network_operations") != 0
        or base.get("build_executed") is not False
        or base.get("signing_executed") is not False
        or base.get("packaging_executed") is not False
        or base.get("post_prepare_sha256", {}).get("chrome/VERSION")
        != PINNED_BASE_VERSION_SHA256
        or base_platform != current_platform[:3]
    ):
        raise ReleaseError("historical base preparation semantics changed")
    acquisition_path = source.parent / ".focus-chromium-acquisition.json"
    tool_path = source.parent / ".focus-macos-tool-bootstrap.json"
    if (
        sha256_file(acquisition_path) != PINNED_BASE_ACQUISITION_SHA256
        or base.get("acquisition", {}).get("sha256")
        != PINNED_BASE_ACQUISITION_SHA256
    ):
        raise ReleaseError("historical acquisition provenance changed")
    if (
        sha256_file(tool_path) != PINNED_BASE_TOOL_BOOTSTRAP_SHA256
        or base.get("tool_bootstrap", {}).get("sha256")
        != PINNED_BASE_TOOL_BOOTSTRAP_SHA256
    ):
        raise ReleaseError("historical tool-bootstrap provenance changed")
    acquisition = _load_strict_json_file(acquisition_path, "acquisition marker")
    tool = _load_strict_json_file(tool_path, "tool-bootstrap receipt")
    if (
        acquisition.get("status") != "acquisition_complete"
        or acquisition.get("pins", {}).get("chromium_commit")
        != build_pipeline.acquire_chromium.CHROMIUM_COMMIT
        or acquisition.get("pins", {}).get("depot_tools_commit")
        != build_pipeline.acquire_chromium.DEPOT_TOOLS_COMMIT
        or tool.get("chromium_commit")
        != build_pipeline.acquire_chromium.CHROMIUM_COMMIT
        or tool.get("depot_tools_commit")
        != build_pipeline.acquire_chromium.DEPOT_TOOLS_COMMIT
        or tool.get("hooks_complete") is not True
        or tool.get("build_executed") is not False
    ):
        raise ReleaseError("historical acquisition/tool semantics changed")
    profiles = canonical_profiles()
    version = _safe_file_binding(source / "chrome/VERSION", "Auto chrome/VERSION")
    if version["sha256"] != PINNED_AUTO_VERSION_SHA256:
        raise ReleaseError("current Auto chrome/VERSION hash changed")
    repository_paths = {
        "common_flags": focus_macos.COMMON_FLAGS,
        "arm64_auto_flags": focus_macos.AUTOUPDATE_MACOS_FLAGS["arm64"],
        "x64_auto_flags": focus_macos.AUTOUPDATE_MACOS_FLAGS["x64"],
        "platform_series": focus_macos.PLATFORM_SERIES,
        "focus_macos.py": MACOS_DIR / "focus_macos.py",
        "autoupdate_contract.py": MACOS_DIR / "autoupdate_contract.py",
        "autoupdate_release.py": MACOS_DIR / "autoupdate_release.py",
        "runtime_smoke.py": MACOS_DIR / "runtime_smoke.py",
        "package_local_dmg.py": MACOS_DIR / "package_local_dmg.py",
    }
    if set(COMPATIBILITY_RECEIPTS) != set(
        PINNED_COMPATIBILITY_RECEIPT_SHA256
    ):
        raise ReleaseError("historical compatibility receipt pin set changed")
    compat = {}
    for name in COMPATIBILITY_RECEIPTS:
        path = source / "out" / name
        binding = _safe_file_binding(path, "compatibility receipt")
        if binding["sha256"] != PINNED_COMPATIBILITY_RECEIPT_SHA256[name]:
            raise ReleaseError(
                "historical compatibility receipt changed: {}".format(name)
            )
        compat[name] = binding
    return {
        "schema": SCHEMA,
        "stage": "prepare-auto",
        "update_mode": UPDATE_MODE,
        "source_root": str(source),
        "base_preparation": _safe_file_binding(
            base_path, "historical base preparation receipt"
        ),
        "base_acquisition": _safe_file_binding(
            acquisition_path, "historical acquisition marker"
        ),
        "base_tool_bootstrap": _safe_file_binding(
            tool_path, "historical tool-bootstrap receipt"
        ),
        "compatibility_receipts": compat,
        "repository_contract": {
            name: _safe_file_binding(path, name)
            for name, path in sorted(repository_paths.items())
        },
        "auto_patch_application": _auto_patch_target_bindings(
            source, current_platform
        ),
        "direct_source": {
            "chrome/VERSION": version,
            "chromium_macos_build": (
                focus_macos.validate_chromium_macos_build_contract(source)
            ),
        },
        "profiles": {"canonical": profiles},
        "publication": False,
        "notarization": False,
        "developer_id": False,
    }


def prepare_auto_plan(source):
    paths = release_paths(source)
    _ensure_absent(
        paths["auto_preparation_addendum"], "Auto preparation addendum"
    )
    _ensure_absent(
        _part_path(paths["auto_preparation_addendum"]),
        "Auto preparation addendum transaction",
    )
    return {
        **_auto_preparation_contract(source),
        "receipt_path": str(paths["auto_preparation_addendum"]),
        "dry_run": True,
    }


def execute_prepare_auto(source, plan):
    expected = prepare_auto_plan(source)
    if plan != expected:
        raise ReleaseError("Auto preparation inputs changed after planning")
    receipt = {
        key: value
        for key, value in plan.items()
        if key not in ("receipt_path", "dry_run")
    }
    _atomic_json(plan["receipt_path"], receipt)
    return receipt


def _validated_auto_preparation(source):
    path = release_paths(source)["auto_preparation_addendum"]
    receipt = _load_receipt(path, "prepare-auto")
    current = _auto_preparation_contract(source)
    if receipt != current:
        raise ReleaseError("Auto preparation addendum no longer matches source")
    return path, receipt


def _no_work_contract(source, out, ninja):
    del source
    command = [ninja["path"], "-C", str(out), "-n", *NINJA_SEAL_TARGETS]
    output = _capture(command)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines or lines[-1] != "ninja: no work to do.":
        raise ReleaseError("completed Auto output is not a no-work Ninja graph")
    if any(line.startswith("[") for line in lines):
        raise ReleaseError("Ninja dry run reported pending build edges")
    return {
        "command": command,
        "targets": list(NINJA_SEAL_TARGETS),
        "no_work": True,
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "line_count": len(lines),
    }


def _build_state_contract(out):
    return {
        name: _safe_file_binding(Path(out) / name, "Ninja build state")
        for name in ("args.gn", "build.ninja", ".ninja_deps", ".ninja_log")
    }


def _seal_contract(source):
    _require_tools()
    paths = release_paths(source)
    addendum_path, _addendum = _validated_auto_preparation(source)
    profiles = canonical_profiles()
    args = {
        "arm64": validate_args(paths["arm_args"], "arm64", profiles),
        "x64": validate_args(paths["x64_args"], "x64", profiles),
    }
    apps = {
        "arm64": inspect_thin_app(paths["arm_app"], "arm64"),
        "x64": inspect_thin_app(paths["x64_app"], "x64"),
    }
    app_trees = {
        "arm64": _tree_contract(paths["arm_app"]),
        "x64": _tree_contract(paths["x64_app"]),
    }
    try:
        ninja = build_pipeline.ninja_contract(source)
    except build_pipeline.PipelineError as exc:
        raise ReleaseError("pinned Ninja provenance failed: {}".format(exc)) from exc
    no_work = {
        "arm64": _no_work_contract(source, paths["arm_out"], ninja),
        "x64": _no_work_contract(source, paths["x64_out"], ninja),
    }
    return {
        "schema": SCHEMA,
        "stage": "seal",
        "update_mode": UPDATE_MODE,
        "source_root": str(source),
        "auto_preparation_addendum_sha256": _receipt_sha256(addendum_path),
        "source_provenance": {
            "auto_preparation_addendum": _safe_file_binding(
                addendum_path, "Auto preparation addendum"
            ),
            "historical_receipts_relocated_without_rewrite": True,
            "current_source_rebound_by_addendum": True,
        },
        "profiles": profiles,
        "args": args,
        "apps": apps,
        "app_trees": app_trees,
        "signing_packaging": _packaging_contract(
            paths["x64_packaging"], source
        ),
        "packaging_python": _pinned_packaging_python(source),
        "ninja": ninja,
        "no_work": no_work,
        "build_state": {
            "arm64": _build_state_contract(paths["arm_out"]),
            "x64": _build_state_contract(paths["x64_out"]),
        },
        "publication": False,
        "notarization": False,
        "developer_id": False,
    }


def seal_plan(source):
    path = release_paths(source)["build_seal"]
    _ensure_absent(path, "Auto build seal")
    _ensure_absent(_part_path(path), "Auto build seal transaction")
    return {**_seal_contract(source), "receipt_path": str(path), "dry_run": True}


def execute_seal(source, plan):
    expected = seal_plan(source)
    if plan != expected:
        raise ReleaseError("Auto build seal inputs changed after planning")
    receipt = {
        key: value
        for key, value in plan.items()
        if key not in ("receipt_path", "dry_run")
    }
    _atomic_json(plan["receipt_path"], receipt)
    return receipt


def _validated_seal(source):
    path = release_paths(source)["build_seal"]
    receipt = _load_receipt(path, "seal")
    current = _seal_contract(source)
    if receipt != current:
        raise ReleaseError("Auto build seal no longer matches completed outputs")
    return path, receipt


def stage_plan(source):
    _require_tools()
    paths = release_paths(source)
    build_seal_path, _build_seal = _validated_seal(source)
    profiles = canonical_profiles()
    args = {
        "arm64": validate_args(paths["arm_args"], "arm64", profiles),
        "x64": validate_args(paths["x64_args"], "x64", profiles),
    }
    apps = {
        "arm64": inspect_thin_app(paths["arm_app"], "arm64"),
        "x64": inspect_thin_app(paths["x64_app"], "x64"),
    }
    app_trees = {
        "arm64": _tree_contract(paths["arm_app"]),
        "x64": _tree_contract(paths["x64_app"]),
    }
    packaging = _packaging_contract(paths["x64_packaging"], source)
    packaging_python = _pinned_packaging_python(source)
    universalizer = _universalizer_contract(paths["universalizer"])
    _ensure_absent(paths["staging"], "Auto staging output")
    _ensure_absent(_part_path(paths["staging"]), "Auto staging transaction")
    part = _part_path(paths["staging"])
    commands = [
        _copy_command(paths["arm_app"], part / "arm64" / APP_NAME),
        _copy_command(paths["arm_args"], part / "arm64" / "args.gn"),
        _copy_command(paths["x64_app"], part / "x64" / APP_NAME),
        _copy_command(paths["x64_args"], part / "x64" / "args.gn"),
        _copy_command(paths["x64_packaging"], part / "x64" / PACKAGING_NAME),
    ]
    return {
        "schema": SCHEMA,
        "stage": "stage",
        "update_mode": UPDATE_MODE,
        "dry_run": True,
        "source_root": str(source),
        "build_seal": {
            "path": str(build_seal_path),
            "sha256": _receipt_sha256(build_seal_path),
        },
        "paths": {key: str(value) for key, value in paths.items()},
        "profiles": profiles,
        "args": args,
        "apps": apps,
        "app_trees": app_trees,
        "signing_packaging": packaging,
        "packaging_python": packaging_python,
        "universalizer": universalizer,
        "commands": commands,
    }


def execute_stage(source, plan):
    expected = stage_plan(source)
    if plan != expected:
        raise ReleaseError("stage inputs changed after planning")
    paths = release_paths(source)
    part, identity = _new_directory_transaction(paths["staging"])
    committed = False
    try:
        destinations = {
            "staged_arm_app": part / "arm64" / APP_NAME,
            "staged_arm_args": part / "arm64" / "args.gn",
            "staged_x64_app": part / "x64" / APP_NAME,
            "staged_x64_args": part / "x64" / "args.gn",
            "staged_packaging": part / "x64" / PACKAGING_NAME,
        }
        (part / "arm64").mkdir()
        (part / "x64").mkdir()
        sources = (
            (paths["arm_app"], destinations["staged_arm_app"]),
            (paths["arm_args"], destinations["staged_arm_args"]),
            (paths["x64_app"], destinations["staged_x64_app"]),
            (paths["x64_args"], destinations["staged_x64_args"]),
            (paths["x64_packaging"], destinations["staged_packaging"]),
        )
        commands = [
            _copy_command(source_path, destination)
            for source_path, destination in sources
        ]
        if commands != plan["commands"]:
            raise ReleaseError("stage copy command contract changed")
        for command in commands:
            _run(command)
        staged_args = {
            "arm64": validate_args(destinations["staged_arm_args"], "arm64", plan["profiles"]),
            "x64": validate_args(destinations["staged_x64_args"], "x64", plan["profiles"]),
        }
        staged_apps = {
            "arm64": inspect_thin_app(destinations["staged_arm_app"], "arm64"),
            "x64": inspect_thin_app(destinations["staged_x64_app"], "x64"),
        }
        staged_apps = _relocate_report(staged_apps, part, paths["staging"])
        for architecture in ("arm64", "x64"):
            if staged_args[architecture]["sha256"] != plan["args"][architecture]["sha256"]:
                raise ReleaseError("{} args.gn changed during staging".format(architecture))
        staged_packaging = _packaging_contract(
            destinations["staged_packaging"], source
        )
        _require_same_packaging(
            plan["signing_packaging"], staged_packaging, "during staging"
        )
        current_python = _pinned_packaging_python(source)
        if current_python != plan["packaging_python"]:
            raise ReleaseError("pinned packaging Python changed during staging")
        receipt = {
            "schema": SCHEMA,
            "stage": "stage",
            "update_mode": UPDATE_MODE,
            "source_root": str(source),
            "build_seal_sha256": plan["build_seal"]["sha256"],
            "profiles": plan["profiles"],
            "args": {
                architecture: {
                    "source_sha256": plan["args"][architecture]["sha256"],
                    "staged_sha256": staged_args[architecture]["sha256"],
                    "canonical_assignments_sha256": staged_args[architecture]["canonical_assignments_sha256"],
                }
                for architecture in ("arm64", "x64")
            },
            "apps": {
                "arm64": {
                    "architecture": "arm64",
                    "source_tree": plan["app_trees"]["arm64"],
                    "staged_tree": _tree_contract(destinations["staged_arm_app"]),
                },
                "x64": {
                    "architecture": "x86_64",
                    "source_tree": plan["app_trees"]["x64"],
                    "staged_tree": _tree_contract(destinations["staged_x64_app"]),
                },
            },
            "thin_app_contracts": staged_apps,
            "signing_packaging": _relocate_report(
                staged_packaging, part, paths["staging"]
            ),
            "packaging_python": current_python,
            "universalizer_sha256": plan["universalizer"]["sha256"],
            "universalizer_input_order": ["x64", "arm64"],
            "publication": False,
            "notarization": False,
            "developer_id": False,
        }
        for architecture in ("arm64", "x64"):
            if receipt["apps"][architecture]["source_tree"] != receipt["apps"][architecture]["staged_tree"]:
                raise ReleaseError("{} app changed during staging".format(architecture))
        _write_json(part / STAGE_RECEIPT, receipt)
        _publish_directory(part, paths["staging"])
        committed = True
        return receipt
    finally:
        _finish_directory_transaction(
            part, paths["staging"], identity, committed
        )


def _validated_stage(source):
    paths = release_paths(source)
    receipt_path = paths["staging"] / STAGE_RECEIPT
    receipt = _load_receipt(receipt_path, "stage")
    if receipt.get("source_root") != str(source):
        raise ReleaseError("stage receipt source root mismatch")
    build_seal_path, _build_seal = _validated_seal(source)
    if receipt.get("build_seal_sha256") != _receipt_sha256(build_seal_path):
        raise ReleaseError("stage receipt no longer binds the Auto build seal")
    profiles = canonical_profiles()
    if receipt.get("profiles") != profiles:
        raise ReleaseError("stage receipt Auto profiles changed")
    for architecture, app_key, args_key in (
        ("arm64", "staged_arm_app", "staged_arm_args"),
        ("x64", "staged_x64_app", "staged_x64_args"),
    ):
        source_app_key = "arm_app" if architecture == "arm64" else "x64_app"
        source_args_key = "arm_args" if architecture == "arm64" else "x64_args"
        current_thin = inspect_thin_app(paths[app_key], architecture)
        current_thin = _relocate_report(current_thin, paths["staging"], paths["staging"])
        source_thin = inspect_thin_app(paths[source_app_key], architecture)
        observed_args = validate_args(paths[args_key], architecture, profiles)
        source_args = validate_args(paths[source_args_key], architecture, profiles)
        saved_args = receipt.get("args", {}).get(architecture, {})
        if (
            set(saved_args) != {
                "source_sha256",
                "staged_sha256",
                "canonical_assignments_sha256",
            }
            or source_args["sha256"] != saved_args.get("source_sha256")
            or observed_args["sha256"] != saved_args.get("staged_sha256")
            or observed_args["canonical_assignments_sha256"]
            != saved_args.get("canonical_assignments_sha256")
        ):
            raise ReleaseError("staged {} args receipt mismatch".format(architecture))
        saved_app = receipt.get("apps", {}).get(architecture, {})
        expected_architecture = "arm64" if architecture == "arm64" else "x86_64"
        if (
            set(saved_app) != {"architecture", "source_tree", "staged_tree"}
            or saved_app.get("architecture") != expected_architecture
            or _tree_contract(paths[source_app_key]) != saved_app.get("source_tree")
            or _tree_contract(paths[app_key]) != saved_app.get("staged_tree")
            or saved_app.get("source_tree") != saved_app.get("staged_tree")
        ):
            raise ReleaseError("staged {} app changed after receipt".format(architecture))
        saved_thin = receipt.get("thin_app_contracts", {}).get(architecture)
        if saved_thin != current_thin:
            raise ReleaseError("staged {} thin-app contract changed".format(architecture))
        if source_thin.get("architecture") != expected_architecture:
            raise ReleaseError("source {} thin-app contract changed".format(architecture))
    if set(receipt.get("args", {})) != {"arm64", "x64"}:
        raise ReleaseError("stage args receipt architecture set mismatch")
    if set(receipt.get("apps", {})) != {"arm64", "x64"}:
        raise ReleaseError("stage app receipt architecture set mismatch")
    if set(receipt.get("thin_app_contracts", {})) != {"arm64", "x64"}:
        raise ReleaseError("stage thin-app receipt architecture set mismatch")
    staged_packaging = _packaging_contract(paths["staged_packaging"], source)
    saved_packaging = receipt.get("signing_packaging")
    if staged_packaging != saved_packaging:
        raise ReleaseError("staged signing packaging receipt mismatch")
    source_packaging = _packaging_contract(paths["x64_packaging"], source)
    _require_same_packaging(source_packaging, staged_packaging, "after staging")
    packaging_python = _pinned_packaging_python(source)
    if receipt.get("packaging_python") != packaging_python:
        raise ReleaseError("stage packaging Python receipt mismatch")
    universalizer = _universalizer_contract(paths["universalizer"])
    if (
        universalizer["sha256"] != receipt.get("universalizer_sha256")
        or receipt.get("universalizer_input_order") != ["x64", "arm64"]
    ):
        raise ReleaseError("stage universalizer provenance mismatch")
    return receipt_path, receipt


def merge_plan(source):
    _require_tools()
    paths = release_paths(source)
    receipt_path, _receipt = _validated_stage(source)
    _ensure_absent(paths["unsigned"], "unsigned Auto universal output")
    _ensure_absent(_part_path(paths["unsigned"]), "unsigned Auto transaction")
    part = _part_path(paths["unsigned"])
    packaging_python = _pinned_packaging_python(source)
    commands = {
        "copy_packaging": _copy_command(paths["staged_packaging"], part / PACKAGING_NAME),
        "universalize": [
            packaging_python["path"],
            *PINNED_PYTHON_ISOLATION_ARGS,
            str(paths["universalizer"]),
            str(paths["staged_x64_app"]),
            str(paths["staged_arm_app"]),
            str(part / APP_NAME),
        ],
    }
    return {
        "schema": SCHEMA,
        "stage": "merge",
        "update_mode": UPDATE_MODE,
        "dry_run": True,
        "source_root": str(source),
        "stage_receipt": {"path": str(receipt_path), "sha256": _receipt_sha256(receipt_path)},
        "input_order": ["x64", "arm64"],
        "packaging_python": packaging_python,
        "unsigned_root": str(paths["unsigned"]),
        "commands": commands,
        "publication": False,
        "notarization": False,
        "developer_id": False,
    }


def execute_merge(source, plan):
    expected = merge_plan(source)
    if plan != expected:
        raise ReleaseError("merge inputs changed after planning")
    paths = release_paths(source)
    part, identity = _new_directory_transaction(paths["unsigned"])
    committed = False
    try:
        copy_command = _copy_command(paths["staged_packaging"], part / PACKAGING_NAME)
        universalize_command = [
            plan["packaging_python"]["path"],
            *PINNED_PYTHON_ISOLATION_ARGS,
            str(paths["universalizer"]),
            str(paths["staged_x64_app"]),
            str(paths["staged_arm_app"]),
            str(part / APP_NAME),
        ]
        if plan["commands"] != {"copy_packaging": copy_command, "universalize": universalize_command}:
            raise ReleaseError("merge command contract changed")
        _run(copy_command)
        staged_packaging = _packaging_contract(paths["staged_packaging"], source)
        copied_packaging_before = _packaging_contract(
            part / PACKAGING_NAME, source
        )
        _require_same_packaging(
            staged_packaging, copied_packaging_before, "during merge copy"
        )
        _run(universalize_command)
        if _pinned_packaging_python(source) != plan["packaging_python"]:
            raise ReleaseError("pinned packaging Python changed during merge")
        copied_packaging_after = _packaging_contract(
            part / PACKAGING_NAME, source
        )
        _require_same_packaging(
            copied_packaging_before,
            copied_packaging_after,
            "while universalizing",
        )
        acceptance = validate_universal_app(part / APP_NAME, signed=False)
        acceptance = _relocate_report(
            acceptance, part, paths["unsigned"]
        )
        receipt = {
            "schema": SCHEMA,
            "stage": "merge",
            "update_mode": UPDATE_MODE,
            "source_root": str(source),
            "stage_receipt_sha256": plan["stage_receipt"]["sha256"],
            "universalizer_sha256": focus_macos.PINNED_CHROMIUM_UNIVERSALIZER_SHA256,
            "input_order": ["x64", "arm64"],
            "app_tree": _tree_contract(part / APP_NAME),
            "autoupdate_contract_after_merge": acceptance,
            "signing_packaging": _relocate_report(
                copied_packaging_after, part, paths["unsigned"]
            ),
            "packaging_python": plan["packaging_python"],
            "publication": False,
            "notarization": False,
            "developer_id": False,
        }
        _write_json(part / MERGE_RECEIPT, receipt)
        _publish_directory(part, paths["unsigned"])
        committed = True
        return receipt
    finally:
        _finish_directory_transaction(
            part, paths["unsigned"], identity, committed
        )


def _validated_merge(source):
    paths = release_paths(source)
    receipt_path = paths["unsigned"] / MERGE_RECEIPT
    receipt = _load_receipt(receipt_path, "merge")
    if receipt.get("source_root") != str(source) or receipt.get("input_order") != ["x64", "arm64"]:
        raise ReleaseError("merge receipt provenance mismatch")
    stage_path, _stage = _validated_stage(source)
    if receipt.get("stage_receipt_sha256") != _receipt_sha256(stage_path):
        raise ReleaseError("merge receipt no longer binds the stage receipt")
    universalizer = _universalizer_contract(paths["universalizer"])
    if receipt.get("universalizer_sha256") != universalizer["sha256"]:
        raise ReleaseError("merge receipt universalizer mismatch")
    packaging = _packaging_contract(paths["unsigned_packaging"], source)
    if receipt.get("signing_packaging") != packaging:
        raise ReleaseError("merge signing packaging receipt mismatch")
    if receipt.get("packaging_python") != _pinned_packaging_python(source):
        raise ReleaseError("merge packaging Python receipt mismatch")
    if _tree_contract(paths["unsigned_app"]) != receipt.get("app_tree"):
        raise ReleaseError("unsigned universal app changed after merge")
    acceptance = validate_universal_app(paths["unsigned_app"], signed=False)
    if receipt.get("autoupdate_contract_after_merge") != acceptance:
        raise ReleaseError("merge autoupdate contract receipt mismatch")
    return receipt_path, receipt


def sign_plan(source):
    _require_tools()
    paths = release_paths(source)
    receipt_path, _receipt = _validated_merge(source)
    _ensure_absent(paths["signed"], "signed Auto universal output")
    part = _part_path(paths["signed"])
    _ensure_absent(part, "signed Auto transaction")
    packaging = _packaging_contract(paths["unsigned_packaging"], source)
    driver = _driver_contract(paths["unsigned_packaging"] / "sign_chrome.py")
    wrapper = _adhoc_signing_wrapper_contract()
    packaging_python = _pinned_packaging_python(source)
    snapshot = _signing_snapshot_plan(part, wrapper, packaging)
    command = [
        packaging_python["path"],
        *PINNED_PYTHON_ISOLATION_ARGS,
        "-c",
        SIGNING_WRAPPER_BOOTSTRAP,
        DESCRIPTOR_BOUND_WRAPPER,
        str(snapshot["wrapper"]["size"]),
        snapshot["wrapper"]["sha256"],
        "--signing-package",
        snapshot["root"],
        "--signing-manifest-sha256",
        snapshot["manifest"]["sha256"],
        "--",
        "--identity",
        "-",
        "--notarize",
        "none",
        "--disable-packaging",
        "--input",
        str(paths["unsigned"]),
        "--output",
        str(part),
    ]
    return {
        "schema": SCHEMA,
        "stage": "sign",
        "update_mode": UPDATE_MODE,
        "dry_run": True,
        "source_root": str(source),
        "merge_receipt": {"path": str(receipt_path), "sha256": _receipt_sha256(receipt_path)},
        "signing_driver": driver,
        "signing_wrapper": wrapper,
        "signing_snapshot": snapshot,
        "signing_execution": dict(SIGNING_EXECUTION_CONTRACT),
        "signing_policy": _adhoc_signing_policy(),
        "signing_packaging": packaging,
        "packaging_python": packaging_python,
        "signed_root": str(paths["signed"]),
        "command": command,
        "identity": "-",
        "development": False,
        "provisioning_profile": False,
        "run_spctl_assess": False,
        "inject_get_task_allow_entitlement": False,
        "notarization": "none",
        "packaging": False,
        "publication": False,
        "developer_id": False,
    }


def execute_sign(source, plan):
    expected = sign_plan(source)
    if plan != expected:
        raise ReleaseError("sign inputs changed after planning")
    paths = release_paths(source)
    part, identity = _new_directory_transaction(paths["signed"])
    directory_fd = _pin_private_directory(part, identity)
    committed = False
    try:
        packaging_before = _packaging_contract(paths["unsigned_packaging"], source)
        _require_same_packaging(
            plan["signing_packaging"], packaging_before, "before signing"
        )
        wrapper_before = _adhoc_signing_wrapper_contract()
        if wrapper_before != plan["signing_wrapper"]:
            raise ReleaseError("ad-hoc signing wrapper changed before signing")
        _require_private_directory(part, directory_fd, identity)
        expected_snapshot = _signing_snapshot_plan(
            part, wrapper_before, packaging_before
        )
        if expected_snapshot != plan["signing_snapshot"]:
            raise ReleaseError("signing snapshot plan changed before signing")
        snapshot_before = _create_signing_snapshot(
            part, wrapper_before, packaging_before, expected_snapshot
        )
        _validate_signing_snapshot_report(snapshot_before, expected_snapshot)
        wrapper_descriptor = None
        signing_error = None
        cleanup_error = None
        snapshot_after = None
        try:
            wrapper_descriptor = _open_signing_snapshot_wrapper(
                expected_snapshot
            )
            actual_command = list(plan["command"])
            if actual_command.count(DESCRIPTOR_BOUND_WRAPPER) != 1:
                raise ReleaseError("signing command lost its descriptor token")
            actual_command[actual_command.index(DESCRIPTOR_BOUND_WRAPPER)] = (
                str(wrapper_descriptor)
            )
            _run(actual_command, pass_fds=(wrapper_descriptor,))
            snapshot_after = _signing_snapshot_contract(expected_snapshot)
            _validate_signing_snapshot_report(
                snapshot_after, expected_snapshot
            )
            if snapshot_after != snapshot_before:
                raise ReleaseError("signing snapshot changed while signing")
        except BaseException as exc:
            signing_error = exc
        finally:
            if wrapper_descriptor is not None:
                try:
                    os.close(wrapper_descriptor)
                except OSError as exc:
                    if signing_error is None:
                        signing_error = exc
            try:
                _remove_signing_snapshot(expected_snapshot)
            except BaseException as exc:
                cleanup_error = exc
        if signing_error is not None:
            if cleanup_error is not None:
                raise ReleaseError(
                    "signing failed and snapshot cleanup also failed: {!r}; {!r}"
                    .format(signing_error, cleanup_error)
                ) from signing_error
            raise signing_error
        if cleanup_error is not None:
            raise cleanup_error
        _require_private_directory(part, directory_fd, identity)
        wrapper_after = _adhoc_signing_wrapper_contract()
        if wrapper_after != wrapper_before:
            raise ReleaseError("ad-hoc signing wrapper changed while signing")
        packaging_after = _packaging_contract(paths["unsigned_packaging"], source)
        _require_same_packaging(
            packaging_before, packaging_after, "while signing"
        )
        if _pinned_packaging_python(source) != plan["packaging_python"]:
            raise ReleaseError("pinned packaging Python changed while signing")
        signed_app = part / SIGNED_DISTRIBUTION / APP_NAME
        acceptance = validate_universal_app(signed_app, signed=True)
        acceptance = _relocate_report(
            acceptance, part, paths["signed"]
        )
        receipt = {
            "schema": SCHEMA,
            "stage": "sign",
            "update_mode": UPDATE_MODE,
            "source_root": str(source),
            "merge_receipt_sha256": plan["merge_receipt"]["sha256"],
            "signing_packaging_before": packaging_before,
            "signing_packaging_after": packaging_after,
            "signing_wrapper_before": wrapper_before,
            "signing_wrapper_after": wrapper_after,
            "signing_snapshot_plan": expected_snapshot,
            "signing_snapshot_before": snapshot_before,
            "signing_snapshot_after": snapshot_after,
            "signing_execution": dict(SIGNING_EXECUTION_CONTRACT),
            "signing_driver": plan["signing_driver"],
            "signing_policy": plan["signing_policy"],
            "packaging_python": plan["packaging_python"],
            "signing_command": plan["command"],
            "app_tree": _tree_contract(signed_app),
            "autoupdate_contract_after_sign": acceptance,
            "codesign_deep_strict": True,
            "identity": "adhoc",
            "publication": False,
            "notarization": False,
            "developer_id": False,
        }
        _write_json(part / SIGN_RECEIPT, receipt)
        _require_private_directory(part, directory_fd, identity)
        _publish_directory(part, paths["signed"])
        committed = True
        return receipt
    finally:
        os.close(directory_fd)
        _finish_directory_transaction(
            part, paths["signed"], identity, committed
        )


def _validated_sign(source):
    paths = release_paths(source)
    receipt_path = paths["signed"] / SIGN_RECEIPT
    receipt = _load_receipt(receipt_path, "sign")
    if receipt.get("source_root") != str(source):
        raise ReleaseError("sign receipt source root mismatch")
    merge_path, _merge = _validated_merge(source)
    if receipt.get("merge_receipt_sha256") != _receipt_sha256(merge_path):
        raise ReleaseError("sign receipt no longer binds the merge receipt")
    packaging = _packaging_contract(paths["unsigned_packaging"], source)
    if (
        receipt.get("signing_packaging_before") != packaging
        or receipt.get("signing_packaging_after") != packaging
    ):
        raise ReleaseError("sign receipt packaging contract mismatch")
    wrapper = _adhoc_signing_wrapper_contract()
    if (
        receipt.get("signing_wrapper_before") != wrapper
        or receipt.get("signing_wrapper_after") != wrapper
    ):
        raise ReleaseError("sign receipt wrapper contract mismatch")
    driver = _driver_contract(paths["unsigned_packaging"] / "sign_chrome.py")
    if receipt.get("signing_driver") != driver:
        raise ReleaseError("sign receipt Chromium driver mismatch")
    if receipt.get("signing_policy") != _adhoc_signing_policy():
        raise ReleaseError("sign receipt signing policy details mismatch")
    packaging_python = _pinned_packaging_python(source)
    if receipt.get("packaging_python") != packaging_python:
        raise ReleaseError("sign packaging Python receipt mismatch")
    snapshot_plan = _signing_snapshot_plan(
        _part_path(paths["signed"]), wrapper, packaging
    )
    expected_command = [
        packaging_python["path"],
        *PINNED_PYTHON_ISOLATION_ARGS,
        "-c",
        SIGNING_WRAPPER_BOOTSTRAP,
        DESCRIPTOR_BOUND_WRAPPER,
        str(snapshot_plan["wrapper"]["size"]),
        snapshot_plan["wrapper"]["sha256"],
        "--signing-package",
        snapshot_plan["root"],
        "--signing-manifest-sha256",
        snapshot_plan["manifest"]["sha256"],
        "--",
        "--identity",
        "-",
        "--notarize",
        "none",
        "--disable-packaging",
        "--input",
        str(paths["unsigned"]),
        "--output",
        str(_part_path(paths["signed"])),
    ]
    if receipt.get("signing_command") != expected_command:
        raise ReleaseError("sign receipt command mismatch")
    if receipt.get("signing_snapshot_plan") != snapshot_plan:
        raise ReleaseError("sign receipt snapshot plan mismatch")
    snapshot_before = receipt.get("signing_snapshot_before")
    snapshot_after = receipt.get("signing_snapshot_after")
    _validate_signing_snapshot_report(snapshot_before, snapshot_plan)
    _validate_signing_snapshot_report(snapshot_after, snapshot_plan)
    if snapshot_before != snapshot_after:
        raise ReleaseError("sign receipt snapshot changed during execution")
    if receipt.get("signing_execution") != SIGNING_EXECUTION_CONTRACT:
        raise ReleaseError("sign receipt descriptor execution mismatch")
    if (
        receipt.get("codesign_deep_strict") is not True
        or receipt.get("identity") != "adhoc"
    ):
        raise ReleaseError("sign receipt signing policy mismatch")
    if _tree_contract(paths["signed_app"]) != receipt.get("app_tree"):
        raise ReleaseError("signed universal app changed after receipt")
    acceptance = validate_universal_app(paths["signed_app"], signed=True)
    if receipt.get("autoupdate_contract_after_sign") != acceptance:
        raise ReleaseError("sign autoupdate contract receipt mismatch")
    return receipt_path, receipt


def _resolve_sparkle_root(value):
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise ReleaseError("pinned Sparkle dependency root must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ReleaseError("pinned Sparkle dependency root does not exist") from exc
    return _require_real_directory(resolved, "pinned Sparkle dependency root")


def _validate_runtime_acceptance_report(report, app, timeout_seconds):
    expected_root_keys = {
        "app",
        "host_architecture",
        "rosetta_required",
        "rosetta_available",
        "architectures",
        "passed",
    }
    if not isinstance(report, dict) or set(report) != expected_root_keys:
        raise ReleaseError("runtime acceptance report schema mismatch")
    expected_app = (
        str(Path(app).resolve(strict=True)) if app is not None else None
    )
    observed_app = report.get("app")
    if app is None and (
        not isinstance(observed_app, str)
        or not Path(observed_app).is_absolute()
        or Path(observed_app).name != APP_NAME
    ):
        raise ReleaseError("mounted runtime app path mismatch")
    if (
        (expected_app is not None and observed_app != expected_app)
        or report.get("host_architecture") != "arm64"
        or report.get("rosetta_required") is not True
        or report.get("rosetta_available") is not True
        or report.get("passed") is not True
    ):
        raise ReleaseError("runtime acceptance report policy mismatch")
    architectures = report.get("architectures")
    if not isinstance(architectures, list) or len(architectures) != 2:
        raise ReleaseError("runtime acceptance architecture set mismatch")
    expected_keys = {
        "architecture",
        "execution",
        "exit_code",
        "verification_exit_code",
        "storage_control_persistence_verified",
        "storage_control_write_exit_code",
        "storage_control_read_exit_code",
        "incognito",
        "incognito_storage_isolated",
        "incognito_proof",
        "offline_navigation",
        "marker",
        "marker_observed",
        "fresh_profile",
        "timeout_seconds",
        "duration_seconds",
        "stdout_sha256",
        "stderr_sha256",
        "verification_stdout_sha256",
        "verification_stderr_sha256",
        "storage_control_sha256",
        "network_disabling_arguments",
    }
    for index, architecture in enumerate(("arm64", "x86_64")):
        value = architectures[index]
        execution = "native" if architecture == "arm64" else "Rosetta"
        if not isinstance(value, dict) or set(value) != expected_keys:
            raise ReleaseError("runtime {} report schema mismatch".format(architecture))
        if (
            value.get("architecture") != architecture
            or value.get("execution") != execution
            or value.get("exit_code")
            not in runtime_smoke.CONTROLLED_BROWSER_EXIT_CODES
            or value.get("verification_exit_code")
            not in runtime_smoke.CONTROLLED_BROWSER_EXIT_CODES
            or value.get("storage_control_persistence_verified") is not True
            or value.get("storage_control_write_exit_code")
            not in runtime_smoke.CONTROLLED_BROWSER_EXIT_CODES
            or value.get("storage_control_read_exit_code")
            not in runtime_smoke.CONTROLLED_BROWSER_EXIT_CODES
            or value.get("incognito") is not True
            or value.get("incognito_storage_isolated") is not True
            or value.get("incognito_proof")
            != "incognito-write/normal-read localStorage beacon isolation"
            or value.get("offline_navigation")
            != "loopback-http/localStorage-beacon"
            or value.get("marker_observed") is not True
            or value.get("fresh_profile") is not True
            or value.get("timeout_seconds") != timeout_seconds
            or value.get("network_disabling_arguments")
            != list(runtime_smoke.RUNTIME_ARGUMENTS)
            or not _SHA256.fullmatch(value.get("stdout_sha256", ""))
            or not _SHA256.fullmatch(value.get("stderr_sha256", ""))
            or not _SHA256.fullmatch(
                value.get("verification_stdout_sha256", "")
            )
            or not _SHA256.fullmatch(
                value.get("verification_stderr_sha256", "")
            )
            or not _SHA256.fullmatch(value.get("storage_control_sha256", ""))
            or not re.fullmatch(
                r"FOCUSBROWSER_{}_([0-9A-F]{{24}})_OK".format(
                    architecture.upper()
                ),
                value.get("marker", ""),
            )
            or not isinstance(value.get("duration_seconds"), (int, float))
            or isinstance(value.get("duration_seconds"), bool)
            or not 0 <= value["duration_seconds"] <= timeout_seconds * 4
        ):
            raise ReleaseError("runtime {} acceptance mismatch".format(architecture))
    return report


def accept_plan(source, sparkle_source_root):
    _require_tools()
    paths = release_paths(source)
    sign_receipt_path, _sign_receipt = _validated_sign(source)
    sparkle_root = _resolve_sparkle_root(sparkle_source_root)
    receipt_path = paths["signed"] / ACCEPT_RECEIPT
    _ensure_absent(receipt_path, "runtime acceptance receipt")
    _ensure_absent(_part_path(receipt_path), "runtime acceptance transaction")
    release_contract = validate_universal_app(
        paths["signed_app"],
        signed=True,
        sparkle_source_root=sparkle_root,
    )
    try:
        signing_matrix = runtime_smoke.validate_adhoc_signing_matrix(
            paths["signed_app"],
            update_mode=UPDATE_MODE,
            sparkle_source_root=sparkle_root,
        )
    except runtime_smoke.RuntimeSmokeError as exc:
        raise ReleaseError("ad-hoc signing acceptance failed: {}".format(exc)) from exc
    return {
        "schema": SCHEMA,
        "stage": "accept",
        "update_mode": UPDATE_MODE,
        "dry_run": True,
        "source_root": str(source),
        "sign_receipt": {
            "path": str(sign_receipt_path),
            "sha256": _receipt_sha256(sign_receipt_path),
        },
        "accept_receipt": str(receipt_path),
        "sparkle_source_root": str(sparkle_root),
        "packaging_python": _pinned_packaging_python(source),
        "app_tree": _tree_contract(paths["signed_app"]),
        "autoupdate_contract": release_contract,
        "adhoc_signing_matrix": signing_matrix,
        "runtime_timeout_seconds": RUNTIME_TIMEOUT_SECONDS,
        "publication": False,
        "notarization": False,
        "developer_id": False,
    }


def execute_accept(source, sparkle_source_root, plan):
    expected = accept_plan(source, sparkle_source_root)
    if plan != expected:
        raise ReleaseError("runtime acceptance inputs changed after planning")
    paths = release_paths(source)
    before = _tree_contract(paths["signed_app"])
    try:
        runtime_report = runtime_smoke.validate_universal_app_runtime(
            paths["signed_app"],
            timeout_seconds=plan["runtime_timeout_seconds"],
            update_mode=UPDATE_MODE,
            sparkle_source_root=plan["sparkle_source_root"],
        )
    except runtime_smoke.RuntimeSmokeError as exc:
        raise ReleaseError("universal runtime acceptance failed: {}".format(exc)) from exc
    runtime_report = _validate_runtime_acceptance_report(
        runtime_report,
        paths["signed_app"],
        plan["runtime_timeout_seconds"],
    )
    after = _tree_contract(paths["signed_app"])
    if before != plan["app_tree"] or after != before:
        raise ReleaseError("signed app changed during runtime acceptance")
    receipt = {
        "schema": SCHEMA,
        "stage": "accept",
        "update_mode": UPDATE_MODE,
        "source_root": str(source),
        "sign_receipt_sha256": plan["sign_receipt"]["sha256"],
        "sparkle_source_root": plan["sparkle_source_root"],
        "packaging_python": plan["packaging_python"],
        "app_tree": after,
        "autoupdate_contract": plan["autoupdate_contract"],
        "adhoc_signing_matrix": plan["adhoc_signing_matrix"],
        "runtime_acceptance": runtime_report,
        "runtime_timeout_seconds": plan["runtime_timeout_seconds"],
        "publication": False,
        "notarization": False,
        "developer_id": False,
    }
    _atomic_json(plan["accept_receipt"], receipt)
    return receipt


def _validated_accept(source, sparkle_source_root):
    paths = release_paths(source)
    sparkle_root = _resolve_sparkle_root(sparkle_source_root)
    receipt_path = paths["signed"] / ACCEPT_RECEIPT
    receipt = _load_receipt(receipt_path, "accept")
    if (
        receipt.get("source_root") != str(source)
        or receipt.get("sparkle_source_root") != str(sparkle_root)
        or receipt.get("runtime_timeout_seconds") != RUNTIME_TIMEOUT_SECONDS
    ):
        raise ReleaseError("runtime acceptance receipt provenance mismatch")
    sign_path, _sign_receipt = _validated_sign(source)
    if receipt.get("sign_receipt_sha256") != _receipt_sha256(sign_path):
        raise ReleaseError("acceptance receipt no longer binds sign receipt")
    if receipt.get("packaging_python") != _pinned_packaging_python(source):
        raise ReleaseError("acceptance packaging Python receipt mismatch")
    tree = _tree_contract(paths["signed_app"])
    if receipt.get("app_tree") != tree:
        raise ReleaseError("accepted app changed after runtime acceptance")
    release_contract = validate_universal_app(
        paths["signed_app"], signed=True, sparkle_source_root=sparkle_root
    )
    if receipt.get("autoupdate_contract") != release_contract:
        raise ReleaseError("accepted release contract changed")
    try:
        signing_matrix = runtime_smoke.validate_adhoc_signing_matrix(
            paths["signed_app"],
            update_mode=UPDATE_MODE,
            sparkle_source_root=sparkle_root,
        )
    except runtime_smoke.RuntimeSmokeError as exc:
        raise ReleaseError("accepted signing matrix changed: {}".format(exc)) from exc
    if receipt.get("adhoc_signing_matrix") != signing_matrix:
        raise ReleaseError("accepted signing matrix receipt mismatch")
    _validate_runtime_acceptance_report(
        receipt.get("runtime_acceptance"),
        paths["signed_app"],
        RUNTIME_TIMEOUT_SECONDS,
    )
    return receipt_path, receipt


def package_plan(source, dmg_output, sparkle_source_root):
    _require_tools()
    paths = release_paths(source)
    sparkle_root = _resolve_sparkle_root(sparkle_source_root)
    receipt_path, accepted_receipt = _validated_accept(source, sparkle_root)
    provenance_acceptance = validate_universal_app(
        paths["signed_app"],
        signed=True,
        sparkle_source_root=sparkle_root,
    )
    provenance = provenance_acceptance.get("contract", {}).get(
        "sparkle", {}
    ).get("provenance")
    if not isinstance(provenance, dict):
        raise ReleaseError("signed app lacks pinned Sparkle dependency provenance")
    output = package_local_dmg.resolve_output_path(dmg_output)
    candidate_root = _part_path(output)
    candidate = candidate_root / output.name
    sidecar = Path(str(output) + PACKAGE_RECEIPT_SUFFIX)
    _ensure_absent(output, "final DMG")
    _ensure_absent(candidate_root, "private DMG transaction")
    _ensure_absent(sidecar, "package receipt")
    _ensure_absent(_part_path(sidecar), "package receipt transaction")
    packaging_python = _pinned_packaging_python(source)
    package_driver = _package_driver_contract()
    command = [
        packaging_python["path"],
        *PINNED_PYTHON_ISOLATION_ARGS,
        str(Path(package_local_dmg.__file__).resolve()),
        "--app",
        str(paths["signed_app"]),
        "--output",
        str(candidate),
        "--require-universal",
        "--require-autoupdate",
        "--sparkle-source-root",
        str(sparkle_root),
        "--json",
    ]
    return {
        "schema": SCHEMA,
        "stage": "package",
        "update_mode": UPDATE_MODE,
        "dry_run": True,
        "source_root": str(source),
        "accept_receipt": {"path": str(receipt_path), "sha256": _receipt_sha256(receipt_path)},
        "app": str(paths["signed_app"]),
        "dmg_output": str(output),
        "candidate_root": str(candidate_root),
        "candidate_dmg": str(candidate),
        "package_receipt": str(sidecar),
        "require_universal": True,
        "require_autoupdate": True,
        "sparkle_source_root": str(sparkle_root),
        "packaging_python": packaging_python,
        "package_driver": package_driver,
        "accepted_app_tree": accepted_receipt["app_tree"],
        "autoupdate_contract_with_sparkle_provenance": provenance_acceptance,
        "command": command,
        "publication": False,
        "notarization": False,
        "developer_id": False,
    }


def execute_package(source, dmg_output, sparkle_source_root, plan):
    expected = package_plan(source, dmg_output, sparkle_source_root)
    if plan != expected:
        raise ReleaseError("package inputs changed after planning")
    output = Path(plan["dmg_output"])
    candidate_root, root_identity = _new_directory_transaction(output)
    root_fd = _pin_private_directory(candidate_root, root_identity)
    candidate = candidate_root / output.name
    public_committed = False
    retain_candidate_root = False
    committed_warning = None
    final_contract = None
    try:
        if str(candidate_root) != plan["candidate_root"] or str(candidate) != plan["candidate_dmg"]:
            raise ReleaseError("private DMG transaction path changed")
        _require_private_directory(candidate_root, root_fd, root_identity)
        try:
            if _pinned_packaging_python(source) != plan["packaging_python"]:
                raise ReleaseError("pinned packaging Python changed before DMG helper")
            if _package_driver_contract() != plan["package_driver"]:
                raise ReleaseError("DMG subprocess driver changed before execution")
            package_process = _run(plan["command"])
            package_report = _strict_json_object(
                package_process.stdout, "package_local_dmg report"
            )
        except BaseException as exc:
            # A separate helper can have mounted media or retained quarantine state;
            # preserve the whole private root when its typed exception cannot cross
            # the subprocess boundary.
            retain_candidate_root = True
            if isinstance(exc, ReleaseError):
                raise
            raise ReleaseError(
                "universal DMG subprocess failed; private root retained at {}: {}".format(
                    candidate_root, exc
                )
            ) from exc
        _require_private_directory(candidate_root, root_fd, root_identity)
        if _package_driver_contract() != plan["package_driver"]:
            raise ReleaseError("DMG subprocess driver changed during execution")
        if _pinned_packaging_python(source) != plan["packaging_python"]:
            raise ReleaseError("pinned packaging Python changed during DMG helper")
        candidate_contract = _regular_output_contract(candidate)
        if (
            package_report.get("output") != str(candidate)
            or package_report.get("architectures") != ["arm64", "x86_64"]
            or package_report.get("require_universal") is not True
            or package_report.get("require_autoupdate") is not True
            or package_report.get("sparkle_source_root") != plan["sparkle_source_root"]
            or package_report.get("notarization_performed") is not False
            or package_report.get("signing_performed") is not False
            or package_report.get("size_bytes") != candidate_contract["size_bytes"]
            or package_report.get("sha256") != candidate_contract["sha256"]
        ):
            raise ReleaseError("package_local_dmg returned an unsafe release report")
        try:
            mounted_runtime = runtime_smoke.validate_mounted_dmg_runtime(
                candidate,
                timeout_seconds=RUNTIME_TIMEOUT_SECONDS,
                update_mode=UPDATE_MODE,
                sparkle_source_root=plan["sparkle_source_root"],
            )
        except runtime_smoke.DmgDetachError as exc:
            retain_candidate_root = True
            raise ReleaseError(
                "mounted DMG detach is unproven; candidate retained at {}: {}".format(
                    candidate_root, exc
                )
            ) from exc
        except runtime_smoke.RuntimeSmokeError as exc:
            raise ReleaseError("mounted DMG runtime acceptance failed: {}".format(exc)) from exc
        if (
            not isinstance(mounted_runtime, dict)
            or set(mounted_runtime)
            != {
                "dmg",
                "size_bytes",
                "sha256",
                "descriptor_pinned",
                "mounted_read_only",
                "runtime",
                "passed",
            }
            or mounted_runtime.get("dmg") != str(candidate.resolve(strict=True))
            or mounted_runtime.get("size_bytes") != candidate_contract["size_bytes"]
            or mounted_runtime.get("sha256") != candidate_contract["sha256"]
            or mounted_runtime.get("descriptor_pinned") is not True
            or mounted_runtime.get("mounted_read_only") is not True
            or mounted_runtime.get("passed") is not True
        ):
            raise ReleaseError("mounted DMG runtime report mismatch")
        _validate_runtime_acceptance_report(
            mounted_runtime.get("runtime"), None, RUNTIME_TIMEOUT_SECONDS
        )
        if _tree_contract(Path(plan["app"])) != plan["accepted_app_tree"]:
            raise ReleaseError("accepted app changed during DMG packaging")
        current_release = validate_universal_app(
            plan["app"],
            signed=True,
            sparkle_source_root=plan["sparkle_source_root"],
        )
        if current_release != plan["autoupdate_contract_with_sparkle_provenance"]:
            raise ReleaseError("release contract changed during DMG packaging")
        candidate_contract_after = _regular_output_contract(
            candidate, expected_identity=candidate_contract["identity"]
        )
        if candidate_contract_after != candidate_contract:
            raise ReleaseError("DMG candidate changed after mounted runtime acceptance")
        rebound_accept_path, rebound_accept = _validated_accept(
            source, plan["sparkle_source_root"]
        )
        if (
            _receipt_sha256(rebound_accept_path)
            != plan["accept_receipt"]["sha256"]
            or rebound_accept.get("app_tree") != plan["accepted_app_tree"]
            or _tree_contract(Path(plan["app"])) != plan["accepted_app_tree"]
            or _pinned_packaging_python(source) != plan["packaging_python"]
            or _package_driver_contract() != plan["package_driver"]
            or _regular_output_contract(
                candidate, expected_identity=candidate_contract["identity"]
            )
            != candidate_contract
        ):
            raise ReleaseError(
                "release acceptance/provenance changed immediately before DMG commit"
            )
        try:
            package_local_dmg.durable_publish_candidate(
                candidate,
                output,
                candidate_contract["identity"],
                candidate_contract["size_bytes"],
                candidate_contract["sha256"],
            )
            public_committed = True
        except package_local_dmg.CommittedPublishError as exc:
            public_committed = True
            retain_candidate_root = True
            committed_warning = {
                "message": str(exc),
                "retained_quarantine": exc.retained_quarantine,
            }
        except package_local_dmg.RetainedQuarantineError as exc:
            retain_candidate_root = True
            raise ReleaseError(
                "safe DMG publication retained a racing entry under {}: {}".format(
                    candidate_root, exc
                )
            ) from exc
        except package_local_dmg.PackageError as exc:
            raise ReleaseError("safe final DMG publication failed: {}".format(exc)) from exc
        final_contract = _regular_output_contract(
            output,
            expected_identity=candidate_contract["identity"],
            allow_retained_link=committed_warning is not None,
        )
        if (
            final_contract["size_bytes"] != candidate_contract["size_bytes"]
            or final_contract["sha256"] != candidate_contract["sha256"]
        ):
            raise ReleaseError("final DMG differs from accepted candidate")
        final_package_report = _relocate_report(package_report, candidate, output)
        final_mounted_runtime = _relocate_report(mounted_runtime, candidate, output)
        receipt = {
            "schema": SCHEMA,
            "stage": "package",
            "update_mode": UPDATE_MODE,
            "source_root": str(source),
            "accept_receipt_sha256": plan["accept_receipt"]["sha256"],
            "sparkle_provenance": plan[
                "autoupdate_contract_with_sparkle_provenance"
            ]["contract"]["sparkle"]["provenance"],
            "packaging_python": plan["packaging_python"],
            "package_driver": plan["package_driver"],
            "dmg": final_package_report,
            "mounted_runtime_acceptance": final_mounted_runtime,
            "final_output": final_contract,
            "committed_cleanup_warning": committed_warning,
            "publication": False,
            "notarization": False,
            "developer_id": False,
        }
        try:
            receipt_report = _atomic_json(plan["package_receipt"], receipt)
        except Exception as exc:
            verified = _regular_output_contract(
                output,
                expected_identity=final_contract["identity"],
                allow_retained_link=committed_warning is not None,
            )
            raise CommittedOutputError(
                "DMG receipt could not be committed: {!r}".format(exc),
                output,
                verified["size_bytes"],
                verified["sha256"],
            ) from exc
        rebound = _regular_output_contract(
            output,
            expected_identity=final_contract["identity"],
            allow_retained_link=committed_warning is not None,
        )
        if rebound != final_contract:
            raise CommittedOutputError(
                "final DMG changed after receipt commit",
                output,
                rebound["size_bytes"],
                rebound["sha256"],
            )
        return {"stage": "package", "receipt": receipt_report, "release": receipt}
    except CommittedOutputError:
        raise
    except Exception as exc:
        if public_committed:
            if final_contract is None:
                final_contract = _regular_output_contract(
                    output,
                    allow_retained_link=retain_candidate_root,
                )
            raise CommittedOutputError(
                "post-commit DMG processing failed: {!r}".format(exc),
                output,
                final_contract["size_bytes"],
                final_contract["sha256"],
            ) from exc
        raise
    finally:
        os.close(root_fd)
        if not retain_candidate_root:
            try:
                _cleanup_created_directory(candidate_root, root_identity)
            except Exception as cleanup_error:
                if public_committed and final_contract is not None:
                    raise CommittedOutputError(
                        "private DMG transaction cleanup failed: {!r}".format(
                            cleanup_error
                        ),
                        output,
                        final_contract["size_bytes"],
                        final_contract["sha256"],
                    ) from cleanup_error
                raise


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="stage", required=True)
    for name in (
        "prepare-auto",
        "seal",
        "stage",
        "merge",
        "sign",
        "accept",
        "package",
    ):
        child = subparsers.add_parser(name)
        child.add_argument("--source-root", required=True)
        child.add_argument("--execute", action="store_true")
        child.add_argument("--json", action="store_true")
        if name == "package":
            child.add_argument("--dmg-output", required=True)
        if name in ("accept", "package"):
            child.add_argument(
                "--sparkle-source-root",
                required=True,
                help="completed acquire_sparkle.py dependency root",
            )
    return parser


def _emit(report, as_json):
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("OK: {} macOS Auto release stage ({})".format(
            report.get("stage", "completed"),
            "executed" if report.get("dry_run") is False else "dry run",
        ))


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    try:
        source = resolve_source_root(arguments.source_root)
        if arguments.stage == "prepare-auto":
            plan = prepare_auto_plan(source)
            result = (
                execute_prepare_auto(source, plan)
                if arguments.execute
                else plan
            )
        elif arguments.stage == "seal":
            plan = seal_plan(source)
            result = execute_seal(source, plan) if arguments.execute else plan
        elif arguments.stage == "stage":
            plan = stage_plan(source)
            result = execute_stage(source, plan) if arguments.execute else plan
        elif arguments.stage == "merge":
            plan = merge_plan(source)
            result = execute_merge(source, plan) if arguments.execute else plan
        elif arguments.stage == "sign":
            plan = sign_plan(source)
            result = execute_sign(source, plan) if arguments.execute else plan
        elif arguments.stage == "accept":
            plan = accept_plan(source, arguments.sparkle_source_root)
            result = (
                execute_accept(source, arguments.sparkle_source_root, plan)
                if arguments.execute
                else plan
            )
        else:
            plan = package_plan(
                source,
                arguments.dmg_output,
                arguments.sparkle_source_root,
            )
            result = (
                execute_package(
                    source,
                    arguments.dmg_output,
                    arguments.sparkle_source_root,
                    plan,
                )
                if arguments.execute
                else plan
            )
        result = dict(result)
        result["dry_run"] = not arguments.execute
    except (
        OSError,
        ReleaseError,
        focus_macos.ContractError,
        package_local_dmg.PackageError,
        plistlib.InvalidFileException,
    ) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2
    _emit(result, arguments.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
