"""Unit tests for the safe macOS Chromium acquisition planner."""

import importlib.util
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PLATFORM_DIR / "acquire_chromium.py"
SPEC = importlib.util.spec_from_file_location("acquire_chromium", MODULE_PATH)
acquire = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquire)


class AcquireChromiumTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.parent = self.root / "external"
        self.parent.mkdir()
        self.destination = self.parent / "chromium-150"

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def disk_usage(gib=200):
        free = gib * acquire.GIB
        return SimpleNamespace(total=free * 2, used=free, free=free)

    def preflight(self, **kwargs):
        options = {
            "destination": self.destination,
            "environ": {"PATH": os.environ.get("PATH", "")},
            "platform_name": "darwin",
            "machine": "arm64",
        }
        options.update(kwargs)
        with mock.patch.object(
            acquire.shutil, "disk_usage", return_value=self.disk_usage()
        ):
            return acquire.preflight(**options)

    def test_immutable_pins_are_exact(self):
        self.assertEqual("150.0.7871.128", acquire.CHROMIUM_VERSION)
        self.assertEqual(
            "81891e5ca708047763816c778216799ef14c66cb",
            acquire.CHROMIUM_COMMIT,
        )
        self.assertEqual(
            "93919990d65a94fd62a5b1bae4e2909df6996e4a",
            acquire.DEPOT_TOOLS_COMMIT,
        )
        self.assertEqual(
            "158806c990d70174a6f401ae488d03246d867e0272b753bfbcb7c1757633b9ea",
            acquire.DEPS_INI_SHA256,
        )

    def test_plan_is_mac_only_pinned_shallow_and_hook_free(self):
        report = self.preflight()
        self.assertEqual(["mac"], report["gclient"]["target_os"])
        self.assertTrue(report["gclient"]["target_os_only"])
        sync = report["commands"][-1]
        self.assertIn("--no-history", sync)
        self.assertIn("--nohooks", sync)
        self.assertIn("src@" + acquire.CHROMIUM_COMMIT, sync)
        config = report["commands"][-2]
        spec = config[-1]
        expected_spec = """solutions = [
  {
    "name": "src",
    "url": 'https://chromium.googlesource.com/chromium/src.git',
    "custom_deps": {
      'src/third_party/angle/third_party/VK-GL-CTS/src': None,
    },
    "custom_vars": {
      "checkout_configuration": "small",
      "non_git_source": "False",
    },
  },
]
target_os = ["mac"]
target_os_only = True
"""
        self.assertEqual(expected_spec, spec)
        self.assertEqual(
            "c2ab1fe66688245018194e7845ba97102efbf9f0d40eddf87712ec7f46ce26af",
            report["gclient"]["spec_sha256"],
        )
        self.assertIn('target_os = ["mac"]', spec)
        self.assertIn("target_os_only = True", spec)
        self.assertIn(
            "{!r}: None".format(acquire.EXCLUDED_ANGLE_TEST_DEP), spec
        )
        self.assertIn('"checkout_configuration": "small"', spec)
        self.assertIn('"non_git_source": "False"', spec)
        self.assertNotIn("cache_dir", spec)
        self.assertNotIn("--git-cache", "\n".join(sync))
        self.assertFalse(report["gclient"]["git_cache"])
        self.assertNotIn("cache", report)
        combined = "\n".join(argument for command in report["commands"] for argument in command)
        self.assertNotIn("target_os = [\"ios\"]", combined)
        self.assertNotIn("target_os = [\"win\"]", combined)

    def test_plan_pins_depot_tools_fetch_and_checkout(self):
        commands = self.preflight()["commands"]
        fetch = commands[2]
        checkout = commands[3]
        self.assertEqual("--depth=1", fetch[-3])
        self.assertEqual(acquire.DEPOT_TOOLS_COMMIT, fetch[-1])
        self.assertEqual(acquire.DEPOT_TOOLS_COMMIT, checkout[-1])

    def test_default_preflight_has_no_network_or_writes(self):
        with mock.patch.object(acquire, "preflight", return_value={"status": "preflight_only"}), \
                mock.patch.object(acquire, "execute") as execute_mock:
            output = io.StringIO()
            with redirect_stdout(output):
                result = acquire.main(
                    ["--destination", str(self.destination)]
                )
        self.assertEqual(0, result)
        execute_mock.assert_not_called()
        self.assertEqual("preflight_only", json.loads(output.getvalue())["status"])

    def test_project_dependencies_are_opt_in_and_manifest_pinned(self):
        disabled = self.preflight()
        self.assertFalse(disabled["project_dependencies"]["enabled"])
        self.assertEqual([], disabled["project_dependencies"]["commands"])
        self.assertFalse(disabled["project_dependencies"]["windows_downloads_ini_used"])

        dependency_cache = self.parent / "focus-dependencies"
        report = self.preflight(
            dependency_cache=dependency_cache,
            include_project_dependencies=True,
        )
        dependencies = report["project_dependencies"]
        self.assertTrue(dependencies["enabled"])
        self.assertEqual(acquire.DEPS_INI_SHA256, dependencies["manifest"]["sha256"])
        self.assertEqual(3, len(dependencies["commands"]))
        self.assertFalse(dependencies["unpack_planned"])
        self.assertFalse(dependencies["source_mutation_planned"])
        for command, contract in zip(
            dependencies["commands"], acquire.PROJECT_DEPENDENCIES
        ):
            self.assertEqual("curl", Path(command[0]).name)
            self.assertIn("--max-filesize", command)
            self.assertIn("=https", command)
            self.assertIn(
                str(dependency_cache / (contract["filename"] + ".part")), command
            )
            self.assertEqual(contract["url"], command[-1])

    def test_dependency_stage_requires_explicit_cache_and_flag_pair(self):
        with self.assertRaisesRegex(acquire.AcquisitionError, "dependency-cache"):
            self.preflight(include_project_dependencies=True)
        with self.assertRaisesRegex(acquire.AcquisitionError, "accepted only"):
            self.preflight(dependency_cache=self.parent / "unused-dependencies")

    def test_dependency_hash_verification_precedes_atomic_rename(self):
        dependency_cache = self.parent / "dependency-fixture"
        dependency_cache.mkdir()
        payload = b"verified dependency fixture\n"
        digest = hashlib.sha256(payload).hexdigest()
        manifest = {
            "sha256": "f" * 64,
            "entries": [
                {
                    "name": "fixture",
                    "url": "https://example.invalid/fixture.zip",
                    "filename": "fixture.zip",
                    "sha256": digest,
                }
            ],
        }
        partial = dependency_cache / "fixture.zip.part"
        partial.write_bytes(payload)
        report = acquire.finalize_dependency_downloads(dependency_cache, manifest)
        final = dependency_cache / "fixture.zip"
        self.assertFalse(partial.exists())
        self.assertEqual(payload, final.read_bytes())
        self.assertEqual(digest, report["archives"][0]["sha256"])
        self.assertFalse(report["unpacked"])
        self.assertFalse(report["source_mutated"])

    def test_dependency_hash_mismatch_keeps_only_part_file(self):
        dependency_cache = self.parent / "dependency-mismatch"
        dependency_cache.mkdir()
        manifest = {
            "sha256": "e" * 64,
            "entries": [
                {
                    "name": "fixture",
                    "url": "https://example.invalid/fixture.zip",
                    "filename": "fixture.zip",
                    "sha256": "0" * 64,
                }
            ],
        }
        partial = dependency_cache / "fixture.zip.part"
        partial.write_bytes(b"wrong")
        with self.assertRaisesRegex(acquire.AcquisitionError, "hash mismatch"):
            acquire.finalize_dependency_downloads(dependency_cache, manifest)
        self.assertTrue(partial.exists())
        self.assertFalse((dependency_cache / "fixture.zip").exists())

    def test_execute_requires_explicit_flag(self):
        report = {"status": "preflight_only"}
        completed = {"status": "acquisition_complete"}
        with mock.patch.object(acquire, "preflight", return_value=report), \
                mock.patch.object(acquire, "execute", return_value=completed) as execute_mock:
            output = io.StringIO()
            with redirect_stdout(output):
                result = acquire.main(
                    [
                        "--destination",
                        str(self.destination),
                        "--execute-acquisition",
                    ]
                )
        self.assertEqual(0, result)
        execute_mock.assert_called_once_with(report)
        self.assertEqual("acquisition_complete", json.loads(output.getvalue())["status"])

    def test_execute_rejects_tampered_command_plan_before_write(self):
        report = self.preflight()
        report["commands"][-1][-1] = "src@" + ("0" * 40)
        with mock.patch.object(
            acquire.shutil, "disk_usage", return_value=self.disk_usage()
        ), mock.patch.object(acquire, "validate_host", return_value="arm64"):
            with self.assertRaisesRegex(
                acquire.AcquisitionError, "changed after preflight"
            ):
                acquire.execute(report, {"PATH": os.environ.get("PATH", "")})
        self.assertFalse(self.destination.exists())

    def test_execute_success_with_mocked_acquisition_subprocesses(self):
        report = self.preflight()

        def fake_run(command, _cwd, _environ, _watched_paths):
            if command[1:3] == ["init", "--quiet"]:
                (self.destination / "depot_tools").mkdir()
            elif Path(command[0]).name == "gclient" and command[1] == "config":
                (self.destination / ".gclient").write_text(
                    acquire.gclient_spec(), encoding="utf-8"
                )
            elif Path(command[0]).name == "gclient" and command[1] == "sync":
                chrome = self.destination / "src" / "chrome"
                chrome.mkdir(parents=True)
                (chrome / "VERSION").write_text(
                    "MAJOR=150\nMINOR=0\nBUILD=7871\nPATCH=128\n",
                    encoding="utf-8",
                )

        with mock.patch.object(
            acquire.shutil, "disk_usage", return_value=self.disk_usage()
        ), mock.patch.object(
            acquire, "validate_host", return_value="arm64"
        ), mock.patch.object(
            acquire, "run_monitored", side_effect=fake_run
        ) as runner, mock.patch.object(
            acquire,
            "capture",
            side_effect=[acquire.DEPOT_TOOLS_COMMIT, acquire.CHROMIUM_COMMIT],
        ):
            completed = acquire.execute(
                report, {"PATH": os.environ.get("PATH", "")}
            )
        self.assertEqual(6, runner.call_count)
        self.assertEqual("acquisition_complete", completed["status"])
        marker = Path(completed["complete_marker"])
        self.assertTrue(marker.is_file())
        self.assertEqual(
            acquire.CHROMIUM_COMMIT,
            json.loads(marker.read_text(encoding="utf-8"))["verification"][
                "chromium_commit"
            ],
        )

    def test_rejects_non_macos_and_ios_sdk(self):
        with self.assertRaisesRegex(acquire.AcquisitionError, "only on macOS"):
            acquire.validate_host({}, "win32", "AMD64")
        with self.assertRaisesRegex(acquire.AcquisitionError, "iOS-family"):
            acquire.validate_host(
                {"SDKROOT": "/Platforms/iPhoneOS.platform/SDKs/iPhoneOS.sdk"},
                "darwin",
                "arm64",
            )

    def test_accepts_arm_and_intel_macs(self):
        self.assertEqual("arm64", acquire.validate_host({}, "darwin", "arm64"))
        self.assertEqual("x86_64", acquire.validate_host({}, "darwin", "x86_64"))

    def test_rejects_relative_existing_and_nested_targets(self):
        with self.assertRaisesRegex(acquire.AcquisitionError, "absolute"):
            acquire.validate_new_leaf(Path("chromium"), "destination")
        self.destination.mkdir()
        with self.assertRaisesRegex(acquire.AcquisitionError, "already exists"):
            acquire.validate_new_leaf(self.destination, "destination")
        with self.assertRaisesRegex(acquire.AcquisitionError, "contain"):
            acquire.validate_distinct_paths(
                self.parent / "source", self.parent / "source" / "cache"
            )

    def test_rejects_symlinked_parent(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "linked"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(acquire.AcquisitionError, "symlink"):
            acquire.validate_new_leaf(link / "chromium", "destination")

    def test_disk_gates_are_explicit(self):
        with mock.patch.object(
            acquire.shutil, "disk_usage", return_value=self.disk_usage(114)
        ):
            with self.assertRaisesRegex(acquire.AcquisitionError, "pre-sync gate"):
                acquire.ensure_initial_capacity(self.parent)
        with mock.patch.object(
            acquire.shutil, "disk_usage", return_value=self.disk_usage(84)
        ):
            with self.assertRaisesRegex(acquire.AcquisitionError, "post-sync"):
                acquire.ensure_post_sync_capacity(self.parent)
        with mock.patch.object(
            acquire.shutil, "disk_usage", return_value=self.disk_usage(29)
        ):
            with self.assertRaisesRegex(acquire.AcquisitionError, "hard disk floor"):
                acquire.ensure_hard_floor((self.parent,))

    def test_safe_environment_is_child_only_strips_ios_sdk_and_git_cache(self):
        inherited = {
            "PATH": "/usr/bin",
            "SDKROOT": "/iPhoneOS.sdk",
            "IPHONEOS_DEPLOYMENT_TARGET": "27.0",
            "GIT_CACHE_PATH": "/unexpected/global-cache",
            "DEVELOPER_DIR": "/Applications/Xcode-beta.app/Contents/Developer",
        }
        environment = acquire.safe_environment(self.destination, inherited)
        self.assertNotIn("SDKROOT", environment)
        self.assertNotIn("IPHONEOS_DEPLOYMENT_TARGET", environment)
        self.assertNotIn("GIT_CACHE_PATH", environment)
        self.assertEqual("0", environment["DEPOT_TOOLS_UPDATE"])
        self.assertEqual(inherited["DEVELOPER_DIR"], environment["DEVELOPER_DIR"])
        self.assertEqual("/iPhoneOS.sdk", inherited["SDKROOT"])

    def test_removed_git_cache_cli_is_rejected(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                acquire.parse_args(
                    [
                        "--destination",
                        str(self.destination),
                        "--cache",
                        str(self.parent / "forbidden-git-cache"),
                    ]
                )

    def test_commands_never_change_global_xcode_selection(self):
        report = self.preflight()
        programs = [Path(command[0]).name for command in report["commands"]]
        arguments = [item for command in report["commands"] for item in command]
        self.assertNotIn("xcode-select", programs)
        self.assertNotIn("sudo", programs)
        self.assertNotIn("--global", arguments)
        self.assertFalse(report["global_xcode_select_mutation"])

    def test_monitor_uses_argv_popen_and_checks_floor(self):
        process = mock.Mock()
        process.poll.side_effect = [None, 0]
        process.returncode = 0
        process.pid = 42
        with mock.patch.object(acquire, "ensure_hard_floor") as floor_mock, \
                mock.patch.object(
                    acquire.subprocess, "Popen", return_value=process
                ) as popen_mock, \
                mock.patch.object(acquire.time, "sleep"):
            acquire.run_monitored(
                ["/usr/bin/git", "--version"],
                self.parent,
                {"PATH": "/usr/bin"},
                (self.parent,),
            )
        self.assertGreaterEqual(floor_mock.call_count, 3)
        _, kwargs = popen_mock.call_args
        self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("shell", kwargs)

    def test_monitor_stops_process_group_on_floor_breach(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.pid = 73
        floor_error = acquire.AcquisitionError("hard disk floor breached")
        with mock.patch.object(
            acquire, "ensure_hard_floor", side_effect=[None, floor_error]
        ), mock.patch.object(acquire.subprocess, "Popen", return_value=process), \
                mock.patch.object(acquire, "_stop_process_group") as stop_mock:
            with self.assertRaises(acquire.AcquisitionError):
                acquire.run_monitored(
                    ["/usr/bin/git", "fetch"],
                    self.parent,
                    {"PATH": "/usr/bin"},
                    (self.parent,),
                )
        stop_mock.assert_called_once_with(process)

    def test_verify_checkout_rejects_wrong_revision(self):
        self.destination.mkdir()
        (self.destination / "depot_tools").mkdir()
        source = self.destination / "src"
        (source / "chrome").mkdir(parents=True)
        (source / "chrome" / "VERSION").write_text(
            "MAJOR=150\nMINOR=0\nBUILD=7871\nPATCH=128\n", encoding="utf-8"
        )
        (self.destination / ".gclient").write_text(
            acquire.gclient_spec(), encoding="utf-8"
        )
        with mock.patch.object(
            acquire,
            "capture",
            side_effect=[acquire.DEPOT_TOOLS_COMMIT, "0" * 40],
        ):
            with self.assertRaisesRegex(acquire.AcquisitionError, "Chromium HEAD"):
                acquire.verify_checkout(
                    "/usr/bin/git", self.destination, {"PATH": "/usr/bin"}
                )

    def test_main_reports_safety_failure_without_traceback(self):
        with mock.patch.object(
            acquire, "preflight", side_effect=acquire.AcquisitionError("blocked fixture")
        ):
            error = io.StringIO()
            with redirect_stderr(error):
                result = acquire.main(
                    ["--destination", str(self.destination)]
                )
        self.assertEqual(2, result)
        payload = json.loads(error.getvalue())
        self.assertEqual("blocked", payload["status"])
        self.assertIn("blocked fixture", payload["error"])


if __name__ == "__main__":
    unittest.main()
