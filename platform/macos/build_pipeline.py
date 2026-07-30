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
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path


MACOS_DIR = Path(__file__).resolve().parent
if str(MACOS_DIR) not in sys.path:
    sys.path.insert(0, str(MACOS_DIR))

import acquire_chromium  # pylint: disable=wrong-import-position
import focus_macos  # pylint: disable=wrong-import-position
import package_local_dmg  # pylint: disable=wrong-import-position
import prepare_source  # pylint: disable=wrong-import-position
import runtime_smoke  # pylint: disable=wrong-import-position


GIB = 1024 ** 3
SOFT_FLOOR_GIB = 35
HARD_FLOOR_GIB = 30
BOOTSTRAP_POST_GIB = 70
BUILD_JOBS = 8
POLL_SECONDS = 2.0
DMG_RUNTIME_CANDIDATE_PREFIX = ".focusbrowser-runtime-candidate-"

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
SIGNED_DISTRIBUTION_DIR = "stable"
SLICE_RECEIPT_NAME = "FocusMacBuild.json"
GN_COMPAT_RECEIPT = "out/FocusMacGnCompatibility.json"
GN_COMPAT_PATCH = MACOS_DIR / "patches/gn-disabled-feature-compat.patch"
GN_COMPAT_PATCH_SHA256 = (
    "38f26860f0696b42d4fa4bdd0ecd9b1d845b7efb934ec4c1fe686c6d56e89946"
)
GN_COMPAT_FILES = {
    "chrome/BUILD.gn": {
        "pre_sha256": "7ba0e1514324ede6c1fd6eccf99f5353cd84ecc5975b57db7524771c8e74c568",
        "post_sha256": "e5fb9f6c7c09a8c452d70e57ffce40372d9846750a23f0ef5382062d1115944c",
    },
    "content/shell/BUILD.gn": {
        "pre_sha256": "0d2d9301245d9a95f2cd82f79ff200d2bbfefbe9c645be5a8b73fde96f61aeb8",
        "post_sha256": "c1633186735a85ee533b871a8b10133f3ef093364a1f012866fca5ac48fc348e",
    },
    "chrome/test/BUILD.gn": {
        "pre_sha256": "3c646a781cfb05291945565e8dee931b78f064c1b0cd8e5e701368107d3295a0",
        "post_sha256": "95176a7d97703e8574f05b5872b9e12d633c7cee5faa6be1467032bf72aa01a1",
    },
}
XCODE27_COMPAT_RECEIPT = "out/FocusMacXcode27Compatibility.json"
XCODE27_COMPAT_PATCH = MACOS_DIR / "patches/xcode27-builtin-float.patch"
XCODE27_COMPAT_PATCH_SHA256 = (
    "045cd70cc5d744008420d23d8cf5c3f57a01d8bd17c4a73eb418a55e6213f000"
)
XCODE27_COMPAT_UPSTREAM = {
    "commit": "f0ccfb5933f7daa9545159afbb35bdf8951efcc4",
    "change_id": "Ia03a9da205754591442355f99fa42e21e05ed0dc",
    "commit_position": "refs/heads/main@{#1643921}",
    "url": (
        "https://chromium.googlesource.com/chromium/src/buildtools/+/"
        "f0ccfb5933f7daa9545159afbb35bdf8951efcc4%5E%21/"
    ),
}
XCODE27_COMPAT_TOOLCHAIN = {
    "xcode": {"version": "27.0", "build": "27A5228h"},
    "sdk": {
        "version": "27.0",
        "build": "26A5388f",
        "minimum_deployment_target": "12.0",
        "architectures": ["arm64", "arm64e", "x86_64", "x86_64h"],
    },
}
XCODE27_COMPAT_FILES = {
    "buildtools/third_party/libc++/BUILD.gn": {
        "pre_sha256": "9753adb6fe3c4ded31e7631d0e8c1a3ee7d17b6b3c9a595976c7f50ad393f46a",
        "post_sha256": "e1916a65a94dfe6a4d1324eed43def375377d32f86ed6ac77759a036f0db85bf",
    }
}
XCODE27_SEATBELT_RECEIPT = "out/FocusMacXcode27SeatbeltCompatibility.json"
XCODE27_SEATBELT_PATCH = MACOS_DIR / "patches/xcode27-seatbelt.patch"
XCODE27_SEATBELT_PATCH_SHA256 = (
    "91e05b88e414a4115924a3296284691975be846189a4087a87acc5fa20a462d0"
)
XCODE27_SEATBELT_UPSTREAM = {
    "commit": "6c0a651f9cf91d07c87be8feba854a38a311aba6",
    "change_id": "Idcca8b7863c2b10821d8eae5bca782c80be6fe70",
    "commit_position": "refs/heads/main@{#1655528}",
    "url": (
        "https://chromium.googlesource.com/chromium/src/+"
        "/6c0a651f9cf91d07c87be8feba854a38a311aba6%5E%21/"
    ),
}
XCODE27_SEATBELT_FILES = {
    "sandbox/mac/seatbelt.cc": {
        "pre_sha256": "2b2c1a3821bd4546e6f59fa8e666a1846c7d607aa2e3834a75c4a0fe519a55f2",
        "post_sha256": "ca74d83dfa03acd119d3900ba0c10608df46df21982126c3c0a8832d4d23b08d",
    },
    "sandbox/mac/seatbelt.h": {
        "pre_sha256": "d29decfc61f3f9c56e635c00936a129082e5781f11717d9a8ecffaab278161dc",
        "post_sha256": "56d67b2b333ef2cefaf8f1f609f3ca6c9dcfa745fd93c16ad0ad6baef8302691",
    },
}
SCREEN_AI_DISABLED_RECEIPT = "out/FocusMacScreenAIDisabledCompatibility.json"
SCREEN_AI_DISABLED_RECEIPT_SCHEMA = 2
SCREEN_AI_DISABLED_PATCH = MACOS_DIR / "patches/screen-ai-disabled-link.patch"
SCREEN_AI_DISABLED_PATCH_SHA256 = (
    "58fc6459a40e1bf6b732a8055d58376e811ffdff6604f7af87ef13d39f95de03"
)
SCREEN_AI_DISABLED_UPSTREAM = {
    "known_good_commit": "50ec1394c5b291e34376e4f8daa77052653713ee",
    "introduced_commit": "c5de29a7cd701daec46a7bf042dd0551e5e8c5c3",
    "regression_commit": "4ee66d6d1eb2b630a9e30f52f08e3233e23c5864",
    "change_id": "Iba8cd5583026a993e3236f1fe4bb48e822368b54",
    "change_number": 5762356,
    "commit_position": "refs/heads/main@{#1337465}",
    "bug": "353628440",
    "url": (
        "https://chromium.googlesource.com/chromium/src/+"
        "/4ee66d6d1eb2b630a9e30f52f08e3233e23c5864%5E%21/"
        "chrome/browser/chrome_content_browser_client.cc"
    ),
}
# The first local receipt was published before the review distinguished
# Gerrit's Change-Id from its numeric change number. Keep that exact byte-level
# shape readable so already-built slices remain verifiable; new receipts always
# use the corrected canonical metadata above.
SCREEN_AI_DISABLED_LEGACY_UPSTREAM = dict(SCREEN_AI_DISABLED_UPSTREAM)
SCREEN_AI_DISABLED_LEGACY_UPSTREAM.pop("change_number")
SCREEN_AI_DISABLED_LEGACY_UPSTREAM["change_id"] = "5762356"
SCREEN_AI_DISABLED_FILES = {
    "chrome/browser/chrome_content_browser_client.cc": {
        "pre_sha256": "ad2e820a3e194e98110159417b4a5f334dc3ce7b66e852c9384572f4b9e6ba4b",
        "post_sha256": "974c24cc412b9b0017325f978c80f0d1e8b74dfbb22953764e503b4c3ecddd11",
    }
}
SCREEN_AI_DISABLED_CONFIG_FILES = {
    "services/screen_ai/buildflags/features.gni": (
        "ffcd36b688338f311810db3ade6e4862d9bff524de52bd09e64c16ef7fedfc05"
    )
}

XCODE27_LINKEDIT_STRIP_RECEIPT = (
    "out/FocusMacXcode27LinkeditStripCompatibility.json"
)
XCODE27_LINKEDIT_STRIP_PATCH = (
    MACOS_DIR / "patches/xcode27-linkedit-strip.patch"
)
XCODE27_LINKEDIT_STRIP_PATCH_SHA256 = (
    "52ed98954f21815a95440e64c0ea6e3abf9ef89461698990caeb8cbe4076ead7"
)
XCODE27_LINKEDIT_STRIP_FILES = {
    "build/toolchain/apple/toolchain.gni": {
        "pre_sha256": "7f4408b57541d1a87abfe20a0ac8b4a5381c112d223b440bdb80752bab0e78aa",
        "post_sha256": "4d917e683ee6af93b3b2720308bff55530ffbdd1ee445d841b7deee653a4b29e",
    }
}
XCODE27_LINKEDIT_STRIP_UPSTREAM = {
    "issue": 203678,
    "issue_url": "https://github.com/llvm/llvm-project/issues/203678",
    "pull_request": 203680,
    "pull_request_url": "https://github.com/llvm/llvm-project/pull/203680",
    "fix_commit": "18c1cbce6874a7341f357014befb66d4c11a04a9",
    "fix_commit_url": (
        "https://github.com/llvm/llvm-project/commit/"
        "18c1cbce6874a7341f357014befb66d4c11a04a9"
    ),
    "pinned_llvm_commit": "20b6ec66967ac2a8f932863c1abf251e5b17a843",
    "pinned_llvm_package_revision": "llvmorg-23-init-10931-g20b6ec66-11",
}
XCODE27_LINKEDIT_STRIP_RELATIVE = (
    "Toolchains/XcodeDefault.xctoolchain/usr/bin/strip"
)
XCODE27_LINKEDIT_STRIP_SHA256 = (
    "4c52b02258f7e881010f34b68d47fb2d18b69c02fa6ef4f66cbd6f58d6e6f00e"
)
BUNDLED_LLVM_STRIP_RELATIVE = (
    "third_party/llvm-build/Release+Asserts/bin/llvm-strip"
)
BUNDLED_LLVM_STRIP_SYMLINK_TARGET = "llvm-objcopy"
BUNDLED_LLVM_STRIP_SHA256 = (
    "ce152d23693da05c4f91d0ab9f6916c52cc19aaa9ff43092ff10424fe20b9679"
)
BUNDLED_LLVM_REVISION_RELATIVE = (
    "third_party/llvm-build/Release+Asserts/cr_build_revision"
)
BUNDLED_LLVM_REVISION_SHA256 = (
    "38992a784aa4df47f4d55cf1175316642e2ba39aed39ac01caa6a552fea818ea"
)

SWIFTSHADER_DISABLED_SIGNING_RECEIPT = (
    "out/FocusMacSwiftShaderDisabledSigningCompatibility.json"
)
SWIFTSHADER_DISABLED_SIGNING_PATCH = (
    MACOS_DIR / "patches/swiftshader-disabled-signing.patch"
)
SWIFTSHADER_DISABLED_SIGNING_PATCH_SHA256 = (
    "e28b64ca51c0589c4f20c40276e2fa6064e3b95fd8d1cc350e913e9769b289a4"
)
SWIFTSHADER_DISABLED_SIGNING_FILES = {
    "chrome/installer/mac/signing/parts.py": {
        "pre_sha256": "bf24d456dd39b0cedaee3801e60918ba45559648406b923199021035bff32eb7",
        "post_sha256": "41c05f155e53f9d109464954e5efa88fc1e8e4882237e5f224a081fdf056f498",
    }
}
SWIFTSHADER_DISABLED_PROFILE_SHA256 = {
    "arm64": "c20cfc128b0a29e4a1a9a6c77d4136aeffd8475a38731c8a1a25b31a4deac543",
    "x64": "97049f4283c4eb0bd3b598832b73d102506e01d756645794b048c97e54448a0d",
}
SWIFTSHADER_DISABLED_ARGS_SHA256 = {
    "arm64": "69180d23743db64b6630f5f41e3154a8dee16967a0a00b6e83c103eda6542ad3",
    "x64": "3c48347a05797ed1e2e6ffc0be6ef00b277cc4d838ae78cd5294a5387d4d4ec1",
}
SWIFTSHADER_REQUIRED_ANGLE_LIBRARIES = (
    "libEGL.dylib",
    "libGLESv2.dylib",
)
SWIFTSHADER_VULKAN_LIBRARY = "libvk_swiftshader.dylib"
SWIFTSHADER_DISABLED_SIGNING_UPSTREAM = {
    "swiftshader_signing_introduced_commit": (
        "abafaff8b0398c70bd12d1dcc7d5b100fd426a19"
    ),
    "conditional_signing_precedent_commit": (
        "64b4ad0ad126ef8db7769501d6ffc7cb85f29866"
    ),
    "conditional_signing_precedent_change_id": (
        "Ia2b5a2d0bfafbab22f0521e5c063a17795463e77"
    ),
    "conditional_signing_precedent_commit_position": (
        "refs/heads/main@{#1655305}"
    ),
}

ADHOC_RUNTIME_SIGNING_RECEIPT = (
    "out/FocusMacAdHocRuntimeSigningCompatibility.json"
)
ADHOC_RUNTIME_SIGNING_PATCH = MACOS_DIR / "patches/adhoc-runtime-signing.patch"
ADHOC_RUNTIME_SIGNING_PATCH_SHA256 = (
    "133638295cf4cb04a651017b07366d39c0aa0ae3e1595fbc45e57c390dff4e13"
)
ADHOC_RUNTIME_SIGNING_FILES = {
    "chrome/installer/mac/signing/parts.py": {
        "pre_sha256": "41c05f155e53f9d109464954e5efa88fc1e8e4882237e5f224a081fdf056f498",
        "post_sha256": "5822b9f15388158b79cbbb7325ae080c5a7cf99ec41de4ef210a741e836ec177",
    },
    "chrome/installer/mac/signing/modification.py": {
        "pre_sha256": "35873786f936c81423104149c0ce37f4ed68d5c3576e046a1631fe2b03808f9c",
        "post_sha256": "b1d696a20e9ea1cfa0b8e8e3139c5b6595bdf1fe10c55aeee303af1da4faa790",
    },
    "chrome/installer/mac/signing/parts_test.py": {
        "pre_sha256": "1fada206e0c427bf6d3d31fc4b90d546045486cf0f440b8e18fb50f61ea475d9",
        "post_sha256": "1a26211ba3cc2743d945ccd76fc20b29a68febba5feddd2468fd6f0b446a9f0a",
    },
    "chrome/installer/mac/signing/modification_test.py": {
        "pre_sha256": "9efa3bcc72b6d4ad9442cf58f63a209a7946aa1e43cb6422d83bf8019129d364",
        "post_sha256": "fcfd35b3c212db95f3097a0b9968cc5d8eb923de652f888f9af9c1b049f5876a",
    },
}
ADHOC_RUNTIME_SIGNING_GENERATED_FILES = {
    relative: hashes
    for relative, hashes in ADHOC_RUNTIME_SIGNING_FILES.items()
    if Path(relative).name in ("parts.py", "modification.py")
    and not Path(relative).name.endswith("_test.py")
}
ADHOC_RUNTIME_SIGNING_FRAMEWORK_PRODUCTS = (
    "app",
    "helper-app",
    "helper-renderer-app",
    "helper-gpu-app",
    "helper-alerts",
    "app-mode-app",
    "web-app-shortcut-copier",
)
ADHOC_RUNTIME_SIGNING_PROVENANCE = {
    "apple_entitlement": (
        "https://developer.apple.com/documentation/bundleresources/"
        "entitlements/com.apple.security.cs.disable-library-validation"
    ),
    "chromium_ad_hoc_precedent": (
        "chrome/browser/web_applications/os_integration/mac/"
        "web_app_shortcut_creator.mm"
    ),
    "identity": "-",
}

