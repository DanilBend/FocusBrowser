#!/usr/bin/env python3
"""Validate and print a native Focus Browser macOS arm64 build plan.

This first-stage tool is intentionally read-only. It has no download, patch,
copy, delete, build, signing, publishing, or shutdown operation.
"""

import argparse
import hashlib
import json
import platform
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

COMMON_FLAGS = REPO_ROOT / "focus-chromium" / "flags.gn"
MACOS_FLAGS = MACOS_DIR / "flags.arm64.gn"
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
        "9cb58b4011c3fe5b825f954b94b3a2774d1c06260f6ccbb165f5675bcfbc6706",
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
    """Require the native Apple Silicon host used by this platform plan."""
    system = platform.system()
    machine = platform.machine().lower()
    if system != "Darwin" or machine not in ("arm64", "aarch64"):
        raise ContractError(
            "native macOS/Apple Silicon required; found {}/{}".format(system, machine)
        )
    return {"system": system, "machine": machine}


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


def validate_platform_patch_series():
    """Verify exact shared Views patch order, targets, and content hashes."""
    entries = read_series(PLATFORM_SERIES)
    if len(entries) != len(EXPECTED_PATCHES):
        raise ContractError("macOS patch series must contain exactly two patches")

    report = []
    for position, (entry, expected) in enumerate(zip(entries, EXPECTED_PATCHES), 1):
        expected_relative, expected_hash = expected
        patch_path = (PLATFORM_PATCH_DIR / entry).resolve()
        expected_path = (REPO_ROOT / expected_relative).resolve()
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
        targets = []
        for line in patch_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("--- a/") or line.startswith("+++ b/"):
                target = line.split(None, 1)[1][2:]
                targets.append(target)
                if is_overlay_excluded(target, EXPECTED_EXCLUDE_PREFIXES):
                    raise ContractError(
                        "shared Views patch touches Windows-only path: {}".format(target)
                    )
        if not targets:
            raise ContractError("patch has no Chromium targets: {}".format(patch_path))
        report.append(
            {
                "order": position,
                "path": expected_relative.as_posix(),
                "sha256": actual_hash,
                "target_count": len(set(targets)),
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
        if not (patch_root / entry).is_file():
            raise ContractError("common patch target is missing: {}".format(patch_root / entry))

    required = []
    for entry, expected_position, expected_hash in REQUIRED_COMMON_PATCHES:
        actual_position = entries.index(entry) + 1 if entry in entries else None
        if actual_position != expected_position:
            raise ContractError(
                "required common patch position changed for {}: expected {}, got {}".format(
                    entry, expected_position, actual_position
                )
            )
        actual_hash = sha256_file(patch_root / entry)
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
    return {
        "path": str(COMMON_SERIES.relative_to(REPO_ROOT)),
        "sha256": actual_series_hash,
        "total_entries": len(entries),
        "excluded_for_macos": list(SHARED_SERIES_EXCLUSIONS),
        "exclusion_positions": COMMON_EXCLUSION_POSITIONS,
        "planned_entries": len(filtered),
        "filtered_order_sha256": filtered_hash,
        "required_patches": required,
    }


def require_file_tokens(path, tokens, label):
    """Require a file and exact semantic sentinels in its text."""
    if not path.is_file():
        raise ContractError("missing {} file: {}".format(label, path))
    text = path.read_text(encoding="utf-8")
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


def parse_gn_assignments(paths):
    """Compose GN files while rejecting malformed or duplicate assignments."""
    assignments = {}
    blocks = ["# Generated plan: common Focus flags, then native macOS arm64 flags.\n"]
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
        "target_cpu": '"arm64"',
        "is_component_build": "false",
        "is_debug": "false",
        "is_official_build": "true",
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
    return composed, sorted(assignments)


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

    gn_text, _ = parse_gn_assignments((COMMON_FLAGS, MACOS_FLAGS))
    for required in (
        "enable_widevine=true",
        'ffmpeg_branding="Chrome"',
        "proprietary_codecs=true",
    ):
        if required not in gn_text:
            raise ContractError("redistribution-sensitive GN input changed: {}".format(required))

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
    prefixes = read_exclude_prefixes()
    cleanup = validate_delete_manifest(prefixes)
    all_files = sorted(path for path in OVERLAY_ROOT.rglob("*") if path.is_file())
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

    gn_text, gn_names = parse_gn_assignments((COMMON_FLAGS, MACOS_FLAGS))
    legal_gate = validate_legal_inventory()
    return {
        "chromium_pin": pin,
        "branding": branding,
        "icons": icons,
        "locales": locales,
        "features": features,
        "platform_patches": patch_report,
        "shared_series": common_series,
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
        "gn": {"arg_names": gn_names, "args_gn": gn_text},
        "updater": "off",
        "signing": "off",
        "notarization": "off",
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


def validate(source_root, minimum_free_gib=None):
    """Build a complete read-only validation report."""
    host = validate_host()
    root, version = resolve_source_root(source_root)
    repository = validate_repository_contract()
    return {
        "command": "validate",
        "dry_run": True,
        "host": host,
        "source": {"root": str(root), "chromium_version": version},
        "disk_gate": disk_gate(root, minimum_free_gib),
        "repository": repository,
    }


def normalise_out_dir(value):
    """Require an out directory relative to the explicit Chromium root."""
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "\\" in value or not pure.parts:
        raise ContractError("--out-dir must be a safe path relative to Chromium source")
    return pure.as_posix()


def plan(source_root, minimum_free_gib, out_dir="out/FocusMacArm64"):
    """Return, but never execute, the pinned macOS build pipeline and commands."""
    report = validate(source_root, minimum_free_gib)
    root = Path(report["source"]["root"])
    relative_out = normalise_out_dir(out_dir)
    args_destination = root / Path(relative_out) / "args.gn"
    gn_text = report["repository"]["gn"].pop("args_gn")
    report.update(
        {
            "command": "plan",
            "build": {
                "architecture": "arm64",
                "configuration": "official-release-unsigned",
                "out_dir": relative_out,
                "args_gn_destination": str(args_destination),
                "args_gn": gn_text,
                "ninja_targets": ["chrome"],
                "commands": [
                    ["gn", "gen", relative_out, "--fail-on-unused-args"],
                    ["autoninja", "-C", relative_out, "chrome"],
                ],
                "executed": False,
            },
            "pipeline": [
                "validate explicit existing Chromium 150.0.7871.128 source",
                "apply filtered focus-chromium/patches/series (manual)",
                "apply platform/macos/patches/series in order (manual)",
                "run common substitutions and RU/EN i18n (manual)",
                "review safe filtered delete.txt cleanup plan (manual; not executed)",
                "apply filtered source_overrides (manual)",
                "generate and copy common Focus resources (manual)",
                "append Focus version and write planned args.gn (manual)",
                "run displayed GN/Ninja commands after review (manual)",
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
        "Contracts: bundle {}, RU/EN, FocusBlock, FocusYoutube, {} Windows files excluded".format(
            repository["branding"]["bundle_id"],
            repository["overlay"]["excluded_count"],
        )
    )
    if report["command"] == "plan":
        print("args.gn destination: {}".format(report["build"]["args_gn_destination"]))
        for command in report["build"]["commands"]:
            print("PLAN ONLY: {}".format(shlex.join(command)))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="validate an existing pinned Chromium source tree"
    )
    validate_parser.add_argument("--source-root", required=True)
    validate_parser.add_argument("--min-free-gib", type=positive_decimal)
    validate_parser.add_argument("--dry-run", action="store_true")
    validate_parser.add_argument("--json", action="store_true")

    plan_parser = subparsers.add_parser(
        "plan", help="validate and print a non-executing GN/Ninja plan"
    )
    plan_parser.add_argument("--source-root", required=True)
    plan_parser.add_argument("--min-free-gib", type=positive_decimal, required=True)
    plan_parser.add_argument("--out-dir", default="out/FocusMacArm64")
    plan_parser.add_argument("--dry-run", action="store_true")
    plan_parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            report = validate(args.source_root, args.min_free_gib)
        else:
            report = plan(args.source_root, args.min_free_gib, args.out_dir)
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
