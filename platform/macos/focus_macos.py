#!/usr/bin/env python3
"""Validate and print a native Focus Browser universal macOS build plan.

This first-stage tool is intentionally read-only. It has no download, patch,
copy, delete, build, signing, publishing, or shutdown operation.
"""

import argparse
import hashlib
import json
import platform
import plistlib
import re
import shlex
import shutil
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

from icon_contract import (
    FOCUS_ICNS_SHA256,
    IconContractError,
    inspect_icns,
    inspect_png,
    validate_focus_icns,
)


MACOS_DIR = Path(__file__).resolve().parent
REPO_ROOT = MACOS_DIR.parent.parent

PINNED_CHROMIUM_VERSION = "150.0.7871.128"
PINNED_XCODE_VERSION = "27.0"
PINNED_XCODE_BUILD = "27A5228h"
PINNED_MACOS_SDK_VERSION = "27.0"
PINNED_MACOS_SDK_BUILD = "26A5388f"
PINNED_MACOS_SDK_CANONICAL_NAME = "macosx27.0"
PINNED_MACOS_MINIMUM = "12.0"
PINNED_CHROMIUM_MAC_SDK_GNI_SHA256 = (
    "faab8ecd3da90f31bff07d03b847731e4e8d310a10d212720e320edafd946542"
)
PINNED_CHROMIUM_UNIVERSALIZER_SHA256 = (
    "c514adedd2dbd04532d3ddd95ded3ec1bd129ba81570b1f68ddad2a21bed7ab0"
)
BUNDLE_ID = "com.focusbrowser.browser"
CANONICAL_ICON_SHA256 = (
    "0492cd1a9fca0f6e658910c85a21ea854f6a8494dc67b6f95998cd91f953f3a5"
)
EXPECTED_EXCLUDED_OVERLAY_FILES = 15
COMMON_SERIES_SHA256 = (
    "19024bfebaad5f41feb9b656b4bcb5938297a49008a114e65d3a9109c2dbde97"
)
FILTERED_COMMON_SERIES_SHA256 = (
    "18743abf37930f84f6ba31cde63e2d887a99cd997794189ec50eb0cfc1dd11ae"
)
COMMON_SERIES_ENTRY_COUNT = 323

INCOGNITO_PATCHES = (
    (
        "ungoogled-chromium/add-flag-for-custom-ntp.patch",
        67,
        "572c8a93907b540912a5de7d954687f24031e7edfc376e534be2587bf66f515d",
    ),
    (
        "ungoogled-chromium/add-flag-for-incognito-themes.patch",
        75,
        "23ade5049b9356a69178f61ce07f61e38d91a4ab5f90eb1491cc593df2d431ba",
    ),
    (
        "ungoogled-chromium/add-flag-to-increase-incognito-storage-quota.patch",
        83,
        "172169e80ee346be08cc790a649b5b2fa930983effac1e17222ec16f55fe4d5e",
    ),
    (
        "focus/core/keyboard-shortcuts.patch",
        113,
        "ae26f79f89a2d57b2bbad5029a31a112778c5179dace40dd2c20f6b4cd2b3f9a",
    ),
    (
        "focus/core/ublock-reconfigure-defaults.patch",
        149,
        "e9ad27321506b90329f4323758cb871c9257c132d2356e852a4ec31cd910293d",
    ),
    (
        "focus/core/increase-incognito-storage-quota.patch",
        152,
        "6e9924ded8f370633f4d99d7bb8d393ccc71c77225c0aed5cc546742c5485724",
    ),
    (
        "focus/core/custom-keyboard-shortcuts.patch",
        177,
        "1146bce5b289d19088b6d58b89c6d554ee309d1337ebf6a55576720c57aeb674",
    ),
    (
        "focus/ui/clean-incognito-guest-ntp.patch",
        282,
        "edc49fda683020279f4e34d55d6cd155e2899a69c48592552cb78aed7c1b146a",
    ),
    (
        "focus/ui/custom-keyboard-shortcuts-wiring.patch",
        290,
        "3f3a6fff6b2b7d63971b53e5775de5d2a576d6dfae8853081b2e00e23dfb48a5",
    ),
)

INCOGNITO_OVERLAY_HASHES = {
    "chrome/app/chrome_command_ids.h":
        "aa2fcef9dd1e150f3917f28ab2d02e32aa076576d112799fb8b4d2451b96e16f",
    "chrome/app/generated_resources.grd":
        "8b0738b0f21ff6b8492850c4bbc8c4bbe291a20f720c53ef8070064d9c1a129e",
    "chrome/browser/chrome_content_browser_client.cc":
        "ad2e820a3e194e98110159417b4a5f334dc3ce7b66e852c9384572f4b9e6ba4b",
    "extensions/browser/extension_prefs.cc":
        "0db396656bc3a5f5cd7df5840e1d3b3397186af6cc7c48237d37d376fb5058ae",
    "chrome/browser/focus_block/focus_block_service_factory.cc":
        "31bbf15861c9c851d3114dbfa8257935bd8cb09d7b434f340e7d231ea1637c83",
    "chrome/browser/ui/browser_command_controller.cc":
        "f505c5fce51d2e53db1a5b94e088ff8d006e784ba840aa29da35d604c5cc6692",
    "chrome/browser/ui/toolbar/app_menu_model.cc":
        "507d4f66d686e0c833c4b188242a803114f0d16fda4f18adbb78ecd02ee6c654",
    "chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.cc":
        "cbdcc0be9c65e9db339cec5ffe33a2fbc58cd2a9c446f848c8c92b6ddbba7162",
    "chrome/browser/ui/views/toolbar/toolbar_view.cc":
        "72bc801261b3f6ae8d3fcdf4d045f74d65de22ee33856b0f962dc46a744c27c6",
    "extensions/browser/extension_util.cc":
        "11f64b40e93b92e1624d3ecf521f50fe99a23f632abe7b887374cb1b9f8ae9fb",
    "third_party/focus_youtube/manifest.json":
        "3d2abea38ac141e4f40a1ab28ca591fd51fd19cc772827ab5f6dcc5a1842bf05",
    "components/policy/core/common/focus_opinionated_policy_provider.cc":
        "e428e3d1ea42c9966ec5fa8eb6f4c36b87d156d565d6b598ad0395676b2e3062",
}

COMMON_FLAGS = REPO_ROOT / "focus-chromium" / "flags.gn"
MACOS_FLAGS = {
    "arm64": MACOS_DIR / "flags.arm64.gn",
    "x64": MACOS_DIR / "flags.x64.gn",
}
DEFAULT_OUT_DIRS = {
    "arm64": "out/FocusMacArm64",
    "x64": "out/FocusMacX64",
    "universal": "out/FocusMacUniversal",
}
CHROMIUM_MAC_SDK_GNI = "build/config/mac/mac_sdk.gni"
CHROMIUM_UNIVERSALIZER = "chrome/installer/mac/universalizer.py"
COMMON_SERIES = REPO_ROOT / "focus-chromium" / "patches" / "series"
PLATFORM_PATCH_DIR = MACOS_DIR / "patches"
PLATFORM_SERIES = PLATFORM_PATCH_DIR / "series"
OVERLAY_ROOT = REPO_ROOT / "source_overrides"
OVERLAY_EXCLUDES = MACOS_DIR / "overlay-excludes.txt"
FOCUS_ICNS = MACOS_DIR / "resources" / "FocusBrowser.icns"

EXPECTED_PATCHES = (
    (
        Path("patches/focus/windows/focusblock-location-bar-shield.patch"),
        "007693b8afa0295f9dea73a10eea7e2999a0971115e529419f292a85de6da1f9",
    ),
    (
        Path("patches/focus/windows/focusyoutube-native-popup.patch"),
        "3dfc8b8b605d036bbfd834c350fb8e2d68c579d84faca69703eed55236bda18a",
    ),
    (
        Path("platform/macos/patches/native-incognito-contract.patch"),
        "7b537baeb77019270aa183908529ba2236e9ba5bda436c3562b6f7c4173cd199",
    ),
)

EXPECTED_EXCLUDE_PREFIXES = (
    "chrome/app/theme/chromium/win/",
    "chrome/browser/win/",
    "chrome/installer/",
)

SHARED_SERIES_EXCLUSIONS = (
    "focus/core/windows-first-run-locale.patch",
    "focus/ui/fix-windows-ui-position.patch",
)

COMMON_EXCLUSION_POSITIONS = {
    "focus/core/windows-first-run-locale.patch": 97,
    "focus/ui/fix-windows-ui-position.patch": 315,
}

REQUIRED_COMMON_PATCHES = (
    (
        "focus/core/change-chromium-branding.patch",
        102,
        "e54090550c39ebc8295e46aeff61429d8bf425401c0fbad3b9b37336dab94fb7",
    ),
    (
        "focus/core/add-focus-versioning.patch",
        132,
        "30aaa7aa1feea2395041dac7635e49b6d98136daeef6f887b4d77e282966ad0a",
    ),
    (
        "focus/core/ublock-setup-sources.patch",
        147,
        "144d8c911352f5916a7182dc55c1e605a449f55178d39793d2526219326ee6e5",
    ),
    (
        "focus/core/ublock-install-as-component.patch",
        148,
        "85d0c23c1423678a404465fd3219bbd6e0efc648fd1fa2d612db5916c8f4e628",
    ),
    (
        "focus/core/ublock-reconfigure-defaults.patch",
        149,
        "e9ad27321506b90329f4323758cb871c9257c132d2356e852a4ec31cd910293d",
    ),
    (
        "focus/core/ublock-focus-services.patch",
        150,
        "1ae42d5e1757ef359a29058e67a6cdb1bc0755efabdc6e3c48f6e57ec1b2c084",
    ),
    (
        "focus/ui/ublock-show-in-settings.patch",
        286,
        "8feb876d728b6d49422da743e768285e1404f41bca1e3612508b528a9d4e7b99",
    ),
    (
        "focus/core/focusblock-native-service.patch",
        319,
        "b03926e51ba69392f40146ad35d24a10a2c33aa78d9335bb37fddc12acf8e3b6",
    ),
)

FOCUS_FEATURE_HASHES = {
    "chrome/browser/focus_block/focus_block_service.cc":
        "30b288518aaa1fcf62129deb310c7979dd8afcfcb141cc3de4b0a31c9030ff74",
    "chrome/browser/focus_block/focus_block_service.h":
        "9a953121223e60d28f7af13c0e2a5bc34dddff421189e9849f19626b372fcde2",
    "chrome/browser/focus_block/focus_block_url_loader_factory.cc":
        "f4497e5024c63723249d35215c7a417a32d267bae54a4fe1d719994101503d8a",
    "chrome/browser/focus_block/BUILD.gn":
        "7b7050535fd82b57b174c9e69ed14a2277e07536789563d7fdee255671a1fff8",
    "chrome/browser/focus_block/focus_block_ghostery_engine.cc":
        "cb6eed64a391574f23e993fa50bde0f1e8fcb3cf7f1512343da93bfea5f7ee47",
    "chrome/browser/focus_block/focus_block_ghostery_engine.h":
        "15ed842b1c486e7e57eddbae3843b6e92a91004c6e9eef46d1cd22181c6cdebd",
    "chrome/browser/ui/views/location_bar/focus_block_bubble_view.cc":
        "250f0a755414020f37799d72d0ddc8477bffed901f8b2e18804f932ea0ea400b",
    "chrome/browser/ui/views/location_bar/focus_block_bubble_view.h":
        "5a32e327b10fdad3a9e3a7027e51cbc75fe8f3fa456589420641188ca25d2d18",
    "chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.cc":
        "cbdcc0be9c65e9db339cec5ffe33a2fbc58cd2a9c446f848c8c92b6ddbba7162",
    "chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.h":
        "6cba91267a85914a9ddd4195fd61070ef60d16b741445e964b160aba14b0d41f",
    "components/focus_services/extension_ids.h":
        "79fa0c35d8c6667b7aa339fd63d0ea874512b96ad334cd27efcc08562262594a",
    "third_party/focus_youtube/manifest.json":
        "3d2abea38ac141e4f40a1ab28ca591fd51fd19cc772827ab5f6dcc5a1842bf05",
    "third_party/focus_youtube/BUILD.gn":
        "8e657e38720045b4cf40c3b9a0d58b6e4b4240661cff7842c2673474a29741d9",
    "chrome/browser/extensions/component_loader.cc":
        "275e3fc7dd2b358498871db9a90119821cda9e0b19ca14438318b83a5d8e6913",
    "chrome/browser/extensions/chrome_component_extension_resource_manager.cc":
        "0871df7b3c6e26dab7cf540c3f637e91f7e834ec2c0efdaf9744a0709103295f",
    "chrome/browser/extensions/component_extensions_allowlist/allowlist.cc":
        "d7e30b52697018d692f7c64add6801b21271042529e1791e0db8ae6d031ed26c",
    "chrome/browser/ui/views/toolbar/toolbar_view.cc":
        "72bc801261b3f6ae8d3fcdf4d045f74d65de22ee33856b0f962dc46a744c27c6",
    "chrome/browser/ui/BUILD.gn":
        "12e3bb979a8693a1e272259cc0b78d76f87bf039a997ba5dbc56a36e0058b760",
    "chrome/browser/extensions/BUILD.gn":
        "c851e51045ac0d37b7c61a80b317669f7fde0ebd4750cfcd05f0cc7a4e076708",
}

