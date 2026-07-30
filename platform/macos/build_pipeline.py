#!/usr/bin/env python3
"""Run the reviewed low-space native Focus Browser macOS build stages.

Every command is a dry run unless ``--execute`` is present.  The stages are
deliberately separate so a 16 GiB Mac can build arm64, preserve the thin app,
reclaim only that measured output, and then build x86_64.  This tool never
publishes, notarizes, uses a Developer ID, changes xcode-select, or targets a
non-macOS platform.
"""

import argparse
import ctypes
import errno
import hashlib
import json
import os
import platform
import plistlib
import re
import selectors
import shutil
import shlex
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


MACOS_DIR = Path(__file__).resolve().parent
if str(MACOS_DIR) not in sys.path:
    sys.path.insert(0, str(MACOS_DIR))

import acquire_chromium  # pylint: disable=wrong-import-position
import focus_macos  # pylint: disable=wrong-import-position
import onboarding_alias_compat  # pylint: disable=wrong-import-position
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
FRESH_X64_PREPARATION_RECEIPT = "out/FocusMacFreshX64Preparation.json"
FRESH_X64_LEGACY_ROOT = "out/FocusMacX64LegacyInvalid"
FRESH_X64_TRANSACTION_ROOT = "out/.FocusMacX64LegacyInvalid.part"
FRESH_X64_TRANSACTION_PREPARED = "prepared.json"
FRESH_X64_FAILED_ROOT = "out/FocusMacX64FreshFailed"
FRESH_X64_TRANSACTION_FAILED_ROOT = "out/FocusMacX64TransactionFailed"
FRESH_X64_RECEIPT_FAILED = "out/FocusMacFreshX64PreparationFailed.json"
STAGING_ROOT = "out/FocusMacStaging"
STAGED_ARM_APP = STAGING_ROOT + "/arm64/" + APP_NAME
STAGE_RECEIPT = STAGING_ROOT + "/arm64-receipt.json"
RECLAIM_RECEIPT = STAGING_ROOT + "/arm64-reclaim-complete.json"
HOME_ALIAS_RECEIPT = "out/FocusMacHomeAliasCompatibility.json"
HOME_ALIAS_RECEIPT_SCHEMA = 2
RESUMED_SLICE_RECEIPT_SCHEMA = 2
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
SIGNING_TRANSACTION_ROOT = "out/FocusMacSigningTransactions"
SWIFTSHADER_SIGNING_TRANSACTION = SIGNING_TRANSACTION_ROOT + "/swiftshader"
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
ADHOC_SIGNING_TRANSACTION = SIGNING_TRANSACTION_ROOT + "/adhoc"
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
MAX_RESUME_STDOUT_BYTES = 128 * 1024 * 1024
MAX_NO_WORK_OUTPUT_BYTES = 1024 * 1024
LEGACY_LLVM_STRIP_TOKEN = (
    "../../third_party/llvm-build/Release+Asserts/bin/llvm-strip"
)
LEGACY_X64_TOOLCHAIN_FILE_COUNT = 6
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


class ReceiptPublication(dict):
    """Public receipt report carrying a non-serialized inode capability."""

    def __init__(self, value, publication_identity):
        super().__init__(value)
        self.publication_identity = publication_identity


