"""Tests for the public macOS DMG release gate."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


MODULE_PATH = Path(__file__).with_name("verify_public_macos_dmg.py")
WORKFLOW_PATH = MODULE_PATH.parents[1] / "workflows/publish-macos-appcast.yml"
SPEC = importlib.util.spec_from_file_location("verify_public_macos_dmg", MODULE_PATH)
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


class VerifyPublicMacosDmgTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dmg = self.root / "FocusBrowser.dmg"
        self.dmg.write_bytes(b"public dmg fixture")
        self.e2e_challenge = "e" * 64
        self.e2e_receipt = self.root / "sparkle-e2e.json"
        self.e2e_receipt.write_text(
            json.dumps(self.e2e_report(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.e2e_receipt.chmod(0o600)
        self.commands = []
        self.mounted = False

    def tearDown(self):
        self.temporary.cleanup()

    def runner(self, command, pass_fds=()):
        self.assertIsInstance(tuple(pass_fds), tuple)
        self.commands.append(list(command))
        if command[:2] == [verify.HDIUTIL, "attach"]:
            mountpoint = Path(command[command.index("-mountpoint") + 1])
            (mountpoint / verify.APP_NAME).mkdir()
            (mountpoint / "Applications").symlink_to("/Applications")
            self.mounted = True
        elif command[:2] == [verify.HDIUTIL, "detach"]:
            mountpoint = Path(command[-1])
            app = mountpoint / verify.APP_NAME
            if app.exists():
                app.rmdir()
            applications = mountpoint / "Applications"
            if applications.is_symlink():
                applications.unlink()
            self.mounted = False
        return b""

    def mount_checker(self, _path):
        return self.mounted

    @staticmethod
    def e2e_report():
        module = verify.sparkle_update_e2e
        return {
            "schema": module.SCHEMA,
            "passed": True,
            "test": "isolated-full-sparkle-update",
            "old_version": module.OLD_VERSION,
            "next_version": module.NEXT_VERSION,
            "version_namespace": "CFBundleVersion/sparkle:version",
            "sparkle_version": module.acquire_sparkle.SPARKLE_VERSION,
            "sparkle_framework_subtree_sha256": (
                module.acquire_sparkle.EXPECTED_FRAMEWORK_SUBTREE_SHA256
            ),
            "sparkle_dependency_receipt_sha256": "a" * 64,
            "updater_patch_sha256": module._sha256(module.UPDATER_PATCH),
            "harness_sha256": module._sha256(Path(module.__file__)),
            "release_challenge": "e" * 64,
            "architecture": "arm64",
            "feed_transport": "loopback-http-only",
            "feed_request_verified": True,
            "archive_download_verified": True,
            "eddsa_archive_verified_by_sparkle": True,
            "signed_feed_verified_by_sparkle": True,
            "bundle_replacement_verified": True,
            "relaunch_verified": True,
            "user_profile_isolated": True,
            "keychain_private_key_used": False,
            "production_private_key_used": False,
            "real_application_install_used": False,
            "public_network_used": False,
            "archive": {"bytes": 123, "sha256": "b" * 64},
            "appcast_sha256": "c" * 64,
            "event_sequence": [
                "launched:" + module.OLD_VERSION,
                "updater-started",
                "feed-request-started",
                "feed-loaded",
                "valid-update:" + module.NEXT_VERSION,
                "update-found:" + module.NEXT_VERSION,
                "will-download",
                "download-started",
                "did-download",
                "will-extract",
                "extract-started",
                "did-extract",
                "ready-to-install",
                "will-install",
                "will-relaunch",
                "launched:" + module.NEXT_VERSION,
                "relaunch-next-version",
            ],
            "http_requests": [
                {
                    "method": "GET",
                    "path": "/appcast.xml",
                    "peer": "127.0.0.1",
                    "status": 200,
                    "bytes": 123,
                },
                {
                    "method": "GET",
                    "path": "/" + module.ARCHIVE_NAME,
                    "peer": "127.0.0.1",
                    "status": 200,
                    "bytes": 123,
                },
            ],
        }

    @staticmethod
    def report():
        architectures = sorted(verify.autoupdate_contract.ARCHITECTURES)
        loaders = list(verify.autoupdate_contract.FRAMEWORK_LOADERS)
        signing_products = {}
        for label in loaders:
            entitlements = verify.autoupdate_contract.EXACT_ENTITLEMENTS[label]
            signing_products[label] = {
                "relative_path": "." if label == "app" else "Contents/" + label,
                "architectures": {
                    architecture: {
                        "flags": sorted(verify.autoupdate_contract.LOADER_FLAGS),
                        "entitlements": dict(entitlements),
                        "disable_library_validation": True,
                        "entitlement_keys": sorted(entitlements),
                    }
                    for architecture in architectures
                },
            }
        for label, flags in (
            ("framework", verify.autoupdate_contract.DATA_ONLY_FLAGS),
            ("crashpad", verify.autoupdate_contract.FULL_RUNTIME_FLAGS),
            ("dylib:libEGL.dylib", verify.autoupdate_contract.DATA_ONLY_FLAGS),
            ("dylib:libGLESv2.dylib", verify.autoupdate_contract.DATA_ONLY_FLAGS),
        ):
            signing_products[label] = {
                "relative_path": "Contents/" + label,
                "architectures": {
                    architecture: {
                        "flags": sorted(flags),
                        "entitlements": {},
                        "disable_library_validation": False,
                        "entitlement_keys": [],
                    }
                    for architecture in architectures
                },
            }
        return {
            "passed": True,
            "codesign_verified": True,
            "provisioning_profiles_absent": True,
            "sparkle": {
                "provenance": {
                    "receipt_sha256": "a" * 64,
                    "framework_subtree_sha256": (
                        verify.autoupdate_contract.acquire_sparkle
                        .EXPECTED_FRAMEWORK_SUBTREE_SHA256
                    ),
                }
            },
            "universal_products": {
                "app": {
                    "relative_path": ".",
                    "architectures": architectures,
                    "mode": "0755",
                    "executable": True,
                    "group_world_writable": False,
                },
                "focus-framework": {
                    "relative_path": (
                        "Contents/Frameworks/Focus Browser Framework.framework/"
                        "Versions/150.0.7871.128/Focus Browser Framework"
                    ),
                    "architectures": architectures,
                    "mode": "0755",
                    "executable": True,
                    "group_world_writable": False,
                },
            },
            "release_gate": {
                "passed": True,
                "sparkle_provenance_required": True,
                "executable_modes_verified": True,
                "update_e2e_verified": False,
                "update_e2e_required_for_public_release": True,
                "adhoc_signing": {
                    "passed": True,
                    "identity": "adhoc",
                    "architectures": architectures,
                    "framework_loaders": loaders,
                    "products": signing_products,
                },
                "macho_minimum_system_versions": {
                    "passed": True,
                    "advertised_minimum": (
                        verify.autoupdate_contract.MINIMUM_MACOS_VERSION
                    ),
                    "products": {
                        "app": {
                            "relative_path": ".",
                            "policy": "exact-advertised",
                            "architectures": {
                                architecture: (
                                    verify.autoupdate_contract.MINIMUM_MACOS_VERSION
                                )
                                for architecture in architectures
                            },
                        },
                        "focus-framework": {
                            "relative_path": (
                                "Contents/Frameworks/Focus Browser Framework.framework/"
                                "Versions/150.0.7871.128/Focus Browser Framework"
                            ),
                            "policy": "exact-advertised",
                            "architectures": {
                                architecture: (
                                    verify.autoupdate_contract.MINIMUM_MACOS_VERSION
                                )
                                for architecture in architectures
                            },
                        },
                    },
                },
                "focus_sparkle_linkage": {
                    "passed": True,
                    "relative_path": (
                        "Contents/Frameworks/Focus Browser Framework.framework/"
                        "Versions/150.0.7871.128/Focus Browser Framework"
                    ),
                    "architectures": {
                        architecture: {
                            "sparkle_dependency": (
                                verify.autoupdate_contract.SPARKLE_DEPENDENCY
                            ),
                            "rpaths": [
                                verify.autoupdate_contract.FOCUS_FRAMEWORK_RPATH
                            ],
                        }
                        for architecture in architectures
                    },
                },
            },
        }

    def verify(self, validator=None, stat_flags=os.ST_RDONLY):
        return verify.verify_public_dmg(
            self.dmg,
            sparkle_source_root=self.root / "sparkle",
            sparkle_e2e_receipt=self.e2e_receipt,
            sparkle_e2e_challenge=self.e2e_challenge,
            runner=self.runner,
            validator=validator or (lambda _app, **_kwargs: self.report()),
            mount_checker=self.mount_checker,
            statvfs_reader=lambda _path: SimpleNamespace(f_flag=stat_flags),
        )

    def test_mounts_read_only_runs_full_contract_and_detaches(self):
        calls = []

        def validator(app, **kwargs):
            calls.append((app, kwargs))
            return self.report()

        report = self.verify(validator=validator)
        self.assertTrue(report["passed"])
        self.assertTrue(report["mounted_read_only"])
        self.assertTrue(report["detached"])
        self.assertFalse(self.mounted)
        self.assertIn("-readonly", self.commands[0])
        self.assertEqual([verify.HDIUTIL, "detach"], self.commands[-1][:2])
        self.assertEqual(self.root / "sparkle", calls[0][1]["sparkle_source_root"])
        self.assertTrue(report["sparkle_update_e2e"]["report"]["passed"])
        self.assertEqual(
            str(self.e2e_receipt.resolve()),
            report["sparkle_update_e2e"]["path"],
        )

    def test_private_dmg_copy_allows_hdiutil_checksum_ctime_change(self):
        original_runner = self.runner
        ctime_changed = []

        def runner(command, pass_fds=()):
            if command[:2] == [verify.HDIUTIL, "attach"]:
                image = Path(command[-1])
                before = image.stat()
                os.chmod(image, 0o600)
                after = image.stat()
                ctime_changed.append(after.st_ctime_ns != before.st_ctime_ns)
            return original_runner(command, pass_fds=pass_fds)

        report = verify.verify_public_dmg(
            self.dmg,
            sparkle_source_root=self.root / "sparkle",
            sparkle_e2e_receipt=self.e2e_receipt,
            sparkle_e2e_challenge=self.e2e_challenge,
            runner=runner,
            validator=lambda _app, **_kwargs: self.report(),
            mount_checker=self.mount_checker,
            statvfs_reader=lambda _path: SimpleNamespace(f_flag=os.ST_RDONLY),
        )
        self.assertTrue(report["passed"])
        self.assertEqual([True], ctime_changed)

    def test_private_dmg_copy_retains_same_size_content_tamper(self):
        original_runner = self.runner

        def runner(command, pass_fds=()):
            if command[:2] == [verify.HDIUTIL, "attach"]:
                Path(command[-1]).write_bytes(b"x" * self.dmg.stat().st_size)
            return original_runner(command, pass_fds=pass_fds)

        with self.assertRaisesRegex(
            verify.PublicDmgError,
            "private public-DMG pin was replaced; retained",
        ) as raised:
            verify.verify_public_dmg(
                self.dmg,
                sparkle_source_root=self.root / "sparkle",
                sparkle_e2e_receipt=self.e2e_receipt,
                sparkle_e2e_challenge=self.e2e_challenge,
                runner=runner,
                validator=lambda _app, **_kwargs: self.report(),
                mount_checker=self.mount_checker,
                statvfs_reader=lambda _path: SimpleNamespace(
                    f_flag=os.ST_RDONLY
                ),
            )
        retained = Path(
            str(raised.exception).split("retained ", 1)[1].split(";", 1)[0]
        )
        self.assertTrue((retained / "pinned-public.dmg").is_file())

    def test_public_gate_requires_provenance_before_mounting(self):
        runner = mock.Mock()
        with self.assertRaisesRegex(verify.PublicDmgError, "requires Sparkle provenance"):
            verify.verify_public_dmg(self.dmg, runner=runner)
        runner.assert_not_called()

    def test_public_gate_requires_passing_e2e_receipt_before_mounting(self):
        runner = mock.Mock()
        with self.assertRaisesRegex(
            verify.PublicDmgError, "requires a passing isolated Sparkle E2E receipt"
        ):
            verify.verify_public_dmg(
                self.dmg,
                sparkle_source_root=self.root / "sparkle",
                sparkle_e2e_challenge=self.e2e_challenge,
                runner=runner,
            )
        runner.assert_not_called()

    def test_public_gate_requires_fresh_challenge_before_mounting(self):
        runner = mock.Mock()
        with self.assertRaisesRegex(
            verify.PublicDmgError, "requires a fresh release challenge"
        ):
            verify.verify_public_dmg(
                self.dmg,
                sparkle_source_root=self.root / "sparkle",
                sparkle_e2e_receipt=self.e2e_receipt,
                runner=runner,
            )
        runner.assert_not_called()

    def test_public_gate_rejects_unsafe_or_tampered_e2e_receipt_before_mounting(self):
        cases = []

        noncanonical = self.root / "noncanonical.json"
        noncanonical.write_text(json.dumps(self.e2e_report()), encoding="utf-8")
        noncanonical.chmod(0o600)
        cases.append((noncanonical, "not canonically encoded"))

        unsafe_mode = self.root / "unsafe-mode.json"
        unsafe_mode.write_text(
            json.dumps(self.e2e_report(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        unsafe_mode.chmod(0o644)
        cases.append((unsafe_mode, "not an owner-private regular file"))

        symlink = self.root / "receipt-symlink.json"
        symlink.symlink_to(self.e2e_receipt)
        cases.append((symlink, "must not be a symlink"))

        wrong_patch = self.root / "wrong-patch.json"
        wrong_patch_report = self.e2e_report()
        wrong_patch_report["updater_patch_sha256"] = "0" * 64
        wrong_patch.write_text(
            json.dumps(wrong_patch_report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        wrong_patch.chmod(0o600)
        cases.append((wrong_patch, "does not bind the updater patch"))

        for receipt, message in cases:
            runner = mock.Mock()
            with self.subTest(message=message), self.assertRaisesRegex(
                verify.PublicDmgError, message
            ):
                verify.verify_public_dmg(
                    self.dmg,
                    sparkle_source_root=self.root / "sparkle",
                    sparkle_e2e_receipt=receipt,
                    sparkle_e2e_challenge=self.e2e_challenge,
                    runner=runner,
                )
            runner.assert_not_called()

    def test_public_gate_rejects_hardlinked_e2e_receipt_before_mounting(self):
        hardlink = self.root / "receipt-hardlink.json"
        os.link(self.e2e_receipt, hardlink)
        runner = mock.Mock()
        try:
            with self.assertRaisesRegex(
                verify.PublicDmgError, "not an owner-private regular file"
            ):
                verify.verify_public_dmg(
                    self.dmg,
                    sparkle_source_root=self.root / "sparkle",
                    sparkle_e2e_receipt=hardlink,
                    sparkle_e2e_challenge=self.e2e_challenge,
                    runner=runner,
                )
            runner.assert_not_called()
        finally:
            hardlink.unlink()

    def test_public_gate_binds_exact_download_size_and_sha256(self):
        runner = mock.Mock()
        with self.assertRaisesRegex(verify.PublicDmgError, "size differs"):
            verify.verify_public_dmg(
                self.dmg,
                sparkle_source_root=self.root / "sparkle",
                sparkle_e2e_receipt=self.e2e_receipt,
                sparkle_e2e_challenge=self.e2e_challenge,
                expected_size=self.dmg.stat().st_size + 1,
                expected_sha256="0" * 64,
                runner=runner,
            )
        runner.assert_not_called()

    def test_public_gate_rejects_missing_release_mode_or_signing_evidence(self):
        for mutation, message in (
            (lambda report: report.pop("sparkle"), "omitted Sparkle provenance"),
            (
                lambda report: report["universal_products"]["app"].update(
                    {"mode": "0644"}
                ),
                "unsafe executable mode",
            ),
            (
                lambda report: report.update(
                    {"provisioning_profiles_absent": False}
                ),
                "did not reject provisioning profiles",
            ),
            (lambda report: report.pop("release_gate"), "release gate is incomplete"),
        ):
            with self.subTest(message=message):
                candidate = self.report()
                mutation(candidate)
                with self.assertRaisesRegex(verify.PublicDmgError, message):
                    self.verify(validator=lambda _app, **_kwargs: candidate)

    def test_public_gate_binds_e2e_to_dmg_dependency_receipt(self):
        candidate = self.report()
        candidate["sparkle"]["provenance"]["receipt_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            verify.PublicDmgError, "does not bind the DMG dependency provenance"
        ):
            self.verify(validator=lambda _app, **_kwargs: candidate)

    def test_public_gate_rejects_missing_loader_or_invalid_slice_evidence(self):
        cases = []
        missing_loader = self.report()
        missing_loader["release_gate"]["adhoc_signing"]["products"].pop(
            "helper-gpu-app"
        )
        cases.append((missing_loader, "signing inventory is incomplete"))

        wrong_flags = self.report()
        wrong_flags["release_gate"]["adhoc_signing"]["products"]["app"][
            "architectures"
        ]["arm64"]["flags"] = ["adhoc"]
        cases.append((wrong_flags, "signing state is invalid"))

        wrong_framework_flags = self.report()
        wrong_framework_flags["release_gate"]["adhoc_signing"]["products"][
            "framework"
        ]["architectures"]["arm64"]["flags"] = sorted(
            verify.autoupdate_contract.FULL_RUNTIME_FLAGS
        )
        cases.append((wrong_framework_flags, "signing state is invalid"))

        wrong_minimum = self.report()
        wrong_minimum["release_gate"]["macho_minimum_system_versions"][
            "products"
        ]["app"]["architectures"]["arm64"] = "13.0"
        cases.append((wrong_minimum, "minimum-system state is invalid"))

        extra_entitlement = self.report()
        extra_entitlement["release_gate"]["adhoc_signing"]["products"]["app"][
            "architectures"
        ]["arm64"]["entitlements"]["com.example.unexpected"] = True
        cases.append((extra_entitlement, "signing state is invalid"))

        wrong_linkage = self.report()
        wrong_linkage["release_gate"]["focus_sparkle_linkage"]["architectures"][
            "arm64"
        ]["rpaths"] = ["@loader_path/unsafe"]
        cases.append((wrong_linkage, "Sparkle linkage is invalid"))

        for candidate, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                verify.PublicDmgError, message
            ):
                self.verify(validator=lambda _app, **_kwargs: candidate)

    def test_contract_failure_still_detaches(self):
        def reject(_app, **_kwargs):
            raise verify.autoupdate_contract.AutoupdateContractError("rejected")

        with self.assertRaisesRegex(verify.PublicDmgError, "rejected"):
            self.verify(validator=reject)
        self.assertFalse(self.mounted)
        self.assertEqual([verify.HDIUTIL, "detach"], self.commands[-1][:2])

    def test_writable_mount_fails_and_detaches_before_validation(self):
        validator = mock.Mock(return_value=self.report())
        with self.assertRaisesRegex(verify.PublicDmgError, "not read-only"):
            self.verify(validator=validator, stat_flags=0)
        validator.assert_not_called()
        self.assertFalse(self.mounted)

    def test_unexpected_top_level_entry_fails_and_detaches(self):
        original_runner = self.runner

        def runner(command, pass_fds=()):
            value = original_runner(command, pass_fds=pass_fds)
            if command[:2] == [verify.HDIUTIL, "attach"]:
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                (mountpoint / "unexpected.txt").write_bytes(b"unexpected")
            return value

        with self.assertRaisesRegex(verify.PublicDmgError, "inventory mismatch"):
            verify.verify_public_dmg(
                self.dmg,
                sparkle_source_root=self.root / "sparkle",
                sparkle_e2e_receipt=self.e2e_receipt,
                sparkle_e2e_challenge=self.e2e_challenge,
                runner=runner,
                validator=lambda _app, **_kwargs: self.report(),
                mount_checker=self.mount_checker,
                statvfs_reader=lambda _path: SimpleNamespace(f_flag=os.ST_RDONLY),
            )
        self.assertFalse(self.mounted)

    def test_failed_normal_detach_attempts_force_and_fails_closed(self):
        def runner(command, pass_fds=()):
            if command[:2] == [verify.HDIUTIL, "detach"] and "-force" not in command:
                self.commands.append(list(command))
                raise verify.PublicDmgError("normal detach failed")
            return self.runner(command, pass_fds=pass_fds)

        with self.assertRaisesRegex(verify.PublicDmgError, "required forced detach"):
            verify.verify_public_dmg(
                self.dmg,
                sparkle_source_root=self.root / "sparkle",
                sparkle_e2e_receipt=self.e2e_receipt,
                sparkle_e2e_challenge=self.e2e_challenge,
                runner=runner,
                validator=lambda _app, **_kwargs: self.report(),
                mount_checker=self.mount_checker,
                statvfs_reader=lambda _path: SimpleNamespace(f_flag=os.ST_RDONLY),
            )
        self.assertFalse(self.mounted)
        self.assertIn("-force", self.commands[-1])

    def test_unprovable_detach_retains_mount_root_and_reports_path(self):
        retained = self.root / "retained-mount-root"

        def runner(command, pass_fds=()):
            if command[:2] == [verify.HDIUTIL, "detach"]:
                raise verify.PublicDmgError("detach failed")
            return self.runner(command, pass_fds=pass_fds)

        def make_retained_root(*_args, **_kwargs):
            retained.mkdir()
            return str(retained)

        with mock.patch.object(
            verify.tempfile, "mkdtemp", side_effect=make_retained_root
        ), self.assertRaisesRegex(
            verify.PublicDmgError, "retained mount root"
        ):
            verify.verify_public_dmg(
                self.dmg,
                sparkle_source_root=self.root / "sparkle",
                sparkle_e2e_receipt=self.e2e_receipt,
                sparkle_e2e_challenge=self.e2e_challenge,
                runner=runner,
                validator=lambda _app, **_kwargs: self.report(),
                mount_checker=self.mount_checker,
                statvfs_reader=lambda _path: SimpleNamespace(f_flag=os.ST_RDONLY),
            )
        self.assertTrue(retained.is_dir())
        self.assertTrue(self.mounted)

    def test_checked_run_is_bounded_and_never_uses_shell(self):
        completed = subprocess.CompletedProcess([verify.HDIUTIL], 0, b"ok", b"")
        with mock.patch("subprocess.run", return_value=completed) as run:
            self.assertEqual(b"ok", verify.checked_run([verify.HDIUTIL, "info"]))
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(verify.TOOL_TIMEOUT_SECONDS, run.call_args.kwargs["timeout"])

    def test_workflow_gates_downloaded_public_dmg_without_private_key(self):
        value = WORKFLOW_PATH.read_text(encoding="utf-8")
        download = value.index("Download and verify exact public macOS assets")
        full_gate = value.index(
            "Validate public DMG, Sparkle provenance, and appcast signature"
        )
        deploy = value.index("Upload GitHub Pages artifact")
        self.assertLess(download, full_gate)
        self.assertLess(full_gate, deploy)
        self.assertIn("platform/macos/acquire_sparkle.py", value)
        e2e = value.index("platform/macos/sparkle_update_e2e.py")
        verifier = value.index(".github/scripts/verify_public_macos_dmg.py")
        self.assertLess(e2e, verifier)
        self.assertIn(".github/scripts/verify_public_macos_dmg.py", value)
        self.assertIn('--dmg "$FOCUS_MACOS_PAYLOAD"', value)
        self.assertIn('--expected-size "$FOCUS_MACOS_PAYLOAD_SIZE"', value)
        self.assertIn('--expected-sha256 "$FOCUS_MACOS_PAYLOAD_SHA256"', value)
        self.assertIn('--sparkle-source-root "$sparkle_root"', value)
        self.assertIn('--sparkle-e2e-receipt "$sparkle_e2e_receipt"', value)
        self.assertIn('--release-challenge "$sparkle_e2e_challenge"', value)
        self.assertIn('--sparkle-e2e-challenge "$sparkle_e2e_challenge"', value)
        self.assertIn('--output "$sparkle_e2e_receipt"', value)
        self.assertNotIn("SPARKLE_PRIVATE", value)
        self.assertNotIn("sign_update", value)


if __name__ == "__main__":
    unittest.main()
