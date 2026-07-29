#!/usr/bin/env python3
"""Unit tests for the staged, low-space macOS build pipeline."""

import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import build_pipeline


class BuildPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.checkout = self.root / "checkout"
        self.source = self.checkout / "src"
        self.source.mkdir(parents=True)
        self.source = self.source.resolve()
        self.checkout = self.source.parent
        self.depot = self.checkout / "depot_tools"
        self.depot.mkdir()
        for name in ("gclient", "gn", "autoninja"):
            path = self.depot / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)
        self.developer = self.root / "Xcode.app/Contents/Developer"
        self.write_acquisition_marker()
        self.write_tool_receipt()
        self.write_preparation_receipt()

    def tearDown(self):
        self.temporary.cleanup()

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        return path

    def write_acquisition_marker(self):
        marker = self.checkout / build_pipeline.acquire_chromium.COMPLETE_MARKER
        return self.write_json(
            marker,
            {
                "status": "acquisition_complete",
                "execution_requested": True,
                "network_performed": True,
                "destination": str(self.checkout),
                "pins": {
                    "chromium_version": build_pipeline.acquire_chromium.CHROMIUM_VERSION,
                    "chromium_tag": build_pipeline.acquire_chromium.CHROMIUM_TAG,
                    "chromium_commit": build_pipeline.acquire_chromium.CHROMIUM_COMMIT,
                    "depot_tools_commit": build_pipeline.acquire_chromium.DEPOT_TOOLS_COMMIT,
                },
                "verification": {
                    "source_root": str(self.source),
                    "chromium_version": build_pipeline.acquire_chromium.CHROMIUM_VERSION,
                    "chromium_commit": build_pipeline.acquire_chromium.CHROMIUM_COMMIT,
                    "depot_tools_commit": build_pipeline.acquire_chromium.DEPOT_TOOLS_COMMIT,
                },
                "gclient": {
                    "target_os": ["mac"],
                    "target_os_only": True,
                    "hooks_during_acquisition": False,
                    "git_cache": False,
                    "spec_sha256": build_pipeline.acquire_chromium.GCLIENT_SPEC_SHA256,
                },
            },
        )

    def write_tool_receipt(self):
        marker = self.checkout / build_pipeline.acquire_chromium.COMPLETE_MARKER
        return self.write_json(
            self.checkout / build_pipeline.TOOL_RECEIPT,
            {
                "schema": 1,
                "hooks_complete": True,
                "chromium_commit": build_pipeline.acquire_chromium.CHROMIUM_COMMIT,
                "depot_tools_commit": build_pipeline.acquire_chromium.DEPOT_TOOLS_COMMIT,
                "source_root": str(self.source),
                "developer_dir": str(self.developer),
                "acquisition_marker_sha256": build_pipeline.sha256_file(marker),
                "gclient_command": [str(self.depot / "gclient"), "runhooks"],
                "gn_version": "150.0",
                "tool_sha256": {
                    name: build_pipeline.sha256_file(self.depot / name)
                    for name in ("gclient", "gn", "autoninja")
                },
                "post_hooks_free_bytes": 75 * build_pipeline.GIB,
                "build_executed": False,
            },
        )

    def write_preparation_receipt(self):
        prepared_paths = {
            "chrome/BUILD.gn": "chrome/BUILD.gn",
            build_pipeline.prepare_source.INSTALLER_MAC_BUILD_GN: build_pipeline.prepare_source.INSTALLER_MAC_BUILD_GN,
            "chrome/app/theme/chromium/BRANDING": "chrome/app/theme/chromium/BRANDING",
            "chrome/VERSION": "chrome/VERSION",
            build_pipeline.prepare_source.MAC_ICON_DESTINATION: build_pipeline.prepare_source.MAC_ICON_DESTINATION,
            "args_gn/arm64": build_pipeline.ARM_OUT + "/args.gn",
            "args_gn/x64": build_pipeline.X64_OUT + "/args.gn",
        }
        post_hashes = {}
        for label, relative in prepared_paths.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(label + "\n", encoding="utf-8")
            post_hashes[label] = build_pipeline.sha256_file(path)
        acquisition = self.checkout / build_pipeline.acquire_chromium.COMPLETE_MARKER
        tools = self.checkout / build_pipeline.TOOL_RECEIPT
        return self.write_json(
            self.source / build_pipeline.PREPARATION_RECEIPT,
            {
                "schema": 1,
                "chromium_version": build_pipeline.focus_macos.PINNED_CHROMIUM_VERSION,
                "offline": True,
                "network_operations": 0,
                "acquisition": {
                    "path": str(acquisition),
                    "sha256": build_pipeline.sha256_file(acquisition),
                    "source_root": str(self.source),
                },
                "tool_bootstrap": {
                    "path": str(tools),
                    "sha256": build_pipeline.sha256_file(tools),
                    "source_root": str(self.source),
                },
                "patch_contract": {
                    "common_filtered_count": build_pipeline.focus_macos.EXPECTED_FULL_PATCH_BODY_COUNT,
                    "common_filtered_order_sha256": build_pipeline.focus_macos.FILTERED_COMMON_SERIES_SHA256,
                    "common_full_body_sha256": build_pipeline.focus_macos.EXPECTED_FULL_PATCH_BODY_SHA256,
                    "platform": build_pipeline.focus_macos.validate_platform_patch_series(),
                },
                "dependency_contract": {
                    "manifest_sha256": build_pipeline.prepare_source.DEPS_INI_SHA256,
                    "archives": {
                        name: contract["sha256"]
                        for name, contract in build_pipeline.prepare_source.DEPENDENCY_CONTRACTS.items()
                    },
                },
                "pruning_contract": {
                    "manifest_sha256": build_pipeline.prepare_source.PRUNING_LIST_SHA256,
                    "listed_files": build_pipeline.prepare_source.PRUNING_ENTRY_COUNT,
                    "contingent_paths_pruned": False,
                    "directory_pruning_executed": False,
                },
                "overlay_contract": {
                    "count": build_pipeline.focus_macos.EXPECTED_FULL_OVERLAY_BODY_COUNT,
                    "sha256": build_pipeline.focus_macos.EXPECTED_FULL_OVERLAY_BODY_SHA256,
                },
                "resource_contract": {
                    "count": build_pipeline.prepare_source.RESOURCE_BODY_COUNT,
                    "sha256": build_pipeline.prepare_source.RESOURCE_BODY_SHA256,
                },
                "icns_sha256": build_pipeline.focus_macos.FOCUS_ICNS_SHA256,
                "post_prepare_sha256": post_hashes,
                "build_executed": False,
                "signing_executed": False,
                "packaging_executed": False,
            },
        )

    def make_app(self, parent, architecture="arm64"):
        app = parent / build_pipeline.APP_NAME
        executable = app / "Contents/MacOS/Focus Browser"
        executable.parent.mkdir(parents=True)
        executable.write_bytes((architecture + " executable").encode("utf-8"))
        info = {
            "CFBundleIdentifier": build_pipeline.focus_macos.BUNDLE_ID,
            "CFBundleExecutable": "Focus Browser",
        }
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        return app

    def lipo_result(self, architectures):
        return subprocess.CompletedProcess(
            ["lipo"], 0, stdout=" ".join(architectures) + "\n", stderr=""
        )

    def write_slice_receipt(self, out, architecture):
        expected = "arm64" if architecture == "arm64" else "x86_64"
        args_path = out / "args.gn"
        prep = self.source / build_pipeline.PREPARATION_RECEIPT
        return self.write_json(
            out / build_pipeline.SLICE_RECEIPT_NAME,
            {
                "schema": 1,
                "architecture": architecture,
                "mach_o_architecture": expected,
                "source_root": str(self.source),
                "app": {"architectures": [expected]},
                "args_gn_sha256": build_pipeline.sha256_file(args_path),
                "preparation_receipt_sha256": build_pipeline.sha256_file(prep),
                "build_complete": True,
            },
        )

    def test_contract_receipts_bind_exact_checkout_and_marker(self):
        build_pipeline.acquisition_contract(self.source)
        build_pipeline.tool_receipt_contract(self.source)
        receipt = json.loads(
            (self.checkout / build_pipeline.TOOL_RECEIPT).read_text(encoding="utf-8")
        )
        receipt["source_root"] = str(self.root / "replayed/src")
        self.write_json(self.checkout / build_pipeline.TOOL_RECEIPT, receipt)
        with self.assertRaisesRegex(build_pipeline.PipelineError, "source_root"):
            build_pipeline.tool_receipt_contract(self.source)

    def test_safe_environment_is_child_only_and_macos_only(self):
        inherited = {
            "PATH": "/bin",
            "SDKROOT": "/iPhoneOS.sdk",
            "IPHONEOS_DEPLOYMENT_TARGET": "27.0",
            "GIT_CACHE_PATH": "/cache",
            "KEEP": "yes",
        }
        developer = self.root / "Xcode.app/Contents/Developer"
        result = build_pipeline.safe_environment(self.source, developer, inherited)
        self.assertNotIn("SDKROOT", result)
        self.assertNotIn("IPHONEOS_DEPLOYMENT_TARGET", result)
        self.assertNotIn("GIT_CACHE_PATH", result)
        self.assertEqual("yes", result["KEEP"])
        self.assertEqual(str(developer), result["DEVELOPER_DIR"])
        self.assertTrue(result["PATH"].startswith(str(self.depot)))

    def test_bootstrap_is_hook_only_and_must_precede_preparation(self):
        (self.checkout / build_pipeline.TOOL_RECEIPT).unlink()
        (self.source / build_pipeline.PREPARATION_RECEIPT).unlink()
        with mock.patch.object(
            build_pipeline, "free_bytes", return_value=80 * build_pipeline.GIB
        ):
            plan = build_pipeline.bootstrap_plan(self.source, self.developer)
        self.assertEqual(
            [str(self.depot / "gclient"), "runhooks"], plan["command"]
        )
        self.assertEqual(str(self.checkout), plan["cwd"])
        self.write_json(
            self.source / build_pipeline.PREPARATION_RECEIPT, {"present": True}
        )
        with mock.patch.object(
            build_pipeline, "free_bytes", return_value=80 * build_pipeline.GIB
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "before source preparation"):
            build_pipeline.bootstrap_plan(self.source, self.developer)

    def test_execute_bootstrap_writes_source_bound_receipt(self):
        (self.checkout / build_pipeline.TOOL_RECEIPT).unlink()
        (self.source / build_pipeline.PREPARATION_RECEIPT).unlink()
        plan = {"command": [str(self.depot / "gclient"), "runhooks"]}
        with mock.patch.object(
            build_pipeline, "verify_pristine_bootstrap_source"
        ) as pristine, mock.patch.object(build_pipeline, "run_monitored") as run, mock.patch.object(
            build_pipeline, "capture", return_value="150.0"
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=75 * build_pipeline.GIB
        ):
            report = build_pipeline.execute_bootstrap(
                self.source, self.developer, plan
            )
        run.assert_called_once()
        self.assertEqual(2, pristine.call_count)
        receipt = json.loads(Path(report["path"]).read_text(encoding="utf-8"))
        self.assertEqual(str(self.source), receipt["source_root"])
        self.assertTrue(receipt["hooks_complete"])
        self.assertFalse(receipt["build_executed"])

    def test_build_plan_is_sequential_local_and_four_jobs(self):
        out = self.source / build_pipeline.ARM_OUT
        out.mkdir(parents=True, exist_ok=True)
        plan = build_pipeline.build_plan(
            self.source, self.developer, "arm64"
        )
        self.assertEqual("build-arm64", plan["stage"])
        self.assertEqual("-j4", plan["commands"][1][1])
        self.assertEqual(
            ["chrome", "chrome/installer/mac:copies"], plan["commands"][1][-2:]
        )
        flattened = "\n".join(" ".join(command) for command in plan["commands"])
        for forbidden in ("android", "iphone", "windows", "remoteexec", "siso"):
            self.assertNotIn(forbidden, flattened.lower())

    def test_execute_build_writes_a_provenance_receipt(self):
        out = self.source / build_pipeline.ARM_OUT
        out.mkdir(parents=True, exist_ok=True)
        (out / "args.gn").write_text('target_cpu="arm64"\n', encoding="utf-8")
        self.make_app(out)
        packaging = out / build_pipeline.PACKAGING_NAME
        packaging.mkdir()
        sign = packaging / "sign_chrome.py"
        sign.write_bytes(b"sign")
        sign_hash = hashlib.sha256(b"sign").hexdigest()
        plan = {
            "architecture": "arm64",
            "out": str(out),
            "commands": [["gn", "gen"], ["autoninja", "chrome"]],
            "receipt": str(out / build_pipeline.SLICE_RECEIPT_NAME),
        }
        app_report = {
            "app": str(out / build_pipeline.APP_NAME),
            "bundle_id": build_pipeline.focus_macos.BUNDLE_ID,
            "executable": "Focus Browser",
            "architectures": ["arm64"],
        }
        with mock.patch.object(build_pipeline, "SIGN_CHROME_SHA256", sign_hash), mock.patch.object(
            build_pipeline, "require_free", return_value=80 * build_pipeline.GIB
        ), mock.patch.object(build_pipeline, "run_monitored") as run, mock.patch.object(
            build_pipeline, "app_report", return_value=app_report
        ):
            report = build_pipeline.execute_build(
                self.source, self.developer, plan
            )
        self.assertEqual(2, run.call_count)
        receipt = json.loads(Path(report["path"]).read_text(encoding="utf-8"))
        self.assertTrue(receipt["build_complete"])
        self.assertEqual("arm64", receipt["mach_o_architecture"])
        self.assertEqual(sign_hash, receipt["sign_chrome_sha256"])

    def test_stage_reclaims_only_exact_arm_output_after_verified_copy(self):
        arm_out = self.source / build_pipeline.ARM_OUT
        arm_out.mkdir(parents=True, exist_ok=True)
        app = self.make_app(arm_out)
        (arm_out / "args.gn").write_text("arm\n", encoding="utf-8")
        build_receipt = self.write_slice_receipt(arm_out, "arm64")
        build_receipt_hash = build_pipeline.sha256_file(build_receipt)
        x64_sentinel = self.source / build_pipeline.X64_OUT / "keep.txt"
        x64_sentinel.parent.mkdir(parents=True, exist_ok=True)
        x64_sentinel.write_text("keep", encoding="utf-8")
        staged = self.source / build_pipeline.STAGED_ARM_APP
        partial_root = self.source / build_pipeline.STAGING_ROOT / ".arm64.part"
        partial_app = partial_root / build_pipeline.APP_NAME
        plan = {
            "source_app": str(app),
            "staged_app": str(staged),
            "arm_out": str(arm_out),
            "receipt": str(self.source / build_pipeline.STAGE_RECEIPT),
            "reclaim_receipt": str(self.source / build_pipeline.RECLAIM_RECEIPT),
            "build_receipt": str(build_receipt),
            "partial_root": str(partial_root),
            "partial_app": str(partial_app),
            "ditto_command": ["/usr/bin/ditto", str(app), str(partial_app)],
        }

        def fake_ditto(_command, _cwd, _environment, watched_paths=None):
            self.assertEqual((self.source,), watched_paths)
            shutil.copytree(app, partial_app, symlinks=True)

        with mock.patch.object(
            build_pipeline, "run_monitored", side_effect=fake_ditto
        ), mock.patch.object(
            build_pipeline, "app_report", return_value={"architectures": ["arm64"]}
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=60 * build_pipeline.GIB
        ):
            report = build_pipeline.execute_stage_arm(self.source, plan, True)
        self.assertFalse(arm_out.exists())
        self.assertEqual("keep", x64_sentinel.read_text(encoding="utf-8"))
        receipt = json.loads(Path(report["path"]).read_text(encoding="utf-8"))
        self.assertTrue(receipt["reclaim_complete"])
        self.assertEqual(
            build_receipt_hash,
            json.loads(
                (self.source / build_pipeline.STAGE_RECEIPT).read_text(encoding="utf-8")
            )["build_receipt_sha256"],
        )

    def prepare_merge_fixture(self):
        arm_app = self.source / build_pipeline.STAGED_ARM_APP
        self.make_app(arm_app.parent, "arm64")
        digest = build_pipeline.tree_digest(arm_app)
        stage_receipt = self.write_json(
            self.source / build_pipeline.STAGE_RECEIPT,
            {"tree_sha256": digest},
        )
        prep = json.loads(
            (self.source / build_pipeline.PREPARATION_RECEIPT).read_text(encoding="utf-8")
        )
        prep["post_prepare_sha256"][
            build_pipeline.prepare_source.INSTALLER_MAC_BUILD_GN
        ] = build_pipeline.INSTALLER_BUILD_GN_SHA256
        self.write_json(self.source / build_pipeline.PREPARATION_RECEIPT, prep)
        arm_args_hash = prep["post_prepare_sha256"]["args_gn/arm64"]
        arm_out = self.source / build_pipeline.ARM_OUT
        if arm_out.exists():
            shutil.rmtree(arm_out)
        self.write_json(
            self.source / build_pipeline.RECLAIM_RECEIPT,
            {
                "schema": 1,
                "reclaim_complete": True,
                "source_root": str(self.source),
                "staged_app": str(arm_app),
                "tree_sha256": digest,
                "reclaimed_out": str(arm_out),
                "reclaimed_out_bytes": 100,
                "arm_args_gn_sha256": arm_args_hash,
                "stage_receipt_sha256": build_pipeline.sha256_file(stage_receipt),
            },
        )
        x64_out = self.source / build_pipeline.X64_OUT
        x64_out.mkdir(parents=True, exist_ok=True)
        self.make_app(x64_out, "x86_64")
        packaging = x64_out / build_pipeline.PACKAGING_NAME
        packaging.mkdir()
        (packaging / "sign_chrome.py").write_text("sign\n", encoding="utf-8")
        self.write_slice_receipt(x64_out, "x64")
        signing_root = self.source / "chrome/installer/mac"
        (signing_root / "sign_chrome.py").write_text("source sign\n", encoding="utf-8")
        (signing_root / "mac_signing_sources.gni").write_text(
            "sources = []\n", encoding="utf-8"
        )
        universalizer = self.source / build_pipeline.focus_macos.CHROMIUM_UNIVERSALIZER
        universalizer.parent.mkdir(parents=True, exist_ok=True)
        universalizer.write_text("universalize\n", encoding="utf-8")
        return arm_app, x64_out, universalizer

    def test_merge_plan_uses_chromium_x64_first_and_ad_hoc_signing(self):
        _, x64_out, universalizer = self.prepare_merge_fixture()
        output = self.root / "FocusBrowser.dmg"
        real_hash = build_pipeline.sha256_file

        def pinned_hash(path):
            path = Path(path)
            if path.name == "sign_chrome.py":
                return build_pipeline.SIGN_CHROME_SHA256
            if path == universalizer:
                return build_pipeline.focus_macos.PINNED_CHROMIUM_UNIVERSALIZER_SHA256
            if path == self.source / "chrome/installer/mac/BUILD.gn":
                return build_pipeline.INSTALLER_BUILD_GN_SHA256
            if path.name == "mac_signing_sources.gni":
                return build_pipeline.MAC_SIGNING_SOURCES_GNI_SHA256
            return real_hash(path)

        with mock.patch.object(
            build_pipeline, "app_report", return_value={"architectures": ["arm64"]}
        ), mock.patch.object(build_pipeline, "sha256_file", side_effect=pinned_hash):
            plan = build_pipeline.merge_plan(
                self.source, self.developer, output
            )
        universalize = plan["commands"]["universalize"]
        self.assertEqual(str(x64_out / build_pipeline.APP_NAME), universalize[-3])
        self.assertEqual(str(self.source / build_pipeline.STAGED_ARM_APP), universalize[-2])
        sign = plan["commands"]["sign"]
        self.assertIn("--identity", sign)
        self.assertEqual("-", sign[sign.index("--identity") + 1])
        self.assertIn("--development", sign)
        self.assertIn("--disable-packaging", sign)
        self.assertEqual("none", sign[sign.index("--notarize") + 1])
        joined = " ".join(sign).lower()
        self.assertNotIn("developer id", joined)
        self.assertNotIn("notarytool", joined)

    def test_merge_rejects_relative_dmg_output(self):
        _, _, universalizer = self.prepare_merge_fixture()
        real_hash = build_pipeline.sha256_file

        def pinned_hash(path):
            path = Path(path)
            if path.name == "sign_chrome.py":
                return build_pipeline.SIGN_CHROME_SHA256
            if path == universalizer:
                return build_pipeline.focus_macos.PINNED_CHROMIUM_UNIVERSALIZER_SHA256
            if path == self.source / "chrome/installer/mac/BUILD.gn":
                return build_pipeline.INSTALLER_BUILD_GN_SHA256
            if path.name == "mac_signing_sources.gni":
                return build_pipeline.MAC_SIGNING_SOURCES_GNI_SHA256
            return real_hash(path)

        with mock.patch.object(
            build_pipeline, "app_report", return_value={"architectures": ["arm64"]}
        ), mock.patch.object(build_pipeline, "sha256_file", side_effect=pinned_hash), self.assertRaisesRegex(
            build_pipeline.PipelineError, "absolute"
        ):
            build_pipeline.merge_plan(
                self.source, self.developer, Path("relative.dmg")
            )

    def test_recursive_reclamation_requires_explicit_flag(self):
        with self.assertRaisesRegex(build_pipeline.PipelineError, "allow-reclaim"):
            build_pipeline.execute_stage_arm(self.source, {}, False)

    def test_forbidden_privileged_build_program_is_rejected(self):
        with self.assertRaisesRegex(build_pipeline.PipelineError, "forbidden"):
            build_pipeline.run_monitored(
                ["sudo", "true"], self.source, {}, poll_seconds=0
            )

    def test_monitor_checks_soft_floor_before_spawning(self):
        with mock.patch.object(
            build_pipeline, "free_bytes", return_value=34 * build_pipeline.GIB
        ), mock.patch.object(build_pipeline.subprocess, "Popen") as popen, self.assertRaisesRegex(
            build_pipeline.PipelineError, "pre-command"
        ):
            build_pipeline.run_monitored(
                ["/usr/bin/true"], self.source, {}, poll_seconds=0
            )
        popen.assert_not_called()

    def test_reclaim_receipt_is_false_if_arm_output_reappears(self):
        self.prepare_merge_fixture()
        arm_out = self.source / build_pipeline.ARM_OUT
        arm_out.mkdir(parents=True)
        with self.assertRaisesRegex(build_pipeline.PipelineError, "still exists"):
            build_pipeline.reclaim_contract(self.source)

    def test_json_receipts_reject_duplicate_keys(self):
        receipt = self.root / "duplicate.json"
        receipt.write_text('{"schema": 1, "schema": 2}\n', encoding="utf-8")
        with self.assertRaisesRegex(build_pipeline.PipelineError, "duplicate"):
            build_pipeline.load_json(receipt, "fixture receipt")

    def test_dmg_output_rejects_symlinked_ancestor(self):
        real = self.root / "real-output"
        real.mkdir()
        alias = self.root / "output-alias"
        alias.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(build_pipeline.PipelineError, "symlink"):
            build_pipeline.resolve_absent_dmg(alias / "FocusBrowser.dmg")

    def test_cli_defaults_to_dry_run(self):
        args = build_pipeline.parser().parse_args(
            [
                "build-arm64",
                "--source-root",
                str(self.source),
                "--developer-dir",
                "/Xcode.app/Contents/Developer",
            ]
        )
        self.assertFalse(args.execute)
        self.assertEqual("build-arm64", args.command)


if __name__ == "__main__":
    unittest.main()
