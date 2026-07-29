"""Static unit tests for the read-only Focus Browser macOS planner."""

import importlib.util
import io
import json
import plistlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PLATFORM_DIR / "focus_macos.py"
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))
SPEC = importlib.util.spec_from_file_location("focus_macos", MODULE_PATH)
focus_macos = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(focus_macos)


class FocusMacPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.source_root = Path(self.temporary.name) / "src"
        for relative in ("chrome/browser", "components", "third_party"):
            (self.source_root / relative).mkdir(parents=True, exist_ok=True)
        (self.source_root / "BUILD.gn").write_text("# fixture\n", encoding="utf-8")
        self.write_version("150", "0", "7871", "128")
        for relative, tokens in focus_macos.CHROMIUM_INCOGNITO_SOURCE_CONTRACTS.items():
            contract_path = self.source_root / relative
            contract_path.parent.mkdir(parents=True, exist_ok=True)
            contract_path.write_text("\n".join(tokens) + "\n", encoding="utf-8")
        self.write_chromium_macos_contracts()
        self.developer_dir = self.make_fake_xcode()

    def tearDown(self):
        self.temporary.cleanup()

    def write_version(self, major, minor, build, patch):
        (self.source_root / "chrome" / "VERSION").write_text(
            "MAJOR={}\nMINOR={}\nBUILD={}\nPATCH={}\n".format(
                major, minor, build, patch
            ),
            encoding="utf-8",
        )

    @staticmethod
    def write_plist(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as stream:
            plistlib.dump(value, stream)

    def write_chromium_macos_contracts(self):
        sdk_gni = self.source_root / focus_macos.CHROMIUM_MAC_SDK_GNI
        sdk_gni.parent.mkdir(parents=True, exist_ok=True)
        sdk_gni.write_text(
            'mac_deployment_target = "12.0"\n'
            'mac_min_system_version = "12.0"\n'
            'mac_sdk_official_version = "26.5"\n'
            'mac_sdk_official_build_version = "25F70"\n',
            encoding="utf-8",
        )
        universalizer = self.source_root / focus_macos.CHROMIUM_UNIVERSALIZER
        universalizer.parent.mkdir(parents=True, exist_ok=True)
        universalizer.write_text(
            "def universalize(input_paths, output_path):\n"
            "    pass\n"
            "parser.add_argument('output', help='output')\n"
            "universalize(parsed.inputs, parsed.output)\n",
            encoding="utf-8",
        )

    def make_fake_xcode(self):
        app = Path(self.temporary.name) / "Xcode Beta.app"
        contents = app / "Contents"
        developer = contents / "Developer"
        self.write_plist(
            contents / "version.plist",
            {
                "CFBundleShortVersionString": "27.0",
                "CFBundleVersion": "25183.64.12",
                "ProductBuildVersion": "27A5228h",
            },
        )
        self.write_plist(
            contents / "Info.plist",
            {"DTXcodeBuild": "27A5228g"},
        )
        platform_dir = developer / "Platforms" / "MacOSX.platform"
        self.write_plist(
            platform_dir / "Info.plist",
            {"Version": "27.0", "CFBundleShortVersionString": "27.0"},
        )
        self.write_plist(
            platform_dir / "version.plist",
            {
                "CFBundleShortVersionString": "27.0",
                "ProductBuildVersion": "26A5388f",
            },
        )
        sdks_root = platform_dir / "Developer" / "SDKs"
        sdk_root = sdks_root / "MacOSX.sdk"
        self.write_plist(
            sdk_root / "SDKSettings.plist",
            {
                "Version": "27.0",
                "CanonicalName": "macosx27.0",
                "DisplayName": "macOS 27.0",
                "SupportedTargets": {
                    "macosx": {
                        "Archs": ["x86_64", "x86_64h", "arm64", "arm64e"],
                        "MinimumDeploymentTarget": "12.0",
                    }
                },
            },
        )
        self.write_plist(
            sdk_root
            / "System"
            / "Library"
            / "CoreServices"
            / "SystemVersion.plist",
            {
                "ProductName": "macOS",
                "ProductVersion": "27.0",
                "ProductBuildVersion": "26A5388f",
            },
        )
        (sdks_root / "MacOSX27.0.sdk").symlink_to("MacOSX.sdk", target_is_directory=True)
        for executable in (
            developer / "usr" / "bin" / "xcodebuild",
            developer
            / "Toolchains"
            / "XcodeDefault.xctoolchain"
            / "usr"
            / "bin"
            / "clang",
        ):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"fixture\n")
            executable.chmod(0o755)
        return developer

    def pinned_source_hashes(self):
        real_sha256_file = focus_macos.sha256_file
        pinned = {
            (self.source_root / focus_macos.CHROMIUM_MAC_SDK_GNI).resolve():
                focus_macos.PINNED_CHROMIUM_MAC_SDK_GNI_SHA256,
            (self.source_root / focus_macos.CHROMIUM_UNIVERSALIZER).resolve():
                focus_macos.PINNED_CHROMIUM_UNIVERSALIZER_SHA256,
        }

        def sha256_fixture(path):
            return pinned.get(Path(path).resolve(), real_sha256_file(path))

        return mock.patch.object(
            focus_macos, "sha256_file", side_effect=sha256_fixture
        )

    @staticmethod
    def disk_usage(free_gib=512):
        free = free_gib * (1024 ** 3)
        return SimpleNamespace(total=free * 2, used=free, free=free)

    def test_repository_contract_preserves_branding_features_and_overlay(self):
        report = focus_macos.validate_repository_contract()
        self.assertEqual("150.0.7871.128", report["chromium_pin"])
        self.assertEqual("com.focusbrowser.browser", report["branding"]["bundle_id"])
        self.assertEqual("Focus Browser", report["branding"]["product_fullname"])
        self.assertEqual("English (UK)", report["locales"]["languages"]["en-GB"])
        self.assertEqual("Russian", report["locales"]["languages"]["ru"])
        self.assertTrue(report["features"]["FocusBlock"]["service"])
        self.assertTrue(report["features"]["FocusYoutube"]["component_integration"])
        self.assertEqual(25, report["features"]["FocusYoutube"]["native_controls"])
        self.assertEqual(29, report["features"]["FocusYoutube"]["storage_keys"])
        self.assertEqual(15, report["overlay"]["excluded_count"])
        self.assertGreater(len(report["overlay"]["planned_cleanup_paths"]), 0)
        self.assertFalse(report["overlay"]["delete_manifest_executed"])
        self.assertEqual("off", report["updater"])
        self.assertEqual("off", report["signing"]["developer_id"])
        self.assertFalse(report["signing"]["paid_account_required"])
        self.assertEqual("off", report["notarization"])
        self.assertEqual("local_macos_only", report["distribution"])
        self.assertFalse(report["local_installation"]["dmg_account_required"])
        self.assertEqual(
            "blocked_pending_legal_and_component_evidence",
            report["redistribution_gate"]["status"],
        )
        self.assertFalse(report["redistribution_gate"]["redistribution_allowed"])
        self.assertEqual(
            "native_chromium_off_the_record",
            report["incognito"]["implementation"],
        )
        self.assertTrue(report["incognito"]["command_shift_n_locked"])
        self.assertFalse(report["incognito"]["runtime_verified"])

    def test_common_series_is_exactly_pinned_filtered_and_ordered(self):
        report = focus_macos.validate_common_series()
        self.assertEqual(323, report["total_entries"])
        self.assertEqual(321, report["planned_entries"])
        self.assertEqual(focus_macos.COMMON_SERIES_SHA256, report["sha256"])
        self.assertEqual(
            focus_macos.FILTERED_COMMON_SERIES_SHA256,
            report["filtered_order_sha256"],
        )
        required = {item["path"]: item for item in report["required_patches"]}
        self.assertEqual(102, required["focus/core/change-chromium-branding.patch"]["position"])
        self.assertEqual(319, required["focus/core/focusblock-native-service.patch"]["position"])
        self.assertEqual(
            97,
            report["exclusion_positions"]["focus/core/windows-first-run-locale.patch"],
        )
        protected = report["protected_incognito_inventory"]
        self.assertEqual(focus_macos.EXPECTED_PROTECTED_PATCH_COUNT, protected["count"])
        self.assertEqual(focus_macos.EXPECTED_PROTECTED_PATCH_SHA256, protected["sha256"])
        full = report["full_body_inventory"]
        self.assertEqual(focus_macos.EXPECTED_FULL_PATCH_BODY_COUNT, full["count"])
        self.assertEqual(focus_macos.EXPECTED_FULL_PATCH_BODY_SHA256, full["sha256"])
        portable = report["portable_delete_create"]
        self.assertEqual(
            focus_macos.EXPECTED_PORTABLE_DELETE_CREATE_COUNT,
            portable["pair_count"],
        )
        self.assertEqual(
            focus_macos.EXPECTED_PORTABLE_DELETE_CREATE_SHA256,
            portable["sha256"],
        )
        self.assertEqual(
            245,
            required[
                "focus/core/rename-focus-import-product-layer.patch"
            ]["position"],
        )
        self.assertEqual(
            246,
            required["focus/core/rename-focus-import-internals.patch"]["position"],
        )

    def test_common_patch_portability_rejects_git_only_rename_metadata(self):
        patch = Path(self.temporary.name) / "raw-git-rename.patch"
        patch.write_text(
            "diff --git a/old.txt b/new.txt\n"
            "similarity index 50%\n"
            "rename from old.txt\n"
            "rename to new.txt\n"
            "--- a/old.txt\n"
            "+++ b/new.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            focus_macos.ContractError, "Git-only patch metadata"
        ):
            focus_macos.scan_common_patch_path_operations(patch)

    def test_common_patch_portability_ignores_header_like_hunk_payload(self):
        patch = Path(self.temporary.name) / "header-like-payload.patch"
        patch.write_text(
            "diff --git a/value.txt b/value.txt\n"
            "--- a/value.txt\n"
            "+++ b/value.txt\n"
            "@@ -1 +1 @@\n"
            "--- old marker\n"
            "+++ new marker\n",
            encoding="utf-8",
        )
        self.assertEqual(
            {"deletions": [], "creations": []},
            focus_macos.scan_common_patch_path_operations(patch),
        )

    def test_i18n_catalogs_have_semantic_ru_en_contracts(self):
        report = focus_macos.validate_i18n_catalogs()
        self.assertEqual({"source": 267, "en-GB": 165, "ru": 189}, report["catalog_counts"])
        self.assertTrue(report["placeholder_contracts"])
        self.assertEqual([], report["source_reference_gaps"]["en-GB"])
        self.assertEqual(2, len(report["source_reference_gaps"]["ru"]))
        self.assertIn(
            "IDS_SETTINGS_FOCUS_SERVICES_TOGGLE",
            report["required_message_contracts"],
        )

    def test_feature_contracts_cover_service_bubbles_and_component(self):
        report = focus_macos.validate_feature_contracts()
        self.assertEqual(len(focus_macos.FOCUS_FEATURE_HASHES), len(report["hash_pinned_files"]))
        self.assertEqual(4096, report["FocusBlock"]["bounded_startup_queue"])
        self.assertTrue(report["FocusBlock"]["bubble_ru_en"])
        self.assertEqual(4, report["FocusYoutube"]["schema_version"])
        self.assertEqual(
            "jafokmemnknjknbdiklabcnhlpheefbm",
            report["FocusYoutube"]["component_id"],
        )

    def test_icns_is_real_hash_pinned_and_multiresolution(self):
        report = focus_macos.validate_icns_asset()
        self.assertEqual(focus_macos.FOCUS_ICNS_SHA256, report["icns_sha256"])
        self.assertEqual([1024, 1024], report["canonical_png_dimensions"])
        self.assertEqual(
            [[32, 32], [64, 64], [128, 128], [256, 256], [512, 512], [1024, 1024]],
            report["embedded_png_dimensions"],
        )
        self.assertIn("ic10", report["chunk_types"])

    def test_icns_parser_rejects_truncated_container(self):
        corrupt = Path(self.temporary.name) / "corrupt.icns"
        corrupt.write_bytes(b"icns\x00\x00\x00\x10short")
        with self.assertRaises(focus_macos.IconContractError):
            focus_macos.inspect_icns(corrupt)

    def test_icon_generator_is_explicit_fixed_and_uses_system_tools(self):
        source = (PLATFORM_DIR / "generate_icns.py").read_text(encoding="utf-8")
        for required in (
            "TemporaryDirectory",
            '"/usr/bin/sips"',
            '"/usr/bin/iconutil"',
            'operation.add_argument(\n        "--generate"',
            "refusing to overwrite",
            'OUTPUT = MACOS_DIR / "resources" / "FocusBrowser.icns"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

    def test_legal_inventory_stays_hard_blocked(self):
        report = focus_macos.validate_legal_inventory()
        components = " ".join(item["component"] for item in report["inventory"])
        blockers = " ".join(report["manual_blockers"])
        for required in ("uBlock", "Ghostery", "Unhook", "FFmpeg", "Widevine"):
            self.assertIn(required, components + " " + blockers)
        self.assertIn("Apple App Store", blockers)
        self.assertIn("territories/worldwide", blockers)
        self.assertFalse(report["redistribution_allowed"])

    def test_platform_patch_series_is_hash_pinned_and_ordered(self):
        patches = focus_macos.validate_platform_patch_series()
        self.assertEqual(
            [
                "patches/focus/windows/focusblock-location-bar-shield.patch",
                "patches/focus/windows/focusyoutube-native-popup.patch",
                "platform/macos/patches/native-incognito-contract.patch",
            ],
            [item["path"] for item in patches],
        )
        self.assertEqual([1, 2, 3], [item["order"] for item in patches])
        self.assertEqual(4, patches[-1]["target_count"])

    def test_incognito_contract_covers_native_ui_services_and_honest_copy(self):
        report = focus_macos.validate_incognito_repository_contract()
        self.assertEqual(
            ["File > New Incognito Window", "Command-Shift-N"],
            report["macos_entry_points"],
        )
        self.assertTrue(report["private_window_identity_marker_enforced_on_macos"])
        self.assertTrue(report["runtime_theme_override_removed_by_macos_patch"])
        self.assertTrue(report["custom_ntp_blocked_for_otr_on_macos"])
        self.assertTrue(report["focusblock_own_otr_service"])
        self.assertTrue(report["focusyoutube_component_allowed_in_incognito"])
        self.assertFalse(report["focusyoutube_storage_isolation_verified"])
        self.assertFalse(report["incognito_storage_quota"]["ephemerality_evidence"])
        self.assertEqual(
            "higher_memory_pressure_and_availability_risk",
            report["incognito_storage_quota"]["tradeoff"],
        )
        self.assertFalse(report["opinionated_policy_private_mode_override"])
        overlay_inventory = report["protected_overlay_inventory"]
        self.assertEqual(
            focus_macos.EXPECTED_PROTECTED_OVERLAY_COUNT,
            overlay_inventory["count"],
        )
        self.assertEqual(
            focus_macos.EXPECTED_PROTECTED_OVERLAY_SHA256,
            overlay_inventory["sha256"],
        )
        full_overlay = report["full_overlay_body_inventory"]
        self.assertEqual(
            focus_macos.EXPECTED_FULL_OVERLAY_BODY_COUNT,
            full_overlay["count"],
        )
        self.assertEqual(
            focus_macos.EXPECTED_FULL_OVERLAY_BODY_SHA256,
            full_overlay["sha256"],
        )
        disclosure = report["privacy_disclosure"]
        self.assertIn("internet provider", disclosure["en"])
        self.assertIn("интернет-провайдер", disclosure["ru"])
        self.assertNotIn("won't leave any traces", disclosure["en"])

    def test_chromium_incognito_source_contract_is_static_not_runtime_claim(self):
        report = focus_macos.validate_chromium_incognito_source(self.source_root)
        self.assertTrue(report["native_otr_profile_creation_sentinels_present"])
        self.assertTrue(report["storage_partition_shutdown_sentinels_present"])
        self.assertTrue(report["history_service_guard_sentinels_present"])
        self.assertTrue(report["session_service_scope_sentinels_present"])
        self.assertFalse(report["semantic_or_runtime_proof"])
        self.assertNotIn("history_service_blocked_for_otr", report)
        self.assertFalse(report["runtime_verified"])

    def test_chromium_incognito_source_contract_fails_when_semantics_disappear(self):
        source_path = self.source_root / "chrome/browser/history/history_tab_helper.cc"
        source_path.write_text("return history_service;\n", encoding="utf-8")
        with self.assertRaisesRegex(focus_macos.ContractError, "Chromium Incognito source"):
            focus_macos.validate_chromium_incognito_source(self.source_root)

    def test_chromium_incognito_source_contract_ignores_comment_spoof(self):
        source_path = self.source_root / "chrome/browser/history/history_tab_helper.cc"
        source_path.write_text(
            "/* if (profile->IsOffTheRecord())\n"
            "   return nullptr; */\n"
            "return history_service;\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(focus_macos.ContractError, "Chromium Incognito source"):
            focus_macos.validate_chromium_incognito_source(self.source_root)

    def test_cpp_comment_stripper_preserves_literals_and_inline_arguments(self):
        stripped = focus_macos.strip_cpp_comments(
            'const char* url = "https://focusbrowser.app";\n'
            "auto* profile = GetPrimaryOTRProfile(/*create_if_needed=*/true);\n"
            "// return nullptr;\n"
        )
        self.assertIn('"https://focusbrowser.app"', stripped)
        self.assertIn("GetPrimaryOTRProfile(", stripped)
        self.assertIn("true)", stripped)
        self.assertNotIn("return nullptr", stripped)

    def test_unified_diff_parser_rejects_hunk_count_mismatch(self):
        malformed = Path(self.temporary.name) / "malformed.patch"
        malformed.write_text(
            "--- a/example.cc\n"
            "+++ b/example.cc\n"
            "@@ -1,2 +1,1 @@\n"
            " context\n"
            "-removed-one\n"
            "-removed-two\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(focus_macos.ContractError, "hunk count mismatch"):
            focus_macos.validate_unified_diff_syntax(malformed)

    def test_unified_diff_parser_sees_all_native_incognito_targets(self):
        pairs = focus_macos.validate_unified_diff_syntax(
            PLATFORM_DIR / "patches" / "native-incognito-contract.patch"
        )
        self.assertEqual(
            {
                "chrome/browser/ui/browser_shortcuts/browser_shortcut_metadata.cc",
                "chrome/browser/ui/views/frame/browser_widget.cc",
                "chrome/browser/ungoogled_flag_entries.h",
                "chrome/browser/ui/webui/ntp/ntp_resource_cache.cc",
            },
            {new or old for old, new in pairs},
        )

    def test_unified_diff_parser_rejects_body_outside_hunk(self):
        malformed = Path(self.temporary.name) / "stray-body.patch"
        malformed.write_text(
            "+stray\n"
            "--- a/example.cc\n+++ b/example.cc\n@@ -1 +1 @@\n-old\n+new\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(focus_macos.ContractError, "unscoped"):
            focus_macos.validate_unified_diff_syntax(malformed)

    def write_patch_fixture(self, name, text):
        patch_root = Path(self.temporary.name) / "patches"
        patch_root.mkdir(exist_ok=True)
        patch_path = patch_root / name
        patch_path.write_text(text, encoding="utf-8")
        return patch_root

    def test_protected_inventory_selects_sensitive_hunk_in_unrelated_file(self):
        patch_root = self.write_patch_fixture(
            "ordinary.patch",
            "--- a/example.cc\n+++ b/example.cc\n@@ -1 +1 @@\n"
            "-enabled = true;\n+IncognitoModeAvailability::kDisabled;\n",
        )
        report = focus_macos.build_protected_patch_inventory(
            ["ordinary.patch"], patch_root
        )
        self.assertEqual(1, report["count"])

    def test_protected_inventory_selects_neutral_edit_to_protected_target(self):
        patch_root = self.write_patch_fixture(
            "neutral.patch",
            "--- a/chrome/browser/history/history_tab_helper.cc\n"
            "+++ b/chrome/browser/history/history_tab_helper.cc\n"
            "@@ -1 +1 @@\n-old_name\n+new_name\n",
        )
        report = focus_macos.build_protected_patch_inventory(
            ["neutral.patch"], patch_root
        )
        self.assertEqual(1, report["count"])

    def test_protected_inventory_hashes_entire_selected_patch(self):
        patch_root = self.write_patch_fixture(
            "selected.patch",
            "--- a/example.cc\n+++ b/example.cc\n@@ -1 +1 @@\n"
            "-incognito = false;\n+incognito = true;\n",
        )
        first = focus_macos.build_protected_patch_inventory(
            ["selected.patch"], patch_root
        )
        (patch_root / "selected.patch").write_text(
            (patch_root / "selected.patch").read_text(encoding="utf-8")
            + "\n--- a/unrelated.cc\n+++ b/unrelated.cc\n@@ -1 +1 @@\n-a\n+b\n",
            encoding="utf-8",
        )
        second = focus_macos.build_protected_patch_inventory(
            ["selected.patch"], patch_root
        )
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_full_patch_inventory_covers_indirect_extension_storage_change(self):
        patch_root = self.write_patch_fixture(
            "neutral-name.patch",
            "--- a/extensions/browser/api/storage/storage_frontend.cc\n"
            "+++ b/extensions/browser/api/storage/storage_frontend.cc\n"
            "@@ -1 +1 @@\n"
            "-return context;\n"
            "+return context->GetOriginalContext();\n",
        )
        protected = focus_macos.build_protected_patch_inventory(
            ["neutral-name.patch"], patch_root
        )
        self.assertEqual(0, protected["count"])
        first = focus_macos.build_full_patch_body_inventory(
            ["neutral-name.patch"], patch_root
        )
        (patch_root / "neutral-name.patch").write_text(
            "--- a/extensions/browser/api/storage/storage_frontend.cc\n"
            "+++ b/extensions/browser/api/storage/storage_frontend.cc\n"
            "@@ -1 +1 @@\n"
            "-return context;\n"
            "+return context->GetOriginalContext()->GetOriginalContext();\n",
            encoding="utf-8",
        )
        second = focus_macos.build_full_patch_body_inventory(
            ["neutral-name.patch"], patch_root
        )
        self.assertEqual(1, first["count"])
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_protected_overlay_inventory_selects_sensitive_new_source(self):
        overlay_root = Path(self.temporary.name) / "overlay-sensitive"
        source_path = overlay_root / "chrome/browser/example.cc"
        source_path.parent.mkdir(parents=True)
        source_path.write_text(
            "auto mode = IncognitoModeAvailability::kDisabled;\n",
            encoding="utf-8",
        )
        report = focus_macos.build_protected_overlay_inventory(overlay_root)
        self.assertEqual(1, report["count"])

    def test_protected_overlay_inventory_selects_neutral_critical_path(self):
        overlay_root = Path(self.temporary.name) / "overlay-path"
        source_path = overlay_root / "chrome/browser/history/history_tab_helper.cc"
        source_path.parent.mkdir(parents=True)
        source_path.write_text("int renamed_value = 1;\n", encoding="utf-8")
        report = focus_macos.build_protected_overlay_inventory(overlay_root)
        self.assertEqual(1, report["count"])

    def test_full_overlay_inventory_covers_indirect_extension_storage_change(self):
        overlay_root = Path(self.temporary.name) / "overlay-storage"
        source_path = (
            overlay_root / "extensions/browser/api/storage/storage_frontend.cc"
        )
        source_path.parent.mkdir(parents=True)
        source_path.write_text(
            "return context->GetOriginalContext();\n",
            encoding="utf-8",
        )
        protected = focus_macos.build_protected_overlay_inventory(overlay_root)
        self.assertEqual(0, protected["count"])
        first = focus_macos.build_full_overlay_body_inventory(overlay_root)
        source_path.write_text(
            "return context->GetOriginalContext()->GetOriginalContext();\n",
            encoding="utf-8",
        )
        second = focus_macos.build_full_overlay_body_inventory(overlay_root)
        self.assertEqual(1, first["count"])
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_full_body_inventories_reject_symlinks(self):
        outside = Path(self.temporary.name) / "outside.patch"
        outside.write_text("external\n", encoding="utf-8")
        patch_root = Path(self.temporary.name) / "symlink-patches"
        patch_root.mkdir()
        (patch_root / "linked.patch").symlink_to(outside)
        with self.assertRaisesRegex(focus_macos.ContractError, "symlink"):
            focus_macos.build_full_patch_body_inventory(
                ["linked.patch"], patch_root
            )

        overlay_root = Path(self.temporary.name) / "symlink-overlay"
        overlay_root.mkdir()
        (overlay_root / "linked.cc").symlink_to(outside)
        with self.assertRaisesRegex(focus_macos.ContractError, "symlink"):
            focus_macos.build_full_overlay_body_inventory(overlay_root)

    def test_exact_version_is_accepted(self):
        root, version = focus_macos.resolve_source_root(str(self.source_root))
        self.assertEqual(self.source_root.resolve(), root)
        self.assertEqual("150.0.7871.128", version)

    def test_version_mismatch_is_rejected(self):
        self.write_version("150", "0", "7871", "129")
        with self.assertRaisesRegex(focus_macos.ContractError, "version mismatch"):
            focus_macos.resolve_source_root(str(self.source_root))

    def test_missing_source_root_is_rejected(self):
        with self.assertRaisesRegex(focus_macos.ContractError, "does not exist"):
            focus_macos.resolve_source_root(str(self.source_root / "missing"))

    def test_xcode_toolchain_plists_pin_beta_build_sdk_and_both_architectures(self):
        report = focus_macos.validate_xcode_toolchain(str(self.developer_dir))
        self.assertEqual("27A5228h", report["xcode"]["build"])
        self.assertEqual("27.0", report["sdk"]["version"])
        self.assertEqual("26A5388f", report["sdk"]["build"])
        self.assertEqual("macosx27.0", report["sdk"]["canonical_name"])
        self.assertIn("arm64", report["sdk"]["architectures"])
        self.assertIn("x86_64", report["sdk"]["architectures"])
        self.assertEqual("12.0", report["sdk"]["minimum_deployment_target"])
        self.assertTrue(report["identity_validated"])
        self.assertFalse(report["subprocess_executed"])
        self.assertFalse(report["build_compatibility_runtime_verified"])

    def test_xcode_toolchain_rejects_wrong_xcode_build(self):
        self.write_plist(
            self.developer_dir.parent / "version.plist",
            {
                "CFBundleShortVersionString": "27.0",
                "ProductBuildVersion": "27A5228g",
            },
        )
        with self.assertRaisesRegex(focus_macos.ContractError, "ProductBuildVersion"):
            focus_macos.validate_xcode_toolchain(str(self.developer_dir))

    def test_xcode_toolchain_rejects_sdk_alias_escape(self):
        alias = (
            self.developer_dir
            / "Platforms"
            / "MacOSX.platform"
            / "Developer"
            / "SDKs"
            / "MacOSX27.0.sdk"
        )
        alias.unlink()
        outside = Path(self.temporary.name) / "outside-sdk"
        outside.mkdir()
        alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(focus_macos.ContractError, "SDK alias"):
            focus_macos.validate_xcode_toolchain(str(self.developer_dir))

    def test_xcode_toolchain_rejects_intermediate_platform_symlink_escape(self):
        platform_dir = self.developer_dir / "Platforms" / "MacOSX.platform"
        outside = Path(self.temporary.name) / "external-platform"
        platform_dir.rename(outside)
        platform_dir.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(focus_macos.ContractError, "symlinks"):
            focus_macos.validate_xcode_toolchain(str(self.developer_dir))

    def test_xcode_toolchain_allows_only_the_versioned_sdk_alias(self):
        sdks_root = (
            self.developer_dir
            / "Platforms"
            / "MacOSX.platform"
            / "Developer"
            / "SDKs"
        )
        canonical = sdks_root / "MacOSX.sdk"
        real_sdk = sdks_root / "RealMacOSX.sdk"
        canonical.rename(real_sdk)
        canonical.symlink_to(real_sdk.name, target_is_directory=True)
        with self.assertRaisesRegex(focus_macos.ContractError, "symlinks"):
            focus_macos.validate_xcode_toolchain(str(self.developer_dir))

    def test_xcode_toolchain_rejects_nested_sdk_system_symlink_escape(self):
        sdk_root = (
            self.developer_dir
            / "Platforms"
            / "MacOSX.platform"
            / "Developer"
            / "SDKs"
            / "MacOSX.sdk"
        )
        system = sdk_root / "System"
        outside = Path(self.temporary.name) / "external-sdk-system"
        system.rename(outside)
        system.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(focus_macos.ContractError, "symlinks"):
            focus_macos.validate_xcode_toolchain(str(self.developer_dir))

    def test_xcode_toolchain_rejects_missing_architecture_and_minimum_drift(self):
        settings = (
            self.developer_dir
            / "Platforms"
            / "MacOSX.platform"
            / "Developer"
            / "SDKs"
            / "MacOSX.sdk"
            / "SDKSettings.plist"
        )
        base = plistlib.loads(settings.read_bytes())
        base["SupportedTargets"]["macosx"]["Archs"] = ["arm64"]
        self.write_plist(settings, base)
        with self.assertRaisesRegex(focus_macos.ContractError, "arm64 and x86_64"):
            focus_macos.validate_xcode_toolchain(str(self.developer_dir))

        base["SupportedTargets"]["macosx"]["Archs"] = ["arm64", "x86_64"]
        base["SupportedTargets"]["macosx"]["MinimumDeploymentTarget"] = "13.0"
        self.write_plist(settings, base)
        with self.assertRaisesRegex(focus_macos.ContractError, "minimum deployment"):
            focus_macos.validate_xcode_toolchain(str(self.developer_dir))

    def test_xcode_toolchain_rejects_sdk_identity_and_missing_executable(self):
        sdk_root = (
            self.developer_dir
            / "Platforms"
            / "MacOSX.platform"
            / "Developer"
            / "SDKs"
            / "MacOSX.sdk"
        )
        settings = sdk_root / "SDKSettings.plist"
        value = plistlib.loads(settings.read_bytes())
        value["CanonicalName"] = "macosx27.1"
        self.write_plist(settings, value)
        with self.assertRaisesRegex(focus_macos.ContractError, "SDK identity"):
            focus_macos.validate_xcode_toolchain(str(self.developer_dir))

        value["CanonicalName"] = "macosx27.0"
        self.write_plist(settings, value)
        system_version = (
            sdk_root / "System" / "Library" / "CoreServices" / "SystemVersion.plist"
        )
        system_value = plistlib.loads(system_version.read_bytes())
        system_value["ProductBuildVersion"] = "26A5388e"
        self.write_plist(system_version, system_value)
        with self.assertRaisesRegex(focus_macos.ContractError, "SDK identity"):
            focus_macos.validate_xcode_toolchain(str(self.developer_dir))

        system_value["ProductBuildVersion"] = "26A5388f"
        self.write_plist(system_version, system_value)
        clang = (
            self.developer_dir
            / "Toolchains"
            / "XcodeDefault.xctoolchain"
            / "usr"
            / "bin"
            / "clang"
        )
        clang.chmod(0o644)
        with self.assertRaisesRegex(focus_macos.ContractError, "Xcode clang"):
            focus_macos.validate_xcode_toolchain(str(self.developer_dir))

    def test_xcode_toolchain_requires_absolute_developer_dir(self):
        with self.assertRaisesRegex(focus_macos.ContractError, "absolute"):
            focus_macos.validate_xcode_toolchain("Xcode.app/Contents/Developer")

    def test_chromium_macos_source_contract_pins_minimum_and_universalizer(self):
        with self.pinned_source_hashes():
            report = focus_macos.validate_chromium_macos_build_contract(
                self.source_root
            )
        self.assertEqual("12.0", report["mac_deployment_target"])
        self.assertEqual("12.0", report["mac_min_system_version"])
        self.assertEqual(["arm64", "x64"], report["supported_target_cpus"])
        self.assertEqual("26.5", report["upstream_official_sdk"]["version"])
        self.assertFalse(report["all_macs_or_macos_claimed"])
        self.assertFalse(report["universal_output"]["assembly_executed"])
        self.assertFalse(report["universal_output"]["signing_executed"])
        self.assertFalse(report["universal_output"]["runtime_verified"])

    def test_chromium_macos_source_contract_rejects_hash_and_minimum_drift(self):
        with self.assertRaisesRegex(focus_macos.ContractError, "mac_sdk.gni hash"):
            focus_macos.validate_chromium_macos_build_contract(self.source_root)

        sdk_gni = self.source_root / focus_macos.CHROMIUM_MAC_SDK_GNI
        sdk_gni.write_text(
            sdk_gni.read_text(encoding="utf-8").replace(
                'mac_min_system_version = "12.0"',
                'mac_min_system_version = "13.0"',
            ),
            encoding="utf-8",
        )
        with self.pinned_source_hashes():
            with self.assertRaisesRegex(focus_macos.ContractError, "mac_min_system_version"):
                focus_macos.validate_chromium_macos_build_contract(self.source_root)

    def test_chromium_macos_source_contract_rejects_universalizer_hash_drift(self):
        real_sha256_file = focus_macos.sha256_file
        sdk_path = (self.source_root / focus_macos.CHROMIUM_MAC_SDK_GNI).resolve()

        def pin_only_sdk(path):
            if Path(path).resolve() == sdk_path:
                return focus_macos.PINNED_CHROMIUM_MAC_SDK_GNI_SHA256
            return real_sha256_file(path)

        with mock.patch.object(
            focus_macos, "sha256_file", side_effect=pin_only_sdk
        ):
            with self.assertRaisesRegex(focus_macos.ContractError, "universalizer hash"):
                focus_macos.validate_chromium_macos_build_contract(self.source_root)

    def test_non_macos_host_is_rejected(self):
        with mock.patch.object(
            focus_macos.platform, "system", return_value="Linux"
        ), mock.patch.object(
            focus_macos.platform, "machine", return_value="x86_64"
        ):
            with self.assertRaisesRegex(focus_macos.ContractError, "macOS host"):
                focus_macos.validate_host()

    def test_intel_macos_planning_host_is_accepted(self):
        with mock.patch.object(
            focus_macos.platform, "system", return_value="Darwin"
        ), mock.patch.object(
            focus_macos.platform, "machine", return_value="x86_64"
        ):
            self.assertEqual(
                {"system": "Darwin", "machine": "x86_64"},
                focus_macos.validate_host(),
            )

    def test_plan_is_read_only_and_targets_only_chrome(self):
        before = sorted(
            (path.relative_to(self.source_root).as_posix(), path.stat().st_mtime_ns)
            for path in self.source_root.rglob("*")
        )
        with self.pinned_source_hashes(), mock.patch.object(
            focus_macos, "validate_host", return_value={"system": "Darwin", "machine": "arm64"}
        ), mock.patch.object(
            focus_macos.shutil, "disk_usage", return_value=self.disk_usage()
        ):
            report = focus_macos.plan(
                str(self.source_root),
                str(self.developer_dir),
                focus_macos.Decimal("180"),
            )
        after = sorted(
            (path.relative_to(self.source_root).as_posix(), path.stat().st_mtime_ns)
            for path in self.source_root.rglob("*")
        )
        self.assertEqual(before, after)
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["build"]["executed"])
        self.assertEqual(["arm64", "x64"], report["build"]["architectures"])
        self.assertEqual("12.0", report["build"]["minimum_macos"])
        self.assertEqual(str(self.source_root.resolve()), report["build"]["working_directory"])
        self.assertEqual(["chrome"], report["build"]["ninja_targets"])
        commands = json.dumps(report["build"]["commands"])
        self.assertNotIn("mini_installer", commands)
        self.assertNotIn("setup", commands)
        arm64 = report["build"]["slices"]["arm64"]
        x64 = report["build"]["slices"]["x64"]
        self.assertEqual("out/FocusMacArm64", arm64["out_dir"])
        self.assertEqual("out/FocusMacX64", x64["out_dir"])
        self.assertIn('target_cpu="arm64"', arm64["args_gn"])
        self.assertIn('target_cpu="x64"', x64["args_gn"])
        developer_assignment = "DEVELOPER_DIR={}".format(
            self.developer_dir.resolve()
        )
        for command in report["build"]["commands"]:
            self.assertEqual(["/usr/bin/env", developer_assignment], command[:2])
            self.assertNotIn("sh", command[:3])
            self.assertNotIn("-c", command[:3])
        universal = report["build"]["universal"]
        self.assertEqual(["x64", "arm64"], universal["input_order"])
        self.assertEqual([x64["app"], arm64["app"]], universal["inputs"])
        self.assertEqual(
            "out/FocusMacUniversal/Focus Browser.app", universal["output"]
        )
        self.assertEqual(
            ["/bin/mkdir", "-p", "out/FocusMacUniversal"],
            universal["parent_directory_command"][-3:],
        )
        self.assertEqual(
            universal["parent_directory_command"], report["build"]["commands"][-2]
        )
        self.assertEqual(universal["command"], report["build"]["commands"][-1])
        self.assertEqual(
            universal["inputs"] + [universal["output"]],
            universal["command"][-3:],
        )
        self.assertFalse(universal["assembly_executed"])
        self.assertFalse(universal["signing_executed"])
        self.assertFalse(universal["runtime_verified"])

    def test_plan_fails_closed_when_disk_gate_is_too_high(self):
        with self.pinned_source_hashes(), mock.patch.object(
            focus_macos, "validate_host", return_value={"system": "Darwin", "machine": "arm64"}
        ), mock.patch.object(
            focus_macos.shutil, "disk_usage", return_value=self.disk_usage(10)
        ):
            with self.assertRaisesRegex(focus_macos.ContractError, "disk gate failed"):
                focus_macos.plan(
                    str(self.source_root),
                    str(self.developer_dir),
                    focus_macos.Decimal("20"),
                )

    def test_out_dir_cannot_escape_source_root(self):
        for value in ("../out", "/tmp/out", "out\\Default"):
            with self.subTest(value=value):
                with self.assertRaises(focus_macos.ContractError):
                    focus_macos.normalise_out_dir(value)

    def write_gn_fixture(self, text, name="flags.gn"):
        path = Path(self.temporary.name) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_gn_parser_accepts_only_complete_typed_assignments(self):
        path = self.write_gn_fixture(
            'target_os = "mac"\n'
            'target_cpu="arm64"\n'
            'mac_deployment_target="12.0"\n'
            'mac_min_system_version="12.0"\n'
            "use_system_xcode=true\n"
            "is_component_build=false\n"
            "is_debug=false\n"
            "is_official_build=true\n"
            "enable_updater=false\n"
            "include_branded_entitlements=false\n"
            "use_siso=false\n"
            "use_remoteexec=false\n"
            "symbol_level=0\n"
        )
        composed, names = focus_macos.parse_gn_assignments((path,))
        self.assertIn('target_os = "mac"', composed)
        self.assertIn("symbol_level", names)

    def test_gn_parser_rejects_trailing_garbage_and_inline_comments(self):
        suffixes = (' garbage', ' # inline comment', '; another=true')
        for index, suffix in enumerate(suffixes):
            path = self.write_gn_fixture(
                'target_os="mac"{}\n'.format(suffix)
                + 'target_cpu="arm64"\n'
                + 'mac_deployment_target="12.0"\n'
                + 'mac_min_system_version="12.0"\n'
                + "use_system_xcode=true\n"
                + "is_component_build=false\n"
                + "is_debug=false\n"
                + "is_official_build=true\n"
                + "enable_updater=false\n"
                + "include_branded_entitlements=false\n"
                + "use_siso=false\n"
                + "use_remoteexec=false\n",
                "invalid-{}.gn".format(index),
            )
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(focus_macos.ContractError, "invalid GN assignment"):
                    focus_macos.parse_gn_assignments((path,))

    def test_gn_parser_rejects_duplicate_assignments(self):
        first = self.write_gn_fixture(
            'target_os="mac"\n'
            'target_cpu="arm64"\n'
            'mac_deployment_target="12.0"\n'
            'mac_min_system_version="12.0"\n'
            "use_system_xcode=true\n"
            "is_component_build=false\n"
            "is_debug=false\n"
            "is_official_build=true\n"
            "enable_updater=false\n"
            "include_branded_entitlements=false\n"
            "use_siso=false\n"
            "use_remoteexec=false\n",
            "first.gn",
        )
        second = self.write_gn_fixture('target_cpu="arm64"\n', "second.gn")
        with self.assertRaisesRegex(focus_macos.ContractError, "duplicate GN arg target_cpu"):
            focus_macos.parse_gn_assignments((first, second))

    def test_gn_parser_rejects_wrong_required_value(self):
        path = self.write_gn_fixture(
            'target_os="mac"\n'
            'target_cpu="x64"\n'
            'mac_deployment_target="12.0"\n'
            'mac_min_system_version="12.0"\n'
            "use_system_xcode=true\n"
            "is_component_build=false\n"
            "is_debug=false\n"
            "is_official_build=true\n"
            "enable_updater=false\n"
            "include_branded_entitlements=false\n"
            "use_siso=false\n"
            "use_remoteexec=false\n"
        )
        with self.assertRaisesRegex(focus_macos.ContractError, "target_cpu"):
            focus_macos.parse_gn_assignments((path,))

    def test_arm64_x64_gn_profiles_may_differ_only_by_target_cpu(self):
        common = self.write_gn_fixture("", "common.gn")
        base = (
            'target_os="mac"\n'
            'target_cpu="{}"\n'
            'mac_deployment_target="12.0"\n'
            'mac_min_system_version="12.0"\n'
            "use_system_xcode=true\n"
            "is_component_build=false\n"
            "is_debug=false\n"
            "is_official_build=true\n"
            "enable_updater=false\n"
            "include_branded_entitlements=false\n"
            "use_siso=false\n"
            "use_remoteexec=false\n"
            "symbol_level={}\n"
        )
        arm64 = self.write_gn_fixture(base.format("arm64", 0), "arm64.gn")
        x64 = self.write_gn_fixture(base.format("x64", 0), "x64.gn")
        with mock.patch.object(focus_macos, "COMMON_FLAGS", common), mock.patch.object(
            focus_macos, "MACOS_FLAGS", {"arm64": arm64, "x64": x64}
        ):
            report = focus_macos.validate_gn_profiles()
        self.assertTrue(report["profiles_equal_except_target_cpu"])

        x64.write_text(base.format("x64", 1), encoding="utf-8")
        with mock.patch.object(focus_macos, "COMMON_FLAGS", common), mock.patch.object(
            focus_macos, "MACOS_FLAGS", {"arm64": arm64, "x64": x64}
        ):
            with self.assertRaisesRegex(focus_macos.ContractError, "symbol_level"):
                focus_macos.validate_gn_profiles()

    def test_repository_profiles_disable_updater_and_remote_build_services(self):
        for architecture, flags_path in focus_macos.MACOS_FLAGS.items():
            with self.subTest(architecture=architecture):
                _, _, values = focus_macos.parse_gn_assignments(
                    (focus_macos.COMMON_FLAGS, flags_path),
                    expected_target_cpu=architecture,
                    include_values=True,
                )
                self.assertEqual("false", values["enable_updater"])
                self.assertEqual("false", values["include_branded_entitlements"])
                self.assertEqual("false", values["use_siso"])
                self.assertEqual("false", values["use_remoteexec"])

    def test_validate_parser_requires_explicit_source_root(self):
        parser = focus_macos.build_parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["validate"])

    def test_validate_and_plan_parsers_require_explicit_developer_dir(self):
        parser = focus_macos.build_parser()
        for arguments in (
            ["validate", "--source-root", "/tmp/src"],
            ["plan", "--source-root", "/tmp/src", "--min-free-gib", "180"],
        ):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(arguments)

    def test_json_mode_returns_machine_readable_error(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = focus_macos.main(
                [
                    "validate",
                    "--source-root",
                    str(self.source_root / "missing"),
                    "--developer-dir",
                    str(self.developer_dir),
                    "--json",
                ]
            )
        self.assertEqual(2, result)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["ok"])

    def test_cli_has_no_network_copy_delete_or_process_execution(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "import subprocess",
            "import urllib",
            "import requests",
            "copy2(",
            "rmtree(",
            ".unlink(",
            ".write_text(",
            "os.system(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
