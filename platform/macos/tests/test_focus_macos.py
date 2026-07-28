"""Static unit tests for the read-only Focus Browser macOS planner."""

import importlib.util
import io
import json
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
        self.assertEqual("off", report["signing"])
        self.assertEqual("off", report["notarization"])
        self.assertEqual(
            "blocked_pending_legal_and_component_evidence",
            report["redistribution_gate"]["status"],
        )
        self.assertFalse(report["redistribution_gate"]["redistribution_allowed"])

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
            ],
            [item["path"] for item in patches],
        )
        self.assertEqual([1, 2], [item["order"] for item in patches])

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

    def test_non_apple_silicon_host_is_rejected(self):
        with mock.patch.object(focus_macos.platform, "system", return_value="Linux"), mock.patch.object(
            focus_macos.platform, "machine", return_value="x86_64"
        ):
            with self.assertRaisesRegex(focus_macos.ContractError, "Apple Silicon"):
                focus_macos.validate_host()

    def test_plan_is_read_only_and_targets_only_chrome(self):
        before = sorted(
            (path.relative_to(self.source_root).as_posix(), path.stat().st_mtime_ns)
            for path in self.source_root.rglob("*")
        )
        with mock.patch.object(
            focus_macos, "validate_host", return_value={"system": "Darwin", "machine": "arm64"}
        ), mock.patch.object(
            focus_macos.shutil, "disk_usage", return_value=self.disk_usage()
        ):
            report = focus_macos.plan(str(self.source_root), focus_macos.Decimal("180"))
        after = sorted(
            (path.relative_to(self.source_root).as_posix(), path.stat().st_mtime_ns)
            for path in self.source_root.rglob("*")
        )
        self.assertEqual(before, after)
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["build"]["executed"])
        self.assertEqual(["chrome"], report["build"]["ninja_targets"])
        commands = json.dumps(report["build"]["commands"])
        self.assertNotIn("mini_installer", commands)
        self.assertNotIn("setup", commands)
        self.assertEqual("out/FocusMacArm64", report["build"]["out_dir"])
        self.assertIn('target_cpu="arm64"', report["build"]["args_gn"])

    def test_plan_fails_closed_when_disk_gate_is_too_high(self):
        with mock.patch.object(
            focus_macos, "validate_host", return_value={"system": "Darwin", "machine": "arm64"}
        ), mock.patch.object(
            focus_macos.shutil, "disk_usage", return_value=self.disk_usage(10)
        ):
            with self.assertRaisesRegex(focus_macos.ContractError, "disk gate failed"):
                focus_macos.plan(str(self.source_root), focus_macos.Decimal("20"))

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
            "is_component_build=false\n"
            "is_debug=false\n"
            "is_official_build=true\n"
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
                + "is_component_build=false\n"
                + "is_debug=false\n"
                + "is_official_build=true\n",
                "invalid-{}.gn".format(index),
            )
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(focus_macos.ContractError, "invalid GN assignment"):
                    focus_macos.parse_gn_assignments((path,))

    def test_gn_parser_rejects_duplicate_assignments(self):
        first = self.write_gn_fixture(
            'target_os="mac"\n'
            'target_cpu="arm64"\n'
            "is_component_build=false\n"
            "is_debug=false\n"
            "is_official_build=true\n",
            "first.gn",
        )
        second = self.write_gn_fixture('target_cpu="arm64"\n', "second.gn")
        with self.assertRaisesRegex(focus_macos.ContractError, "duplicate GN arg target_cpu"):
            focus_macos.parse_gn_assignments((first, second))

    def test_gn_parser_rejects_wrong_required_value(self):
        path = self.write_gn_fixture(
            'target_os="mac"\n'
            'target_cpu="x64"\n'
            "is_component_build=false\n"
            "is_debug=false\n"
            "is_official_build=true\n"
        )
        with self.assertRaisesRegex(focus_macos.ContractError, "target_cpu"):
            focus_macos.parse_gn_assignments((path,))

    def test_validate_parser_requires_explicit_source_root(self):
        parser = focus_macos.build_parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["validate"])

    def test_json_mode_returns_machine_readable_error(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = focus_macos.main(
                ["validate", "--source-root", str(self.source_root / "missing"), "--json"]
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
