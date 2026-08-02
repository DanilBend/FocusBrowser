#!/usr/bin/env python3
"""Unit tests for the fail-closed macOS Sparkle bundle contract."""

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

import autoupdate_contract


class AutoupdateContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.app = self.make_valid_app(self.root / autoupdate_contract.APP_NAME)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write_plist(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as stream:
            plistlib.dump(value, stream)
        return path

    @staticmethod
    def read_plist(path):
        with path.open("rb") as stream:
            return plistlib.load(stream)

    @staticmethod
    def write_macho(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes.fromhex("cafebabe") + b"universal fixture\n")
        path.chmod(0o755)
        return path

    def make_nested_app(self, path, bundle_id, executable, sparkle=False):
        info = {
            "CFBundleIdentifier": bundle_id,
            "CFBundleExecutable": executable,
            "LSUIElement": 1,
        }
        if sparkle:
            info.update(
                {
                    "CFBundleShortVersionString": autoupdate_contract.SPARKLE_VERSION,
                    "CFBundleVersion": autoupdate_contract.SPARKLE_BUILD_VERSION,
                }
            )
        self.write_plist(path / "Contents/Info.plist", info)
        self.write_macho(path / "Contents/MacOS" / executable)
        return path

    def make_xpc(self, path, bundle_id, executable):
        self.write_plist(
            path / "Contents/Info.plist",
            {
                "CFBundleIdentifier": bundle_id,
                "CFBundleExecutable": executable,
                "CFBundleShortVersionString": autoupdate_contract.SPARKLE_VERSION,
                "CFBundleVersion": autoupdate_contract.SPARKLE_BUILD_VERSION,
            },
        )
        self.write_macho(path / "Contents/MacOS" / executable)

    def make_valid_app(self, app):
        self.write_plist(
            app / "Contents/Info.plist",
            {
                "CFBundleIdentifier": autoupdate_contract.APP_BUNDLE_ID,
                "CFBundleExecutable": autoupdate_contract.APP_EXECUTABLE,
                "CFBundleShortVersionString": (
                    autoupdate_contract.APP_SHORT_VERSION
                ),
                "CFBundleVersion": autoupdate_contract.APP_VERSION,
                "LSMinimumSystemVersion": (
                    autoupdate_contract.MINIMUM_MACOS_VERSION
                ),
                "CFBundleIconFile": "app.icns",
                **autoupdate_contract.SPARKLE_APP_INFO_CONTRACT,
            },
        )
        self.write_macho(
            app / "Contents/MacOS" / autoupdate_contract.APP_EXECUTABLE
        )

        canonical_icon = PLATFORM_DIR / "resources/FocusBrowser.icns"
        main_icon = app / "Contents/Resources/app.icns"
        main_icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(canonical_icon, main_icon)

        focus_framework = (
            app
            / "Contents/Frameworks"
            / autoupdate_contract.FOCUS_FRAMEWORK_NAME
        )
        focus_version = focus_framework / "Versions/150.0.7871.128"
        helpers = focus_version / "Helpers"
        self.focus_version = focus_version
        self.helpers = helpers
        for name, (bundle_id, executable) in (
            autoupdate_contract.FOCUS_HELPER_IDENTITIES.items()
        ):
            helper = self.make_nested_app(
                helpers / name, bundle_id, executable
            )
            if name == "Focus Browser Helper (Alerts).app":
                info_path = helper / "Contents/Info.plist"
                info = self.read_plist(info_path)
                info.update({"CFBundleIconFile": "app.icns"})
                self.write_plist(info_path, info)
                icon = helper / "Contents/Resources/app.icns"
                icon.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(canonical_icon, icon)
        for relative in autoupdate_contract.FOCUS_RUNTIME_PRODUCTS.values():
            self.write_macho(focus_version / relative)
        (focus_version / "Resources").mkdir(parents=True, exist_ok=True)
        (focus_framework / "Versions").mkdir(parents=True, exist_ok=True)
        (focus_framework / "Versions/Current").symlink_to("150.0.7871.128")
        (focus_framework / "Focus Browser Framework").symlink_to(
            "Versions/Current/Focus Browser Framework"
        )
        (focus_framework / "Helpers").symlink_to("Versions/Current/Helpers")
        (focus_framework / "Libraries").symlink_to("Versions/Current/Libraries")
        (focus_framework / "Resources").symlink_to("Versions/Current/Resources")

        sparkle = app / autoupdate_contract.SPARKLE_FRAMEWORK_RELATIVE_PATH
        version = sparkle / "Versions/B"
        self.sparkle_framework = sparkle
        self.sparkle_version = version
        self.write_plist(
            version / "Resources/Info.plist",
            {
                "CFBundleIdentifier": "org.sparkle-project.Sparkle",
                "CFBundleExecutable": "Sparkle",
                "CFBundleShortVersionString": autoupdate_contract.SPARKLE_VERSION,
                "CFBundleVersion": autoupdate_contract.SPARKLE_BUILD_VERSION,
            },
        )
        self.write_macho(version / "Sparkle")
        self.write_macho(version / "Autoupdate")
        self.make_nested_app(
            version / "Updater.app",
            "org.sparkle-project.Sparkle.Updater",
            "Updater",
            sparkle=True,
        )
        self.make_xpc(
            version / "XPCServices/Downloader.xpc",
            "org.sparkle-project.DownloaderService",
            "Downloader",
        )
        self.make_xpc(
            version / "XPCServices/Installer.xpc",
            "org.sparkle-project.InstallerLauncher",
            "Installer",
        )
        (sparkle / "Versions/Current").symlink_to("B")
        (sparkle / "Sparkle").symlink_to("Versions/Current/Sparkle")
        (sparkle / "Autoupdate").symlink_to("Versions/Current/Autoupdate")
        (sparkle / "Resources").symlink_to("Versions/Current/Resources")
        (sparkle / "Updater.app").symlink_to("Versions/Current/Updater.app")
        (sparkle / "XPCServices").symlink_to("Versions/Current/XPCServices")
        return app

    @staticmethod
    def universal(_path):
        return frozenset(("arm64", "x86_64"))

    @staticmethod
    def valid_signature(_app):
        return True

    def validate(self):
        return autoupdate_contract.validate_app_bundle(
            self.app,
            architecture_reader=self.universal,
            signature_verifier=self.valid_signature,
        )

    def release_fixture(self, codesign_state_reader=None, minimum_reader=None):
        source_root = Path(
            tempfile.mkdtemp(prefix="release-sparkle-source-", dir=str(self.root))
        )
        source_framework = source_root / "Sparkle.framework"
        shutil.copytree(self.sparkle_framework, source_framework, symlinks=True)
        manifest = autoupdate_contract.acquire_sparkle.framework_subtree_manifest(
            source_framework
        )

        def dependency_validator(_value):
            return {
                "root": str(source_root),
                "receipt_sha256": "c" * 64,
                "framework_subtree_sha256": (
                    autoupdate_contract.acquire_sparkle.framework_subtree_sha256(
                        manifest
                    )
                ),
            }

        def signing_state(path, _architecture):
            if codesign_state_reader is not None:
                return codesign_state_reader(path, _architecture)
            loader_labels = {
                self.app: "app",
                self.helpers / "Focus Browser Helper.app": "helper-app",
                self.helpers / "Focus Browser Helper (Renderer).app": (
                    "helper-renderer-app"
                ),
                self.helpers / "Focus Browser Helper (GPU).app": "helper-gpu-app",
                self.helpers / "Focus Browser Helper (Alerts).app": "helper-alerts",
                self.helpers / "app_mode_loader": "app-mode-app",
                self.helpers / "web_app_shortcut_copier": (
                    "web-app-shortcut-copier"
                ),
            }
            if path in loader_labels:
                return {
                    "flags": autoupdate_contract.LOADER_FLAGS,
                    "entitlements": dict(
                        autoupdate_contract.EXACT_ENTITLEMENTS[
                            loader_labels[path]
                        ]
                    ),
                }
            if path.name == "chrome_crashpad_handler":
                flags = autoupdate_contract.FULL_RUNTIME_FLAGS
            else:
                flags = autoupdate_contract.DATA_ONLY_FLAGS
            return {"flags": flags, "entitlements": {}}

        def deployment_target(path, architecture):
            if minimum_reader is not None:
                return minimum_reader(path, architecture)
            if self.sparkle_framework in path.parents:
                return "11.0" if architecture == "arm64" else "10.13"
            return "12.0"

        return autoupdate_contract.validate_release_bundle(
            self.app,
            sparkle_source_root=source_root,
            architecture_reader=self.universal,
            signature_verifier=self.valid_signature,
            dependency_validator=dependency_validator,
            codesign_state_reader=signing_state,
            minimum_reader=deployment_target,
            linkage_reader=lambda _path, _architecture: {
                "sparkle_dependency": autoupdate_contract.SPARKLE_DEPENDENCY,
                "rpaths": [autoupdate_contract.FOCUS_FRAMEWORK_RPATH],
            },
        )

    def test_valid_bundle_reports_exact_production_contract(self):
        report = self.validate()
        self.assertTrue(report["passed"])
        self.assertEqual(
            autoupdate_contract.SPARKLE_FEED_URL, report["feed_url"]
        )
        self.assertEqual(
            autoupdate_contract.SPARKLE_PUBLIC_ED_KEY, report["public_ed_key"]
        )
        self.assertEqual("2.9.4", report["sparkle"]["version"])
        self.assertEqual("2059", report["sparkle"]["build_version"])
        self.assertEqual("1.0.6", report["app_short_version"])
        self.assertEqual("1.0.6.0", report["app_version"])
        self.assertEqual("12.0", report["minimum_macos"])
        self.assertEqual(
            "Contents/Frameworks/Sparkle.framework",
            report["sparkle"]["relative_path"],
        )
        self.assertEqual(
            autoupdate_contract.CANONICAL_ICON_SHA256,
            report["icons"]["app"],
        )
        self.assertEqual(
            report["icons"]["app"], report["icons"]["alerts"]
        )
        self.assertEqual(16, len(report["universal_products"]))
        self.assertTrue(report["codesign_verified"])
        self.assertTrue(report["provisioning_profiles_absent"])
        self.assertIsNone(report["sparkle"]["provenance"])

    def test_bundle_rejects_every_provisioning_profile_spelling_and_location(self):
        cases = (
            "Contents/embedded.provisionprofile",
            "Contents/Resources/Embedded.ProvisionProfile",
            "Contents/Resources/profile.mobileprovision",
        )
        for relative in cases:
            with self.subTest(relative=relative):
                app = self.root / ("fixture-" + str(cases.index(relative))) / autoupdate_contract.APP_NAME
                self.make_valid_app(app)
                profile = app / relative
                profile.parent.mkdir(parents=True, exist_ok=True)
                profile.write_bytes(b"synthetic provisioning profile")
                with self.assertRaisesRegex(
                    autoupdate_contract.AutoupdateContractError,
                    "provisioning profile is prohibited",
                ):
                    autoupdate_contract.validate_app_bundle(
                        app,
                        architecture_reader=self.universal,
                        signature_verifier=self.valid_signature,
                    )

    def test_release_gate_reports_modes_signing_entitlements_and_minos(self):
        report = self.release_fixture()
        gate = report["release_gate"]
        self.assertTrue(gate["passed"])
        self.assertTrue(gate["sparkle_provenance_required"])
        self.assertTrue(gate["executable_modes_verified"])
        self.assertFalse(gate["update_e2e_verified"])
        self.assertTrue(gate["update_e2e_required_for_public_release"])
        self.assertTrue(gate["focus_sparkle_linkage"]["passed"])
        self.assertEqual(
            autoupdate_contract.APP_ENTITLEMENTS,
            gate["adhoc_signing"]["products"]["app"]["architectures"]
            ["arm64"]["entitlements"],
        )
        self.assertEqual(
            ["adhoc"],
            gate["adhoc_signing"]["products"]["framework"]
            ["architectures"]["arm64"]["flags"],
        )
        self.assertEqual(
            sorted(autoupdate_contract.FULL_RUNTIME_FLAGS),
            gate["adhoc_signing"]["products"]["crashpad"]
            ["architectures"]["arm64"]["flags"],
        )
        self.assertEqual(
            list(autoupdate_contract.FRAMEWORK_LOADERS),
            gate["adhoc_signing"]["framework_loaders"],
        )
        self.assertEqual(
            "10.13",
            gate["macho_minimum_system_versions"]["products"]
            ["sparkle:framework"]["architectures"]["x86_64"],
        )
        for product in report["universal_products"].values():
            self.assertEqual("0755", product["mode"])
            self.assertTrue(product["executable"])
            self.assertFalse(product["group_world_writable"])

    def test_release_gate_requires_sparkle_provenance(self):
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError, "requires Sparkle provenance"
        ):
            autoupdate_contract.validate_release_bundle(
                self.app,
                sparkle_source_root=None,
                architecture_reader=self.universal,
                signature_verifier=self.valid_signature,
            )

    def test_release_gate_rejects_missing_loader_entitlement_and_wrong_flags(self):
        def missing_entitlement(_path, _architecture):
            return {
                "flags": autoupdate_contract.LOADER_FLAGS,
                "entitlements": {},
            }

        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "entitlement dictionary mismatch",
        ):
            self.release_fixture(codesign_state_reader=missing_entitlement)

        def wrong_flags(_path, _architecture):
            return {
                "flags": {"adhoc"},
                "entitlements": {
                    autoupdate_contract.DISABLE_LIBRARY_VALIDATION: True
                },
            }

        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError, "flags mismatch"
        ):
            self.release_fixture(codesign_state_reader=wrong_flags)

    def test_release_gate_rejects_extra_or_wrong_entitlement_values(self):
        def state(path, _architecture):
            label = "app" if path == self.app else None
            if label is not None:
                entitlements = dict(autoupdate_contract.APP_ENTITLEMENTS)
                entitlements["com.example.unexpected"] = True
                return {
                    "flags": autoupdate_contract.LOADER_FLAGS,
                    "entitlements": entitlements,
                }
            return {
                "flags": autoupdate_contract.LOADER_FLAGS,
                "entitlements": {
                    autoupdate_contract.DISABLE_LIBRARY_VALIDATION: False
                },
            }

        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "entitlement dictionary mismatch",
        ):
            self.release_fixture(codesign_state_reader=state)

    def test_release_gate_rejects_sparkle_dependency_or_rpath_drift(self):
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "linkage mismatch",
        ):
            autoupdate_contract.validate_focus_sparkle_linkage(
                self.app,
                linkage_reader=lambda _path, _architecture: {
                    "sparkle_dependency": autoupdate_contract.SPARKLE_DEPENDENCY,
                    "rpaths": ["@loader_path/unsafe"],
                },
            )

        dependencies = (
            "Focus Browser Framework:\n"
            "\t{} (compatibility version 1.0.0, current version 1.0.0)\n"
        ).format(autoupdate_contract.SPARKLE_DEPENDENCY)
        commands = (
            "Load command 0\n"
            "          cmd LC_RPATH\n"
            "      cmdsize 48\n"
            "         path {} (offset 12)\n"
        ).format(autoupdate_contract.FOCUS_FRAMEWORK_RPATH)
        self.assertEqual(
            [autoupdate_contract.FOCUS_FRAMEWORK_RPATH],
            autoupdate_contract._parse_focus_sparkle_linkage(
                dependencies, commands, "fixture", "arm64"
            )["rpaths"],
        )

    def test_release_gate_rejects_newer_or_drifting_macho_minimum(self):
        def newer_sparkle(path, _architecture):
            return "13.0" if self.sparkle_framework in path.parents else "12.0"

        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError, "newer than advertised"
        ):
            self.release_fixture(minimum_reader=newer_sparkle)

        def older_chromium(path, _architecture):
            return "11.0" if self.sparkle_framework not in path.parents else "10.13"

        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "must target exactly macOS 12.0",
        ):
            self.release_fixture(minimum_reader=older_chromium)

    def test_every_macho_requires_exact_executable_mode(self):
        executable = self.app / "Contents/MacOS" / autoupdate_contract.APP_EXECUTABLE
        executable.chmod(0o644)
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError, "executable mode 0755"
        ):
            self.validate()

        self.app = self.make_valid_app(
            self.root / "second" / autoupdate_contract.APP_NAME
        )
        extra = self.write_macho(
            self.focus_version / "Resources/extra-runtime-product"
        )
        extra.chmod(0o644)
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError, "executable mode 0755"
        ):
            self.validate()

    def test_macos_minimum_parser_supports_modern_and_legacy_commands(self):
        modern = b"""Load command 1
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 1
    minos 11.0
      sdk 26.2
"""
        legacy = b"""Load command 1
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.13
      sdk 26.2
"""
        self.assertEqual(
            "11.0",
            autoupdate_contract._parse_macos_minimum(
                modern, Path("modern"), "arm64"
            ),
        )
        self.assertEqual(
            "10.13",
            autoupdate_contract._parse_macos_minimum(
                legacy, Path("legacy"), "x86_64"
            ),
        )

    def test_macos_minimum_reader_uses_descriptor_for_helper_path(self):
        helper = self.write_macho(
            self.root
            / "Focus Browser Helper (Alerts).app"
            / "Contents"
            / "MacOS"
            / "Focus Browser Helper (Alerts)"
        )
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            self.assertEqual(autoupdate_contract.VTOOL, command[0])
            self.assertEqual(["-arch", "arm64", "-show-build"], command[1:4])
            self.assertRegex(command[4], r"^/dev/fd/[0-9]+$")
            self.assertNotIn("Focus Browser Helper", command[4])
            self.assertEqual((int(command[4].rsplit("/", 1)[1]),), kwargs["pass_fds"])
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=b"""/dev/fd/9 (architecture arm64):
Load command 11
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 12.0
      sdk 27.0
""",
                stderr=b"",
            )

        self.assertEqual(
            "12.0",
            autoupdate_contract.read_macos_minimum(
                helper, "arm64", runner=runner
            ),
        )
        self.assertEqual(1, len(calls))

    def test_macos_minimum_parser_rejects_missing_malformed_and_duplicate(self):
        malformed = b"""Load command 1
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
      sdk 27.0
"""
        duplicate = b"""Load command 1
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 12.0
      sdk 27.0
Load command 2
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 12.0
      sdk 27.0
"""
        noncanonical = b"""Load command 1
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 012.0
      sdk 27.0
"""
        cases = (
            (b"Load command 1\n      cmd LC_UUID\n", "exactly one"),
            (malformed, "omitted minos"),
            (duplicate, "exactly one"),
            (noncanonical, "exactly one"),
        )
        for payload, message in cases:
            with self.subTest(message=message, payload=payload):
                with self.assertRaisesRegex(
                    autoupdate_contract.AutoupdateContractError, message
                ):
                    autoupdate_contract._parse_macos_minimum(
                        payload, Path("Focus Helper (Test)"), "arm64"
                    )

    def test_codesign_is_mandatory_and_fail_closed(self):
        calls = []

        def verifier(app):
            calls.append(app)
            return True

        report = autoupdate_contract.validate_app_bundle(
            self.app,
            architecture_reader=self.universal,
            signature_verifier=verifier,
        )
        self.assertTrue(report["codesign_verified"])
        self.assertEqual([self.app.resolve()], calls)

        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "codesign verifier rejected",
        ):
            autoupdate_contract.validate_app_bundle(
                self.app,
                architecture_reader=self.universal,
                signature_verifier=lambda _app: False,
            )

    def test_system_codesign_verifier_uses_deep_strict_without_shell(self):
        completed = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch(
            "autoupdate_contract.subprocess.run", return_value=completed
        ) as run:
            self.assertTrue(
                autoupdate_contract._verify_codesign(self.app.resolve())
            )
        self.assertEqual(
            [
                autoupdate_contract.CODESIGN,
                "--verify",
                "--deep",
                "--strict",
                str(self.app.resolve()),
            ],
            run.call_args.args[0],
        )
        self.assertFalse(run.call_args.kwargs["check"])

        completed.returncode = 1
        completed.stderr = b"bad signature"
        with mock.patch(
            "autoupdate_contract.subprocess.run", return_value=completed
        ), self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "bad signature",
        ):
            autoupdate_contract._verify_codesign(self.app.resolve())

    def test_source_root_proves_exact_sparkle_subtree_and_receipt(self):
        source_root = self.root / "sparkle-source"
        source_root.mkdir()
        source_framework = source_root / "Sparkle.framework"
        shutil.copytree(self.sparkle_framework, source_framework, symlinks=True)
        manifest = autoupdate_contract.acquire_sparkle.framework_subtree_manifest(
            source_framework
        )
        receipt_sha = "a" * 64

        def dependency_validator(value):
            self.assertEqual(source_root, Path(value))
            return {
                "root": str(source_root),
                "receipt_sha256": receipt_sha,
                "framework_subtree_sha256": (
                    autoupdate_contract.acquire_sparkle.framework_subtree_sha256(
                        manifest
                    )
                ),
            }

        report = autoupdate_contract.validate_app_bundle(
            self.app,
            architecture_reader=self.universal,
            signature_verifier=self.valid_signature,
            sparkle_source_root=source_root,
            dependency_validator=dependency_validator,
        )
        provenance = report["sparkle"]["provenance"]
        self.assertEqual(receipt_sha, provenance["receipt_sha256"])
        self.assertEqual(len(manifest), provenance["framework_entries"])

        embedded_product = self.sparkle_version / "Sparkle"
        embedded_product.write_bytes(embedded_product.read_bytes() + b"tampered")
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "differs from pinned dependency subtree",
        ):
            autoupdate_contract.validate_app_bundle(
                self.app,
                architecture_reader=self.universal,
                signature_verifier=self.valid_signature,
                sparkle_source_root=source_root,
                dependency_validator=dependency_validator,
            )

    def test_source_root_rejects_invalid_receipt_or_subtree_digest(self):
        source_root = self.root / "invalid-sparkle-source"
        source_root.mkdir()
        shutil.copytree(
            self.sparkle_framework,
            source_root / "Sparkle.framework",
            symlinks=True,
        )

        def invalid_receipt(_value):
            raise autoupdate_contract.acquire_sparkle.SparkleAcquisitionError(
                "receipt mismatch"
            )

        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "receipt mismatch",
        ):
            autoupdate_contract.validate_app_bundle(
                self.app,
                architecture_reader=self.universal,
                signature_verifier=self.valid_signature,
                sparkle_source_root=source_root,
                dependency_validator=invalid_receipt,
            )

        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "subtree digest changed",
        ):
            autoupdate_contract.validate_app_bundle(
                self.app,
                architecture_reader=self.universal,
                signature_verifier=self.valid_signature,
                sparkle_source_root=source_root,
                dependency_validator=lambda _value: {
                    "root": str(source_root),
                    "receipt_sha256": "b" * 64,
                    "framework_subtree_sha256": "0" * 64,
                },
            )

    def test_rejects_missing_or_changed_feed_and_public_key(self):
        info_path = self.app / "Contents/Info.plist"
        original = self.read_plist(info_path)
        for key, value in (
            ("SUFeedURL", "https://example.invalid/appcast.xml"),
            ("SUPublicEDKey", "A" * 44),
        ):
            with self.subTest(key=key):
                info = dict(original)
                info[key] = value
                self.write_plist(info_path, info)
                with self.assertRaisesRegex(
                    autoupdate_contract.AutoupdateContractError,
                    key,
                ):
                    self.validate()
        for key in ("SUFeedURL", "SUPublicEDKey"):
            with self.subTest(key=key, state="missing"):
                info = dict(original)
                del info[key]
                self.write_plist(info_path, info)
                with self.assertRaisesRegex(
                    autoupdate_contract.AutoupdateContractError,
                    key,
                ):
                    self.validate()
        self.write_plist(info_path, original)

    def test_rejects_any_app_version_or_update_policy_drift(self):
        info_path = self.app / "Contents/Info.plist"
        original = self.read_plist(info_path)
        expected = {
            "CFBundleShortVersionString": autoupdate_contract.APP_SHORT_VERSION,
            "CFBundleVersion": autoupdate_contract.APP_VERSION,
            "LSMinimumSystemVersion": autoupdate_contract.MINIMUM_MACOS_VERSION,
            **autoupdate_contract.SPARKLE_APP_INFO_CONTRACT,
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                info = dict(original)
                if type(value) is bool:
                    info[key] = not value
                elif type(value) is int:
                    info[key] = value + 1
                else:
                    info[key] = value + ".tampered"
                self.write_plist(info_path, info)
                with self.assertRaisesRegex(
                    autoupdate_contract.AutoupdateContractError,
                    key,
                ):
                    self.validate()
        info = dict(original)
        info["SUEnableAutomaticChecks"] = 1
        self.write_plist(info_path, info)
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "SUEnableAutomaticChecks",
        ):
            self.validate()
        self.write_plist(info_path, original)

    def test_rejects_wrong_sparkle_marketing_or_build_version(self):
        info_path = self.sparkle_version / "Resources/Info.plist"
        original = self.read_plist(info_path)
        for key, value in (
            ("CFBundleShortVersionString", "2.9.3"),
            ("CFBundleVersion", "2058"),
        ):
            with self.subTest(key=key):
                info = dict(original)
                info[key] = value
                self.write_plist(info_path, info)
                with self.assertRaisesRegex(
                    autoupdate_contract.AutoupdateContractError,
                    "metadata mismatch",
                ):
                    self.validate()
        self.write_plist(info_path, original)

    def test_rejects_non_universal_nested_binary(self):
        thin = (
            self.sparkle_version
            / "XPCServices/Downloader.xpc/Contents/MacOS/Downloader"
        )

        def architectures(path):
            if path == thin:
                return frozenset(("arm64",))
            return self.universal(path)

        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "not exactly universal",
        ):
            autoupdate_contract.validate_app_bundle(
                self.app,
                architecture_reader=architectures,
                signature_verifier=self.valid_signature,
            )

    def test_rejects_non_universal_focus_runtime_binary(self):
        thin = self.focus_version / "Helpers/chrome_crashpad_handler"

        def architectures(path):
            if path == thin:
                return frozenset(("x86_64",))
            return self.universal(path)

        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "not exactly universal",
        ):
            autoupdate_contract.validate_app_bundle(
                self.app,
                architecture_reader=architectures,
                signature_verifier=self.valid_signature,
            )

    def test_rejects_extra_sparkle_macho_product(self):
        self.write_macho(
            self.sparkle_version / "Resources/ExtraTool"
        )
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "Mach-O inventory mismatch",
        ):
            self.validate()

    def test_rejects_sparkle_outside_top_level_frameworks(self):
        nested = self.focus_version / "Libraries/Sparkle.framework"
        self.sparkle_framework.rename(nested)
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "outside its pinned Contents/Frameworks path",
        ):
            self.validate()

    def test_rejects_unallowlisted_symlink_container(self):
        outside = self.root / "outside"
        self.make_nested_app(
            outside / "Focus Browser Hidden.app",
            "com.focusbrowser.browser.hidden",
            "Focus Browser Hidden",
        )
        (self.app / "Contents/Extras").symlink_to(outside)
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "unexpected app-bundle symlinks",
        ):
            self.validate()

    def test_rejects_chromium_updater_and_keystone_artifacts(self):
        for relative in (
            "Contents/Frameworks/ChromiumUpdater.app",
            "Contents/Resources/Keystone.bundle",
        ):
            with self.subTest(relative=relative):
                path = self.app / relative
                path.mkdir(parents=True)
                try:
                    with self.assertRaisesRegex(
                        autoupdate_contract.AutoupdateContractError,
                        "prohibited updater artifact",
                    ):
                        self.validate()
                finally:
                    path.rmdir()

    def test_rejects_keystone_plist_keys(self):
        info_path = self.app / "Contents/Info.plist"
        info = self.read_plist(info_path)
        info["KSUpdateURL"] = "https://example.invalid"
        self.write_plist(info_path, info)
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "prohibited Keystone Info.plist keys",
        ):
            self.validate()

    def test_rejects_main_or_alerts_icon_drift(self):
        paths = (
            self.app / "Contents/Resources/app.icns",
            self.app
            / "Contents/Frameworks"
            / autoupdate_contract.FOCUS_FRAMEWORK_NAME
            / "Versions/150.0.7871.128/Helpers"
            / "Focus Browser Helper (Alerts).app/Contents/Resources/app.icns",
        )
        for path in paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b"tampered")
                try:
                    with self.assertRaisesRegex(
                        autoupdate_contract.AutoupdateContractError,
                        "SHA-256 mismatch",
                    ):
                        self.validate()
                finally:
                    path.write_bytes(original)

    def test_rejects_main_or_alerts_icon_metadata_drift(self):
        info_paths = (
            self.app / "Contents/Info.plist",
            self.helpers
            / "Focus Browser Helper (Alerts).app/Contents/Info.plist",
        )
        for info_path in info_paths:
            with self.subTest(info_path=info_path):
                info = self.read_plist(info_path)
                info["CFBundleIconFile"] = "Chromium.icns"
                self.write_plist(info_path, info)
                try:
                    with self.assertRaisesRegex(
                        autoupdate_contract.AutoupdateContractError,
                        "CFBundleIconFile",
                    ):
                        self.validate()
                finally:
                    info["CFBundleIconFile"] = "app.icns"
                    self.write_plist(info_path, info)

    def test_rejects_named_asset_catalog_icon_precedence(self):
        info_paths = (
            self.app / "Contents/Info.plist",
            self.helpers
            / "Focus Browser Helper (Alerts).app/Contents/Info.plist",
            self.helpers
            / "Focus Browser Helper (GPU).app/Contents/Info.plist",
            self.sparkle_version / "Resources/Info.plist",
        )
        for info_path in info_paths:
            with self.subTest(info_path=info_path):
                info = self.read_plist(info_path)
                info["CFBundleIconName"] = "AppIcon"
                self.write_plist(info_path, info)
                try:
                    with self.assertRaisesRegex(
                        autoupdate_contract.AutoupdateContractError,
                        "CFBundleIconName must be absent",
                    ):
                        self.validate()
                finally:
                    info.pop("CFBundleIconName")
                    self.write_plist(info_path, info)

    def test_rejects_any_asset_catalog(self):
        assets = (
            self.app / "Contents/Resources/Assets.car",
            self.helpers
            / "Focus Browser Helper (Alerts).app/Contents/Resources/Assets.car",
            self.helpers
            / "Focus Browser Helper (Renderer).app/Contents/Resources/Assets.car",
            self.sparkle_version / "Resources/Assets.car",
        )
        for path in assets:
            with self.subTest(path=path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"stale Chromium AppIcon")
                try:
                    with self.assertRaisesRegex(
                        autoupdate_contract.AutoupdateContractError,
                        "Assets.car is prohibited anywhere",
                    ):
                        self.validate()
                finally:
                    path.unlink()

    def test_rejects_icon_metadata_or_file_on_iconless_helpers(self):
        helper = self.helpers / "Focus Browser Helper (GPU).app"
        info_path = helper / "Contents/Info.plist"
        info = self.read_plist(info_path)
        info["CFBundleIconFile"] = "helper.icns"
        self.write_plist(info_path, info)
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "nested helper must be iconless",
        ):
            self.validate()

        info.pop("CFBundleIconFile")
        self.write_plist(info_path, info)
        icon = helper / "Contents/Resources/helper.icns"
        icon.parent.mkdir(parents=True, exist_ok=True)
        icon.write_bytes(b"unexpected helper icon")
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "nested helper must be iconless",
        ):
            self.validate()

    def test_rejects_visible_focus_or_sparkle_helper(self):
        helper_infos = (
            self.app
            / "Contents/Frameworks"
            / autoupdate_contract.FOCUS_FRAMEWORK_NAME
            / "Versions/150.0.7871.128/Helpers"
            / "Focus Browser Helper (GPU).app/Contents/Info.plist",
            self.sparkle_version / "Updater.app/Contents/Info.plist",
        )
        for info_path in helper_infos:
            with self.subTest(info_path=info_path):
                info = self.read_plist(info_path)
                original = info["LSUIElement"]
                info["LSUIElement"] = False
                self.write_plist(info_path, info)
                try:
                    with self.assertRaisesRegex(
                        autoupdate_contract.AutoupdateContractError,
                        "LSUIElement",
                    ):
                        self.validate()
                finally:
                    info["LSUIElement"] = original
                    self.write_plist(info_path, info)

    def test_rejects_any_unexpected_nested_focus_app(self):
        self.make_nested_app(
            self.app / "Contents/Helpers/Focus Browser Copy.app",
            "com.focusbrowser.browser.copy",
            "Focus Browser Copy",
        )
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "nested app inventory mismatch",
        ):
            self.validate()

    def test_rejects_legacy_dsa_key_even_with_valid_eddsa_key(self):
        info_path = self.app / "Contents/Info.plist"
        info = self.read_plist(info_path)
        info["SUPublicDSAKeyFile"] = "dsa_pub.pem"
        self.write_plist(info_path, info)
        with self.assertRaisesRegex(
            autoupdate_contract.AutoupdateContractError,
            "legacy Sparkle DSA",
        ):
            self.validate()


if __name__ == "__main__":
    unittest.main()