FOCUS_YOUTUBE_STORAGE_KEYS = (
    "remove_homepage",
    "remove_entire_sidebar",
    "remove_sidebar",
    "remove_chat",
    "remove_playlist_panel",
    "remove_all_shorts",
    "remove_end_of_video",
    "remove_info_cards",
    "remove_mixes",
    "remove_video_metadata",
    "disable_autoplay",
    "disable_annotations",
    "remove_comments",
    "remove_comment_profiles",
    "remove_merch_shelves",
    "remove_menu_buttons",
    "remove_channel_owner",
    "remove_vid_description",
    "remove_top_header",
    "remove_notif_bell",
    "remove_extra_results",
    "remove_trending_page",
    "remove_explore_link",
    "remove_explore_section",
    "remove_more_section",
    "remove_subscriptions_page",
    "remove_subscriptions_link",
    "remove_sub_section",
    "redirect_to_subs",
)

FOCUS_VIEW_SENTINELS = (
    "chrome/browser/ui/views/location_bar/focus_block_bubble_view.cc",
    "chrome/browser/ui/views/location_bar/focus_block_bubble_view.h",
    "chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.cc",
    "chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.h",
)

SOURCE_SENTINELS = (
    "BUILD.gn",
    "chrome/VERSION",
    "chrome/browser",
    "components",
    "third_party",
)

INCOGNITO_PRIVACY_EN = (
    "After all Incognito windows are closed, Focus Browser does not retain "
    "browsing history, cookies and site data, or information entered in forms "
    "in your browser profile. Downloads and bookmarks stay on this device. "
    "Websites, your network or internet provider, and administrators of a "
    "managed device may still observe your activity."
)
INCOGNITO_PRIVACY_RU = (
    "После закрытия всех окон инкогнито Focus Browser не сохраняет в профиле "
    "историю просмотров, файлы cookie и данные сайтов, а также сведения, "
    "введённые в формы в этой сессии. Загрузки и закладки остаются на этом "
    "устройстве. Сайты, ваша сеть или интернет-провайдер и администраторы "
    "управляемого устройства всё ещё могут видеть вашу активность."
)

PROTECTED_PATCH_TARGETS = frozenset(
    {
        "chrome/browser/chrome_content_browser_client.cc",
        "chrome/browser/extensions/component_loader.cc",
        "chrome/browser/global_keyboard_shortcuts_mac.mm",
        "chrome/browser/history/history_tab_helper.cc",
        "chrome/browser/profiles/off_the_record_profile_impl.cc",
        "chrome/browser/prefs/incognito_mode_prefs.cc",
        "chrome/browser/prefs/pref_service_syncable_util.cc",
        "chrome/browser/resources/new_tab_page_incognito_guest/incognito_tab.html",
        "chrome/browser/sessions/session_restore.cc",
        "chrome/browser/sessions/session_service_factory.cc",
        "chrome/browser/ui/browser_command_controller.cc",
        "chrome/browser/ui/browser_commands.cc",
        "chrome/browser/ui/browser_shortcuts/browser_shortcut_metadata.cc",
        "chrome/browser/ui/cocoa/accelerators_cocoa.mm",
        "chrome/browser/ui/incognito_allowed_url.cc",
        "chrome/browser/ui/toolbar/app_menu_model.cc",
        "chrome/browser/ui/views/frame/browser_widget.cc",
        "chrome/browser/ui/views/frame/browser_view.cc",
        "chrome/browser/ui/views/toolbar/toolbar_view.cc",
        "chrome/browser/ui/webui/ntp/ntp_resource_cache.cc",
        "components/new_or_sad_tab_strings.grdp",
        "content/browser/browser_context.cc",
        "content/browser/storage_partition_impl_map.cc",
        "extensions/browser/extension_prefs.cc",
        "extensions/browser/extension_util.cc",
        "storage/browser/quota/quota_features.cc",
    }
)
PROTECTED_PATCH_PATTERN = re.compile(
    r"incognito|off[_ -]?the[_ -]?record|getprimaryotrprofile|\botr\b|"
    r"custom-ntp|ProfileSelection::k(?:OriginalOnly|OwnInstance|"
    r"RedirectedToOriginal|OffTheRecordOnly)",
    re.IGNORECASE,
)
EXPECTED_PROTECTED_PATCH_COUNT = 59
EXPECTED_PROTECTED_PATCH_SHA256 = (
    "7d6b2eb80652ba32a8f24e8f0686c132e88380b7a4fca085af753793808b3001"
)
EXPECTED_FULL_PATCH_BODY_COUNT = 321
EXPECTED_FULL_PATCH_BODY_SHA256 = (
    "86281ce7822db3e8880422ac5cdfc0b0ae46e43090663afa0f8060dc07ece9c2"
)
PROTECTED_OVERLAY_TARGETS = frozenset(
    set(PROTECTED_PATCH_TARGETS)
    | set(INCOGNITO_OVERLAY_HASHES)
    | {"components/policy/core/common/focus_opinionated_policy_provider.cc"}
)
EXPECTED_PROTECTED_OVERLAY_COUNT = 72
EXPECTED_PROTECTED_OVERLAY_SHA256 = (
    "7903ff45ba87700e9b7b253f06706cf3e1d2e99062f8b2e338eb5981aa589f36"
)
EXPECTED_FULL_OVERLAY_BODY_COUNT = 2531
EXPECTED_FULL_OVERLAY_BODY_SHA256 = (
    "d4b9b13a9d82d6b5e4e9a1c73891c0a818b4e1c61dbcfc4a5000209ad151f48e"
)

CHROMIUM_INCOGNITO_SOURCE_CONTRACTS = {
    "chrome/browser/profiles/off_the_record_profile_impl.cc": (
        "CreateIncognitoPrefServiceSyncable(",
        "ShutdownStoragePartitions();",
        "OffTheRecordProfileImpl::GetProfileUserName() const",
        "return std::string();",
    ),
    "chrome/browser/profiles/profile_impl.cc": (
        "otr_profiles_.erase(profile_id);",
        "ClearAllIncognitoSessionOnlyPreferences();",
    ),
    "chrome/browser/ui/browser_commands.cc": (
        "void NewIncognitoWindow(Profile* profile)",
        "GetPrimaryOTRProfile(",
        "true)",
    ),
    "chrome/browser/history/history_tab_helper.cc": (
        "if (profile->IsOffTheRecord())",
        "return nullptr;",
    ),
    "chrome/browser/sessions/session_service_factory.cc": (
        '"SessionService"',
        ".WithRegular(ProfileSelection::kOriginalOnly)",
    ),
}


class ContractError(RuntimeError):
    """Raised when a pinned build contract is not satisfied."""


