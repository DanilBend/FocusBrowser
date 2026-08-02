import contextlib
import hashlib
import io
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MACOS_DIR = Path(__file__).resolve().parents[1]
if str(MACOS_DIR) not in sys.path:
    sys.path.insert(0, str(MACOS_DIR))

import autoupdate_release


class AutoupdateReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, value="x\n", mode="w"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        kwargs = {} if "b" in mode else {"encoding": "utf-8"}
        with path.open(mode, **kwargs) as stream:
            stream.write(value)
        return path

    @staticmethod
    def make_focus_framework(app, version="150.0.7871.128"):
        framework = (
            Path(app)
            / "Contents/Frameworks/Focus Browser Framework.framework"
        )
        version_directory = framework / "Versions" / version
        version_directory.mkdir(parents=True)
        (framework / "Versions/Current").symlink_to(version)
        executable = version_directory / "Focus Browser Framework"
        executable.write_bytes(b"mach-o")
        return executable

    def canonical_fixture(self):
        profiles = {
            "update_mode": "autoupdate",
            "feed_url": "feed",
            "public_key": "key",
            "common_flags": {"path": "common", "sha256": "c" * 64},
            "slices": {
                "arm64": {
                    "profile_path": "arm",
                    "profile_sha256": "a" * 64,
                    "canonical_assignments": {
                        "target_cpu": '"arm64"',
                        "enabled": "true",
                        "feed": '"https://example.test/feed"',
                    },
                    "canonical_assignments_sha256": "1" * 64,
                },
                "x64": {
                    "profile_path": "x64",
                    "profile_sha256": "b" * 64,
                    "canonical_assignments": {
                        "target_cpu": '"x64"',
                        "enabled": "true",
                        "feed": '"https://example.test/feed"',
                    },
                    "canonical_assignments_sha256": "2" * 64,
                },
            },
        }
        return profiles

    @staticmethod
    def python_fixture():
        return {
            "path": "/pinned/python3.11",
            "wrapper": "/pinned/python-wrapper",
            "wrapper_sha256": "w" * 64,
            "architecture": "arm64",
            "version": "3.11.8",
            "sha256": "p" * 64,
            "cipd_package": "python",
            "cipd_version": "version:3.11.8",
            "cipd_instance": "instance",
            "asyncio_task_group": True,
        }

    @staticmethod
    def tree_fixture():
        return {
            "tree_sha256": "t" * 64,
            "paths_sha256": "q" * 64,
            "xattrs_sha256": "x" * 64,
            "acls_sha256": "l" * 64,
            "entry_count": 1,
            "root_mode": "0755",
            "owner_uid": os.geteuid(),
        }

    @staticmethod
    def packaging_fixture(path="/packaging"):
        files = {
            relative: {"sha256": digest, "size": 1}
            for relative, digest in (
                autoupdate_release.PINNED_SIGNING_PYTHON_SHA256.items()
            )
        }
        files["signing/build_props_config.py"] = {
            "sha256": "b" * 64,
            "size": 1,
        }
        return {
            "path": str(path),
            "tree": AutoupdateReleaseTests.tree_fixture(),
            "directories": {},
            "files": files,
            "trusted_python_sha256": autoupdate_release.PINNED_SIGNING_PYTHON_SHA256,
            "trusted_source_sha256": autoupdate_release.PINNED_PACKAGING_SOURCE_SHA256,
            "build_props_template_sha256": autoupdate_release.PINNED_BUILD_PROPS_TEMPLATE_SHA256,
            "app_entitlements_template_sha256": autoupdate_release.PINNED_APP_ENTITLEMENTS_TEMPLATE_SHA256,
        }

    @staticmethod
    def signing_wrapper_fixture():
        return {
            "path": "/repo/platform/macos/adhoc_release_sign.py",
            "sha256": "r" * 64,
            "size": 4096,
            "mode": "0644",
        }

    @staticmethod
    def runtime_fixture(app):
        values = []
        for architecture, execution in (("arm64", "native"), ("x86_64", "Rosetta")):
            values.append(
                {
                    "architecture": architecture,
                    "execution": execution,
                    "exit_code": 0,
                    "verification_exit_code": 0,
                    "storage_control_persistence_verified": True,
                    "storage_control_write_exit_code": 0,
                    "storage_control_read_exit_code": 0,
                    "incognito": True,
                    "incognito_storage_isolated": True,
                    "incognito_proof": "incognito-write/normal-read localStorage beacon isolation",
                    "offline_navigation": "loopback-http/localStorage-beacon",
                    "marker": "FOCUSBROWSER_{}_{}_OK".format(
                        architecture.upper(), "A" * 24
                    ),
                    "marker_observed": True,
                    "fresh_profile": True,
                    "timeout_seconds": autoupdate_release.RUNTIME_TIMEOUT_SECONDS,
                    "duration_seconds": 1.0,
                    "stdout_sha256": "a" * 64,
                    "stderr_sha256": "b" * 64,
                    "verification_stdout_sha256": "c" * 64,
                    "verification_stderr_sha256": "d" * 64,
                    "storage_control_sha256": "e" * 64,
                    "network_disabling_arguments": list(
                        autoupdate_release.runtime_smoke.RUNTIME_ARGUMENTS
                    ),
                }
            )
        return {
            "app": str(app),
            "host_architecture": "arm64",
            "rosetta_required": True,
            "rosetta_available": True,
            "architectures": values,
            "passed": True,
        }

    def test_repository_canonical_profiles_match_pins(self):
        report = autoupdate_release.canonical_profiles()
        self.assertEqual(report["update_mode"], "autoupdate")
        self.assertEqual(set(report["slices"]), {"arm64", "x64"})
        for architecture in ("arm64", "x64"):
            self.assertEqual(
                report["slices"][architecture]["profile_sha256"],
                autoupdate_release.PINNED_AUTO_PROFILE_SHA256[architecture],
            )
            self.assertEqual(
                report["slices"][architecture]["canonical_assignments_sha256"],
                autoupdate_release.PINNED_CANONICAL_ASSIGNMENTS_SHA256[architecture],
            )

    def test_canonical_profiles_fail_closed_on_profile_hash_drift(self):
        original = autoupdate_release.focus_macos.sha256_file

        def changed(path):
            if Path(path) == autoupdate_release.focus_macos.AUTOUPDATE_MACOS_FLAGS["arm64"]:
                return "0" * 64
            return original(path)

        with mock.patch.object(
            autoupdate_release.focus_macos, "sha256_file", side_effect=changed
        ):
            with self.assertRaisesRegex(autoupdate_release.ReleaseError, "profile SHA-256"):
                autoupdate_release.canonical_profiles()

    def test_generated_args_accept_gn_spacing_and_wrapped_string(self):
        args = self.write(
            "args.gn",
            '# generated\ntarget_cpu = "arm64"\nenabled = true\nfeed =\n'
            '    "https://example.test/feed"\n',
        )
        observed = autoupdate_release.parse_generated_args(args)
        self.assertEqual(
            observed,
            {
                "target_cpu": '"arm64"',
                "enabled": "true",
                "feed": '"https://example.test/feed"',
            },
        )

    def test_generated_args_reject_duplicate(self):
        args = self.write("args.gn", "enabled=true\nenabled = false\n")
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "duplicate"):
            autoupdate_release.parse_generated_args(args)

    def test_generated_args_reject_unknown_syntax(self):
        args = self.write("args.gn", 'import("//build.gni")\n')
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "unsupported"):
            autoupdate_release.parse_generated_args(args)

    def test_validate_args_rejects_unknown_assignment(self):
        args = self.write(
            "args.gn",
            'target_cpu="arm64"\nenabled=true\nfeed="https://example.test/feed"\nextra=1\n',
        )
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, r"extra=\['extra'\]"):
            autoupdate_release.validate_args(
                args, "arm64", self.canonical_fixture()
            )

    def test_validate_args_records_actual_file_hash(self):
        args = self.write(
            "args.gn",
            'target_cpu = "arm64"\nenabled = true\nfeed = "https://example.test/feed"\n',
        )
        report = autoupdate_release.validate_args(
            args, "arm64", self.canonical_fixture()
        )
        self.assertEqual(report["sha256"], autoupdate_release.sha256_file(args))
        self.assertEqual(report["assignment_count"], 3)

    def test_release_paths_are_strict_auto_paths(self):
        paths = autoupdate_release.release_paths(Path("/source"))
        self.assertEqual(str(paths["arm_out"]), "/source/out/FocusMacArm64Auto")
        self.assertEqual(str(paths["x64_out"]), "/source/out/FocusMacX64Auto")
        self.assertEqual(str(paths["staging"]), "/source/out/FocusMacAutoStaging")
        self.assertEqual(
            str(paths["unsigned"]), "/source/out/FocusMacUnsignedUniversalAuto"
        )
        self.assertEqual(
            str(paths["signed"]), "/source/out/FocusMacSignedUniversalAuto"
        )

    def test_resolve_source_requires_exact_focus_version(self):
        source = self.root / "source"
        source.mkdir()
        version = self.write(
            "source/chrome/VERSION",
            "MAJOR=150\nMINOR=0\nBUILD=7871\nPATCH=128\n"
            "FOCUS_MAJOR=1\nFOCUS_MINOR=0\nFOCUS_PATCH=6\nFOCUS_PLATFORM=0\n",
        )
        self.assertEqual(autoupdate_release.resolve_source_root(source), source.resolve())
        version.write_text(version.read_text().replace("FOCUS_PATCH=6", "FOCUS_PATCH=5"))
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "1.0.6.0"):
            autoupdate_release.resolve_source_root(source)

    def test_stage_plan_never_contains_gn_or_ninja_and_uses_part(self):
        source = self.root / "source"
        source.mkdir()
        seal_receipt = self.write("seal.json", "{}\n")
        profiles = self.canonical_fixture()
        with (
            mock.patch.object(autoupdate_release, "_require_tools"),
            mock.patch.object(
                autoupdate_release,
                "_validated_seal",
                return_value=(seal_receipt, {}),
            ) as validated_seal,
            mock.patch.object(autoupdate_release, "canonical_profiles", return_value=profiles),
            mock.patch.object(autoupdate_release, "validate_args", return_value={"sha256": "a" * 64}),
            mock.patch.object(autoupdate_release, "inspect_thin_app", return_value={"ok": True}),
            mock.patch.object(autoupdate_release, "_tree_contract", return_value=self.tree_fixture()),
            mock.patch.object(autoupdate_release, "_packaging_contract", return_value=self.packaging_fixture()),
            mock.patch.object(autoupdate_release, "_pinned_packaging_python", return_value=self.python_fixture()),
            mock.patch.object(autoupdate_release, "_universalizer_contract", return_value={"sha256": "u" * 64}),
            mock.patch.object(autoupdate_release, "_ensure_absent"),
        ):
            plan = autoupdate_release.stage_plan(source)
        encoded = json.dumps(plan)
        self.assertNotIn("ninja", encoded.casefold())
        self.assertNotIn("gn gen", encoded.casefold())
        self.assertTrue(all(".FocusMacAutoStaging.part" in command[-1] for command in plan["commands"]))
        self.assertEqual(plan["update_mode"], "autoupdate")
        validated_seal.assert_called_once_with(source)

    def test_stage_validation_fails_closed_without_build_seal(self):
        source = self.root / "unsealed-source"
        (source / "out").mkdir(parents=True)
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "missing safe seal receipt"
        ):
            autoupdate_release._validated_seal(source)

    def test_ninja_no_work_contract_rejects_any_pending_edge(self):
        with mock.patch.object(
            autoupdate_release,
            "_capture",
            return_value="[1/1] CXX obj/injected.o\n",
        ), self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "not a no-work Ninja graph"
        ):
            autoupdate_release._no_work_contract(
                self.root,
                self.root / "out",
                {"path": "/pinned/ninja"},
            )

    def test_ninja_no_work_contract_requires_both_exact_build_targets(self):
        capture = mock.Mock(return_value="ninja: no work to do.\n")
        with mock.patch.object(autoupdate_release, "_capture", capture):
            contract = autoupdate_release._no_work_contract(
                self.root,
                self.root / "out",
                {"path": "/pinned/ninja"},
            )
        capture.assert_called_once_with(
            [
                "/pinned/ninja",
                "-C",
                str(self.root / "out"),
                "-n",
                "chrome",
                "chrome/installer/mac:copies",
            ]
        )
        self.assertEqual(
            contract["targets"],
            ["chrome", "chrome/installer/mac:copies"],
        )

    def test_validated_seal_rejects_omitted_installer_copies_target(self):
        complete = {
            "no_work": {
                "arm64": {
                    "targets": ["chrome", "chrome/installer/mac:copies"]
                },
                "x64": {
                    "targets": ["chrome", "chrome/installer/mac:copies"]
                },
            }
        }
        omitted = json.loads(json.dumps(complete))
        omitted["no_work"]["x64"]["targets"] = ["chrome"]
        with (
            mock.patch.object(
                autoupdate_release, "_load_receipt", return_value=omitted
            ),
            mock.patch.object(
                autoupdate_release, "_seal_contract", return_value=complete
            ),
            self.assertRaisesRegex(
                autoupdate_release.ReleaseError,
                "no longer matches completed outputs",
            ),
        ):
            autoupdate_release._validated_seal(self.root)

    def test_prepare_auto_and_seal_receipts_are_no_overwrite(self):
        source = self.root / "prepared-source"
        (source / "out").mkdir(parents=True)
        paths = autoupdate_release.release_paths(source)
        paths["auto_preparation_addendum"].write_text("existing\n")
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "overwrite"):
            autoupdate_release.prepare_auto_plan(source)
        paths["build_seal"].write_text("existing\n")
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "overwrite"):
            autoupdate_release.seal_plan(source)

    def test_merge_plan_pins_x64_then_arm64_order(self):
        source = self.root / "source"
        source.mkdir()
        stage_receipt = self.write("stage.json", "{}")
        with (
            mock.patch.object(autoupdate_release, "_require_tools"),
            mock.patch.object(autoupdate_release, "_validated_stage", return_value=(stage_receipt, {})),
            mock.patch.object(autoupdate_release, "_ensure_absent"),
            mock.patch.object(autoupdate_release, "sha256_file", return_value="a" * 64),
            mock.patch.object(
                autoupdate_release,
                "_pinned_packaging_python",
                return_value=self.python_fixture(),
            ),
        ):
            plan = autoupdate_release.merge_plan(source)
        command = plan["commands"]["universalize"]
        self.assertEqual(plan["input_order"], ["x64", "arm64"])
        self.assertEqual(
            command[1 : 1 + len(autoupdate_release.PINNED_PYTHON_ISOLATION_ARGS)],
            list(autoupdate_release.PINNED_PYTHON_ISOLATION_ARGS),
        )
        self.assertIn("FocusMacAutoStaging/x64", command[-3])
        self.assertIn("FocusMacAutoStaging/arm64", command[-2])
        self.assertTrue(command[-1].endswith(".FocusMacUnsignedUniversalAuto.part/Focus Browser.app"))

    def test_universalizer_hash_mismatch_is_rejected(self):
        path = self.write("universalizer.py", "changed")
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "universalizer SHA-256"):
            autoupdate_release._universalizer_contract(path)

    def test_generated_signing_driver_hash_mismatch_is_rejected(self):
        path = self.write("sign_chrome.py", "changed")
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "sign_chrome.py SHA-256"):
            autoupdate_release._driver_contract(path)

    def test_adhoc_signing_wrapper_hash_mismatch_is_rejected(self):
        self.write("adhoc_release_sign.py", "changed\n")
        with mock.patch.object(autoupdate_release, "MACOS_DIR", self.root):
            with self.assertRaisesRegex(
                autoupdate_release.ReleaseError, "wrapper SHA-256"
            ):
                autoupdate_release._adhoc_signing_wrapper_contract()

    def test_signing_snapshot_is_read_only_descriptor_bound_and_tamper_evident(self):
        part = self.root / "sign-transaction"
        part.mkdir(mode=0o700)
        wrapper_path = self.write(
            "trusted-wrapper.py",
            "def main(args):\n    print('BOOTSTRAP_OK:' + args[0])\n",
        )
        module_path = self.write(
            "packaging/signing/__init__.py", "sentinel = 1234\n"
        )
        wrapper = {
            "path": str(wrapper_path),
            "sha256": autoupdate_release.sha256_file(wrapper_path),
            "size": wrapper_path.stat().st_size,
            "mode": "0644",
        }
        packaging = self.packaging_fixture(self.root / "packaging")
        packaging["files"] = {
            "signing/__init__.py": {
                "sha256": autoupdate_release.sha256_file(module_path),
                "size": module_path.stat().st_size,
            }
        }
        paths = {"signing": "signing/__init__.py"}
        with mock.patch.object(
            autoupdate_release, "SIGNING_MODULE_PATHS", paths
        ):
            plan = autoupdate_release._signing_snapshot_plan(
                part, wrapper, packaging
            )
            before = autoupdate_release._create_signing_snapshot(
                part, wrapper, packaging, plan
            )
            autoupdate_release._validate_signing_snapshot_report(before, plan)
            descriptor = autoupdate_release._open_signing_snapshot_wrapper(plan)
            try:
                self.assertEqual(
                    autoupdate_release._sha256_fd(descriptor), wrapper["sha256"]
                )
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        autoupdate_release.SIGNING_WRAPPER_BOOTSTRAP,
                        str(descriptor),
                        str(wrapper["size"]),
                        wrapper["sha256"],
                        "sentinel",
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    pass_fds=(descriptor,),
                )
                self.assertEqual(completed.stdout.strip(), "BOOTSTRAP_OK:sentinel")
            finally:
                os.close(descriptor)
            snap_module = Path(plan["root"]) / "signing/__init__.py"
            snap_module.chmod(0o600)
            snap_module.write_text("sentinel = 5678\n", encoding="utf-8")
            snap_module.chmod(0o400)
            try:
                with self.assertRaisesRegex(
                    autoupdate_release.ReleaseError,
                    "snapshot file contract mismatch",
                ):
                    autoupdate_release._signing_snapshot_contract(plan)
            finally:
                autoupdate_release._remove_signing_snapshot(plan)
        self.assertFalse(Path(plan["root"]).exists())

    def test_sign_plan_uses_only_pinned_adhoc_local_flags(self):
        source = self.root / "source"
        source.mkdir()
        merge_receipt = self.write("merge.json", "{}")
        with (
            mock.patch.object(autoupdate_release, "_require_tools"),
            mock.patch.object(autoupdate_release, "_validated_merge", return_value=(merge_receipt, {})),
            mock.patch.object(autoupdate_release, "_ensure_absent"),
            mock.patch.object(autoupdate_release, "sha256_file", return_value="a" * 64),
            mock.patch.object(
                autoupdate_release,
                "_packaging_contract",
                return_value=self.packaging_fixture(),
            ),
            mock.patch.object(
                autoupdate_release,
                "_pinned_packaging_python",
                return_value=self.python_fixture(),
            ),
            mock.patch.object(
                autoupdate_release,
                "_driver_contract",
                return_value={"path": "/driver/sign_chrome.py", "sha256": "d" * 64, "origin": autoupdate_release.X64_OUT},
            ),
            mock.patch.object(
                autoupdate_release,
                "_adhoc_signing_wrapper_contract",
                return_value=self.signing_wrapper_fixture(),
            ),
        ):
            plan = autoupdate_release.sign_plan(source)
        command = plan["command"]
        self.assertEqual(command[0], self.python_fixture()["path"])
        self.assertEqual(
            command[1 : 1 + len(autoupdate_release.PINNED_PYTHON_ISOLATION_ARGS)],
            list(autoupdate_release.PINNED_PYTHON_ISOLATION_ARGS),
        )
        self.assertEqual(command[command.index("--identity") + 1], "-")
        self.assertTrue(
            command[command.index("--signing-package") + 1].endswith(
                ".FocusMacSignedUniversalAuto.part/.focus-signing-snapshot"
            )
        )
        self.assertEqual(
            command[1 + len(autoupdate_release.PINNED_PYTHON_ISOLATION_ARGS)],
            "-c",
        )
        self.assertIn(autoupdate_release.DESCRIPTOR_BOUND_WRAPPER, command)
        self.assertIn(autoupdate_release.SIGNING_WRAPPER_BOOTSTRAP, command)
        self.assertEqual(
            command[command.index("--signing-manifest-sha256") + 1],
            plan["signing_snapshot"]["manifest"]["sha256"],
        )
        self.assertEqual(
            plan["signing_execution"],
            autoupdate_release.SIGNING_EXECUTION_CONTRACT,
        )
        self.assertNotIn("--development", command)
        self.assertNotIn("--no-embed-development-provisioning-profile", command)
        self.assertEqual(command[command.index("--notarize") + 1], "none")
        self.assertIn("--disable-packaging", command)
        self.assertNotIn("Developer ID", " ".join(command))
        self.assertFalse(plan["development"])
        self.assertFalse(plan["provisioning_profile"])
        self.assertFalse(plan["run_spctl_assess"])
        self.assertFalse(plan["inject_get_task_allow_entitlement"])
        self.assertEqual(plan["signing_policy"], autoupdate_release._adhoc_signing_policy())
        self.assertFalse(plan["publication"])
        self.assertFalse(plan["developer_id"])

    def test_sign_receipt_rejects_debug_entitlement_policy(self):
        source = self.root / "source-sign-policy"
        source.mkdir()
        merge_receipt = self.write("merge-policy.json", "{}")
        wrapper = self.signing_wrapper_fixture()
        driver = {
            "path": "/driver/sign_chrome.py",
            "sha256": "d" * 64,
            "origin": autoupdate_release.X64_OUT,
        }
        policy = autoupdate_release._adhoc_signing_policy()
        policy["inject_get_task_allow_entitlement"] = True
        receipt = {
            "source_root": str(source),
            "merge_receipt_sha256": "m" * 64,
            "signing_packaging_before": self.packaging_fixture(),
            "signing_packaging_after": self.packaging_fixture(),
            "signing_wrapper_before": wrapper,
            "signing_wrapper_after": wrapper,
            "signing_driver": driver,
            "signing_policy": policy,
        }
        with (
            mock.patch.object(autoupdate_release, "_load_receipt", return_value=receipt),
            mock.patch.object(
                autoupdate_release, "_validated_merge", return_value=(merge_receipt, {})
            ),
            mock.patch.object(autoupdate_release, "_receipt_sha256", return_value="m" * 64),
            mock.patch.object(
                autoupdate_release,
                "_packaging_contract",
                return_value=self.packaging_fixture(),
            ),
            mock.patch.object(
                autoupdate_release,
                "_adhoc_signing_wrapper_contract",
                return_value=wrapper,
            ),
            mock.patch.object(autoupdate_release, "_driver_contract", return_value=driver),
        ):
            with self.assertRaisesRegex(
                autoupdate_release.ReleaseError, "policy details"
            ):
                autoupdate_release._validated_sign(source)

    def test_sign_receipt_rejects_development_command_even_with_safe_policy(self):
        source = self.root / "source-sign-command"
        source.mkdir()
        merge_receipt = self.write("merge-command.json", "{}")
        wrapper = self.signing_wrapper_fixture()
        driver = {
            "path": "/driver/sign_chrome.py",
            "sha256": "d" * 64,
            "origin": autoupdate_release.X64_OUT,
        }
        receipt = {
            "source_root": str(source),
            "merge_receipt_sha256": "m" * 64,
            "signing_packaging_before": self.packaging_fixture(),
            "signing_packaging_after": self.packaging_fixture(),
            "signing_wrapper_before": wrapper,
            "signing_wrapper_after": wrapper,
            "signing_driver": driver,
            "signing_policy": autoupdate_release._adhoc_signing_policy(),
            "packaging_python": self.python_fixture(),
            "signing_command": ["/pinned/python3.11", "--development"],
        }
        with (
            mock.patch.object(autoupdate_release, "_load_receipt", return_value=receipt),
            mock.patch.object(
                autoupdate_release, "_validated_merge", return_value=(merge_receipt, {})
            ),
            mock.patch.object(autoupdate_release, "_receipt_sha256", return_value="m" * 64),
            mock.patch.object(
                autoupdate_release,
                "_packaging_contract",
                return_value=self.packaging_fixture(),
            ),
            mock.patch.object(
                autoupdate_release,
                "_adhoc_signing_wrapper_contract",
                return_value=wrapper,
            ),
            mock.patch.object(autoupdate_release, "_driver_contract", return_value=driver),
            mock.patch.object(
                autoupdate_release,
                "_pinned_packaging_python",
                return_value=self.python_fixture(),
            ),
        ):
            with self.assertRaisesRegex(
                autoupdate_release.ReleaseError, "command mismatch"
            ):
                autoupdate_release._validated_sign(source)

    def test_unsigned_contract_calls_autoupdate_without_signature(self):
        app = self.root / autoupdate_release.APP_NAME
        app.mkdir()
        contract = mock.Mock(return_value={"universal_products": {}})
        with (
            mock.patch.object(autoupdate_release.autoupdate_contract, "validate_app_bundle", contract),
            mock.patch.object(autoupdate_release, "validate_otool_contract", return_value={"ok": True}),
        ):
            result = autoupdate_release.validate_universal_app(app, signed=False)
        self.assertFalse(result["signed"])
        self.assertTrue(callable(contract.call_args.kwargs["signature_verifier"]))

    def test_signed_contract_runs_deep_strict_and_requires_adhoc(self):
        app = self.root / autoupdate_release.APP_NAME
        app.mkdir()
        with (
            mock.patch.object(
                autoupdate_release.autoupdate_contract,
                "validate_app_bundle",
                return_value={"universal_products": {}},
            ) as contract,
            mock.patch.object(
                autoupdate_release.autoupdate_contract,
                "validate_adhoc_signing_contract",
                return_value={"passed": True},
            ),
            mock.patch.object(
                autoupdate_release.autoupdate_contract,
                "validate_macho_minimum_system_versions",
                return_value={"passed": True},
            ),
            mock.patch.object(autoupdate_release, "validate_otool_contract", return_value={"ok": True}),
            mock.patch.object(
                autoupdate_release,
                "_capture",
                side_effect=["", "Signature=adhoc\nIdentifier=com.focusbrowser.browser\n"],
            ) as capture,
        ):
            result = autoupdate_release.validate_universal_app(app, signed=True)
        self.assertNotIn("signature_verifier", contract.call_args.kwargs)
        self.assertEqual(capture.call_args_list[0].args[0][1:4], ["--verify", "--deep", "--strict"])
        self.assertEqual(result["codesign"]["identity"], "adhoc")

    def test_signed_contract_rejects_non_adhoc(self):
        app = self.root / autoupdate_release.APP_NAME
        app.mkdir()
        with (
            mock.patch.object(
                autoupdate_release.autoupdate_contract,
                "validate_app_bundle",
                return_value={"universal_products": {}},
            ),
            mock.patch.object(
                autoupdate_release.autoupdate_contract,
                "validate_adhoc_signing_contract",
                return_value={"passed": True},
            ),
            mock.patch.object(
                autoupdate_release.autoupdate_contract,
                "validate_macho_minimum_system_versions",
                return_value={"passed": True},
            ),
            mock.patch.object(autoupdate_release, "validate_otool_contract", return_value={}),
            mock.patch.object(autoupdate_release, "_capture", side_effect=["", "Authority=Developer ID Application: X\n"]),
        ):
            with self.assertRaisesRegex(autoupdate_release.ReleaseError, "not ad-hoc"):
                autoupdate_release.validate_universal_app(app, signed=True)

    def test_otool_contract_requires_exact_dependency_and_rpath(self):
        app = self.root / autoupdate_release.APP_NAME
        framework = self.make_focus_framework(app)
        dependency = "{}:\n\t{} (compatibility version 1.0.0, current version 2.9.4)\n".format(
            framework, autoupdate_release.SPARKLE_DEPENDENCY
        )
        loads = "Load command 1\n          cmd LC_RPATH\n      cmdsize 40\n         path {} (offset 12)\n".format(
            autoupdate_release.FOCUS_FRAMEWORK_RPATH
        )
        with mock.patch.object(
            autoupdate_release,
            "_capture",
            side_effect=[dependency, loads, dependency, loads],
        ) as capture:
            report = autoupdate_release.validate_otool_contract(app)
        self.assertEqual(report["sparkle_dependency"], autoupdate_release.SPARKLE_DEPENDENCY)
        self.assertEqual(set(report["architectures"]), {"arm64", "x86_64"})
        self.assertEqual(
            [call.args[0][2] for call in capture.call_args_list],
            ["arm64", "arm64", "x86_64", "x86_64"],
        )

    def test_otool_contract_rejects_missing_rpath(self):
        app = self.root / autoupdate_release.APP_NAME
        framework = self.make_focus_framework(app)
        dependency = "x:\n\t{} (compatibility version 1.0.0)\n".format(
            autoupdate_release.SPARKLE_DEPENDENCY
        )
        with mock.patch.object(autoupdate_release, "_capture", side_effect=[dependency, ""]):
            with self.assertRaisesRegex(autoupdate_release.ReleaseError, "rpath mismatch"):
                autoupdate_release.validate_otool_contract(app)

    def test_otool_contract_rejects_any_additional_rpath(self):
        app = self.root / autoupdate_release.APP_NAME
        framework = self.make_focus_framework(app)
        dependency = "x:\n\t{} (compatibility version 1.0.0)\n".format(
            autoupdate_release.SPARKLE_DEPENDENCY
        )
        loads = (
            "path {} (offset 12)\n"
            "path @loader_path/unexpected (offset 12)\n"
        ).format(autoupdate_release.FOCUS_FRAMEWORK_RPATH)
        with mock.patch.object(
            autoupdate_release, "_capture", side_effect=[dependency, loads]
        ), self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "rpath mismatch"
        ):
            autoupdate_release.validate_otool_contract(app)

    def test_otool_contract_rejects_x86_64_only_extra_rpath(self):
        app = self.root / autoupdate_release.APP_NAME
        framework = self.make_focus_framework(app)
        dependency = "x:\n\t{} (compatibility version 1.0.0)\n".format(
            autoupdate_release.SPARKLE_DEPENDENCY
        )
        good = "path {} (offset 12)\n".format(
            autoupdate_release.FOCUS_FRAMEWORK_RPATH
        )
        bad = good + "path @loader_path/x64-injected (offset 12)\n"
        with mock.patch.object(
            autoupdate_release,
            "_capture",
            side_effect=[dependency, good, dependency, bad],
        ), self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "x86_64 rpath mismatch"
        ):
            autoupdate_release.validate_otool_contract(app)

    def test_focus_framework_binary_resolves_real_chromium_version(self):
        app = self.root / autoupdate_release.APP_NAME
        executable = self.make_focus_framework(app)
        observed = autoupdate_release._focus_framework_binary(app)
        self.assertEqual(observed, executable)
        self.assertFalse(observed.is_symlink())

    def test_focus_framework_binary_rejects_duplicate_real_versions(self):
        app = self.root / autoupdate_release.APP_NAME
        executable = self.make_focus_framework(app)
        (executable.parents[1] / "149.0.0.0").mkdir()
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "exactly one real version"
        ):
            autoupdate_release._focus_framework_binary(app)

    def test_focus_framework_binary_rejects_current_traversal(self):
        app = self.root / autoupdate_release.APP_NAME
        executable = self.make_focus_framework(app)
        current = executable.parents[1] / "Current"
        current.unlink()
        current.symlink_to("../Versions/150.0.7871.128")
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "unsafe or unexpected target"
        ):
            autoupdate_release._focus_framework_binary(app)

    def test_focus_framework_binary_rejects_absolute_current_target(self):
        app = self.root / autoupdate_release.APP_NAME
        executable = self.make_focus_framework(app)
        current = executable.parents[1] / "Current"
        current.unlink()
        current.symlink_to(executable.parent)
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "unsafe or unexpected target"
        ):
            autoupdate_release._focus_framework_binary(app)

    def test_focus_framework_binary_rejects_real_executable_symlink(self):
        app = self.root / autoupdate_release.APP_NAME
        executable = self.make_focus_framework(app)
        target = executable.with_name("real-binary")
        executable.rename(target)
        executable.symlink_to(target.name)
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "must be a real regular file"
        ):
            autoupdate_release._focus_framework_binary(app)

    def test_focus_framework_binary_rejects_non_symlink_current(self):
        app = self.root / autoupdate_release.APP_NAME
        executable = self.make_focus_framework(app)
        current = executable.parents[1] / "Current"
        current.unlink()
        current.mkdir()
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "Current must be a symlink"
        ):
            autoupdate_release._focus_framework_binary(app)

    def test_focus_framework_binary_rejects_extra_versions_entry(self):
        app = self.root / autoupdate_release.APP_NAME
        executable = self.make_focus_framework(app)
        (executable.parents[1] / ".DS_Store").write_bytes(b"junk")
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "exactly one real version"
        ):
            autoupdate_release._focus_framework_binary(app)

    def test_thin_app_checks_all_focus_products_and_keeps_sparkle_universal(self):
        app = self.root / autoupdate_release.APP_NAME
        app.mkdir()
        report = {
            "universal_products": {
                "app": {"relative_path": "Contents/MacOS/Focus Browser"},
                "sparkle:framework": {
                    "relative_path": "Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle"
                },
            }
        }

        def architectures(path):
            if "Sparkle.framework" in str(path):
                return frozenset(("arm64", "x86_64"))
            return frozenset(("arm64",))

        with (
            mock.patch.object(autoupdate_release.autoupdate_contract, "validate_app_bundle", return_value=report),
            mock.patch.object(autoupdate_release, "_architectures", side_effect=architectures),
        ):
            observed = autoupdate_release.inspect_thin_app(app, "arm64")
        self.assertEqual(observed["architecture"], "arm64")
        self.assertEqual(len(observed["products"]), 2)

    def test_load_receipt_rejects_wrong_update_mode(self):
        value = {key: None for key in autoupdate_release.RECEIPT_KEYS["merge"]}
        value.update(
            schema=autoupdate_release.SCHEMA,
            stage="merge",
            update_mode="manual",
            publication=False,
            notarization=False,
            developer_id=False,
        )
        path = self.write("receipt.json", json.dumps(value))
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "contract mismatch"):
            autoupdate_release._load_receipt(path, "merge")

    def test_load_receipt_rejects_extra_schema_key(self):
        value = {key: None for key in autoupdate_release.RECEIPT_KEYS["merge"]}
        value.update(
            schema=autoupdate_release.SCHEMA,
            stage="merge",
            update_mode="autoupdate",
            publication=False,
            notarization=False,
            developer_id=False,
            extra=True,
        )
        path = self.write("receipt.json", json.dumps(value))
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "schema keys"):
            autoupdate_release._load_receipt(path, "merge")

    def test_ensure_absent_refuses_overwrite(self):
        path = self.write("already.dmg")
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "overwrite"):
            autoupdate_release._ensure_absent(path, "output")

    def test_tree_digest_rejects_escaping_symlink(self):
        tree = self.root / "tree"
        tree.mkdir()
        os.symlink("../../outside", tree / "escape")
        with self.assertRaisesRegex(autoupdate_release.ReleaseError, "escapes root"):
            autoupdate_release._tree_sha256(tree)

    def test_tree_contract_binds_root_mode_owner_and_xattrs_digest(self):
        tree = self.root / "tree-metadata"
        tree.mkdir(mode=0o755)
        self.write("tree-metadata/file", "payload")
        first = autoupdate_release._tree_contract(tree)
        tree.chmod(0o700)
        second = autoupdate_release._tree_contract(tree)
        self.assertNotEqual(first["tree_sha256"], second["tree_sha256"])
        self.assertEqual(second["root_mode"], "0700")
        self.assertEqual(second["owner_uid"], os.geteuid())
        self.assertRegex(second["xattrs_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(second["acls_sha256"], r"^[0-9a-f]{64}$")
        subprocess.run(
            ["/bin/chmod", "+a", "everyone allow read", str(tree)],
            check=True,
        )
        with_acl = autoupdate_release._tree_contract(tree)
        self.assertNotEqual(second["acls_sha256"], with_acl["acls_sha256"])
        subprocess.run(["/bin/chmod", "-N", str(tree)], check=True)
        tree.chmod(0o777)
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "unsafe permissions"
        ):
            autoupdate_release._tree_contract(tree)

    def test_tree_contract_rejects_regular_file_hardlink_alias(self):
        tree = self.root / "hardlinked-tree"
        tree.mkdir()
        original = self.write("hardlinked-tree/original", "payload")
        os.link(original, tree / "alias")
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "hard-link aliases"
        ):
            autoupdate_release._tree_contract(tree)

    def test_output_contract_binds_xattrs_flags_acl_and_link_count(self):
        output = self.write("metadata.dmg", b"payload", mode="wb")
        first = autoupdate_release._regular_output_contract(output)
        self.assertIn("xattrs", first)
        self.assertIn("acl", first)
        self.assertEqual(0, first["flags"])
        self.assertEqual(1, first["nlink"])
        subprocess.run(
            ["/usr/bin/xattr", "-w", "com.focusbrowser.test", "changed", str(output)],
            check=True,
        )
        second = autoupdate_release._regular_output_contract(output)
        self.assertNotEqual(first["xattrs"], second["xattrs"])

    def test_private_transaction_rejects_extended_acl(self):
        path = self.root / "private-transaction"
        path.mkdir(mode=0o700)
        observed = os.lstat(str(path))
        subprocess.run(
            ["/bin/chmod", "+a", "everyone allow read", str(path)],
            check=True,
        )
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "private transaction root changed"
        ):
            autoupdate_release._pin_private_directory(
                path, (observed.st_dev, observed.st_ino)
            )
        subprocess.run(["/bin/chmod", "-N", str(path)], check=True)

    def test_packaging_contract_rejects_any_extra_executable_surface(self):
        packaging = self.root / autoupdate_release.PACKAGING_NAME
        (packaging / "signing").mkdir(parents=True)
        self.write(
            "{}/signing/injected.py".format(autoupdate_release.PACKAGING_NAME),
            "raise SystemExit('injected')\n",
        )
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "inventory mismatch"
        ):
            autoupdate_release._packaging_contract(packaging, self.root)

    def test_pinned_packaging_python_rejects_unpinned_wrapper(self):
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "wrapper contract mismatch"
        ):
            autoupdate_release._pinned_packaging_python(self.root)

    def test_atomic_json_removes_only_its_partial_on_publish_failure(self):
        receipt = self.root / "receipt.json"
        with mock.patch.object(
            autoupdate_release,
            "_rename_no_replace",
            side_effect=autoupdate_release.ReleaseError("synthetic rename failure"),
        ), self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "synthetic rename failure"
        ):
            autoupdate_release._atomic_json(receipt, {"safe": True})
        self.assertFalse(receipt.exists())
        self.assertFalse(autoupdate_release._part_path(receipt).exists())

    def test_atomic_json_parent_fsync_failure_retains_uncertain_final(self):
        receipt = self.root / "uncertain-receipt.json"
        real_fsync_directory = autoupdate_release._fsync_directory
        calls = 0

        def fail_after_rename(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic parent fsync failure")
            return real_fsync_directory(path)

        with mock.patch.object(
            autoupdate_release,
            "_fsync_directory",
            side_effect=fail_after_rename,
        ), self.assertRaises(
            autoupdate_release.UncertainReleasePublicationError
        ) as raised:
            autoupdate_release._atomic_json(receipt, {"safe": True})

        self.assertEqual(str(receipt), raised.exception.output)
        self.assertEqual(str(receipt), raised.exception.retained_path)
        self.assertTrue(receipt.is_file())
        self.assertFalse(autoupdate_release._part_path(receipt).exists())
        self.assertEqual(
            raised.exception.final_identity,
            (receipt.stat().st_dev, receipt.stat().st_ino),
        )
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "refusing to overwrite"
        ):
            autoupdate_release._atomic_json(receipt, {"safe": True})

    def test_atomic_json_interrupted_rename_retains_uncertain_final(self):
        receipt = self.root / "interrupted-receipt.json"
        real_rename = autoupdate_release._rename_no_replace

        def rename_then_interrupt(source, destination):
            real_rename(source, destination)
            raise KeyboardInterrupt("synthetic post-rename interrupt")

        with mock.patch.object(
            autoupdate_release,
            "_rename_no_replace",
            side_effect=rename_then_interrupt,
        ), self.assertRaises(
            autoupdate_release.UncertainReleasePublicationError
        ):
            autoupdate_release._atomic_json(receipt, {"safe": True})

        self.assertTrue(receipt.is_file())
        self.assertFalse(autoupdate_release._part_path(receipt).exists())

    def test_atomic_json_postcommit_verification_failure_is_typed(self):
        receipt = self.root / "committed-receipt.json"
        with mock.patch.object(
            autoupdate_release,
            "_receipt_sha256",
            side_effect=OSError("synthetic receipt verification failure"),
        ), self.assertRaises(
            autoupdate_release.CommittedReleasePublicationError
        ) as raised:
            autoupdate_release._atomic_json(receipt, {"safe": True})

        self.assertEqual(str(receipt), raised.exception.output)
        self.assertTrue(receipt.is_file())
        self.assertFalse(autoupdate_release._part_path(receipt).exists())

    def test_publish_directory_parent_fsync_failure_retains_uncertain_final(self):
        final = self.root / "published-stage"
        part = autoupdate_release._part_path(final)
        part.mkdir(mode=0o700)
        identity = (part.stat().st_dev, part.stat().st_ino)
        real_fsync_directory = autoupdate_release._fsync_directory
        calls = 0

        def fail_after_rename(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic stage parent fsync failure")
            return real_fsync_directory(path)

        with mock.patch.object(
            autoupdate_release,
            "_fsync_directory",
            side_effect=fail_after_rename,
        ), self.assertRaises(
            autoupdate_release.UncertainReleasePublicationError
        ) as raised:
            autoupdate_release._publish_directory(part, final)

        self.assertEqual(identity, raised.exception.final_identity)
        self.assertTrue(final.is_dir())
        self.assertFalse(part.exists())

    def test_publish_directory_interrupted_rename_retains_uncertain_final(self):
        final = self.root / "interrupted-stage"
        part = autoupdate_release._part_path(final)
        part.mkdir(mode=0o700)
        real_rename = autoupdate_release._rename_no_replace

        def rename_then_interrupt(source, destination):
            real_rename(source, destination)
            raise KeyboardInterrupt("synthetic post-rename interrupt")

        with mock.patch.object(
            autoupdate_release,
            "_rename_no_replace",
            side_effect=rename_then_interrupt,
        ), self.assertRaises(
            autoupdate_release.UncertainReleasePublicationError
        ):
            autoupdate_release._publish_directory(part, final)

        self.assertTrue(final.is_dir())
        self.assertFalse(part.exists())

    def test_package_subprocess_report_rejects_duplicate_or_trailing_json(self):
        for payload in (
            b'{"safe":true,"safe":false}',
            b'{"safe":true}\nnot-json',
            b'[1,2,3]',
        ):
            with self.subTest(payload=payload), self.assertRaises(
                autoupdate_release.ReleaseError
            ):
                autoupdate_release._strict_json_object(payload, "fixture")

    def test_execute_sign_precreates_and_cleans_private_partial_on_failure(self):
        source = self.root / "source-sign-failure"
        source.mkdir()
        (source / "out").mkdir()
        packaging = self.packaging_fixture()
        wrapper = self.signing_wrapper_fixture()
        signed = autoupdate_release.release_paths(source)["signed"]
        snapshot = autoupdate_release._signing_snapshot_plan(
            autoupdate_release._part_path(signed), wrapper, packaging
        )
        plan = {
            "command": [
                "/pinned/python3.11",
                autoupdate_release.DESCRIPTOR_BOUND_WRAPPER,
            ],
            "signing_packaging": packaging,
            "signing_wrapper": wrapper,
            "signing_snapshot": snapshot,
            "packaging_python": self.python_fixture(),
        }
        with mock.patch.object(
            autoupdate_release, "sign_plan", return_value=plan
        ), mock.patch.object(
            autoupdate_release,
            "_packaging_contract",
            return_value=packaging,
        ), mock.patch.object(
            autoupdate_release,
            "_adhoc_signing_wrapper_contract",
            return_value=wrapper,
        ), mock.patch.object(
            autoupdate_release,
            "_create_signing_snapshot",
            return_value={**snapshot, "tree": self.tree_fixture()},
        ), mock.patch.object(
            autoupdate_release,
            "_validate_signing_snapshot_report",
        ), mock.patch.object(
            autoupdate_release,
            "_open_signing_snapshot_wrapper",
            side_effect=lambda _plan: os.open(os.devnull, os.O_RDONLY),
        ), mock.patch.object(
            autoupdate_release,
            "_remove_signing_snapshot",
        ), mock.patch.object(
            autoupdate_release,
            "_run",
            side_effect=autoupdate_release.ReleaseError("synthetic signing failure"),
        ), self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "synthetic signing failure"
        ):
            autoupdate_release.execute_sign(source, plan)
        self.assertFalse(signed.exists())
        self.assertFalse(autoupdate_release._part_path(signed).exists())

    def test_runtime_receipt_rejects_non_incognito_or_wrong_architecture_order(self):
        app = self.root / autoupdate_release.APP_NAME
        app.mkdir()
        report = self.runtime_fixture(app.resolve())
        report["architectures"][0]["incognito"] = False
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "arm64 acceptance mismatch"
        ):
            autoupdate_release._validate_runtime_acceptance_report(
                report, app, autoupdate_release.RUNTIME_TIMEOUT_SECONDS
            )

    def test_runtime_receipt_accepts_only_controlled_chromium_exit_codes(self):
        app = self.root / autoupdate_release.APP_NAME
        app.mkdir()
        report = self.runtime_fixture(app.resolve())
        exit_fields = (
            "exit_code",
            "verification_exit_code",
            "storage_control_write_exit_code",
            "storage_control_read_exit_code",
        )
        for architecture in report["architectures"]:
            for field in exit_fields:
                architecture[field] = 128 + signal.SIGINT
        self.assertIs(
            report,
            autoupdate_release._validate_runtime_acceptance_report(
                report, app, autoupdate_release.RUNTIME_TIMEOUT_SECONDS
            ),
        )
        report["architectures"][0]["exit_code"] = 7
        with self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "arm64 acceptance mismatch"
        ):
            autoupdate_release._validate_runtime_acceptance_report(
                report, app, autoupdate_release.RUNTIME_TIMEOUT_SECONDS
            )

    def test_accept_cli_requires_pinned_sparkle_root(self):
        parser = autoupdate_release.build_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["accept", "--source-root", "/tmp/source"])

    def test_relocate_report_rewrites_only_transaction_paths(self):
        report = {
            "path": "/out/.part/Focus Browser.app",
            "other": ["/out/.partial", "/out/.part"],
        }
        observed = autoupdate_release._relocate_report(report, "/out/.part", "/out/final")
        self.assertEqual(observed["path"], "/out/final/Focus Browser.app")
        self.assertEqual(observed["other"], ["/out/.partial", "/out/final"])

    def test_package_plan_is_explicit_universal_final_stage(self):
        source = self.root / "source"
        source.mkdir()
        sparkle_root = self.root / "sparkle"
        sparkle_root.mkdir()
        accepted_receipt_path = self.write("accept.json", "{}")
        output = self.root / "FocusBrowser.dmg"
        provenance = {
            "contract": {"sparkle": {"provenance": {"receipt_sha256": "s" * 64}}}
        }
        with (
            mock.patch.object(autoupdate_release, "_require_tools"),
            mock.patch.object(
                autoupdate_release,
                "_validated_accept",
                return_value=(
                    accepted_receipt_path,
                    {"app_tree": self.tree_fixture()},
                ),
            ) as accepted,
            mock.patch.object(autoupdate_release, "validate_universal_app", return_value=provenance),
            mock.patch.object(autoupdate_release, "sha256_file", return_value="a" * 64),
            mock.patch.object(
                autoupdate_release,
                "_pinned_packaging_python",
                return_value=self.python_fixture(),
            ),
            mock.patch.object(
                autoupdate_release,
                "_package_driver_contract",
                return_value={"entrypoint": "/driver", "modules": {}},
            ),
            mock.patch.object(autoupdate_release, "_ensure_absent"),
            mock.patch.object(
                autoupdate_release.package_local_dmg,
                "resolve_output_path",
                return_value=output,
            ),
        ):
            plan = autoupdate_release.package_plan(source, output, sparkle_root)
        self.assertEqual(plan["stage"], "package")
        self.assertTrue(plan["require_universal"])
        self.assertTrue(plan["require_autoupdate"])
        self.assertIn("--require-universal", plan["command"])
        self.assertIn("--require-autoupdate", plan["command"])
        self.assertEqual(plan["command"][0], self.python_fixture()["path"])
        self.assertEqual(
            plan["command"][1 : 1 + len(autoupdate_release.PINNED_PYTHON_ISOLATION_ARGS)],
            list(autoupdate_release.PINNED_PYTHON_ISOLATION_ARGS),
        )
        self.assertEqual(
            plan["command"][plan["command"].index("--output") + 1],
            plan["candidate_dmg"],
        )
        self.assertEqual(
            plan["command"][plan["command"].index("--sparkle-source-root") + 1],
            str(sparkle_root.resolve()),
        )
        self.assertFalse(plan["publication"])
        accepted.assert_called_once_with(source, sparkle_root.resolve())

    def test_execute_package_runs_pinned_python_subprocess_json(self):
        source = self.root / "source"
        source.mkdir()
        output = self.root / "FocusBrowser.dmg"
        sparkle_root = self.root / "sparkle"
        sparkle_root.mkdir()
        receipt = self.root / "FocusBrowser.dmg.autoupdate-release.json"
        app = self.root / autoupdate_release.APP_NAME
        app.mkdir()
        candidate_root = autoupdate_release._part_path(output)
        candidate = candidate_root / output.name
        app_tree = autoupdate_release._tree_contract(app)
        provenance = {
            "contract": {
                "sparkle": {"provenance": {"receipt_sha256": "s" * 64}}
            }
        }
        plan = {
            "app": str(app),
            "dmg_output": str(output),
            "candidate_root": str(candidate_root),
            "candidate_dmg": str(candidate),
            "package_receipt": str(receipt),
            "accept_receipt": {"sha256": "a" * 64},
            "sparkle_source_root": str(sparkle_root),
            "packaging_python": self.python_fixture(),
            "package_driver": {"entrypoint": "/driver", "modules": {}},
            "command": ["/pinned/python3.11", "-I", "/driver"],
            "accepted_app_tree": app_tree,
            "autoupdate_contract_with_sparkle_provenance": provenance,
        }

        def package_subprocess(command):
            self.assertEqual(plan["command"], command)
            path = candidate
            path.write_bytes(b"accepted dmg")
            digest = autoupdate_release.sha256_file(path)
            report = {
                "output": str(path),
                "architectures": ["arm64", "x86_64"],
                "require_universal": True,
                "require_autoupdate": True,
                "sparkle_source_root": str(sparkle_root),
                "notarization_performed": False,
                "signing_performed": False,
                "size_bytes": path.stat().st_size,
                "sha256": digest,
            }
            return subprocess.CompletedProcess(
                command, 0, json.dumps(report).encode("utf-8"), b""
            )

        mounted = {
            "dmg": str(candidate.resolve()),
            "size_bytes": len(b"accepted dmg"),
            "sha256": hashlib.sha256(b"accepted dmg").hexdigest(),
            "descriptor_pinned": True,
            "mounted_read_only": True,
            "runtime": self.runtime_fixture(
                Path("/private/tmp/focus-runtime-dmg/mounted")
                / autoupdate_release.APP_NAME
            ),
            "passed": True,
        }
        events = []
        accepted_path = self.write("accepted.json", "{}\n")
        real_publish = autoupdate_release.package_local_dmg.durable_publish_candidate

        def revalidate_accept(*_args):
            events.append("accept-rebind")
            return accepted_path, {"app_tree": app_tree}

        def publish(*args):
            events.append("durable-publish")
            return real_publish(*args)

        with (
            mock.patch.object(
                autoupdate_release, "package_plan", return_value=plan
            ),
            mock.patch.object(
                autoupdate_release, "_run", side_effect=package_subprocess
            ) as package,
            mock.patch.object(
                autoupdate_release,
                "_pinned_packaging_python",
                return_value=self.python_fixture(),
            ),
            mock.patch.object(
                autoupdate_release,
                "_package_driver_contract",
                return_value=plan["package_driver"],
            ),
            mock.patch.object(
                autoupdate_release,
                "_validated_accept",
                side_effect=revalidate_accept,
            ),
            mock.patch.object(
                autoupdate_release, "_receipt_sha256", return_value="a" * 64
            ),
            mock.patch.object(
                autoupdate_release.runtime_smoke,
                "validate_mounted_dmg_runtime",
                return_value=mounted,
            ),
            mock.patch.object(
                autoupdate_release,
                "validate_universal_app",
                return_value=provenance,
            ),
            mock.patch.object(
                autoupdate_release.package_local_dmg,
                "durable_publish_candidate",
                side_effect=publish,
            ),
        ):
            result = autoupdate_release.execute_package(
                source, output, sparkle_root, plan
            )
        package.assert_called_once_with(plan["command"])
        self.assertEqual(events, ["accept-rebind", "durable-publish"])
        self.assertEqual(result["stage"], "package")
        self.assertFalse(result["release"]["publication"])
        self.assertTrue(output.is_file())
        self.assertTrue(receipt.is_file())
        self.assertFalse(candidate_root.exists())

    def test_package_receipt_failure_preserves_verified_committed_dmg(self):
        source = self.root / "source-committed"
        source.mkdir()
        output = self.root / "Committed.dmg"
        receipt = Path(str(output) + autoupdate_release.PACKAGE_RECEIPT_SUFFIX)
        sparkle_root = self.root / "sparkle-committed"
        sparkle_root.mkdir()
        app = self.root / "committed" / autoupdate_release.APP_NAME
        app.mkdir(parents=True)
        candidate_root = autoupdate_release._part_path(output)
        candidate = candidate_root / output.name
        app_tree = autoupdate_release._tree_contract(app)
        provenance = {
            "contract": {
                "sparkle": {"provenance": {"receipt_sha256": "c" * 64}}
            }
        }
        plan = {
            "app": str(app),
            "dmg_output": str(output),
            "candidate_root": str(candidate_root),
            "candidate_dmg": str(candidate),
            "package_receipt": str(receipt),
            "accept_receipt": {"sha256": "a" * 64},
            "sparkle_source_root": str(sparkle_root),
            "packaging_python": self.python_fixture(),
            "package_driver": {"entrypoint": "/driver", "modules": {}},
            "command": ["/pinned/python3.11", "-I", "/driver"],
            "accepted_app_tree": app_tree,
            "autoupdate_contract_with_sparkle_provenance": provenance,
        }
        payload = b"durable accepted dmg"
        digest = hashlib.sha256(payload).hexdigest()

        def package_subprocess(command):
            path = candidate
            path.write_bytes(payload)
            report = {
                "output": str(path),
                "architectures": ["arm64", "x86_64"],
                "require_universal": True,
                "require_autoupdate": True,
                "sparkle_source_root": str(sparkle_root),
                "notarization_performed": False,
                "signing_performed": False,
                "size_bytes": len(payload),
                "sha256": digest,
            }
            return subprocess.CompletedProcess(
                command, 0, json.dumps(report).encode("utf-8"), b""
            )

        mounted = {
            "dmg": str(candidate.resolve()),
            "size_bytes": len(payload),
            "sha256": digest,
            "descriptor_pinned": True,
            "mounted_read_only": True,
            "runtime": self.runtime_fixture(
                Path("/private/tmp/focus-runtime-dmg/mounted")
                / autoupdate_release.APP_NAME
            ),
            "passed": True,
        }
        with mock.patch.object(
            autoupdate_release, "package_plan", return_value=plan
        ), mock.patch.object(
            autoupdate_release,
            "_run",
            side_effect=package_subprocess,
        ), mock.patch.object(
            autoupdate_release,
            "_pinned_packaging_python",
            return_value=self.python_fixture(),
        ), mock.patch.object(
            autoupdate_release,
            "_package_driver_contract",
            return_value=plan["package_driver"],
        ), mock.patch.object(
            autoupdate_release,
            "_validated_accept",
            return_value=(self.write("accepted-committed.json", "{}\n"), {"app_tree": app_tree}),
        ), mock.patch.object(
            autoupdate_release, "_receipt_sha256", return_value="a" * 64
        ), mock.patch.object(
            autoupdate_release.runtime_smoke,
            "validate_mounted_dmg_runtime",
            return_value=mounted,
        ), mock.patch.object(
            autoupdate_release,
            "validate_universal_app",
            return_value=provenance,
        ), mock.patch.object(
            autoupdate_release,
            "_atomic_json",
            side_effect=autoupdate_release.ReleaseError("receipt fsync failed"),
        ), self.assertRaisesRegex(
            autoupdate_release.CommittedOutputError, "remains committed"
        ) as raised:
            autoupdate_release.execute_package(
                source, output, sparkle_root, plan
            )
        self.assertEqual(raised.exception.sha256, digest)
        self.assertEqual(output.read_bytes(), payload)
        self.assertFalse(receipt.exists())
        self.assertFalse(candidate_root.exists())

    def test_unproven_dmg_detach_retains_private_candidate(self):
        source = self.root / "source-detach"
        source.mkdir()
        output = self.root / "Detach.dmg"
        sparkle_root = self.root / "sparkle-detach"
        sparkle_root.mkdir()
        app = self.root / "detach" / autoupdate_release.APP_NAME
        app.mkdir(parents=True)
        candidate_root = autoupdate_release._part_path(output)
        candidate = candidate_root / output.name
        provenance = {
            "contract": {
                "sparkle": {"provenance": {"receipt_sha256": "d" * 64}}
            }
        }
        plan = {
            "app": str(app),
            "dmg_output": str(output),
            "candidate_root": str(candidate_root),
            "candidate_dmg": str(candidate),
            "package_receipt": str(output) + autoupdate_release.PACKAGE_RECEIPT_SUFFIX,
            "accept_receipt": {"sha256": "a" * 64},
            "sparkle_source_root": str(sparkle_root),
            "packaging_python": self.python_fixture(),
            "package_driver": {"entrypoint": "/driver", "modules": {}},
            "command": ["/pinned/python3.11", "-I", "/driver"],
            "accepted_app_tree": autoupdate_release._tree_contract(app),
            "autoupdate_contract_with_sparkle_provenance": provenance,
        }
        payload = b"candidate retained"
        digest = hashlib.sha256(payload).hexdigest()

        def package_subprocess(command):
            path = candidate
            path.write_bytes(payload)
            report = {
                "output": str(path),
                "architectures": ["arm64", "x86_64"],
                "require_universal": True,
                "require_autoupdate": True,
                "sparkle_source_root": str(sparkle_root),
                "notarization_performed": False,
                "signing_performed": False,
                "size_bytes": len(payload),
                "sha256": digest,
            }
            return subprocess.CompletedProcess(
                command, 0, json.dumps(report).encode("utf-8"), b""
            )

        detach_error = autoupdate_release.runtime_smoke.DmgDetachError(
            "synthetic detach failure",
            mountpoint="/private/tmp/mounted",
            retained_root="/private/tmp/retained",
        )
        with mock.patch.object(
            autoupdate_release, "package_plan", return_value=plan
        ), mock.patch.object(
            autoupdate_release, "_run", side_effect=package_subprocess
        ), mock.patch.object(
            autoupdate_release,
            "_pinned_packaging_python",
            return_value=self.python_fixture(),
        ), mock.patch.object(
            autoupdate_release,
            "_package_driver_contract",
            return_value=plan["package_driver"],
        ), mock.patch.object(
            autoupdate_release.runtime_smoke,
            "validate_mounted_dmg_runtime",
            side_effect=detach_error,
        ), self.assertRaisesRegex(
            autoupdate_release.ReleaseError, "candidate retained"
        ):
            autoupdate_release.execute_package(
                source, output, sparkle_root, plan
            )
        self.assertFalse(output.exists())
        self.assertEqual(candidate.read_bytes(), payload)
        shutil.rmtree(candidate_root)

    def test_main_is_dry_run_by_default(self):
        plan = {"stage": "stage", "dry_run": True}
        with (
            mock.patch.object(autoupdate_release, "resolve_source_root", return_value=self.root),
            mock.patch.object(autoupdate_release, "stage_plan", return_value=plan),
            mock.patch.object(autoupdate_release, "execute_stage") as execute,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = autoupdate_release.main(
                ["stage", "--source-root", str(self.root), "--json"]
            )
        self.assertEqual(result, 0)
        execute.assert_not_called()

    def test_main_executes_only_with_explicit_flag(self):
        plan = {"stage": "stage", "dry_run": True}
        executed = {"stage": "stage", "update_mode": "autoupdate"}
        output = io.StringIO()
        with (
            mock.patch.object(autoupdate_release, "resolve_source_root", return_value=self.root),
            mock.patch.object(autoupdate_release, "stage_plan", return_value=plan),
            mock.patch.object(autoupdate_release, "execute_stage", return_value=executed) as execute,
            contextlib.redirect_stdout(output),
        ):
            result = autoupdate_release.main(
                ["stage", "--source-root", str(self.root), "--execute", "--json"]
            )
        self.assertEqual(result, 0)
        execute.assert_called_once_with(self.root, plan)
        self.assertIn('"dry_run": false', output.getvalue())

    def test_cli_has_no_all_in_one_or_publish_stage(self):
        parser = autoupdate_release.build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["publish", "--source-root", "/tmp/source"])


if __name__ == "__main__":
    unittest.main()