LINKEDIT_RECOVERY_ROOT = "out/FocusMacXcode27LinkeditRecovery"
LINKEDIT_RECOVERY_PARTIAL = "out/.FocusMacXcode27LinkeditRecovery.part"
LINKEDIT_RECOVERY_MANIFEST = LINKEDIT_RECOVERY_ROOT + "/manifest.json"
LINKEDIT_RECOVERY_LEGACY_ARTIFACTS = {
    STAGED_ARM_APP: {
        "kind": "tree",
        "sha256": "76d04d6d126c692dedfd2a50e83356d3fb4ce6e17eb151fe1698fcb372221461",
    },
    STAGE_RECEIPT: {
        "kind": "file",
        "sha256": "7b377abffb9a70a5405ca5bcae5918b2bbeafa2f79830d23a4707f8243f11137",
    },
    RECLAIM_RECEIPT: {
        "kind": "file",
        "sha256": "0c32a715f40f0657d3992130beb147751da4b68775e95d77fae94fbc6a74aaa5",
    },
    X64_OUT + "/" + APP_NAME: {
        "kind": "tree",
        "sha256": "0f490737f4c5806441ea538a5cfc73ffc55adac650ef2cb8e949f3b8bd3c411c",
    },
    X64_OUT + "/" + SLICE_RECEIPT_NAME: {
        "kind": "file",
        "sha256": "222a290e354fccb07bf1d64bac92664f53419c3d8d93923b67e3436ede2dab57",
    },
    SWIFTSHADER_DISABLED_SIGNING_RECEIPT: {
        "kind": "file",
        "sha256": "afddb96e4a2b6d6771a626ba05ba6a2bed166673c5df57f598e7b8841ac9ced3",
    },
}

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
PACKAGING_PYTHON_WRAPPER_RELATIVE = "depot_tools/python-bin/python3"
PACKAGING_PYTHON_WRAPPER_SHA256 = (
    "2d813fd16b24d1e466d6852b14f13552103262b706071b7066ef35143ff35348"
)
PACKAGING_PYTHON_RELDIR = (
    "bootstrap-2@3.11.8.chromium.35_bin/python3/bin"
)
PACKAGING_PYTHON_RELDIR_SHA256 = (
    "616f672408066fd076e3fdbfa7d506d7b765a7249035ea32e08860b0f1644842"
)
PACKAGING_PYTHON_VERSION = (3, 11, 8)
PACKAGING_PYTHON_CIPD_VERSION = "version:2@3.11.8.chromium.35"
PACKAGING_PYTHON_SHA256_BY_HOST = {
    "arm64": "a4472b6fd8d81757bf30ded029f75902d4563a94a07fd6ff587e1788537c3fad",
}
PACKAGING_PYTHON_CIPD_INSTANCE_BY_HOST = {
    "arm64": "dl9bUIZGl7f8pb51Mu3HiNfyngIPmCaS-aoII_eAklUC",
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


def best_effort_remove_tree(path):
    """Remove rollback scratch without turning a committed result into failure."""
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return True


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


def packaging_python_contract(source):
    """Bind packaging to Chromium's bootstrapped Python with TaskGroup support."""
    machine = platform.machine().lower()
    if machine == "aarch64":
        machine = "arm64"
    elif machine == "amd64":
        machine = "x86_64"
    if machine not in PACKAGING_PYTHON_SHA256_BY_HOST:
        raise PipelineError(
            "unsupported Mac host architecture for packaging Python: {}".format(
                machine
            )
        )
    depot = source.parent / "depot_tools"
    wrapper = source.parent / PACKAGING_PYTHON_WRAPPER_RELATIVE
    if wrapper.is_symlink() or not wrapper.is_file() or not os.access(str(wrapper), os.X_OK):
        raise PipelineError("packaging Python wrapper is not a regular executable")
    if sha256_file(wrapper) != PACKAGING_PYTHON_WRAPPER_SHA256:
        raise PipelineError("packaging Python wrapper hash mismatch")
    reldir_file = depot / "python3_bin_reldir.txt"
    if reldir_file.is_symlink() or not reldir_file.is_file():
        raise PipelineError("packaging Python relative-path marker is missing")
    if (
        sha256_file(reldir_file) != PACKAGING_PYTHON_RELDIR_SHA256
        or reldir_file.read_text(encoding="utf-8") != PACKAGING_PYTHON_RELDIR
    ):
        raise PipelineError("packaging Python relative-path marker mismatch")
    python_bin_dir = depot / PACKAGING_PYTHON_RELDIR
    bootstrap_root = python_bin_dir.parent.parent
    for path in (depot, bootstrap_root, bootstrap_root / "python3", python_bin_dir):
        if path.is_symlink() or not path.is_dir():
            raise PipelineError("packaging Python path is unsafe: {}".format(path))
    executable = python_bin_dir / "python3.11"
    if executable.is_symlink() or not executable.is_file() or not os.access(
        str(executable), os.X_OK
    ):
        raise PipelineError("packaging Python is not a regular executable")
    observed_hash = sha256_file(executable)
    if observed_hash != PACKAGING_PYTHON_SHA256_BY_HOST[machine]:
        raise PipelineError("packaging Python executable hash mismatch")
    instance = PACKAGING_PYTHON_CIPD_INSTANCE_BY_HOST[machine]
    cipd_package = "infra/3pp/tools/cpython3/mac-{}".format(machine)
    cipd_slot = bootstrap_root / ".cipd/pkgs/0"
    description = load_json(
        cipd_slot / "description.json", "packaging Python CIPD description"
    )
    if description != {"subdir": "python3", "package_name": cipd_package}:
        raise PipelineError("packaging Python CIPD package mismatch")
    current = cipd_slot / "_current"
    if not current.is_symlink() or os.readlink(str(current)) != instance:
        raise PipelineError("packaging Python CIPD instance mismatch")
    instance_dir = cipd_slot / instance
    if instance_dir.is_symlink() or not instance_dir.is_dir():
        raise PipelineError("packaging Python CIPD instance is missing")
    environment = {
        "PATH": SYSTEM_PATH,
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    architectures = capture(
        ["/usr/bin/lipo", "-archs", str(executable)], source, environment
    ).split()
    if architectures != [machine]:
        raise PipelineError("packaging Python architecture mismatch")
    probe = capture(
        [
            str(executable),
            "-c",
            (
                "import asyncio,json,platform,sys;"
                "print(json.dumps({'machine':platform.machine().lower(),"
                "'task_group':hasattr(asyncio,'TaskGroup'),"
                "'version':list(sys.version_info[:3])},sort_keys=True))"
            ),
        ],
        source,
        environment,
    )
    try:
        identity = json.loads(probe)
    except json.JSONDecodeError as exc:
        raise PipelineError("packaging Python identity is not JSON") from exc
    if identity != {
        "machine": machine,
        "task_group": True,
        "version": list(PACKAGING_PYTHON_VERSION),
    }:
        raise PipelineError("packaging Python identity mismatch")
    return {
        "path": str(executable),
        "wrapper": str(wrapper),
        "wrapper_sha256": PACKAGING_PYTHON_WRAPPER_SHA256,
        "architecture": machine,
        "version": ".".join(str(value) for value in PACKAGING_PYTHON_VERSION),
        "sha256": observed_hash,
        "cipd_package": cipd_package,
        "cipd_version": PACKAGING_PYTHON_CIPD_VERSION,
        "cipd_instance": instance,
        "asyncio_task_group": True,
    }


def developer_contract(value):
    try:
        return focus_macos.validate_xcode_toolchain(value)
    except focus_macos.ContractError as exc:
        raise PipelineError(str(exc)) from exc


def gn_compat_is_required(source, allow_missing_arm=False):
    """Return whether both prepared profiles disable the guarded features."""
    states = []
    for relative in (ARM_OUT + "/args.gn", X64_OUT + "/args.gn"):
        path = in_source(source, relative, "GN compatibility args")
        if not path.is_file() and allow_missing_arm and relative.startswith(ARM_OUT + "/"):
            continue
        if path.is_symlink() or not path.is_file():
            raise PipelineError("missing GN compatibility args: {}".format(path))
        text = path.read_text(encoding="utf-8")
        swiftshader = text.count("enable_swiftshader=false")
        safe_browsing = text.count("safe_browsing_mode=0")
        if swiftshader not in (0, 1) or safe_browsing not in (0, 1):
            raise PipelineError("GN compatibility flags are duplicated")
        if bool(swiftshader) != bool(safe_browsing):
            raise PipelineError("GN compatibility flags are only partially disabled")
        states.append(bool(swiftshader))
    if len(states) == 2 and states[0] != states[1]:
        raise PipelineError("arm64/x64 GN compatibility flags differ")
    if not states:
        raise PipelineError("no GN compatibility args were available")
    return states[0]


def gn_compat_receipt_contract(source, preparation_receipt_path, required=True):
    """Validate the audited post-preparation fix for disabled GN features."""
    receipt_path = in_source(source, GN_COMPAT_RECEIPT, "GN compatibility receipt")
    if not receipt_path.exists():
        if required:
            raise PipelineError("GN compatibility receipt is required")
        return None
    receipt = load_json(receipt_path, "GN compatibility receipt")
    expected_keys = {
        "schema",
        "source_root",
        "preparation_receipt",
        "patch",
        "files",
        "offline",
        "network_operations",
        "build_executed",
        "signing_executed",
        "packaging_executed",
    }
    if set(receipt) != expected_keys or receipt.get("schema") != 1:
        raise PipelineError("GN compatibility receipt schema mismatch")
    expected_preparation = {
        "path": str(preparation_receipt_path),
        "sha256": sha256_file(preparation_receipt_path),
    }
    expected_patch = {
        "path": str(GN_COMPAT_PATCH),
        "sha256": GN_COMPAT_PATCH_SHA256,
    }
    if (
        receipt.get("source_root") != str(source)
        or receipt.get("preparation_receipt") != expected_preparation
        or receipt.get("patch") != expected_patch
        or receipt.get("files") != GN_COMPAT_FILES
        or receipt.get("offline") is not True
        or receipt.get("network_operations") != 0
        or receipt.get("build_executed") is not False
        or receipt.get("signing_executed") is not False
        or receipt.get("packaging_executed") is not False
    ):
        raise PipelineError("GN compatibility provenance mismatch")
    if sha256_file(GN_COMPAT_PATCH) != GN_COMPAT_PATCH_SHA256:
        raise PipelineError("GN compatibility patch hash mismatch")
    for relative, hashes in GN_COMPAT_FILES.items():
        current = in_source(
            source, relative, "GN compatibility source", must_exist=True
        )
        if sha256_file(current) != hashes["post_sha256"]:
            raise PipelineError(
                "GN compatibility source hash mismatch: {}".format(relative)
            )
    return receipt_path, receipt


def gn_compat_plan(source):
    """Validate the exact pre-fix state without changing the checkout."""
    preparation_path, _ = preparation_contract(
        source, allow_missing_gn_compat=True
    )
    if not gn_compat_is_required(source):
        raise PipelineError("prepared profiles do not require the GN compatibility fix")
    receipt_path = in_source(source, GN_COMPAT_RECEIPT, "GN compatibility receipt")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PipelineError("GN compatibility receipt already exists")
    if GN_COMPAT_PATCH.is_symlink() or not GN_COMPAT_PATCH.is_file():
        raise PipelineError("GN compatibility patch is not a regular file")
    if sha256_file(GN_COMPAT_PATCH) != GN_COMPAT_PATCH_SHA256:
        raise PipelineError("GN compatibility patch hash mismatch")
    files = {}
    for relative, hashes in GN_COMPAT_FILES.items():
        path = in_source(source, relative, "GN compatibility source", must_exist=True)
        observed = sha256_file(path)
        if observed != hashes["pre_sha256"]:
            raise PipelineError(
                "GN compatibility pre-fix hash mismatch: {}".format(relative)
            )
        files[relative] = dict(hashes)
    try:
        boundary = prepare_source.check_patch_boundary(source, GN_COMPAT_PATCH)
    except prepare_source.PreparationError as exc:
        raise PipelineError(str(exc)) from exc
    return {
        "stage": "apply-gn-compat",
        "source_root": str(source),
        "preparation_receipt": {
            "path": str(preparation_path),
            "sha256": sha256_file(preparation_path),
        },
        "patch": boundary,
        "files": files,
        "receipt": str(receipt_path),
        "offline": True,
        "network_operations": 0,
    }


def execute_gn_compat(source, plan):
    """Apply the three-file GN fix transactionally and publish its receipt."""
    expected = gn_compat_plan(source)
    if plan != expected:
        raise PipelineError("GN compatibility plan changed before execution")
    require_free(source, SOFT_FLOOR_GIB, "GN compatibility fix")
    snapshot_root = Path(tempfile.mkdtemp(prefix="focus-gn-compat-rollback-")).resolve()
    backups = {}
    try:
        for position, relative in enumerate(GN_COMPAT_FILES, 1):
            current = in_source(
                source, relative, "GN compatibility snapshot", must_exist=True
            )
            backup = snapshot_root / "{:02d}.backup".format(position)
            prepare_source.atomic_copy(current, backup)
            backups[relative] = backup
        prepare_source.apply_patch_plan(source, [GN_COMPAT_PATCH], total_patches=1)
        for relative, hashes in GN_COMPAT_FILES.items():
            current = in_source(
                source, relative, "GN compatibility result", must_exist=True
            )
            if sha256_file(current) != hashes["post_sha256"]:
                raise PipelineError(
                    "GN compatibility post-fix hash mismatch: {}".format(relative)
                )
        receipt_value = {
            "schema": 1,
            "source_root": str(source),
            "preparation_receipt": expected["preparation_receipt"],
            "patch": {
                "path": str(GN_COMPAT_PATCH),
                "sha256": GN_COMPAT_PATCH_SHA256,
            },
            "files": GN_COMPAT_FILES,
            "offline": True,
            "network_operations": 0,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
        }
        receipt_report = atomic_json(expected["receipt"], receipt_value)
        gn_compat_receipt_contract(
            source, Path(expected["preparation_receipt"]["path"]), required=True
        )
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            if receipt_path.is_symlink() or (
                receipt_path.exists() and not receipt_path.is_file()
            ):
                raise PipelineError("unsafe GN compatibility receipt during rollback")
            if receipt_path.is_file():
                receipt_path.unlink()
            for relative, backup in backups.items():
                target = in_source(
                    source, relative, "GN compatibility rollback", must_exist=True
                )
                prepare_source.atomic_copy(backup, target)
                if sha256_file(target) != GN_COMPAT_FILES[relative]["pre_sha256"]:
                    raise PipelineError(
                        "GN compatibility rollback hash mismatch: {}".format(relative)
                    )
        except BaseException as rollback_error:
            raise PipelineError(
                "GN compatibility fix failed and rollback failed; snapshot retained "
                "at {}: original={!r}; rollback={!r}".format(
                    snapshot_root, original_error, rollback_error
                )
            ) from original_error
        shutil.rmtree(snapshot_root)
        if isinstance(original_error, prepare_source.PreparationError):
            raise PipelineError(str(original_error)) from original_error
        raise
    else:
        shutil.rmtree(snapshot_root)
    return {
        "stage": "apply-gn-compat",
        "applied": True,
        "receipt": receipt_report,
        "files": GN_COMPAT_FILES,
        "offline": True,
        "network_operations": 0,
        "build_executed": False,
    }


def xcode27_toolchain_identity(report):
    """Keep only the immutable Xcode/SDK fields relevant to this backport."""
    try:
        return {
            "xcode": {
                "version": report["xcode"]["version"],
                "build": report["xcode"]["build"],
            },
            "sdk": {
                "version": report["sdk"]["version"],
                "build": report["sdk"]["build"],
                "minimum_deployment_target": report["sdk"][
                    "minimum_deployment_target"
                ],
                "architectures": report["sdk"]["architectures"],
            },
        }
    except (KeyError, TypeError) as exc:
        raise PipelineError("Xcode 27 toolchain identity is incomplete") from exc


def xcode27_provenance_links(
    source, developer_dir=None, allow_reclaimed_arm=False
):
    """Resolve the exact preparation, optional GN fix, and tool receipts."""
    preparation_path, _ = preparation_contract(
        source, allow_reclaimed_arm=allow_reclaimed_arm
    )
    tool_path, _ = tool_receipt_contract(source, developer_dir)
    gn_path = in_source(source, GN_COMPAT_RECEIPT, "GN compatibility receipt")
    if gn_path.exists():
        gn_path, _ = gn_compat_receipt_contract(
            source, preparation_path, required=True
        )
        gn_link = {"path": str(gn_path), "sha256": sha256_file(gn_path)}
    else:
        gn_link = None
    return {
        "preparation_receipt": {
            "path": str(preparation_path),
            "sha256": sha256_file(preparation_path),
        },
        "gn_compatibility_receipt": gn_link,
        "tool_bootstrap_receipt": {
            "path": str(tool_path),
            "sha256": sha256_file(tool_path),
        },
    }


def xcode27_compat_receipt_contract(
    source, developer_dir=None, required=True, allow_reclaimed_arm=False
):
    """Validate the exact upstream explicit-module fix for Xcode 27."""
    receipt_path = in_source(
        source, XCODE27_COMPAT_RECEIPT, "Xcode 27 compatibility receipt"
    )
    if not receipt_path.exists():
        if required:
            raise PipelineError("Xcode 27 compatibility receipt is required")
        return None
    receipt = load_json(receipt_path, "Xcode 27 compatibility receipt")
    expected_keys = {
        "schema",
        "source_root",
        "preparation_receipt",
        "gn_compatibility_receipt",
        "tool_bootstrap_receipt",
        "toolchain",
        "upstream",
        "patch",
        "files",
        "offline",
        "network_operations",
        "build_executed",
        "signing_executed",
        "packaging_executed",
    }
    links = xcode27_provenance_links(
        source, developer_dir, allow_reclaimed_arm=allow_reclaimed_arm
    )
    if set(receipt) != expected_keys or receipt.get("schema") != 1:
        raise PipelineError("Xcode 27 compatibility receipt schema mismatch")
    if (
        receipt.get("source_root") != str(source)
        or receipt.get("preparation_receipt") != links["preparation_receipt"]
        or receipt.get("gn_compatibility_receipt")
        != links["gn_compatibility_receipt"]
        or receipt.get("tool_bootstrap_receipt")
        != links["tool_bootstrap_receipt"]
        or receipt.get("toolchain") != XCODE27_COMPAT_TOOLCHAIN
        or receipt.get("upstream") != XCODE27_COMPAT_UPSTREAM
        or receipt.get("patch")
        != {
            "path": str(XCODE27_COMPAT_PATCH),
            "sha256": XCODE27_COMPAT_PATCH_SHA256,
        }
        or receipt.get("files") != XCODE27_COMPAT_FILES
        or receipt.get("offline") is not True
        or receipt.get("network_operations") != 0
        or receipt.get("build_executed") is not False
        or receipt.get("signing_executed") is not False
        or receipt.get("packaging_executed") is not False
    ):
        raise PipelineError("Xcode 27 compatibility provenance mismatch")
    if sha256_file(XCODE27_COMPAT_PATCH) != XCODE27_COMPAT_PATCH_SHA256:
        raise PipelineError("Xcode 27 compatibility patch hash mismatch")
    for relative, hashes in XCODE27_COMPAT_FILES.items():
        current = in_source(
            source, relative, "Xcode 27 compatibility source", must_exist=True
        )
        if sha256_file(current) != hashes["post_sha256"]:
            raise PipelineError(
                "Xcode 27 compatibility source hash mismatch: {}".format(relative)
            )
    return receipt_path, receipt


def xcode27_compat_plan(source, developer_dir):
    """Validate the exact pre-backport state without changing the checkout."""
    links = xcode27_provenance_links(source, developer_dir)
    identity = xcode27_toolchain_identity(developer_contract(developer_dir))
    if identity != XCODE27_COMPAT_TOOLCHAIN:
        raise PipelineError("Xcode 27 compatibility toolchain mismatch")
    receipt_path = in_source(
        source, XCODE27_COMPAT_RECEIPT, "Xcode 27 compatibility receipt"
    )
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PipelineError("Xcode 27 compatibility receipt already exists")
    if XCODE27_COMPAT_PATCH.is_symlink() or not XCODE27_COMPAT_PATCH.is_file():
        raise PipelineError("Xcode 27 compatibility patch is not a regular file")
    if sha256_file(XCODE27_COMPAT_PATCH) != XCODE27_COMPAT_PATCH_SHA256:
        raise PipelineError("Xcode 27 compatibility patch hash mismatch")
    files = {}
    for relative, hashes in XCODE27_COMPAT_FILES.items():
        path = in_source(
            source, relative, "Xcode 27 compatibility source", must_exist=True
        )
        if sha256_file(path) != hashes["pre_sha256"]:
            raise PipelineError(
                "Xcode 27 compatibility pre-fix hash mismatch: {}".format(relative)
            )
        files[relative] = dict(hashes)
    try:
        boundary = prepare_source.check_patch_boundary(source, XCODE27_COMPAT_PATCH)
    except prepare_source.PreparationError as exc:
        raise PipelineError(str(exc)) from exc
    return {
        "stage": "apply-xcode27-compat",
        "source_root": str(source),
        **links,
        "toolchain": identity,
        "upstream": XCODE27_COMPAT_UPSTREAM,
        "patch": boundary,
        "files": files,
        "receipt": str(receipt_path),
        "offline": True,
        "network_operations": 0,
    }


def execute_xcode27_compat(source, developer_dir, plan):
    """Backport the one-file upstream Xcode 27 fix transactionally."""
    expected = xcode27_compat_plan(source, developer_dir)
    if plan != expected:
        raise PipelineError("Xcode 27 compatibility plan changed before execution")
    require_free(source, SOFT_FLOOR_GIB, "Xcode 27 compatibility fix")
    snapshot_root = Path(
        tempfile.mkdtemp(prefix="focus-xcode27-compat-rollback-")
    ).resolve()
    backups = {}
    try:
        for position, relative in enumerate(XCODE27_COMPAT_FILES, 1):
            current = in_source(
                source, relative, "Xcode 27 compatibility snapshot", must_exist=True
            )
            backup = snapshot_root / "{:02d}.backup".format(position)
            prepare_source.atomic_copy(current, backup)
            backups[relative] = backup
        prepare_source.apply_patch_plan(
            source, [XCODE27_COMPAT_PATCH], total_patches=1
        )
        for relative, hashes in XCODE27_COMPAT_FILES.items():
            current = in_source(
                source, relative, "Xcode 27 compatibility result", must_exist=True
            )
            if sha256_file(current) != hashes["post_sha256"]:
                raise PipelineError(
                    "Xcode 27 compatibility post-fix hash mismatch: {}".format(
                        relative
                    )
                )
        receipt_value = {
            "schema": 1,
            "source_root": str(source),
            "preparation_receipt": expected["preparation_receipt"],
            "gn_compatibility_receipt": expected["gn_compatibility_receipt"],
            "tool_bootstrap_receipt": expected["tool_bootstrap_receipt"],
            "toolchain": XCODE27_COMPAT_TOOLCHAIN,
            "upstream": XCODE27_COMPAT_UPSTREAM,
            "patch": {
                "path": str(XCODE27_COMPAT_PATCH),
                "sha256": XCODE27_COMPAT_PATCH_SHA256,
            },
            "files": XCODE27_COMPAT_FILES,
            "offline": True,
            "network_operations": 0,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
        }
        receipt_report = atomic_json(expected["receipt"], receipt_value)
        xcode27_compat_receipt_contract(source, developer_dir, required=True)
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            if receipt_path.is_symlink() or (
                receipt_path.exists() and not receipt_path.is_file()
            ):
                raise PipelineError(
                    "unsafe Xcode 27 compatibility receipt during rollback"
                )
            if receipt_path.is_file():
                receipt_path.unlink()
            for relative, backup in backups.items():
                target = in_source(
                    source,
                    relative,
                    "Xcode 27 compatibility rollback",
                    must_exist=True,
                )
                prepare_source.atomic_copy(backup, target)
                if sha256_file(target) != XCODE27_COMPAT_FILES[relative][
                    "pre_sha256"
                ]:
                    raise PipelineError(
                        "Xcode 27 compatibility rollback hash mismatch: {}".format(
                            relative
                        )
                    )
        except BaseException as rollback_error:
            raise PipelineError(
                "Xcode 27 compatibility fix failed and rollback failed; "
                "snapshot retained at {}: original={!r}; rollback={!r}".format(
                    snapshot_root, original_error, rollback_error
                )
            ) from original_error
        shutil.rmtree(snapshot_root)
        if isinstance(original_error, prepare_source.PreparationError):
            raise PipelineError(str(original_error)) from original_error
        raise
    else:
        shutil.rmtree(snapshot_root)
    return {
        "stage": "apply-xcode27-compat",
        "applied": True,
        "receipt": receipt_report,
        "files": XCODE27_COMPAT_FILES,
        "upstream": XCODE27_COMPAT_UPSTREAM,
        "offline": True,
        "network_operations": 0,
        "build_executed": False,
    }


def xcode27_seatbelt_provenance_link(
    source, developer_dir=None, allow_reclaimed_arm=False
):
    """Bind the sandbox fix to the already-validated module compatibility fix."""
    receipt_path, _ = xcode27_compat_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
    return {
        "path": str(receipt_path),
        "sha256": sha256_file(receipt_path),
    }


def xcode27_seatbelt_receipt_contract(
    source, developer_dir=None, required=True, allow_reclaimed_arm=False
):
    """Validate Chromium's exact macOS 27 Seatbelt source backport."""
    receipt_path = in_source(
        source, XCODE27_SEATBELT_RECEIPT, "Xcode 27 Seatbelt receipt"
    )
    if not receipt_path.exists():
        if required:
            raise PipelineError("Xcode 27 Seatbelt compatibility receipt is required")
        return None
    receipt = load_json(receipt_path, "Xcode 27 Seatbelt compatibility receipt")
    expected_keys = {
        "schema",
        "source_root",
        "xcode27_module_compatibility_receipt",
        "toolchain",
        "upstream",
        "patch",
        "files",
        "offline",
        "network_operations",
        "build_executed",
        "signing_executed",
        "packaging_executed",
    }
    module_link = xcode27_seatbelt_provenance_link(
        source, developer_dir, allow_reclaimed_arm=allow_reclaimed_arm
    )
    if set(receipt) != expected_keys or receipt.get("schema") != 1:
        raise PipelineError("Xcode 27 Seatbelt compatibility receipt schema mismatch")
    if (
        receipt.get("source_root") != str(source)
        or receipt.get("xcode27_module_compatibility_receipt") != module_link
        or receipt.get("toolchain") != XCODE27_COMPAT_TOOLCHAIN
        or receipt.get("upstream") != XCODE27_SEATBELT_UPSTREAM
        or receipt.get("patch")
        != {
            "path": str(XCODE27_SEATBELT_PATCH),
            "sha256": XCODE27_SEATBELT_PATCH_SHA256,
        }
        or receipt.get("files") != XCODE27_SEATBELT_FILES
        or receipt.get("offline") is not True
        or receipt.get("network_operations") != 0
        or receipt.get("build_executed") is not False
        or receipt.get("signing_executed") is not False
        or receipt.get("packaging_executed") is not False
    ):
        raise PipelineError("Xcode 27 Seatbelt compatibility provenance mismatch")
    if sha256_file(XCODE27_SEATBELT_PATCH) != XCODE27_SEATBELT_PATCH_SHA256:
        raise PipelineError("Xcode 27 Seatbelt compatibility patch hash mismatch")
    for relative, hashes in XCODE27_SEATBELT_FILES.items():
        current = in_source(
            source, relative, "Xcode 27 Seatbelt source", must_exist=True
        )
        if sha256_file(current) != hashes["post_sha256"]:
            raise PipelineError(
                "Xcode 27 Seatbelt source hash mismatch: {}".format(relative)
            )
    return receipt_path, receipt


def xcode27_seatbelt_plan(source, developer_dir):
    """Validate the exact pre-backport Seatbelt state without mutation."""
    module_link = xcode27_seatbelt_provenance_link(source, developer_dir)
    identity = xcode27_toolchain_identity(developer_contract(developer_dir))
    if identity != XCODE27_COMPAT_TOOLCHAIN:
        raise PipelineError("Xcode 27 Seatbelt compatibility toolchain mismatch")
    receipt_path = in_source(
        source, XCODE27_SEATBELT_RECEIPT, "Xcode 27 Seatbelt receipt"
    )
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PipelineError("Xcode 27 Seatbelt compatibility receipt already exists")
    if XCODE27_SEATBELT_PATCH.is_symlink() or not XCODE27_SEATBELT_PATCH.is_file():
        raise PipelineError("Xcode 27 Seatbelt patch is not a regular file")
    if sha256_file(XCODE27_SEATBELT_PATCH) != XCODE27_SEATBELT_PATCH_SHA256:
        raise PipelineError("Xcode 27 Seatbelt compatibility patch hash mismatch")
    files = {}
    for relative, hashes in XCODE27_SEATBELT_FILES.items():
        path = in_source(
            source, relative, "Xcode 27 Seatbelt source", must_exist=True
        )
        if sha256_file(path) != hashes["pre_sha256"]:
            raise PipelineError(
                "Xcode 27 Seatbelt pre-fix hash mismatch: {}".format(relative)
            )
        files[relative] = dict(hashes)
    try:
        boundary = prepare_source.check_patch_boundary(source, XCODE27_SEATBELT_PATCH)
    except prepare_source.PreparationError as exc:
        raise PipelineError(str(exc)) from exc
    return {
        "stage": "apply-xcode27-seatbelt-compat",
        "source_root": str(source),
        "xcode27_module_compatibility_receipt": module_link,
        "toolchain": identity,
        "upstream": XCODE27_SEATBELT_UPSTREAM,
        "patch": boundary,
        "files": files,
        "receipt": str(receipt_path),
        "offline": True,
        "network_operations": 0,
    }


def execute_xcode27_seatbelt(source, developer_dir, plan):
    """Remove the obsolete SDK symbol dependency with transactional rollback."""
    expected = xcode27_seatbelt_plan(source, developer_dir)
    if plan != expected:
        raise PipelineError("Xcode 27 Seatbelt plan changed before execution")
    require_free(source, SOFT_FLOOR_GIB, "Xcode 27 Seatbelt compatibility fix")
    snapshot_root = Path(
        tempfile.mkdtemp(prefix="focus-xcode27-seatbelt-rollback-")
    ).resolve()
    backups = {}
    try:
        for position, relative in enumerate(XCODE27_SEATBELT_FILES, 1):
            current = in_source(
                source, relative, "Xcode 27 Seatbelt snapshot", must_exist=True
            )
            backup = snapshot_root / "{:02d}.backup".format(position)
            prepare_source.atomic_copy(current, backup)
            backups[relative] = backup
        prepare_source.apply_patch_plan(
            source, [XCODE27_SEATBELT_PATCH], total_patches=1
        )
        for relative, hashes in XCODE27_SEATBELT_FILES.items():
            current = in_source(
                source, relative, "Xcode 27 Seatbelt result", must_exist=True
            )
            if sha256_file(current) != hashes["post_sha256"]:
                raise PipelineError(
                    "Xcode 27 Seatbelt post-fix hash mismatch: {}".format(relative)
                )
        receipt_value = {
            "schema": 1,
            "source_root": str(source),
            "xcode27_module_compatibility_receipt": expected[
                "xcode27_module_compatibility_receipt"
            ],
            "toolchain": XCODE27_COMPAT_TOOLCHAIN,
            "upstream": XCODE27_SEATBELT_UPSTREAM,
            "patch": {
                "path": str(XCODE27_SEATBELT_PATCH),
                "sha256": XCODE27_SEATBELT_PATCH_SHA256,
            },
            "files": XCODE27_SEATBELT_FILES,
            "offline": True,
            "network_operations": 0,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
        }
        receipt_report = atomic_json(expected["receipt"], receipt_value)
        xcode27_seatbelt_receipt_contract(source, developer_dir, required=True)
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            if receipt_path.is_symlink() or (
                receipt_path.exists() and not receipt_path.is_file()
            ):
                raise PipelineError(
                    "unsafe Xcode 27 Seatbelt receipt during rollback"
                )
            if receipt_path.is_file():
                receipt_path.unlink()
            for relative, backup in backups.items():
                target = in_source(
                    source, relative, "Xcode 27 Seatbelt rollback", must_exist=True
                )
                prepare_source.atomic_copy(backup, target)
                if sha256_file(target) != XCODE27_SEATBELT_FILES[relative][
                    "pre_sha256"
                ]:
                    raise PipelineError(
                        "Xcode 27 Seatbelt rollback hash mismatch: {}".format(
                            relative
                        )
                    )
        except BaseException as rollback_error:
            raise PipelineError(
                "Xcode 27 Seatbelt fix failed and rollback failed; snapshot "
                "retained at {}: original={!r}; rollback={!r}".format(
                    snapshot_root, original_error, rollback_error
                )
            ) from original_error
        shutil.rmtree(snapshot_root)
        if isinstance(original_error, prepare_source.PreparationError):
            raise PipelineError(str(original_error)) from original_error
        raise
    else:
        shutil.rmtree(snapshot_root)
    return {
        "stage": "apply-xcode27-seatbelt-compat",
        "applied": True,
        "receipt": receipt_report,
        "files": XCODE27_SEATBELT_FILES,
        "upstream": XCODE27_SEATBELT_UPSTREAM,
        "offline": True,
        "network_operations": 0,
        "build_executed": False,
    }


def screen_ai_disabled_provenance_link(
    source, developer_dir=None, allow_reclaimed_arm=False
):
    """Bind the disabled-ScreenAI guard to validated toolchain fixes."""
    receipt_path, _ = xcode27_seatbelt_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
    return {"path": str(receipt_path), "sha256": sha256_file(receipt_path)}


def screen_ai_disabled_config_contract(source):
    """Pin the Focus profile input that resolves ScreenAI to disabled."""
    observed = {}
    for relative, expected_hash in SCREEN_AI_DISABLED_CONFIG_FILES.items():
        path = in_source(
            source, relative, "disabled ScreenAI config", must_exist=True
        )
        if sha256_file(path) != expected_hash:
            raise PipelineError(
                "disabled ScreenAI config hash mismatch: {}".format(relative)
            )
        text = path.read_text(encoding="utf-8")
        if text.count("enable_screen_ai_service = false") != 1:
            raise PipelineError("ScreenAI is not explicitly disabled")
        observed[relative] = expected_hash
    return observed


def screen_ai_disabled_receipt_contract(
    source, developer_dir=None, required=True, allow_reclaimed_arm=False
):
    """Validate the exact disabled-ScreenAI macOS link compatibility fix."""
    receipt_path = in_source(
        source, SCREEN_AI_DISABLED_RECEIPT, "disabled ScreenAI receipt"
    )
    if not receipt_path.exists():
        if required:
            raise PipelineError("disabled ScreenAI compatibility receipt is required")
        return None
    receipt = load_json(receipt_path, "disabled ScreenAI compatibility receipt")
    legacy_keys = {
        "schema",
        "source_root",
        "xcode27_seatbelt_compatibility_receipt",
        "upstream",
        "patch",
        "files",
        "enable_screen_ai_service",
        "offline",
        "network_operations",
        "build_executed",
        "signing_executed",
        "packaging_executed",
    }
    current_keys = legacy_keys | {"config_files"}
    seatbelt_link = screen_ai_disabled_provenance_link(
        source, developer_dir, allow_reclaimed_arm=allow_reclaimed_arm
    )
    legacy_receipt = (
        receipt.get("schema") == 1
        and receipt.get("upstream") == SCREEN_AI_DISABLED_LEGACY_UPSTREAM
    )
    current_receipt = (
        receipt.get("schema") == SCREEN_AI_DISABLED_RECEIPT_SCHEMA
        and receipt.get("upstream") == SCREEN_AI_DISABLED_UPSTREAM
    )
    if not (
        (legacy_receipt and set(receipt) == legacy_keys)
        or (current_receipt and set(receipt) == current_keys)
    ):
        raise PipelineError("disabled ScreenAI compatibility receipt schema mismatch")
    config_files = screen_ai_disabled_config_contract(source)
    if (
        receipt.get("source_root") != str(source)
        or receipt.get("xcode27_seatbelt_compatibility_receipt") != seatbelt_link
        or receipt.get("patch")
        != {
            "path": str(SCREEN_AI_DISABLED_PATCH),
            "sha256": SCREEN_AI_DISABLED_PATCH_SHA256,
        }
        or receipt.get("files") != SCREEN_AI_DISABLED_FILES
        or (current_receipt and receipt.get("config_files") != config_files)
        or receipt.get("enable_screen_ai_service") is not False
        or receipt.get("offline") is not True
        or receipt.get("network_operations") != 0
        or receipt.get("build_executed") is not False
        or receipt.get("signing_executed") is not False
        or receipt.get("packaging_executed") is not False
    ):
        raise PipelineError("disabled ScreenAI compatibility provenance mismatch")
    if sha256_file(SCREEN_AI_DISABLED_PATCH) != SCREEN_AI_DISABLED_PATCH_SHA256:
        raise PipelineError("disabled ScreenAI compatibility patch hash mismatch")
    for relative, hashes in SCREEN_AI_DISABLED_FILES.items():
        current = in_source(
            source, relative, "disabled ScreenAI source", must_exist=True
        )
        if sha256_file(current) != hashes["post_sha256"]:
            raise PipelineError(
                "disabled ScreenAI source hash mismatch: {}".format(relative)
            )
    return receipt_path, receipt


def screen_ai_disabled_plan(source, developer_dir):
    """Validate the exact pre-fix caller and patch boundary without mutation."""
    seatbelt_link = screen_ai_disabled_provenance_link(source, developer_dir)
    receipt_path = in_source(
        source, SCREEN_AI_DISABLED_RECEIPT, "disabled ScreenAI receipt"
    )
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PipelineError("disabled ScreenAI compatibility receipt already exists")
    if SCREEN_AI_DISABLED_PATCH.is_symlink() or not SCREEN_AI_DISABLED_PATCH.is_file():
        raise PipelineError("disabled ScreenAI patch is not a regular file")
    if sha256_file(SCREEN_AI_DISABLED_PATCH) != SCREEN_AI_DISABLED_PATCH_SHA256:
        raise PipelineError("disabled ScreenAI compatibility patch hash mismatch")
    files = {}
    source_states = set()
    for relative, hashes in SCREEN_AI_DISABLED_FILES.items():
        path = in_source(
            source, relative, "disabled ScreenAI source", must_exist=True
        )
        observed_hash = sha256_file(path)
        if observed_hash == hashes["pre_sha256"]:
            source_states.add("pre")
        elif observed_hash == hashes["post_sha256"]:
            source_states.add("post")
        else:
            raise PipelineError(
                "disabled ScreenAI source state mismatch: {}".format(relative)
            )
        files[relative] = dict(hashes)
    if len(source_states) != 1:
        raise PipelineError("disabled ScreenAI source state is mixed")
    source_state = source_states.pop()
    try:
        boundary = prepare_source.check_patch_boundary(
            source, SCREEN_AI_DISABLED_PATCH, reverse=(source_state == "post")
        )
    except prepare_source.PreparationError as exc:
        raise PipelineError(str(exc)) from exc
    return {
        "stage": "apply-screen-ai-disabled-compat",
        "source_root": str(source),
        "xcode27_seatbelt_compatibility_receipt": seatbelt_link,
        "upstream": SCREEN_AI_DISABLED_UPSTREAM,
        "patch": boundary,
        "files": files,
        "config_files": screen_ai_disabled_config_contract(source),
        "source_state": source_state,
        "enable_screen_ai_service": False,
        "receipt": str(receipt_path),
        "offline": True,
        "network_operations": 0,
    }


def execute_screen_ai_disabled(source, developer_dir, plan):
    """Guard the caller, or finalize an exact post-image after interruption."""
    expected = screen_ai_disabled_plan(source, developer_dir)
    if plan != expected:
        raise PipelineError("disabled ScreenAI plan changed before execution")
    require_free(source, SOFT_FLOOR_GIB, "disabled ScreenAI compatibility fix")
    snapshot_root = None
    backups = {}
    snapshot_cleanup_complete = True
    try:
        if expected["source_state"] == "pre":
            snapshot_root = Path(
                tempfile.mkdtemp(prefix="focus-screen-ai-disabled-rollback-")
            ).resolve()
            patch_snapshot = snapshot_root / "screen-ai-disabled-link.patch"
            prepare_source.atomic_copy(SCREEN_AI_DISABLED_PATCH, patch_snapshot)
            if sha256_file(patch_snapshot) != SCREEN_AI_DISABLED_PATCH_SHA256:
                raise PipelineError("disabled ScreenAI patch snapshot hash mismatch")
            try:
                prepare_source.check_patch_boundary(source, patch_snapshot)
            except prepare_source.PreparationError as exc:
                raise PipelineError(str(exc)) from exc
            for position, relative in enumerate(SCREEN_AI_DISABLED_FILES, 1):
                current = in_source(
                    source, relative, "disabled ScreenAI snapshot", must_exist=True
                )
                backup = snapshot_root / "{:02d}.backup".format(position)
                prepare_source.atomic_copy(current, backup)
                backups[relative] = backup
            prepare_source.apply_patch_plan(
                source, [patch_snapshot], total_patches=1
            )
        for relative, hashes in SCREEN_AI_DISABLED_FILES.items():
            current = in_source(
                source, relative, "disabled ScreenAI result", must_exist=True
            )
            if sha256_file(current) != hashes["post_sha256"]:
                raise PipelineError(
                    "disabled ScreenAI post-fix hash mismatch: {}".format(relative)
                )
        receipt_value = {
            "schema": SCREEN_AI_DISABLED_RECEIPT_SCHEMA,
            "source_root": str(source),
            "xcode27_seatbelt_compatibility_receipt": expected[
                "xcode27_seatbelt_compatibility_receipt"
            ],
            "upstream": SCREEN_AI_DISABLED_UPSTREAM,
            "patch": {
                "path": str(SCREEN_AI_DISABLED_PATCH),
                "sha256": SCREEN_AI_DISABLED_PATCH_SHA256,
            },
            "files": SCREEN_AI_DISABLED_FILES,
            "config_files": expected["config_files"],
            "enable_screen_ai_service": False,
            "offline": True,
            "network_operations": 0,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
        }
        receipt_report = atomic_json(expected["receipt"], receipt_value)
        screen_ai_disabled_receipt_contract(source, developer_dir, required=True)
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            if receipt_path.is_symlink() or (
                receipt_path.exists() and not receipt_path.is_file()
            ):
                raise PipelineError(
                    "unsafe disabled ScreenAI receipt during rollback"
                )
            if receipt_path.is_file():
                receipt_path.unlink()
            if expected["source_state"] == "pre":
                for relative, backup in backups.items():
                    target = in_source(
                        source,
                        relative,
                        "disabled ScreenAI rollback",
                        must_exist=True,
                    )
                    prepare_source.atomic_copy(backup, target)
                    if sha256_file(target) != SCREEN_AI_DISABLED_FILES[relative][
                        "pre_sha256"
                    ]:
                        raise PipelineError(
                            "disabled ScreenAI rollback hash mismatch: {}".format(
                                relative
                            )
                        )
        except BaseException as rollback_error:
            raise PipelineError(
                "disabled ScreenAI fix failed and rollback failed; snapshot "
                "retained at {}: original={!r}; rollback={!r}".format(
                    snapshot_root or "not-created", original_error, rollback_error
                )
            ) from original_error
        if snapshot_root is not None:
            best_effort_remove_tree(snapshot_root)
        if isinstance(original_error, prepare_source.PreparationError):
            raise PipelineError(str(original_error)) from original_error
        raise
    else:
        if snapshot_root is not None:
            snapshot_cleanup_complete = best_effort_remove_tree(snapshot_root)
    return {
        "stage": "apply-screen-ai-disabled-compat",
        "applied": True,
        "resumed_from_exact_post_image": expected["source_state"] == "post",
        "snapshot_cleanup_complete": snapshot_cleanup_complete,
        "receipt": receipt_report,
        "files": SCREEN_AI_DISABLED_FILES,
        "upstream": SCREEN_AI_DISABLED_UPSTREAM,
        "offline": True,
        "network_operations": 0,
        "build_executed": False,
    }


def xcode27_linkedit_strip_tool_contract(source, developer_dir):
    """Pin both the rejected LLVM strip and selected Xcode 27 strip."""
    developer_dir = Path(developer_dir).resolve(strict=True)
    identity = xcode27_toolchain_identity(developer_contract(developer_dir))
    if identity != XCODE27_COMPAT_TOOLCHAIN:
        raise PipelineError("Xcode 27 LINKEDIT strip toolchain mismatch")
    selected = developer_dir / XCODE27_LINKEDIT_STRIP_RELATIVE
    try:
        focus_macos.require_executable_file(
            selected, developer_dir, "Xcode 27 strip"
        )
    except focus_macos.ContractError as exc:
        raise PipelineError(str(exc)) from exc
    if sha256_file(selected) != XCODE27_LINKEDIT_STRIP_SHA256:
        raise PipelineError("Xcode 27 strip hash mismatch")

    llvm_bin = in_source(
        source,
        str(Path(BUNDLED_LLVM_STRIP_RELATIVE).parent),
        "bundled LLVM binary directory",
        must_exist=True,
        directory=True,
    )
    bundled_strip = llvm_bin / Path(BUNDLED_LLVM_STRIP_RELATIVE).name
    if (
        not bundled_strip.is_symlink()
        or os.readlink(str(bundled_strip)) != BUNDLED_LLVM_STRIP_SYMLINK_TARGET
    ):
        raise PipelineError("bundled llvm-strip symlink contract mismatch")
    bundled_objcopy = llvm_bin / BUNDLED_LLVM_STRIP_SYMLINK_TARGET
    if (
        bundled_objcopy.is_symlink()
        or not bundled_objcopy.is_file()
        or not os.access(str(bundled_objcopy), os.X_OK)
    ):
        raise PipelineError("bundled llvm-objcopy is not a regular executable")
    if sha256_file(bundled_objcopy) != BUNDLED_LLVM_STRIP_SHA256:
        raise PipelineError("bundled llvm-strip content hash mismatch")
    revision = in_source(
        source,
        BUNDLED_LLVM_REVISION_RELATIVE,
        "bundled LLVM revision",
        must_exist=True,
    )
    if (
        sha256_file(revision) != BUNDLED_LLVM_REVISION_SHA256
        or revision.read_text(encoding="utf-8")
        != XCODE27_LINKEDIT_STRIP_UPSTREAM["pinned_llvm_package_revision"] + "\n"
    ):
        raise PipelineError("bundled LLVM revision mismatch")
    return {
        "selected": {
            "path": str(selected),
            "relative_to_developer_dir": XCODE27_LINKEDIT_STRIP_RELATIVE,
            "sha256": XCODE27_LINKEDIT_STRIP_SHA256,
        },
        "replaced": {
            "path": str(bundled_strip),
            "relative_to_source": BUNDLED_LLVM_STRIP_RELATIVE,
            "symlink_target": BUNDLED_LLVM_STRIP_SYMLINK_TARGET,
            "resolved_path": str(bundled_objcopy),
            "sha256": BUNDLED_LLVM_STRIP_SHA256,
            "revision_path": str(revision),
            "revision_sha256": BUNDLED_LLVM_REVISION_SHA256,
            "revision": XCODE27_LINKEDIT_STRIP_UPSTREAM[
                "pinned_llvm_package_revision"
            ],
        },
    }


def xcode27_linkedit_strip_provenance_link(
    source, developer_dir=None, allow_reclaimed_arm=False
):
    """Bind the strip workaround to the last source compatibility receipt."""
    receipt_path, _ = screen_ai_disabled_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
    return {"path": str(receipt_path), "sha256": sha256_file(receipt_path)}


def xcode27_linkedit_strip_receipt_contract(
    source, developer_dir, required=True, allow_reclaimed_arm=False
):
    """Validate the exact Xcode-strip selection and its provenance."""
    receipt_path = in_source(
        source,
        XCODE27_LINKEDIT_STRIP_RECEIPT,
        "Xcode 27 LINKEDIT strip receipt",
    )
    if not receipt_path.exists():
        if required:
            raise PipelineError("Xcode 27 LINKEDIT strip receipt is required")
        return None
    receipt = load_json(receipt_path, "Xcode 27 LINKEDIT strip receipt")
    expected_keys = {
        "schema",
        "source_root",
        "screen_ai_disabled_compatibility_receipt",
        "toolchain",
        "upstream",
        "patch",
        "files",
        "tools",
        "scope",
        "offline",
        "network_operations",
        "build_executed",
        "signing_executed",
        "packaging_executed",
    }
    upstream_link = xcode27_linkedit_strip_provenance_link(
        source, developer_dir, allow_reclaimed_arm=allow_reclaimed_arm
    )
    tools = xcode27_linkedit_strip_tool_contract(source, developer_dir)
    expected_scope = {
        "target_os": "mac",
        "minimum_xcode_version_int": 2700,
        "architectures": ["arm64", "x86_64"],
        "use_lld_unchanged": True,
        "postprocess_existing_binaries": False,
    }
    if set(receipt) != expected_keys or receipt.get("schema") != 1:
        raise PipelineError("Xcode 27 LINKEDIT strip receipt schema mismatch")
    if (
        receipt.get("source_root") != str(source)
        or receipt.get("screen_ai_disabled_compatibility_receipt")
        != upstream_link
        or receipt.get("toolchain") != XCODE27_COMPAT_TOOLCHAIN
        or receipt.get("upstream") != XCODE27_LINKEDIT_STRIP_UPSTREAM
        or receipt.get("patch")
        != {
            "path": str(XCODE27_LINKEDIT_STRIP_PATCH),
            "sha256": XCODE27_LINKEDIT_STRIP_PATCH_SHA256,
        }
        or receipt.get("files") != XCODE27_LINKEDIT_STRIP_FILES
        or receipt.get("tools") != tools
        or receipt.get("scope") != expected_scope
        or receipt.get("offline") is not True
        or receipt.get("network_operations") != 0
        or receipt.get("build_executed") is not False
        or receipt.get("signing_executed") is not False
        or receipt.get("packaging_executed") is not False
    ):
        raise PipelineError("Xcode 27 LINKEDIT strip provenance mismatch")
    if sha256_file(XCODE27_LINKEDIT_STRIP_PATCH) != (
        XCODE27_LINKEDIT_STRIP_PATCH_SHA256
    ):
        raise PipelineError("Xcode 27 LINKEDIT strip patch hash mismatch")
    for relative, hashes in XCODE27_LINKEDIT_STRIP_FILES.items():
        current = in_source(
            source, relative, "Xcode 27 LINKEDIT strip source", must_exist=True
        )
        if sha256_file(current) != hashes["post_sha256"]:
            raise PipelineError(
                "Xcode 27 LINKEDIT strip source hash mismatch: {}".format(
                    relative
                )
            )
    return receipt_path, receipt


def xcode27_linkedit_strip_plan(source, developer_dir):
    """Plan the one-file post-link strip correction without mutation."""
    upstream_link = xcode27_linkedit_strip_provenance_link(
        source, developer_dir, allow_reclaimed_arm=True
    )
    identity = xcode27_toolchain_identity(developer_contract(developer_dir))
    if identity != XCODE27_COMPAT_TOOLCHAIN:
        raise PipelineError("Xcode 27 LINKEDIT strip toolchain mismatch")
    tools = xcode27_linkedit_strip_tool_contract(source, developer_dir)
    receipt_path = in_source(
        source,
        XCODE27_LINKEDIT_STRIP_RECEIPT,
        "Xcode 27 LINKEDIT strip receipt",
    )
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PipelineError("Xcode 27 LINKEDIT strip receipt already exists")
    patch = XCODE27_LINKEDIT_STRIP_PATCH
    if patch.is_symlink() or not patch.is_file():
        raise PipelineError("Xcode 27 LINKEDIT strip patch is not regular")
    if sha256_file(patch) != XCODE27_LINKEDIT_STRIP_PATCH_SHA256:
        raise PipelineError("Xcode 27 LINKEDIT strip patch hash mismatch")
    files = {}
    for relative, hashes in XCODE27_LINKEDIT_STRIP_FILES.items():
        path = in_source(
            source, relative, "Xcode 27 LINKEDIT strip source", must_exist=True
        )
        if sha256_file(path) != hashes["pre_sha256"]:
            raise PipelineError(
                "Xcode 27 LINKEDIT strip pre-fix hash mismatch: {}".format(
                    relative
                )
            )
        files[relative] = dict(hashes)
    try:
        boundary = prepare_source.check_patch_boundary(source, patch)
    except prepare_source.PreparationError as exc:
        raise PipelineError(str(exc)) from exc
    return {
        "stage": "apply-xcode27-linkedit-strip-compat",
        "source_root": str(source),
        "screen_ai_disabled_compatibility_receipt": upstream_link,
        "toolchain": identity,
        "upstream": XCODE27_LINKEDIT_STRIP_UPSTREAM,
        "patch": boundary,
        "files": files,
        "tools": tools,
        "receipt": str(receipt_path),
        "offline": True,
        "network_operations": 0,
    }


def execute_xcode27_linkedit_strip(source, developer_dir, plan):
    """Select Xcode strip transactionally; never rewrite a built binary."""
    expected = xcode27_linkedit_strip_plan(source, developer_dir)
    if plan != expected:
        raise PipelineError("Xcode 27 LINKEDIT strip plan changed")
    require_free(source, SOFT_FLOOR_GIB, "Xcode 27 LINKEDIT strip fix")
    snapshot_root = Path(
        tempfile.mkdtemp(prefix="focus-xcode27-linkedit-strip-rollback-")
    ).resolve()
    backups = {}
    try:
        for position, relative in enumerate(XCODE27_LINKEDIT_STRIP_FILES, 1):
            current = in_source(
                source, relative, "Xcode 27 LINKEDIT strip snapshot", must_exist=True
            )
            backup = snapshot_root / "{:02d}.backup".format(position)
            prepare_source.atomic_copy(current, backup)
            backups[relative] = backup
        prepare_source.apply_patch_plan(
            source, [XCODE27_LINKEDIT_STRIP_PATCH], total_patches=1
        )
        for relative, hashes in XCODE27_LINKEDIT_STRIP_FILES.items():
            current = in_source(
                source, relative, "Xcode 27 LINKEDIT strip result", must_exist=True
            )
            if sha256_file(current) != hashes["post_sha256"]:
                raise PipelineError(
                    "Xcode 27 LINKEDIT strip post-fix hash mismatch: {}".format(
                        relative
                    )
                )
        receipt_value = {
            "schema": 1,
            "source_root": str(source),
            "screen_ai_disabled_compatibility_receipt": expected[
                "screen_ai_disabled_compatibility_receipt"
            ],
            "toolchain": XCODE27_COMPAT_TOOLCHAIN,
            "upstream": XCODE27_LINKEDIT_STRIP_UPSTREAM,
            "patch": {
                "path": str(XCODE27_LINKEDIT_STRIP_PATCH),
                "sha256": XCODE27_LINKEDIT_STRIP_PATCH_SHA256,
            },
            "files": XCODE27_LINKEDIT_STRIP_FILES,
            "tools": expected["tools"],
            "scope": {
                "target_os": "mac",
                "minimum_xcode_version_int": 2700,
                "architectures": ["arm64", "x86_64"],
                "use_lld_unchanged": True,
                "postprocess_existing_binaries": False,
            },
            "offline": True,
            "network_operations": 0,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
        }
        receipt_report = atomic_json(expected["receipt"], receipt_value)
        xcode27_linkedit_strip_receipt_contract(
            source, developer_dir, required=True, allow_reclaimed_arm=True
        )
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            if receipt_path.is_symlink() or (
                receipt_path.exists() and not receipt_path.is_file()
            ):
                raise PipelineError(
                    "unsafe Xcode 27 LINKEDIT strip receipt during rollback"
                )
            if receipt_path.is_file():
                receipt_path.unlink()
            for relative, backup in backups.items():
                target = in_source(
                    source,
                    relative,
                    "Xcode 27 LINKEDIT strip rollback",
                    must_exist=True,
                )
                prepare_source.atomic_copy(backup, target)
                if sha256_file(target) != XCODE27_LINKEDIT_STRIP_FILES[relative][
                    "pre_sha256"
                ]:
                    raise PipelineError(
                        "Xcode 27 LINKEDIT strip rollback hash mismatch: {}".format(
                            relative
                        )
                    )
        except BaseException as rollback_error:
            raise PipelineError(
                "Xcode 27 LINKEDIT strip fix and rollback failed; snapshot "
                "retained at {}: original={!r}; rollback={!r}".format(
                    snapshot_root, original_error, rollback_error
                )
            ) from original_error
        shutil.rmtree(snapshot_root)
        if isinstance(original_error, prepare_source.PreparationError):
            raise PipelineError(str(original_error)) from original_error
        raise
    else:
        shutil.rmtree(snapshot_root)
    return {
        "stage": "apply-xcode27-linkedit-strip-compat",
        "applied": True,
        "receipt": receipt_report,
        "files": XCODE27_LINKEDIT_STRIP_FILES,
        "tools": expected["tools"],
        "upstream": XCODE27_LINKEDIT_STRIP_UPSTREAM,
        "offline": True,
        "network_operations": 0,
        "build_executed": False,
    }


def _disabled_swiftshader_text_contract(path, label, expected_sha256):
    """Require one exact disabled flag and reject any enabled spelling."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PipelineError("missing regular {}: {}".format(label, path))
    observed = sha256_file(path)
    if observed != expected_sha256:
        raise PipelineError("{} hash mismatch".format(label))
    text = path.read_text(encoding="utf-8")
    if (
        text.count("enable_swiftshader=false") != 1
        or re.search(r"enable_swiftshader\s*=\s*true", text)
    ):
        raise PipelineError("{} does not pin disabled SwiftShader".format(label))
    return {"path": str(path), "sha256": observed}


def swiftshader_app_library_contract(app):
    """Prove the bundle has ANGLE libraries and intentionally lacks SwiftShader."""
    app = Path(app)
    if app.is_symlink() or not app.is_dir() or app.name != APP_NAME:
        raise PipelineError("invalid app for SwiftShader signing contract: {}".format(app))
    framework = (
        app
        / "Contents"
        / "Frameworks"
        / "Focus Browser Framework.framework"
    )
    versions = framework / "Versions"
    if framework.is_symlink() or not framework.is_dir():
        raise PipelineError("missing Focus framework for SwiftShader signing contract")
    if versions.is_symlink() or not versions.is_dir():
        raise PipelineError("missing real Focus framework Versions directory")
    version_dirs = sorted(
        (
            child
            for child in versions.iterdir()
            if child.name != "Current" and child.is_dir() and not child.is_symlink()
        ),
        key=lambda child: child.name,
    )
    if len(version_dirs) != 1:
        raise PipelineError("Focus framework must contain one real version directory")
    libraries = version_dirs[0] / "Libraries"
    if libraries.is_symlink() or not libraries.is_dir():
        raise PipelineError("missing real Focus framework Libraries directory")
    required = {}
    for name in SWIFTSHADER_REQUIRED_ANGLE_LIBRARIES:
        library = libraries / name
        if library.is_symlink() or not library.is_file():
            raise PipelineError("required ANGLE library is missing: {}".format(name))
        required[name] = sha256_file(library)
    forbidden = libraries / SWIFTSHADER_VULKAN_LIBRARY
    if os.path.lexists(str(forbidden)):
        raise PipelineError("disabled SwiftShader library is unexpectedly bundled")
    return {
        "app": str(app),
        "framework_version": version_dirs[0].name,
        "libraries": str(libraries),
        "required_sha256": required,
        "swiftshader_library_absent": True,
    }


def swiftshader_disabled_build_contract(source):
    """Bind the signing exception to both exact Focus build profiles and apps."""
    profiles = {}
    for architecture in ("arm64", "x64"):
        profiles[architecture] = _disabled_swiftshader_text_contract(
            MACOS_DIR / "flags.{}.gn".format(architecture),
            "{} SwiftShader profile".format(architecture),
            SWIFTSHADER_DISABLED_PROFILE_SHA256[architecture],
        )
    reclaim_path, reclaim = reclaim_contract(source)
    arm_args_hash = reclaim.get("arm_args_gn_sha256")
    if arm_args_hash != SWIFTSHADER_DISABLED_ARGS_SHA256["arm64"]:
        raise PipelineError("reclaimed arm64 args do not pin disabled SwiftShader")
    x64_out = in_source(
        source, X64_OUT, "x86_64 output", must_exist=True, directory=True
    )
    x64_receipt_path, _ = slice_receipt_contract(source, x64_out, "x64")
    x64_args = _disabled_swiftshader_text_contract(
        x64_out / "args.gn",
        "x64 generated args",
        SWIFTSHADER_DISABLED_ARGS_SHA256["x64"],
    )
    arm_app = in_source(
        source, STAGED_ARM_APP, "staged arm64 app", must_exist=True, directory=True
    )
    x64_app = x64_out / APP_NAME
    libraries = {
        "arm64": swiftshader_app_library_contract(arm_app),
        "x64": swiftshader_app_library_contract(x64_app),
    }
    return {
        "profiles": profiles,
        "build_args": {
            "arm64": {
                "path": None,
                "sha256": arm_args_hash,
                "reclaimed": True,
            },
            "x64": {**x64_args, "reclaimed": False},
        },
        "libraries": libraries,
        "app_tree_sha256": {
            "arm64": reclaim["tree_sha256"],
            "x64": tree_digest(x64_app),
        },
        "reclaim_receipt": {
            "path": str(reclaim_path),
            "sha256": sha256_file(reclaim_path),
        },
        "x64_build_receipt": {
            "path": str(x64_receipt_path),
            "sha256": sha256_file(x64_receipt_path),
        },
    }


def swiftshader_signing_refresh_contract(source):
    """Return the one small j8 target that refreshes generated signing scripts."""
    tools = tool_paths(source)
    ninja = ninja_contract(source)
    return {
        "command": [
            str(tools["autoninja"]),
            "-j{}".format(BUILD_JOBS),
            "-C",
            X64_OUT,
            "chrome/installer/mac:copy_signing",
        ],
        "ninja": ninja,
    }


def _swiftshader_signing_file_state(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PipelineError("missing regular {}: {}".format(label, path))
    observed = sha256_file(path)
    hashes = next(iter(SWIFTSHADER_DISABLED_SIGNING_FILES.values()))
    if observed == hashes["pre_sha256"]:
        return "pre"
    if observed == hashes["post_sha256"]:
        return "post"
    if observed == ADHOC_RUNTIME_SIGNING_FILES[
        "chrome/installer/mac/signing/parts.py"
    ]["post_sha256"]:
        return "post-adhoc"
    raise PipelineError("{} is neither the audited pre nor post image".format(label))


def _swiftshader_signing_state_contract(source_state, packaging_state):
    """Reject stale/ahead combinations, including the recovery mtime hazard."""
    if source_state == "pre" and packaging_state != "pre":
        raise PipelineError("generated signing package is ahead of its source")
    if source_state == "post" and packaging_state == "post-adhoc":
        raise PipelineError("generated signing package is ahead of its source")
    if source_state == "post-adhoc" and packaging_state != "post-adhoc":
        raise PipelineError(
            "post-ad-hoc recovery requires an already-matching generated package"
        )
    return {"source": source_state, "packaging": packaging_state}


def swiftshader_disabled_signing_plan(source, developer_dir):
    """Plan the Focus-only correction to Chromium's generated signing package."""
    acquisition_contract(source)
    tool_receipt_contract(source, developer_dir)
    preparation_path, _ = preparation_contract(source, allow_reclaimed_arm=True)
    receipt_path = in_source(
        source,
        SWIFTSHADER_DISABLED_SIGNING_RECEIPT,
        "disabled SwiftShader signing receipt",
    )
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PipelineError("disabled SwiftShader signing receipt already exists")
    patch = SWIFTSHADER_DISABLED_SIGNING_PATCH
    if patch.is_symlink() or not patch.is_file():
        raise PipelineError("disabled SwiftShader signing patch is not regular")
    if sha256_file(patch) != SWIFTSHADER_DISABLED_SIGNING_PATCH_SHA256:
        raise PipelineError("disabled SwiftShader signing patch hash mismatch")
    relative = next(iter(SWIFTSHADER_DISABLED_SIGNING_FILES))
    source_parts = in_source(
        source, relative, "Chromium signing parts", must_exist=True
    )
    packaging_parts = in_source(
        source,
        X64_OUT + "/" + PACKAGING_NAME + "/signing/parts.py",
        "generated x86_64 signing parts",
        must_exist=True,
    )
    source_state = _swiftshader_signing_file_state(
        source_parts, "Chromium signing parts"
    )
    packaging_state = _swiftshader_signing_file_state(
        packaging_parts, "generated x86_64 signing parts"
    )
    _swiftshader_signing_state_contract(source_state, packaging_state)
    if source_state != "post-adhoc":
        try:
            prepare_source.check_patch_boundary(
                source, patch, reverse=(source_state == "post")
            )
        except prepare_source.PreparationError as exc:
            raise PipelineError(str(exc)) from exc
    build = swiftshader_disabled_build_contract(source)
    refresh = swiftshader_signing_refresh_contract(source)
    return {
        "stage": "apply-swiftshader-disabled-signing-compat",
        "source_root": str(source),
        "developer_dir": str(developer_dir),
        "preparation_receipt": {
            "path": str(preparation_path),
            "sha256": sha256_file(preparation_path),
        },
        **build,
        "upstream": SWIFTSHADER_DISABLED_SIGNING_UPSTREAM,
        "patch": {
            "path": str(patch),
            "sha256": SWIFTSHADER_DISABLED_SIGNING_PATCH_SHA256,
        },
        "files": SWIFTSHADER_DISABLED_SIGNING_FILES,
        "source_parts": str(source_parts),
        "packaging_parts": str(packaging_parts),
        "source_state": source_state,
        "packaging_state": packaging_state,
        "refresh": refresh,
        "receipt": str(receipt_path),
        "offline": True,
        "network_operations": 0,
    }


def swiftshader_disabled_signing_receipt_contract(
    source, developer_dir, allow_adhoc_runtime_signing=False
):
    """Validate the completed source and generated-package signing correction."""
    receipt_path = in_source(
        source,
        SWIFTSHADER_DISABLED_SIGNING_RECEIPT,
        "disabled SwiftShader signing receipt",
        must_exist=True,
    )
    receipt = load_json(receipt_path, "disabled SwiftShader signing receipt")
    expected_keys = {
        "schema",
        "source_root",
        "developer_dir",
        "preparation_receipt",
        "reclaim_receipt",
        "x64_build_receipt",
        "profiles",
        "build_args",
        "libraries",
        "app_tree_sha256",
        "upstream",
        "patch",
        "files",
        "refresh",
        "recovery_state",
        "offline",
        "network_operations",
        "app_build_executed",
        "signing_scripts_refreshed",
        "signing_executed",
        "packaging_executed",
    }
    preparation_path, _ = preparation_contract(source, allow_reclaimed_arm=True)
    build = swiftshader_disabled_build_contract(source)
    refresh = swiftshader_signing_refresh_contract(source)
    recovery_state = receipt.get("recovery_state")
    allowed_recovery_states = (
        {"source": "pre", "packaging": "pre"},
        {"source": "post", "packaging": "pre"},
        {"source": "post", "packaging": "post"},
        {"source": "post-adhoc", "packaging": "post-adhoc"},
    )
    if set(receipt) != expected_keys or receipt.get("schema") != 1:
        raise PipelineError("disabled SwiftShader signing receipt schema mismatch")
    if (
        receipt.get("source_root") != str(source)
        or receipt.get("developer_dir") != str(developer_dir)
        or receipt.get("preparation_receipt")
        != {"path": str(preparation_path), "sha256": sha256_file(preparation_path)}
        or receipt.get("reclaim_receipt") != build["reclaim_receipt"]
        or receipt.get("x64_build_receipt") != build["x64_build_receipt"]
        or receipt.get("profiles") != build["profiles"]
        or receipt.get("build_args") != build["build_args"]
        or receipt.get("libraries") != build["libraries"]
        or receipt.get("app_tree_sha256") != build["app_tree_sha256"]
        or receipt.get("upstream") != SWIFTSHADER_DISABLED_SIGNING_UPSTREAM
        or receipt.get("patch")
        != {
            "path": str(SWIFTSHADER_DISABLED_SIGNING_PATCH),
            "sha256": SWIFTSHADER_DISABLED_SIGNING_PATCH_SHA256,
        }
        or receipt.get("files") != SWIFTSHADER_DISABLED_SIGNING_FILES
        or receipt.get("refresh") != refresh
        or recovery_state not in allowed_recovery_states
        or receipt.get("offline") is not True
        or receipt.get("network_operations") != 0
        or receipt.get("app_build_executed") is not False
        or receipt.get("signing_scripts_refreshed") is not True
        or receipt.get("signing_executed") is not False
        or receipt.get("packaging_executed") is not False
    ):
        raise PipelineError("disabled SwiftShader signing provenance mismatch")
    if sha256_file(SWIFTSHADER_DISABLED_SIGNING_PATCH) != (
        SWIFTSHADER_DISABLED_SIGNING_PATCH_SHA256
    ):
        raise PipelineError("disabled SwiftShader signing patch hash mismatch")
    relative, hashes = next(iter(SWIFTSHADER_DISABLED_SIGNING_FILES.items()))
    source_parts = in_source(
        source, relative, "Chromium signing parts", must_exist=True
    )
    packaging_parts = in_source(
        source,
        X64_OUT + "/" + PACKAGING_NAME + "/signing/parts.py",
        "generated x86_64 signing parts",
        must_exist=True,
    )
    allowed_hashes = {hashes["post_sha256"]}
    if allow_adhoc_runtime_signing or recovery_state == {
        "source": "post-adhoc",
        "packaging": "post-adhoc",
    }:
        allowed_hashes.add(
            ADHOC_RUNTIME_SIGNING_FILES[
                "chrome/installer/mac/signing/parts.py"
            ]["post_sha256"]
        )
    for label, path in (
        ("Chromium signing parts", source_parts),
        ("generated x86_64 signing parts", packaging_parts),
    ):
        if sha256_file(path) not in allowed_hashes:
            raise PipelineError("{} post-fix hash mismatch".format(label))
    return receipt_path, receipt


def execute_swiftshader_disabled_signing(source, developer_dir, plan):
    """Apply the one-file correction and refresh only copy_signing at j8."""
    expected = swiftshader_disabled_signing_plan(source, developer_dir)
    if plan != expected:
        raise PipelineError("disabled SwiftShader signing plan changed")
    require_free(source, SOFT_FLOOR_GIB, "disabled SwiftShader signing fix")
    source_parts = Path(expected["source_parts"])
    packaging_parts = Path(expected["packaging_parts"])
    initial_app_trees = expected["app_tree_sha256"]
    snapshot_root = Path(
        tempfile.mkdtemp(prefix="focus-swiftshader-signing-rollback-")
    ).resolve()
    source_backup = snapshot_root / "source-parts.py"
    packaging_backup = snapshot_root / "packaging-parts.py"
    prepare_source.atomic_copy(source_parts, source_backup)
    prepare_source.atomic_copy(packaging_parts, packaging_backup)
    receipt_report = None
    try:
        if expected["source_state"] == "pre":
            prepare_source.apply_patch_plan(
                source,
                [SWIFTSHADER_DISABLED_SIGNING_PATCH],
                total_patches=1,
            )
        expected_final_state = (
            "post-adhoc" if expected["source_state"] == "post-adhoc" else "post"
        )
        if _swiftshader_signing_file_state(
            source_parts, "Chromium signing parts"
        ) != expected_final_state:
            raise PipelineError("Chromium signing parts post-fix hash mismatch")
        if expected["packaging_state"] == "pre":
            environment = safe_environment(
                source,
                developer_dir,
                build_ninja=Path(expected["refresh"]["ninja"]["path"]),
            )
            run_monitored(expected["refresh"]["command"], source, environment)
        if _swiftshader_signing_file_state(
            packaging_parts, "generated signing parts"
        ) != expected_final_state:
            raise PipelineError("generated signing parts post-fix hash mismatch")
        current_build = swiftshader_disabled_build_contract(source)
        if current_build["app_tree_sha256"] != initial_app_trees:
            raise PipelineError("signing-script refresh changed an app bundle")
        receipt_value = {
            "schema": 1,
            "source_root": str(source),
            "developer_dir": str(developer_dir),
            "preparation_receipt": expected["preparation_receipt"],
            "reclaim_receipt": expected["reclaim_receipt"],
            "x64_build_receipt": expected["x64_build_receipt"],
            "profiles": expected["profiles"],
            "build_args": expected["build_args"],
            "libraries": expected["libraries"],
            "app_tree_sha256": initial_app_trees,
            "upstream": SWIFTSHADER_DISABLED_SIGNING_UPSTREAM,
            "patch": expected["patch"],
            "files": SWIFTSHADER_DISABLED_SIGNING_FILES,
            "refresh": expected["refresh"],
            "recovery_state": {
                "source": expected["source_state"],
                "packaging": expected["packaging_state"],
            },
            "offline": True,
            "network_operations": 0,
            "app_build_executed": False,
            "signing_scripts_refreshed": True,
            "signing_executed": False,
            "packaging_executed": False,
        }
        receipt_report = atomic_json(Path(expected["receipt"]), receipt_value)
        swiftshader_disabled_signing_receipt_contract(source, developer_dir)
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            if receipt_path.is_symlink() or (
                receipt_path.exists() and not receipt_path.is_file()
            ):
                raise PipelineError("unsafe SwiftShader receipt during rollback")
            if receipt_path.is_file():
                receipt_path.unlink()
            prepare_source.atomic_copy(source_backup, source_parts)
            prepare_source.atomic_copy(packaging_backup, packaging_parts)
            if (
                _swiftshader_signing_file_state(
                    source_parts, "rolled-back Chromium signing parts"
                )
                != expected["source_state"]
                or _swiftshader_signing_file_state(
                    packaging_parts, "rolled-back generated signing parts"
                )
                != expected["packaging_state"]
            ):
                raise PipelineError("SwiftShader signing rollback state mismatch")
        except BaseException as rollback_error:
            raise PipelineError(
                "disabled SwiftShader signing fix and rollback failed; snapshot "
                "retained at {}: original={!r}; rollback={!r}".format(
                    snapshot_root, original_error, rollback_error
                )
            ) from original_error
        shutil.rmtree(snapshot_root)
        if isinstance(original_error, prepare_source.PreparationError):
            raise PipelineError(str(original_error)) from original_error
        raise
    else:
        shutil.rmtree(snapshot_root)
    return {
        "stage": "apply-swiftshader-disabled-signing-compat",
        "applied": True,
        "receipt": receipt_report,
        "files": SWIFTSHADER_DISABLED_SIGNING_FILES,
        "refresh_command": expected["refresh"]["command"],
        "jobs": BUILD_JOBS,
        "app_build_executed": False,
        "signing_executed": False,
        "packaging_executed": False,
    }


def _adhoc_runtime_signing_file_state(path, hashes, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PipelineError("missing regular {}: {}".format(label, path))
    observed = sha256_file(path)
    if observed == hashes["pre_sha256"]:
        return "pre"
    if observed == hashes["post_sha256"]:
        return "post"
    raise PipelineError("{} is neither the audited pre nor post image".format(label))


def _adhoc_runtime_signing_set_state(paths, files, label):
    states = {
        _adhoc_runtime_signing_file_state(
            paths[relative], hashes, "{} {}".format(label, relative)
        )
        for relative, hashes in files.items()
    }
    if len(states) != 1:
        raise PipelineError("{} has a mixed pre/post state".format(label))
    return next(iter(states))


def adhoc_runtime_signing_refresh_contract(source):
    """Return the exact j8 target used to refresh generated signing scripts."""
    return swiftshader_signing_refresh_contract(source)


def adhoc_runtime_signing_test_contract(source):
    """Bind the targeted Chromium signing tests to the pinned Python 3.11."""
    python = packaging_python_contract(source)
    working_directory = in_source(
        source,
        "chrome/installer/mac",
        "Chromium macOS signing test root",
        must_exist=True,
        directory=True,
    )
    return {
        "command": [
            python["path"],
            "-m",
            "unittest",
            "signing.parts_test",
            "signing.modification_test",
        ],
        "working_directory": str(working_directory),
        "python": python,
        "modules": ["signing.parts_test", "signing.modification_test"],
    }


def _adhoc_runtime_signing_paths(source):
    source_paths = {
        relative: in_source(
            source,
            relative,
            "Chromium ad-hoc signing source",
            must_exist=True,
        )
        for relative in ADHOC_RUNTIME_SIGNING_FILES
    }
    packaging_paths = {
        relative: in_source(
            source,
            X64_OUT
            + "/"
            + PACKAGING_NAME
            + "/signing/"
            + Path(relative).name,
            "generated x86_64 ad-hoc signing source",
            must_exist=True,
        )
        for relative in ADHOC_RUNTIME_SIGNING_GENERATED_FILES
    }
    return source_paths, packaging_paths


def adhoc_runtime_signing_plan(source, developer_dir):
    """Plan the identity-'-' Framework-loading compatibility correction."""
    acquisition_contract(source)
    tool_receipt_contract(source, developer_dir)
    preparation_path, _ = preparation_contract(source, allow_reclaimed_arm=True)
    swiftshader_path, _ = swiftshader_disabled_signing_receipt_contract(
        source, developer_dir, allow_adhoc_runtime_signing=True
    )
    receipt_path = in_source(
        source,
        ADHOC_RUNTIME_SIGNING_RECEIPT,
        "ad-hoc runtime signing receipt",
    )
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PipelineError("ad-hoc runtime signing receipt already exists")
    patch = ADHOC_RUNTIME_SIGNING_PATCH
    if patch.is_symlink() or not patch.is_file():
        raise PipelineError("ad-hoc runtime signing patch is not regular")
    if sha256_file(patch) != ADHOC_RUNTIME_SIGNING_PATCH_SHA256:
        raise PipelineError("ad-hoc runtime signing patch hash mismatch")
    source_paths, packaging_paths = _adhoc_runtime_signing_paths(source)
    source_state = _adhoc_runtime_signing_set_state(
        source_paths, ADHOC_RUNTIME_SIGNING_FILES, "Chromium signing sources"
    )
    packaging_state = _adhoc_runtime_signing_set_state(
        packaging_paths,
        ADHOC_RUNTIME_SIGNING_GENERATED_FILES,
        "generated signing package",
    )
    if source_state == "pre" and packaging_state == "post":
        raise PipelineError("generated signing package is ahead of its source")
    try:
        prepare_source.check_patch_boundary(
            source, patch, reverse=(source_state == "post")
        )
    except prepare_source.PreparationError as exc:
        raise PipelineError(str(exc)) from exc
    build = swiftshader_disabled_build_contract(source)
    return {
        "stage": "apply-adhoc-runtime-signing-compat",
        "source_root": str(source),
        "developer_dir": str(developer_dir),
        "preparation_receipt": {
            "path": str(preparation_path),
            "sha256": sha256_file(preparation_path),
        },
        "swiftshader_disabled_signing": {
            "path": str(swiftshader_path),
            "sha256": sha256_file(swiftshader_path),
        },
        "reclaim_receipt": build["reclaim_receipt"],
        "x64_build_receipt": build["x64_build_receipt"],
        "app_tree_sha256": build["app_tree_sha256"],
        "provenance": ADHOC_RUNTIME_SIGNING_PROVENANCE,
        "identity_scope": "-",
        "framework_loading_products": list(
            ADHOC_RUNTIME_SIGNING_FRAMEWORK_PRODUCTS
        ),
        "patch": {
            "path": str(patch),
            "sha256": ADHOC_RUNTIME_SIGNING_PATCH_SHA256,
        },
        "files": ADHOC_RUNTIME_SIGNING_FILES,
        "generated_files": ADHOC_RUNTIME_SIGNING_GENERATED_FILES,
        "source_paths": {
            relative: str(path) for relative, path in source_paths.items()
        },
        "packaging_paths": {
            relative: str(path) for relative, path in packaging_paths.items()
        },
        "source_state": source_state,
        "packaging_state": packaging_state,
        "tests": adhoc_runtime_signing_test_contract(source),
        "refresh": adhoc_runtime_signing_refresh_contract(source),
        "refresh_strategy": {
            "mtime_independent": True,
            "forced_missing_outputs": [
                str(path) for path in packaging_paths.values()
            ],
        },
        "receipt": str(receipt_path),
        "offline": True,
        "network_operations": 0,
    }


def adhoc_runtime_signing_receipt_contract(source, developer_dir):
    """Validate the ad-hoc-only options, entitlements, tests, and refresh."""
    receipt_path = in_source(
        source,
        ADHOC_RUNTIME_SIGNING_RECEIPT,
        "ad-hoc runtime signing receipt",
        must_exist=True,
    )
    receipt = load_json(receipt_path, "ad-hoc runtime signing receipt")
    expected_keys = {
        "schema",
        "source_root",
        "developer_dir",
        "preparation_receipt",
        "swiftshader_disabled_signing",
        "reclaim_receipt",
        "x64_build_receipt",
        "app_tree_sha256",
        "provenance",
        "identity_scope",
        "framework_loading_products",
        "patch",
        "files",
        "generated_files",
        "tests",
        "refresh",
        "refresh_strategy",
        "recovery_state",
        "offline",
        "network_operations",
        "chromium_tests_executed",
        "chromium_tests_passed",
        "app_build_executed",
        "signing_scripts_refreshed",
        "signing_executed",
        "packaging_executed",
    }
    preparation_path, _ = preparation_contract(source, allow_reclaimed_arm=True)
    swiftshader_path, _ = swiftshader_disabled_signing_receipt_contract(
        source, developer_dir, allow_adhoc_runtime_signing=True
    )
    build = swiftshader_disabled_build_contract(source)
    tests = adhoc_runtime_signing_test_contract(source)
    refresh = adhoc_runtime_signing_refresh_contract(source)
    _, packaging_paths = _adhoc_runtime_signing_paths(source)
    refresh_strategy = {
        "mtime_independent": True,
        "forced_missing_outputs": [
            str(path) for path in packaging_paths.values()
        ],
    }
    recovery_state = receipt.get("recovery_state")
    allowed_recovery_states = (
        {"source": "pre", "packaging": "pre"},
        {"source": "post", "packaging": "pre"},
        {"source": "post", "packaging": "post"},
    )
    if set(receipt) != expected_keys or receipt.get("schema") != 1:
        raise PipelineError("ad-hoc runtime signing receipt schema mismatch")
    if (
        receipt.get("source_root") != str(source)
        or receipt.get("developer_dir") != str(developer_dir)
        or receipt.get("preparation_receipt")
        != {"path": str(preparation_path), "sha256": sha256_file(preparation_path)}
        or receipt.get("swiftshader_disabled_signing")
        != {"path": str(swiftshader_path), "sha256": sha256_file(swiftshader_path)}
        or receipt.get("reclaim_receipt") != build["reclaim_receipt"]
        or receipt.get("x64_build_receipt") != build["x64_build_receipt"]
        or receipt.get("app_tree_sha256") != build["app_tree_sha256"]
        or receipt.get("provenance") != ADHOC_RUNTIME_SIGNING_PROVENANCE
        or receipt.get("identity_scope") != "-"
        or receipt.get("framework_loading_products")
        != list(ADHOC_RUNTIME_SIGNING_FRAMEWORK_PRODUCTS)
        or receipt.get("patch")
        != {
            "path": str(ADHOC_RUNTIME_SIGNING_PATCH),
            "sha256": ADHOC_RUNTIME_SIGNING_PATCH_SHA256,
        }
        or receipt.get("files") != ADHOC_RUNTIME_SIGNING_FILES
        or receipt.get("generated_files")
        != ADHOC_RUNTIME_SIGNING_GENERATED_FILES
        or receipt.get("tests") != tests
        or receipt.get("refresh") != refresh
        or receipt.get("refresh_strategy") != refresh_strategy
        or recovery_state not in allowed_recovery_states
        or receipt.get("offline") is not True
        or receipt.get("network_operations") != 0
        or receipt.get("chromium_tests_executed") is not True
        or receipt.get("chromium_tests_passed") is not True
        or receipt.get("app_build_executed") is not False
        or receipt.get("signing_scripts_refreshed") is not True
        or receipt.get("signing_executed") is not False
        or receipt.get("packaging_executed") is not False
    ):
        raise PipelineError("ad-hoc runtime signing provenance mismatch")
    if sha256_file(ADHOC_RUNTIME_SIGNING_PATCH) != (
        ADHOC_RUNTIME_SIGNING_PATCH_SHA256
    ):
        raise PipelineError("ad-hoc runtime signing patch hash mismatch")
    source_paths, packaging_paths = _adhoc_runtime_signing_paths(source)
    if (
        _adhoc_runtime_signing_set_state(
            source_paths,
            ADHOC_RUNTIME_SIGNING_FILES,
            "Chromium signing sources",
        )
        != "post"
        or _adhoc_runtime_signing_set_state(
            packaging_paths,
            ADHOC_RUNTIME_SIGNING_GENERATED_FILES,
            "generated signing package",
        )
        != "post"
    ):
        raise PipelineError("ad-hoc runtime signing post-fix state mismatch")
    return receipt_path, receipt


def execute_adhoc_runtime_signing(source, developer_dir, plan):
    """Test and publish the ad-hoc signing correction transactionally."""
    expected = adhoc_runtime_signing_plan(source, developer_dir)
    if plan != expected:
        raise PipelineError("ad-hoc runtime signing plan changed")
    require_free(source, SOFT_FLOOR_GIB, "ad-hoc runtime signing fix")
    source_paths = {
        relative: Path(path) for relative, path in expected["source_paths"].items()
    }
    packaging_paths = {
        relative: Path(path)
        for relative, path in expected["packaging_paths"].items()
    }
    initial_app_trees = expected["app_tree_sha256"]
    snapshot_root = Path(
        tempfile.mkdtemp(prefix="focus-adhoc-runtime-signing-rollback-")
    ).resolve()
    source_backups = {}
    packaging_backups = {}
    receipt_report = None
    try:
        for position, (relative, path) in enumerate(source_paths.items(), 1):
            backup = snapshot_root / "source-{:02d}".format(position)
            prepare_source.atomic_copy(path, backup)
            source_backups[relative] = backup
        for position, (relative, path) in enumerate(packaging_paths.items(), 1):
            backup = snapshot_root / "packaging-{:02d}".format(position)
            prepare_source.atomic_copy(path, backup)
            packaging_backups[relative] = backup
        if expected["source_state"] == "pre":
            prepare_source.apply_patch_plan(
                source, [ADHOC_RUNTIME_SIGNING_PATCH], total_patches=1
            )
        if _adhoc_runtime_signing_set_state(
            source_paths,
            ADHOC_RUNTIME_SIGNING_FILES,
            "Chromium signing sources",
        ) != "post":
            raise PipelineError("Chromium ad-hoc signing post-fix hash mismatch")
        environment = safe_environment(source, developer_dir)
        run_monitored(
            expected["tests"]["command"],
            Path(expected["tests"]["working_directory"]),
            environment,
            watched_paths=(source,),
        )
        refresh_environment = safe_environment(
            source,
            developer_dir,
            build_ninja=Path(expected["refresh"]["ninja"]["path"]),
        )
        if _adhoc_runtime_signing_set_state(
            packaging_paths,
            ADHOC_RUNTIME_SIGNING_GENERATED_FILES,
            "generated signing package before forced refresh",
        ) != expected["packaging_state"]:
            raise PipelineError(
                "generated signing package changed before forced refresh"
            )
        for relative, path in packaging_paths.items():
            if path.is_symlink() or not path.is_file():
                raise PipelineError(
                    "generated signing output is unsafe before refresh: {}".format(
                        relative
                    )
                )
            path.unlink()
            if os.path.lexists(str(path)):
                raise PipelineError(
                    "generated signing output survived forced refresh removal: {}".format(
                        relative
                    )
                )
        run_monitored(expected["refresh"]["command"], source, refresh_environment)
        if _adhoc_runtime_signing_set_state(
            packaging_paths,
            ADHOC_RUNTIME_SIGNING_GENERATED_FILES,
            "generated signing package",
        ) != "post":
            raise PipelineError("generated ad-hoc signing post-fix hash mismatch")
        current_build = swiftshader_disabled_build_contract(source)
        if current_build["app_tree_sha256"] != initial_app_trees:
            raise PipelineError("ad-hoc signing refresh changed an app bundle")
        receipt_value = {
            "schema": 1,
            "source_root": str(source),
            "developer_dir": str(developer_dir),
            "preparation_receipt": expected["preparation_receipt"],
            "swiftshader_disabled_signing": expected[
                "swiftshader_disabled_signing"
            ],
            "reclaim_receipt": expected["reclaim_receipt"],
            "x64_build_receipt": expected["x64_build_receipt"],
            "app_tree_sha256": initial_app_trees,
            "provenance": ADHOC_RUNTIME_SIGNING_PROVENANCE,
            "identity_scope": "-",
            "framework_loading_products": list(
                ADHOC_RUNTIME_SIGNING_FRAMEWORK_PRODUCTS
            ),
            "patch": expected["patch"],
            "files": ADHOC_RUNTIME_SIGNING_FILES,
            "generated_files": ADHOC_RUNTIME_SIGNING_GENERATED_FILES,
            "tests": expected["tests"],
            "refresh": expected["refresh"],
            "refresh_strategy": expected["refresh_strategy"],
            "recovery_state": {
                "source": expected["source_state"],
                "packaging": expected["packaging_state"],
            },
            "offline": True,
            "network_operations": 0,
            "chromium_tests_executed": True,
            "chromium_tests_passed": True,
            "app_build_executed": False,
            "signing_scripts_refreshed": True,
            "signing_executed": False,
            "packaging_executed": False,
        }
        receipt_report = atomic_json(Path(expected["receipt"]), receipt_value)
        adhoc_runtime_signing_receipt_contract(source, developer_dir)
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            if receipt_path.is_symlink() or (
                receipt_path.exists() and not receipt_path.is_file()
            ):
                raise PipelineError("unsafe ad-hoc receipt during rollback")
            if receipt_path.is_file():
                receipt_path.unlink()
            for relative, backup in packaging_backups.items():
                prepare_source.atomic_copy(backup, packaging_paths[relative])
            for relative, backup in source_backups.items():
                prepare_source.atomic_copy(backup, source_paths[relative])
            if (
                _adhoc_runtime_signing_set_state(
                    source_paths,
                    ADHOC_RUNTIME_SIGNING_FILES,
                    "rolled-back Chromium signing sources",
                )
                != expected["source_state"]
                or _adhoc_runtime_signing_set_state(
                    packaging_paths,
                    ADHOC_RUNTIME_SIGNING_GENERATED_FILES,
                    "rolled-back generated signing package",
                )
                != expected["packaging_state"]
            ):
                raise PipelineError("ad-hoc runtime signing rollback state mismatch")
        except BaseException as rollback_error:
            raise PipelineError(
                "ad-hoc runtime signing fix and rollback failed; snapshot "
                "retained at {}: original={!r}; rollback={!r}".format(
                    snapshot_root, original_error, rollback_error
                )
            ) from original_error
        shutil.rmtree(snapshot_root)
        if isinstance(original_error, prepare_source.PreparationError):
            raise PipelineError(str(original_error)) from original_error
        raise
    else:
        shutil.rmtree(snapshot_root)
    return {
        "stage": "apply-adhoc-runtime-signing-compat",
        "applied": True,
        "receipt": receipt_report,
        "files": ADHOC_RUNTIME_SIGNING_FILES,
        "tests": expected["tests"],
        "refresh_command": expected["refresh"]["command"],
        "jobs": BUILD_JOBS,
        "app_build_executed": False,
        "signing_executed": False,
        "packaging_executed": False,
    }


def preparation_contract(
    source, allow_reclaimed_arm=False, allow_missing_gn_compat=False
):
    receipt_path = in_source(
        source, PREPARATION_RECEIPT, "preparation receipt", must_exist=True
    )
    receipt = load_json(receipt_path, "preparation receipt")
    if (
        receipt.get("schema") != prepare_source.PREPARATION_RECEIPT_SCHEMA
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
    try:
        if "recovery_checkpoint" not in receipt:
            raise prepare_source.PreparationError(
                "preparation receipt lacks recovery provenance"
            )
        prepare_source.validate_recovery_execution_link(
            receipt.get("preparation_execution"),
            receipt.get("recovery_checkpoint"),
        )
        prepare_source.validate_recovery_checkpoint_report(
            receipt.get("recovery_checkpoint"), source
        )
    except prepare_source.PreparationError as exc:
        raise PipelineError(str(exc)) from exc
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
    compat_path = in_source(source, GN_COMPAT_RECEIPT, "GN compatibility receipt")
    compat = None
    if compat_path.exists():
        compat = gn_compat_receipt_contract(source, receipt_path, required=True)
    elif gn_compat_is_required(
        source, allow_missing_arm=allow_reclaimed_arm
    ) and not allow_missing_gn_compat:
        raise PipelineError("GN compatibility receipt is required")
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
        expected_hash = post[label]
        if label == "chrome/BUILD.gn" and compat is not None:
            compat_files = compat[1]["files"]
            if compat_files[label]["pre_sha256"] != expected_hash:
                raise PipelineError("GN compatibility pre-hash is not preparation-bound")
            expected_hash = compat_files[label]["post_sha256"]
        if observed != expected_hash:
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
    xcode27_receipt = in_source(
        source,
        XCODE27_COMPAT_RECEIPT,
        "Xcode 27 compatibility receipt",
        must_exist=True,
    )
    if receipt.get("xcode27_compatibility_receipt_sha256") != sha256_file(
        xcode27_receipt
    ):
        raise PipelineError(
            "{} Xcode 27 compatibility receipt mismatch".format(architecture)
        )
    seatbelt_receipt = in_source(
        source,
        XCODE27_SEATBELT_RECEIPT,
        "Xcode 27 Seatbelt compatibility receipt",
        must_exist=True,
    )
    if receipt.get(
        "xcode27_seatbelt_compatibility_receipt_sha256"
    ) != sha256_file(seatbelt_receipt):
        raise PipelineError(
            "{} Xcode 27 Seatbelt receipt mismatch".format(architecture)
        )
    screen_ai_receipt = in_source(
        source,
        SCREEN_AI_DISABLED_RECEIPT,
        "disabled ScreenAI compatibility receipt",
        must_exist=True,
    )
    if receipt.get(
        "screen_ai_disabled_compatibility_receipt_sha256"
    ) != sha256_file(screen_ai_receipt):
        raise PipelineError(
            "{} disabled ScreenAI receipt mismatch".format(architecture)
        )
    linkedit_receipt, linkedit_value = xcode27_linkedit_strip_receipt_contract(
        source,
        required=True,
        developer_dir=Path(
            tool_receipt_contract(source)[1]["developer_dir"]
        ),
        allow_reclaimed_arm=(architecture == "x64"),
    )
    if receipt.get(
        "xcode27_linkedit_strip_compatibility_receipt_sha256"
    ) != sha256_file(linkedit_receipt):
        raise PipelineError(
            "{} Xcode 27 LINKEDIT strip receipt mismatch".format(architecture)
        )
    generated = generated_linkedit_strip_contract(
        Path(out), linkedit_value["tools"]
    )
    if receipt.get("generated_linkedit_strip") != generated:
        raise PipelineError(
            "{} generated LINKEDIT strip provenance mismatch".format(architecture)
        )
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


def generated_linkedit_strip_contract(out, tools):
    """Require every generated Apple linker rule to select the pinned strip."""
    out = Path(out)
    if out.is_symlink() or not out.is_dir():
        raise PipelineError("missing generated GN output for LINKEDIT audit")
    selected = tools.get("selected", {})
    expected = selected.get("path")
    if (
        not isinstance(expected, str)
        or selected.get("sha256") != XCODE27_LINKEDIT_STRIP_SHA256
    ):
        raise PipelineError("invalid selected strip provenance")
    reports = []
    token_count = 0
    pattern = re.compile(r"-Wcrl,strippath,([^\s\"']+)")
    for path in sorted(out.rglob("toolchain.ninja")):
        if path.is_symlink() or not path.is_file():
            raise PipelineError("unsafe generated toolchain file: {}".format(path))
        text = path.read_text(encoding="utf-8")
        tokens = pattern.findall(text)
        if not tokens:
            continue
        unexpected = sorted(set(tokens) - {expected})
        if unexpected:
            raise PipelineError(
                "generated linker selected an unpinned strip: {}".format(
                    ", ".join(unexpected)
                )
            )
        if "llvm-strip" in " ".join(tokens):
            raise PipelineError("generated linker still selects llvm-strip")
        token_count += len(tokens)
        reports.append(
            {
                "path": str(path),
                "relative_to_out": path.relative_to(out).as_posix(),
                "sha256": sha256_file(path),
                "strip_token_count": len(tokens),
            }
        )
    if not reports or token_count <= 0:
        raise PipelineError("generated linker contains no strip selection")
    return {
        "out": str(out),
        "selected_strip": selected,
        "toolchain_files": reports,
        "strip_token_count": token_count,
        "all_linker_rules_use_selected_strip": True,
        "llvm_strip_selected": False,
    }


_MACHO_64_MAGICS = {
    b"\xcf\xfa\xed\xfe": "<",
    b"\xfe\xed\xfa\xcf": ">",
}
_MACHO_32_MAGICS = {
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
}
_FAT_MAGICS = {
    b"\xca\xfe\xba\xbe": (">", False),
    b"\xbe\xba\xfe\xca": ("<", False),
    b"\xca\xfe\xba\xbf": (">", True),
    b"\xbf\xba\xfe\xca": ("<", True),
}
_MACHO_CPU_NAMES = {
    0x01000007: "x86_64",
    0x0100000C: "arm64",
}
_LC_SEGMENT_64 = 0x19
_LC_SYMTAB = 0x02
_LC_DYSYMTAB = 0x0B
_LC_TWOLEVEL_HINTS = 0x16
_LC_DYLD_INFO = 0x22
_LC_DYLD_INFO_ONLY = 0x80000022
_LC_CODE_SIGNATURE = 0x1D
_LINKEDIT_DATA_COMMANDS = {
    _LC_CODE_SIGNATURE,
    0x1E,  # LC_SEGMENT_SPLIT_INFO
    0x26,  # LC_FUNCTION_STARTS
    0x29,  # LC_DATA_IN_CODE
    0x2B,  # LC_DYLIB_CODE_SIGN_DRS
    0x2E,  # LC_LINKER_OPTIMIZATION_HINT
    0x80000033,  # LC_DYLD_EXPORTS_TRIE
    0x80000034,  # LC_DYLD_CHAINED_FIXUPS
    0x36,  # LC_ATOM_INFO
    0x37,  # LC_FUNCTION_VARIANTS
    0x38,  # LC_FUNCTION_VARIANT_FIXUPS
    0x3A,  # LC_LAZY_LOAD_DYLIB_INFO
}


def _read_macho_bytes(stream, offset, size, file_size, label):
    if offset < 0 or size < 0 or offset + size > file_size:
        raise PipelineError("{} escapes Mach-O file bounds".format(label))
    stream.seek(offset)
    value = stream.read(size)
    if len(value) != size:
        raise PipelineError("short read for {}".format(label))
    return value


def _macho_slice_report(stream, path, base, size, expected_cpu=None):
    file_size = os.fstat(stream.fileno()).st_size
    header = _read_macho_bytes(stream, base, 32, file_size, "Mach-O header")
    endian = _MACHO_64_MAGICS.get(header[:4])
    if endian is None:
        if header[:4] in _MACHO_32_MAGICS:
            raise PipelineError("32-bit Mach-O slice is forbidden: {}".format(path))
        raise PipelineError("fat entry is not a 64-bit Mach-O slice: {}".format(path))
    values = struct.unpack(endian + "8I", header)
    cpu_type = values[1]
    ncmds = values[4]
    sizeofcmds = values[5]
    if expected_cpu is not None and cpu_type != expected_cpu:
        raise PipelineError("fat architecture/header CPU mismatch: {}".format(path))
    if ncmds <= 0 or ncmds > 65535 or sizeofcmds < 8 or 32 + sizeofcmds > size:
        raise PipelineError("invalid Mach-O load-command bounds: {}".format(path))
    commands = _read_macho_bytes(
        stream, base + 32, sizeofcmds, file_size, "Mach-O load commands"
    )
    entries = []
    linkedit = None

    def add_entry(label, offset, alignment=8):
        if offset == 0:
            return
        entries.append(
            {
                "name": label,
                "offset": offset,
                "required_alignment": alignment,
                "aligned": offset % alignment == 0,
            }
        )

    cursor = 0
    for _ in range(ncmds):
        if cursor + 8 > len(commands):
            raise PipelineError("truncated Mach-O load command: {}".format(path))
        command, command_size = struct.unpack_from(endian + "II", commands, cursor)
        if (
            command_size < 8
            or command_size % 4
            or cursor + command_size > len(commands)
        ):
            raise PipelineError("invalid Mach-O load command size: {}".format(path))
        body = commands[cursor : cursor + command_size]
        if command == _LC_SEGMENT_64 and command_size >= 72:
            segment = struct.unpack_from(endian + "II16sQQQQiiII", body)
            name = segment[2].split(b"\0", 1)[0]
            if name == b"__LINKEDIT":
                if linkedit is not None:
                    raise PipelineError("duplicate __LINKEDIT segment: {}".format(path))
                linkedit = {"fileoff": segment[5], "filesize": segment[6]}
        elif command == _LC_SYMTAB and command_size >= 24:
            symtab = struct.unpack_from(endian + "6I", body)
            add_entry("symtab.symoff", symtab[2])
            add_entry("symtab.stroff", symtab[4])
        elif command == _LC_DYSYMTAB and command_size >= 80:
            dysymtab = struct.unpack_from(endian + "20I", body)
            for name, position in (
                ("dysymtab.tocoff", 8),
                ("dysymtab.modtaboff", 10),
                ("dysymtab.extrefsymoff", 12),
                ("dysymtab.indirectsymoff", 14),
                ("dysymtab.extreloff", 16),
                ("dysymtab.locreloff", 18),
            ):
                add_entry(name, dysymtab[position])
        elif command in (_LC_DYLD_INFO, _LC_DYLD_INFO_ONLY) and command_size >= 48:
            dyld = struct.unpack_from(endian + "12I", body)
            for name, position in (
                ("dyld.rebase_off", 2),
                ("dyld.bind_off", 4),
                ("dyld.weak_bind_off", 6),
                ("dyld.lazy_bind_off", 8),
                ("dyld.export_off", 10),
            ):
                add_entry(name, dyld[position])
        elif command == _LC_TWOLEVEL_HINTS and command_size >= 16:
            hints = struct.unpack_from(endian + "4I", body)
            add_entry("twolevel_hints.offset", hints[2])
        elif command in _LINKEDIT_DATA_COMMANDS and command_size >= 16:
            data = struct.unpack_from(endian + "4I", body)
            alignment = 16 if command == _LC_CODE_SIGNATURE else 8
            add_entry("linkedit_data.0x{:08x}".format(command), data[2], alignment)
        cursor += command_size
    if cursor != sizeofcmds:
        raise PipelineError("Mach-O load-command size mismatch: {}".format(path))
    if entries and linkedit is None:
        raise PipelineError("Mach-O has LINKEDIT tables but no segment: {}".format(path))
    if linkedit is not None:
        start = linkedit["fileoff"]
        end = start + linkedit["filesize"]
        if start % 8 or start > size or end > size or end < start:
            raise PipelineError("invalid __LINKEDIT segment bounds: {}".format(path))
        for entry in entries:
            if not start <= entry["offset"] < end:
                raise PipelineError(
                    "{} is outside __LINKEDIT: {}".format(entry["name"], path)
                )
    violations = [entry for entry in entries if not entry["aligned"]]
    return {
        "architecture": _MACHO_CPU_NAMES.get(
            cpu_type, "cpu-0x{:08x}".format(cpu_type)
        ),
        "cpu_type": cpu_type,
        "slice_offset": base,
        "slice_size": size,
        "linkedit": linkedit,
        "entries": entries,
        "violations": violations,
        "aligned": not violations,
    }


def _macho_file_report(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PipelineError("Mach-O audit path must be a regular file: {}".format(path))
    file_size = path.stat().st_size
    if file_size < 4:
        return None
    with path.open("rb") as stream:
        magic = _read_macho_bytes(stream, 0, 4, file_size, "file magic")
        if magic in _MACHO_32_MAGICS:
            raise PipelineError("32-bit Mach-O file is forbidden: {}".format(path))
        if magic in _MACHO_64_MAGICS:
            slices = [_macho_slice_report(stream, path, 0, file_size)]
            container = "thin"
        elif magic in _FAT_MAGICS:
            endian, uses_64 = _FAT_MAGICS[magic]
            count = struct.unpack(
                endian + "I",
                _read_macho_bytes(stream, 4, 4, file_size, "fat architecture count"),
            )[0]
            if count <= 0 or count > 64:
                raise PipelineError("invalid fat Mach-O architecture count: {}".format(path))
            entry_size = 32 if uses_64 else 20
            table = _read_macho_bytes(
                stream,
                8,
                count * entry_size,
                file_size,
                "fat architecture table",
            )
            slices = []
            ranges = []
            for index in range(count):
                offset = index * entry_size
                if uses_64:
                    cpu_type, _, slice_offset, slice_size, alignment, _ = (
                        struct.unpack_from(endian + "IIQQII", table, offset)
                    )
                else:
                    cpu_type, _, slice_offset, slice_size, alignment = (
                        struct.unpack_from(endian + "IIIII", table, offset)
                    )
                if (
                    slice_size <= 0
                    or slice_offset < 8 + count * entry_size
                    or slice_offset + slice_size > file_size
                    or alignment > 63
                    or slice_offset % (1 << alignment)
                ):
                    raise PipelineError("invalid fat Mach-O slice bounds: {}".format(path))
                ranges.append((slice_offset, slice_offset + slice_size))
                slices.append(
                    _macho_slice_report(
                        stream, path, slice_offset, slice_size, expected_cpu=cpu_type
                    )
                )
            ordered = sorted(ranges)
            if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
                raise PipelineError("overlapping fat Mach-O slices: {}".format(path))
            container = "fat64" if uses_64 else "fat32-header"
        else:
            return None
    return {
        "path": str(path),
        "bytes": file_size,
        "container": container,
        "slices": slices,
        "aligned": all(item["aligned"] for item in slices),
    }


def macho_linkedit_alignment_report(root, require_aligned=True):
    """Audit every 64-bit Mach-O slice under a real directory tree."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise PipelineError("Mach-O audit root must be a real directory")
    reports = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(directory)
        dirnames[:] = sorted(
            name for name in dirnames if not (directory / name).is_symlink()
        )
        for name in sorted(filenames):
            path = directory / name
            if path.is_symlink():
                continue
            if not path.is_file():
                raise PipelineError("special file in Mach-O audit tree: {}".format(path))
            report = _macho_file_report(path)
            if report is not None:
                report["relative_path"] = path.relative_to(root).as_posix()
                reports.append(report)
    if not reports:
        raise PipelineError("Mach-O audit found no binaries: {}".format(root))
    violations = []
    slice_count = 0
    for report in reports:
        slice_count += len(report["slices"])
        for slice_report in report["slices"]:
            for violation in slice_report["violations"]:
                violations.append(
                    {
                        "relative_path": report["relative_path"],
                        "architecture": slice_report["architecture"],
                        **violation,
                    }
                )
    if require_aligned and violations:
        first = violations[0]
        raise PipelineError(
            "misaligned LINKEDIT entry {} at {} in {} ({})".format(
                first["name"],
                first["offset"],
                first["relative_path"],
                first["architecture"],
            )
        )
    return {
        "schema": 1,
        "root": str(root),
        "macho_files": len(reports),
        "slices": slice_count,
        "files": reports,
        "violations": violations,
        "all_64_bit_linkedit_offsets_aligned": not violations,
        "pointer_alignment": 8,
        "code_signature_alignment": 16,
    }


def _linkedit_recovery_artifact(source, relative, contract):
    path = in_source(source, relative, "legacy LINKEDIT recovery artifact")
    kind = contract["kind"]
    if kind == "file":
        if path.is_symlink() or not path.is_file():
            raise PipelineError("missing legacy recovery file: {}".format(path))
        observed = sha256_file(path)
        size = path.stat().st_size
    elif kind == "tree":
        if path.is_symlink() or not path.is_dir():
            raise PipelineError("missing legacy recovery tree: {}".format(path))
        observed = tree_digest(path)
        size = physical_size(path)
    else:
        raise PipelineError("unknown LINKEDIT recovery artifact kind")
    if observed != contract["sha256"]:
        raise PipelineError("legacy recovery artifact hash mismatch: {}".format(relative))
    return {
        "relative_path": relative,
        "source": str(path),
        "archive_relative_path": "artifacts/" + relative,
        "kind": kind,
        "sha256": observed,
        "bytes": size,
    }


def linkedit_recovery_plan(source, developer_dir):
    """Plan the one supported recovery from already-stripped invalid slices."""
    acquisition_contract(source)
    tool_receipt_contract(source, developer_dir)
    preparation_contract(source, allow_reclaimed_arm=True)
    linkedit_receipt, _ = xcode27_linkedit_strip_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=True,
    )
    final_root = in_source(source, LINKEDIT_RECOVERY_ROOT, "LINKEDIT recovery root")
    partial_root = in_source(
        source, LINKEDIT_RECOVERY_PARTIAL, "LINKEDIT recovery partial root"
    )
    if (
        final_root.exists()
        or final_root.is_symlink()
        or partial_root.exists()
        or partial_root.is_symlink()
    ):
        raise PipelineError("LINKEDIT recovery destination already exists")
    arm_out = in_source(source, ARM_OUT, "reclaimed arm64 output")
    if os.path.lexists(str(arm_out)):
        raise PipelineError("arm64 output must still be reclaimed before recovery")
    for relative in (UNSIGNED_ROOT, SIGNED_ROOT):
        path = in_source(source, relative, "universal output recovery guard")
        if os.path.lexists(str(path)):
            raise PipelineError("refusing recovery after universal output exists")
    adhoc_receipt = in_source(
        source, ADHOC_RUNTIME_SIGNING_RECEIPT, "ad-hoc receipt recovery guard"
    )
    if os.path.lexists(str(adhoc_receipt)):
        raise PipelineError("canonical ad-hoc receipt must be absent for recovery")

    artifacts = [
        _linkedit_recovery_artifact(source, relative, contract)
        for relative, contract in LINKEDIT_RECOVERY_LEGACY_ARTIFACTS.items()
    ]
    arm_app = in_source(source, STAGED_ARM_APP, "legacy arm64 app")
    x64_app = in_source(
        source, X64_OUT + "/" + APP_NAME, "legacy x86_64 app"
    )
    legacy_alignment = {
        "arm64": macho_linkedit_alignment_report(
            arm_app, require_aligned=False
        ),
        "x86_64": macho_linkedit_alignment_report(
            x64_app, require_aligned=False
        ),
    }
    for architecture, report in legacy_alignment.items():
        if not report["violations"]:
            raise PipelineError(
                "legacy {} app has no LINKEDIT defect to recover".format(
                    architecture
                )
            )

    source_paths, packaging_paths = _adhoc_runtime_signing_paths(source)
    signing_state = {
        "source": _adhoc_runtime_signing_set_state(
            source_paths,
            ADHOC_RUNTIME_SIGNING_FILES,
            "LINKEDIT recovery signing source",
        ),
        "packaging": _adhoc_runtime_signing_set_state(
            packaging_paths,
            ADHOC_RUNTIME_SIGNING_GENERATED_FILES,
            "LINKEDIT recovery generated signing package",
        ),
        "adhoc_receipt_absent": True,
    }
    if signing_state["source"] != "post" or signing_state["packaging"] != "post":
        raise PipelineError(
            "LINKEDIT recovery requires matching post ad-hoc signing sources"
        )

    profiles = focus_macos.validate_gn_profiles()["profiles"]
    arm_args_text = profiles["arm64"]["args_gn"]
    arm_args_hash = hashlib.sha256(arm_args_text.encode("utf-8")).hexdigest()
    if arm_args_hash != SWIFTSHADER_DISABLED_ARGS_SHA256["arm64"]:
        raise PipelineError("recovered arm64 args.gn hash mismatch")
    x64_out = in_source(
        source, X64_OUT, "preserved x86_64 object output", must_exist=True, directory=True
    )
    x64_args = x64_out / "args.gn"
    if sha256_file(x64_args) != SWIFTSHADER_DISABLED_ARGS_SHA256["x64"]:
        raise PipelineError("preserved x86_64 args.gn hash mismatch")
    if not (x64_out / "obj").is_dir() or (x64_out / "obj").is_symlink():
        raise PipelineError("x86_64 object graph is unavailable for relink")
    return {
        "stage": "prepare-xcode27-linkedit-recovery",
        "source_root": str(source),
        "linkedit_strip_receipt": {
            "path": str(linkedit_receipt),
            "sha256": sha256_file(linkedit_receipt),
        },
        "recovery_root": str(final_root),
        "partial_root": str(partial_root),
        "manifest": str(in_source(source, LINKEDIT_RECOVERY_MANIFEST, "recovery manifest")),
        "artifacts": artifacts,
        "legacy_alignment": legacy_alignment,
        "signing_state": signing_state,
        "restore_arm_args": {
            "path": str(arm_out / "args.gn"),
            "sha256": arm_args_hash,
            "bytes": len(arm_args_text.encode("utf-8")),
        },
        "preserve_x64_objects": {
            "out": str(x64_out),
            "args_gn_sha256": sha256_file(x64_args),
            "incremental_relink": True,
        },
        "required_followup_stages": [
            "build-arm64",
            "stage-arm64",
            "build-x64",
            "apply-swiftshader-disabled-signing-compat",
            "apply-adhoc-runtime-signing-compat",
            "merge-sign-package",
        ],
        "postprocess_existing_binaries": False,
        "offline": True,
        "network_operations": 0,
    }


def _verify_linkedit_recovery_moved_artifact(root, artifact):
    """Re-hash and re-size one moved artifact without following its root."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise PipelineError("LINKEDIT recovery verification root is unsafe")
    relative = artifact.get("archive_relative_path")
    relative_path = Path(relative) if isinstance(relative, str) else None
    if (
        not isinstance(relative, str)
        or not relative.startswith("artifacts/")
        or "\\" in relative
        or relative_path.is_absolute()
        or ".." in relative_path.parts
    ):
        raise PipelineError("invalid LINKEDIT recovery archive path")
    destination = root / relative
    kind = artifact.get("kind")
    if kind == "file":
        if destination.is_symlink() or not destination.is_file():
            raise PipelineError(
                "moved LINKEDIT recovery file is unsafe: {}".format(relative)
            )
        observed_hash = sha256_file(destination)
        observed_bytes = destination.stat().st_size
    elif kind == "tree":
        if destination.is_symlink() or not destination.is_dir():
            raise PipelineError(
                "moved LINKEDIT recovery tree is unsafe: {}".format(relative)
            )
        observed_hash = tree_digest(destination)
        observed_bytes = physical_size(destination)
    else:
        raise PipelineError("unknown moved LINKEDIT recovery artifact kind")
    if (
        observed_hash != artifact.get("sha256")
        or observed_bytes != artifact.get("bytes")
    ):
        raise PipelineError(
            "moved LINKEDIT recovery artifact changed before publication: {}".format(
                relative
            )
        )
    return {
        "archive_relative_path": relative,
        "kind": kind,
        "sha256": observed_hash,
        "bytes": observed_bytes,
        "verified": True,
    }


def _linkedit_recovery_rollback_states(source, partial_root, artifacts):
    """Validate every exact source/archive pair before rollback mutation."""
    states = []
    for artifact in artifacts:
        source_path = Path(artifact["source"])
        expected_source = in_source(
            source,
            artifact["relative_path"],
            "LINKEDIT recovery rollback source",
        )
        if source_path != expected_source:
            raise PipelineError("LINKEDIT recovery rollback source changed")
        destination = Path(partial_root) / artifact["archive_relative_path"]
        source_exists = os.path.lexists(str(source_path))
        destination_exists = os.path.lexists(str(destination))
        if source_exists == destination_exists:
            raise PipelineError(
                "LINKEDIT recovery artifact must exist at exactly one location: {}".format(
                    artifact["relative_path"]
                )
            )
        if source_exists:
            observed = _linkedit_recovery_artifact(
                source,
                artifact["relative_path"],
                {
                    "kind": artifact["kind"],
                    "sha256": artifact["sha256"],
                },
            )
            if observed["bytes"] != artifact["bytes"]:
                raise PipelineError(
                    "LINKEDIT recovery source size changed during rollback: {}".format(
                        artifact["relative_path"]
                    )
                )
            location = "source"
        else:
            _verify_linkedit_recovery_moved_artifact(partial_root, artifact)
            location = "archive"
        states.append(
            {
                "artifact": artifact,
                "source": source_path,
                "destination": destination,
                "location": location,
            }
        )
    return states


def execute_linkedit_recovery(source, developer_dir, plan, allow_recovery_move):
    """Archive exact invalid evidence and prepare clean/relinkable outputs."""
    if not allow_recovery_move:
        raise PipelineError(
            "LINKEDIT recovery execution requires --allow-recovery-move"
        )
    expected = linkedit_recovery_plan(source, developer_dir)
    if plan != expected:
        raise PipelineError("LINKEDIT recovery plan changed before execution")
    require_free(source, SOFT_FLOOR_GIB, "LINKEDIT recovery")
    partial_root = Path(expected["partial_root"])
    final_root = Path(expected["recovery_root"])
    arm_args = Path(expected["restore_arm_args"]["path"])
    arm_out = arm_args.parent
    manifest_sha256 = None
    moved_verification = None
    try:
        partial_root.mkdir(parents=False, exist_ok=False)
        for artifact in expected["artifacts"]:
            source_path = Path(artifact["source"])
            destination = partial_root / artifact["archive_relative_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(source_path), str(destination))
        staged_arm_parent = in_source(
            source,
            str(Path(STAGED_ARM_APP).parent),
            "emptied staged arm64 directory",
            must_exist=True,
            directory=True,
        )
        staged_arm_parent.rmdir()
        arm_out.mkdir(parents=False, exist_ok=False)
        arm_args_text = focus_macos.validate_gn_profiles()["profiles"]["arm64"][
            "args_gn"
        ]
        prepare_source.atomic_publish_text(arm_args, arm_args_text)
        if sha256_file(arm_args) != expected["restore_arm_args"]["sha256"]:
            raise PipelineError("restored arm64 args.gn hash mismatch")
        manifest_value = {
            "schema": 1,
            "source_root": str(source),
            "linkedit_strip_receipt": expected["linkedit_strip_receipt"],
            "artifacts": expected["artifacts"],
            "legacy_alignment": expected["legacy_alignment"],
            "signing_state": expected["signing_state"],
            "restore_arm_args": expected["restore_arm_args"],
            "preserve_x64_objects": expected["preserve_x64_objects"],
            "required_followup_stages": expected["required_followup_stages"],
            "postprocess_existing_binaries": False,
            "offline": True,
            "network_operations": 0,
            "rebuild_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
            "moved_artifacts_verified_before_publication": True,
        }
        manifest_report = atomic_json(partial_root / "manifest.json", manifest_value)
        manifest_sha256 = manifest_report["sha256"]
        moved_verification = [
            _verify_linkedit_recovery_moved_artifact(partial_root, artifact)
            for artifact in expected["artifacts"]
        ]
        os.replace(str(partial_root), str(final_root))
    except BaseException as original_error:
        try:
            if os.path.lexists(str(final_root)):
                if os.path.lexists(str(partial_root)):
                    raise PipelineError(
                        "both partial and final LINKEDIT recovery roots exist"
                    )
                if final_root.is_symlink() or not final_root.is_dir():
                    raise PipelineError(
                        "published LINKEDIT recovery root is unsafe"
                    )
                final_manifest = final_root / "manifest.json"
                if (
                    manifest_sha256 is None
                    or final_manifest.is_symlink()
                    or not final_manifest.is_file()
                    or sha256_file(final_manifest) != manifest_sha256
                ):
                    raise PipelineError(
                        "published LINKEDIT recovery manifest cannot be normalized"
                    )
                for artifact in expected["artifacts"]:
                    _verify_linkedit_recovery_moved_artifact(
                        final_root, artifact
                    )
                os.replace(str(final_root), str(partial_root))
            rollback_states = _linkedit_recovery_rollback_states(
                source, partial_root, expected["artifacts"]
            )
            if arm_args.is_file() and not arm_args.is_symlink():
                if sha256_file(arm_args) != expected["restore_arm_args"]["sha256"]:
                    raise PipelineError("unsafe recovered arm64 args during rollback")
                arm_args.unlink()
            if arm_out.is_dir() and not arm_out.is_symlink():
                arm_out.rmdir()
            for state in reversed(rollback_states):
                if state["location"] != "archive":
                    continue
                source_path = state["source"]
                destination = state["destination"]
                source_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(destination), str(source_path))
            # Reconcile the complete fixed inventory, including an artifact whose
            # rename completed immediately before an asynchronous interruption.
            for artifact in expected["artifacts"]:
                source_report = _linkedit_recovery_artifact(
                    source,
                    artifact["relative_path"],
                    {
                        "kind": artifact["kind"],
                        "sha256": artifact["sha256"],
                    },
                )
                if source_report["bytes"] != artifact["bytes"]:
                    raise PipelineError(
                        "LINKEDIT recovery source size changed after rollback: {}".format(
                            artifact["relative_path"]
                        )
                    )
                destination = partial_root / artifact["archive_relative_path"]
                if os.path.lexists(str(destination)):
                    raise PipelineError(
                        "LINKEDIT recovery archive survived rollback: {}".format(
                            artifact["relative_path"]
                        )
                    )
            if partial_root.is_dir() and not partial_root.is_symlink():
                shutil.rmtree(str(partial_root))
        except BaseException as rollback_error:
            if os.path.lexists(str(final_root)):
                retained_root = str(final_root)
            elif os.path.lexists(str(partial_root)):
                retained_root = str(partial_root)
            else:
                retained_root = "<none>"
            raise PipelineError(
                "LINKEDIT recovery and rollback failed; recovery state retained at {}: "
                "original={!r}; rollback={!r}".format(
                    retained_root, original_error, rollback_error
                )
            ) from original_error
        raise
    manifest = final_root / "manifest.json"
    return {
        "stage": "prepare-xcode27-linkedit-recovery",
        "prepared": True,
        "recovery_root": str(final_root),
        "manifest": {"path": str(manifest), "sha256": sha256_file(manifest)},
        "restored_arm_args": expected["restore_arm_args"],
        "x64_objects_preserved": expected["preserve_x64_objects"],
        "required_followup_stages": expected["required_followup_stages"],
        "moved_artifacts": moved_verification,
        "postprocess_existing_binaries": False,
    }


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
    linkedit = macho_linkedit_alignment_report(app, require_aligned=True)
    return {
        "app": str(app),
        "bundle_id": focus_macos.BUNDLE_ID,
        "executable": str(executable),
        "architectures": sorted(observed),
        "linkedit_alignment": linkedit,
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
    allow_reclaimed_arm = architecture == "x64"
    xcode27_path, _ = xcode27_compat_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
    seatbelt_path, _ = xcode27_seatbelt_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
    screen_ai_path, _ = screen_ai_disabled_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
    linkedit_path, linkedit_receipt = xcode27_linkedit_strip_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
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
        "xcode27_compatibility": {
            "path": str(xcode27_path),
            "sha256": sha256_file(xcode27_path),
        },
        "xcode27_seatbelt_compatibility": {
            "path": str(seatbelt_path),
            "sha256": sha256_file(seatbelt_path),
        },
        "screen_ai_disabled_compatibility": {
            "path": str(screen_ai_path),
            "sha256": sha256_file(screen_ai_path),
        },
        "xcode27_linkedit_strip_compatibility": {
            "path": str(linkedit_path),
            "sha256": sha256_file(linkedit_path),
        },
        "linkedit_strip_tools": linkedit_receipt["tools"],
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
    allow_reclaimed_arm = plan["architecture"] == "x64"
    xcode27_path, _ = xcode27_compat_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
    current_xcode27 = {
        "path": str(xcode27_path),
        "sha256": sha256_file(xcode27_path),
    }
    if plan.get("xcode27_compatibility") != current_xcode27:
        raise PipelineError(
            "build plan Xcode 27 compatibility provenance changed before execution"
        )
    seatbelt_path, _ = xcode27_seatbelt_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
    current_seatbelt = {
        "path": str(seatbelt_path),
        "sha256": sha256_file(seatbelt_path),
    }
    if plan.get("xcode27_seatbelt_compatibility") != current_seatbelt:
        raise PipelineError(
            "build plan Xcode 27 Seatbelt provenance changed before execution"
        )
    screen_ai_path, _ = screen_ai_disabled_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
    current_screen_ai = {
        "path": str(screen_ai_path),
        "sha256": sha256_file(screen_ai_path),
    }
    if plan.get("screen_ai_disabled_compatibility") != current_screen_ai:
        raise PipelineError(
            "build plan disabled ScreenAI provenance changed before execution"
        )
    linkedit_path, linkedit_receipt = xcode27_linkedit_strip_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
    )
    current_linkedit = {
        "path": str(linkedit_path),
        "sha256": sha256_file(linkedit_path),
    }
    if (
        plan.get("xcode27_linkedit_strip_compatibility") != current_linkedit
        or plan.get("linkedit_strip_tools") != linkedit_receipt["tools"]
    ):
        raise PipelineError(
            "build plan Xcode 27 LINKEDIT strip provenance changed"
        )
    environment = safe_environment(
        source, developer_dir, build_ninja=Path(current_ninja["path"])
    )
    run_monitored(plan["commands"][0], source, environment)
    generated_linkedit = generated_linkedit_strip_contract(
        Path(plan["out"]), linkedit_receipt["tools"]
    )
    run_monitored(plan["commands"][1], source, environment)
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
        "xcode27_compatibility_receipt_sha256": current_xcode27["sha256"],
        "xcode27_seatbelt_compatibility_receipt_sha256": current_seatbelt[
            "sha256"
        ],
        "screen_ai_disabled_compatibility_receipt_sha256": current_screen_ai[
            "sha256"
        ],
        "xcode27_linkedit_strip_compatibility_receipt_sha256": current_linkedit[
            "sha256"
        ],
        "generated_linkedit_strip": generated_linkedit,
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
    linkedit_receipt_path, _ = xcode27_linkedit_strip_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=True,
    )
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
    swiftshader_receipt_path, _ = swiftshader_disabled_signing_receipt_contract(
        source, developer_dir, allow_adhoc_runtime_signing=True
    )
    adhoc_receipt_path, _ = adhoc_runtime_signing_receipt_contract(
        source, developer_dir
    )
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
    packaging_python = packaging_python_contract(source)
    python = packaging_python["path"]
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
            python,
            str(universalizer),
            str(x64_app),
            str(arm_app),
            str(unsigned_app),
        ],
        "sign": [
            python,
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
            python,
            str(MACOS_DIR / "package_local_dmg.py"),
            "--app",
            str(signed_root / SIGNED_DISTRIBUTION_DIR / APP_NAME),
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
        "packaging_python": packaging_python,
        "swiftshader_disabled_signing": {
            "path": str(swiftshader_receipt_path),
            "sha256": sha256_file(swiftshader_receipt_path),
        },
        "adhoc_runtime_signing": {
            "path": str(adhoc_receipt_path),
            "sha256": sha256_file(adhoc_receipt_path),
        },
        "xcode27_linkedit_strip_compatibility": {
            "path": str(linkedit_receipt_path),
            "sha256": sha256_file(linkedit_receipt_path),
        },
        "runtime_acceptance": {
            "signed_app_before_packaging": True,
            "mounted_final_dmg": True,
            "private_candidate_mode": "0700",
            "final_output_absent_until_runtime_passes": True,
            "atomic_no_overwrite_publish": True,
            "architectures": ["arm64", "x86_64"],
            "native_arm64_required": True,
            "rosetta_x86_64_required": True,
            "fresh_profiles": True,
            "incognito": True,
            "offline_navigation": "data:text/html",
            "timeout_seconds": runtime_smoke.DEFAULT_TIMEOUT_SECONDS,
        },
        "developer_dir": str(developer_dir),
    }


def _unlink_created_dmg(output, identity):
    """Remove only the exact regular DMG inode created by this execution."""
    output = Path(output)
    if not os.path.lexists(str(output)):
        return False
    observed = os.lstat(str(output))
    if (
        not stat.S_ISREG(observed.st_mode)
        or (observed.st_dev, observed.st_ino) != tuple(identity)
    ):
        raise PipelineError(
            "refusing to remove a changed DMG after acceptance failure"
        )
    output.unlink()
    if os.path.lexists(str(output)):
        raise PipelineError("failed to remove rejected DMG output")
    return True


def _private_candidate_root_identity(root):
    """Require the exact private directory used for one unpublished DMG."""
    root = Path(root)
    observed = os.lstat(str(root))
    if (
        not stat.S_ISDIR(observed.st_mode)
        or stat.S_ISLNK(observed.st_mode)
        or stat.S_IMODE(observed.st_mode) != 0o700
        or observed.st_uid != os.geteuid()
    ):
        raise PipelineError("DMG runtime candidate root is not private")
    return observed.st_dev, observed.st_ino


def _create_private_dmg_candidate(output):
    """Create an owner-only same-filesystem root for an unpublished DMG."""
    output = Path(output)
    if os.path.lexists(str(output)):
        raise PipelineError("DMG output appeared before candidate packaging")
    temporary_root = Path(
        tempfile.mkdtemp(
            dir=str(output.parent),
            prefix=DMG_RUNTIME_CANDIDATE_PREFIX,
        )
    )
    try:
        os.chmod(str(temporary_root), 0o700)
        identity = _private_candidate_root_identity(temporary_root)
    except BaseException:
        try:
            temporary_root.rmdir()
        except OSError:
            pass
        raise
    candidate = temporary_root / output.name
    if os.path.lexists(str(candidate)):
        raise PipelineError("private DMG candidate unexpectedly exists")
    return temporary_root, identity, candidate


def _candidate_package_command(command, planned_output, candidate):
    """Retarget only the planned package output to the private candidate."""
    command = list(command)
    indexes = [index for index, value in enumerate(command) if value == "--output"]
    if len(indexes) != 1 or indexes[0] + 1 >= len(command):
        raise PipelineError("DMG package command must have one --output value")
    output_index = indexes[0] + 1
    if command[output_index] != str(planned_output):
        raise PipelineError("DMG package command output changed before execution")
    command[output_index] = str(candidate)
    return command


def _cleanup_private_dmg_candidate(
    temporary_root, root_identity, candidate, candidate_identity=None
):
    """Unlink only the expected candidate, then remove its exact empty root."""
    temporary_root = Path(temporary_root)
    candidate = Path(candidate)
    if candidate.parent != temporary_root:
        raise PipelineError("DMG runtime candidate escaped its private root")
    if _private_candidate_root_identity(temporary_root) != tuple(root_identity):
        raise PipelineError("DMG runtime candidate root identity changed")
    if os.path.lexists(str(candidate)):
        observed = os.lstat(str(candidate))
        if not stat.S_ISREG(observed.st_mode):
            raise PipelineError("refusing to remove unsafe DMG runtime candidate")
        if candidate_identity is not None and (
            observed.st_dev,
            observed.st_ino,
        ) != tuple(candidate_identity):
            raise PipelineError("DMG runtime candidate identity changed")
        candidate.unlink()
    try:
        temporary_root.rmdir()
    except OSError as exc:
        raise PipelineError(
            "private DMG runtime candidate root is not safely empty; retained at {}".format(
                temporary_root
            )
        ) from exc


def _publish_accepted_dmg(candidate, candidate_identity, output, size, digest):
    """No-overwrite publish the exact runtime-accepted inode."""
    candidate = Path(candidate)
    output = Path(output)
    observed = os.lstat(str(candidate))
    if (
        not stat.S_ISREG(observed.st_mode)
        or (observed.st_dev, observed.st_ino) != tuple(candidate_identity)
        or observed.st_size != size
        or package_local_dmg.sha256_file(candidate) != digest
    ):
        raise PipelineError("DMG runtime candidate changed before publication")
    if os.path.lexists(str(output)):
        raise PipelineError("refusing to overwrite DMG output created during acceptance")
    placed = False
    try:
        try:
            os.link(str(candidate), str(output), follow_symlinks=False)
        except FileExistsError as exc:
            raise PipelineError(
                "refusing to overwrite DMG output created during acceptance"
            ) from exc
        except OSError as exc:
            raise PipelineError(
                "failed to atomically publish accepted DMG: {}".format(exc)
            ) from exc
        placed = True
        published = os.lstat(str(output))
        if (
            not stat.S_ISREG(published.st_mode)
            or (published.st_dev, published.st_ino) != tuple(candidate_identity)
            or published.st_size != size
            or package_local_dmg.sha256_file(output) != digest
        ):
            raise PipelineError("published DMG does not match accepted candidate")
        candidate.unlink()
        return published
    except BaseException as original_error:
        if placed:
            try:
                _unlink_created_dmg(output, candidate_identity)
            except BaseException as cleanup_error:
                raise PipelineError(
                    "accepted DMG publication failed and output rollback also failed: "
                    "original={!r}; rollback={!r}".format(
                        original_error, cleanup_error
                    )
                ) from original_error
        raise


def execute_merge(source, developer_dir, plan):
    current_python = packaging_python_contract(source)
    if plan.get("packaging_python") != current_python:
        raise PipelineError("packaging Python changed before merge execution")
    for name in ("universalize", "sign", "package"):
        command = plan.get("commands", {}).get(name)
        if (
            not isinstance(command, list)
            or not command
            or command[0] != current_python["path"]
        ):
            raise PipelineError("merge command does not use pinned packaging Python")
    planned_output = plan.get("dmg_output")
    if not isinstance(planned_output, str) or not planned_output:
        raise PipelineError("merge plan is missing its DMG output")
    output = resolve_absent_dmg(planned_output)
    if str(output) != planned_output:
        raise PipelineError("planned DMG output changed before merge execution")
    _candidate_package_command(plan["commands"]["package"], output, output)
    linkedit_receipt_path, _ = xcode27_linkedit_strip_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=True,
    )
    current_linkedit = {
        "path": str(linkedit_receipt_path),
        "sha256": sha256_file(linkedit_receipt_path),
    }
    if plan.get("xcode27_linkedit_strip_compatibility") != current_linkedit:
        raise PipelineError(
            "Xcode 27 LINKEDIT strip provenance changed before merge"
        )
    swiftshader_receipt_path, _ = swiftshader_disabled_signing_receipt_contract(
        source, developer_dir, allow_adhoc_runtime_signing=True
    )
    current_swiftshader = {
        "path": str(swiftshader_receipt_path),
        "sha256": sha256_file(swiftshader_receipt_path),
    }
    if plan.get("swiftshader_disabled_signing") != current_swiftshader:
        raise PipelineError(
            "disabled SwiftShader signing provenance changed before merge"
        )
    adhoc_receipt_path, _ = adhoc_runtime_signing_receipt_contract(
        source, developer_dir
    )
    current_adhoc = {
        "path": str(adhoc_receipt_path),
        "sha256": sha256_file(adhoc_receipt_path),
    }
    if plan.get("adhoc_runtime_signing") != current_adhoc:
        raise PipelineError(
            "ad-hoc runtime signing provenance changed before merge"
        )
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
    for relative, hashes in ADHOC_RUNTIME_SIGNING_GENERATED_FILES.items():
        copied_source = (
            unsigned_root / PACKAGING_NAME / "signing" / Path(relative).name
        )
        if sha256_file(copied_source) != hashes["post_sha256"]:
            raise PipelineError(
                "copied ad-hoc signing source hash mismatch: {}".format(
                    Path(relative).name
                )
            )
    if packaging_python_contract(source) != current_python:
        raise PipelineError("packaging Python changed before signing")
    run_monitored(plan["commands"]["sign"], source, environment)
    signed_app = (
        Path(plan["signed_root"]) / SIGNED_DISTRIBUTION_DIR / APP_NAME
    )
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
    signed_app_digest = tree_digest(signed_app)
    signing_matrix = runtime_smoke.validate_adhoc_signing_matrix(signed_app)
    signed_runtime = runtime_smoke.validate_universal_app_runtime(signed_app)
    if tree_digest(signed_app) != signed_app_digest:
        raise PipelineError("signed app changed during runtime acceptance")
    universal_size = physical_size(signed_app)
    package_required = SOFT_FLOOR_GIB + 5 + (3 * universal_size) / GIB
    require_free(source, package_required, "DMG packaging source")
    require_free(output.parent, package_required, "DMG packaging output")
    if packaging_python_contract(source) != current_python:
        raise PipelineError("packaging Python changed before DMG packaging")
    temporary_root, root_identity, candidate = _create_private_dmg_candidate(
        output
    )
    candidate_identity = None
    published_identity = None
    try:
        package_command = _candidate_package_command(
            plan["commands"]["package"], output, candidate
        )
        run_monitored(
            package_command,
            source,
            environment,
            watched_paths=(source, temporary_root),
        )
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or candidate.stat().st_size <= 0
        ):
            raise PipelineError(
                "DMG packager did not create the expected private candidate"
            )
        candidate_stat = os.lstat(str(candidate))
        if not stat.S_ISREG(candidate_stat.st_mode):
            raise PipelineError("DMG packager candidate is no longer regular")
        candidate_identity = (candidate_stat.st_dev, candidate_stat.st_ino)
        mounted_runtime = runtime_smoke.validate_mounted_dmg_runtime(candidate)
        app_identity = package_local_dmg.validate_app(signed_app)
        if app_identity["architectures"] != ["arm64", "x86_64"]:
            raise PipelineError("packaged app is no longer universal")
        accepted_stat = os.lstat(str(candidate))
        if (
            not stat.S_ISREG(accepted_stat.st_mode)
            or (accepted_stat.st_dev, accepted_stat.st_ino) != candidate_identity
            or accepted_stat.st_size != mounted_runtime["size_bytes"]
        ):
            raise PipelineError("DMG candidate changed after mounted runtime acceptance")
        output_digest = package_local_dmg.sha256_file(candidate)
        if output_digest != mounted_runtime["sha256"]:
            raise PipelineError("DMG candidate hash changed after runtime acceptance")
        published_stat = _publish_accepted_dmg(
            candidate,
            candidate_identity,
            output,
            accepted_stat.st_size,
            output_digest,
        )
        published_identity = (published_stat.st_dev, published_stat.st_ino)
        _cleanup_private_dmg_candidate(
            temporary_root,
            root_identity,
            candidate,
            candidate_identity,
        )
        final_stat = os.lstat(str(output))
        if (
            not stat.S_ISREG(final_stat.st_mode)
            or (final_stat.st_dev, final_stat.st_ino) != published_identity
            or final_stat.st_size != accepted_stat.st_size
            or package_local_dmg.sha256_file(output) != output_digest
        ):
            raise PipelineError("published DMG changed after atomic placement")
        mounted_runtime = dict(mounted_runtime)
        mounted_runtime["published_dmg"] = str(output)
        mounted_runtime["published_same_inode"] = True
    except BaseException as original_error:
        if isinstance(original_error, runtime_smoke.DmgDetachError):
            raise PipelineError(
                "DMG acceptance could not prove that the image detached; the final "
                "output was not published and the private backing candidate was "
                "retained for manual detach at {}: {!r}".format(
                    candidate, original_error
                )
            ) from original_error
        cleanup_errors = []
        if published_identity is not None:
            try:
                _unlink_created_dmg(output, published_identity)
            except BaseException as cleanup_error:
                cleanup_errors.append("published output={!r}".format(cleanup_error))
        try:
            if os.path.lexists(str(temporary_root)):
                _cleanup_private_dmg_candidate(
                    temporary_root,
                    root_identity,
                    candidate,
                    candidate_identity,
                )
        except BaseException as cleanup_error:
            cleanup_errors.append("private candidate={!r}".format(cleanup_error))
        if cleanup_errors:
            raise PipelineError(
                "DMG acceptance failed and safe cleanup also failed: "
                "original={!r}; cleanup={}".format(
                    original_error, "; ".join(cleanup_errors)
                )
            ) from original_error
        raise
    report = {
        "app": str(signed_app),
        "output": str(output),
        "bundle_id": app_identity["bundle_id"],
        "executable": app_identity["executable"],
        "architectures": app_identity["architectures"],
        "require_universal": True,
        "format": "UDZO",
        "size_bytes": final_stat.st_size,
        "sha256": output_digest,
        "signature": (
            "ad-hoc; exact nested policy, signed-app runtime, and mounted-DMG "
            "runtime verified"
        ),
        "signing_performed": True,
        "notarization_performed": False,
        "local_only": True,
        "packaging_python": current_python,
        "swiftshader_disabled_signing": current_swiftshader,
        "adhoc_runtime_signing": current_adhoc,
        "xcode27_linkedit_strip_compatibility": current_linkedit,
        "codesign_matrix": signing_matrix,
        "runtime_acceptance": {
            "signed_app": signed_runtime,
            "mounted_final_dmg": mounted_runtime,
        },
    }
    report["signed_app"] = str(signed_app)
    report["signed_app_tree_sha256"] = signed_app_digest
    report["notarized"] = False
    report["developer_id_signed"] = False
    return report


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)
    for name in (
        "bootstrap-tools",
        "apply-gn-compat",
        "apply-xcode27-compat",
        "apply-xcode27-seatbelt-compat",
        "apply-screen-ai-disabled-compat",
        "apply-xcode27-linkedit-strip-compat",
        "prepare-xcode27-linkedit-recovery",
        "apply-swiftshader-disabled-signing-compat",
        "apply-adhoc-runtime-signing-compat",
        "build-arm64",
        "stage-arm64",
        "build-x64",
        "merge-sign-package",
    ):
        child = subparsers.add_parser(name)
        child.add_argument("--source-root", required=True)
        child.add_argument("--execute", action="store_true")
        child.add_argument("--json", action="store_true")
        if name not in ("apply-gn-compat", "stage-arm64"):
            child.add_argument("--developer-dir", required=True)
        if name == "stage-arm64":
            child.add_argument("--allow-reclaim-arm64-out", action="store_true")
        if name == "prepare-xcode27-linkedit-recovery":
            child.add_argument("--allow-recovery-move", action="store_true")
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
        elif args.command == "apply-gn-compat":
            plan = gn_compat_plan(source)
            result = execute_gn_compat(source, plan) if args.execute else plan
        elif args.command == "apply-xcode27-compat":
            plan = xcode27_compat_plan(source, developer_dir)
            result = (
                execute_xcode27_compat(source, developer_dir, plan)
                if args.execute
                else plan
            )
        elif args.command == "apply-xcode27-seatbelt-compat":
            plan = xcode27_seatbelt_plan(source, developer_dir)
            result = (
                execute_xcode27_seatbelt(source, developer_dir, plan)
                if args.execute
                else plan
            )
        elif args.command == "apply-screen-ai-disabled-compat":
            plan = screen_ai_disabled_plan(source, developer_dir)
            result = (
                execute_screen_ai_disabled(source, developer_dir, plan)
                if args.execute
                else plan
            )
        elif args.command == "apply-xcode27-linkedit-strip-compat":
            plan = xcode27_linkedit_strip_plan(source, developer_dir)
            result = (
                execute_xcode27_linkedit_strip(source, developer_dir, plan)
                if args.execute
                else plan
            )
        elif args.command == "prepare-xcode27-linkedit-recovery":
            plan = linkedit_recovery_plan(source, developer_dir)
            result = (
                execute_linkedit_recovery(
                    source, developer_dir, plan, args.allow_recovery_move
                )
                if args.execute
                else plan
            )
        elif args.command == "apply-swiftshader-disabled-signing-compat":
            plan = swiftshader_disabled_signing_plan(source, developer_dir)
            result = (
                execute_swiftshader_disabled_signing(source, developer_dir, plan)
                if args.execute
                else plan
            )
        elif args.command == "apply-adhoc-runtime-signing-compat":
            plan = adhoc_runtime_signing_plan(source, developer_dir)
            result = (
                execute_adhoc_runtime_signing(source, developer_dir, plan)
                if args.execute
                else plan
            )
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
        runtime_smoke.RuntimeSmokeError,
        OSError,
        ValueError,
        plistlib.InvalidFileException,
    ) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