@dataclass(frozen=True)
class AliasContext:
    """One immutable logical-to-physical home relocation contract.

    ``st_dev`` is deliberately not part of the durable identity: APFS may
    renumber it across boots.  The stable volume UUID, path, owner, and inode
    bind persisted evidence; current logical/physical pairs must still have
    the same device and inode while a contract is evaluated.
    """

    logical_home: Path
    physical_home: Path
    logical_workspace: Path
    physical_workspace: Path
    logical_source: Path
    physical_source: Path
    logical_developer: Path
    physical_developer: Path
    logical_repo: Path
    physical_repo: Path
    volume_uuid: str

    def pairs(self):
        return (
            ("workspace", self.logical_workspace, self.physical_workspace),
            ("source", self.logical_source, self.physical_source),
            ("developer", self.logical_developer, self.physical_developer),
            ("repo", self.logical_repo, self.physical_repo),
        )

    def project(self, value):
        """Project a verified physical path into the recorded logical tree."""
        path = Path(value)
        if not path.is_absolute() or Path(os.path.abspath(str(path))) != path:
            raise PipelineError("projected path must be absolute and normalized")
        pairs = sorted(
            self.pairs(), key=lambda item: len(item[2].parts), reverse=True
        )
        for _, logical, physical in pairs:
            try:
                return logical / path.relative_to(physical)
            except ValueError:
                pass
            try:
                path.relative_to(logical)
                return path
            except ValueError:
                pass
        raise PipelineError(
            "path is outside the explicit home-alias mappings: {}".format(path)
        )


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
    published = False
    temporary_identity = None
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(
                    str(temporary),
                    str(path),
                    src_dir_fd=None,
                    dst_dir_fd=None,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise PipelineError(
                    "refusing to replace receipt: {}".format(path)
                ) from exc
            published = True
            temporary_identity = _stat_identity_value(os.fstat(stream.fileno()))
        try:
            _unlink_regular_identity(
                temporary, temporary_identity, "temporary receipt"
            )
        except (OSError, PipelineError):
            # The published inode is already complete and immutable. A stale
            # private hard link is cleanup-only and must not turn publication
            # into a false failure.
            pass
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        current_identity = _lstat_identity(path)
        if (
            not stat.S_ISREG(current_identity["mode"])
            or temporary_identity is None
            or (current_identity["device"], current_identity["inode"])
            != (temporary_identity["device"], temporary_identity["inode"])
            or current_identity["uid"] != temporary_identity["uid"]
            or current_identity["gid"] != temporary_identity["gid"]
            or current_identity["mode"] != temporary_identity["mode"]
            or current_identity["bytes"] != temporary_identity["bytes"]
            or current_identity["mtime_ns"] != temporary_identity["mtime_ns"]
        ):
            raise PipelineError("receipt path changed during atomic publication")
    except Exception:
        if (
            not published
            and temporary_identity is not None
            and os.path.lexists(str(temporary))
        ):
            _unlink_regular_identity(
                temporary, temporary_identity, "failed temporary receipt"
            )
        raise
    return ReceiptPublication(
        {"path": str(path), "sha256": hashlib.sha256(
            (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
        ).hexdigest()},
        current_identity,
    )


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


def resolve_source(
    value, allow_recorded_home_alias=False, allow_unrecorded_home_alias=False
):
    supplied = Path(os.path.abspath(os.path.expanduser(value)))
    if supplied.is_symlink():
        raise PipelineError("source root must not be a symlink")
    try:
        physical, _ = focus_macos.resolve_source_root(str(supplied))
    except focus_macos.ContractError as exc:
        raise PipelineError(str(exc)) from exc
    if supplied == physical:
        return physical
    if not (allow_recorded_home_alias or allow_unrecorded_home_alias):
        raise PipelineError(
            "source root resolves through a symlink; use the explicit home-alias "
            "compatibility workflow"
        )
    if allow_recorded_home_alias:
        receipt = supplied / HOME_ALIAS_RECEIPT
        if receipt.is_symlink() or not receipt.is_file():
            raise PipelineError("recorded home-alias compatibility receipt is missing")
    return supplied


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
        cursor.resolve(strict=False).relative_to(source.resolve(strict=True))
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
    if Path(verification.get("source_root", "")).resolve() != source.resolve():
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


def safe_environment(
    source,
    developer_dir,
    inherited=None,
    build_ninja=None,
    alias_context=None,
):
    inherited = os.environ if inherited is None else inherited
    if Path(source).resolve(strict=True) != Path(source) and alias_context is None:
        raise PipelineError(
            "logical alias source requires an explicit validated AliasContext"
        )
    result = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
    inherited_home = inherited.get("HOME")
    if inherited_home:
        home_path = Path(inherited_home)
        home_is_symlink = home_path.is_symlink()
        if (
            not home_path.is_absolute()
            or any(ord(character) < 0x20 for character in inherited_home)
            or not home_path.is_dir()
        ):
            raise PipelineError("inherited HOME is not a safe real absolute directory")
        if home_is_symlink:
            if (
                not isinstance(alias_context, AliasContext)
                or home_path != alias_context.logical_home
                or Path(source) != alias_context.logical_source
                or Path(developer_dir) != alias_context.logical_developer
                or _recorded_alias_context(source, developer_dir) != alias_context
            ):
                raise PipelineError(
                    "symlink HOME is not the revalidated recorded home alias"
                )
        elif alias_context is not None:
            raise PipelineError("alias context requires its exact logical HOME")
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


def _process_group_exists(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS can transiently report EPERM while a just-signalled orphaned
        # group is being reparented.  It still means the PGID has not yet
        # disappeared, so keep waiting and fail closed if it persists.
        return True
    return True


def _wait_process_group_absent(pgid, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while _process_group_exists(pgid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _stop_process(process, force=False, grace_seconds=5):
    """Terminate the entire new-session group, even if its leader exited."""
    pgid = process.pid
    signal_to_send = signal.SIGKILL if force else signal.SIGINT
    try:
        os.killpg(pgid, signal_to_send)
    except ProcessLookupError:
        try:
            process.wait(timeout=0)
        except (subprocess.TimeoutExpired, ChildProcessError):
            pass
        return
    deadline = time.monotonic() + (0 if force else grace_seconds)
    try:
        while not force and _process_group_exists(pgid):
            if time.monotonic() >= deadline:
                break
            process.poll()
            time.sleep(0.05)
    finally:
        if _process_group_exists(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if _process_group_exists(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        process.wait(timeout=10)
    if not _wait_process_group_absent(pgid, 2):
        raise PipelineError(
            "process group {} survived unconditional SIGKILL".format(pgid)
        )


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
    try:
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
        if _process_group_exists(process.pid):
            _stop_process(process, force=True)
            raise PipelineError("command leader exited while descendants remained")
        if stopped:
            raise PipelineError(
                "{} disk floor crossed; build process stopped (return {})".format(
                    stopped, returncode
                )
            )
        if returncode:
            raise PipelineError(
                "command failed with exit {}: {}".format(
                    returncode, " ".join(command)
                )
            )
        for path in watched:
            require_free(path, SOFT_FLOOR_GIB, "post-command {}".format(path))
    except BaseException as original_error:
        if "process" in locals():
            try:
                _stop_process(process, force=False)
            except BaseException as cleanup_error:
                raise PipelineError(
                    "command failed and its process group could not be stopped: "
                    "original={!r}; cleanup={!r}".format(
                        original_error, cleanup_error
                    )
                ) from original_error
        raise


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


def _path_hash_report_matches(report, current_path, expected_hash, alias_context=None):
    """Accept one immutable path report through only the proven home alias."""
    if not isinstance(report, dict) or set(report) != {"path", "sha256"}:
        return False
    if report.get("sha256") != expected_hash:
        return False
    current_path = Path(current_path)
    recorded = Path(report.get("path", ""))
    if not recorded.is_absolute() or not current_path.is_absolute():
        return False
    if alias_context is not None:
        try:
            expected_path = alias_context.project(current_path)
        except PipelineError:
            return False
        if recorded == expected_path:
            return True
    try:
        return recorded.resolve(strict=True) == current_path.resolve(strict=True)
    except OSError:
        return False


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


def gn_compat_receipt_contract(
    source, preparation_receipt_path, required=True, alias_context=None
):
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
    if (
        receipt.get("source_root") != str(source)
        or receipt.get("preparation_receipt") != expected_preparation
        or not _path_hash_report_matches(
            receipt.get("patch"),
            GN_COMPAT_PATCH,
            GN_COMPAT_PATCH_SHA256,
            alias_context,
        )
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
    receipt_publication_identity = None
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
        receipt_publication_identity = getattr(
            receipt_report, "publication_identity", None
        )
        gn_compat_receipt_contract(
            source, Path(expected["preparation_receipt"]["path"]), required=True
        )
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            _remove_failed_execution_receipt(
                receipt_path,
                receipt_publication_identity,
                lambda: gn_compat_receipt_contract(
                    source,
                    Path(expected["preparation_receipt"]["path"]),
                    required=True,
                ),
                "GN compatibility receipt",
            )
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
    source, developer_dir=None, allow_reclaimed_arm=False, alias_context=None
):
    """Resolve the exact preparation, optional GN fix, and tool receipts."""
    preparation_path, _ = preparation_contract(
        source,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    tool_path, _ = tool_receipt_contract(source, developer_dir)
    gn_path = in_source(source, GN_COMPAT_RECEIPT, "GN compatibility receipt")
    if gn_path.exists():
        gn_path, _ = gn_compat_receipt_contract(
            source,
            preparation_path,
            required=True,
            alias_context=alias_context,
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
    source,
    developer_dir=None,
    required=True,
    allow_reclaimed_arm=False,
    alias_context=None,
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
        source,
        developer_dir,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
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
        or not _path_hash_report_matches(
            receipt.get("patch"),
            XCODE27_COMPAT_PATCH,
            XCODE27_COMPAT_PATCH_SHA256,
            alias_context,
        )
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
    receipt_publication_identity = None
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
        receipt_publication_identity = getattr(
            receipt_report, "publication_identity", None
        )
        xcode27_compat_receipt_contract(source, developer_dir, required=True)
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            _remove_failed_execution_receipt(
                receipt_path,
                receipt_publication_identity,
                lambda: xcode27_compat_receipt_contract(
                    source, developer_dir, required=True
                ),
                "Xcode 27 compatibility receipt",
            )
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
    source, developer_dir=None, allow_reclaimed_arm=False, alias_context=None
):
    """Bind the sandbox fix to the already-validated module compatibility fix."""
    receipt_path, _ = xcode27_compat_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    return {
        "path": str(receipt_path),
        "sha256": sha256_file(receipt_path),
    }


def xcode27_seatbelt_receipt_contract(
    source,
    developer_dir=None,
    required=True,
    allow_reclaimed_arm=False,
    alias_context=None,
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
        source,
        developer_dir,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    if set(receipt) != expected_keys or receipt.get("schema") != 1:
        raise PipelineError("Xcode 27 Seatbelt compatibility receipt schema mismatch")
    if (
        receipt.get("source_root") != str(source)
        or receipt.get("xcode27_module_compatibility_receipt") != module_link
        or receipt.get("toolchain") != XCODE27_COMPAT_TOOLCHAIN
        or receipt.get("upstream") != XCODE27_SEATBELT_UPSTREAM
        or not _path_hash_report_matches(
            receipt.get("patch"),
            XCODE27_SEATBELT_PATCH,
            XCODE27_SEATBELT_PATCH_SHA256,
            alias_context,
        )
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
    receipt_publication_identity = None
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
        receipt_publication_identity = getattr(
            receipt_report, "publication_identity", None
        )
        xcode27_seatbelt_receipt_contract(source, developer_dir, required=True)
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            _remove_failed_execution_receipt(
                receipt_path,
                receipt_publication_identity,
                lambda: xcode27_seatbelt_receipt_contract(
                    source, developer_dir, required=True
                ),
                "Xcode 27 Seatbelt receipt",
            )
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
    source, developer_dir=None, allow_reclaimed_arm=False, alias_context=None
):
    """Bind the disabled-ScreenAI guard to validated toolchain fixes."""
    receipt_path, _ = xcode27_seatbelt_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
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
    source,
    developer_dir=None,
    required=True,
    allow_reclaimed_arm=False,
    alias_context=None,
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
        source,
        developer_dir,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
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
        or not _path_hash_report_matches(
            receipt.get("patch"),
            SCREEN_AI_DISABLED_PATCH,
            SCREEN_AI_DISABLED_PATCH_SHA256,
            alias_context,
        )
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
    receipt_publication_identity = None
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
        receipt_publication_identity = getattr(
            receipt_report, "publication_identity", None
        )
        screen_ai_disabled_receipt_contract(source, developer_dir, required=True)
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            _remove_failed_execution_receipt(
                receipt_path,
                receipt_publication_identity,
                lambda: screen_ai_disabled_receipt_contract(
                    source, developer_dir, required=True
                ),
                "disabled ScreenAI receipt",
            )
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
    logical_developer_dir = Path(developer_dir)
    physical_developer_dir = logical_developer_dir.resolve(strict=True)
    identity = xcode27_toolchain_identity(
        developer_contract(physical_developer_dir)
    )
    if identity != XCODE27_COMPAT_TOOLCHAIN:
        raise PipelineError("Xcode 27 LINKEDIT strip toolchain mismatch")
    selected = logical_developer_dir / XCODE27_LINKEDIT_STRIP_RELATIVE
    physical_selected = physical_developer_dir / XCODE27_LINKEDIT_STRIP_RELATIVE
    try:
        focus_macos.require_executable_file(
            physical_selected, physical_developer_dir, "Xcode 27 strip"
        )
    except focus_macos.ContractError as exc:
        raise PipelineError(str(exc)) from exc
    if sha256_file(physical_selected) != XCODE27_LINKEDIT_STRIP_SHA256:
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
    source, developer_dir=None, allow_reclaimed_arm=False, alias_context=None
):
    """Bind the strip workaround to the last source compatibility receipt."""
    receipt_path, _ = screen_ai_disabled_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    return {"path": str(receipt_path), "sha256": sha256_file(receipt_path)}


def xcode27_linkedit_strip_receipt_contract(
    source,
    developer_dir,
    required=True,
    allow_reclaimed_arm=False,
    alias_context=None,
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
        source,
        developer_dir,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
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
        or not _path_hash_report_matches(
            receipt.get("patch"),
            XCODE27_LINKEDIT_STRIP_PATCH,
            XCODE27_LINKEDIT_STRIP_PATCH_SHA256,
            alias_context,
        )
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
    receipt_publication_identity = None
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
        receipt_publication_identity = getattr(
            receipt_report, "publication_identity", None
        )
        xcode27_linkedit_strip_receipt_contract(
            source, developer_dir, required=True, allow_reclaimed_arm=True
        )
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            _remove_failed_execution_receipt(
                receipt_path,
                receipt_publication_identity,
                lambda: xcode27_linkedit_strip_receipt_contract(
                    source,
                    developer_dir,
                    required=True,
                    allow_reclaimed_arm=True,
                ),
                "Xcode 27 LINKEDIT strip receipt",
            )
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


def swiftshader_disabled_build_contract(
    source,
    allow_resumed_history_growth=False,
    authorized_resumed_history=None,
):
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
    x64_receipt_path, _ = slice_receipt_contract(
        source,
        x64_out,
        "x64",
        allow_resumed_history_growth=allow_resumed_history_growth,
        authorized_resumed_history=authorized_resumed_history,
    )
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
    crash_recovery = _load_durable_signing_transaction(
        source, "swiftshader", receipt_path
    )
    if crash_recovery is not None:
        return {
            "stage": "recover-swiftshader-signing-transaction",
            "source_root": str(source),
            "developer_dir": str(developer_dir),
            "receipt": str(receipt_path),
            "crash_recovery": crash_recovery,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
        }
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
    alias_active = _home_alias_is_active(source)
    ninja_history_before = None
    if alias_active:
        ninja_history_before = _ninja_history_snapshot(
            in_source(
                source,
                X64_OUT,
                "x86_64 output",
                must_exist=True,
                directory=True,
            )
        )
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
        **(
            {"ninja_history_before": ninja_history_before}
            if alias_active
            else {}
        ),
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
    alias_active = _home_alias_is_active(source)
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
    if alias_active:
        expected_keys.update(("ninja_history_before", "ninja_history_after"))
    preparation_path, _ = preparation_contract(source, allow_reclaimed_arm=True)
    x64_out = in_source(
        source, X64_OUT, "x86_64 output", must_exist=True, directory=True
    )
    adhoc_receipt_path = in_source(
        source, ADHOC_RUNTIME_SIGNING_RECEIPT, "ad-hoc runtime signing receipt"
    )
    adhoc_chain = None
    if adhoc_receipt_path.exists() and not adhoc_receipt_path.is_symlink():
        adhoc_chain = load_json(
            adhoc_receipt_path, "ad-hoc runtime signing history chain"
        )
    authorized_history = (
        adhoc_chain.get("ninja_history_after")
        if isinstance(adhoc_chain, dict)
        else receipt.get("ninja_history_after")
    )
    build = swiftshader_disabled_build_contract(
        source,
        allow_resumed_history_growth=alias_active,
        authorized_resumed_history=authorized_history,
    )
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
    x64_receipt = load_json(
        build["x64_build_receipt"]["path"], "x86_64 build receipt"
    )
    if alias_active and x64_receipt.get("schema") == RESUMED_SLICE_RECEIPT_SCHEMA:
        base_post = x64_receipt.get("resume_execution", {}).get("post_run", {})
        base_history = {
            name: base_post.get(name) for name in ("ninja_log", "ninja_deps")
        }
        if receipt.get("ninja_history_before") != base_history:
            raise PipelineError(
                "disabled SwiftShader history does not start at raw-Ninja completion"
            )
    before_history = receipt.get("ninja_history_before")
    after_history = receipt.get("ninja_history_after")
    if alias_active:
        for name in ("ninja_log", "ninja_deps"):
            expected_path = x64_out / (
                ".ninja_log" if name == "ninja_log" else ".ninja_deps"
            )
            _validate_recorded_file_snapshot(
                before_history.get(name)
                if isinstance(before_history, dict)
                else None,
                expected_path,
                "SwiftShader pre-refresh {}".format(name),
            )
            _validate_recorded_file_snapshot(
                after_history.get(name)
                if isinstance(after_history, dict)
                else None,
                expected_path,
                "SwiftShader post-refresh {}".format(name),
            )
        if isinstance(adhoc_chain, dict) and adhoc_chain.get(
            "ninja_history_before"
        ) != after_history:
            raise PipelineError(
                "ad-hoc history does not continue SwiftShader history"
            )
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
        or (alias_active and receipt.get("ninja_history_before") != before_history)
        or (alias_active and receipt.get("ninja_history_after") != after_history)
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
    if expected.get("crash_recovery") is not None:
        if expected["crash_recovery"]["receipt_published"]:
            try:
                swiftshader_disabled_signing_receipt_contract(
                    source, developer_dir
                )
            except PipelineError:
                if expected["crash_recovery"].get("cleanup_only"):
                    raise PipelineError(
                        "published SwiftShader receipt is invalid and its "
                        "rollback journal is incomplete"
                    )
                _remove_invalid_transaction_receipt(
                    Path(expected["receipt"]),
                    expected["crash_recovery"]["receipt_identity"],
                )
                _restore_durable_signing_transaction(
                    source, "swiftshader", Path(expected["receipt"])
                )
                fresh = swiftshader_disabled_signing_plan(source, developer_dir)
                result = execute_swiftshader_disabled_signing(
                    source, developer_dir, fresh
                )
                result["invalid_receipt_rolled_back"] = True
                return result
            _discard_durable_signing_transaction(source, "swiftshader")
            return {
                "stage": "recover-swiftshader-signing-transaction",
                "recovered": True,
                "receipt": {
                    "path": expected["receipt"],
                    "sha256": sha256_file(expected["receipt"]),
                },
            }
        _restore_durable_signing_transaction(
            source, "swiftshader", Path(expected["receipt"])
        )
        fresh = swiftshader_disabled_signing_plan(source, developer_dir)
        result = execute_swiftshader_disabled_signing(
            source, developer_dir, fresh
        )
        result["crash_recovered"] = True
        return result
    _live_alias_slice_no_work(source, developer_dir, "x64")
    require_free(source, SOFT_FLOOR_GIB, "disabled SwiftShader signing fix")
    source_parts = Path(expected["source_parts"])
    packaging_parts = Path(expected["packaging_parts"])
    initial_app_trees = expected["app_tree_sha256"]
    snapshot_root = Path(
        tempfile.mkdtemp(prefix="focus-swiftshader-signing-rollback-")
    ).resolve()
    source_backup = snapshot_root / "source-parts.py"
    packaging_backup = snapshot_root / "packaging-parts.py"
    history_rollback = _snapshot_alias_ninja_history(source, snapshot_root)
    prepare_source.atomic_copy(source_parts, source_backup)
    prepare_source.atomic_copy(packaging_parts, packaging_backup)
    durable_transaction = _begin_durable_signing_transaction(
        source, "swiftshader", Path(expected["receipt"])
    )
    receipt_report = None
    receipt_publication_identity = None
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
            environment = _build_child_environment(
                source,
                developer_dir,
                build_ninja=Path(expected["refresh"]["ninja"]["path"]),
            )
            run_monitored(expected["refresh"]["command"], source, environment)
        if _swiftshader_signing_file_state(
            packaging_parts, "generated signing parts"
        ) != expected_final_state:
            raise PipelineError("generated signing parts post-fix hash mismatch")
        current_history = None
        if "ninja_history_before" in expected:
            current_history = _ninja_history_snapshot(
                in_source(
                    source,
                    X64_OUT,
                    "x86_64 output",
                    must_exist=True,
                    directory=True,
                )
            )
        current_build = swiftshader_disabled_build_contract(
            source,
            allow_resumed_history_growth=(current_history is not None),
            authorized_resumed_history=current_history,
        )
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
            **(
                {
                    "ninja_history_before": expected["ninja_history_before"],
                    "ninja_history_after": current_history,
                }
                if current_history is not None
                else {}
            ),
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
        if durable_transaction is not None:
            _fsync_durable_signing_targets(source, "swiftshader")
        receipt_report = atomic_json(Path(expected["receipt"]), receipt_value)
        receipt_publication_identity = getattr(
            receipt_report, "publication_identity", None
        )
        swiftshader_disabled_signing_receipt_contract(source, developer_dir)
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            _remove_failed_execution_receipt(
                receipt_path,
                receipt_publication_identity,
                lambda: swiftshader_disabled_signing_receipt_contract(
                    source, developer_dir
                ),
                "SwiftShader receipt",
            )
            prepare_source.atomic_copy(source_backup, source_parts)
            prepare_source.atomic_copy(packaging_backup, packaging_parts)
            _restore_alias_ninja_history(history_rollback)
            if durable_transaction is not None:
                _restore_durable_signing_transaction(
                    source, "swiftshader", Path(expected["receipt"])
                )
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
        if durable_transaction is not None:
            _discard_durable_signing_transaction(source, "swiftshader")
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
    receipt_path = in_source(
        source,
        ADHOC_RUNTIME_SIGNING_RECEIPT,
        "ad-hoc runtime signing receipt",
    )
    crash_recovery = _load_durable_signing_transaction(
        source, "adhoc", receipt_path
    )
    if crash_recovery is not None:
        return {
            "stage": "recover-adhoc-signing-transaction",
            "source_root": str(source),
            "developer_dir": str(developer_dir),
            "receipt": str(receipt_path),
            "crash_recovery": crash_recovery,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
        }
    swiftshader_path, swiftshader_receipt = swiftshader_disabled_signing_receipt_contract(
        source, developer_dir, allow_adhoc_runtime_signing=True
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
    x64_out = in_source(
        source, X64_OUT, "x86_64 output", must_exist=True, directory=True
    )
    alias_active = _home_alias_is_active(source)
    ninja_history_before = None
    if alias_active:
        ninja_history_before = _ninja_history_snapshot(x64_out)
        if ninja_history_before != swiftshader_receipt.get("ninja_history_after"):
            raise PipelineError(
                "Ninja history changed between SwiftShader and ad-hoc refreshes"
            )
    build = swiftshader_disabled_build_contract(
        source,
        allow_resumed_history_growth=alias_active,
        authorized_resumed_history=ninja_history_before,
    )
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
        **(
            {"ninja_history_before": ninja_history_before}
            if alias_active
            else {}
        ),
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
    alias_active = _home_alias_is_active(source)
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
    if alias_active:
        expected_keys.update(("ninja_history_before", "ninja_history_after"))
    preparation_path, _ = preparation_contract(source, allow_reclaimed_arm=True)
    swiftshader_path, swiftshader_receipt = swiftshader_disabled_signing_receipt_contract(
        source, developer_dir, allow_adhoc_runtime_signing=True
    )
    build = swiftshader_disabled_build_contract(
        source,
        allow_resumed_history_growth=alias_active,
        authorized_resumed_history=receipt.get("ninja_history_after"),
    )
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
    x64_out = in_source(
        source, X64_OUT, "x86_64 output", must_exist=True, directory=True
    )
    if alias_active and receipt.get("ninja_history_before") != swiftshader_receipt.get(
        "ninja_history_after"
    ):
        raise PipelineError("ad-hoc Ninja history does not follow SwiftShader history")
    before_history = receipt.get("ninja_history_before")
    after_history = receipt.get("ninja_history_after")
    if alias_active:
        for name in ("ninja_log", "ninja_deps"):
            expected_path = x64_out / (
                ".ninja_log" if name == "ninja_log" else ".ninja_deps"
            )
            _validate_recorded_file_snapshot(
                before_history.get(name)
                if isinstance(before_history, dict)
                else None,
                expected_path,
                "ad-hoc pre-refresh {}".format(name),
            )
            _validate_recorded_file_snapshot(
                after_history.get(name)
                if isinstance(after_history, dict)
                else None,
                expected_path,
                "ad-hoc post-refresh {}".format(name),
            )
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
        or (alias_active and receipt.get("ninja_history_before") != before_history)
        or (alias_active and receipt.get("ninja_history_after") != after_history)
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
    if expected.get("crash_recovery") is not None:
        if expected["crash_recovery"]["receipt_published"]:
            try:
                adhoc_runtime_signing_receipt_contract(source, developer_dir)
            except PipelineError:
                if expected["crash_recovery"].get("cleanup_only"):
                    raise PipelineError(
                        "published ad-hoc receipt is invalid and its rollback "
                        "journal is incomplete"
                    )
                _remove_invalid_transaction_receipt(
                    Path(expected["receipt"]),
                    expected["crash_recovery"]["receipt_identity"],
                )
                _restore_durable_signing_transaction(
                    source, "adhoc", Path(expected["receipt"])
                )
                fresh = adhoc_runtime_signing_plan(source, developer_dir)
                result = execute_adhoc_runtime_signing(
                    source, developer_dir, fresh
                )
                result["invalid_receipt_rolled_back"] = True
                return result
            _discard_durable_signing_transaction(source, "adhoc")
            return {
                "stage": "recover-adhoc-signing-transaction",
                "recovered": True,
                "receipt": {
                    "path": expected["receipt"],
                    "sha256": sha256_file(expected["receipt"]),
                },
            }
        _restore_durable_signing_transaction(
            source, "adhoc", Path(expected["receipt"])
        )
        fresh = adhoc_runtime_signing_plan(source, developer_dir)
        result = execute_adhoc_runtime_signing(source, developer_dir, fresh)
        result["crash_recovered"] = True
        return result
    _live_alias_slice_no_work(
        source,
        developer_dir,
        "x64",
        authorized_history=plan.get("ninja_history_before"),
    )
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
    history_rollback = _snapshot_alias_ninja_history(source, snapshot_root)
    durable_transaction = _begin_durable_signing_transaction(
        source, "adhoc", Path(expected["receipt"])
    )
    receipt_report = None
    receipt_publication_identity = None
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
        environment = _build_child_environment(source, developer_dir)
        run_monitored(
            expected["tests"]["command"],
            Path(expected["tests"]["working_directory"]),
            environment,
            watched_paths=(source,),
        )
        refresh_environment = _build_child_environment(
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
        current_history = None
        if "ninja_history_before" in expected:
            current_history = _ninja_history_snapshot(
                in_source(
                    source,
                    X64_OUT,
                    "x86_64 output",
                    must_exist=True,
                    directory=True,
                )
            )
        current_build = swiftshader_disabled_build_contract(
            source,
            allow_resumed_history_growth=(current_history is not None),
            authorized_resumed_history=current_history,
        )
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
            **(
                {
                    "ninja_history_before": expected["ninja_history_before"],
                    "ninja_history_after": current_history,
                }
                if current_history is not None
                else {}
            ),
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
        if durable_transaction is not None:
            _fsync_durable_signing_targets(source, "adhoc")
        receipt_report = atomic_json(Path(expected["receipt"]), receipt_value)
        receipt_publication_identity = getattr(
            receipt_report, "publication_identity", None
        )
        adhoc_runtime_signing_receipt_contract(source, developer_dir)
    except BaseException as original_error:
        try:
            receipt_path = Path(expected["receipt"])
            _remove_failed_execution_receipt(
                receipt_path,
                receipt_publication_identity,
                lambda: adhoc_runtime_signing_receipt_contract(
                    source, developer_dir
                ),
                "ad-hoc receipt",
            )
            for relative, backup in packaging_backups.items():
                prepare_source.atomic_copy(backup, packaging_paths[relative])
            for relative, backup in source_backups.items():
                prepare_source.atomic_copy(backup, source_paths[relative])
            _restore_alias_ninja_history(history_rollback)
            if durable_transaction is not None:
                _restore_durable_signing_transaction(
                    source, "adhoc", Path(expected["receipt"])
                )
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
        if durable_transaction is not None:
            _discard_durable_signing_transaction(source, "adhoc")
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


def _reclaimed_arm_onboarding_evidence(source):
    """Construct the exported canonical bridge after the ARM graph was reclaimed."""
    source = Path(source)
    onboarding_path = in_source(
        source,
        onboarding_alias_compat.RECEIPT_RELATIVE,
        "onboarding alias-root receipt",
        must_exist=True,
    )
    onboarding = load_json(onboarding_path, "onboarding alias-root receipt")
    graph = onboarding.get("graph_inventory")
    home_alias = onboarding.get("home_alias_compatibility")
    if not isinstance(graph, dict) or not isinstance(home_alias, dict):
        raise PipelineError("onboarding receipt lacks reclaimed graph evidence")
    stage_path = in_source(
        source, STAGE_RECEIPT, "arm64 stage receipt", must_exist=True
    )
    reclaim_path, reclaim = reclaim_contract(source)
    evidence = {
        "schema": 1,
        "kind": onboarding_alias_compat.RECLAIMED_ARM_EVIDENCE_KIND,
        "home_alias_compatibility": home_alias,
        "graph_inventory_sha256": graph.get("aggregate_sha256"),
        "stage_receipt": {
            "path": STAGE_RECEIPT,
            "bytes": stage_path.stat().st_size,
            "sha256": sha256_file(stage_path),
        },
        "reclaim_receipt": {
            "path": RECLAIM_RECEIPT,
            "bytes": Path(reclaim_path).stat().st_size,
            "sha256": sha256_file(reclaim_path),
        },
        "staged_app": {
            "path": STAGED_ARM_APP,
            "tree_sha256": reclaim["tree_sha256"],
        },
        "reclaimed_out": ARM_OUT,
    }
    try:
        validated_graph = onboarding_alias_compat.validate_graph_inventory(
            source, graph, reclaimed_arm=evidence
        )
    except (KeyError, TypeError, onboarding_alias_compat.AliasCompatError) as exc:
        raise PipelineError(str(exc)) from exc
    if not _strict_json_identity(validated_graph, graph):
        raise PipelineError("reclaimed ARM graph validation changed its evidence")
    return evidence


def _reclaimed_arm_onboarding_contract(source, reclaimed_arm):
    """Revalidate receipt, transition, and preparation projection as one value."""
    source = Path(source)
    receipt_path = in_source(
        source,
        onboarding_alias_compat.RECEIPT_RELATIVE,
        "onboarding alias-root receipt",
        must_exist=True,
    )
    receipt = load_json(receipt_path, "onboarding alias-root receipt")
    trial = receipt.get("trial_evidence")
    trial_report = trial.get("trial_report") if isinstance(trial, dict) else None
    failure_report = trial.get("failure_report") if isinstance(trial, dict) else None
    home = reclaimed_arm.get("home_alias_compatibility")
    mappings = home.get("mappings") if isinstance(home, dict) else None
    workspace = (
        Path(mappings["workspace"]["physical"])
        if isinstance(mappings, dict) and "workspace" in mappings
        else None
    )
    if (
        workspace is None
        or not isinstance(trial_report, dict)
        or not isinstance(failure_report, dict)
    ):
        raise PipelineError("reclaimed onboarding evidence links are missing")
    trial_path = workspace / "work/logs" / onboarding_alias_compat.TRIAL_REPORT_BASENAME
    failure_path = (
        workspace / "work/logs" / onboarding_alias_compat.FAILURE_REPORT_BASENAME
    )
    try:
        contract = onboarding_alias_compat.receipt_contract(
            source,
            trial_path=trial_path,
            failure_path=failure_path,
            reclaimed_arm=reclaimed_arm,
        )
        projection = (
            onboarding_alias_compat.preparation_dependency_tree_projection_contract(
                source, workspace, reclaimed_arm=reclaimed_arm
            )
        )
    except (KeyError, TypeError, onboarding_alias_compat.AliasCompatError) as exc:
        raise PipelineError(str(exc)) from exc
    if (
        Path(contract.get("path", "")).resolve(strict=True)
        != receipt_path.resolve(strict=True)
        or contract.get("value") != receipt
        or contract.get("bytes") != receipt_path.stat().st_size
        or contract.get("sha256") != sha256_file(receipt_path)
        or not isinstance(projection, dict)
        or projection.get("kind")
        != onboarding_alias_compat.PREPARATION_PROJECTION_KIND
    ):
        raise PipelineError("reclaimed onboarding canonical contract mismatch")
    return {
        "evidence": reclaimed_arm,
        "receipt": {
            "path": str(receipt_path),
            "bytes": contract["bytes"],
            "sha256": contract["sha256"],
        },
        "projection_sha256": hashlib.sha256(
            json.dumps(
                projection,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
        ).hexdigest(),
    }


def _onboarding_preparation_projection(
    source, alias_context, reclaimed_arm=None
):
    """Return the sole cycle-free POST projection allowed by preparation."""
    if alias_context is None:
        return None
    if not isinstance(alias_context, AliasContext):
        raise PipelineError("preparation alias context type mismatch")
    if Path(source).resolve(strict=True) != alias_context.physical_source:
        raise PipelineError("preparation alias source binding changed")
    try:
        projection = (
            onboarding_alias_compat.preparation_dependency_tree_projection_contract(
                source,
                alias_context.physical_workspace,
                reclaimed_arm=reclaimed_arm,
            )
        )
    except onboarding_alias_compat.AliasCompatError as exc:
        raise PipelineError(str(exc)) from exc
    if projection is None:
        return None
    expected_tree_projection = {
        "relative_path": onboarding_alias_compat.SOURCE_RELATIVE,
        "observed": {
            "mode": 0o644,
            "bytes": onboarding_alias_compat.POST_BYTES,
            "sha256": onboarding_alias_compat.POST_SHA256,
        },
        "projected": {
            "mode": 0o644,
            "bytes": onboarding_alias_compat.PRE_BYTES,
            "sha256": onboarding_alias_compat.PRE_SHA256,
        },
    }
    if (
        not isinstance(projection, dict)
        or set(projection)
        != {"schema", "kind", "workspace", "tree_projection", "transition", "safety"}
        or type(projection.get("schema")) is not int
        or projection["schema"] != 1
        or projection.get("kind")
        != onboarding_alias_compat.PREPARATION_PROJECTION_KIND
        or projection.get("workspace") != str(alias_context.physical_workspace)
        or projection.get("tree_projection") != expected_tree_projection
    ):
        raise PipelineError("onboarding preparation projection contract mismatch")
    return projection


def _strict_json_identity(left, right):
    try:
        return json.dumps(
            left,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) == json.dumps(
            right,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return False


def preparation_contract(
    source,
    allow_reclaimed_arm=False,
    allow_missing_gn_compat=False,
    alias_context=None,
    reclaimed_arm=None,
):
    if alias_context is None and Path(source).resolve(strict=True) != Path(source):
        alias_context = _recorded_alias_context(source)
    if reclaimed_arm is None and allow_reclaimed_arm and alias_context is not None:
        arm_out = in_source(source, ARM_OUT, "arm64 output")
        if not os.path.lexists(str(arm_out)):
            reclaimed_arm = _reclaimed_arm_onboarding_evidence(source)
    path_projector = alias_context.project if alias_context is not None else None
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
            path_projector=path_projector,
        )
        prepare_source.validate_recovery_checkpoint_report(
            receipt.get("recovery_checkpoint"),
            source_root=source,
            path_projector=path_projector,
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
            marker_path.parent,
            prepare_source.DEPENDENCY_CONTRACTS,
            path_projector=path_projector,
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
    projection_before = _onboarding_preparation_projection(
        source, alias_context, reclaimed_arm=reclaimed_arm
    )
    try:
        try:
            installed = prepare_source.installed_dependency_tree(
                source,
                prepare_source.DEPENDENCY_CONTRACTS,
                exact_file_projection=(
                    projection_before["tree_projection"]
                    if projection_before is not None
                    else None
                ),
            )
        finally:
            projection_after = _onboarding_preparation_projection(
                source, alias_context, reclaimed_arm=reclaimed_arm
            )
            if not _strict_json_identity(projection_after, projection_before):
                raise PipelineError(
                    "onboarding preparation projection changed during dependency scan"
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
        current_node = prepare_source.onboarding_node_contract(
            source, path_projector=path_projector
        )
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


def slice_receipt_contract(
    source,
    out,
    architecture,
    allow_resumed_history_growth=False,
    authorized_resumed_history=None,
):
    """Bind staging/merging to a completed slice build from this checkout."""
    receipt_path = Path(out) / SLICE_RECEIPT_NAME
    receipt = load_json(receipt_path, "{} build receipt".format(architecture))
    expected_arch = "arm64" if architecture == "arm64" else "x86_64"
    schema = receipt.get("schema")
    alias_receipt = in_source(source, HOME_ALIAS_RECEIPT, "home-alias receipt")
    if (alias_receipt.exists() or alias_receipt.is_symlink()) and (
        schema != RESUMED_SLICE_RECEIPT_SCHEMA
    ):
        raise PipelineError(
            "home-alias builds require a resumed schema-two slice receipt"
        )
    if (
        schema not in (1, RESUMED_SLICE_RECEIPT_SCHEMA)
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
    current_ninja = ninja_contract(source)
    if receipt.get("ninja") != current_ninja:
        raise PipelineError("{} Ninja provenance mismatch".format(architecture))
    if schema == RESUMED_SLICE_RECEIPT_SCHEMA:
        if receipt.get("app_tree_sha256") != tree_digest(
            Path(out) / APP_NAME
        ):
            raise PipelineError("resumed slice app tree changed")
        developer_dir = Path(tool_receipt_contract(source)[1]["developer_dir"])
        alias_path, alias = home_alias_receipt_contract(source, developer_dir)
        if receipt.get("home_alias_compatibility") != {
            "path": str(alias_path),
            "sha256": sha256_file(alias_path),
        }:
            raise PipelineError("resumed slice home-alias provenance mismatch")
        _, _, onboarding_alias_root = onboarding_alias_root_receipt_contract(
            source
        )
        if (
            receipt.get("onboarding_alias_root_compatibility")
            != onboarding_alias_root
        ):
            raise PipelineError(
                "resumed slice onboarding alias-root provenance mismatch"
            )
        execution_path = receipt.get("resume_execution", {}).get("path", "")
        execution = resume_execution_record_contract(
            execution_path,
            alias,
            source,
            developer_dir,
            architecture,
            Path(out),
            current_ninja,
            allow_history_growth=allow_resumed_history_growth,
            authorized_history=authorized_resumed_history,
        )
        if receipt.get("resume_execution") != execution:
            raise PipelineError("resumed slice execution record changed")
        mixed = changed_path_scan(
            Path(out),
            execution["started_at_ns"],
            Path(alias["logical_home"]),
            Path(alias["physical_home"]),
        )
        if (
            not allow_resumed_history_growth
            and receipt.get("mixed_path_scan") != mixed
        ):
            raise PipelineError("resumed slice mixed-path inventory changed")
        if (
            receipt.get("raw_ninja_completed") is not True
            or receipt.get("gn_gen_executed_by_finalizer") is not False
            or receipt.get("build_command_executed_by_finalizer") is not False
            or receipt.get("no_work_probe", {}).get("no_work") is not True
        ):
            raise PipelineError("resumed slice execution provenance mismatch")
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


def _fresh_x64_directory_identity(path, label):
    """Return the rename-stable identity of one real owned directory."""
    path = Path(path)
    observed = os.stat(str(path), follow_symlinks=False)
    if (
        not stat.S_ISDIR(observed.st_mode)
        or path.is_symlink()
        or observed.st_uid != os.getuid()
        or observed.st_nlink < 2
    ):
        raise PipelineError("{} is not a safe owned directory".format(label))
    return {
        "device": observed.st_dev,
        "inode": observed.st_ino,
        "uid": observed.st_uid,
        "gid": observed.st_gid,
        "mode": stat.S_IMODE(observed.st_mode),
    }


def _legacy_x64_invalid_strip_contract(out):
    """Prove that an old x64 graph is the llvm-strip graph being retired."""
    out = Path(out)
    identity = _fresh_x64_directory_identity(out, "legacy x86_64 output")
    args = out / "args.gn"
    if sha256_file(args) != SWIFTSHADER_DISABLED_ARGS_SHA256["x64"]:
        raise PipelineError("legacy x86_64 args.gn is not the pinned profile")
    build_receipt = out / SLICE_RECEIPT_NAME
    if os.path.lexists(str(build_receipt)):
        raise PipelineError(
            "refusing to retire x86_64 output carrying a build receipt"
        )
    manifests = []
    pattern = re.compile(r"-Wcrl,strippath,([^\s\"']+)")
    toolchains = sorted(
        out.rglob("toolchain.ninja"),
        key=lambda item: item.relative_to(out).as_posix(),
    )
    if len(toolchains) != LEGACY_X64_TOOLCHAIN_FILE_COUNT:
        raise PipelineError("legacy x86_64 toolchain count changed")
    for path in toolchains:
        if path.is_symlink() or not path.is_file():
            raise PipelineError("legacy x86_64 toolchain is unsafe")
        text = path.read_text(encoding="utf-8")
        tokens = pattern.findall(text)
        if not tokens or set(tokens) != {LEGACY_LLVM_STRIP_TOKEN}:
            raise PipelineError(
                "legacy x86_64 graph is not the exact llvm-strip graph"
            )
        manifests.append(
            {
                "path": path.relative_to(out).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "strip_token_count": len(tokens),
                "strip_tokens": sorted(set(tokens)),
            }
        )
    return {
        "root": str(out),
        "identity": identity,
        "tree_sha256": tree_digest(out),
        "allocated_bytes": physical_size(out),
        "args_gn_sha256": sha256_file(args),
        "toolchain_files": manifests,
        "toolchain_file_count": len(manifests),
        "llvm_strip_token_count": sum(
            item["strip_token_count"] for item in manifests
        ),
        "xcode_strip_token_count": 0,
        "confirmed_invalid": True,
    }


def _verify_legacy_x64_inventory(path, expected):
    """Re-prove the exact invalid tree after each directory-only rename."""
    current = _legacy_x64_invalid_strip_contract(path)
    comparable = dict(current)
    comparable["root"] = expected.get("root")
    if comparable != expected:
        raise PipelineError("legacy x86_64 inventory changed during transaction")
    return current


def _fresh_x64_generated_graph_contract(out, linkedit_tools):
    """Bind the fresh GN graph and reject every residual llvm-strip spelling."""
    out = Path(out)
    generated = generated_linkedit_strip_contract(out, linkedit_tools)
    toolchains = []
    llvm_occurrences = 0
    for path in sorted(
        out.rglob("toolchain.ninja"),
        key=lambda item: item.relative_to(out).as_posix(),
    ):
        if path.is_symlink() or not path.is_file():
            raise PipelineError("fresh x86_64 toolchain is unsafe")
        text = path.read_text(encoding="utf-8")
        llvm_occurrences += text.count("llvm-strip")
        toolchains.append(
            {
                "path": path.relative_to(out).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    if (
        not toolchains
        or len(generated["toolchain_files"]) != len(toolchains)
        or llvm_occurrences != 0
    ):
        raise PipelineError(
            "fresh x86_64 graph contains an unverified or llvm-strip toolchain"
        )
    args = out / "args.gn"
    build_ninja = out / "build.ninja"
    build_ninja_d = out / "build.ninja.d"
    for path, label in (
        (args, "args.gn"),
        (build_ninja, "build.ninja"),
        (build_ninja_d, "build.ninja.d"),
    ):
        if path.is_symlink() or not path.is_file():
            raise PipelineError("fresh x86_64 {} is missing".format(label))
    return {
        "root": str(out),
        "args_gn": {
            "bytes": args.stat().st_size,
            "sha256": sha256_file(args),
        },
        "build_ninja": {
            "bytes": build_ninja.stat().st_size,
            "sha256": sha256_file(build_ninja),
        },
        "build_ninja_d": {
            "bytes": build_ninja_d.stat().st_size,
            "sha256": sha256_file(build_ninja_d),
        },
        "toolchains": toolchains,
        "toolchain_file_count": len(toolchains),
        "llvm_strip_occurrences": 0,
        "linkedit_strip": generated,
    }


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


def _rename_exclusive(source_path, destination_path, label):
    """Use Darwin's atomic RENAME_EXCL primitive; never replace a rival."""
    if sys.platform != "darwin":
        raise PipelineError("{} exclusive rename requires macOS".format(label))
    libc = ctypes.CDLL(None, use_errno=True)
    renamex = libc.renamex_np
    renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex.restype = ctypes.c_int
    result = renamex(
        os.fsencode(source_path), os.fsencode(destination_path), 0x00000004
    )
    if result != 0:
        error = ctypes.get_errno()
        detail = "destination exists" if error == errno.EEXIST else os.strerror(error)
        raise PipelineError("{} exclusive rename failed: {}".format(label, detail))


def _rename_owned_directory(source_path, destination_path, expected_identity, label):
    """Exclusively rename one exact directory and prove its identity afterward."""
    source_path = Path(source_path)
    destination_path = Path(destination_path)
    if _fresh_x64_directory_identity(source_path, label) != expected_identity:
        raise PipelineError("{} identity changed before rename".format(label))
    _rename_exclusive(source_path, destination_path, label)
    _fsync_directory(source_path.parent)
    if destination_path.parent != source_path.parent:
        _fsync_directory(destination_path.parent)
    if (
        os.path.lexists(str(source_path))
        or _fresh_x64_directory_identity(destination_path, label)
        != expected_identity
    ):
        raise PipelineError("{} rename identity mismatch".format(label))


def _locate_fresh_x64_directory(expected_identity, candidates, label):
    """Locate one inode across known transaction states without changing it."""
    matches = []
    for candidate in map(Path, candidates):
        if not os.path.lexists(str(candidate)):
            continue
        try:
            current = _fresh_x64_directory_identity(candidate, label)
        except (OSError, PipelineError):
            continue
        if current == expected_identity:
            matches.append(candidate)
    if len(matches) != 1:
        raise PipelineError(
            "{} identity has {} known locations".format(label, len(matches))
        )
    return matches[0]


def _quarantine_owned_directory(path, destination, expected_identity, label):
    """Move rollback material aside atomically; never recursively delete it."""
    path = Path(path)
    destination = Path(destination)
    _rename_owned_directory(path, destination, expected_identity, label)
    return destination


def _fresh_x64_fixed_paths(source):
    return {
        "out": in_source(source, X64_OUT, "x86_64 output"),
        "receipt": in_source(
            source,
            FRESH_X64_PREPARATION_RECEIPT,
            "fresh x86_64 preparation receipt",
        ),
        "legacy_root": in_source(
            source, FRESH_X64_LEGACY_ROOT, "legacy x86_64 root"
        ),
        "transaction_root": in_source(
            source, FRESH_X64_TRANSACTION_ROOT, "fresh x86_64 transaction"
        ),
        "fresh_failed": in_source(
            source, FRESH_X64_FAILED_ROOT, "failed fresh x86_64 output"
        ),
        "transaction_failed": in_source(
            source,
            FRESH_X64_TRANSACTION_FAILED_ROOT,
            "failed fresh x86_64 transaction",
        ),
        "receipt_failed": in_source(
            source, FRESH_X64_RECEIPT_FAILED, "failed fresh x86_64 receipt"
        ),
    }


def fresh_x64_preparation_plan(source, developer_dir):
    """Plan one alias-safe fresh GN graph while preserving the invalid graph."""
    source = Path(source)
    alias_context = _recorded_alias_context(source, developer_dir)
    acquisition_path, _ = acquisition_contract(source)
    tool_path, _ = tool_receipt_contract(source, developer_dir)
    reclaimed_arm = _reclaimed_arm_onboarding_evidence(source)
    preparation_path, _ = preparation_contract(
        source,
        allow_reclaimed_arm=True,
        alias_context=alias_context,
        reclaimed_arm=reclaimed_arm,
    )
    onboarding = _reclaimed_arm_onboarding_contract(source, reclaimed_arm)
    xcode_path, _ = xcode27_compat_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=True,
        alias_context=alias_context,
    )
    seatbelt_path, _ = xcode27_seatbelt_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=True,
        alias_context=alias_context,
    )
    screen_ai_path, _ = screen_ai_disabled_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=True,
        alias_context=alias_context,
    )
    linkedit_path, linkedit = xcode27_linkedit_strip_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=True,
        alias_context=alias_context,
    )
    paths = _fresh_x64_fixed_paths(source)
    for key in (
        "receipt",
        "legacy_root",
        "transaction_root",
        "fresh_failed",
        "transaction_failed",
        "receipt_failed",
    ):
        if os.path.lexists(str(paths[key])):
            raise PipelineError(
                "fresh x86_64 {} already exists".format(key.replace("_", " "))
            )
    for relative in (
        SWIFTSHADER_DISABLED_SIGNING_RECEIPT,
        ADHOC_RUNTIME_SIGNING_RECEIPT,
        UNSIGNED_ROOT,
        SIGNED_ROOT,
    ):
        if os.path.lexists(str(in_source(source, relative, "fresh x86_64 guard"))):
            raise PipelineError("fresh x86_64 preparation is after a downstream stage")
    legacy = _legacy_x64_invalid_strip_contract(paths["out"])
    profiles = focus_macos.validate_gn_profiles()["profiles"]
    profile = profiles["x64"]
    args_data = profile["args_gn"].encode("utf-8")
    if hashlib.sha256(args_data).hexdigest() != SWIFTSHADER_DISABLED_ARGS_SHA256["x64"]:
        raise PipelineError("fresh x86_64 profile hash changed")
    tools = tool_paths(source)
    command = [str(tools["gn"]), "gen", X64_OUT, "--fail-on-unused-args"]
    return {
        "stage": "prepare-fresh-x64",
        "source_root": str(source),
        "developer_dir": str(developer_dir),
        "out": str(paths["out"]),
        "receipt": str(paths["receipt"]),
        "legacy_root": str(paths["legacy_root"]),
        "legacy_out": str(paths["legacy_root"] / Path(X64_OUT).name),
        "transaction_root": str(paths["transaction_root"]),
        "transaction_legacy_out": str(
            paths["transaction_root"] / Path(X64_OUT).name
        ),
        "transaction_prepared": str(
            paths["transaction_root"] / FRESH_X64_TRANSACTION_PREPARED
        ),
        "fresh_failed": str(paths["fresh_failed"]),
        "transaction_failed": str(paths["transaction_failed"]),
        "receipt_failed": str(paths["receipt_failed"]),
        "legacy_inventory": legacy,
        "fresh_profile": {
            "flags_file": profile["flags_file"],
            "arg_names": profile["arg_names"],
            "args_gn_bytes": len(args_data),
            "args_gn_sha256": hashlib.sha256(args_data).hexdigest(),
        },
        "gn_command": command,
        "acquisition_receipt": {
            "path": str(acquisition_path),
            "sha256": sha256_file(acquisition_path),
        },
        "tool_receipt": {
            "path": str(tool_path),
            "sha256": sha256_file(tool_path),
        },
        "preparation_receipt": {
            "path": str(preparation_path),
            "sha256": sha256_file(preparation_path),
        },
        "reclaimed_arm_onboarding": onboarding,
        "xcode27_compatibility_receipt_sha256": sha256_file(xcode_path),
        "xcode27_seatbelt_compatibility_receipt_sha256": sha256_file(
            seatbelt_path
        ),
        "screen_ai_disabled_compatibility_receipt_sha256": sha256_file(
            screen_ai_path
        ),
        "xcode27_linkedit_strip_compatibility_receipt_sha256": sha256_file(
            linkedit_path
        ),
        "linkedit_strip_tools": linkedit["tools"],
        "legacy_preserved": True,
        "gn_invocations": 1,
        "ninja_invocations": 0,
        "offline": True,
        "network_operations": 0,
    }


def _quarantine_fresh_x64_receipt(
    receipt_path, failed_path, expected_value, publication
):
    """Move only our exact receipt aside; never unlink a possible replacement."""
    receipt_path = Path(receipt_path)
    failed_path = Path(failed_path)
    if not os.path.lexists(str(receipt_path)):
        return None
    expected_bytes = (
        json.dumps(expected_value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    snapshot = _regular_file_snapshot(receipt_path)
    identity = _lstat_identity(receipt_path)
    if (
        snapshot["bytes"] != len(expected_bytes)
        or snapshot["sha256"] != hashlib.sha256(expected_bytes).hexdigest()
        or (
            publication is not None
            and identity != publication.publication_identity
        )
    ):
        raise PipelineError("fresh x86_64 rollback receipt is not our inode")
    _rename_exclusive(
        receipt_path, failed_path, "failed fresh x86_64 preparation receipt"
    )
    _fsync_directory(receipt_path.parent)
    if (
        os.path.lexists(str(receipt_path))
        or _lstat_identity(failed_path) != identity
    ):
        raise PipelineError("fresh x86_64 receipt quarantine identity mismatch")
    return failed_path


def execute_fresh_x64_preparation(
    source, developer_dir, plan, allow_exact_legacy_move
):
    """Move the invalid tree, run only fresh offline GN, and stop before Ninja."""
    if not allow_exact_legacy_move:
        raise PipelineError(
            "prepare-fresh-x64 requires --confirm-exact-legacy-move"
        )
    expected = fresh_x64_preparation_plan(source, developer_dir)
    if plan != expected:
        raise PipelineError("fresh x86_64 preparation plan changed")
    require_free(source, SOFT_FLOOR_GIB, "fresh x86_64 GN preparation")
    paths = {name: Path(expected[name]) for name in (
        "out", "receipt", "legacy_root", "legacy_out", "transaction_root",
        "transaction_legacy_out", "transaction_prepared", "fresh_failed",
        "transaction_failed", "receipt_failed",
    )}
    transaction_identity = None
    fresh_identity = None
    receipt_publication = None
    receipt_value = None
    try:
        paths["transaction_root"].mkdir(mode=0o700, parents=False, exist_ok=False)
        transaction_identity = _fresh_x64_directory_identity(
            paths["transaction_root"], "fresh x86_64 transaction"
        )
        _fsync_directory(paths["transaction_root"].parent)
        prepared_value = {
            "schema": 1,
            "stage": "prepare-fresh-x64",
            "source_root": str(source),
            "legacy_inventory": expected["legacy_inventory"],
            "fresh_profile": expected["fresh_profile"],
            "gn_command": expected["gn_command"],
            "legacy_destination": expected["legacy_out"],
            "offline": True,
            "network_operations": 0,
            "gn_started": False,
            "ninja_started": False,
        }
        atomic_json(paths["transaction_prepared"], prepared_value)
        _rename_owned_directory(
            paths["out"],
            paths["transaction_legacy_out"],
            expected["legacy_inventory"]["identity"],
            "legacy x86_64 output",
        )
        _verify_legacy_x64_inventory(
            paths["transaction_legacy_out"], expected["legacy_inventory"]
        )
        paths["out"].mkdir(mode=0o755, parents=False, exist_ok=False)
        fresh_identity = _fresh_x64_directory_identity(
            paths["out"], "fresh x86_64 output"
        )
        profile_text = focus_macos.validate_gn_profiles()["profiles"]["x64"][
            "args_gn"
        ]
        prepare_source.atomic_publish_text(paths["out"] / "args.gn", profile_text)
        if sha256_file(paths["out"] / "args.gn") != expected["fresh_profile"][
            "args_gn_sha256"
        ]:
            raise PipelineError("fresh x86_64 args.gn changed before GN")
        alias_context = _recorded_alias_context(source, developer_dir)
        environment = safe_environment(
            source,
            developer_dir,
            inherited={"HOME": str(alias_context.logical_home)},
            alias_context=alias_context,
        )
        run_monitored(
            expected["gn_command"], source, environment, watched_paths=(source,)
        )
        if (
            os.path.lexists(str(paths["out"] / ".ninja_log"))
            or os.path.lexists(str(paths["out"] / ".ninja_deps"))
            or os.path.lexists(str(paths["out"] / APP_NAME))
        ):
            raise PipelineError("fresh x86_64 GN preparation observed Ninja output")
        graph = _fresh_x64_generated_graph_contract(
            paths["out"], expected["linkedit_strip_tools"]
        )
        if graph["args_gn"]["sha256"] != expected["fresh_profile"][
            "args_gn_sha256"
        ]:
            raise PipelineError("fresh x86_64 GN rewrote args.gn")
        if _fresh_x64_directory_identity(
            paths["transaction_root"], "fresh x86_64 transaction"
        ) != transaction_identity:
            raise PipelineError("fresh x86_64 transaction identity changed")
        _rename_owned_directory(
            paths["transaction_root"],
            paths["legacy_root"],
            transaction_identity,
            "fresh x86_64 legacy container",
        )
        _verify_legacy_x64_inventory(
            paths["legacy_out"], expected["legacy_inventory"]
        )
        prepared_final = paths["legacy_root"] / FRESH_X64_TRANSACTION_PREPARED
        receipt_value = {
            "schema": 1,
            "stage": "prepare-fresh-x64",
            "source_root": str(source),
            "developer_dir": str(developer_dir),
            "legacy_root": str(paths["legacy_root"]),
            "legacy_out": str(paths["legacy_out"]),
            "legacy_inventory": expected["legacy_inventory"],
            "prepared_evidence": {
                "path": str(prepared_final),
                "sha256": sha256_file(prepared_final),
            },
            "fresh_out": str(paths["out"]),
            "fresh_out_identity": fresh_identity,
            "fresh_profile": expected["fresh_profile"],
            "generated_graph": graph,
            "gn_command": expected["gn_command"],
            "acquisition_receipt": expected["acquisition_receipt"],
            "tool_receipt": expected["tool_receipt"],
            "preparation_receipt": expected["preparation_receipt"],
            "reclaimed_arm_onboarding": expected["reclaimed_arm_onboarding"],
            "xcode27_compatibility_receipt_sha256": expected[
                "xcode27_compatibility_receipt_sha256"
            ],
            "xcode27_seatbelt_compatibility_receipt_sha256": expected[
                "xcode27_seatbelt_compatibility_receipt_sha256"
            ],
            "screen_ai_disabled_compatibility_receipt_sha256": expected[
                "screen_ai_disabled_compatibility_receipt_sha256"
            ],
            "xcode27_linkedit_strip_compatibility_receipt_sha256": expected[
                "xcode27_linkedit_strip_compatibility_receipt_sha256"
            ],
            "linkedit_strip_tools": expected["linkedit_strip_tools"],
            "legacy_preserved": True,
            "legacy_deleted": False,
            "gn_gen_executed": True,
            "gn_gen_succeeded": True,
            "ninja_executed": False,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
            "offline": True,
            "network_operations": 0,
        }
        receipt_publication = atomic_json(paths["receipt"], receipt_value)
        fresh_x64_preparation_contract(source, developer_dir)
        return receipt_publication
    except BaseException as original_error:
        rollback_errors = []
        try:
            if receipt_value is not None:
                _quarantine_fresh_x64_receipt(
                    paths["receipt"],
                    paths["receipt_failed"],
                    receipt_value,
                    receipt_publication,
                )
        except BaseException as exc:
            rollback_errors.append("receipt={!r}".format(exc))
        try:
            if fresh_identity is not None:
                fresh_location = _locate_fresh_x64_directory(
                    fresh_identity,
                    (paths["out"], paths["fresh_failed"]),
                    "fresh x86_64 rollback output",
                )
                if fresh_location == paths["out"]:
                    _quarantine_owned_directory(
                        fresh_location,
                        paths["fresh_failed"],
                        fresh_identity,
                        "fresh x86_64 rollback output",
                    )
        except BaseException as exc:
            rollback_errors.append("fresh_output={!r}".format(exc))
        try:
            legacy_location = _locate_fresh_x64_directory(
                expected["legacy_inventory"]["identity"],
                (paths["out"], paths["transaction_legacy_out"], paths["legacy_out"]),
                "legacy x86_64 rollback",
            )
            if legacy_location != paths["out"]:
                _verify_legacy_x64_inventory(
                    legacy_location, expected["legacy_inventory"]
                )
                if os.path.lexists(str(paths["out"])):
                    raise PipelineError("original x86_64 path is occupied during rollback")
                _rename_owned_directory(
                    legacy_location,
                    paths["out"],
                    expected["legacy_inventory"]["identity"],
                    "legacy x86_64 rollback",
                )
            _verify_legacy_x64_inventory(paths["out"], expected["legacy_inventory"])
        except BaseException as exc:
            rollback_errors.append("legacy_restore={!r}".format(exc))
        try:
            if transaction_identity is not None:
                container = _locate_fresh_x64_directory(
                    transaction_identity,
                    (
                        paths["transaction_root"],
                        paths["legacy_root"],
                        paths["transaction_failed"],
                    ),
                    "fresh x86_64 rollback transaction",
                )
                if container != paths["transaction_failed"]:
                    _quarantine_owned_directory(
                        container,
                        paths["transaction_failed"],
                        transaction_identity,
                        "fresh x86_64 rollback transaction",
                    )
        except BaseException as exc:
            rollback_errors.append("transaction={!r}".format(exc))
        if rollback_errors:
            raise PipelineError(
                "fresh x86_64 preparation failed and rollback failed closed: "
                "original={!r}; rollback={}".format(
                    original_error, "; ".join(rollback_errors)
                )
            ) from original_error
        raise


def fresh_x64_preparation_contract(source, developer_dir):
    """Revalidate the preserved invalid tree and the fresh no-Ninja GN graph."""
    source = Path(source)
    paths = _fresh_x64_fixed_paths(source)
    receipt = load_json(paths["receipt"], "fresh x86_64 preparation receipt")
    expected_keys = {
        "schema", "stage", "source_root", "developer_dir", "legacy_root",
        "legacy_out", "legacy_inventory", "prepared_evidence", "fresh_out",
        "fresh_out_identity", "fresh_profile", "generated_graph", "gn_command",
        "acquisition_receipt", "tool_receipt", "preparation_receipt",
        "reclaimed_arm_onboarding", "xcode27_compatibility_receipt_sha256",
        "xcode27_seatbelt_compatibility_receipt_sha256",
        "screen_ai_disabled_compatibility_receipt_sha256",
        "xcode27_linkedit_strip_compatibility_receipt_sha256",
        "linkedit_strip_tools", "legacy_preserved", "legacy_deleted",
        "gn_gen_executed", "gn_gen_succeeded", "ninja_executed",
        "build_executed", "signing_executed", "packaging_executed",
        "offline", "network_operations",
    }
    if set(receipt) != expected_keys or (
        receipt.get("schema") != 1
        or receipt.get("stage") != "prepare-fresh-x64"
        or receipt.get("source_root") != str(source)
        or receipt.get("developer_dir") != str(developer_dir)
        or receipt.get("legacy_root") != str(paths["legacy_root"])
        or receipt.get("legacy_out")
        != str(paths["legacy_root"] / Path(X64_OUT).name)
        or receipt.get("fresh_out") != str(paths["out"])
        or receipt.get("legacy_preserved") is not True
        or receipt.get("legacy_deleted") is not False
        or receipt.get("gn_gen_executed") is not True
        or receipt.get("gn_gen_succeeded") is not True
        or receipt.get("ninja_executed") is not False
        or receipt.get("build_executed") is not False
        or receipt.get("signing_executed") is not False
        or receipt.get("packaging_executed") is not False
        or receipt.get("offline") is not True
        or receipt.get("network_operations") != 0
        or os.path.lexists(str(paths["transaction_root"]))
    ):
        raise PipelineError("fresh x86_64 preparation receipt schema mismatch")
    alias_context = _recorded_alias_context(source, developer_dir)
    reclaimed_arm = _reclaimed_arm_onboarding_evidence(source)
    preparation_path, _ = preparation_contract(
        source,
        allow_reclaimed_arm=True,
        alias_context=alias_context,
        reclaimed_arm=reclaimed_arm,
    )
    onboarding = _reclaimed_arm_onboarding_contract(source, reclaimed_arm)
    acquisition_path, _ = acquisition_contract(source)
    tool_path, _ = tool_receipt_contract(source, developer_dir)
    current_links = {
        "acquisition_receipt": {
            "path": str(acquisition_path), "sha256": sha256_file(acquisition_path)
        },
        "tool_receipt": {
            "path": str(tool_path), "sha256": sha256_file(tool_path)
        },
        "preparation_receipt": {
            "path": str(preparation_path), "sha256": sha256_file(preparation_path)
        },
        "reclaimed_arm_onboarding": onboarding,
    }
    if any(receipt.get(key) != value for key, value in current_links.items()):
        raise PipelineError("fresh x86_64 preparation provenance changed")
    xcode_path, _ = xcode27_compat_receipt_contract(
        source, developer_dir, True, True, alias_context
    )
    seatbelt_path, _ = xcode27_seatbelt_receipt_contract(
        source, developer_dir, True, True, alias_context
    )
    screen_ai_path, _ = screen_ai_disabled_receipt_contract(
        source, developer_dir, True, True, alias_context
    )
    linkedit_path, linkedit = xcode27_linkedit_strip_receipt_contract(
        source, developer_dir, True, True, alias_context
    )
    hashes = {
        "xcode27_compatibility_receipt_sha256": sha256_file(xcode_path),
        "xcode27_seatbelt_compatibility_receipt_sha256": sha256_file(seatbelt_path),
        "screen_ai_disabled_compatibility_receipt_sha256": sha256_file(screen_ai_path),
        "xcode27_linkedit_strip_compatibility_receipt_sha256": sha256_file(linkedit_path),
    }
    if any(receipt.get(key) != value for key, value in hashes.items()) or receipt.get(
        "linkedit_strip_tools"
    ) != linkedit["tools"]:
        raise PipelineError("fresh x86_64 compatibility provenance changed")
    legacy = _verify_legacy_x64_inventory(
        Path(receipt["legacy_out"]), receipt["legacy_inventory"]
    )
    prepared = receipt.get("prepared_evidence")
    prepared_path = paths["legacy_root"] / FRESH_X64_TRANSACTION_PREPARED
    if prepared != {
        "path": str(prepared_path), "sha256": sha256_file(prepared_path)
    }:
        raise PipelineError("fresh x86_64 prepared evidence changed")
    if legacy["tree_sha256"] != receipt["legacy_inventory"]["tree_sha256"]:
        raise PipelineError("fresh x86_64 legacy tree changed")
    if _fresh_x64_directory_identity(paths["out"], "fresh x86_64 output") != receipt.get(
        "fresh_out_identity"
    ):
        raise PipelineError("fresh x86_64 output identity changed")
    graph = _fresh_x64_generated_graph_contract(paths["out"], linkedit["tools"])
    if graph != receipt.get("generated_graph"):
        raise PipelineError("fresh x86_64 generated graph changed")
    profile = focus_macos.validate_gn_profiles()["profiles"]["x64"]
    args_data = profile["args_gn"].encode("utf-8")
    expected_profile = {
        "flags_file": profile["flags_file"],
        "arg_names": profile["arg_names"],
        "args_gn_bytes": len(args_data),
        "args_gn_sha256": hashlib.sha256(args_data).hexdigest(),
    }
    command = [str(tool_paths(source)["gn"]), "gen", X64_OUT, "--fail-on-unused-args"]
    if (
        receipt.get("fresh_profile") != expected_profile
        or receipt.get("gn_command") != command
        or graph["args_gn"]["sha256"] != expected_profile["args_gn_sha256"]
        or os.path.lexists(str(paths["out"] / ".ninja_log"))
        or os.path.lexists(str(paths["out"] / ".ninja_deps"))
        or os.path.lexists(str(paths["out"] / APP_NAME))
    ):
        raise PipelineError("fresh x86_64 no-Ninja graph contract changed")
    return paths["receipt"], receipt


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


def _path_identity(path):
    observed = os.stat(str(path), follow_symlinks=True)
    return {"device": observed.st_dev, "inode": observed.st_ino}


def _run_bounded_output(command, max_bytes, timeout_seconds, label):
    """Run one local inspection command with a hard combined-output bound."""
    process = subprocess.Popen(
        command,
        env={
            "PATH": SYSTEM_PATH,
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    if process.stdout is None:
        raise PipelineError("{} has no output pipe".format(label))
    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    selector = selectors.DefaultSelector()
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    selector.register(descriptor, selectors.EVENT_READ)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PipelineError("{} timed out".format(label))
            events = selector.select(min(remaining, 0.25))
            if not events and process.poll() is not None:
                events = [(selector.get_key(descriptor), selectors.EVENT_READ)]
            for key, _ in events:
                try:
                    chunk = os.read(key.fd, min(64 * 1024, max_bytes + 1))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                if len(output) + len(chunk) > max_bytes:
                    raise PipelineError("{} output exceeded its bound".format(label))
                output.extend(chunk)
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise PipelineError("{} timed out".format(label)) from exc
        if _process_group_exists(process.pid):
            _stop_process(process, force=True)
            raise PipelineError("{} left descendant processes".format(label))
        if process.returncode:
            raise PipelineError("{} failed with exit {}".format(label, process.returncode))
        return bytes(output)
    except BaseException:
        _stop_process(process, force=True)
        raise
    finally:
        selector.close()
        process.stdout.close()


def _volume_identity(path):
    """Return a stable APFS volume UUID for one existing local path."""
    path = Path(path)
    if not path.is_absolute() or Path(os.path.abspath(str(path))) != path:
        raise PipelineError("home-alias volume path must be absolute and normalized")
    try:
        before = os.lstat(str(path))
    except OSError as exc:
        raise PipelineError("home-alias volume path must exist") from exc
    if stat.S_ISLNK(before.st_mode) or not (
        stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)
    ):
        raise PipelineError("home-alias volume path must be absolute and existing")
    df_output = _run_bounded_output(
        ["/bin/df", "-P", str(path)],
        64 * 1024,
        10,
        "home-alias filesystem inspection",
    )
    try:
        df_lines = [
            line for line in df_output.decode("utf-8").splitlines() if line.strip()
        ]
    except UnicodeDecodeError as exc:
        raise PipelineError("home-alias filesystem report is not UTF-8") from exc
    if (
        len(df_lines) != 2
        or not df_lines[0].startswith("Filesystem ")
        or len(df_lines[1].split()) < 6
    ):
        raise PipelineError("home-alias filesystem report is invalid")
    device_node = df_lines[1].split()[0]
    if not re.fullmatch(r"/dev/[A-Za-z0-9._-]+", device_node):
        raise PipelineError("home-alias filesystem device is invalid")
    disk_output = _run_bounded_output(
        ["/usr/sbin/diskutil", "info", "-plist", device_node],
        256 * 1024,
        10,
        "home-alias APFS inspection",
    )
    try:
        value = plistlib.loads(disk_output)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise PipelineError("home-alias volume report is invalid") from exc
    if not isinstance(value, dict):
        raise PipelineError("home-alias volume report is not a dictionary")
    volume_uuid = value.get("VolumeUUID")
    mount_point = value.get("MountPoint")
    if (
        value.get("DeviceNode") != device_node
        or value.get("FilesystemType") != "apfs"
        or not isinstance(mount_point, str)
        or not mount_point.startswith("/")
        or Path(os.path.abspath(mount_point)) != Path(mount_point)
        or not Path(mount_point).is_dir()
        or not isinstance(volume_uuid, str)
        or not re.fullmatch(
            r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}",
            volume_uuid,
        )
    ):
        raise PipelineError("home-alias volume identity is incomplete")
    mount_stat = os.lstat(mount_point)
    try:
        after = os.lstat(str(path))
    except OSError as exc:
        raise PipelineError("home-alias volume path changed during inspection") from exc
    if (
        stat.S_ISLNK(mount_stat.st_mode)
        or not stat.S_ISDIR(mount_stat.st_mode)
        or before.st_dev != mount_stat.st_dev
        or (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode))
        != (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode))
    ):
        raise PipelineError("home-alias path and APFS mount device do not match")
    return {
        "device_node": device_node,
        "mount_point": mount_point,
        "volume_uuid": volume_uuid.upper(),
    }


def _same_inode_mapping(logical, physical, label, volume_uuid=None):
    logical_identity = _path_identity(logical)
    physical_identity = _path_identity(physical)
    if logical_identity != physical_identity:
        raise PipelineError("{} alias does not resolve to the same inode".format(label))
    observed = os.stat(str(physical), follow_symlinks=False)
    return {
        "logical": str(logical),
        "physical": str(physical),
        "identity": {
            "volume_uuid": volume_uuid,
            "device": logical_identity["device"],
            "inode": logical_identity["inode"],
            "uid": observed.st_uid,
            "gid": observed.st_gid,
            "mode": stat.S_IMODE(observed.st_mode),
        },
    }


def _require_real_descendant(root, path, label, allow_root_symlink=False):
    root = Path(root)
    path = Path(path)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PipelineError("{} is outside its declared root".format(label)) from exc
    cursor = root
    if (cursor.is_symlink() and not allow_root_symlink) or not cursor.is_dir():
        raise PipelineError("{} root must be a real directory".format(label))
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PipelineError("{} traverses an extra symlink: {}".format(label, cursor))
    return relative


def _legacy_receipt_inventory(source, developer_dir, alias_context):
    """Hash the immutable receipt chain without rewriting legacy bytes."""
    allow_reclaimed_arm = not (source / ARM_OUT).exists()
    preparation_path, preparation = preparation_contract(
        source,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    acquisition_path, _ = acquisition_contract(source)
    tool_path, _ = tool_receipt_contract(source, developer_dir)
    gn_path, _ = gn_compat_receipt_contract(
        source, preparation_path, required=True, alias_context=alias_context
    )
    xcode_path, _ = xcode27_compat_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    seatbelt_path, _ = xcode27_seatbelt_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    screen_ai_path, _ = screen_ai_disabled_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    linkedit_path, _ = xcode27_linkedit_strip_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    cache_marker = Path(
        preparation["dependency_contract"]["cache_marker"]["path"]
    )
    ordered = (
        ("acquisition", acquisition_path),
        ("tool_bootstrap", tool_path),
        ("dependency_cache", cache_marker),
        ("preparation", preparation_path),
        ("gn_compatibility", gn_path),
        ("xcode27_compatibility", xcode_path),
        ("xcode27_seatbelt_compatibility", seatbelt_path),
        ("screen_ai_disabled_compatibility", screen_ai_path),
        ("xcode27_linkedit_strip_compatibility", linkedit_path),
    )
    return {
        name: {"path": str(path), "sha256": sha256_file(path)}
        for name, path in ordered
    }


def _build_alias_context(source, developer_dir, logical_home, logical_workspace):
    source = Path(source)
    developer_dir = Path(developer_dir)
    logical_home = Path(logical_home)
    logical_workspace = Path(logical_workspace)
    for label, path in (
        ("source root", source),
        ("Developer directory", developer_dir),
        ("logical home", logical_home),
        ("logical workspace", logical_workspace),
    ):
        if not path.is_absolute() or Path(os.path.abspath(str(path))) != path:
            raise PipelineError("{} must be an absolute normalized path".format(label))
    if logical_home.parent != Path("/Users"):
        raise PipelineError("logical home alias must be a direct /Users child")
    alias_stat = os.lstat(str(logical_home))
    if not stat.S_ISLNK(alias_stat.st_mode) or alias_stat.st_uid != 0:
        raise PipelineError("logical home alias must be a root-owned symbolic link")
    if alias_stat.st_mode & 0o022:
        raise PipelineError("logical home alias metadata is unexpectedly writable")
    raw_target = Path(os.readlink(str(logical_home)))
    if not raw_target.is_absolute():
        raise PipelineError("logical home alias target must be absolute")
    physical_home = raw_target.resolve(strict=True)
    if raw_target != physical_home or physical_home.is_symlink() or not physical_home.is_dir():
        raise PipelineError("logical home alias target must be one exact real directory")
    if physical_home.parent != Path("/Users"):
        raise PipelineError("physical home must be a direct /Users child")
    physical_home_stat = os.stat(str(physical_home), follow_symlinks=False)
    if (
        physical_home_stat.st_uid != os.getuid()
        or physical_home_stat.st_mode & 0o022
    ):
        raise PipelineError("physical home must be owned by the invoking user")
    workspace_suffix = _require_real_descendant(
        logical_home,
        logical_workspace,
        "logical workspace",
        allow_root_symlink=True,
    )
    source_suffix = _require_real_descendant(
        logical_workspace, source, "logical Chromium source"
    )
    developer_suffix = _require_real_descendant(
        logical_home,
        developer_dir,
        "logical Xcode Developer directory",
        allow_root_symlink=True,
    )
    physical_workspace = physical_home / workspace_suffix
    physical_source = physical_workspace / source_suffix
    physical_developer = physical_home / developer_suffix
    physical_repo = MACOS_DIR.parent.parent
    repo_suffix = _require_real_descendant(
        physical_workspace, physical_repo, "physical macOS repository"
    )
    logical_repo = logical_workspace / repo_suffix
    _require_real_descendant(physical_home, physical_workspace, "physical workspace")
    _require_real_descendant(
        physical_workspace, physical_source, "physical Chromium source"
    )
    _require_real_descendant(
        physical_home, physical_developer, "physical Xcode Developer directory"
    )
    try:
        validated_source, _ = focus_macos.resolve_source_root(str(physical_source))
        developer_contract(physical_developer)
    except focus_macos.ContractError as exc:
        raise PipelineError(str(exc)) from exc
    if validated_source != physical_source:
        raise PipelineError("physical Chromium source identity changed")
    volume = _volume_identity(physical_home)
    for label, path in (
        ("workspace", physical_workspace),
        ("source", physical_source),
        ("Developer directory", physical_developer),
        ("repository", physical_repo),
    ):
        if _volume_identity(path)["volume_uuid"] != volume["volume_uuid"]:
            raise PipelineError("{} is on a different volume".format(label))
    return AliasContext(
        logical_home=logical_home,
        physical_home=physical_home,
        logical_workspace=logical_workspace,
        physical_workspace=physical_workspace,
        logical_source=source,
        physical_source=physical_source,
        logical_developer=developer_dir,
        physical_developer=physical_developer,
        logical_repo=logical_repo,
        physical_repo=physical_repo,
        volume_uuid=volume["volume_uuid"],
    )


def _home_alias_value(source, developer_dir, logical_home, logical_workspace):
    context = _build_alias_context(
        source, developer_dir, logical_home, logical_workspace
    )
    logical_home = context.logical_home
    physical_home = context.physical_home
    alias_stat = os.lstat(str(logical_home))
    physical_home_stat = os.stat(str(physical_home), follow_symlinks=False)
    raw_target = Path(os.readlink(str(logical_home)))
    mappings = {}
    for name, logical, physical in context.pairs():
        mappings[name] = _same_inode_mapping(
            logical, physical, name, volume_uuid=context.volume_uuid
        )
    parent_stat = os.stat("/Users", follow_symlinks=False)
    if (
        parent_stat.st_uid != 0
        or not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_mode & 0o022
    ):
        raise PipelineError("/Users must remain a non-writable root-owned directory")
    value = {
        "schema": HOME_ALIAS_RECEIPT_SCHEMA,
        "logical_home": str(logical_home),
        "physical_home": str(physical_home),
        "volume": {
            "filesystem": "apfs",
            "volume_uuid": context.volume_uuid,
        },
        "alias": {
            "path": str(logical_home),
            "target": str(raw_target),
            "device": alias_stat.st_dev,
            "inode": alias_stat.st_ino,
            "uid": alias_stat.st_uid,
            "gid": alias_stat.st_gid,
            "mode": stat.S_IMODE(alias_stat.st_mode),
            "root_owned": True,
            "absolute_exact_target": True,
            "target_identity": {
                "device": physical_home_stat.st_dev,
                "inode": physical_home_stat.st_ino,
                "uid": physical_home_stat.st_uid,
                "gid": physical_home_stat.st_gid,
                "mode": stat.S_IMODE(physical_home_stat.st_mode),
                "volume_uuid": context.volume_uuid,
            },
        },
        "mappings": mappings,
        "legacy_receipts": _legacy_receipt_inventory(
            context.logical_source, context.logical_developer, context
        ),
        "legacy_receipts_rewritten": False,
        "gn_gen_executed": False,
        "build_executed": False,
        "signing_executed": False,
        "packaging_executed": False,
        "offline": True,
        "network_operations": 0,
    }
    final_alias_stat = os.lstat(str(logical_home))
    if (
        final_alias_stat.st_dev != alias_stat.st_dev
        or final_alias_stat.st_ino != alias_stat.st_ino
        or final_alias_stat.st_uid != alias_stat.st_uid
        or final_alias_stat.st_gid != alias_stat.st_gid
        or final_alias_stat.st_mode != alias_stat.st_mode
        or Path(os.readlink(str(logical_home))) != raw_target
    ):
        raise PipelineError("logical home alias changed during validation")
    return value


def home_alias_plan(source, developer_dir, logical_home, logical_workspace):
    receipt = in_source(source, HOME_ALIAS_RECEIPT, "home-alias receipt")
    if receipt.exists() or receipt.is_symlink():
        raise PipelineError("home-alias compatibility receipt already exists")
    value = _home_alias_value(
        source, developer_dir, logical_home, logical_workspace
    )
    return {
        "stage": "adopt-home-alias",
        "receipt": str(receipt),
        "value": value,
    }


def execute_home_alias(
    source,
    developer_dir,
    logical_home,
    logical_workspace,
    plan,
    allow_adoption,
):
    if not allow_adoption:
        raise PipelineError("home-alias adoption requires --confirm-home-alias")
    expected = home_alias_plan(
        source, developer_dir, logical_home, logical_workspace
    )
    if plan != expected:
        raise PipelineError("home-alias compatibility changed before execution")
    return atomic_json(Path(expected["receipt"]), expected["value"])


def home_alias_receipt_contract(source, developer_dir):
    receipt_path = in_source(
        source, HOME_ALIAS_RECEIPT, "home-alias receipt", must_exist=True
    )
    receipt = load_json(receipt_path, "home-alias receipt")
    expected_keys = {
        "schema",
        "logical_home",
        "physical_home",
        "volume",
        "alias",
        "mappings",
        "legacy_receipts",
        "legacy_receipts_rewritten",
        "gn_gen_executed",
        "build_executed",
        "signing_executed",
        "packaging_executed",
        "offline",
        "network_operations",
    }
    if (
        set(receipt) != expected_keys
        or receipt.get("schema") != HOME_ALIAS_RECEIPT_SCHEMA
    ):
        raise PipelineError("home-alias receipt schema mismatch")
    workspace = receipt.get("mappings", {}).get("workspace", {}).get("logical")
    if not isinstance(workspace, str):
        raise PipelineError("home-alias workspace mapping is missing")
    current = _home_alias_value(
        source,
        developer_dir,
        Path(receipt.get("logical_home", "")),
        Path(workspace),
    )
    def durable(value):
        value = json.loads(json.dumps(value))
        value["alias"].pop("device", None)
        value["alias"]["target_identity"].pop("device", None)
        for mapping in value["mappings"].values():
            mapping["identity"].pop("device", None)
        return value

    recorded_devices = [
        receipt.get("alias", {}).get("device"),
        receipt.get("alias", {}).get("target_identity", {}).get("device"),
    ] + [
        mapping.get("identity", {}).get("device")
        for mapping in receipt.get("mappings", {}).values()
        if isinstance(mapping, dict)
    ]
    if any(type(device) is not int or device <= 0 for device in recorded_devices):
        raise PipelineError("home-alias device observations are invalid")
    if durable(receipt) != durable(current):
        raise PipelineError("home-alias identity or immutable receipt chain changed")
    return receipt_path, receipt


def onboarding_alias_root_receipt_contract(source):
    """Bind resumed builds to the audited fix for Vite's logical-home root."""
    receipt_path = in_source(
        source,
        onboarding_alias_compat.RECEIPT_RELATIVE,
        "onboarding alias-root receipt",
        must_exist=True,
    )
    receipt = load_json(receipt_path, "onboarding alias-root receipt")
    trial = receipt.get("trial_evidence")
    trial_report = trial.get("trial_report") if isinstance(trial, dict) else None
    failure_report = (
        trial.get("failure_report") if isinstance(trial, dict) else None
    )
    if (
        not isinstance(trial_report, dict)
        or not isinstance(failure_report, dict)
        or not isinstance(trial_report.get("path"), str)
        or not isinstance(failure_report.get("path"), str)
    ):
        raise PipelineError("onboarding alias-root evidence links are missing")
    try:
        home_alias = onboarding_alias_compat.validate_home_alias_receipt(source)
        physical_workspace = Path(
            home_alias["mappings"]["workspace"]["physical"]
        )
        trial_path = (
            physical_workspace
            / "work/logs"
            / onboarding_alias_compat.TRIAL_REPORT_BASENAME
        )
        failure_path = (
            physical_workspace
            / "work/logs"
            / onboarding_alias_compat.FAILURE_REPORT_BASENAME
        )
        if (
            Path(trial_report["path"]).name
            != onboarding_alias_compat.TRIAL_REPORT_BASENAME
            or Path(failure_report["path"]).name
            != onboarding_alias_compat.FAILURE_REPORT_BASENAME
        ):
            raise onboarding_alias_compat.AliasCompatError(
                "onboarding alias-root evidence basenames changed"
            )
        contract = onboarding_alias_compat.receipt_contract(
            source,
            trial_path=trial_path,
            failure_path=failure_path,
        )
    except (KeyError, TypeError, onboarding_alias_compat.AliasCompatError) as exc:
        raise PipelineError(str(exc)) from exc
    physical_receipt = Path(contract.get("path", ""))
    if (
        physical_receipt.resolve(strict=True)
        != receipt_path.resolve(strict=True)
        or contract.get("value") != receipt
        or contract.get("bytes") != receipt_path.stat().st_size
        or contract.get("sha256") != sha256_file(receipt_path)
    ):
        raise PipelineError("onboarding alias-root receipt contract mismatch")
    return receipt_path, receipt, {
        "path": str(receipt_path),
        "bytes": contract["bytes"],
        "sha256": contract["sha256"],
    }


def _recorded_alias_context(source, developer_dir=None):
    """Rebuild the explicit context without trusting persisted device numbers."""
    receipt_path = in_source(
        Path(source), HOME_ALIAS_RECEIPT, "home-alias receipt", must_exist=True
    )
    receipt = load_json(receipt_path, "home-alias receipt")
    mappings = receipt.get("mappings")
    if (
        receipt.get("schema") != HOME_ALIAS_RECEIPT_SCHEMA
        or not isinstance(mappings, dict)
        or set(mappings) != {"workspace", "source", "developer", "repo"}
    ):
        raise PipelineError("home-alias receipt schema mismatch")
    recorded_developer = Path(mappings["developer"].get("logical", ""))
    if developer_dir is not None and Path(developer_dir) != recorded_developer:
        raise PipelineError("home-alias Developer directory mismatch")
    context = _build_alias_context(
        Path(source),
        recorded_developer,
        Path(receipt.get("logical_home", "")),
        Path(mappings["workspace"].get("logical", "")),
    )
    if (
        receipt.get("physical_home") != str(context.physical_home)
        or receipt.get("volume")
        != {"filesystem": "apfs", "volume_uuid": context.volume_uuid}
    ):
        raise PipelineError("home-alias stable volume identity changed")
    for name, logical, physical in context.pairs():
        recorded = mappings.get(name)
        current = _same_inode_mapping(
            logical, physical, name, volume_uuid=context.volume_uuid
        )
        if not isinstance(recorded, dict):
            raise PipelineError("home-alias {} mapping is missing".format(name))
        recorded_stable = json.loads(json.dumps(recorded))
        current_stable = json.loads(json.dumps(current))
        recorded_device = recorded_stable.get("identity", {}).pop("device", None)
        current_stable.get("identity", {}).pop("device", None)
        if type(recorded_device) is not int or recorded_device <= 0:
            raise PipelineError("home-alias device observation is invalid")
        if recorded_stable != current_stable:
            raise PipelineError("home-alias {} identity changed".format(name))
    return context


def _home_alias_is_active(source):
    receipt = in_source(source, HOME_ALIAS_RECEIPT, "home-alias receipt")
    return receipt.exists() or receipt.is_symlink()


def _build_child_environment(source, developer_dir, build_ninja=None):
    """Construct a child environment, revalidating the alias when applicable."""
    source = Path(source)
    developer_dir = Path(developer_dir)
    if source.resolve(strict=True) != source:
        context = _recorded_alias_context(source, developer_dir)
        return safe_environment(
            source,
            developer_dir,
            inherited={"HOME": str(context.logical_home)},
            build_ninja=build_ninja,
            alias_context=context,
        )
    return safe_environment(source, developer_dir, build_ninja=build_ninja)


def _regular_file_snapshot(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PipelineError("execution evidence file must be regular: {}".format(path))
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb", closefd=True) as stream:
        observed = os.fstat(stream.fileno())
        if not stat.S_ISREG(observed.st_mode):
            raise PipelineError("execution evidence file changed while opening")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after_read = os.fstat(stream.fileno())
        if (
            after_read.st_dev != observed.st_dev
            or after_read.st_ino != observed.st_ino
            or after_read.st_size != observed.st_size
            or after_read.st_mtime_ns != observed.st_mtime_ns
            or after_read.st_ctime_ns != observed.st_ctime_ns
        ):
            raise PipelineError("execution evidence file changed while hashing")
    after_path = os.stat(str(path), follow_symlinks=False)
    if (
        after_path.st_dev != observed.st_dev
        or after_path.st_ino != observed.st_ino
        or after_path.st_size != observed.st_size
        or after_path.st_mtime_ns != observed.st_mtime_ns
        or after_path.st_ctime_ns != observed.st_ctime_ns
    ):
        raise PipelineError("execution evidence path changed while hashing")
    return {
        "path": str(path),
        "bytes": observed.st_size,
        "mtime_ns": observed.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def _sha256_file_prefix(path, byte_count):
    """Hash exactly the first recorded bytes of an unchanged regular inode."""
    path = Path(path)
    if type(byte_count) is not int or byte_count < 0:
        raise PipelineError("prefix byte count is invalid")
    before = os.stat(str(path), follow_symlinks=False)
    if path.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_size < byte_count:
        raise PipelineError("prefix source is not a large-enough regular file")
    digest = hashlib.sha256()
    remaining = byte_count
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb", closefd=True) as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ):
            raise PipelineError("prefix source changed while opening")
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise PipelineError("prefix source was truncated while hashing")
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(stream.fileno())
        if (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise PipelineError("prefix source changed while hashing")
    return digest.hexdigest()


def _ninja_history_snapshot(out):
    out = Path(out)
    return {
        name: _regular_file_snapshot(out / relative)
        for name, relative in (
            ("ninja_log", ".ninja_log"),
            ("ninja_deps", ".ninja_deps"),
        )
    }


def _ninja_history_exact_contract(recorded, out, label):
    if not isinstance(recorded, dict) or set(recorded) != {
        "ninja_log",
        "ninja_deps",
    }:
        raise PipelineError("{} history schema mismatch".format(label))
    for name in ("ninja_log", "ninja_deps"):
        expected_path = Path(out) / (
            ".ninja_log" if name == "ninja_log" else ".ninja_deps"
        )
        _validate_recorded_file_snapshot(
            recorded.get(name), expected_path, "{} {}".format(label, name)
        )
    current = _ninja_history_snapshot(out)
    if recorded != current:
        raise PipelineError("{} history changed after authorization".format(label))
    return current


def _copy_regular_snapshot(source, destination):
    """Copy one regular file into a private rollback area and fsync it."""
    source = Path(source)
    destination = Path(destination)
    before = os.stat(str(source), follow_symlinks=False)
    if source.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise PipelineError("rollback source is not a regular file")
    source_fd = os.open(str(source), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    destination_fd = os.open(
        str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(source_fd, "rb", closefd=False) as input_stream, os.fdopen(
            destination_fd, "wb", closefd=False
        ) as output_stream:
            shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        after = os.fstat(source_fd)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise PipelineError("rollback source changed while copying")
        os.fchmod(destination_fd, stat.S_IMODE(before.st_mode))
        os.utime(
            destination,
            ns=(before.st_atime_ns, before.st_mtime_ns),
            follow_symlinks=False,
        )
        os.fsync(destination_fd)
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    return {
        "backup": str(destination),
        "mode": stat.S_IMODE(before.st_mode),
        "atime_ns": before.st_atime_ns,
        "snapshot": _regular_file_snapshot(source),
    }


def _snapshot_alias_ninja_history(source, snapshot_root):
    alias_receipt = in_source(source, HOME_ALIAS_RECEIPT, "home-alias receipt")
    if not alias_receipt.exists() and not alias_receipt.is_symlink():
        return None
    out = in_source(
        source, X64_OUT, "x86_64 output", must_exist=True, directory=True
    )
    snapshot_root = Path(snapshot_root)
    return {
        "out": str(out),
        "files": {
            name: _copy_regular_snapshot(out / relative, snapshot_root / name)
            for name, relative in (
                ("ninja_log", ".ninja_log"),
                ("ninja_deps", ".ninja_deps"),
            )
        },
    }


def _restore_alias_ninja_history(rollback):
    if rollback is None:
        return
    out = Path(rollback["out"])
    for name, relative in (
        ("ninja_log", ".ninja_log"),
        ("ninja_deps", ".ninja_deps"),
    ):
        item = rollback["files"][name]
        destination = out / relative
        current = os.lstat(str(destination))
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            raise PipelineError("unsafe Ninja history rollback destination")
        temporary = destination.with_name(
            ".{}.focus-history-rollback".format(destination.name)
        )
        if os.path.lexists(str(temporary)):
            raise PipelineError("stale Ninja history rollback temporary exists")
        _copy_regular_snapshot(Path(item["backup"]), temporary)
        os.chmod(temporary, item["mode"], follow_symlinks=False)
        os.utime(
            temporary,
            ns=(item["atime_ns"], item["snapshot"]["mtime_ns"]),
            follow_symlinks=False,
        )
        temporary_fd = os.open(
            str(temporary), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)
        os.replace(str(temporary), str(destination))
        directory_fd = os.open(str(out), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if _regular_file_snapshot(destination) != item["snapshot"]:
            raise PipelineError("Ninja history rollback did not restore exact bytes")


def _fsync_directory(path):
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stat_identity_value(observed):
    return {
        "device": observed.st_dev,
        "inode": observed.st_ino,
        "uid": observed.st_uid,
        "gid": observed.st_gid,
        "mode": observed.st_mode,
        "bytes": observed.st_size,
        "mtime_ns": observed.st_mtime_ns,
        "ctime_ns": observed.st_ctime_ns,
    }


def _lstat_identity(path):
    return _stat_identity_value(os.lstat(str(path)))


def _unlink_regular_identity(path, expected, label):
    """Unlink one exact regular inode using a held parent directory fd."""
    path = Path(path)
    if not isinstance(expected, dict) or set(expected) != {
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
        "bytes",
        "mtime_ns",
        "ctime_ns",
    }:
        raise PipelineError("{} identity schema mismatch".format(label))
    parent_fd = os.open(
        str(path.parent),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    descriptor = None
    try:
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or _stat_identity_value(current) != expected
        ):
            raise PipelineError("{} identity changed before removal".format(label))
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (
            expected["device"],
            expected["inode"],
        ):
            raise PipelineError("{} changed while opening".format(label))
        final = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        final_identity = _stat_identity_value(final)
        if final_identity != expected:
            raise PipelineError("{} identity changed before unlink".format(label))
        os.unlink(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def _remove_failed_execution_receipt(
    receipt_path, publication_identity, validator, label
):
    """Remove only this execution's invalid publication, never a valid commit."""
    receipt_path = Path(receipt_path)
    if not os.path.lexists(str(receipt_path)):
        return False
    try:
        validator()
    except PipelineError:
        pass
    else:
        raise PipelineError(
            "{} is a valid committed receipt; refusing rollback".format(label)
        )
    if publication_identity is None:
        raise PipelineError(
            "{} was not published by this execution; refusing removal".format(label)
        )
    observed = _lstat_identity(receipt_path)
    if observed != publication_identity:
        raise PipelineError("{} identity changed before rollback".format(label))
    _unlink_regular_identity(receipt_path, publication_identity, label)
    return True


def _remove_directory_inode(path, expected, label):
    """Recursively empty and remove only the directory inode authorized earlier."""
    path = Path(path)
    if not isinstance(expected, dict) or set(expected) != {
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
    }:
        raise PipelineError("{} live identity schema mismatch".format(label))
    parent_fd = os.open(
        str(path.parent),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )

    def remove_children(directory_fd):
        for name in sorted(os.listdir(directory_fd)):
            observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(observed.st_mode):
                child_fd = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    opened = os.fstat(child_fd)
                    if (opened.st_dev, opened.st_ino) != (
                        observed.st_dev,
                        observed.st_ino,
                    ):
                        raise PipelineError(
                            "{} child changed while opening".format(label)
                        )
                    remove_children(child_fd)
                    current = os.stat(
                        name, dir_fd=directory_fd, follow_symlinks=False
                    )
                    if (current.st_dev, current.st_ino) != (
                        opened.st_dev,
                        opened.st_ino,
                    ):
                        raise PipelineError(
                            "{} child identity changed before rmdir".format(label)
                        )
                    os.rmdir(name, dir_fd=directory_fd)
                finally:
                    os.close(child_fd)
            else:
                os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)

    directory_fd = None
    try:
        directory_fd = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (
                expected.get("device"),
                expected.get("inode"),
            )
        ):
            raise PipelineError("{} identity changed before cleanup".format(label))
        remove_children(directory_fd)
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise PipelineError("{} path was replaced during cleanup".format(label))
        os.rmdir(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(parent_fd)


def _signing_transaction_targets(source, stage):
    if stage == "swiftshader":
        relative = next(iter(SWIFTSHADER_DISABLED_SIGNING_FILES))
        return {
            "source_parts": in_source(source, relative, "SwiftShader source rollback"),
            "packaging_parts": in_source(
                source,
                X64_OUT + "/" + PACKAGING_NAME + "/signing/parts.py",
                "SwiftShader package rollback",
            ),
            "ninja_log": in_source(
                source, X64_OUT + "/.ninja_log", "SwiftShader Ninja log rollback"
            ),
            "ninja_deps": in_source(
                source, X64_OUT + "/.ninja_deps", "SwiftShader Ninja deps rollback"
            ),
        }
    if stage == "adhoc":
        targets = {}
        for position, relative in enumerate(ADHOC_RUNTIME_SIGNING_FILES, 1):
            targets["source_{:02d}".format(position)] = in_source(
                source, relative, "ad-hoc source rollback"
            )
        for position, relative in enumerate(
            ADHOC_RUNTIME_SIGNING_GENERATED_FILES, 1
        ):
            targets["packaging_{:02d}".format(position)] = in_source(
                source,
                X64_OUT
                + "/"
                + PACKAGING_NAME
                + "/signing/"
                + Path(relative).name,
                "ad-hoc package rollback",
            )
        targets["ninja_log"] = in_source(
            source, X64_OUT + "/.ninja_log", "ad-hoc Ninja log rollback"
        )
        targets["ninja_deps"] = in_source(
            source, X64_OUT + "/.ninja_deps", "ad-hoc Ninja deps rollback"
        )
        return targets
    raise PipelineError("unknown durable signing transaction stage")


def _signing_transaction_path(source, stage):
    relative = (
        SWIFTSHADER_SIGNING_TRANSACTION
        if stage == "swiftshader"
        else ADHOC_SIGNING_TRANSACTION
    )
    return in_source(source, relative, "durable signing transaction")


def _signing_transaction_cleanup_path(source, stage):
    root = _signing_transaction_path(source, stage)
    return root.with_name(root.name + ".cleanup")


def _signing_transaction_cleanup_marker_path(source, stage):
    root = _signing_transaction_path(source, stage)
    return root.with_name(root.name + ".cleanup-authorization.json")


def _signing_transaction_cleanup_marker_temp(source, stage):
    marker = _signing_transaction_cleanup_marker_path(source, stage)
    return marker.with_name(
        ".{}.{}.tmp".format(marker.name, os.urandom(16).hex())
    )


def _live_directory_identity(path):
    observed = os.lstat(str(path))
    if not stat.S_ISDIR(observed.st_mode):
        raise PipelineError("durable signing transaction root is not a directory")
    return {
        "device": observed.st_dev,
        "inode": observed.st_ino,
        "uid": observed.st_uid,
        "gid": observed.st_gid,
        "mode": observed.st_mode,
    }


def _stable_directory_identity(path):
    before = _live_directory_identity(path)
    volume_uuid = _volume_identity(path)["volume_uuid"]
    after = _live_directory_identity(path)
    if before != after:
        raise PipelineError(
            "durable signing transaction root changed during volume inspection"
        )
    return {
        "volume_uuid": volume_uuid,
        "inode": after["inode"],
        "uid": after["uid"],
        "gid": after["gid"],
        "mode": after["mode"],
    }


def _publish_signing_cleanup_authorization(source, stage, root_identity):
    marker = _signing_transaction_cleanup_marker_path(source, stage)
    temporary = _signing_transaction_cleanup_marker_temp(source, stage)
    if os.path.lexists(str(marker)):
        raise PipelineError("durable signing cleanup authorization already exists")
    alias_receipt = in_source(
        source, HOME_ALIAS_RECEIPT, "home-alias receipt", must_exist=True
    )
    value = {
        "schema": 1,
        "kind": "focus-macos-signing-cleanup-authorization",
        "stage": stage,
        "source_root": str(source),
        "transaction_root": str(_signing_transaction_path(source, stage)),
        "cleanup_path": str(_signing_transaction_cleanup_path(source, stage)),
        "root_identity": root_identity,
        "home_alias_receipt": {
            "path": str(alias_receipt),
            "sha256": sha256_file(alias_receipt),
        },
    }
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(
        str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444
    )
    published = False
    temporary_identity = None
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise PipelineError("short cleanup authorization write")
            offset += written
        os.fsync(descriptor)
        temporary_identity = _stat_identity_value(os.fstat(descriptor))
        try:
            os.link(str(temporary), str(marker), follow_symlinks=False)
        except FileExistsError as exc:
            raise PipelineError(
                "durable signing cleanup authorization raced"
            ) from exc
        published = True
        temporary_identity = _stat_identity_value(os.fstat(descriptor))
        os.close(descriptor)
        descriptor = None
        try:
            _unlink_regular_identity(
                temporary, temporary_identity, "cleanup authorization temporary"
            )
        except (OSError, PipelineError):
            # The uniquely named private link is never trusted or auto-removed
            # later.  A cleanup failure must not invalidate the published
            # no-replace authorization marker.
            pass
        _fsync_directory(marker.parent)
        return marker
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if (
            not published
            and temporary_identity is not None
            and os.path.lexists(str(temporary))
        ):
            _unlink_regular_identity(
                temporary,
                temporary_identity,
                "failed cleanup authorization temporary",
            )
        raise


def _cleanup_signing_transaction_tombstone(source, stage):
    """Idempotently finish a transaction cleanup atomically authorized earlier."""
    root = _signing_transaction_path(source, stage)
    cleanup = _signing_transaction_cleanup_path(source, stage)
    marker = _signing_transaction_cleanup_marker_path(source, stage)
    marker_exists = os.path.lexists(str(marker))
    if not marker_exists:
        if os.path.lexists(str(cleanup)):
            raise PipelineError(
                "durable signing cleanup tombstone has no authorization"
            )
        return
    marker_identity = _lstat_identity(marker)
    if (
        not stat.S_ISREG(marker_identity["mode"])
        or marker_identity["uid"] != os.getuid()
        or stat.S_IMODE(marker_identity["mode"]) & 0o222
    ):
        raise PipelineError("durable signing cleanup authorization is unsafe")
    authorization = load_json(marker, "durable signing cleanup authorization")
    alias_receipt = in_source(
        source, HOME_ALIAS_RECEIPT, "home-alias receipt", must_exist=True
    )
    expected_keys = {
        "schema",
        "kind",
        "stage",
        "source_root",
        "transaction_root",
        "cleanup_path",
        "root_identity",
        "home_alias_receipt",
    }
    if (
        set(authorization) != expected_keys
        or authorization.get("schema") != 1
        or authorization.get("kind")
        != "focus-macos-signing-cleanup-authorization"
        or authorization.get("stage") != stage
        or authorization.get("source_root") != str(source)
        or authorization.get("transaction_root") != str(root)
        or authorization.get("cleanup_path") != str(cleanup)
        or authorization.get("home_alias_receipt")
        != {"path": str(alias_receipt), "sha256": sha256_file(alias_receipt)}
    ):
        raise PipelineError("durable signing cleanup authorization mismatch")
    root_identity = authorization.get("root_identity")
    if not isinstance(root_identity, dict) or set(root_identity) != {
        "volume_uuid",
        "inode",
        "uid",
        "gid",
        "mode",
    } or not re.fullmatch(
        r"[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}",
        root_identity.get("volume_uuid", ""),
    ):
        raise PipelineError("durable signing cleanup root identity is invalid")
    root_exists = os.path.lexists(str(root))
    cleanup_exists = os.path.lexists(str(cleanup))
    if root_exists and cleanup_exists:
        raise PipelineError("durable signing cleanup has two live roots")
    if root_exists:
        if _stable_directory_identity(root) != root_identity:
            raise PipelineError("durable signing transaction root was replaced")
        live_identity = _live_directory_identity(root)
        os.replace(str(root), str(cleanup))
        _fsync_directory(cleanup.parent)
        if _live_directory_identity(cleanup) != live_identity:
            raise PipelineError(
                "durable signing transaction changed while tombstoning"
            )
        cleanup_exists = True
    if cleanup_exists:
        if _stable_directory_identity(cleanup) != root_identity:
            raise PipelineError("durable signing cleanup tombstone was replaced")
        live_identity = _live_directory_identity(cleanup)
        _remove_directory_inode(
            cleanup, live_identity, "durable signing cleanup tombstone"
        )
    _unlink_regular_identity(
        marker, marker_identity, "cleanup authorization marker"
    )


def _fsync_durable_signing_targets(source, stage):
    """Make every mutation covered by the crash journal durable before receipt."""
    parents = set()
    for target in _signing_transaction_targets(source, stage).values():
        target = Path(target)
        before = os.stat(str(target), follow_symlinks=False)
        if target.is_symlink() or not stat.S_ISREG(before.st_mode):
            raise PipelineError("durable signing target is unsafe before fsync")
        descriptor = os.open(
            str(target), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise PipelineError("durable signing target changed while opening")
            os.fsync(descriptor)
            after = os.fstat(descriptor)
            if (
                after.st_dev != opened.st_dev
                or after.st_ino != opened.st_ino
                or after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns
                or after.st_ctime_ns != opened.st_ctime_ns
            ):
                raise PipelineError("durable signing target changed during fsync")
            try:
                current = os.stat(str(target), follow_symlinks=False)
            except OSError as exc:
                raise PipelineError(
                    "durable signing target disappeared after fsync"
                ) from exc
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_dev != after.st_dev
                or current.st_ino != after.st_ino
                or current.st_size != after.st_size
                or current.st_mtime_ns != after.st_mtime_ns
                or current.st_ctime_ns != after.st_ctime_ns
            ):
                raise PipelineError(
                    "durable signing target path changed after fsync"
                )
        finally:
            os.close(descriptor)
        parents.add(target.parent)
    for parent in sorted(parents, key=str):
        if parent.is_symlink() or not parent.is_dir():
            raise PipelineError("durable signing target parent is unsafe")
        _fsync_directory(parent)


def _begin_durable_signing_transaction(source, stage, receipt_path):
    if not _home_alias_is_active(source):
        return None
    _cleanup_signing_transaction_tombstone(source, stage)
    root = _signing_transaction_path(source, stage)
    if os.path.lexists(str(root)):
        raise PipelineError("durable signing transaction already exists")
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.parent.is_symlink() or not root.parent.is_dir():
        raise PipelineError("durable signing transaction parent is unsafe")
    root.mkdir(parents=False, exist_ok=False)
    backups = root / "backups"
    backups.mkdir()
    items = []
    try:
        for label, target in _signing_transaction_targets(source, stage).items():
            if target.is_symlink() or not target.is_file():
                raise PipelineError(
                    "durable transaction target is not regular: {}".format(target)
                )
            item = _copy_regular_snapshot(target, backups / label)
            os.chmod(item["backup"], 0o444, follow_symlinks=False)
            backup_fd = os.open(
                item["backup"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                os.fsync(backup_fd)
            finally:
                os.close(backup_fd)
            items.append(
                {
                    "label": label,
                    "target": str(target),
                    "backup": item["backup"],
                    "backup_sha256": sha256_file(item["backup"]),
                    "mode": item["mode"],
                    "atime_ns": item["atime_ns"],
                    "snapshot": item["snapshot"],
                }
            )
        _fsync_directory(backups)
        alias_receipt = in_source(
            source, HOME_ALIAS_RECEIPT, "home-alias receipt", must_exist=True
        )
        journal_value = {
            "schema": 1,
            "kind": "focus-macos-signing-transaction",
            "stage": stage,
            "source_root": str(source),
            "receipt": str(receipt_path),
            "home_alias_receipt": {
                "path": str(alias_receipt),
                "sha256": sha256_file(alias_receipt),
            },
            "files": items,
            "prepared_before_mutation": True,
            "offline": True,
            "network_operations": 0,
        }
        journal_report = atomic_json(root / "journal.json", journal_value)
        os.chmod(journal_report["path"], 0o444, follow_symlinks=False)
        journal_fd = os.open(
            journal_report["path"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            os.fsync(journal_fd)
        finally:
            os.close(journal_fd)
        _fsync_directory(root)
        _fsync_directory(root.parent)
        return root
    except BaseException:
        # Nothing in the build tree has been mutated yet. Retain a complete
        # journal for recovery, but remove an incomplete private root.
        journal = root / "journal.json"
        if not journal.is_file() or journal.is_symlink():
            _discard_durable_signing_transaction(source, stage)
        raise


def _load_durable_signing_transaction(source, stage, receipt_path):
    if not _home_alias_is_active(source):
        return None
    _cleanup_signing_transaction_tombstone(source, stage)
    root = _signing_transaction_path(source, stage)
    if not os.path.lexists(str(root)):
        return None
    if root.is_symlink() or not root.is_dir():
        raise PipelineError("durable signing transaction root is unsafe")
    root_stat = os.stat(str(root), follow_symlinks=False)
    if root_stat.st_uid != os.getuid() or stat.S_IMODE(root_stat.st_mode) & 0o022:
        raise PipelineError("durable signing transaction ownership is unsafe")
    receipt_path = Path(receipt_path)
    receipt_exists = os.path.lexists(str(receipt_path))
    receipt_identity = None
    if receipt_exists:
        receipt_stat = os.lstat(str(receipt_path))
        if (
            not stat.S_ISREG(receipt_stat.st_mode)
            or receipt_stat.st_uid != os.getuid()
            or stat.S_IMODE(receipt_stat.st_mode) & 0o022
        ):
            raise PipelineError("durable signing transaction receipt is unsafe")
        receipt_identity = _lstat_identity(receipt_path)
    journal_path = root / "journal.json"
    if not os.path.lexists(str(journal_path)):
        # begin() cannot return, and callers cannot mutate targets, before the
        # immutable journal exists.  A missing journal is therefore either an
        # interrupted begin or an interrupted cleanup after receipt publication.
        if receipt_exists:
            return {
                "path": str(root),
                "journal": None,
                "journal_sha256": None,
                "receipt_published": True,
                "receipt_identity": receipt_identity,
                "cleanup_only": True,
            }
        _discard_durable_signing_transaction(source, stage)
        return None
    if journal_path.is_symlink() or not journal_path.is_file():
        raise PipelineError("durable signing transaction journal is unsafe")
    journal_stat = os.stat(str(journal_path), follow_symlinks=False)
    if stat.S_IMODE(journal_stat.st_mode) & 0o222:
        if receipt_exists:
            return {
                "path": str(root),
                "journal": None,
                "journal_sha256": None,
                "receipt_published": True,
                "receipt_identity": receipt_identity,
                "cleanup_only": True,
            }
        # atomic_json publishes before chmod; a mutable journal proves begin()
        # never returned, so no covered target could have been mutated yet.
        _discard_durable_signing_transaction(source, stage)
        return None
    try:
        journal = load_json(journal_path, "durable signing transaction journal")
        expected_keys = {
            "schema",
            "kind",
            "stage",
            "source_root",
            "receipt",
            "home_alias_receipt",
            "files",
            "prepared_before_mutation",
            "offline",
            "network_operations",
        }
        alias_receipt = in_source(
            source, HOME_ALIAS_RECEIPT, "home-alias receipt", must_exist=True
        )
        if (
            set(journal) != expected_keys
            or journal.get("schema") != 1
            or journal.get("kind") != "focus-macos-signing-transaction"
            or journal.get("stage") != stage
            or journal.get("source_root") != str(source)
            or journal.get("receipt") != str(receipt_path)
            or journal.get("home_alias_receipt")
            != {"path": str(alias_receipt), "sha256": sha256_file(alias_receipt)}
            or journal.get("prepared_before_mutation") is not True
            or journal.get("offline") is not True
            or journal.get("network_operations") != 0
        ):
            raise PipelineError("durable signing transaction journal mismatch")
        targets = _signing_transaction_targets(source, stage)
        items = journal.get("files")
        if not isinstance(items, list) or [
            item.get("label") for item in items
        ] != list(targets):
            raise PipelineError(
                "durable signing transaction file inventory mismatch"
            )
        for item in items:
            label = item["label"]
            backup = Path(item.get("backup", ""))
            expected_backup = root / "backups" / label
            if (
                set(item)
                != {
                    "label",
                    "target",
                    "backup",
                    "backup_sha256",
                    "mode",
                    "atime_ns",
                    "snapshot",
                }
                or item.get("target") != str(targets[label])
                or backup != expected_backup
                or backup.is_symlink()
                or not backup.is_file()
                or stat.S_IMODE(backup.stat().st_mode) & 0o222
                or item.get("backup_sha256") != sha256_file(backup)
            ):
                raise PipelineError("durable signing transaction backup mismatch")
            _validate_recorded_file_snapshot(
                item.get("snapshot"), targets[label], "durable signing backup"
            )
    except PipelineError:
        # An accepted receipt is the authority after commit.  If cleanup was
        # interrupted, its now-partial private journal is no longer needed;
        # the execute path must still validate the receipt before discarding it.
        if receipt_exists:
            return {
                "path": str(root),
                "journal": None,
                "journal_sha256": None,
                "receipt_published": True,
                "receipt_identity": receipt_identity,
                "cleanup_only": True,
            }
        raise
    return {
        "path": str(root),
        "journal": str(journal_path),
        "journal_sha256": sha256_file(journal_path),
        "receipt_published": receipt_exists,
        "receipt_identity": receipt_identity,
    }


def _restore_regular_transaction_item(item):
    target = Path(item["target"])
    backup = Path(item["backup"])
    if os.path.lexists(str(target)):
        current = os.lstat(str(target))
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            raise PipelineError("durable signing rollback target is unsafe")
    temporary = target.with_name(".{}.focus-signing-restore".format(target.name))
    if os.path.lexists(str(temporary)):
        observed = os.lstat(str(temporary))
        if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
            raise PipelineError("durable signing rollback temporary is unsafe")
        temporary.unlink()
    _copy_regular_snapshot(backup, temporary)
    os.chmod(temporary, item["mode"], follow_symlinks=False)
    os.utime(
        temporary,
        ns=(item["atime_ns"], item["snapshot"]["mtime_ns"]),
        follow_symlinks=False,
    )
    temporary_fd = os.open(
        str(temporary), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        os.fsync(temporary_fd)
    finally:
        os.close(temporary_fd)
    os.replace(str(temporary), str(target))
    _fsync_directory(target.parent)
    if _regular_file_snapshot(target) != item["snapshot"]:
        raise PipelineError("durable signing rollback did not restore exact bytes")


def _discard_durable_signing_transaction(source, stage):
    _cleanup_signing_transaction_tombstone(source, stage)
    root = _signing_transaction_path(source, stage)
    if not os.path.lexists(str(root)):
        return
    if root.is_symlink() or not root.is_dir():
        raise PipelineError("durable signing transaction cleanup root is unsafe")
    root_identity = _stable_directory_identity(root)
    if (
        root_identity["uid"] != os.getuid()
        or stat.S_IMODE(root_identity["mode"]) & 0o022
    ):
        raise PipelineError("durable signing transaction cleanup root is unsafe")
    _publish_signing_cleanup_authorization(source, stage, root_identity)
    _cleanup_signing_transaction_tombstone(source, stage)


def _remove_invalid_transaction_receipt(receipt_path, expected_identity):
    """Remove only an unaccepted local receipt so journal rollback can proceed."""
    receipt_path = Path(receipt_path)
    observed = _lstat_identity(receipt_path)
    if (
        not stat.S_ISREG(observed["mode"])
        or observed["uid"] != os.getuid()
        or stat.S_IMODE(observed["mode"]) & 0o022
        or observed != expected_identity
    ):
        raise PipelineError("invalid signing transaction receipt is unsafe")
    _unlink_regular_identity(
        receipt_path, expected_identity, "invalid signing transaction receipt"
    )


def _restore_durable_signing_transaction(source, stage, receipt_path):
    recovery = _load_durable_signing_transaction(source, stage, receipt_path)
    if recovery is None:
        return
    journal = load_json(recovery["journal"], "durable signing transaction journal")
    for item in journal["files"]:
        _restore_regular_transaction_item(item)
    _discard_durable_signing_transaction(source, stage)


def _toolchain_inventory(out):
    out = Path(out)
    files = []
    for path in sorted(out.rglob("toolchain.ninja")):
        if path.is_symlink() or not path.is_file():
            raise PipelineError("toolchain inventory contains an unsafe file")
        item = _regular_file_snapshot(path)
        item["relative_path"] = path.relative_to(out).as_posix()
        item.pop("path")
        files.append(item)
    if not files:
        raise PipelineError("toolchain inventory is empty")
    encoded = json.dumps(
        files, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "files": files,
        "count": len(files),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _validate_recorded_file_snapshot(value, expected_path, label):
    if not isinstance(value, dict) or set(value) != {
        "path",
        "bytes",
        "mtime_ns",
        "sha256",
    }:
        raise PipelineError("{} snapshot schema mismatch".format(label))
    if (
        value.get("path") != str(expected_path)
        or type(value.get("bytes")) is not int
        or value.get("bytes", -1) < 0
        or type(value.get("mtime_ns")) is not int
        or value.get("mtime_ns", 0) <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", value.get("sha256", ""))
    ):
        raise PipelineError("{} snapshot is invalid".format(label))


def _validate_recorded_toolchain_inventory(value):
    if not isinstance(value, dict) or set(value) != {"files", "count", "sha256"}:
        raise PipelineError("recorded toolchain inventory schema mismatch")
    files = value.get("files")
    if not isinstance(files, list) or not files or value.get("count") != len(files):
        raise PipelineError("recorded toolchain inventory is empty")
    relative_paths = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {
            "relative_path",
            "bytes",
            "mtime_ns",
            "sha256",
        }:
            raise PipelineError("recorded toolchain file schema mismatch")
        relative = item.get("relative_path")
        if (
            not isinstance(relative, str)
            or not relative.endswith("toolchain.ninja")
            or relative.startswith("/")
            or ".." in Path(relative).parts
            or type(item.get("bytes")) is not int
            or item.get("bytes", -1) < 0
            or type(item.get("mtime_ns")) is not int
            or item.get("mtime_ns", 0) <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))
        ):
            raise PipelineError("recorded toolchain file is invalid")
        relative_paths.append(relative)
    if relative_paths != sorted(set(relative_paths)):
        raise PipelineError("recorded toolchain paths are not unique and sorted")
    encoded = json.dumps(
        files, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if value.get("sha256") != hashlib.sha256(encoded).hexdigest():
        raise PipelineError("recorded toolchain inventory hash mismatch")


def _execution_identity_mapping(mapping):
    return {
        "logical": mapping["logical"],
        "physical": mapping["physical"],
        "device": mapping["identity"]["device"],
        "inode": mapping["identity"]["inode"],
    }


def _execution_evidence_path(path, alias_receipt, label):
    path = Path(path)
    if not path.is_absolute() or Path(os.path.abspath(str(path))) != path:
        raise PipelineError("{} path is not absolute and normalized".format(label))
    roots = (
        Path(alias_receipt["mappings"]["workspace"]["logical"]),
        Path(alias_receipt["mappings"]["workspace"]["physical"]),
    )
    for root in roots:
        try:
            _require_real_descendant(root, path, label)
            return path
        except PipelineError:
            pass
    raise PipelineError("{} is outside the recorded workspace".format(label))


def _physical_execution_path(logical_path, alias_receipt, label):
    """Map one logical workspace path through only the recorded home alias."""
    logical_path = Path(logical_path)
    logical_root = Path(alias_receipt["mappings"]["workspace"]["logical"])
    physical_root = Path(alias_receipt["mappings"]["workspace"]["physical"])
    try:
        relative = logical_path.relative_to(logical_root)
    except ValueError as exc:
        raise PipelineError("{} is outside the logical workspace".format(label)) from exc
    physical = physical_root / relative
    _require_real_descendant(physical_root, physical, label)
    return physical


def _linked_execution_evidence(link, alias_receipt, label):
    if not isinstance(link, dict) or set(link) != {"path", "sha256"}:
        raise PipelineError("{} link schema mismatch".format(label))
    path = _execution_evidence_path(link.get("path", ""), alias_receipt, label)
    if path.is_symlink() or not path.is_file():
        raise PipelineError("{} must be a regular file".format(label))
    observed = os.stat(str(path), follow_symlinks=False)
    if observed.st_uid != os.getuid() or stat.S_IMODE(observed.st_mode) & 0o222:
        raise PipelineError("{} ownership or mode is unsafe".format(label))
    if _volume_identity(path)["volume_uuid"] != alias_receipt["volume"][
        "volume_uuid"
    ]:
        raise PipelineError("{} volume changed".format(label))
    if link["sha256"] != sha256_file(path):
        raise PipelineError("{} hash changed".format(label))
    return path, load_json(path, label)


def _descriptor_bound_immutable_json(path, label):
    """Read/hash/parse one immutable JSON inode through a single descriptor."""
    path = Path(path)
    descriptor = None
    try:
        descriptor = os.open(
            str(path),
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & 0o222
            or before.st_size <= 0
            or before.st_size > MAX_RECEIPT_BYTES
        ):
            raise PipelineError("{} ownership, mode, or size is unsafe".format(label))
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, MAX_RECEIPT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_RECEIPT_BYTES:
                raise PipelineError("{} exceeded its byte bound".format(label))
        after = os.fstat(descriptor)
        if (
            _stat_identity_value(before) != _stat_identity_value(after)
            or total != after.st_size
        ):
            raise PipelineError("{} changed during descriptor-bound read".format(label))
        data = b"".join(chunks)
    except OSError as exc:
        raise PipelineError("cannot descriptor-read {}: {}".format(label, path)) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    current = os.stat(str(path), follow_symlinks=False)
    if _stat_identity_value(current) != _stat_identity_value(after):
        raise PipelineError("{} path no longer names the read inode".format(label))

    def object_without_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise PipelineError("duplicate {} key: {}".format(label, key))
            value[key] = item
        return value

    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=object_without_duplicates
        )
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise PipelineError("invalid {}: {}".format(label, path)) from exc
    if not isinstance(value, dict):
        raise PipelineError("{} root must be an object".format(label))
    return value, hashlib.sha256(data).hexdigest(), _stat_identity_value(after)


def _validate_live_process_observation(
    observation, initial_path, initial_hash, record, alias_receipt, ninja
):
    if (
        not isinstance(observation, dict)
        or observation.get("schema") != 1
        or observation.get("kind")
        != "focus-macos-alias-raw-ninja-live-process-chain-observation"
    ):
        raise PipelineError("resume live process observation schema mismatch")
    initial = observation.get("existing_execution_part")
    required_snapshot = {
        "path",
        "bytes",
        "mtime_ns",
        "sha256",
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
    }
    if not isinstance(initial, dict) or set(initial) != required_snapshot:
        raise PipelineError("observed initial execution record schema mismatch")
    current_initial = os.stat(str(initial_path), follow_symlinks=False)
    if (
        Path(initial.get("path", "")).resolve(strict=True)
        != initial_path.resolve(strict=True)
        or initial.get("sha256") != initial_hash
        or initial.get("bytes") != current_initial.st_size
        or initial.get("mtime_ns") != current_initial.st_mtime_ns
        or initial.get("inode") != current_initial.st_ino
        or initial.get("uid") != current_initial.st_uid
        or initial.get("gid") != current_initial.st_gid
        or initial.get("mode") & 0o022
        or stat.S_IMODE(current_initial.st_mode) & 0o222
        or type(initial.get("device")) is not int
        or initial.get("device", 0) <= 0
        or initial.get("mtime_ns", 0) > observation.get("observed_at_ns", 0)
    ):
        raise PipelineError("observed initial execution record changed")
    observed_at = observation.get("observed_at_ns")
    if (
        type(observed_at) is not int
        or observed_at < record["process"]["observed_live_at_ns"]
        or observed_at > record["completion"]["ended_at_ns"]
    ):
        raise PipelineError("resume live process observation time is invalid")
    group = observation.get("process_group")
    members = group.get("members") if isinstance(group, dict) else None
    if (
        not isinstance(members, list)
        or group.get("pgid") != record["process"]["pgid"]
    ):
        raise PipelineError("resume live process group schema mismatch")
    by_role = {}
    observed_pids = set()
    for member in members:
        if not isinstance(member, dict) or not isinstance(member.get("role"), str):
            raise PipelineError("resume live process member is invalid")
        role = member["role"]
        pid = member.get("pid")
        ppid = member.get("ppid")
        if (
            role in by_role
            or type(pid) is not int
            or pid <= 1
            or pid in observed_pids
            or type(ppid) is not int
            or ppid <= 1
        ):
            raise PipelineError("resume live process role or PID is invalid")
        if member.get("pgid") != group["pgid"]:
            raise PipelineError("resume live process escaped its process group")
        by_role[role] = member
        observed_pids.add(pid)
    expected_roles = {
        "pipeline_shell_group_leader",
        "autoninja_shell",
        "stdout_tee",
        "depot_python_launcher_shell",
        "autoninja_python",
        "pinned_ninja",
        "ninja_caffeinate",
    }
    if set(by_role) != expected_roles:
        raise PipelineError("resume live process roles mismatch")
    leader = by_role["pipeline_shell_group_leader"]
    autoninja_shell = by_role["autoninja_shell"]
    tee = by_role["stdout_tee"]
    launcher = by_role["depot_python_launcher_shell"]
    python = by_role["autoninja_python"]
    pinned = by_role["pinned_ninja"]
    caffeinate = by_role["ninja_caffeinate"]
    if (
        leader.get("pid") != group["pgid"]
        or leader.get("pid") != record["process"]["pid"]
        or leader.get("ppid") in observed_pids
        or leader.get("executable") != "/bin/zsh"
        or autoninja_shell.get("ppid") != leader.get("pid")
        or tee.get("ppid") != leader.get("pid")
        or launcher.get("ppid") != autoninja_shell.get("pid")
        or python.get("ppid") != launcher.get("pid")
        or pinned.get("ppid") != python.get("pid")
        or caffeinate.get("ppid") != pinned.get("pid")
    ):
        raise PipelineError("resume live process parent chain mismatch")
    logical_source = Path(record["logical"]["source"])
    physical_source = logical_source.resolve(strict=True)
    logical_out = Path(record["logical"]["out"])
    physical_out = logical_out.resolve(strict=True)
    expected_autoninja = Path(record["process"]["argv"][0])
    depot = logical_source.parent / "depot_tools"
    physical_depot = depot.resolve(strict=True)
    logical_autoninja_py = depot / "autoninja.py"
    physical_autoninja_py = logical_autoninja_py.resolve(strict=True)
    physical_python = (
        physical_depot / PACKAGING_PYTHON_RELDIR / "python3.11"
    ).resolve(strict=True)
    physical_ninja = Path(ninja["path"]).resolve(strict=True)
    physical_stdout = _physical_execution_path(
        Path(record["stdout_log"]["path"]),
        alias_receipt,
        "resume live stdout path",
    )
    declared_args = " ".join(record["process"]["argv"])
    environment = record["process"]["environment"]
    environment_order = (
        "HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        "DEVELOPER_DIR",
        "PATH",
        "DEPOT_TOOLS_UPDATE",
        "DEPOT_TOOLS_METRICS",
        "GCLIENT_FILE",
        "PYTHONDONTWRITEBYTECODE",
        "NINJA_SUMMARIZE_BUILD",
    )
    environment_command = " ".join(
        "{}={}".format(name, environment[name]) for name in environment_order
    )
    trailing_args = " ".join(record["process"]["argv"][1:])
    pinned_command = "{} -d stats {}".format(ninja["path"], trailing_args)
    expected_commands = {
        "pipeline_shell_group_leader": (
            "/bin/zsh -lc set -o pipefail\\012/usr/bin/env -i {} {} 2>&1 | "
            "/usr/bin/tee -a {}"
        ).format(environment_command, declared_args, physical_stdout),
        "autoninja_shell": "bash {} {}".format(
            expected_autoninja, trailing_args
        ),
        "stdout_tee": "/usr/bin/tee -a {}".format(physical_stdout),
        "depot_python_launcher_shell": "bash {} {} {}".format(
            depot / "python-bin" / "python3",
            logical_autoninja_py,
            trailing_args,
        ),
        "autoninja_python": "{} {} {}".format(
            depot
            / "python-bin"
            / ".."
            / PACKAGING_PYTHON_RELDIR
            / "python3",
            logical_autoninja_py,
            trailing_args,
        ),
        "pinned_ninja": pinned_command,
        "ninja_caffeinate": "caffeinate {}".format(pinned_command),
    }
    expected_executables = {
        "pipeline_shell_group_leader": Path("/bin/zsh"),
        "autoninja_shell": Path("/bin/bash"),
        "stdout_tee": Path("/usr/bin/tee"),
        "depot_python_launcher_shell": Path("/bin/bash"),
        "autoninja_python": physical_python,
        "pinned_ninja": physical_ninja,
        "ninja_caffeinate": Path("/usr/bin/caffeinate"),
    }
    for role, member in by_role.items():
        expected_cwd = physical_out if role == "pinned_ninja" else physical_source
        executable = expected_executables[role]
        if (
            member.get("ps_command") != expected_commands[role]
            or member.get("cwd_physical") != str(expected_cwd)
            or Path(member.get("executable", "")) != executable
            or member.get("executable_sha256") != sha256_file(executable)
            or not isinstance(member.get("started_at_local_second"), str)
            or not member.get("started_at_local_second")
        ):
            raise PipelineError("resume live {} identity mismatch".format(role))
    if (
        Path(autoninja_shell.get("script", "")).resolve(strict=True)
        != expected_autoninja.resolve(strict=True)
        or autoninja_shell.get("script_sha256") != sha256_file(expected_autoninja)
        or Path(pinned.get("executable", "")).resolve(strict=True)
        != Path(ninja["path"]).resolve(strict=True)
        or pinned.get("executable_sha256") != ninja["sha256"]
        or pinned.get("executable_bytes") != Path(ninja["path"]).stat().st_size
    ):
        raise PipelineError("resume live tool process identity mismatch")
    expected_environment = dict(record["process"]["environment"])
    expected_environment.pop("PATH")
    expected_environment["PWD"] = str(Path(record["logical"]["source"]).resolve())
    if (
        python.get("allowlisted_environment") != expected_environment
        or pinned.get("allowlisted_environment") != expected_environment
    ):
        raise PipelineError("resume live process environment mismatch")
    live_stdout = observation.get("stdout_log_live_snapshot")
    if not isinstance(live_stdout, dict) or set(live_stdout) != required_snapshot:
        raise PipelineError("resume live stdout observation schema mismatch")
    stdout_path = Path(record["stdout_log"]["path"])
    physical_stdout = _physical_execution_path(
        stdout_path, alias_receipt, "resume live stdout observation"
    )
    final_stdout_stat = os.stat(str(stdout_path), follow_symlinks=False)
    if (
        Path(live_stdout.get("path", "")) != physical_stdout
        or live_stdout.get("inode") != record["stdout_log"]["inode"]
        or live_stdout.get("uid") != os.getuid()
        or live_stdout.get("gid") != final_stdout_stat.st_gid
        or live_stdout.get("mode") & 0o022
        or not re.fullmatch(r"[0-9a-f]{64}", live_stdout.get("sha256", ""))
        or live_stdout.get("bytes", 0) <= 0
        or live_stdout.get("bytes", 0) > final_stdout_stat.st_size
        or live_stdout.get("mtime_ns", 0) < record["stdout_log"]["birth_time_ns"]
        or live_stdout.get("mtime_ns", 0) > observed_at
        or type(live_stdout.get("device")) is not int
        or live_stdout.get("device", 0) <= 0
    ):
        raise PipelineError("resume live stdout observation mismatch")
    if _sha256_file_prefix(
        stdout_path, live_stdout["bytes"]
    ) != live_stdout["sha256"]:
        raise PipelineError("resume live stdout is not a prefix of the final log")


def _validate_live_environment_supplement(
    supplement,
    observation_path,
    observation_hash,
    record,
    supplement_path,
    primary_observation,
):
    if (
        not isinstance(supplement, dict)
        or supplement.get("schema") != 1
        or supplement.get("kind")
        != "focus-macos-alias-raw-ninja-live-process-chain-observation-supplement"
    ):
        raise PipelineError("resume live environment supplement schema mismatch")
    primary = supplement.get("primary_observation")
    observation_stat = os.stat(str(observation_path), follow_symlinks=False)
    if (
        not isinstance(primary, dict)
        or set(primary) != {"path", "bytes", "inode", "sha256"}
        or Path(primary.get("path", "")).resolve(strict=True)
        != observation_path.resolve(strict=True)
        or primary.get("bytes") != observation_stat.st_size
        or primary.get("inode") != observation_stat.st_ino
        or primary.get("sha256") != observation_hash
    ):
        raise PipelineError("resume environment supplement observation link mismatch")
    observed_at = supplement.get("observed_at_ns")
    if (
        type(observed_at) is not int
        or observed_at < record["process"]["observed_live_at_ns"]
        or observed_at > record["completion"]["ended_at_ns"]
        or supplement_path.stat().st_size <= 0
    ):
        raise PipelineError("resume environment supplement time is invalid")
    processes = supplement.get("processes")
    if not isinstance(processes, list) or len(processes) != 2:
        raise PipelineError("resume environment supplement process schema mismatch")
    by_role = {item.get("role"): item for item in processes if isinstance(item, dict)}
    if set(by_role) != {"autoninja_python", "pinned_ninja"}:
        raise PipelineError("resume environment supplement roles mismatch")
    primary_roles = {
        item.get("role"): item
        for item in primary_observation.get("process_group", {}).get("members", ())
        if isinstance(item, dict)
    }
    if not {"depot_python_launcher_shell", "autoninja_python", "pinned_ninja"}.issubset(
        primary_roles
    ):
        raise PipelineError("resume environment supplement primary chain is missing")
    source = Path(record["logical"]["source"])
    python_bin = (
        source.parent
        / "depot_tools"
        / "python-bin"
        / ".."
        / PACKAGING_PYTHON_RELDIR
    )
    expected_path = os.pathsep.join(
        (
            str(python_bin),
            str(python_bin / "Scripts"),
            record["process"]["environment"]["PATH"],
        )
    )
    expected_pwd = str(source.resolve(strict=True))
    for role, expected_parent in (
        (
            "autoninja_python",
            primary_roles["depot_python_launcher_shell"].get("pid"),
        ),
        ("pinned_ninja", primary_roles["autoninja_python"].get("pid")),
    ):
        item = by_role[role]
        primary_item = primary_roles[role]
        if (
            item.get("pid") != primary_item.get("pid")
            or item.get("pgid") != record["process"]["pgid"]
            or item.get("ppid") != expected_parent
            or item.get("PATH") != expected_path
            or item.get("PWD") != expected_pwd
        ):
            raise PipelineError("resume live child environment mismatch")


def _validate_live_process_revalidation(
    revalidation,
    initial_path,
    observation_path,
    supplement_path,
    record,
    alias_receipt,
    primary_observation,
    environment_supplement,
):
    if (
        not isinstance(revalidation, dict)
        or revalidation.get("schema") != 1
        or revalidation.get("kind")
        != "focus-macos-alias-raw-ninja-live-process-chain-revalidation"
    ):
        raise PipelineError("resume process revalidation schema mismatch")
    started = revalidation.get("capture_started_at_ns")
    finished = revalidation.get("capture_finished_at_ns")
    if (
        type(started) is not int
        or type(finished) is not int
        or started < primary_observation.get("observed_at_ns", 0)
        or started < environment_supplement.get("observed_at_ns", 0)
        or finished < started
        or finished > record["completion"]["ended_at_ns"]
    ):
        raise PipelineError("resume process revalidation time is invalid")
    if revalidation.get("volume", {}).get("uuid") != alias_receipt["volume"][
        "volume_uuid"
    ]:
        raise PipelineError("resume process revalidation volume mismatch")
    start_check = revalidation.get("claimed_start_time_check")
    if (
        not isinstance(start_check, dict)
        or start_check.get("matches") is not True
        or start_check.get("allowed_tolerance_ns") != 1_000_000_000
        or start_check.get("initial_record_process_started_at_ns")
        != record["process"]["started_at_ns"]
        or abs(
            start_check.get("group_leader_ps_second_start_ns", 0)
            - record["process"]["started_at_ns"]
        )
        > start_check.get("allowed_tolerance_ns", -1)
    ):
        raise PipelineError("resume process start-time revalidation mismatch")
    links = revalidation.get("linked_evidence")
    expected_links = {
        "initial_record": (initial_path, record["initial_record"]["sha256"]),
        "primary_observation": (
            observation_path,
            record["live_process_observation"]["sha256"],
        ),
        "environment_supplement": (
            supplement_path,
            record["live_process_environment_supplement"]["sha256"],
        ),
    }
    if not isinstance(links, dict) or set(links) != set(expected_links):
        raise PipelineError("resume process revalidation evidence links mismatch")
    for name, (path, digest) in expected_links.items():
        link = links[name]
        observed = os.stat(str(path), follow_symlinks=False)
        if (
            not isinstance(link, dict)
            or Path(link.get("path", "")).resolve(strict=True)
            != path.resolve(strict=True)
            or link.get("bytes") != observed.st_size
            or link.get("inode") != observed.st_ino
            or link.get("sha256") != digest
        ):
            raise PipelineError(
                "resume process revalidation {} link mismatch".format(name)
            )
    initial_link = links["initial_record"]
    initial_stat = os.stat(str(initial_path), follow_symlinks=False)
    initial_birth_ns = int(
        getattr(initial_stat, "st_birthtime", initial_stat.st_ctime)
        * 1_000_000_000
    )
    if (
        set(initial_link)
        != {"path", "birth_time_ns", "bytes", "inode", "mode", "sha256"}
        or initial_link.get("birth_time_ns") != initial_birth_ns
        or initial_link.get("mode") != stat.S_IMODE(initial_stat.st_mode)
        or initial_birth_ns < record["process"]["started_at_ns"]
        or initial_birth_ns > primary_observation.get("observed_at_ns", 0)
        or initial_stat.st_mtime_ns > primary_observation.get("observed_at_ns", 0)
    ):
        raise PipelineError("resume process initial evidence timing mismatch")
    primary_members = {
        item["role"]: {
            key: item[key]
            for key in ("role", "pid", "ppid", "pgid", "ps_command", "started_at_local_second")
        }
        for item in primary_observation["process_group"]["members"]
    }
    spine = revalidation.get("stable_spine")
    if (
        not isinstance(spine, list)
        or {item.get("role") for item in spine if isinstance(item, dict)}
        != set(primary_members)
    ):
        raise PipelineError("resume process stable spine schema mismatch")
    if {item["role"]: item for item in spine} != primary_members:
        raise PipelineError("resume process stable spine changed")
    scripts = revalidation.get("script_identities")
    expected_scripts = {
        str(Path(record["process"]["argv"][0]).resolve(strict=True)),
        str(
            (
                Path(record["logical"]["source"]).parent
                / "depot_tools"
                / "autoninja.py"
            ).resolve(strict=True)
        ),
    }
    if not isinstance(scripts, list) or {
        str(Path(item.get("path", "")).resolve(strict=True)) for item in scripts
    } != expected_scripts:
        raise PipelineError("resume process script identities mismatch")
    for item in scripts:
        path = Path(item["path"])
        observed = os.stat(str(path), follow_symlinks=False)
        if (
            item.get("bytes") != observed.st_size
            or item.get("inode") != observed.st_ino
            or item.get("uid") != observed.st_uid
            or item.get("gid") != observed.st_gid
            or item.get("mode") != stat.S_IMODE(observed.st_mode)
            or item.get("sha256") != sha256_file(path)
            or type(item.get("device_at_capture")) is not int
        ):
            raise PipelineError("resume process script identity changed")
    stdout = revalidation.get("stdout_log_identity")
    logical_stdout = Path(record["stdout_log"]["path"])
    physical_stdout = _physical_execution_path(
        logical_stdout, alias_receipt, "resume stdout physical path"
    )
    if (
        not isinstance(stdout, dict)
        or Path(stdout.get("logical_path", "")) != logical_stdout
        or Path(stdout.get("physical_path", "")) != physical_stdout
        or physical_stdout.resolve(strict=True) != logical_stdout.resolve(strict=True)
        or stdout.get("inode") != record["stdout_log"]["inode"]
        or stdout.get("birth_time_ns") != record["stdout_log"]["birth_time_ns"]
        or stdout.get("bytes_at_capture_finish", 0) <= 0
        or stdout.get("bytes_at_capture_finish", 0)
        < primary_observation.get("stdout_log_live_snapshot", {}).get("bytes", 0)
        or stdout.get("bytes_at_capture_finish", 0)
        > record["completion"]["stdout_log"].get("bytes", 0)
    ):
        raise PipelineError("resume process stdout revalidation mismatch")


def _resume_execution_initial_basename(record_path, architecture):
    name = Path(record_path).name
    if architecture == "arm64":
        pattern = r"build-arm64-resume3-[A-Za-z0-9][A-Za-z0-9._-]*\.execution\.json"
    elif architecture == "x64":
        pattern = r"build-x64-resume[1-9][0-9]*-[A-Za-z0-9][A-Za-z0-9._-]*\.execution\.json"
    else:
        raise PipelineError("unsupported resume execution architecture")
    if re.fullmatch(pattern, name) is None:
        raise PipelineError(
            "resume execution record basename is not the authorized fresh run"
        )
    return name + ".part"


def _resume3_exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise PipelineError("{} schema mismatch".format(label))
    return value


def _resume3_exact_link(record_path, link, alias_receipt, suffix, label):
    if not isinstance(link, dict) or set(link) != {"path", "sha256"}:
        raise PipelineError("{} link schema mismatch".format(label))
    if not isinstance(link["sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", link["sha256"]
    ):
        raise PipelineError("{} link hash is invalid".format(label))
    path = _execution_evidence_path(link["path"], alias_receipt, label)
    stem = record_path.name[: -len(".execution.json")]
    expected = record_path.with_name(stem + suffix)
    if Path(link["path"]) != expected or path != expected:
        raise PipelineError("{} is not the exact run sibling".format(label))
    if _volume_identity(path)["volume_uuid"] != alias_receipt["volume"][
        "volume_uuid"
    ]:
        raise PipelineError("{} volume changed".format(label))
    value, observed_hash, _identity = _descriptor_bound_immutable_json(path, label)
    if observed_hash != link["sha256"]:
        raise PipelineError("{} hash changed".format(label))
    return path, value


def _resume3_snapshot_contract(value, label):
    keys = {
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
        "bytes",
        "mtime_ns",
        "ctime_ns",
        "birth_time_ns",
        "path",
        "sha256",
    }
    _resume3_exact_keys(value, keys, label)
    integer_keys = keys - {"path", "sha256"}
    if (
        any(type(value[key]) is not int for key in integer_keys)
        or any(value[key] < 0 for key in integer_keys)
        or not isinstance(value["path"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
    ):
        raise PipelineError("{} values are invalid".format(label))
    return value


def _resume3_member_contract(member, role, expected_pgid, label):
    keys = {
        "role",
        "pid",
        "ppid",
        "pgid",
        "ps_command",
        "started_at_ns",
        "cwd_physical",
        "executable",
        "executable_bytes",
        "executable_inode",
        "executable_sha256",
    }
    _resume3_exact_keys(member, keys, label)
    if (
        member["role"] != role
        or any(
            type(member[name]) is not int
            for name in (
                "pid",
                "ppid",
                "pgid",
                "started_at_ns",
                "executable_bytes",
                "executable_inode",
            )
        )
        or member["pid"] <= 1
        or member["ppid"] <= 1
        or member["pgid"] != expected_pgid
        or member["started_at_ns"] <= 0
        or member["executable_bytes"] <= 0
        or member["executable_inode"] <= 0
        or not isinstance(member["ps_command"], str)
        or not member["ps_command"]
        or not isinstance(member["cwd_physical"], str)
        or not Path(member["cwd_physical"]).is_absolute()
        or not isinstance(member["executable"], str)
        or not Path(member["executable"]).is_absolute()
        or not re.fullmatch(r"[0-9a-f]{64}", member["executable_sha256"])
    ):
        raise PipelineError("{} values are invalid".format(label))
    executable = Path(member["executable"])
    observed = os.stat(str(executable), follow_symlinks=False)
    if (
        not stat.S_ISREG(observed.st_mode)
        or member["executable_bytes"] != observed.st_size
        or member["executable_inode"] != observed.st_ino
        or member["executable_sha256"] != sha256_file(executable)
    ):
        raise PipelineError("{} executable identity changed".format(label))
    return member


def _resume3_process_group_contract(
    primary, record, source, out, ninja, physical_stdout
):
    group = primary.get("process_group")
    _resume3_exact_keys(group, {"pgid", "members", "dynamic_descendants"}, "resume3 process group")
    if type(group["pgid"]) is not int or group["pgid"] != record["process"]["pgid"]:
        raise PipelineError("resume3 process group PGID mismatch")
    expected_roles = (
        "pipeline_shell_group_leader",
        "autoninja_shell",
        "stdout_tee",
        "depot_python_launcher_shell",
        "autoninja_python",
        "pinned_ninja",
        "ninja_caffeinate",
    )
    members = group["members"]
    dynamic = group["dynamic_descendants"]
    if not isinstance(members, list) or not isinstance(dynamic, list):
        raise PipelineError("resume3 process group members are not lists")
    by_role = {}
    by_pid = {}
    for index, member in enumerate(members):
        if not isinstance(member, dict) or member.get("role") not in expected_roles:
            raise PipelineError("resume3 stable process role is invalid")
        role = member["role"]
        if role in by_role:
            raise PipelineError("resume3 stable process role is duplicated")
        _resume3_member_contract(
            member, role, group["pgid"], "resume3 stable member {}".format(index)
        )
        if member["pid"] in by_pid:
            raise PipelineError("resume3 process PID is duplicated")
        by_role[role] = member
        by_pid[member["pid"]] = member
    if set(by_role) != set(expected_roles):
        raise PipelineError("resume3 stable process roles mismatch")
    for index, member in enumerate(dynamic):
        _resume3_member_contract(
            member,
            "dynamic_descendant",
            group["pgid"],
            "resume3 dynamic descendant {}".format(index),
        )
        if member["pid"] in by_pid:
            raise PipelineError("resume3 dynamic process PID is duplicated")
        by_pid[member["pid"]] = member
    leader = by_role["pipeline_shell_group_leader"]
    shell = by_role["autoninja_shell"]
    tee = by_role["stdout_tee"]
    launcher = by_role["depot_python_launcher_shell"]
    python = by_role["autoninja_python"]
    pinned = by_role["pinned_ninja"]
    caffeinate = by_role["ninja_caffeinate"]
    if (
        leader["pid"] != group["pgid"]
        or shell["ppid"] != leader["pid"]
        or tee["ppid"] != leader["pid"]
        or launcher["ppid"] != shell["pid"]
        or python["ppid"] != launcher["pid"]
        or pinned["ppid"] != python["pid"]
        or caffeinate["ppid"] != pinned["pid"]
    ):
        raise PipelineError("resume3 stable process ancestry mismatch")
    expected_executables = {
        "pipeline_shell_group_leader": Path("/bin/zsh"),
        "autoninja_shell": Path("/bin/bash"),
        "stdout_tee": Path("/usr/bin/tee"),
        "depot_python_launcher_shell": Path("/bin/bash"),
        "autoninja_python": (
            Path(source).resolve(strict=True).parent
            / "depot_tools"
            / PACKAGING_PYTHON_RELDIR
            / "python3.11"
        ).resolve(strict=True),
        "pinned_ninja": Path(ninja["path"]).resolve(strict=True),
        "ninja_caffeinate": Path("/usr/bin/caffeinate"),
    }
    physical_source = Path(source).resolve(strict=True)
    physical_out = Path(out).resolve(strict=True)
    for role, member in by_role.items():
        expected_cwd = physical_out if role == "pinned_ninja" else physical_source
        if (
            Path(member["executable"]).resolve(strict=True)
            != expected_executables[role].resolve(strict=True)
            or Path(member["cwd_physical"]).resolve(strict=True) != expected_cwd
        ):
            raise PipelineError("resume3 {} path identity mismatch".format(role))
    leader_command = leader["ps_command"]
    if (
        "-f -c" not in leader_command
        or "set -o pipefail" not in leader_command
        or str(record["process"]["argv"][0]) not in leader_command
        or " -j8 " not in " " + leader_command + " "
        or str(physical_stdout) not in leader_command
        or "gn gen" in leader_command
        or "http://" in leader_command
        or "https://" in leader_command
    ):
        raise PipelineError("resume3 no-rc pipeline leader command mismatch")
    if str(record["process"]["argv"][0]) not in shell["ps_command"]:
        raise PipelineError("resume3 autoninja shell command mismatch")
    if str(ninja["path"]) not in pinned["ps_command"]:
        raise PipelineError("resume3 pinned Ninja command mismatch")
    for member in dynamic:
        cursor = member
        visited = set()
        while cursor["pid"] != pinned["pid"]:
            if cursor["pid"] in visited:
                raise PipelineError("resume3 dynamic ancestry contains a cycle")
            visited.add(cursor["pid"])
            cursor = by_pid.get(cursor["ppid"])
            if cursor is None:
                raise PipelineError("resume3 dynamic ancestry does not reach Ninja")
    return by_role


def _resume3_monitor_contract(monitor, source, logs):
    keys = {
        "checks",
        "minimum_free_bytes",
        "last_free_bytes",
        "maximum_stdout_bytes",
        "hard_floor_bytes",
        "stdout_limit_bytes",
        "poll_interval_ms",
        "source_path",
        "logs_path",
        "process_group_absent",
        "memory",
    }
    _resume3_exact_keys(monitor, keys, "resume3 runtime monitor")
    hard_floor = HARD_FLOOR_GIB * GIB
    if (
        type(monitor["checks"]) is not int
        or monitor["checks"] < 2
        or type(monitor["maximum_stdout_bytes"]) is not int
        or not 0 <= monitor["maximum_stdout_bytes"] <= MAX_RESUME_STDOUT_BYTES
        or type(monitor["poll_interval_ms"]) is not int
        or monitor["poll_interval_ms"] <= 0
        or monitor["hard_floor_bytes"] != hard_floor
        or monitor["stdout_limit_bytes"] != MAX_RESUME_STDOUT_BYTES
        or monitor["source_path"] != str(Path(source).resolve(strict=True))
        or monitor["logs_path"] != str(Path(logs).resolve(strict=True))
        or monitor["process_group_absent"] is not True
    ):
        raise PipelineError("resume3 runtime monitor success proof mismatch")
    for name in ("minimum_free_bytes", "last_free_bytes"):
        values = monitor[name]
        _resume3_exact_keys(values, {"source", "logs"}, "resume3 {}".format(name))
        if any(type(value) is not int or value < hard_floor for value in values.values()):
            raise PipelineError("resume3 free-space proof crossed the hard floor")
    for name in ("source", "logs"):
        if monitor["minimum_free_bytes"][name] > monitor["last_free_bytes"][name]:
            raise PipelineError("resume3 free-space aggregate is inconsistent")
    memory = monitor["memory"]
    memory_keys = {
        "samples",
        "minimum_free_percent",
        "maximum_swap_used_bytes",
        "maximum_swap_total_bytes",
        "last",
        "critical_free_consecutive",
        "critical_swap_consecutive",
        "maximum_critical_free_consecutive",
        "maximum_critical_swap_consecutive",
        "probe_every_checks",
        "thresholds",
    }
    _resume3_exact_keys(memory, memory_keys, "resume3 memory monitor")
    thresholds = {
        "immediate_free_percent": 5,
        "sustained_free_percent": 10,
        "sustained_free_samples": 3,
        "swap_free_percent": 15,
        "swap_used_bytes": 8 * GIB,
        "swap_sustained_samples": 2,
    }
    if not _strict_json_identity(memory["thresholds"], thresholds):
        raise PipelineError("resume3 memory thresholds mismatch")
    integer_names = (
        "samples",
        "minimum_free_percent",
        "maximum_swap_used_bytes",
        "maximum_swap_total_bytes",
        "critical_free_consecutive",
        "critical_swap_consecutive",
        "maximum_critical_free_consecutive",
        "maximum_critical_swap_consecutive",
        "probe_every_checks",
    )
    if (
        any(type(memory[name]) is not int or memory[name] < 0 for name in integer_names)
        or memory["samples"] < 2
        or memory["samples"] > monitor["checks"]
        or memory["probe_every_checks"] != 5
        or not 0 <= memory["minimum_free_percent"] <= 100
        or memory["minimum_free_percent"] <= thresholds["immediate_free_percent"]
        or memory["maximum_critical_free_consecutive"]
        >= thresholds["sustained_free_samples"]
        or memory["maximum_critical_swap_consecutive"]
        >= thresholds["swap_sustained_samples"]
        or memory["maximum_critical_free_consecutive"]
        < memory["critical_free_consecutive"]
        or memory["maximum_critical_swap_consecutive"]
        < memory["critical_swap_consecutive"]
    ):
        raise PipelineError("resume3 memory aggregate proof mismatch")
    last = memory["last"]
    last_keys = {
        "memory_total_bytes",
        "free_percent",
        "swap_total_bytes",
        "swap_used_bytes",
        "swap_free_bytes",
    }
    _resume3_exact_keys(last, last_keys, "resume3 last memory sample")
    if (
        any(type(last[name]) is not int for name in last_keys)
        or last["memory_total_bytes"] <= 0
        or not 0 <= last["free_percent"] <= 100
        or min(last["swap_total_bytes"], last["swap_used_bytes"], last["swap_free_bytes"])
        < 0
        or memory["minimum_free_percent"] > last["free_percent"]
        or memory["maximum_swap_used_bytes"] < last["swap_used_bytes"]
        or memory["maximum_swap_total_bytes"] < last["swap_total_bytes"]
        or abs(
            last["swap_total_bytes"]
            - last["swap_used_bytes"]
            - last["swap_free_bytes"]
        )
        > 2 * 1024 ** 2
    ):
        raise PipelineError("resume3 last memory sample is inconsistent")
    return monitor


def _fresh_x64_resume_preparation_binding(
    source, developer_dir, out, supplied, pre_run
):
    """Bind a completed x64 run to the exact pre-Ninja fresh-GN receipt."""
    if not isinstance(supplied, dict) or set(supplied) != {
        "receipt",
        "contract_sha256",
    }:
        raise PipelineError("resume3 fresh x86_64 preparation link mismatch")
    link = supplied.get("receipt")
    if not isinstance(link, dict) or set(link) != {"path", "bytes", "sha256"}:
        raise PipelineError("resume3 fresh x86_64 receipt link mismatch")
    receipt_path = in_source(
        source,
        FRESH_X64_PREPARATION_RECEIPT,
        "fresh x86_64 preparation receipt",
        must_exist=True,
    )
    before = _regular_file_snapshot(receipt_path)
    observed = os.stat(str(receipt_path), follow_symlinks=False)
    if (
        observed.st_uid != os.getuid()
        or stat.S_IMODE(observed.st_mode) & 0o022
        or link
        != {
            "path": str(receipt_path),
            "bytes": before["bytes"],
            "sha256": before["sha256"],
        }
    ):
        raise PipelineError("resume3 fresh x86_64 receipt identity changed")
    receipt = load_json(receipt_path, "fresh x86_64 preparation receipt")
    after = _regular_file_snapshot(receipt_path)
    if before != after:
        raise PipelineError("resume3 fresh x86_64 receipt changed while reading")
    try:
        # This is deliberately byte-for-byte the canonical encoding used by
        # alias_resume_runner._canonical_bytes.  The runner records this hash
        # before Ninja starts, so validating a different (compact) JSON
        # serialization would reject honest completed runs.
        canonical = (
            json.dumps(
                receipt,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PipelineError("resume3 fresh x86_64 receipt is not canonical JSON") from exc
    canonical_sha256 = hashlib.sha256(canonical).hexdigest()
    if (
        supplied.get("contract_sha256") != canonical_sha256
        or before["bytes"] != len(canonical)
        or before["sha256"] != canonical_sha256
    ):
        raise PipelineError("resume3 fresh x86_64 contract hash changed")
    expected_keys = {
        "schema", "stage", "source_root", "developer_dir", "legacy_root",
        "legacy_out", "legacy_inventory", "prepared_evidence", "fresh_out",
        "fresh_out_identity", "fresh_profile", "generated_graph", "gn_command",
        "acquisition_receipt", "tool_receipt", "preparation_receipt",
        "reclaimed_arm_onboarding", "xcode27_compatibility_receipt_sha256",
        "xcode27_seatbelt_compatibility_receipt_sha256",
        "screen_ai_disabled_compatibility_receipt_sha256",
        "xcode27_linkedit_strip_compatibility_receipt_sha256",
        "linkedit_strip_tools", "legacy_preserved", "legacy_deleted",
        "gn_gen_executed", "gn_gen_succeeded", "ninja_executed",
        "build_executed", "signing_executed", "packaging_executed",
        "offline", "network_operations",
    }
    if set(receipt) != expected_keys or (
        receipt.get("schema") != 1
        or receipt.get("stage") != "prepare-fresh-x64"
        or receipt.get("source_root") != str(source)
        or receipt.get("developer_dir") != str(developer_dir)
        or receipt.get("fresh_out") != str(out)
        or receipt.get("legacy_preserved") is not True
        or receipt.get("legacy_deleted") is not False
        or receipt.get("gn_gen_executed") is not True
        or receipt.get("gn_gen_succeeded") is not True
        or receipt.get("ninja_executed") is not False
        or receipt.get("build_executed") is not False
        or receipt.get("signing_executed") is not False
        or receipt.get("packaging_executed") is not False
        or receipt.get("offline") is not True
        or receipt.get("network_operations") != 0
        or pre_run.get("ninja_log") is not None
        or pre_run.get("ninja_deps") is not None
    ):
        raise PipelineError("resume3 fresh x86_64 receipt contract mismatch")
    if _fresh_x64_directory_identity(out, "fresh x86_64 resumed output") != receipt.get(
        "fresh_out_identity"
    ):
        raise PipelineError("resume3 fresh x86_64 output identity changed")
    _verify_legacy_x64_inventory(
        Path(receipt["legacy_out"]), receipt["legacy_inventory"]
    )
    graph = _fresh_x64_generated_graph_contract(
        out, receipt.get("linkedit_strip_tools")
    )
    if graph != receipt.get("generated_graph"):
        raise PipelineError("resume3 fresh x86_64 graph changed after preparation")
    build_snapshot = pre_run.get("build_ninja")
    toolchain_inventory = pre_run.get("toolchain_inventory")
    _validate_recorded_file_snapshot(
        build_snapshot, Path(out) / "build.ninja", "resume3 fresh x86_64 build graph"
    )
    _validate_recorded_toolchain_inventory(toolchain_inventory)
    recorded_toolchains = [
        {
            "path": item["relative_path"],
            "bytes": item["bytes"],
            "sha256": item["sha256"],
        }
        for item in toolchain_inventory["files"]
    ]
    if (
        {key: build_snapshot[key] for key in ("bytes", "sha256")}
        != graph["build_ninja"]
        or recorded_toolchains != graph["toolchains"]
    ):
        raise PipelineError("resume3 fresh x86_64 pre-run graph is not receipt-bound")
    return {"receipt": receipt, "graph": graph, "link": supplied}


def _resume3_ninja_history_transition_contract(
    pre_run, post, architecture, process_started_at_ns
):
    """Validate ARM incremental history or fresh-x64 absent-to-created history."""
    if (
        not _strict_json_identity(pre_run["build_ninja"], post["build_ninja"])
        or not _strict_json_identity(
            pre_run["toolchain_inventory"], post["toolchain_inventory"]
        )
    ):
        raise PipelineError("resume3 Ninja graph changed during execution")
    if architecture == "x64":
        if pre_run["ninja_log"] is not None or pre_run["ninja_deps"] is not None:
            raise PipelineError("resume3 fresh x86_64 history was not absent")
        for name in ("ninja_log", "ninja_deps"):
            current = post[name]
            if (
                not isinstance(current, dict)
                or current.get("bytes", 0) <= 0
                or current.get("mtime_ns", 0) < process_started_at_ns
            ):
                raise PipelineError(
                    "resume3 fresh x86_64 {} was not created by Ninja".format(name)
                )
        return True
    if architecture != "arm64" or (
        pre_run["ninja_log"]["sha256"] == post["ninja_log"]["sha256"]
        or post["ninja_log"]["mtime_ns"] <= pre_run["ninja_log"]["mtime_ns"]
        or post["ninja_deps"]["mtime_ns"] < pre_run["ninja_deps"]["mtime_ns"]
        or (
            pre_run["ninja_deps"]["sha256"] == post["ninja_deps"]["sha256"]
            and not _strict_json_identity(
                pre_run["ninja_deps"], post["ninja_deps"]
            )
        )
    ):
        raise PipelineError("resume3 Ninja history transition mismatch")
    return True


def _resume3_execution_record_contract(
    record_path,
    record,
    record_sha256,
    alias_receipt,
    source,
    developer_dir,
    architecture,
    out,
    ninja,
    allow_history_growth=False,
    authorized_history=None,
):
    root_keys = {
        "schema",
        "kind",
        "architecture",
        "logical",
        "process",
        "identity",
        "pre_run",
        "stdout_log",
        "completion",
        "pre_launch",
        "exit_status",
        "live_process_observation",
        "live_process_environment_supplement",
        "live_process_revalidation",
        "runner",
    }
    if architecture == "x64":
        root_keys.add("fresh_x64_preparation")
    _resume3_exact_keys(record, root_keys, "resume3 execution record")
    if (
        type(record["schema"]) is not int
        or record["schema"] != 3
        or record["kind"] != "focus-macos-alias-raw-ninja-execution"
        or record["architecture"] != architecture
    ):
        raise PipelineError("resume3 execution identity mismatch")
    stem = record_path.name[: -len(".execution.json")]
    run_pattern = {
        "arm64": r"build-arm64-resume3-[A-Za-z0-9][A-Za-z0-9._-]*",
        "x64": r"build-x64-resume3-[A-Za-z0-9][A-Za-z0-9._-]*",
    }.get(architecture)
    if run_pattern is None or not re.fullmatch(run_pattern, stem):
        raise PipelineError("resume3 run identifier mismatch")
    expected_stdout_logical = record_path.with_name(stem + ".log")
    expected_stdout_physical = _physical_execution_path(
        expected_stdout_logical, alias_receipt, "resume3 stdout"
    )
    pre_path, pre = _resume3_exact_link(
        record_path,
        record["pre_launch"],
        alias_receipt,
        ".pre-launch.json",
        "resume3 pre-launch evidence",
    )
    primary_path, primary = _resume3_exact_link(
        record_path,
        record["live_process_observation"],
        alias_receipt,
        ".live-process-observation.json",
        "resume3 primary observation",
    )
    supplement_path, supplement = _resume3_exact_link(
        record_path,
        record["live_process_environment_supplement"],
        alias_receipt,
        ".live-environment-supplement.json",
        "resume3 environment supplement",
    )
    revalidation_path, revalidation = _resume3_exact_link(
        record_path,
        record["live_process_revalidation"],
        alias_receipt,
        ".live-process-revalidation.json",
        "resume3 process revalidation",
    )
    status_path, status = _resume3_exact_link(
        record_path,
        record["exit_status"],
        alias_receipt,
        ".exit-status.json",
        "resume3 exit status",
    )
    logical_workspace = Path(alias_receipt["mappings"]["workspace"]["logical"])
    expected_logical = {
        "home": alias_receipt["logical_home"],
        "workspace": str(logical_workspace),
        "source": str(source),
        "developer_dir": str(developer_dir),
        "out": str(out),
    }
    expected_argv = [
        str(Path(source).parent / "depot_tools" / "autoninja"),
        "-j{}".format(BUILD_JOBS),
        "-C",
        str(Path(out).relative_to(source)),
        "chrome",
        "chrome/installer/mac:copies",
    ]
    expected_environment = {
        "HOME": alias_receipt["logical_home"],
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "DEVELOPER_DIR": str(developer_dir),
        "PATH": os.pathsep.join(
            (
                str(Path(source).parent / "depot_tools"),
                str(Path(ninja["path"]).parent),
                SYSTEM_PATH,
            )
        ),
        "DEPOT_TOOLS_UPDATE": "0",
        "DEPOT_TOOLS_METRICS": "0",
        "GCLIENT_FILE": str(Path(source).parent / ".gclient"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NINJA_SUMMARIZE_BUILD": "1",
    }
    environment_order = (
        "HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        "DEVELOPER_DIR",
        "PATH",
        "DEPOT_TOOLS_UPDATE",
        "DEPOT_TOOLS_METRICS",
        "GCLIENT_FILE",
        "PYTHONDONTWRITEBYTECODE",
        "NINJA_SUMMARIZE_BUILD",
    )
    environment_tokens = [
        "{}={}".format(name, shlex.quote(expected_environment[name]))
        for name in environment_order
    ]
    expected_command = " ".join(shlex.quote(item) for item in expected_argv)
    expected_shell_script = (
        "set -o pipefail\n/usr/bin/env -i {} {} 2>&1 | /usr/bin/tee -a {}"
    ).format(
        " ".join(environment_tokens),
        expected_command,
        shlex.quote(str(expected_stdout_physical)),
    )
    process = record["process"]
    _resume3_exact_keys(
        process,
        {"pid", "pgid", "started_at_ns", "observed_live_at_ns", "cwd", "argv", "environment"},
        "resume3 process",
    )
    if (
        type(process["pid"]) is not int
        or process["pid"] <= 1
        or process["pgid"] != process["pid"]
        or type(process["started_at_ns"]) is not int
        or type(process["observed_live_at_ns"]) is not int
        or process["started_at_ns"] <= 0
        or process["observed_live_at_ns"] < process["started_at_ns"]
        or process["cwd"] != str(source)
        or not _strict_json_identity(process["argv"], expected_argv)
        or not _strict_json_identity(process["environment"], expected_environment)
        or not _strict_json_identity(record["logical"], expected_logical)
    ):
        raise PipelineError("resume3 process provenance mismatch")
    alias_identity = dict(alias_receipt["alias"])
    alias_identity.pop("root_owned", None)
    alias_identity.pop("absolute_exact_target", None)
    alias_identity.pop("target_identity", None)
    expected_identity = {
        "alias": alias_identity,
        "source": _execution_identity_mapping(alias_receipt["mappings"]["source"]),
        "developer": _execution_identity_mapping(alias_receipt["mappings"]["developer"]),
    }
    if not _strict_json_identity(record["identity"], expected_identity):
        raise PipelineError("resume3 inode identity mismatch")
    pre_keys = {
        "schema",
        "kind",
        "run_id",
        "created_at_ns",
        "architecture",
        "logical",
        "planned_process",
        "identity",
        "pre_run",
        "stdout_log",
        "runner",
        "policy",
    }
    if architecture == "x64":
        pre_keys.add("fresh_x64_preparation")
    _resume3_exact_keys(pre, pre_keys, "resume3 pre-launch")
    planned = pre["planned_process"]
    _resume3_exact_keys(
        planned,
        {"cwd", "argv", "environment", "shell_argv", "start_new_session", "jobs"},
        "resume3 planned process",
    )
    expected_shell = ["/bin/zsh", "-f", "-c", expected_shell_script]
    shell_argv = planned["shell_argv"]
    if (
        type(pre["schema"]) is not int
        or pre["schema"] != 1
        or pre["kind"] != "focus-macos-alias-resume3-pre-launch"
        or pre["run_id"] != stem
        or type(pre["created_at_ns"]) is not int
        or pre["created_at_ns"] <= 0
        or pre["architecture"] != architecture
        or not _strict_json_identity(pre["logical"], expected_logical)
        or planned["cwd"] != process["cwd"]
        or not _strict_json_identity(planned["argv"], process["argv"])
        or not _strict_json_identity(planned["environment"], process["environment"])
        or not isinstance(shell_argv, list)
        or not _strict_json_identity(shell_argv, expected_shell)
        or planned["start_new_session"] is not True
        or type(planned["jobs"]) is not int
        or planned["jobs"] != BUILD_JOBS
        or not _strict_json_identity(pre["identity"], record["identity"])
        or not _strict_json_identity(pre["pre_run"], record["pre_run"])
        or (
            architecture == "x64"
            and not _strict_json_identity(
                pre["fresh_x64_preparation"],
                record["fresh_x64_preparation"],
            )
        )
    ):
        raise PipelineError("resume3 pre-launch provenance mismatch")
    policy = pre["policy"]
    if not _strict_json_identity(
        policy,
        {
            "explicit_gn_gen_command": False,
            "network_operations": 0,
            "single_run": True,
            "final_success_requires_popen_wait_zero": True,
        },
    ):
        raise PipelineError("resume3 pre-launch policy mismatch")
    runner_identity = pre["runner"]
    _resume3_exact_keys(runner_identity, {"path", "bytes", "sha256"}, "resume3 runner identity")
    expected_runner = (MACOS_DIR / "alias_resume_runner.py").resolve(strict=True)
    if (
        Path(runner_identity["path"]).resolve(strict=True) != expected_runner
        or type(runner_identity["bytes"]) is not int
        or runner_identity["bytes"] != expected_runner.stat().st_size
        or runner_identity["sha256"] != sha256_file(expected_runner)
        or not _strict_json_identity(record["runner"], runner_identity)
    ):
        raise PipelineError("resume3 runner identity changed")
    pre_stat = os.stat(str(pre_path), follow_symlinks=False)
    if (
        pre["created_at_ns"] >= process["started_at_ns"]
        or pre_stat.st_mtime_ns >= process["started_at_ns"]
    ):
        raise PipelineError("resume3 pre-launch evidence is not historical")
    pre_run = record["pre_run"]
    _resume3_exact_keys(
        pre_run,
        {"ninja_log", "ninja_deps", "build_ninja", "toolchain_inventory"},
        "resume3 pre-run",
    )
    if architecture == "x64":
        if pre_run["ninja_log"] is not None or pre_run["ninja_deps"] is not None:
            raise PipelineError("resume3 fresh x86_64 pre-run history is not absent")
        _validate_recorded_file_snapshot(
            pre_run["build_ninja"],
            Path(out) / "build.ninja",
            "resume3 pre-run build_ninja",
        )
    else:
        for name, relative in (
            ("ninja_log", ".ninja_log"),
            ("ninja_deps", ".ninja_deps"),
            ("build_ninja", "build.ninja"),
        ):
            _validate_recorded_file_snapshot(
                pre_run[name],
                Path(out) / relative,
                "resume3 pre-run {}".format(name),
            )
    _validate_recorded_toolchain_inventory(pre_run["toolchain_inventory"])
    fresh_x64_binding = None
    if architecture == "x64":
        fresh_x64_binding = _fresh_x64_resume_preparation_binding(
            source,
            developer_dir,
            out,
            record["fresh_x64_preparation"],
            pre_run,
        )
    initial_stdout = pre["stdout_log"]
    _resume3_exact_keys(
        initial_stdout,
        {"logical_path", "physical_path", "device", "inode", "uid", "gid", "mode", "bytes", "mtime_ns", "birth_time_ns"},
        "resume3 initial stdout",
    )
    logical_stdout = Path(record["stdout_log"]["path"])
    physical_stdout = _physical_execution_path(logical_stdout, alias_receipt, "resume3 stdout")
    if (
        logical_stdout != expected_stdout_logical
        or physical_stdout != expected_stdout_physical
        or initial_stdout["logical_path"] != str(logical_stdout)
        or initial_stdout["physical_path"] != str(physical_stdout)
        or any(type(initial_stdout[name]) is not int for name in set(initial_stdout) - {"logical_path", "physical_path"})
        or initial_stdout["bytes"] != 0
        or initial_stdout["mode"] & 0o022
        or record["stdout_log"]
        != {
            "path": str(logical_stdout),
            "device": initial_stdout["device"],
            "inode": initial_stdout["inode"],
            "birth_time_ns": initial_stdout["birth_time_ns"],
        }
    ):
        raise PipelineError("resume3 initial stdout identity mismatch")
    primary_keys = {"schema", "kind", "run_id", "observed_at_ns", "observation_methods", "pre_launch", "process_group", "stdout_log_live_snapshot"}
    if architecture == "x64":
        primary_keys.add("architecture")
    _resume3_exact_keys(primary, primary_keys, "resume3 primary observation")
    if (
        type(primary["schema"]) is not int
        or primary["schema"] != 2
        or primary["kind"] != "focus-macos-alias-raw-ninja-live-process-chain-observation"
        or primary["run_id"] != stem
        or type(primary["observed_at_ns"]) is not int
        or primary["observed_at_ns"] != process["observed_live_at_ns"]
        or primary["observation_methods"] != ["ps", "lsof", "proc_pidpath"]
        or not _strict_json_identity(primary["pre_launch"], record["pre_launch"])
        or (
            architecture == "x64"
            and primary.get("architecture") != architecture
        )
    ):
        raise PipelineError("resume3 primary observation mismatch")
    stable_roles = _resume3_process_group_contract(
        primary, record, source, out, ninja, physical_stdout
    )
    primary_stdout = _resume3_snapshot_contract(primary["stdout_log_live_snapshot"], "resume3 primary stdout")
    if (
        Path(primary_stdout["path"]) != physical_stdout
        or primary_stdout["inode"] != initial_stdout["inode"]
        or primary_stdout["birth_time_ns"] != initial_stdout["birth_time_ns"]
        or primary_stdout["bytes"] <= 0
        or primary_stdout["bytes"] > MAX_RESUME_STDOUT_BYTES
        or primary_stdout["mode"] & 0o022
        or primary_stdout["mtime_ns"] > primary["observed_at_ns"]
    ):
        raise PipelineError("resume3 primary stdout mismatch")
    supplement_keys = {"schema", "kind", "run_id", "observed_at_ns", "observation_method", "primary_observation", "processes"}
    if architecture == "x64":
        supplement_keys.add("architecture")
    _resume3_exact_keys(supplement, supplement_keys, "resume3 environment supplement")
    if (
        type(supplement["schema"]) is not int
        or supplement["schema"] != 2
        or supplement["kind"]
        != "focus-macos-alias-raw-ninja-live-process-chain-observation-supplement"
        or supplement["run_id"] != stem
        or type(supplement["observed_at_ns"]) is not int
        or supplement["observed_at_ns"] < primary["observed_at_ns"]
        or supplement["observation_method"] != "ps eww"
        or not _strict_json_identity(
            supplement["primary_observation"], record["live_process_observation"]
        )
        or not isinstance(supplement["processes"], list)
        or len(supplement["processes"]) != 2
        or (
            architecture == "x64"
            and supplement.get("architecture") != architecture
        )
    ):
        raise PipelineError("resume3 environment supplement mismatch")
    supplement_by_role = {}
    supplement_process_keys = {
        "role",
        "pid",
        "ppid",
        "pgid",
        "PATH",
        "PWD",
        "allowlisted_environment",
        "ps_eww_bytes",
        "ps_eww_sha256",
    }
    python_bin = (
        Path(source).parent
        / "depot_tools/python-bin/.."
        / PACKAGING_PYTHON_RELDIR
    )
    expected_path = os.pathsep.join(
        (str(python_bin), str(python_bin / "Scripts"), expected_environment["PATH"])
    )
    expected_allowlisted = dict(expected_environment)
    expected_allowlisted.pop("PATH")
    for item in supplement["processes"]:
        _resume3_exact_keys(item, supplement_process_keys, "resume3 supplemented process")
        role = item["role"]
        if role not in {"autoninja_python", "pinned_ninja"} or role in supplement_by_role:
            raise PipelineError("resume3 supplemented process role mismatch")
        stable = stable_roles[role]
        if (
            type(item["pid"]) is not int
            or type(item["ppid"]) is not int
            or type(item["pgid"]) is not int
            or item["pid"] != stable["pid"]
            or item["ppid"] != stable["ppid"]
            or item["pgid"] != process["pgid"]
            or item["PATH"] != expected_path
            or item["PWD"] != str(Path(source).resolve(strict=True))
            or not _strict_json_identity(item["allowlisted_environment"], expected_allowlisted)
            or type(item["ps_eww_bytes"]) is not int
            or item["ps_eww_bytes"] <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", item["ps_eww_sha256"])
        ):
            raise PipelineError("resume3 supplemented process identity mismatch")
        supplement_by_role[role] = item
    revalidation_keys = {"schema", "kind", "run_id", "capture_started_at_ns", "capture_finished_at_ns", "observation_methods", "linked_evidence", "stable_spine", "dynamic_descendants", "script_identities", "stdout_log_live_snapshot"}
    if architecture == "x64":
        revalidation_keys.add("architecture")
    _resume3_exact_keys(revalidation, revalidation_keys, "resume3 process revalidation")
    if (
        type(revalidation["schema"]) is not int
        or revalidation["schema"] != 2
        or revalidation["kind"]
        != "focus-macos-alias-raw-ninja-live-process-chain-revalidation"
        or revalidation["run_id"] != stem
        or type(revalidation["capture_started_at_ns"]) is not int
        or type(revalidation["capture_finished_at_ns"]) is not int
        or revalidation["capture_started_at_ns"] < supplement["observed_at_ns"]
        or revalidation["capture_finished_at_ns"] < revalidation["capture_started_at_ns"]
        or revalidation["observation_methods"] != ["ps", "lsof", "proc_pidpath"]
        or not _strict_json_identity(
            revalidation["linked_evidence"],
            {
                "pre_launch": record["pre_launch"],
                "primary_observation": record["live_process_observation"],
                "environment_supplement": record["live_process_environment_supplement"],
            },
        )
        or (
            architecture == "x64"
            and revalidation.get("architecture") != architecture
        )
    ):
        raise PipelineError("resume3 process revalidation mismatch")
    spine_keys = {"role", "pid", "ppid", "pgid", "started_at_ns", "cwd_physical", "executable", "ps_command"}
    spine = revalidation["stable_spine"]
    if not isinstance(spine, list) or len(spine) != len(stable_roles):
        raise PipelineError("resume3 stable revalidation spine mismatch")
    revalidated_by_role = {}
    for item in spine:
        _resume3_exact_keys(item, spine_keys, "resume3 stable revalidation member")
        role = item["role"]
        if role not in stable_roles or role in revalidated_by_role:
            raise PipelineError("resume3 stable revalidation role mismatch")
        expected = {key: stable_roles[role][key] for key in spine_keys}
        if not _strict_json_identity(item, expected):
            raise PipelineError("resume3 stable process changed during revalidation")
        revalidated_by_role[role] = item
    dynamic_revalidation = revalidation["dynamic_descendants"]
    if not isinstance(dynamic_revalidation, list):
        raise PipelineError("resume3 dynamic revalidation is not a list")
    revalidation_pids = {item["pid"]: item for item in spine}
    for item in dynamic_revalidation:
        _resume3_exact_keys(item, spine_keys, "resume3 revalidated dynamic process")
        if (
            item["role"] != "dynamic_descendant"
            or any(type(item[name]) is not int for name in ("pid", "ppid", "pgid", "started_at_ns"))
            or item["pid"] <= 1
            or item["ppid"] <= 1
            or item["pgid"] != process["pgid"]
            or item["pid"] in revalidation_pids
        ):
            raise PipelineError("resume3 revalidated dynamic process mismatch")
        revalidation_pids[item["pid"]] = item
    pinned_pid = stable_roles["pinned_ninja"]["pid"]
    for item in dynamic_revalidation:
        cursor = item
        visited = set()
        while cursor["pid"] != pinned_pid:
            if cursor["pid"] in visited:
                raise PipelineError("resume3 revalidated dynamic ancestry cycle")
            visited.add(cursor["pid"])
            cursor = revalidation_pids.get(cursor["ppid"])
            if cursor is None:
                raise PipelineError("resume3 revalidated dynamic ancestry does not reach Ninja")
    scripts = revalidation["script_identities"]
    script_keys = {"path", "bytes", "inode", "uid", "gid", "mode", "sha256"}
    expected_scripts = {
        (Path(source).parent / "depot_tools/autoninja").resolve(strict=True),
        (Path(source).parent / "depot_tools/autoninja.py").resolve(strict=True),
        expected_runner,
    }
    if not isinstance(scripts, list) or len(scripts) != len(expected_scripts):
        raise PipelineError("resume3 script identity list mismatch")
    observed_scripts = set()
    for item in scripts:
        _resume3_exact_keys(item, script_keys, "resume3 script identity")
        path = Path(item["path"]).resolve(strict=True)
        observed = os.stat(str(path), follow_symlinks=False)
        if (
            path not in expected_scripts
            or path in observed_scripts
            or any(type(item[name]) is not int for name in ("bytes", "inode", "uid", "gid", "mode"))
            or item["bytes"] != observed.st_size
            or item["inode"] != observed.st_ino
            or item["uid"] != observed.st_uid
            or item["gid"] != observed.st_gid
            or item["mode"] != stat.S_IMODE(observed.st_mode)
            or item["sha256"] != sha256_file(path)
        ):
            raise PipelineError("resume3 script identity changed")
        observed_scripts.add(path)
    revalidation_stdout = _resume3_snapshot_contract(
        revalidation["stdout_log_live_snapshot"], "resume3 revalidation stdout"
    )
    if (
        Path(revalidation_stdout["path"]) != physical_stdout
        or revalidation_stdout["inode"] != initial_stdout["inode"]
        or revalidation_stdout["birth_time_ns"] != initial_stdout["birth_time_ns"]
        or revalidation_stdout["bytes"] < primary_stdout["bytes"]
        or revalidation_stdout["bytes"] > MAX_RESUME_STDOUT_BYTES
        or revalidation_stdout["mode"] & 0o022
        or revalidation_stdout["mtime_ns"] > revalidation["capture_finished_at_ns"]
    ):
        raise PipelineError("resume3 revalidation stdout mismatch")
    status_keys = {"schema", "kind", "run_id", "pid", "pgid", "wait_observation", "pipefail", "outcome", "failure", "monitor", "evidence_complete", "pipeline_success_derived", "pre_launch", "live_evidence", "stdout_log", "post_run", "explicit_gn_gen_command", "network_operations"}
    if architecture == "x64":
        status_keys.add("architecture")
    _resume3_exact_keys(status, status_keys, "resume3 exit status")
    wait = status["wait_observation"]
    _resume3_exact_keys(wait, {"api", "returncode", "wait_returned_at_ns", "runner_pid"}, "resume3 wait observation")
    expected_live = {
        "primary": record["live_process_observation"],
        "supplement": record["live_process_environment_supplement"],
        "revalidation": record["live_process_revalidation"],
    }
    if (
        type(status["schema"]) is not int
        or status["schema"] != 2
        or status["kind"] != "focus-macos-alias-resume3-popen-exit-status"
        or status["run_id"] != stem
        or status["pid"] != process["pid"]
        or status["pgid"] != process["pgid"]
        or wait["api"] != "subprocess.Popen.wait"
        or type(wait["returncode"]) is not int
        or wait["returncode"] != 0
        or type(wait["wait_returned_at_ns"]) is not int
        or wait["wait_returned_at_ns"] <= revalidation["capture_finished_at_ns"]
        or type(wait["runner_pid"]) is not int
        or wait["runner_pid"] <= 1
        or status["pipefail"] is not True
        or status["outcome"] != "completed"
        or status["failure"] is not None
        or status["evidence_complete"] is not True
        or status["pipeline_success_derived"] is not True
        or not _strict_json_identity(status["pre_launch"], record["pre_launch"])
        or not _strict_json_identity(status["live_evidence"], expected_live)
        or status["explicit_gn_gen_command"] is not False
        or type(status["network_operations"]) is not int
        or status["network_operations"] != 0
        or (
            architecture == "x64"
            and status.get("architecture") != architecture
        )
    ):
        raise PipelineError("resume3 successful exit status mismatch")
    logs = record_path.parent
    _resume3_monitor_contract(status["monitor"], source, logs)
    if _process_group_exists(process["pgid"]):
        raise PipelineError("resume3 process group still exists after completion")
    completion = record["completion"]
    completion_keys = {"ended_at_ns", "observed_at_ns", "wrapper_exit_code", "pipefail", "pipeline_success_derived", "stdout_log", "post_run", "explicit_gn_gen_command"}
    _resume3_exact_keys(completion, completion_keys, "resume3 completion")
    if (
        type(completion["ended_at_ns"]) is not int
        or completion["ended_at_ns"] != wait["wait_returned_at_ns"]
        or type(completion["observed_at_ns"]) is not int
        or completion["observed_at_ns"] < completion["ended_at_ns"]
        or type(completion["wrapper_exit_code"]) is not int
        or completion["wrapper_exit_code"] != wait["returncode"]
        or completion["pipefail"] is not True
        or completion["pipeline_success_derived"] is not True
        or completion["explicit_gn_gen_command"] is not False
        or not _strict_json_identity(completion["stdout_log"], status["stdout_log"])
        or not _strict_json_identity(completion["post_run"], status["post_run"])
        or completion["ended_at_ns"] < process["observed_live_at_ns"]
    ):
        raise PipelineError("resume3 completion/status derivation mismatch")
    final_stdout = _resume3_snapshot_contract(status["stdout_log"], "resume3 final stdout")
    stdout_stat = os.stat(str(logical_stdout), follow_symlinks=False)
    current_stdout = {
        "device": stdout_stat.st_dev,
        "inode": stdout_stat.st_ino,
        "uid": stdout_stat.st_uid,
        "gid": stdout_stat.st_gid,
        "mode": stat.S_IMODE(stdout_stat.st_mode),
        "bytes": stdout_stat.st_size,
        "mtime_ns": stdout_stat.st_mtime_ns,
        "ctime_ns": stdout_stat.st_ctime_ns,
        "birth_time_ns": int(
            getattr(stdout_stat, "st_birthtime", stdout_stat.st_ctime)
            * 1_000_000_000
        ),
        "path": str(logical_stdout),
        "sha256": sha256_file(logical_stdout),
    }
    if (
        not _strict_json_identity(final_stdout, current_stdout)
        or final_stdout["inode"] != initial_stdout["inode"]
        or final_stdout["device"] != initial_stdout["device"]
        or final_stdout["birth_time_ns"] != initial_stdout["birth_time_ns"]
        or final_stdout["mode"] & 0o222
        or final_stdout["bytes"] <= 0
        or final_stdout["bytes"] > MAX_RESUME_STDOUT_BYTES
        or primary_stdout["bytes"] > final_stdout["bytes"]
        or revalidation_stdout["bytes"] > final_stdout["bytes"]
        or _sha256_file_prefix(logical_stdout, primary_stdout["bytes"])
        != primary_stdout["sha256"]
        or _sha256_file_prefix(logical_stdout, revalidation_stdout["bytes"])
        != revalidation_stdout["sha256"]
        or status["monitor"]["maximum_stdout_bytes"] != final_stdout["bytes"]
    ):
        raise PipelineError("resume3 final stdout identity/prefix mismatch")
    post = status["post_run"]
    _resume3_exact_keys(
        post,
        {"ninja_log", "ninja_deps", "build_ninja", "toolchain_inventory"},
        "resume3 post-run",
    )
    for name, relative in (
        ("ninja_log", ".ninja_log"),
        ("ninja_deps", ".ninja_deps"),
        ("build_ninja", "build.ninja"),
    ):
        _validate_recorded_file_snapshot(post[name], Path(out) / relative, "resume3 post-run {}".format(name))
    _validate_recorded_toolchain_inventory(post["toolchain_inventory"])
    current_post = {
        "ninja_log": _regular_file_snapshot(Path(out) / ".ninja_log"),
        "ninja_deps": _regular_file_snapshot(Path(out) / ".ninja_deps"),
        "build_ninja": _regular_file_snapshot(Path(out) / "build.ninja"),
        "toolchain_inventory": _toolchain_inventory(out),
    }
    if allow_history_growth:
        if authorized_history is None:
            raise PipelineError("resume3 history growth lacks authorization")
        if (
            post["build_ninja"] != current_post["build_ninja"]
            or post["toolchain_inventory"] != current_post["toolchain_inventory"]
        ):
            raise PipelineError("resume3 graph changed after completion")
        _ninja_history_exact_contract(authorized_history, out, "authorized resumed Ninja phase")
    elif not _strict_json_identity(post, current_post):
        raise PipelineError("resume3 post-run snapshot changed")
    _resume3_ninja_history_transition_contract(
        pre_run, post, architecture, process["started_at_ns"]
    )
    for evidence_path, earliest, latest, label in (
        (primary_path, primary["observed_at_ns"], supplement["observed_at_ns"], "primary"),
        (supplement_path, supplement["observed_at_ns"], revalidation["capture_finished_at_ns"], "supplement"),
        (revalidation_path, revalidation["capture_finished_at_ns"], completion["ended_at_ns"], "revalidation"),
        (status_path, completion["ended_at_ns"], completion["observed_at_ns"], "status"),
    ):
        observed = os.stat(str(evidence_path), follow_symlinks=False)
        if observed.st_mtime_ns < earliest or observed.st_mtime_ns > latest + 1_000_000_000:
            raise PipelineError("resume3 {} evidence timing mismatch".format(label))
    result = {
        "path": str(record_path),
        "sha256": record_sha256,
        "started_at_ns": process["started_at_ns"],
        "ended_at_ns": completion["ended_at_ns"],
        "wrapper_exit_code": 0,
        "pipefail": True,
        "pipeline_success_derived": True,
        "pre_run": pre_run,
        "post_run": post,
        "stdout_log": final_stdout,
        "explicit_gn_gen_command": False,
    }
    if architecture == "x64":
        result["fresh_x64_preparation"] = fresh_x64_binding["link"]
    return result


def resume_execution_record_contract(
    record_path,
    alias_receipt,
    source,
    developer_dir,
    architecture,
    out,
    ninja,
    allow_history_growth=False,
    authorized_history=None,
):
    """Validate the contemporaneous, completed raw Ninja execution record."""
    record_path = _execution_evidence_path(
        record_path, alias_receipt, "resume execution record"
    )
    expected_initial_basename = _resume_execution_initial_basename(
        record_path, architecture
    )
    logical_workspace = Path(alias_receipt["mappings"]["workspace"]["logical"])
    if (
        not record_path.name.endswith(".execution.json")
        or record_path.name.endswith(".part")
        or record_path.is_symlink()
        or not record_path.is_file()
    ):
        raise PipelineError("resume execution record must be a final regular JSON file")
    expected_volume_uuid = alias_receipt.get("volume", {}).get("volume_uuid")
    if _volume_identity(record_path)["volume_uuid"] != expected_volume_uuid:
        raise PipelineError("resume execution record volume changed")
    record, record_sha256, _record_identity = _descriptor_bound_immutable_json(
        record_path, "resume execution record"
    )
    if isinstance(record, dict) and type(record.get("schema")) is int and record.get(
        "schema"
    ) == 3:
        return _resume3_execution_record_contract(
            record_path,
            record,
            record_sha256,
            alias_receipt,
            source,
            developer_dir,
            architecture,
            out,
            ninja,
            allow_history_growth=allow_history_growth,
            authorized_history=authorized_history,
        )
    historical_keys = {
        "schema",
        "kind",
        "architecture",
        "logical",
        "process",
        "identity",
        "pre_run",
        "stdout_log",
        "completion",
    }
    final_keys = historical_keys | {
        "initial_record",
        "live_process_observation",
        "live_process_environment_supplement",
        "live_process_revalidation",
    }
    if (
        set(record) != final_keys
        or record.get("schema") != 2
        or record.get("kind") != "focus-macos-alias-raw-ninja-execution"
    ):
        raise PipelineError("resume execution record schema mismatch")
    initial_path, initial_record = _linked_execution_evidence(
        record.get("initial_record"), alias_receipt, "initial execution record"
    )
    if (
        not initial_path.name.endswith(".execution.json.part")
        or initial_path.name != expected_initial_basename
        or set(initial_record) != historical_keys
        or initial_record.get("schema") != 1
        or initial_record.get("completion") is not None
        or initial_record.get("kind") != record.get("kind")
    ):
        raise PipelineError("initial execution record schema mismatch")
    for key in historical_keys - {"schema", "completion"}:
        if record.get(key) != initial_record.get(key):
            raise PipelineError("final execution record rewrote historical evidence")
    observation_path, observation = _linked_execution_evidence(
        record.get("live_process_observation"),
        alias_receipt,
        "live process observation",
    )
    supplement_path, supplement = _linked_execution_evidence(
        record.get("live_process_environment_supplement"),
        alias_receipt,
        "live process environment supplement",
    )
    revalidation_path, revalidation = _linked_execution_evidence(
        record.get("live_process_revalidation"),
        alias_receipt,
        "live process revalidation",
    )
    if record.get("architecture") != architecture:
        raise PipelineError("resume execution architecture mismatch")
    expected_logical = {
        "home": alias_receipt["logical_home"],
        "workspace": logical_workspace.as_posix(),
        "source": str(source),
        "developer_dir": str(developer_dir),
        "out": str(out),
    }
    if record.get("logical") != expected_logical:
        raise PipelineError("resume execution logical paths mismatch")
    expected_argv = [
        str(source.parent / "depot_tools" / "autoninja"),
        "-j{}".format(BUILD_JOBS),
        "-C",
        str(Path(out).relative_to(source)),
        "chrome",
        "chrome/installer/mac:copies",
    ]
    expected_environment = {
        "HOME": alias_receipt["logical_home"],
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "DEVELOPER_DIR": str(developer_dir),
        "PATH": os.pathsep.join(
            (
                str(source.parent / "depot_tools"),
                str(Path(ninja["path"]).parent),
                SYSTEM_PATH,
            )
        ),
        "DEPOT_TOOLS_UPDATE": "0",
        "DEPOT_TOOLS_METRICS": "0",
        "GCLIENT_FILE": str(source.parent / ".gclient"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NINJA_SUMMARIZE_BUILD": "1",
    }
    process = record.get("process")
    if not isinstance(process, dict) or set(process) != {
        "pid",
        "pgid",
        "started_at_ns",
        "observed_live_at_ns",
        "cwd",
        "argv",
        "environment",
    }:
        raise PipelineError("resume execution process schema mismatch")
    if (
        type(process.get("pid")) is not int
        or process.get("pid", 0) <= 1
        or process.get("pgid") != process.get("pid")
        or type(process.get("started_at_ns")) is not int
        or type(process.get("observed_live_at_ns")) is not int
        or process.get("started_at_ns", 0) <= 0
        or process.get("observed_live_at_ns", 0) < process.get("started_at_ns", 0)
        or process.get("cwd") != str(source)
        or process.get("argv") != expected_argv
        or process.get("environment") != expected_environment
    ):
        raise PipelineError("resume execution process provenance mismatch")
    alias_identity = dict(alias_receipt["alias"])
    alias_identity.pop("root_owned", None)
    alias_identity.pop("absolute_exact_target", None)
    alias_identity.pop("target_identity", None)
    expected_identity = {
        "alias": alias_identity,
        "source": _execution_identity_mapping(
            alias_receipt["mappings"]["source"]
        ),
        "developer": _execution_identity_mapping(
            alias_receipt["mappings"]["developer"]
        ),
    }
    if record.get("identity") != expected_identity:
        raise PipelineError("resume execution inode identity mismatch")
    pre = record.get("pre_run")
    if not isinstance(pre, dict) or set(pre) != {
        "ninja_log",
        "ninja_deps",
        "build_ninja",
        "toolchain_inventory",
    }:
        raise PipelineError("resume execution pre-run schema mismatch")
    for name, relative in (
        ("ninja_log", ".ninja_log"),
        ("ninja_deps", ".ninja_deps"),
        ("build_ninja", "build.ninja"),
    ):
        _validate_recorded_file_snapshot(
            pre.get(name), Path(out) / relative, "pre-run {}".format(name)
        )
    _validate_recorded_toolchain_inventory(pre.get("toolchain_inventory"))
    stdout_initial = record.get("stdout_log")
    if not isinstance(stdout_initial, dict) or set(stdout_initial) != {
        "path",
        "device",
        "inode",
        "birth_time_ns",
    }:
        raise PipelineError("resume stdout-log observation schema mismatch")
    stdout_path = Path(stdout_initial.get("path", ""))
    _require_real_descendant(logical_workspace, stdout_path, "resume stdout log")
    if stdout_path.is_symlink() or not stdout_path.is_file():
        raise PipelineError("resume stdout log is not regular")
    stdout_stat = os.stat(str(stdout_path), follow_symlinks=False)
    if (
        stdout_stat.st_uid != os.getuid()
        or stdout_stat.st_mode & 0o022
        or stdout_stat.st_size <= 0
        or stdout_stat.st_size > MAX_RESUME_STDOUT_BYTES
    ):
        raise PipelineError("resume stdout log ownership or mode is unsafe")
    if _volume_identity(stdout_path)["volume_uuid"] != expected_volume_uuid:
        raise PipelineError("resume stdout log volume changed")
    stdout_birth_ns = int(
        getattr(stdout_stat, "st_birthtime", stdout_stat.st_ctime)
        * 1_000_000_000
    )
    if (
        stdout_initial.get("path") != str(stdout_path)
        or type(stdout_initial.get("device")) is not int
        or stdout_initial.get("device", 0) <= 0
        or stdout_initial.get("inode") != stdout_stat.st_ino
        or stdout_initial.get("birth_time_ns") != stdout_birth_ns
    ):
        raise PipelineError("resume stdout log inode changed")
    completion = record.get("completion")
    if not isinstance(completion, dict) or set(completion) != {
        "ended_at_ns",
        "observed_at_ns",
        "wrapper_exit_code",
        "pipefail",
        "pipeline_success_derived",
        "stdout_log",
        "post_run",
        "explicit_gn_gen_command",
    }:
        raise PipelineError("resume execution is not finalized")
    if (
        completion.get("wrapper_exit_code") != 0
        or completion.get("pipefail") is not True
        or completion.get("pipeline_success_derived") is not True
        or completion.get("explicit_gn_gen_command") is not False
        or type(completion.get("ended_at_ns")) is not int
        or type(completion.get("observed_at_ns")) is not int
        or completion.get("ended_at_ns", 0) < process["started_at_ns"]
        or completion.get("ended_at_ns", 0) < process["observed_live_at_ns"]
        or completion.get("observed_at_ns", 0) < completion.get("ended_at_ns", 0)
    ):
        raise PipelineError("resume execution did not complete successfully")
    final_stdout = _regular_file_snapshot(stdout_path)
    final_stdout.update(
        {
            "device": stdout_initial["device"],
            "inode": stdout_stat.st_ino,
            "birth_time_ns": stdout_birth_ns,
        }
    )
    live_stdout = observation.get("stdout_log_live_snapshot", {})
    if (
        stdout_birth_ns < process["started_at_ns"]
        or stdout_birth_ns > process["observed_live_at_ns"]
        or final_stdout["mtime_ns"] < live_stdout.get("mtime_ns", 0)
        or final_stdout["mtime_ns"] > completion["ended_at_ns"] + 1_000_000_000
        or final_stdout["mtime_ns"] > completion["observed_at_ns"]
    ):
        raise PipelineError("resume stdout log timing is invalid")
    if completion.get("stdout_log") != final_stdout:
        raise PipelineError("resume stdout log changed after completion")
    _validate_live_process_observation(
        observation,
        initial_path,
        record["initial_record"]["sha256"],
        record,
        alias_receipt,
        ninja,
    )
    _validate_live_environment_supplement(
        supplement,
        observation_path,
        record["live_process_observation"]["sha256"],
        record,
        supplement_path,
        observation,
    )
    _validate_live_process_revalidation(
        revalidation,
        initial_path,
        observation_path,
        supplement_path,
        record,
        alias_receipt,
        observation,
        supplement,
    )
    post = completion.get("post_run")
    if not isinstance(post, dict) or set(post) != set(pre):
        raise PipelineError("resume execution post-run schema mismatch")
    for name, relative in (
        ("ninja_log", ".ninja_log"),
        ("ninja_deps", ".ninja_deps"),
        ("build_ninja", "build.ninja"),
    ):
        _validate_recorded_file_snapshot(
            post.get(name), Path(out) / relative, "post-run {}".format(name)
        )
    _validate_recorded_toolchain_inventory(post.get("toolchain_inventory"))
    current_post = {
        "ninja_log": _regular_file_snapshot(Path(out) / ".ninja_log"),
        "ninja_deps": _regular_file_snapshot(Path(out) / ".ninja_deps"),
        "build_ninja": _regular_file_snapshot(Path(out) / "build.ninja"),
        "toolchain_inventory": _toolchain_inventory(out),
    }
    if allow_history_growth:
        if authorized_history is None:
            raise PipelineError(
                "resumed Ninja history growth lacks an authorized phase snapshot"
            )
        if (
            post["build_ninja"] != current_post["build_ninja"]
            or post["toolchain_inventory"]
            != current_post["toolchain_inventory"]
        ):
            raise PipelineError(
                "resumed Ninja graph changed after recorded completion"
            )
        _ninja_history_exact_contract(
            authorized_history,
            out,
            "authorized resumed Ninja phase",
        )
    elif post != current_post:
        raise PipelineError("resumed Ninja graph changed after recorded completion")
    if pre["build_ninja"] != post["build_ninja"]:
        raise PipelineError("raw resume unexpectedly regenerated build.ninja")
    if pre["toolchain_inventory"] != post["toolchain_inventory"]:
        raise PipelineError("raw resume unexpectedly changed toolchain.ninja")
    if pre["ninja_log"]["sha256"] == post["ninja_log"]["sha256"]:
        raise PipelineError("raw resume did not change .ninja_log")
    if post["ninja_log"]["mtime_ns"] <= pre["ninja_log"]["mtime_ns"]:
        raise PipelineError("raw resume .ninja_log timestamp regressed")
    pre_deps = pre["ninja_deps"]
    post_deps = post["ninja_deps"]
    if post_deps["mtime_ns"] < pre_deps["mtime_ns"]:
        raise PipelineError("raw resume .ninja_deps timestamp regressed")
    if pre_deps["sha256"] == post_deps["sha256"] and pre_deps != post_deps:
        raise PipelineError("raw resume .ninja_deps metadata changed without bytes")
    return {
        "path": str(record_path),
        "sha256": sha256_file(record_path),
        "started_at_ns": process["started_at_ns"],
        "ended_at_ns": completion["ended_at_ns"],
        "wrapper_exit_code": completion["wrapper_exit_code"],
        "pipefail": True,
        "pipeline_success_derived": True,
        "pre_run": pre,
        "post_run": post,
        "stdout_log": final_stdout,
        "explicit_gn_gen_command": False,
    }


def _count_stream_needles(path, needles, expected_stat):
    counts = {needle: 0 for needle in needles}
    digest = hashlib.sha256()
    overlap = max((len(needle) for needle in needles), default=1) - 1
    tail = b""
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb", closefd=True) as stream:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != expected_stat.st_dev
            or opened.st_ino != expected_stat.st_ino
            or opened.st_size != expected_stat.st_size
        ):
            raise PipelineError("resumed output changed while opening: {}".format(path))
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            data = tail + chunk
            stable = len(data) if overlap == 0 else max(0, len(data) - overlap)
            for needle in needles:
                start = 0
                while True:
                    position = data.find(needle, start)
                    if position < 0 or position >= stable:
                        break
                    counts[needle] += 1
                    start = position + len(needle)
            tail = data[stable:]
        closed_snapshot = os.fstat(stream.fileno())
        if (
            closed_snapshot.st_size != opened.st_size
            or closed_snapshot.st_mtime_ns != opened.st_mtime_ns
            or closed_snapshot.st_ctime_ns != opened.st_ctime_ns
        ):
            raise PipelineError("resumed output changed while scanning: {}".format(path))
    for needle in needles:
        start = 0
        while True:
            position = tail.find(needle, start)
            if position < 0:
                break
            counts[needle] += 1
            start = position + len(needle)
    return counts, digest.hexdigest()


def changed_path_scan(root, resume_start_ns, logical_home, physical_home):
    """Inventory changed nodes and scan the full tree for physical-home leaks."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise PipelineError("mixed-path scan root must be a real directory")
    logical = str(logical_home).encode("utf-8")
    physical = str(physical_home).encode("utf-8")
    inventory = hashlib.sha256()
    files = 0
    directories = 0
    symlinks = 0
    logical_occurrences = 0
    physical_occurrences = 0
    logical_files = 0
    scanned_bytes = 0
    first_forbidden = None
    full_files = 0
    full_symlinks = 0
    full_scanned_bytes = 0
    full_logical_occurrences = 0
    full_physical_occurrences = 0
    full_inventory = hashlib.sha256()
    def record_node(path, relative, observed, kind):
        nonlocal files, directories, symlinks, logical_occurrences
        nonlocal physical_occurrences, logical_files, scanned_bytes
        nonlocal first_forbidden
        nonlocal full_files, full_symlinks, full_scanned_bytes
        nonlocal full_logical_occurrences, full_physical_occurrences
        if observed.st_uid != os.getuid():
            raise PipelineError("resumed output ownership changed: {}".format(path))
        changed_ns = max(observed.st_mtime_ns, observed.st_ctime_ns)
        symlink_body = None
        counts = {logical: 0, physical: 0}
        body_hash = hashlib.sha256(b"").hexdigest()
        size = 0
        if kind == "symlink":
            symlink_body = os.readlink(str(path))
            target = Path(symlink_body)
            if target.is_absolute():
                raise PipelineError(
                    "absolute symlink in resumed output: {}".format(path)
                )
            lexical_target = Path(
                os.path.normpath(str(path.parent / target))
            )
            try:
                lexical_target.relative_to(root)
            except ValueError as exc:
                raise PipelineError(
                    "symlink escapes resumed output: {}".format(path)
                ) from exc
            body = symlink_body.encode("utf-8")
            counts = {
                logical: body.count(logical),
                physical: body.count(physical),
            }
            body_hash = hashlib.sha256(body).hexdigest()
            size = len(body)
            full_symlinks += 1
        elif kind == "file":
            counts, body_hash = _count_stream_needles(
                path, (logical, physical), observed
            )
            size = observed.st_size
            full_files += 1
        relative_bytes = relative.encode("utf-8")
        counts[logical] += relative_bytes.count(logical)
        counts[physical] += relative_bytes.count(physical)
        full_scanned_bytes += size
        full_logical_occurrences += counts[logical]
        full_physical_occurrences += counts[physical]
        if kind != "directory":
            full_inventory.update(
                "{}\0{}\0{}\0{}\0{}\n".format(
                    kind,
                    relative,
                    size,
                    body_hash,
                    counts[logical],
                ).encode("utf-8")
            )
        if counts[physical] and first_forbidden is None:
            first_forbidden = relative
        if changed_ns < resume_start_ns:
            return
        if kind == "symlink":
            symlinks += 1
        elif kind == "file":
            files += 1
        elif kind == "directory":
            counts = {logical: 0, physical: 0}
            body_hash = hashlib.sha256(b"").hexdigest()
            directories += 1
            size = 0
        else:
            raise PipelineError("unknown mixed-path inventory node")
        scanned_bytes += size
        logical_occurrences += counts[logical]
        physical_occurrences += counts[physical]
        if counts[logical]:
            logical_files += 1
        inventory.update(
            "{}\0{}\0{}\0{}\0{}\0{}\n".format(
                kind,
                relative,
                size,
                changed_ns,
                body_hash,
                counts[logical],
            ).encode("utf-8")
        )

    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(directory)
        traversable = []
        for name in sorted(dirnames):
            path = directory / name
            relative = path.relative_to(root).as_posix()
            observed = os.lstat(str(path))
            if stat.S_ISLNK(observed.st_mode):
                record_node(path, relative, observed, "symlink")
            elif stat.S_ISDIR(observed.st_mode):
                record_node(path, relative, observed, "directory")
                traversable.append(name)
            else:
                raise PipelineError(
                    "special directory entry in resumed output: {}".format(path)
                )
        dirnames[:] = traversable
        for name in sorted(filenames):
            path = directory / name
            relative = path.relative_to(root).as_posix()
            if relative == SLICE_RECEIPT_NAME:
                continue
            observed = os.lstat(str(path))
            if stat.S_ISLNK(observed.st_mode):
                record_node(path, relative, observed, "symlink")
            elif stat.S_ISREG(observed.st_mode):
                record_node(path, relative, observed, "file")
            else:
                raise PipelineError(
                    "special file changed during raw Ninja resume: {}".format(path)
                )
    if full_physical_occurrences:
        raise PipelineError(
            "physical home leaked into resumed output: {}".format(first_forbidden)
        )
    if files + directories + symlinks <= 0:
        raise PipelineError("mixed-path scan found no files changed since resume")
    return {
        "schema": 1,
        "root": str(root),
        "resume_start_ns": resume_start_ns,
        "changed_regular_files": files,
        "changed_directories": directories,
        "changed_symlinks": symlinks,
        "scanned_bytes": scanned_bytes,
        "logical_home": str(logical_home),
        "logical_home_matching_files": logical_files,
        "logical_home_occurrences": logical_occurrences,
        "physical_home": str(physical_home),
        "physical_home_occurrences": full_physical_occurrences,
        "mixed_paths": False,
        "inventory_sha256": inventory.hexdigest(),
        "full_path_scan": {
            "regular_files": full_files,
            "symlinks": full_symlinks,
            "scanned_bytes": full_scanned_bytes,
            "logical_home_occurrences": full_logical_occurrences,
            "physical_home_occurrences": full_physical_occurrences,
            "inventory_sha256": full_inventory.hexdigest(),
        },
    }


def _collect_bounded_probe_output(process, timeout_seconds=60):
    """Drain one merged stdout pipe without ever exceeding the hard cap."""
    if process.stdout is None:
        raise PipelineError("raw Ninja no-work probe has no stdout pipe")
    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    selector = selectors.DefaultSelector()
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    selector.register(descriptor, selectors.EVENT_READ)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PipelineError("raw Ninja no-work probe timed out")
            events = selector.select(min(remaining, 0.25))
            if not events and process.poll() is not None:
                events = [(selector.get_key(descriptor), selectors.EVENT_READ)]
            for key, _ in events:
                try:
                    chunk = os.read(key.fd, 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                if len(output) + len(chunk) > MAX_NO_WORK_OUTPUT_BYTES:
                    raise PipelineError(
                        "raw Ninja no-work probe output exceeded 1 MiB"
                    )
                output.extend(chunk)
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise PipelineError("raw Ninja no-work probe timed out") from exc
        if _process_group_exists(process.pid):
            _stop_process(process, force=True)
            raise PipelineError(
                "raw Ninja no-work probe left descendant processes"
            )
        return bytes(output)
    except BaseException:
        _stop_process(process, force=True)
        raise
    finally:
        selector.close()
        process.stdout.close()


def _ninja_no_work_contract(
    source, developer_dir, out_relative, ninja, alias_context=None
):
    command = [
        ninja["path"],
        "-n",
        "-C",
        out_relative,
        "chrome",
        "chrome/installer/mac:copies",
    ]
    if alias_context is None and Path(source).resolve(strict=True) != Path(source):
        alias_context = _recorded_alias_context(source, developer_dir)
    inherited = None
    if alias_context is not None:
        inherited = {"HOME": str(alias_context.logical_home)}
    environment = safe_environment(
        source,
        developer_dir,
        inherited=inherited,
        build_ninja=Path(ninja["path"]),
        alias_context=alias_context,
    )
    process = subprocess.Popen(
        command,
        cwd=str(source),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    raw_output = _collect_bounded_probe_output(process)
    try:
        output = raw_output.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PipelineError("raw Ninja no-work output is not UTF-8") from exc
    lines = [line for line in output.splitlines() if line]
    allowed_lines = (
        ["ninja: no work to do."],
        [
            "ninja: Entering directory `{}'".format(out_relative),
            "ninja: no work to do.",
        ],
    )
    if (
        process.returncode
        or lines not in allowed_lines
    ):
        raise PipelineError("raw Ninja resume is not complete:\n{}".format(output.strip()))
    return {
        "command": command,
        "returncode": process.returncode,
        "output_bytes": len(raw_output),
        "output_sha256": hashlib.sha256(raw_output).hexdigest(),
        "bounded_output_limit": MAX_NO_WORK_OUTPUT_BYTES,
        "no_work": True,
    }


def _live_alias_slice_no_work(
    source, developer_dir, architecture, authorized_history=None
):
    """Re-prove a resumed alias slice only at an executing boundary."""
    alias_receipt = in_source(source, HOME_ALIAS_RECEIPT, "home-alias receipt")
    if not alias_receipt.exists() and not alias_receipt.is_symlink():
        return None
    if architecture != "x64":
        raise PipelineError("unsupported downstream alias no-work architecture")
    out = in_source(
        source, X64_OUT, "x86_64 output", must_exist=True, directory=True
    )
    _, receipt = slice_receipt_contract(
        source,
        out,
        "x64",
        allow_resumed_history_growth=(authorized_history is not None),
        authorized_resumed_history=authorized_history,
    )
    if receipt.get("schema") != RESUMED_SLICE_RECEIPT_SCHEMA:
        raise PipelineError("downstream alias x64 receipt is not schema two")
    history_boundary = (
        authorized_history
        if authorized_history is not None
        else _ninja_history_snapshot(out)
    )
    _ninja_history_exact_contract(
        history_boundary, out, "authorized downstream signing"
    )
    report = _ninja_no_work_contract(
        source,
        developer_dir,
        X64_OUT,
        ninja_contract(source),
        alias_context=_recorded_alias_context(source, developer_dir),
    )
    _ninja_history_exact_contract(
        history_boundary, out, "post-probe authorized downstream signing"
    )
    return report


def resumed_slice_plan(source, developer_dir, architecture, resume_record):
    alias_path, alias_receipt = home_alias_receipt_contract(
        source, developer_dir
    )
    alias_context = _recorded_alias_context(source, developer_dir)
    acquisition_contract(source)
    tool_receipt_contract(source, developer_dir)
    if architecture == "x64":
        reclaim_contract(source)
        out_relative = X64_OUT
        expected_arch = "x86_64"
        allow_reclaimed_arm = True
    elif architecture == "arm64":
        out_relative = ARM_OUT
        expected_arch = "arm64"
        allow_reclaimed_arm = False
    else:
        raise PipelineError("unsupported resumed slice architecture")
    preparation_contract(
        source,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    xcode_path, _ = xcode27_compat_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    seatbelt_path, _ = xcode27_seatbelt_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    screen_ai_path, _ = screen_ai_disabled_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    linkedit_path, linkedit = xcode27_linkedit_strip_receipt_contract(
        source,
        developer_dir,
        required=True,
        allow_reclaimed_arm=allow_reclaimed_arm,
        alias_context=alias_context,
    )
    _, _, onboarding_alias_root = onboarding_alias_root_receipt_contract(source)
    out = in_source(
        source, out_relative, "resumed build output", must_exist=True, directory=True
    )
    receipt = out / SLICE_RECEIPT_NAME
    if receipt.exists() or receipt.is_symlink():
        raise PipelineError("refusing to overwrite resumed build receipt")
    args = out / "args.gn"
    if args.is_symlink() or not args.is_file():
        raise PipelineError("resumed output is missing args.gn")
    app = out / APP_NAME
    report = app_report(app, (expected_arch,))
    app_tree_sha256 = tree_digest(app)
    packaging = out / PACKAGING_NAME
    if packaging.is_symlink() or not packaging.is_dir():
        raise PipelineError("resumed output is missing the signing package")
    sign_script = packaging / "sign_chrome.py"
    if sha256_file(sign_script) != SIGN_CHROME_SHA256:
        raise PipelineError("resumed sign_chrome.py hash mismatch")
    ninja = ninja_contract(source)
    generated_linkedit = generated_linkedit_strip_contract(out, linkedit["tools"])
    execution = resume_execution_record_contract(
        resume_record,
        alias_receipt,
        source,
        developer_dir,
        architecture,
        out,
        ninja,
    )
    mixed = changed_path_scan(
        out,
        execution["started_at_ns"],
        Path(alias_receipt["logical_home"]),
        Path(alias_receipt["physical_home"]),
    )
    return {
        "stage": "finalize-resumed-{}".format(architecture),
        "architecture": architecture,
        "out": str(out),
        "receipt": str(receipt),
        "app": report,
        "app_tree_sha256": app_tree_sha256,
        "args_gn_sha256": sha256_file(args),
        "packaging": str(packaging),
        "home_alias_compatibility": {
            "path": str(alias_path),
            "sha256": sha256_file(alias_path),
        },
        "onboarding_alias_root_compatibility": onboarding_alias_root,
        "resume_execution": execution,
        "mixed_path_scan": mixed,
        "no_work_probe_command": [
            ninja["path"],
            "-n",
            "-C",
            out_relative,
            "chrome",
            "chrome/installer/mac:copies",
        ],
        "ninja": ninja,
        "generated_linkedit_strip": generated_linkedit,
        "xcode27_compatibility_receipt_sha256": sha256_file(xcode_path),
        "xcode27_seatbelt_compatibility_receipt_sha256": sha256_file(
            seatbelt_path
        ),
        "screen_ai_disabled_compatibility_receipt_sha256": sha256_file(
            screen_ai_path
        ),
        "xcode27_linkedit_strip_compatibility_receipt_sha256": sha256_file(
            linkedit_path
        ),
    }


def execute_resumed_slice(
    source,
    developer_dir,
    architecture,
    resume_record,
    plan,
    allow_finalize,
):
    if not allow_finalize:
        raise PipelineError(
            "resumed slice finalization requires --confirm-resumed-slice"
        )
    expected = resumed_slice_plan(
        source, developer_dir, architecture, resume_record
    )
    if plan != expected:
        raise PipelineError("resumed slice changed before receipt publication")
    out_relative = ARM_OUT if architecture == "arm64" else X64_OUT
    no_work = _ninja_no_work_contract(
        source,
        developer_dir,
        out_relative,
        expected["ninja"],
        alias_context=_recorded_alias_context(source, developer_dir),
    )
    final_expected = resumed_slice_plan(
        source, developer_dir, architecture, resume_record
    )
    if final_expected != expected:
        raise PipelineError("resumed slice changed during no-work acceptance")
    receipt = {
        "schema": RESUMED_SLICE_RECEIPT_SCHEMA,
        "architecture": architecture,
        "mach_o_architecture": (
            "arm64" if architecture == "arm64" else "x86_64"
        ),
        "source_root": str(source),
        "app": expected["app"],
        "app_tree_sha256": expected["app_tree_sha256"],
        "args_gn_sha256": expected["args_gn_sha256"],
        "preparation_receipt_sha256": sha256_file(
            in_source(
                source, PREPARATION_RECEIPT, "preparation receipt", must_exist=True
            )
        ),
        "xcode27_compatibility_receipt_sha256": expected[
            "xcode27_compatibility_receipt_sha256"
        ],
        "xcode27_seatbelt_compatibility_receipt_sha256": expected[
            "xcode27_seatbelt_compatibility_receipt_sha256"
        ],
        "screen_ai_disabled_compatibility_receipt_sha256": expected[
            "screen_ai_disabled_compatibility_receipt_sha256"
        ],
        "xcode27_linkedit_strip_compatibility_receipt_sha256": expected[
            "xcode27_linkedit_strip_compatibility_receipt_sha256"
        ],
        "generated_linkedit_strip": expected["generated_linkedit_strip"],
        "tool_receipt_sha256": sha256_file(source.parent / TOOL_RECEIPT),
        "ninja": expected["ninja"],
        "sign_chrome_sha256": SIGN_CHROME_SHA256,
        "home_alias_compatibility": expected["home_alias_compatibility"],
        "onboarding_alias_root_compatibility": expected[
            "onboarding_alias_root_compatibility"
        ],
        "resume_execution": expected["resume_execution"],
        "mixed_path_scan": expected["mixed_path_scan"],
        "no_work_probe": no_work,
        "raw_ninja_completed": True,
        "gn_gen_executed_by_finalizer": False,
        "build_command_executed_by_finalizer": False,
        "build_complete": True,
    }
    report = atomic_json(Path(expected["receipt"]), receipt)
    home_alias_receipt_contract(source, developer_dir)
    slice_receipt_contract(source, Path(expected["out"]), architecture)
    return report


def build_plan(source, developer_dir, architecture):
    alias_receipt = in_source(source, HOME_ALIAS_RECEIPT, "home-alias receipt")
    if alias_receipt.exists() or alias_receipt.is_symlink():
        raise PipelineError(
            "home-alias compatibility forbids GN regeneration; use a completed "
            "recorded raw-Ninja execution and finalize-resumed-*"
        )
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
    build_receipt_path, build_receipt = slice_receipt_contract(
        source, arm_out, "arm64"
    )
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
        "requires_live_no_work": (
            build_receipt.get("schema") == RESUMED_SLICE_RECEIPT_SCHEMA
        ),
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
    expected_plan = stage_arm_plan(source)
    if plan != expected_plan:
        raise PipelineError("arm64 staging plan changed before execution")
    build_receipt = load_json(plan["build_receipt"], "arm64 build receipt")
    no_work = None
    alias_context = None
    if plan.get("requires_live_no_work"):
        if build_receipt.get("schema") != RESUMED_SLICE_RECEIPT_SCHEMA:
            raise PipelineError("resumed arm64 staging receipt schema changed")
        tool_receipt = load_json(
            source.parent / TOOL_RECEIPT, "tool bootstrap receipt"
        )
        developer_dir = Path(tool_receipt["developer_dir"])
        alias_context = _recorded_alias_context(source, developer_dir)
        no_work = _ninja_no_work_contract(
            source,
            developer_dir,
            ARM_OUT,
            ninja_contract(source),
            alias_context=alias_context,
        )
    require_free(source, SOFT_FLOOR_GIB, "arm64 staging")
    source_app = Path(plan["source_app"])
    staged_app = Path(plan["staged_app"])
    partial_root = Path(plan["partial_root"])
    partial_app = Path(plan["partial_app"])
    partial_root.mkdir(parents=True, exist_ok=False)
    try:
        tool_receipt = load_json(source.parent / TOOL_RECEIPT, "tool bootstrap receipt")
        inherited = None
        if alias_context is not None:
            inherited = {"HOME": str(alias_context.logical_home)}
        environment = safe_environment(
            source,
            Path(tool_receipt["developer_dir"]),
            inherited=inherited,
            alias_context=alias_context,
        )
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
    arm_args_hash = build_receipt.get("args_gn_sha256")
    if arm_args_hash != sha256_file(arm_out / "args.gn"):
        raise PipelineError("arm64 args.gn changed before reclamation")
    stage_value = {
        "schema": 2 if no_work is not None else 1,
        "architecture": "arm64",
        "source_root": str(source),
        "staged_app": str(staged_app),
        "tree_sha256": staged_digest,
        "app_allocated_bytes": physical_size(staged_app),
        "reclaim_requested_out": str(arm_out),
        "reclaim_requested_bytes": out_bytes,
        "arm_args_gn_sha256": arm_args_hash,
        "build_receipt_sha256": sha256_file(plan["build_receipt"]),
        "upstream_no_work_probe": no_work,
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
    x64_app = x64_out / APP_NAME
    app_report(x64_app, ("x86_64",))
    swiftshader_receipt_path, _ = swiftshader_disabled_signing_receipt_contract(
        source, developer_dir, allow_adhoc_runtime_signing=True
    )
    adhoc_receipt_path, adhoc_receipt = adhoc_runtime_signing_receipt_contract(
        source, developer_dir
    )
    alias_active = _home_alias_is_active(source)
    if alias_active:
        slice_receipt_contract(
            source,
            x64_out,
            "x64",
            allow_resumed_history_growth=True,
            authorized_resumed_history=adhoc_receipt["ninja_history_after"],
        )
        _ninja_history_exact_contract(
            adhoc_receipt["ninja_history_after"],
            x64_out,
            "merge-authorized ad-hoc signing",
        )
    else:
        slice_receipt_contract(source, x64_out, "x64")
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
        **(
            {"x64_ninja_history": adhoc_receipt["ninja_history_after"]}
            if alias_active
            else {}
        ),
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
            "descriptor_pinned_publish": True,
            "durable_final_entry_before_candidate_unlink": True,
            "persistent_publish_recovery_journal": False,
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
    """Durably publish the exact accepted inode through the shared primitive."""
    try:
        return package_local_dmg.durable_publish_candidate(
            candidate,
            output,
            candidate_identity,
            size,
            digest,
        )
    except package_local_dmg.CommittedPublishError:
        # The caller must distinguish this durable commit boundary from all
        # pre-commit failures and must never roll the final inode back.
        raise
    except (OSError, package_local_dmg.PackageError) as exc:
        raise PipelineError(
            "accepted DMG publication rejected: {}".format(exc)
        ) from exc


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
    adhoc_receipt_path, adhoc_receipt = adhoc_runtime_signing_receipt_contract(
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
    alias_active = _home_alias_is_active(source)
    if alias_active and plan.get("x64_ninja_history") != adhoc_receipt.get(
        "ninja_history_after"
    ):
        raise PipelineError("authorized x86_64 Ninja history changed before merge")
    _live_alias_slice_no_work(
        source,
        developer_dir,
        "x64",
        authorized_history=(
            adhoc_receipt.get("ninja_history_after") if alias_active else None
        ),
    )
    arm_size = physical_size(plan["arm_app"])
    x64_size = physical_size(plan["x64_app"])
    # Universalization creates one combined app and signing creates another.
    merge_required = SOFT_FLOOR_GIB + 2 + 2.2 * (arm_size + x64_size) / GIB
    require_free(source, merge_required, "universal merge")
    unsigned_root = Path(plan["unsigned_root"])
    unsigned_root.mkdir(parents=True)
    environment = _build_child_environment(source, developer_dir)
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
    publication_committed = False
    post_commit_cleanup_warnings = []
    private_root_cleanup_complete = False
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
        try:
            published_stat = _publish_accepted_dmg(
                candidate,
                candidate_identity,
                output,
                accepted_stat.st_size,
                output_digest,
            )
            published_identity = (
                published_stat.st_dev,
                published_stat.st_ino,
            )
            publication_committed = True
        except package_local_dmg.CommittedPublishError as committed_error:
            # The output-parent fsync already crossed the durable commit
            # boundary.  Recover candidate cleanup if possible, but never
            # unlink the accepted final inode.
            publication_committed = True
            published_identity = tuple(committed_error.final_identity)
            post_commit_cleanup_warnings.append(repr(committed_error))
        try:
            _cleanup_private_dmg_candidate(
                temporary_root,
                root_identity,
                candidate,
                candidate_identity,
            )
            private_root_cleanup_complete = True
        except Exception as cleanup_error:
            post_commit_cleanup_warnings.append(repr(cleanup_error))
        final_stat = os.lstat(str(output))
        if (
            not stat.S_ISREG(final_stat.st_mode)
            or (final_stat.st_dev, final_stat.st_ino) != published_identity
            or final_stat.st_size != accepted_stat.st_size
            or final_stat.st_nlink != 1
            or package_local_dmg.sha256_file(output) != output_digest
        ):
            raise PipelineError("published DMG changed after durable placement")
        mounted_runtime = dict(mounted_runtime)
        mounted_runtime["published_dmg"] = str(output)
        mounted_runtime["published_same_inode"] = True
        mounted_runtime["publication"] = {
            "commit_boundary": "descriptor-pinned output-parent fsync",
            "candidate_unlinked_after_commit": True,
            "final_link_count": final_stat.st_nlink,
            "private_root_cleanup_complete": private_root_cleanup_complete,
            "persistent_recovery_journal": False,
        }
        if post_commit_cleanup_warnings:
            mounted_runtime["publication"]["cleanup_warnings"] = list(
                post_commit_cleanup_warnings
            )
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
        if publication_committed:
            if isinstance(original_error, (KeyboardInterrupt, SystemExit)):
                raise
            raise PipelineError(
                "DMG publication crossed its durable commit boundary; the final "
                "inode was retained after the later failure: original={!r}{}".format(
                    original_error,
                    (
                        "; cleanup={}".format("; ".join(cleanup_errors))
                        if cleanup_errors
                        else ""
                    ),
                )
            ) from original_error
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
        "adopt-home-alias",
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
        "finalize-resumed-arm64",
        "stage-arm64",
        "prepare-fresh-x64",
        "build-x64",
        "finalize-resumed-x64",
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
        if name == "prepare-fresh-x64":
            child.add_argument(
                "--confirm-exact-legacy-move", action="store_true"
            )
        if name == "adopt-home-alias":
            child.add_argument("--logical-home", required=True)
            child.add_argument("--logical-workspace-root", required=True)
            child.add_argument("--confirm-home-alias", action="store_true")
        if name in ("finalize-resumed-arm64", "finalize-resumed-x64"):
            child.add_argument("--resume-record", required=True)
            child.add_argument("--confirm-resumed-slice", action="store_true")
        if name == "merge-sign-package":
            child.add_argument("--dmg-output", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        source_input = Path(
            os.path.abspath(os.path.expanduser(args.source_root))
        )
        source_is_alias = source_input.resolve(strict=True) != source_input
        source = resolve_source(
            args.source_root,
            allow_recorded_home_alias=(
                source_is_alias and args.command != "adopt-home-alias"
            ),
            allow_unrecorded_home_alias=(
                source_is_alias and args.command == "adopt-home-alias"
            ),
        )
        developer_dir = None
        if hasattr(args, "developer_dir"):
            supplied_developer = Path(
                os.path.abspath(os.path.expanduser(args.developer_dir))
            )
            physical_developer = supplied_developer.resolve(strict=True)
            developer_contract(physical_developer)
            developer_dir = (
                supplied_developer if source_is_alias else physical_developer
            )
        if source_is_alias and args.command != "adopt-home-alias":
            alias_receipt = load_json(
                source / HOME_ALIAS_RECEIPT, "home-alias receipt"
            )
            alias_developer = developer_dir
            if alias_developer is None:
                alias_developer = Path(
                    alias_receipt.get("mappings", {})
                    .get("developer", {})
                    .get("logical", "")
                )
            home_alias_receipt_contract(source, alias_developer)
        if args.command == "adopt-home-alias":
            logical_home = Path(
                os.path.abspath(os.path.expanduser(args.logical_home))
            )
            logical_workspace = Path(
                os.path.abspath(os.path.expanduser(args.logical_workspace_root))
            )
            plan = home_alias_plan(
                source, developer_dir, logical_home, logical_workspace
            )
            result = (
                execute_home_alias(
                    source,
                    developer_dir,
                    logical_home,
                    logical_workspace,
                    plan,
                    args.confirm_home_alias,
                )
                if args.execute
                else plan
            )
        elif args.command == "bootstrap-tools":
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
        elif args.command == "finalize-resumed-arm64":
            plan = resumed_slice_plan(
                source, developer_dir, "arm64", args.resume_record
            )
            result = (
                execute_resumed_slice(
                    source,
                    developer_dir,
                    "arm64",
                    args.resume_record,
                    plan,
                    args.confirm_resumed_slice,
                )
                if args.execute
                else plan
            )
        elif args.command == "stage-arm64":
            plan = stage_arm_plan(source)
            result = (
                execute_stage_arm(source, plan, args.allow_reclaim_arm64_out)
                if args.execute
                else plan
            )
        elif args.command == "prepare-fresh-x64":
            plan = fresh_x64_preparation_plan(source, developer_dir)
            result = (
                execute_fresh_x64_preparation(
                    source,
                    developer_dir,
                    plan,
                    args.confirm_exact_legacy_move,
                )
                if args.execute
                else plan
            )
        elif args.command == "build-x64":
            plan = build_plan(source, developer_dir, "x64")
            result = execute_build(source, developer_dir, plan) if args.execute else plan
        elif args.command == "finalize-resumed-x64":
            plan = resumed_slice_plan(
                source, developer_dir, "x64", args.resume_record
            )
            result = (
                execute_resumed_slice(
                    source,
                    developer_dir,
                    "x64",
                    args.resume_record,
                    plan,
                    args.confirm_resumed_slice,
                )
                if args.execute
                else plan
            )
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