def sha256_file(path):
    """Return a file's SHA-256 without invoking external tools."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_series(path):
    """Read a GNU Quilt series file, ignoring blank/comment-only lines."""
    if not path.is_file():
        raise ContractError("missing series file: {}".format(path))
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            entries.append(value.split()[0])
    return entries


def require_regular_tree_file(root, relative, label):
    """Return a regular in-tree file while rejecting traversal and symlinks."""
    root_path = Path(root).absolute()
    pure = PurePosixPath(relative)
    if (
        relative in ("", ".")
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in relative
    ):
        raise ContractError("unsafe {} path: {!r}".format(label, relative))
    if root_path.is_symlink() or not root_path.is_dir():
        raise ContractError(
            "{} root must be a real directory: {}".format(label, root_path)
        )

    candidate = root_path.joinpath(*pure.parts)
    cursor = candidate
    while cursor != root_path:
        if cursor.is_symlink():
            raise ContractError(
                "{} must not be a symlink: {}".format(label, candidate)
            )
        cursor = cursor.parent
    if not candidate.is_file():
        raise ContractError("missing {}: {}".format(label, candidate))
    try:
        candidate.resolve().relative_to(root_path.resolve())
    except ValueError as exc:
        raise ContractError("{} escaped its root: {}".format(label, candidate)) from exc
    return candidate


def iter_overlay_regular_files(overlay_root):
    """Yield every real overlay file and reject symlinks or special files."""
    root = Path(overlay_root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ContractError("overlay root must be a real directory: {}".format(root))
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ContractError("overlay must not contain symlinks: {}".format(path))
        if path.is_dir():
            continue
        if not path.is_file():
            raise ContractError("overlay contains a non-regular file: {}".format(path))
        yield path


def diff_header_path(line):
    """Return a repository-relative path from a unified-diff file header."""
    value = line[4:].split("\t", 1)[0]
    if value == "/dev/null":
        return None
    if value.startswith(("a/", "b/")):
        value = value[2:]
    pure = PurePosixPath(value)
    if (
        value in ("", ".")
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in value
    ):
        raise ContractError("unsafe unified-diff path: {!r}".format(value))
    return pure.as_posix()


def validate_unified_diff_syntax(patch_path):
    """Parse file pairs and prove every unified-diff hunk count is exact."""
    if patch_path.is_symlink():
        raise ContractError("patch file must not be a symlink: {}".format(patch_path))
    if not patch_path.is_file():
        raise ContractError("missing patch file: {}".format(patch_path))
    lines = patch_path.read_text(encoding="utf-8").splitlines()
    hunk_header = re.compile(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
    )
    file_pairs = []
    file_hunks = []
    current_file = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("--- "):
            if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
                raise ContractError(
                    "unpaired unified-diff header in {} at line {}".format(
                        patch_path, index + 1
                    )
                )
            old_path = diff_header_path(line)
            new_path = diff_header_path(lines[index + 1])
            if old_path is None and new_path is None:
                raise ContractError("unified diff cannot map /dev/null to itself")
            file_pairs.append((old_path, new_path))
            file_hunks.append(0)
            current_file = len(file_pairs) - 1
            index += 2
            continue
        if line.startswith("@@ "):
            if current_file is None:
                raise ContractError("unified-diff hunk appears before a file header")
            match = hunk_header.fullmatch(line)
            if not match:
                raise ContractError(
                    "invalid unified-diff hunk header in {}: {!r}".format(
                        patch_path, line
                    )
                )
            expected_old = int(match.group(2) or "1")
            expected_new = int(match.group(4) or "1")
            actual_old = 0
            actual_new = 0
            file_hunks[current_file] += 1
            index += 1
            while index < len(lines) and not lines[index].startswith(("@@ ", "--- ")):
                body_line = lines[index]
                if body_line == r"\ No newline at end of file":
                    index += 1
                    continue
                if body_line.startswith(" "):
                    actual_old += 1
                    actual_new += 1
                elif body_line.startswith("-") and not body_line.startswith("---"):
                    actual_old += 1
                elif body_line.startswith("+") and not body_line.startswith("+++"):
                    actual_new += 1
                else:
                    raise ContractError(
                        "invalid unified-diff hunk body in {} at line {}".format(
                            patch_path, index + 1
                        )
                    )
                index += 1
            if (actual_old, actual_new) != (expected_old, expected_new):
                raise ContractError(
                    "unified-diff hunk count mismatch in {}: expected {}/{}, "
                    "got {}/{}".format(
                        patch_path,
                        expected_old,
                        expected_new,
                        actual_old,
                        actual_new,
                    )
                )
            continue
        if line.startswith(("+++ ", "+", "-", " ")):
            raise ContractError(
                "unscoped unified-diff body in {} at line {}".format(
                    patch_path, index + 1
                )
            )
        index += 1

    if not file_pairs or any(count == 0 for count in file_hunks):
        raise ContractError("unified diff must contain a hunk for every file pair")
    return file_pairs


def build_full_patch_body_inventory(entries, patch_root, excluded=()):
    """Hash every planned patch body, independent of semantic heuristics."""
    excluded = set(excluded)
    records = []
    for position, entry in enumerate(entries, 1):
        if entry in excluded:
            continue
        patch_path = require_regular_tree_file(
            patch_root, entry, "planned common patch"
        )
        records.append(
            {
                "position": position,
                "path": entry,
                "sha256": sha256_file(patch_path),
            }
        )
    manifest = "".join(
        "{position}\t{path}\t{sha256}\n".format(**record) for record in records
    ).encode("utf-8")
    return {
        "count": len(records),
        "manifest_bytes": len(manifest),
        "sha256": hashlib.sha256(manifest).hexdigest(),
    }


def validate_full_patch_body_inventory(entries):
    """Fail when any planned common patch body changes, including indirect edits."""
    report = build_full_patch_body_inventory(
        entries, COMMON_SERIES.parent, SHARED_SERIES_EXCLUSIONS
    )
    if (
        report["count"] != EXPECTED_FULL_PATCH_BODY_COUNT
        or report["sha256"] != EXPECTED_FULL_PATCH_BODY_SHA256
    ):
        raise ContractError(
            "full common patch body inventory changed: expected {}/{}, got "
            "{}/{}".format(
                EXPECTED_FULL_PATCH_BODY_COUNT,
                EXPECTED_FULL_PATCH_BODY_SHA256,
                report["count"],
                report["sha256"],
            )
        )
    return report


def build_protected_patch_inventory(entries, patch_root, excluded=()):
    """Hash the informational subset with direct Off-The-Record touches."""
    excluded = set(excluded)
    records = []
    for position, entry in enumerate(entries, 1):
        if entry in excluded:
            continue
        patch_path = require_regular_tree_file(
            patch_root, entry, "planned common patch"
        )
        lines = patch_path.read_text(encoding="utf-8").splitlines()
        targets = set()
        changed_lines = []
        for line in lines:
            if line.startswith(("--- ", "+++ ")):
                target = diff_header_path(line)
                if target is not None:
                    targets.add(target)
            elif line.startswith(("+", "-")):
                changed_lines.append(line[1:])
        semantic_hit = bool(PROTECTED_PATCH_PATTERN.search(entry)) or any(
            PROTECTED_PATCH_PATTERN.search(line) for line in changed_lines
        )
        if targets.intersection(PROTECTED_PATCH_TARGETS) or semantic_hit:
            records.append(
                {
                    "position": position,
                    "path": entry,
                    "sha256": sha256_file(patch_path),
                }
            )
    manifest = "".join(
        "{position}\t{path}\t{sha256}\n".format(**record) for record in records
    ).encode("utf-8")
    return {
        "count": len(records),
        "manifest_bytes": len(manifest),
        "sha256": hashlib.sha256(manifest).hexdigest(),
        "patches": records,
    }


def validate_protected_patch_inventory(entries):
    """Fail when an unreviewed common patch can alter private-mode behavior."""
    report = build_protected_patch_inventory(
        entries, COMMON_SERIES.parent, SHARED_SERIES_EXCLUSIONS
    )
    if (
        report["count"] != EXPECTED_PROTECTED_PATCH_COUNT
        or report["sha256"] != EXPECTED_PROTECTED_PATCH_SHA256
    ):
        raise ContractError(
            "protected Incognito patch inventory changed: expected {}/{}, got "
            "{}/{}".format(
                EXPECTED_PROTECTED_PATCH_COUNT,
                EXPECTED_PROTECTED_PATCH_SHA256,
                report["count"],
                report["sha256"],
            )
        )
    return report


def build_full_overlay_body_inventory(overlay_root, excluded_prefixes=()):
    """Hash every included regular overlay file, without semantic filtering."""
    records = []
    root = Path(overlay_root).absolute()
    for overlay_path in iter_overlay_regular_files(root):
        relative = overlay_path.relative_to(root).as_posix()
        if relative == "delete.txt" or is_overlay_excluded(
            relative, excluded_prefixes
        ):
            continue
        records.append({"path": relative, "sha256": sha256_file(overlay_path)})
    manifest = "".join(
        "{path}\t{sha256}\n".format(**record) for record in records
    ).encode("utf-8")
    return {
        "count": len(records),
        "manifest_bytes": len(manifest),
        "sha256": hashlib.sha256(manifest).hexdigest(),
    }


def validate_full_overlay_body_inventory():
    """Fail when any included overlay body changes, including indirect edits."""
    report = build_full_overlay_body_inventory(
        OVERLAY_ROOT, read_exclude_prefixes()
    )
    if (
        report["count"] != EXPECTED_FULL_OVERLAY_BODY_COUNT
        or report["sha256"] != EXPECTED_FULL_OVERLAY_BODY_SHA256
    ):
        raise ContractError(
            "full included overlay body inventory changed: expected {}/{}, got "
            "{}/{}".format(
                EXPECTED_FULL_OVERLAY_BODY_COUNT,
                EXPECTED_FULL_OVERLAY_BODY_SHA256,
                report["count"],
                report["sha256"],
            )
        )
    return report


def build_protected_overlay_inventory(overlay_root, excluded_prefixes=()):
    """Hash the informational subset with direct private-mode touches."""
    records = []
    root = Path(overlay_root).absolute()
    for overlay_path in iter_overlay_regular_files(root):
        relative = overlay_path.relative_to(root).as_posix()
        if relative == "delete.txt" or is_overlay_excluded(
            relative, excluded_prefixes
        ):
            continue
        path_hit = (
            relative in PROTECTED_OVERLAY_TARGETS
            or bool(PROTECTED_PATCH_PATTERN.search(relative))
        )
        content_hit = False
        try:
            text = overlay_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            content_hit = bool(PROTECTED_PATCH_PATTERN.search(text))
        if path_hit or content_hit:
            records.append({"path": relative, "sha256": sha256_file(overlay_path)})
    manifest = "".join(
        "{path}\t{sha256}\n".format(**record) for record in records
    ).encode("utf-8")
    return {
        "count": len(records),
        "manifest_bytes": len(manifest),
        "sha256": hashlib.sha256(manifest).hexdigest(),
        "files": records,
    }


def validate_protected_overlay_inventory():
    """Fail when a new source overlay can silently weaken private mode."""
    report = build_protected_overlay_inventory(OVERLAY_ROOT, read_exclude_prefixes())
    if (
        report["count"] != EXPECTED_PROTECTED_OVERLAY_COUNT
        or report["sha256"] != EXPECTED_PROTECTED_OVERLAY_SHA256
    ):
        raise ContractError(
            "protected Incognito overlay inventory changed: expected {}/{}, got "
            "{}/{}".format(
                EXPECTED_PROTECTED_OVERLAY_COUNT,
                EXPECTED_PROTECTED_OVERLAY_SHA256,
                report["count"],
                report["sha256"],
            )
        )
    return report


def read_exclude_prefixes():
    """Read and strictly validate safe, repository-relative path prefixes."""
    if not OVERLAY_EXCLUDES.is_file():
        raise ContractError("missing overlay exclusions: {}".format(OVERLAY_EXCLUDES))
    prefixes = []
    for line in OVERLAY_EXCLUDES.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        pure = PurePosixPath(value)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or "\\" in value
            or not value.endswith("/")
        ):
            raise ContractError("unsafe overlay exclusion: {!r}".format(value))
        prefixes.append(value)
    if tuple(prefixes) != EXPECTED_EXCLUDE_PREFIXES:
        raise ContractError(
            "overlay exclusions changed: expected {!r}, got {!r}".format(
                EXPECTED_EXCLUDE_PREFIXES, tuple(prefixes)
            )
        )
    return prefixes


def is_overlay_excluded(relative_path, prefixes):
    """Return whether a POSIX relative path is under an excluded prefix."""
    value = PurePosixPath(relative_path).as_posix()
    return any(value == prefix[:-1] or value.startswith(prefix) for prefix in prefixes)


def validate_delete_manifest(prefixes):
    """Validate cleanup paths without deleting or requiring their targets."""
    manifest = OVERLAY_ROOT / "delete.txt"
    if not manifest.is_file():
        raise ContractError("missing overlay cleanup manifest: {}".format(manifest))
    planned = []
    excluded = []
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        pure = PurePosixPath(value)
        if pure.is_absolute() or ".." in pure.parts or "\\" in value:
            raise ContractError(
                "unsafe cleanup path at {}:{}: {!r}".format(manifest, number, value)
            )
        normalised = pure.as_posix()
        if normalised in ("", "."):
            raise ContractError("empty cleanup target at {}:{}".format(manifest, number))
        if is_overlay_excluded(normalised, prefixes):
            excluded.append(normalised)
        else:
            planned.append(normalised)
    if len(planned) != len(set(planned)) or len(excluded) != len(set(excluded)):
        raise ContractError("duplicate path in overlay cleanup manifest")
    return {"planned": planned, "excluded": excluded, "executed": False}


def validate_host():
    """Require a supported native macOS planning host."""
    system = platform.system()
    machine = platform.machine().lower()
    if system != "Darwin" or machine not in ("arm64", "aarch64", "x86_64"):
        raise ContractError(
            "native macOS host required; found {}/{}".format(system, machine)
        )
    return {"system": system, "machine": machine}


def read_plist_contract(path, label):
    """Read a regular property-list file without invoking Apple tools."""
    if path.is_symlink() or not path.is_file():
        raise ContractError("missing regular {} plist: {}".format(label, path))
    try:
        value = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ContractError("invalid {} plist {}: {}".format(label, path, exc)) from exc
    if not isinstance(value, dict):
        raise ContractError("{} plist root must be a dictionary: {}".format(label, path))
    return value


def require_real_developer_directory(path, developer_dir, label):
    """Require every directory component to be real and stay in Developer."""
    path = Path(path)
    try:
        path.relative_to(developer_dir)
    except ValueError as exc:
        raise ContractError("{} escaped Developer directory: {}".format(label, path)) from exc
    cursor = path
    while cursor != developer_dir:
        if cursor.is_symlink():
            raise ContractError("{} must not contain symlinks: {}".format(label, cursor))
        cursor = cursor.parent
    if not path.is_dir():
        raise ContractError("missing real {} directory: {}".format(label, path))
    try:
        path.resolve(strict=True).relative_to(developer_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContractError("{} escaped Developer directory: {}".format(label, path)) from exc
    return path


def require_executable_file(path, developer_dir, label):
    """Require an executable regular file contained by the Developer tree."""
    require_real_developer_directory(path.parent, developer_dir, "{} parent".format(label))
    if path.is_symlink() or not path.is_file() or not (path.stat().st_mode & 0o111):
        raise ContractError("missing executable {}: {}".format(label, path))
    try:
        path.resolve(strict=True).relative_to(developer_dir)
    except ValueError as exc:
        raise ContractError("{} escaped Developer directory: {}".format(label, path)) from exc
    return path


def validate_xcode_toolchain(value):
    """Validate the exact Xcode/SDK identity using plist data only."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ContractError("--developer-dir must be an explicit absolute path")
    try:
        developer_dir = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ContractError(
            "Xcode Developer directory does not exist: {}".format(candidate)
        ) from exc
    if not developer_dir.is_dir():
        raise ContractError("Xcode Developer path is not a directory: {}".format(developer_dir))
    if (
        developer_dir.name != "Developer"
        or developer_dir.parent.name != "Contents"
        or developer_dir.parent.parent.suffix != ".app"
    ):
        raise ContractError(
            "--developer-dir must resolve to an Xcode.app/Contents/Developer directory"
        )

    contents_dir = developer_dir.parent
    xcode_version_path = contents_dir / "version.plist"
    xcode_version = read_plist_contract(xcode_version_path, "Xcode version")
    expected_xcode = {
        "CFBundleShortVersionString": PINNED_XCODE_VERSION,
        "ProductBuildVersion": PINNED_XCODE_BUILD,
    }
    for key, expected in expected_xcode.items():
        actual = xcode_version.get(key)
        if actual != expected:
            raise ContractError(
                "Xcode {} mismatch: expected {}, got {!r}".format(key, expected, actual)
            )

    platforms_dir = require_real_developer_directory(
        developer_dir / "Platforms", developer_dir, "Xcode Platforms"
    )
    platform_dir = require_real_developer_directory(
        platforms_dir / "MacOSX.platform", developer_dir, "macOS platform"
    )
    platform_info_path = platform_dir / "Info.plist"
    platform_version_path = platform_dir / "version.plist"
    platform_info = read_plist_contract(platform_info_path, "macOS platform")
    platform_version = read_plist_contract(platform_version_path, "macOS platform version")
    if (
        platform_info.get("Version") != PINNED_MACOS_SDK_VERSION
        or platform_info.get("CFBundleShortVersionString") != PINNED_MACOS_SDK_VERSION
        or platform_version.get("CFBundleShortVersionString") != PINNED_MACOS_SDK_VERSION
        or platform_version.get("ProductBuildVersion") != PINNED_MACOS_SDK_BUILD
    ):
        raise ContractError("macOS platform version/build does not match the pinned SDK")

    platform_developer_dir = require_real_developer_directory(
        platform_dir / "Developer", developer_dir, "macOS platform Developer"
    )
    sdks_root = require_real_developer_directory(
        platform_developer_dir / "SDKs", developer_dir, "macOS SDK root"
    )
    sdk_alias = sdks_root / "MacOSX{}.sdk".format(PINNED_MACOS_SDK_VERSION)
    if not sdk_alias.is_symlink() or sdk_alias.readlink() != Path("MacOSX.sdk"):
        raise ContractError(
            "pinned macOS SDK alias must be MacOSX{}.sdk -> MacOSX.sdk: {}".format(
                PINNED_MACOS_SDK_VERSION, sdk_alias
            )
        )
    canonical_sdk_root = require_real_developer_directory(
        sdks_root / "MacOSX.sdk", developer_dir, "canonical macOS SDK root"
    )
    try:
        sdk_root = sdk_alias.resolve(strict=True)
        sdk_root.relative_to(sdks_root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContractError(
            "pinned macOS SDK alias is missing or escapes SDKs: {}".format(sdk_alias)
        ) from exc
    if sdk_root != canonical_sdk_root.resolve(strict=True):
        raise ContractError("pinned macOS SDK alias target changed: {}".format(sdk_alias))
    require_real_developer_directory(
        sdk_root, developer_dir, "resolved macOS SDK root"
    )

    sdk_settings_path = sdk_root / "SDKSettings.plist"
    sdk_core_services = require_real_developer_directory(
        sdk_root / "System" / "Library" / "CoreServices",
        developer_dir,
        "macOS SDK CoreServices",
    )
    sdk_system_version_path = sdk_core_services / "SystemVersion.plist"
    sdk_settings = read_plist_contract(sdk_settings_path, "macOS SDK settings")
    sdk_system_version = read_plist_contract(
        sdk_system_version_path, "macOS SDK system version"
    )
    if (
        sdk_settings.get("Version") != PINNED_MACOS_SDK_VERSION
        or sdk_settings.get("CanonicalName") != PINNED_MACOS_SDK_CANONICAL_NAME
        or sdk_system_version.get("ProductVersion") != PINNED_MACOS_SDK_VERSION
        or sdk_system_version.get("ProductBuildVersion") != PINNED_MACOS_SDK_BUILD
    ):
        raise ContractError("macOS SDK identity does not match the pinned version/build")

    supported_targets = sdk_settings.get("SupportedTargets")
    macos_target = supported_targets.get("macosx") if isinstance(supported_targets, dict) else None
    architectures = macos_target.get("Archs") if isinstance(macos_target, dict) else None
    minimum = (
        macos_target.get("MinimumDeploymentTarget")
        if isinstance(macos_target, dict)
        else None
    )
    if (
        not isinstance(architectures, list)
        or any(not isinstance(value, str) for value in architectures)
        or not {"arm64", "x86_64"}.issubset(set(architectures))
    ):
        raise ContractError("macOS SDK must advertise both arm64 and x86_64 targets")
    if minimum != PINNED_MACOS_MINIMUM:
        raise ContractError(
            "macOS SDK minimum deployment target mismatch: expected {}, got {!r}".format(
                PINNED_MACOS_MINIMUM, minimum
            )
        )

    xcodebuild = require_executable_file(
        developer_dir / "usr" / "bin" / "xcodebuild",
        developer_dir,
        "xcodebuild",
    )
    clang = require_executable_file(
        developer_dir
        / "Toolchains"
        / "XcodeDefault.xctoolchain"
        / "usr"
        / "bin"
        / "clang",
        developer_dir,
        "Xcode clang",
    )
    return {
        "developer_dir": str(developer_dir),
        "xcode": {
            "version": PINNED_XCODE_VERSION,
            "build": PINNED_XCODE_BUILD,
            "version_plist": str(xcode_version_path),
            "xcodebuild": str(xcodebuild),
        },
        "sdk": {
            "version": PINNED_MACOS_SDK_VERSION,
            "build": PINNED_MACOS_SDK_BUILD,
            "canonical_name": PINNED_MACOS_SDK_CANONICAL_NAME,
            "alias": str(sdk_alias),
            "root": str(sdk_root),
            "settings_plist": str(sdk_settings_path),
            "system_version_plist": str(sdk_system_version_path),
            "architectures": sorted(architectures),
            "minimum_deployment_target": minimum,
        },
        "clang": str(clang),
        "identity_validated": True,
        "subprocess_executed": False,
        "global_xcode_select_used": False,
        "build_compatibility_runtime_verified": False,
    }


def parse_chromium_version(version_file):
    """Parse Chromium's chrome/VERSION into a dotted four-part version."""
    if not version_file.is_file():
        raise ContractError("missing Chromium version file: {}".format(version_file))
    parts = {}
    for line in version_file.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key in ("MAJOR", "MINOR", "BUILD", "PATCH"):
            if key in parts or not value.isdigit():
                raise ContractError("invalid Chromium VERSION entry: {!r}".format(line))
            parts[key] = value
    names = ("MAJOR", "MINOR", "BUILD", "PATCH")
    if any(name not in parts for name in names):
        raise ContractError("Chromium VERSION must contain MAJOR/MINOR/BUILD/PATCH")
    return ".".join(parts[name] for name in names)


def resolve_source_root(value):
    """Resolve an explicit existing Chromium source root."""
    candidate = Path(value).expanduser()
    try:
        root = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ContractError("Chromium source root does not exist: {}".format(candidate)) from exc
    if not root.is_dir():
        raise ContractError("Chromium source root is not a directory: {}".format(root))
    missing = [str(root / item) for item in SOURCE_SENTINELS if not (root / item).exists()]
    if missing:
        raise ContractError("not a complete Chromium source root; missing: {}".format(
            ", ".join(missing)
        ))
    version = parse_chromium_version(root / "chrome" / "VERSION")
    if version != PINNED_CHROMIUM_VERSION:
        raise ContractError(
            "Chromium version mismatch: expected {}, got {}".format(
                PINNED_CHROMIUM_VERSION, version
            )
        )
    return root, version


def parse_unique_gni_string(text, name):
    """Return one exact quoted GN string assignment from a pinned source file."""
    matches = re.findall(
        r'^\s*{}\s*=\s*"([^"]+)"\s*$'.format(re.escape(name)),
        text,
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        raise ContractError("Chromium macOS contract must define {} exactly once".format(name))
    return matches[0]


def validate_chromium_macos_build_contract(source_root):
    """Pin Chromium 150's Mac minimum and official universalizer source."""
    sdk_gni = require_regular_tree_file(
        source_root, CHROMIUM_MAC_SDK_GNI, "Chromium macOS SDK contract"
    )
    universalizer = require_regular_tree_file(
        source_root, CHROMIUM_UNIVERSALIZER, "Chromium universalizer"
    )
    sdk_hash = sha256_file(sdk_gni)
    universalizer_hash = sha256_file(universalizer)
    if sdk_hash != PINNED_CHROMIUM_MAC_SDK_GNI_SHA256:
        raise ContractError(
            "Chromium mac_sdk.gni hash mismatch: expected {}, got {}".format(
                PINNED_CHROMIUM_MAC_SDK_GNI_SHA256, sdk_hash
            )
        )
    if universalizer_hash != PINNED_CHROMIUM_UNIVERSALIZER_SHA256:
        raise ContractError(
            "Chromium universalizer hash mismatch: expected {}, got {}".format(
                PINNED_CHROMIUM_UNIVERSALIZER_SHA256, universalizer_hash
            )
        )

    sdk_text = sdk_gni.read_text(encoding="utf-8")
    values = {
        name: parse_unique_gni_string(sdk_text, name)
        for name in (
            "mac_deployment_target",
            "mac_min_system_version",
            "mac_sdk_official_version",
            "mac_sdk_official_build_version",
        )
    }
    for name in ("mac_deployment_target", "mac_min_system_version"):
        if values[name] != PINNED_MACOS_MINIMUM:
            raise ContractError(
                "Chromium {} mismatch: expected {}, got {}".format(
                    name, PINNED_MACOS_MINIMUM, values[name]
                )
            )

    universalizer_text = universalizer.read_text(encoding="utf-8")
    for token in (
        "def universalize(input_paths, output_path):",
        "parser.add_argument('output'",
        "universalize(parsed.inputs, parsed.output)",
    ):
        if token not in universalizer_text:
            raise ContractError("Chromium universalizer source contract changed")
    return {
        "minimum_macos": PINNED_MACOS_MINIMUM,
        "mac_deployment_target": values["mac_deployment_target"],
        "mac_min_system_version": values["mac_min_system_version"],
        "supported_target_cpus": ["arm64", "x64"],
        "supported_mach_o_architectures": ["arm64", "x86_64"],
        "all_macs_or_macos_claimed": False,
        "upstream_official_sdk": {
            "version": values["mac_sdk_official_version"],
            "build": values["mac_sdk_official_build_version"],
        },
        "selected_sdk_is_newer_than_upstream_official": True,
        "pinned_files": {
            CHROMIUM_MAC_SDK_GNI: sdk_hash,
            CHROMIUM_UNIVERSALIZER: universalizer_hash,
        },
        "universal_output": {
            "assembly_executed": False,
            "signing_executed": False,
            "runtime_verified": False,
        },
        "build_runtime_verified": False,
    }


def validate_platform_patch_series():
    """Verify exact shared Views patch order, targets, and content hashes."""
    entries = read_series(PLATFORM_SERIES)
    if len(entries) != len(EXPECTED_PATCHES):
        raise ContractError(
            "macOS patch series must contain exactly {} patches".format(
                len(EXPECTED_PATCHES)
            )
        )

    report = []
    for position, (entry, expected) in enumerate(zip(entries, EXPECTED_PATCHES), 1):
        expected_relative, expected_hash = expected
        patch_path = (PLATFORM_PATCH_DIR / entry).resolve()
        expected_path = require_regular_tree_file(
            REPO_ROOT, expected_relative.as_posix(), "platform patch"
        ).resolve()
        if patch_path != expected_path:
            raise ContractError(
                "patch {} order/path mismatch: expected {}, got {}".format(
                    position, expected_path, patch_path
                )
            )
        actual_hash = sha256_file(patch_path)
        if actual_hash != expected_hash:
            raise ContractError(
                "patch hash mismatch for {}: expected {}, got {}".format(
                    expected_relative, expected_hash, actual_hash
                )
            )
        file_pairs = validate_unified_diff_syntax(patch_path)
        targets = {
            target
            for pair in file_pairs
            for target in pair
            if target is not None
        }
        for target in targets:
            if is_overlay_excluded(target, EXPECTED_EXCLUDE_PREFIXES):
                raise ContractError(
                    "macOS patch touches Windows-only path: {}".format(target)
                )
        if not targets:
            raise ContractError("patch has no Chromium targets: {}".format(patch_path))
        report.append(
            {
                "order": position,
                "path": expected_relative.as_posix(),
                "sha256": actual_hash,
                "target_count": len(targets),
            }
        )
    return report


def validate_common_series():
    """Pin the complete common patch graph and its filtered macOS order."""
    actual_series_hash = sha256_file(COMMON_SERIES)
    if actual_series_hash != COMMON_SERIES_SHA256:
        raise ContractError(
            "common patch series changed: expected {}, got {}".format(
                COMMON_SERIES_SHA256, actual_series_hash
            )
        )
    entries = read_series(COMMON_SERIES)
    if len(entries) != COMMON_SERIES_ENTRY_COUNT:
        raise ContractError(
            "common patch count changed: expected {}, got {}".format(
                COMMON_SERIES_ENTRY_COUNT, len(entries)
            )
        )
    duplicates = sorted(name for name, count in Counter(entries).items() if count != 1)
    if duplicates:
        raise ContractError("duplicate common patch entries: {}".format(", ".join(duplicates)))

    patch_root = COMMON_SERIES.parent
    for entry in entries:
        require_regular_tree_file(patch_root, entry, "common patch target")

    required = []
    for entry, expected_position, expected_hash in REQUIRED_COMMON_PATCHES:
        actual_position = entries.index(entry) + 1 if entry in entries else None
        if actual_position != expected_position:
            raise ContractError(
                "required common patch position changed for {}: expected {}, got {}".format(
                    entry, expected_position, actual_position
                )
            )
        patch_path = require_regular_tree_file(
            patch_root, entry, "Incognito common patch"
        )
        actual_hash = sha256_file(patch_path)
        if actual_hash != expected_hash:
            raise ContractError(
                "required common patch hash changed for {}: expected {}, got {}".format(
                    entry, expected_hash, actual_hash
                )
            )
        required.append(
            {"path": entry, "position": actual_position, "sha256": actual_hash}
        )

    for entry, expected_position in COMMON_EXCLUSION_POSITIONS.items():
        actual_position = entries.index(entry) + 1 if entry in entries else None
        if actual_position != expected_position:
            raise ContractError(
                "macOS exclusion position changed for {}: expected {}, got {}".format(
                    entry, expected_position, actual_position
                )
            )

    excluded = set(SHARED_SERIES_EXCLUSIONS)
    filtered = [entry for entry in entries if entry not in excluded]
    filtered_hash = hashlib.sha256(("\n".join(filtered) + "\n").encode("utf-8")).hexdigest()
    if len(filtered) != 321 or filtered_hash != FILTERED_COMMON_SERIES_SHA256:
        raise ContractError(
            "filtered macOS common series changed: expected 321/{}, got {}/{}".format(
                FILTERED_COMMON_SERIES_SHA256, len(filtered), filtered_hash
            )
        )
    full_body_inventory = validate_full_patch_body_inventory(entries)
    protected_inventory = validate_protected_patch_inventory(entries)
    return {
        "path": str(COMMON_SERIES.relative_to(REPO_ROOT)),
        "sha256": actual_series_hash,
        "total_entries": len(entries),
        "excluded_for_macos": list(SHARED_SERIES_EXCLUSIONS),
        "exclusion_positions": COMMON_EXCLUSION_POSITIONS,
        "planned_entries": len(filtered),
        "filtered_order_sha256": filtered_hash,
        "required_patches": required,
        "full_body_inventory": full_body_inventory,
        "protected_incognito_inventory": protected_inventory,
    }


def validate_incognito_repository_contract():
    """Pin the Focus/macOS pieces that preserve Chromium Off-The-Record mode."""
    entries = read_series(COMMON_SERIES)
    patch_root = COMMON_SERIES.parent
    patches = []
    for entry, expected_position, expected_hash in INCOGNITO_PATCHES:
        actual_position = entries.index(entry) + 1 if entry in entries else None
        if actual_position != expected_position:
            raise ContractError(
                "Incognito patch position changed for {}: expected {}, got {}".format(
                    entry, expected_position, actual_position
                )
            )
        actual_hash = sha256_file(patch_root / entry)
        if actual_hash != expected_hash:
            raise ContractError(
                "Incognito patch hash changed for {}: expected {}, got {}".format(
                    entry, expected_hash, actual_hash
                )
            )
        patches.append(
            {"path": entry, "position": actual_position, "sha256": actual_hash}
        )

    overlay_hashes = {}
    for relative, expected_hash in INCOGNITO_OVERLAY_HASHES.items():
        path = require_regular_tree_file(
            OVERLAY_ROOT, relative, "Incognito overlay file"
        )
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise ContractError(
                "Incognito overlay changed for {}: expected {}, got {}".format(
                    relative, expected_hash, actual_hash
                )
            )
        overlay_hashes[relative] = actual_hash
    full_overlay_body_inventory = validate_full_overlay_body_inventory()
    protected_overlay_inventory = validate_protected_overlay_inventory()

    keyboard_patch = require_file_tokens(
        require_regular_tree_file(
            patch_root,
            "focus/core/keyboard-shortcuts.patch",
            "Incognito common patch",
        ),
        (
            "command_id == IDC_NEW_INCOGNITO_WINDOW",
            "{IDC_NEW_INCOGNITO_WINDOW, ui::EF_COMMAND_DOWN | ui::EF_SHIFT_DOWN,",
            "ui::VKEY_N}",
        ),
        "macOS Incognito keyboard shortcut",
    )
    if keyboard_patch.count("IDC_NEW_INCOGNITO_WINDOW") < 2:
        raise ContractError("macOS Incognito command is not preserved by shortcut patch")

    platform_incognito_patch = require_file_tokens(
        require_regular_tree_file(
            PLATFORM_PATCH_DIR,
            "native-incognito-contract.patch",
            "platform Incognito patch",
        ),
        (
            "+  if (command_id == IDC_NEW_INCOGNITO_WINDOW) {",
            "+    return false;",
            '-  if (base::CommandLine::ForCurrentProcess()->HasSwitch("enable-incognito-themes"))',
            '-    {"enable-incognito-themes",',
            "+  const bool use_russian_disclosure =",
        ),
        "macOS native Incognito safety patch",
    )
    added_literals = "".join(
        match
        for line in platform_incognito_patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
        for match in re.findall(r'"([^"]*)"', line[1:])
    )
    for disclosure in (INCOGNITO_PRIVACY_EN, INCOGNITO_PRIVACY_RU):
        if disclosure not in added_literals:
            raise ContractError("macOS Incognito privacy disclosure changed")

    require_file_tokens(
        OVERLAY_ROOT / "chrome/app/chrome_command_ids.h",
        ("#define IDC_NEW_INCOGNITO_WINDOW        34001",),
        "Incognito command ID",
    )
    require_file_tokens(
        OVERLAY_ROOT / "chrome/app/generated_resources.grd",
        (
            'name="IDS_NEW_INCOGNITO_WINDOW_MAC"',
            "New Incognito Window",
        ),
        "macOS Incognito menu strings",
    )
    require_file_tokens(
        OVERLAY_ROOT / "chrome/browser/ui/browser_command_controller.cc",
        (
            "case IDC_NEW_INCOGNITO_WINDOW:",
            "NewIncognitoWindow(profile());",
            "IncognitoModePrefs::GetAvailability(profile->GetPrefs())",
            "incognito_availability != policy::IncognitoModeAvailability::kDisabled",
            "!profile->IsGuestSession()",
        ),
        "native Incognito command dispatch and policy",
    )
    require_file_tokens(
        OVERLAY_ROOT / "chrome/browser/ui/toolbar/app_menu_model.cc",
        (
            "case IDC_NEW_INCOGNITO_WINDOW:",
            "IncognitoModePrefs::IsIncognitoAllowed(browser_->profile())",
            "if (!browser_->profile()->IsGuestSession())",
            "this, IDC_NEW_INCOGNITO_WINDOW, IDS_NEW_INCOGNITO_WINDOW",
            "kIncognitoIcon",
        ),
        "native Incognito app menu",
    )
    require_file_tokens(
        OVERLAY_ROOT / "chrome/browser/ui/views/toolbar/toolbar_view.cc",
        (
            'sab_value == "never"',
            "#if BUILDFLAG(IS_MAC)",
            "A private window must remain visually distinguishable",
            "Never let a pref callback hide their persistent visual marker.",
            "browser_->profile()->IsIncognitoProfile() ||",
            "browser_->profile()->IsGuestSession() ||",
        ),
        "persistent macOS private-window identity marker",
    )
    require_file_tokens(
        OVERLAY_ROOT / "chrome/browser/chrome_content_browser_client.cc",
        (
            "if (profile->IsOffTheRecord()) {",
            "#if BUILDFLAG(IS_MAC)",
            "never",
            "--custom-ntp",
            "return false;",
        ),
        "native macOS Incognito new-tab surface",
    )
    opinionated_policy = require_file_tokens(
        OVERLAY_ROOT
        / "components/policy/core/common/focus_opinionated_policy_provider.cc",
        (
            "PolicyBundle bundle;",
            "UpdatePolicy(std::move(bundle));",
        ),
        "Focus opinionated policy provider",
    )
    if PROTECTED_PATCH_PATTERN.search(opinionated_policy):
        raise ContractError("Focus opinionated policy must not override Incognito")
    require_file_tokens(
        patch_root / "focus/ui/clean-incognito-guest-ntp.patch",
        (
            "IDS_NEW_TAB_OTR_SUBTITLE_FOCUS",
            "incognito_tab.html",
        ),
        "Focus Incognito new-tab page",
    )
    require_file_tokens(
        patch_root / "focus/core/increase-incognito-storage-quota.patch",
        (
            "kIncreaseIncognitoStorageQuota",
            "base::FEATURE_ENABLED_BY_DEFAULT",
        ),
        "Incognito storage anti-detection",
    )
    require_file_tokens(
        patch_root / "focus/core/ublock-reconfigure-defaults.patch",
        (
            "pref_key == kPrefIncognitoEnabled",
            "value = true;",
        ),
        "uBlock Incognito default",
    )
    require_file_tokens(
        OVERLAY_ROOT / "extensions/browser/extension_prefs.cc",
        (
            "extension_id == focus::kUBlockOriginComponentId",
            "pref_key == kPrefIncognitoEnabled",
            "value = true;",
        ),
        "final uBlock Incognito default overlay",
    )
    require_file_tokens(
        OVERLAY_ROOT / "chrome/browser/focus_block/focus_block_service_factory.cc",
        (
            '"FocusBlockService"',
            ".WithRegular(ProfileSelection::kOwnInstance)",
        ),
        "FocusBlock Off-The-Record profile service",
    )
    require_file_tokens(
        OVERLAY_ROOT / "extensions/browser/extension_util.cc",
        (
            "If this is an existing component extension we always allow it to",
            "work in incognito mode.",
            "Manifest::IsComponentLocation(extension->location())",
            "!Manifest::IsUBlockComponent(extension->id())",
        ),
        "FocusYoutube Incognito component access",
    )
    require_file_tokens(
        OVERLAY_ROOT / "chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.cc",
        (
            "extensions::ExtensionRegistry::Get(browser->profile())",
            "extensions::StorageFrontend::Get(browser_->profile())",
            "extensions::StorageAreaNamespace::kLocal",
        ),
        "FocusYoutube active-profile registry and storage",
    )
    focusyoutube_manifest = json.loads(
        (OVERLAY_ROOT / "third_party/focus_youtube/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    if "incognito" in focusyoutube_manifest:
        raise ContractError(
            "FocusYoutube must retain Chromium's reviewed component Incognito behavior"
        )

    flags_text = COMMON_FLAGS.read_text(encoding="utf-8") + "".join(
        path.read_text(encoding="utf-8") for path in MACOS_FLAGS.values()
    )
    baked_private_overrides = sorted(
        value for value in ("enable-incognito-themes", "custom-ntp")
        if value in flags_text
    )
    if baked_private_overrides:
        raise ContractError(
            "macOS build flags must not override private identity: {}".format(
                ", ".join(baked_private_overrides)
            )
        )

    return {
        "implementation": "native_chromium_off_the_record",
        "macos_entry_points": ["File > New Incognito Window", "Command-Shift-N"],
        "patches": patches,
        "hash_pinned_overlay_files": overlay_hashes,
        "full_overlay_body_inventory": full_overlay_body_inventory,
        "protected_overlay_inventory": protected_overlay_inventory,
        "opinionated_policy_private_mode_override": False,
        "command_shift_n_locked": True,
        "private_window_identity_marker_enforced_on_macos": True,
        "runtime_theme_override_removed_by_macos_patch": True,
        "custom_ntp_blocked_for_otr_on_macos": True,
        "privacy_disclosure": {
            "en": INCOGNITO_PRIVACY_EN,
            "ru": INCOGNITO_PRIVACY_RU,
            "does_not_claim_network_anonymity": True,
        },
        "focusblock_own_otr_service": True,
        "focusyoutube_component_allowed_in_incognito": True,
        "focusyoutube_storage_isolation_verified": False,
        "ublock_enabled_by_default": True,
        "ublock_storage_isolation_verified": False,
        "incognito_storage_quota": {
            "increase_enabled": True,
            "rationale": "anti_detection",
            "tradeoff": "higher_memory_pressure_and_availability_risk",
            "ephemerality_evidence": False,
        },
        "repository_sentinels_present": True,
        "runtime_verified": False,
    }


def validate_chromium_incognito_source(source_root):
    """Check comment-free upstream source sentinels in an explicit checkout."""
    files = {}
    for relative, tokens in CHROMIUM_INCOGNITO_SOURCE_CONTRACTS.items():
        source_path = source_root / relative
        require_file_tokens(
            source_path,
            tokens,
            "Chromium Incognito source",
            ignore_cpp_comments=True,
        )
        files[relative] = {
            "required_sentinels": len(tokens),
            "comments_ignored": True,
        }
    return {
        "files": files,
        "native_otr_profile_creation_sentinels_present": True,
        "storage_partition_shutdown_sentinels_present": True,
        "history_service_guard_sentinels_present": True,
        "session_service_scope_sentinels_present": True,
        "semantic_or_runtime_proof": False,
        "runtime_verified": False,
    }


def strip_cpp_comments(text):
    """Replace C/C++ comments with whitespace while preserving literals."""
    output = []
    index = 0
    state = "code"
    quote = None
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if char == "/" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "line_comment"
                continue
            if char == "/" and following == "*":
                output.extend((" ", " "))
                index += 2
                state = "block_comment"
                continue
            output.append(char)
            if char in ('"', "'"):
                state = "literal"
                quote = char
            index += 1
            continue
        if state == "literal":
            output.append(char)
            if char == "\\" and following:
                output.append(following)
                index += 2
                continue
            if char == quote:
                state = "code"
                quote = None
            index += 1
            continue
        if state == "line_comment":
            if char == "\n":
                output.append("\n")
                state = "code"
            else:
                output.append(" ")
            index += 1
            continue
        if char == "*" and following == "/":
            output.extend((" ", " "))
            index += 2
            state = "code"
            continue
        output.append("\n" if char == "\n" else " ")
        index += 1
    if state == "block_comment":
        raise ContractError("unterminated C/C++ block comment")
    return "".join(output)


def require_file_tokens(path, tokens, label, ignore_cpp_comments=False):
    """Require a file and exact sentinels in optionally comment-free text."""
    if not path.is_file():
        raise ContractError("missing {} file: {}".format(label, path))
    text = path.read_text(encoding="utf-8")
    if ignore_cpp_comments:
        text = strip_cpp_comments(text)
    missing = [token for token in tokens if token not in text]
    if missing:
        raise ContractError(
            "{} contract missing from {}: {}".format(label, path, repr(missing[0]))
        )
    return text


def validate_branding_patch():
    """Validate added patch lines, so removed Chromium values cannot pass."""
    path = COMMON_SERIES.parent / "focus" / "core" / "change-chromium-branding.patch"
    if not path.is_file():
        raise ContractError("missing common branding patch: {}".format(path))
    added = {
        line[1:].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    }
    required = {
        "PRODUCT_FULLNAME=Focus Browser",
        "PRODUCT_SHORTNAME=Focus Browser",
        "MAC_BUNDLE_ID={}".format(BUNDLE_ID),
        'product_info->product_name = "Focus Browser";',
    }
    if not required.issubset(added):
        raise ContractError(
            "branding patch added-line contract is incomplete: {}".format(
                ", ".join(sorted(required - added))
            )
        )
    return {
        "patch": str(path.relative_to(REPO_ROOT)),
        "sha256": sha256_file(path),
        "product_fullname": "Focus Browser",
        "product_shortname": "Focus Browser",
        "bundle_id": BUNDLE_ID,
        "crash_product_name": "Focus Browser",
    }


def placeholder_contract(value):
    """Return a multiset of GRIT placeholder names and positional references."""
    return Counter(re.findall(r'<ph\s+name="[^"]+"|\$[0-9]+', value))


def load_json_list(path, required_fields):
    if not path.is_file():
        raise ContractError("missing JSON catalog: {}".format(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ContractError("JSON catalog must be a list: {}".format(path))
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not required_fields.issubset(item):
            raise ContractError("invalid entry {} in {}".format(index, path))
        for field in required_fields:
            if not isinstance(item[field], str) or not item[field].strip():
                raise ContractError("empty {} at {} entry {}".format(field, path, index))
    return data


def find_catalog_message(entries, name, source=None):
    matches = [
        item["message"]
        for item in entries
        if item["name"] == name and (source is None or item.get("source") == source)
    ]
    if not matches:
        return None
    if len(set(matches)) != 1:
        raise ContractError("ambiguous i18n contract for {}".format(name))
    return matches[0]


def validate_i18n_catalogs():
    """Semantically validate source/en-GB/ru catalogs and Focus strings."""
    base = REPO_ROOT / "focus-chromium" / "i18n"
    languages_path = base / "languages.json"
    if not languages_path.is_file():
        raise ContractError("missing language catalog: {}".format(languages_path))
    languages = json.loads(languages_path.read_text(encoding="utf-8"))
    if not isinstance(languages, dict) or len(languages) != 81:
        raise ContractError("languages.json must contain the audited 81-language map")
    expected_languages = {"en-GB": "English (UK)", "ru": "Russian"}
    for locale, label in expected_languages.items():
        if languages.get(locale) != label:
            raise ContractError("language mapping changed for {}".format(locale))

    source_path = base / "source.gen.json"
    en_path = base / "translations" / "en-GB.json"
    ru_path = base / "translations" / "ru.json"
    source = load_json_list(source_path, {"name", "source", "context", "message"})
    en_gb = load_json_list(en_path, {"name", "source", "message"})
    ru = load_json_list(ru_path, {"name", "source", "message"})
    expected_counts = {"source": 267, "en-GB": 165, "ru": 189}
    actual_counts = {"source": len(source), "en-GB": len(en_gb), "ru": len(ru)}
    if actual_counts != expected_counts:
        raise ContractError(
            "i18n catalog counts changed: expected {}, got {}".format(
                expected_counts, actual_counts
            )
        )

    source_references = {(item["name"], item["message"]) for item in source}
    allowed_ru_source_gaps = {
        ("IDS_SETTINGS_FOCUS_BROWSER_UPDATES", "Automatically update Focus Browser"),
        (
            "IDS_SETTINGS_FOCUS_BROWSER_UPDATES_DESCRIPTION",
            "Check for signed browser updates and install them automatically",
        ),
    }
    missing_by_locale = {}
    for locale, entries, allowed_gaps in (
        ("en-GB", en_gb, set()),
        ("ru", ru, allowed_ru_source_gaps),
    ):
        missing = set()
        for item in entries:
            reference = (item["name"], item["source"])
            if reference not in source_references:
                missing.add(reference)
            if placeholder_contract(item["source"]) != placeholder_contract(item["message"]):
                raise ContractError(
                    "placeholder mismatch for {} in {}".format(item["name"], locale)
                )
        if missing != allowed_gaps:
            raise ContractError(
                "unexpected {} source-reference gaps: {}".format(
                    locale, sorted(missing ^ allowed_gaps)
                )
            )
        missing_by_locale[locale] = sorted(name for name, _ in missing)

    contracts = (
        (
            "IDS_SETTINGS_FOCUS_SERVICES",
            "Focus Browser services",
            "Focus Browser services",
            "Сервисы Focus Browser",
        ),
        (
            "IDS_SETTINGS_FOCUS_SERVICES_TOGGLE",
            "Allow connecting to Focus Browser services",
            "Allow connecting to Focus Browser services",
            "Разрешить подключение к сервисам Focus Browser",
        ),
        (
            "IDS_FOCUS_ONBOARDING_WELCOME_GREETING",
            "Focus on what matters.",
            None,
            "Сосредоточьтесь на главном.",
        ),
        (
            "IDS_SETTINGS_FOCUS_MOTION",
            "Smooth interface animations",
            None,
            "Плавные анимации интерфейса",
        ),
    )
    validated_contracts = []
    for name, source_message, en_message, ru_message in contracts:
        if find_catalog_message(source, name) != source_message:
            raise ContractError("source message changed for {}".format(name))
        actual_en = find_catalog_message(en_gb, name, source_message)
        actual_ru = find_catalog_message(ru, name, source_message)
        if actual_en != en_message or actual_ru != ru_message:
            raise ContractError("RU/EN translation contract changed for {}".format(name))
        validated_contracts.append(name)

    return {
        "languages": {"count": len(languages), **expected_languages},
        "catalog_counts": actual_counts,
        "sha256": {
            "languages": sha256_file(languages_path),
            "source": sha256_file(source_path),
            "en-GB": sha256_file(en_path),
            "ru": sha256_file(ru_path),
        },
        "placeholder_contracts": True,
        "source_reference_gaps": missing_by_locale,
        "required_message_contracts": validated_contracts,
    }


def validate_feature_contracts():
    """Pin native FocusBlock/FocusYoutube implementation and integration."""
    hashes = {}
    for relative, expected_hash in FOCUS_FEATURE_HASHES.items():
        path = OVERLAY_ROOT / relative
        if not path.is_file():
            raise ContractError("missing Focus feature contract file: {}".format(relative))
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise ContractError(
                "Focus feature file changed for {}: expected {}, got {}".format(
                    relative, expected_hash, actual_hash
                )
            )
        hashes[relative] = actual_hash

    service = require_file_tokens(
        OVERLAY_ROOT / "chrome/browser/focus_block/focus_block_service.cc",
        (
            "constexpr size_t kMaxPendingMatchRequests = 4096;",
            "pending_match_requests_.push_back",
            "void FocusBlockService::OnEngineReady(bool ready)",
            "std::move(pending.callback).Run(false)",
            "FocusBlock failed to initialize Ghostery 2.18.1",
        ),
        "FocusBlock service",
    )
    if "pending_match_requests_.size() < kMaxPendingMatchRequests" not in service:
        raise ContractError("FocusBlock startup queue is not bounded")
    loader = require_file_tokens(
        OVERLAY_ROOT / "chrome/browser/focus_block/focus_block_url_loader_factory.cc",
        ("FocusBlockService", "service_->ShouldBlock"),
        "FocusBlock URL loader",
    )
    if "!service_->engine_ready()" in loader:
        raise ContractError("FocusBlock URL loader bypasses the bounded startup queue")
    require_file_tokens(
        OVERLAY_ROOT / "chrome/browser/focus_block/BUILD.gn",
        (
            '"//components/focus_services"',
            '"//third_party/ghostery_adblocker:resources"',
            '"//third_party/ublock"',
        ),
        "FocusBlock BUILD",
    )
    require_file_tokens(
        OVERLAY_ROOT / "chrome/browser/ui/views/location_bar/focus_block_bubble_view.cc",
        (
            'FocusText(u"Protection across the browser", u"Защита во всём браузере")',
            'FocusText(u"Block ads and trackers", u"Блокировать рекламу и трекеры")',
            "Ghostery 2.18.1",
            'FocusText(u"● Engine active", u"● Движок активен")',
        ),
        "FocusBlock bubble RU/EN",
    )

    youtube_path = OVERLAY_ROOT / "chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.cc"
    youtube = require_file_tokens(
        youtube_path,
        (
            "constexpr int kFocusYoutubeSchemaVersion = 4;",
            "constexpr std::array<FeatureSpec, 25> kFeatures",
            'FocusText(u"Reset", u"Сбросить")',
            'FocusText(u"FocusYoutube is active", u"FocusYoutube активен")',
            'FocusText(u"FocusYoutube is paused", u"FocusYoutube на паузе")',
            'FocusText(u"Enable FocusYoutube", u"Включить FocusYoutube")',
            'values.Set("global_enable", true)',
        ),
        "FocusYoutube native bubble RU/EN",
    )
    initializer_match = re.search(
        r"constexpr std::array<FeatureSpec, 25> kFeatures = \{\{(.*?)\}\};",
        youtube,
        flags=re.DOTALL,
    )
    if not initializer_match:
        raise ContractError("cannot parse FocusYoutube feature table")
    initializer = initializer_match.group(1)
    feature_count = len(re.findall(r"\b(?:Feature|CompositeFeature)\(", initializer))
    storage_keys = tuple(re.findall(r'"([a-z][a-z0-9_]*)"', initializer))
    if feature_count != 25 or storage_keys != FOCUS_YOUTUBE_STORAGE_KEYS:
        raise ContractError(
            "FocusYoutube feature schema changed: expected 25 controls/29 ordered keys"
        )

    manifest_path = OVERLAY_ROOT / "third_party/focus_youtube/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required_manifest = {
        "manifest_version": 3,
        "name": "FocusYoutube",
        "version": "1.6.9.1",
        "permissions": ["storage", "alarms"],
        "host_permissions": ["https://youtube.com/*", "https://*.youtube.com/*"],
    }
    for key, expected in required_manifest.items():
        if manifest.get(key) != expected:
            raise ContractError("FocusYoutube manifest contract changed for {}".format(key))
    if manifest.get("background", {}).get("service_worker") != "background/events.js":
        raise ContractError("FocusYoutube service worker contract changed")
    scripts = manifest.get("content_scripts")
    if not isinstance(scripts, list) or len(scripts) != 1:
        raise ContractError("FocusYoutube must have one audited content script block")
    script = scripts[0]
    if script.get("run_at") != "document_start" or script.get("all_frames") is not True:
        raise ContractError("FocusYoutube content script timing/frame contract changed")

    integration_tokens = {
        "components/focus_services/extension_ids.h": (
            "kFocusYoutubeComponentId",
            '"jafokmemnknjknbdiklabcnhlpheefbm"',
        ),
        "chrome/browser/extensions/component_loader.cc": (
            "void ComponentLoader::AddFocusYoutube()",
            "IDR_FOCUS_YOUTUBE_MANIFEST_JSON",
            "AddFocusYoutube();",
        ),
        "chrome/browser/extensions/chrome_component_extension_resource_manager.cc": (
            "AddComponentResourceEntries(kFocusYoutubeResources);",
        ),
        "chrome/browser/extensions/component_extensions_allowlist/allowlist.cc": (
            "focus::kFocusYoutubeComponentId",
        ),
        "chrome/browser/ui/views/toolbar/toolbar_view.cc": ("FocusYoutubeBubbleView",),
    }
    for relative, tokens in integration_tokens.items():
        require_file_tokens(OVERLAY_ROOT / relative, tokens, "FocusYoutube integration")

    return {
        "hash_pinned_files": hashes,
        "FocusBlock": {
            "service": True,
            "bounded_startup_queue": 4096,
            "fail_open_contract": True,
            "ghostery_engine": "2.18.1",
            "bubble_ru_en": True,
        },
        "FocusYoutube": {
            "component_id": "jafokmemnknjknbdiklabcnhlpheefbm",
            "component_version": "1.6.9.1",
            "schema_version": 4,
            "native_controls": feature_count,
            "storage_keys": len(storage_keys),
            "global_enable": True,
            "bubble_ru_en": True,
            "component_integration": True,
        },
    }


def validate_icns_asset():
    """Pin the canonical PNG and generated multi-resolution ICNS container."""
    source = REPO_ROOT / "focus-chromium" / "resources" / "branding" / "app_icon" / "raw.png"
    try:
        source_report = inspect_png(source)
        icon_report = validate_focus_icns(FOCUS_ICNS)
    except (OSError, IconContractError) as exc:
        raise ContractError("invalid Focus Browser icon asset: {}".format(exc)) from exc
    if source_report["sha256"] != CANONICAL_ICON_SHA256 or (
        source_report["width"],
        source_report["height"],
        source_report["bit_depth"],
        source_report["color_type"],
    ) != (1024, 1024, 8, 6):
        raise ContractError("canonical Focus Browser PNG contract changed")
    return {
        "canonical_png": str(source.relative_to(REPO_ROOT)),
        "canonical_png_sha256": source_report["sha256"],
        "canonical_png_dimensions": [1024, 1024],
        "icns": str(FOCUS_ICNS.relative_to(REPO_ROOT)),
        "icns_sha256": icon_report["sha256"],
        "icns_bytes": icon_report["bytes"],
        "embedded_png_dimensions": [list(value) for value in icon_report["png_dimensions"]],
        "chunk_types": icon_report["chunk_types"],
        "generator": "platform/macos/generate_icns.py --generate",
    }


def parse_gn_assignments(paths, expected_target_cpu="arm64", include_values=False):
    """Compose GN files while rejecting malformed or duplicate assignments."""
    assignments = {}
    if expected_target_cpu not in MACOS_FLAGS:
        raise ContractError("unsupported macOS target CPU: {}".format(expected_target_cpu))
    blocks = [
        "# Generated plan: common Focus flags, then native macOS {} flags.\n".format(
            expected_target_cpu
        )
    ]
    pattern = re.compile(
        r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(true|false|-?[0-9]+|"(?:[^"\\]|\\.)*")$'
    )
    for path in paths:
        if not path.is_file():
            raise ContractError("missing GN flags file: {}".format(path))
        text = path.read_text(encoding="utf-8").rstrip() + "\n"
        try:
            display_path = path.relative_to(REPO_ROOT)
        except ValueError:
            display_path = path
        blocks.append("\n# From {}\n{}".format(display_path, text))
        for number, line in enumerate(text.splitlines(), 1):
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            match = pattern.match(value)
            if not match:
                raise ContractError(
                    "invalid GN assignment at {}:{}: {!r}".format(path, number, value)
                )
            name = match.group(1)
            if name in assignments:
                raise ContractError(
                    "duplicate GN arg {} at {}:{} and {}:{}".format(
                        name,
                        assignments[name]["path"],
                        assignments[name]["line"],
                        path,
                        number,
                    )
                )
            assignments[name] = {
                "path": path,
                "line": number,
                "value": match.group(2),
            }

    composed = "".join(blocks)
    required = {
        "target_os": '"mac"',
        "target_cpu": '"{}"'.format(expected_target_cpu),
        "mac_deployment_target": '"{}"'.format(PINNED_MACOS_MINIMUM),
        "mac_min_system_version": '"{}"'.format(PINNED_MACOS_MINIMUM),
        "use_system_xcode": "true",
        "is_component_build": "false",
        "is_debug": "false",
        "is_official_build": "true",
        "enable_updater": "false",
        "include_branded_entitlements": "false",
        "use_siso": "false",
        "use_remoteexec": "false",
    }
    for name, expected_value in required.items():
        actual_value = assignments.get(name, {}).get("value")
        if actual_value != expected_value:
            raise ContractError(
                "required GN contract changed for {}: expected {}, got {}".format(
                    name, expected_value, actual_value
                )
            )
    if "enable_winsparkle" in assignments:
        raise ContractError("Windows updater GN arg is forbidden in macOS flags")
    if include_values:
        return composed, sorted(assignments), {
            name: record["value"] for name, record in assignments.items()
        }
    return composed, sorted(assignments)


def validate_gn_profiles():
    """Validate both Mac slices and require exact non-CPU assignment parity."""
    profiles = {}
    value_maps = {}
    for architecture, flags_path in MACOS_FLAGS.items():
        gn_text, gn_names, gn_values = parse_gn_assignments(
            (COMMON_FLAGS, flags_path),
            expected_target_cpu=architecture,
            include_values=True,
        )
        try:
            display_path = flags_path.relative_to(REPO_ROOT)
        except ValueError:
            display_path = flags_path
        profiles[architecture] = {
            "flags_file": str(display_path),
            "arg_names": gn_names,
            "args_gn": gn_text,
        }
        value_maps[architecture] = gn_values

    parity_maps = {
        architecture: {
            name: value for name, value in values.items() if name != "target_cpu"
        }
        for architecture, values in value_maps.items()
    }
    if set(parity_maps) != {"arm64", "x64"}:
        raise ContractError("macOS GN profiles must contain exactly arm64 and x64")
    if parity_maps["arm64"] != parity_maps["x64"]:
        changed = sorted(
            name
            for name in set(parity_maps["arm64"]) | set(parity_maps["x64"])
            if parity_maps["arm64"].get(name) != parity_maps["x64"].get(name)
        )
        raise ContractError(
            "arm64/x64 GN profiles differ outside target_cpu: {}".format(
                ", ".join(changed)
            )
        )
    return {
        "architectures": ["arm64", "x64"],
        "profiles": profiles,
        "profiles_equal_except_target_cpu": True,
        "minimum_macos": PINNED_MACOS_MINIMUM,
    }


def validate_legal_inventory():
    """Inventory evidence and keep redistribution blocked pending legal review."""
    evidence = {
        "repository_gpl": (
            REPO_ROOT / "LICENSE",
            ("GNU GENERAL PUBLIC LICENSE", "Version 3, 29 June 2007"),
        ),
        "third_party_notices": (
            REPO_ROOT / "THIRD_PARTY_NOTICES.md",
            ("uBlock Origin", "Ghostery Adblocker", "Unhook / FocusYoutube"),
        ),
        "ublock_metadata": (
            OVERLAY_ROOT / "third_party/ublock/README.chromium",
            ("Version: 1.72.2.2", "License: GPL-3.0", "Shipped: yes"),
        ),
        "ublock_license": (
            OVERLAY_ROOT / "third_party/ublock/LICENSE.txt",
            ("GNU GENERAL PUBLIC LICENSE", "Version 3"),
        ),
        "ghostery_metadata": (
            OVERLAY_ROOT / "third_party/ghostery_adblocker/README.chromium",
            ("Version: 2.18.1", "License: MPL-2.0", "Shipped: yes"),
        ),
        "ghostery_license": (
            OVERLAY_ROOT / "third_party/ghostery_adblocker/LICENSE",
            ("Mozilla Public License", "2.0"),
        ),
        "focusyoutube_metadata": (
            OVERLAY_ROOT / "third_party/focus_youtube/README.chromium",
            ("Version: 1.6.9.1", "authorized in writing", "Shipped: yes"),
        ),
        "focusyoutube_terms": (
            OVERLAY_ROOT / "third_party/focus_youtube/LICENSE.txt",
            (
                "You may not",
                "modify, create derivative works from, distribute",
                "sublicense",
            ),
        ),
    }
    evidence_report = {}
    for name, (path, tokens) in evidence.items():
        require_file_tokens(path, tokens, "legal evidence")
        evidence_report[name] = {
            "path": str(path.relative_to(REPO_ROOT)),
            "sha256": sha256_file(path),
        }
    if not (OVERLAY_ROOT / "third_party/ublock/assets").is_dir():
        raise ContractError("uBlock filter assets directory is missing")
    for notice in ("DEPENDENCIES.md", "LICENSE.esbuild", "LICENSE.tldts"):
        path = OVERLAY_ROOT / "third_party/ghostery_adblocker" / notice
        if not path.is_file():
            raise ContractError("missing Ghostery dependency notice: {}".format(path))

    for architecture, flags_path in MACOS_FLAGS.items():
        gn_text, _ = parse_gn_assignments(
            (COMMON_FLAGS, flags_path), expected_target_cpu=architecture
        )
        for required in (
            "enable_widevine=true",
            'ffmpeg_branding="Chrome"',
            "proprietary_codecs=true",
        ):
            if required not in gn_text:
                raise ContractError(
                    "redistribution-sensitive GN input changed for {}: {}".format(
                        architecture, required
                    )
                )

    return {
        "status": "blocked_pending_legal_and_component_evidence",
        "redistribution_allowed": False,
        "evidence": evidence_report,
        "inventory": [
            {
                "component": "Focus Browser repository",
                "license": "GPL-3.0",
                "gate": "manual_app_store_compatibility_and_corresponding_source_review",
            },
            {
                "component": "uBlock Origin code, resources, and filter assets",
                "version": "1.72.2.2",
                "license": "GPL-3.0 plus per-asset/filter-list terms",
                "gate": "notices_and_corresponding_source_required",
            },
            {
                "component": "Ghostery Adblocker engine",
                "version": "2.18.1",
                "license": "MPL-2.0 plus bundled dependency terms",
                "gate": "MPL_source_and_notice_compliance_review",
            },
            {
                "component": "FocusYoutube derived from Unhook",
                "version": "1.6.9.1",
                "license": "restrictive EULA; repository references separate written permission",
                "gate": "written_grant_evidence_not_present_in_repository",
            },
            {
                "component": "FFmpeg/proprietary codecs",
                "build_inputs": ['ffmpeg_branding="Chrome"', "proprietary_codecs=true"],
                "gate": "codec_patent_and_redistribution_rights_review",
            },
            {
                "component": "Widevine CDM",
                "build_input": "enable_widevine=true",
                "gate": "availability_license_bundling_and_arm64_evidence_required",
            },
        ],
        "manual_blockers": [
            "review GPL-3.0/App Store terms and provide Corresponding Source, scripts, notices, and any required source offer",
            "obtain and review the actual Unhook written grant: modification, distribution and sublicensing scope; macOS and Apple App Store; territories/worldwide; versions, derivatives, and duration",
            "review every bundled uBlock/filter-list asset license and Ghostery dependency notice",
            "confirm proprietary codec/FFmpeg patent and redistribution rights",
            "confirm actual Widevine CDM availability, license, bundling permission, and Apple Silicon support",
        ],
    }


def validate_repository_contract():
    """Validate branding, locales, features, patch series, flags, and overlay."""
    pin_file = REPO_ROOT / "focus-chromium" / "chromium_version.txt"
    pin = pin_file.read_text(encoding="utf-8").strip() if pin_file.is_file() else ""
    if pin != PINNED_CHROMIUM_VERSION:
        raise ContractError(
            "repository Chromium pin mismatch: expected {}, got {!r}".format(
                PINNED_CHROMIUM_VERSION, pin
            )
        )

    branding = validate_branding_patch()
    icons = validate_icns_asset()
    locales = validate_i18n_catalogs()
    features = validate_feature_contracts()
    common_series = validate_common_series()
    patch_report = validate_platform_patch_series()
    incognito = validate_incognito_repository_contract()
    prefixes = read_exclude_prefixes()
    cleanup = validate_delete_manifest(prefixes)
    all_files = list(iter_overlay_regular_files(OVERLAY_ROOT))
    excluded = []
    included = []
    for path in all_files:
        relative = path.relative_to(OVERLAY_ROOT).as_posix()
        if is_overlay_excluded(relative, prefixes):
            excluded.append(relative)
        elif relative != "delete.txt":
            included.append(relative)
    if len(excluded) != EXPECTED_EXCLUDED_OVERLAY_FILES:
        raise ContractError(
            "Windows-only overlay inventory changed: expected {} files, got {}".format(
                EXPECTED_EXCLUDED_OVERLAY_FILES, len(excluded)
            )
        )
    for relative in FOCUS_VIEW_SENTINELS:
        if relative not in included:
            raise ContractError("required Focus Views file was filtered out: {}".format(relative))

    gn = validate_gn_profiles()
    legal_gate = validate_legal_inventory()
    return {
        "chromium_pin": pin,
        "branding": branding,
        "icons": icons,
        "locales": locales,
        "features": features,
        "platform_patches": patch_report,
        "shared_series": common_series,
        "incognito": incognito,
        "overlay": {
            "root": str(OVERLAY_ROOT.relative_to(REPO_ROOT)),
            "exclude_prefixes": prefixes,
            "excluded_count": len(excluded),
            "included_count": len(included),
            "cleanup_manifest": "source_overrides/delete.txt",
            "planned_cleanup_paths": cleanup["planned"],
            "excluded_cleanup_paths": cleanup["excluded"],
            "delete_manifest_executed": cleanup["executed"],
        },
        "gn": gn,
        "updater": "off",
        "signing": {
            "developer_id": "off",
            "ad_hoc": "planned_after_complete_app_build",
            "paid_account_required": False,
            "executed": False,
        },
        "notarization": "off",
        "distribution": "local_macos_only",
        "local_installation": {
            "app": "planned_after_chromium_build",
            "drag_and_drop_dmg": "planned_after_app_acceptance",
            "dmg_account_required": False,
            "executed": False,
        },
        "redistribution_gate": legal_gate,
    }


def disk_gate(path, minimum_free_gib):
    """Return deterministic disk-gate status for an existing filesystem path."""
    usage = shutil.disk_usage(path)
    report = {
        "filesystem_path": str(path),
        "free_bytes": usage.free,
        "free_gib": round(usage.free / (1024 ** 3), 2),
    }
    if minimum_free_gib is None:
        report.update({"status": "not_enforced", "minimum_free_gib": None})
        return report
    required = int(minimum_free_gib * (1024 ** 3))
    report.update(
        {
            "minimum_free_gib": float(minimum_free_gib),
            "minimum_free_bytes": required,
            "status": "pass" if usage.free >= required else "fail",
        }
    )
    if usage.free < required:
        raise ContractError(
            "disk gate failed: {:.2f} GiB free, {} GiB required".format(
                usage.free / (1024 ** 3), minimum_free_gib
            )
        )
    return report


def validate(source_root, developer_dir, minimum_free_gib=None):
    """Build a complete read-only validation report."""
    host = validate_host()
    root, version = resolve_source_root(source_root)
    toolchain = validate_xcode_toolchain(developer_dir)
    macos_build = validate_chromium_macos_build_contract(root)
    repository = validate_repository_contract()
    incognito_source = validate_chromium_incognito_source(root)
    return {
        "command": "validate",
        "dry_run": True,
        "host": host,
        "source": {
            "root": str(root),
            "chromium_version": version,
            "macos_build": macos_build,
            "incognito": incognito_source,
        },
        "toolchain": toolchain,
        "disk_gate": disk_gate(root, minimum_free_gib),
        "repository": repository,
    }


def normalise_out_dir(value):
    """Require an out directory relative to the explicit Chromium root."""
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "\\" in value or not pure.parts:
        raise ContractError("output directory must be a safe path relative to Chromium source")
    return pure.as_posix()


def plan(source_root, developer_dir, minimum_free_gib):
    """Return, but never execute, the pinned macOS build pipeline and commands."""
    report = validate(source_root, developer_dir, minimum_free_gib)
    root = Path(report["source"]["root"])
    developer_assignment = "DEVELOPER_DIR={}".format(
        report["toolchain"]["developer_dir"]
    )
    command_prefix = ["/usr/bin/env", developer_assignment]
    slices = {}
    commands = []
    for architecture in ("arm64", "x64"):
        relative_out = normalise_out_dir(DEFAULT_OUT_DIRS[architecture])
        args_destination = root / Path(relative_out) / "args.gn"
        gn_profile = report["repository"]["gn"]["profiles"][architecture]
        gn_text = gn_profile.pop("args_gn")
        slice_commands = [
            command_prefix
            + ["gn", "gen", relative_out, "--fail-on-unused-args"],
            command_prefix + ["autoninja", "-C", relative_out, "chrome"],
        ]
        commands.extend(slice_commands)
        slices[architecture] = {
            "target_cpu": architecture,
            "mach_o_architecture": "arm64" if architecture == "arm64" else "x86_64",
            "out_dir": relative_out,
            "app": str(PurePosixPath(relative_out) / "Focus Browser.app"),
            "args_gn_destination": str(args_destination),
            "args_gn": gn_text,
            "ninja_targets": ["chrome"],
            "commands": slice_commands,
            "executed": False,
        }

    universal_out = normalise_out_dir(DEFAULT_OUT_DIRS["universal"])
    universal_app = str(PurePosixPath(universal_out) / "Focus Browser.app")
    universal_inputs = [slices["x64"]["app"], slices["arm64"]["app"]]
    universal_parent_command = command_prefix + ["/bin/mkdir", "-p", universal_out]
    universal_command = command_prefix + [
        "python3",
        CHROMIUM_UNIVERSALIZER,
        universal_inputs[0],
        universal_inputs[1],
        universal_app,
    ]
    commands.extend((universal_parent_command, universal_command))
    report.update(
        {
            "command": "plan",
            "build": {
                "architectures": ["arm64", "x64"],
                "configuration": "official-release-unsigned",
                "minimum_macos": PINNED_MACOS_MINIMUM,
                "working_directory": str(root),
                "slices": slices,
                "ninja_targets": ["chrome"],
                "commands": commands,
                "universal": {
                    "tool": CHROMIUM_UNIVERSALIZER,
                    "inputs": universal_inputs,
                    "input_order": ["x64", "arm64"],
                    "out_dir": universal_out,
                    "output": universal_app,
                    "parent_directory_command": universal_parent_command,
                    "command": universal_command,
                    "assembly_executed": False,
                    "signing_executed": False,
                    "runtime_verified": False,
                },
                "executed": False,
                "signing_executed": False,
                "runtime_verified": False,
            },
            "pipeline": [
                "validate explicit existing Chromium 150.0.7871.128 source",
                "validate exact Xcode 27.0 build 27A5228h and macOS SDK 27.0",
                "apply filtered focus-chromium/patches/series (manual)",
                "apply platform/macos/patches/series in order (manual)",
                "run common substitutions and RU/EN i18n (manual)",
                "review safe filtered delete.txt cleanup plan (manual; not executed)",
                "apply filtered source_overrides (manual)",
                "generate and copy common Focus resources (manual)",
                "append Focus version and write planned arm64/x64 args.gn files (manual)",
                "run displayed GN/Ninja commands for both slices after review (manual)",
                "merge x64 then arm64 apps with the pinned Chromium universalizer (manual)",
                "verify nested ad-hoc app signature and native Incognito (manual)",
                "create and verify local drag-and-drop DMG (manual, after app acceptance)",
            ],
        }
    )
    return report


def positive_decimal(value):
    """argparse type for a strictly positive GiB threshold."""
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def emit_human(report):
    """Print a compact operator-oriented report."""
    print("OK: native {system}/{machine}".format(**report["host"]))
    print(
        "Chromium: {chromium_version} at {root}".format(**report["source"])
    )
    print(
        "Xcode: {version} ({build}), macOS SDK {sdk}".format(
            version=report["toolchain"]["xcode"]["version"],
            build=report["toolchain"]["xcode"]["build"],
            sdk=report["toolchain"]["sdk"]["version"],
        )
    )
    gate = report["disk_gate"]
    if gate["status"] == "not_enforced":
        print("Disk gate: not enforced ({:.2f} GiB free)".format(gate["free_gib"]))
    else:
        print(
            "Disk gate: {} ({:.2f} GiB free; {} GiB required)".format(
                gate["status"], gate["free_gib"], gate["minimum_free_gib"]
            )
        )
    repository = report["repository"]
    print(
        "Contracts: bundle {}, RU/EN, FocusBlock, FocusYoutube, native Incognito, "
        "{} Windows files excluded".format(
            repository["branding"]["bundle_id"],
            repository["overlay"]["excluded_count"],
        )
    )
    if report["command"] == "plan":
        for architecture in ("arm64", "x64"):
            print(
                "{} args.gn destination: {}".format(
                    architecture,
                    report["build"]["slices"][architecture]["args_gn_destination"],
                )
            )
        for command in report["build"]["commands"]:
            print("PLAN ONLY: {}".format(shlex.join(command)))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="validate an existing pinned Chromium source tree"
    )
    validate_parser.add_argument("--source-root", required=True)
    validate_parser.add_argument("--developer-dir", required=True)
    validate_parser.add_argument("--min-free-gib", type=positive_decimal)
    validate_parser.add_argument("--dry-run", action="store_true")
    validate_parser.add_argument("--json", action="store_true")

    plan_parser = subparsers.add_parser(
        "plan", help="validate and print a non-executing GN/Ninja plan"
    )
    plan_parser.add_argument("--source-root", required=True)
    plan_parser.add_argument("--developer-dir", required=True)
    plan_parser.add_argument("--min-free-gib", type=positive_decimal, required=True)
    plan_parser.add_argument("--dry-run", action="store_true")
    plan_parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            report = validate(args.source_root, args.developer_dir, args.min_free_gib)
        else:
            report = plan(args.source_root, args.developer_dir, args.min_free_gib)
    except (ContractError, OSError, ValueError, json.JSONDecodeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"ok": True, "result": report}, indent=2, sort_keys=True))
    else:
        emit_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
