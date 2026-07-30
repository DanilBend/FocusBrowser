"""Tests for fail-closed macOS runtime and signing acceptance."""

import os
import plistlib
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import runtime_smoke


class RuntimeSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.app = self.make_app(self.root / runtime_smoke.APP_NAME)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _touch(path, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture\n")
        if executable:
            path.chmod(0o755)
        return path

    def make_app(self, app):
        info = app / "Contents/Info.plist"
        info.parent.mkdir(parents=True)
        with info.open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleIdentifier": runtime_smoke.BUNDLE_ID,
                    "CFBundleExecutable": "Focus Browser",
                },
                stream,
            )
        self.executable = self._touch(
            app / "Contents/MacOS/Focus Browser", executable=True
        )
        version = (
            app
            / "Contents/Frameworks"
            / runtime_smoke.FRAMEWORK_NAME
            / "Versions/150.0.0.0"
        )
        helpers = version / "Helpers"
        for bundle, executable in (
            ("Focus Browser Helper.app", "Focus Browser Helper"),
            (
                "Focus Browser Helper (Renderer).app",
                "Focus Browser Helper (Renderer)",
            ),
            ("Focus Browser Helper (GPU).app", "Focus Browser Helper (GPU)"),
            (
                "Focus Browser Helper (Alerts).app",
                "Focus Browser Helper (Alerts)",
            ),
        ):
            self._touch(
                helpers / bundle / "Contents/MacOS" / executable,
                executable=True,
            )
        for executable in (
            "app_mode_loader",
            "web_app_shortcut_copier",
            "chrome_crashpad_handler",
        ):
            self._touch(helpers / executable, executable=True)
        for library in ("libEGL.dylib", "libGLESv2.dylib"):
            self._touch(version / "Libraries" / library)
        return app

    @staticmethod
    def _plist(value):
        return plistlib.dumps(value, fmt=plistlib.FMT_XML)

    def semantic_codesign_runner(self, crashpad_entitlement=False, loader_library=False):
        loaders, protected = runtime_smoke._signing_inventory(self.app)
        loader_paths = {str(path) for path in loaders.values()}
        protected_paths = {
            str(path): label for label, path in protected.items()
        }

        def run(command, *_args, **_kwargs):
            if command[0] == runtime_smoke.LIPO:
                return b"x86_64 arm64\n", b""
            self.assertEqual(runtime_smoke.CODESIGN, command[0])
            path = command[-1]
            is_entitlements = "--entitlements" in command
            if path in loader_paths:
                if is_entitlements:
                    return self._plist(
                        {
                            "preserved.entitlement": True,
                            runtime_smoke.DISABLE_LIBRARY_VALIDATION: True,
                        }
                    ), b"Executable=fixture\n"
                flags = ["runtime", "adhoc", "restrict", "kill"]
                if loader_library:
                    flags.append("library-validation")
            else:
                label = protected_paths[path]
                if is_entitlements:
                    value = {}
                    if crashpad_entitlement and label == "crashpad":
                        value[runtime_smoke.DISABLE_LIBRARY_VALIDATION] = True
                    return (self._plist(value) if value else b""), b"Executable=fixture\n"
                if label in ("crashpad", "privileged-helper"):
                    flags = [
                        "restrict",
                        "library-validation",
                        "adhoc",
                        "runtime",
                        "kill",
                    ]
                else:
                    flags = ["adhoc"]
            detail = (
                "Executable=fixture\n"
                "CodeDirectory v=20500 size=1 flags=0x2({}) hashes=1+1 location=embedded\n"
                "Signature=adhoc\n"
                "TeamIdentifier=not set\n"
            ).format(",".join(flags))
            return b"", detail.encode("utf-8")

        return run

    def test_codesign_matrix_parses_semantics_for_both_architectures(self):
        with mock.patch.object(
            runtime_smoke,
            "_run_capture",
            side_effect=self.semantic_codesign_runner(),
        ):
            report = runtime_smoke.validate_adhoc_signing_matrix(self.app)
        self.assertTrue(report["passed"])
        self.assertEqual(7, len(report["framework_loaders"]))
        for label in runtime_smoke.FRAMEWORK_LOADERS:
            product = report["products"][label]
            self.assertEqual(
                ["arm64", "x86_64"], sorted(product["architectures"])
            )
            for value in product["architectures"].values():
                self.assertTrue(value["disable_library_validation"])
                self.assertIn("preserved.entitlement", value["entitlement_keys"])
        crashpad = report["products"]["crashpad"]
        for value in crashpad["architectures"].values():
            self.assertIn("library-validation", value["flags"])
            self.assertFalse(value["disable_library_validation"])
        for label in ("framework", "dylib:libEGL.dylib", "dylib:libGLESv2.dylib"):
            for value in report["products"][label]["architectures"].values():
                self.assertEqual(["adhoc"], value["flags"])

    def test_codesign_matrix_rejects_library_validation_on_loader(self):
        with mock.patch.object(
            runtime_smoke,
            "_run_capture",
            side_effect=self.semantic_codesign_runner(loader_library=True),
        ), self.assertRaisesRegex(runtime_smoke.RuntimeSmokeError, "flags mismatch"):
            runtime_smoke.validate_adhoc_signing_matrix(self.app)

    def test_codesign_matrix_rejects_disable_entitlement_on_crashpad(self):
        with mock.patch.object(
            runtime_smoke,
            "_run_capture",
            side_effect=self.semantic_codesign_runner(crashpad_entitlement=True),
        ), self.assertRaisesRegex(runtime_smoke.RuntimeSmokeError, "unexpectedly disables"):
            runtime_smoke.validate_adhoc_signing_matrix(self.app)

    def test_runtime_browser_command_is_offline_incognito_and_bounded(self):
        captured = {}

        class Process:
            pid = 12345

            @staticmethod
            def wait(timeout):
                self.assertEqual(17, timeout)
                return 0

            @staticmethod
            def poll():
                return 0

        def popen(command, **kwargs):
            captured["command"] = list(command)
            captured["kwargs"] = kwargs
            data_url = command[-1]
            marker = data_url.split("FOCUSBROWSER_", 1)[1].split("_OK", 1)[0]
            marker = "FOCUSBROWSER_" + marker + "_OK"
            kwargs["stdout"].write(
                ("<main id=\"focus-runtime-smoke\">" + marker + "</main>").encode()
            )
            return Process()

        profile = self.root / "fresh-profile"
        profile.mkdir(mode=0o700)
        with mock.patch.object(runtime_smoke.subprocess, "Popen", side_effect=popen), mock.patch.object(
            runtime_smoke, "_signal_group", return_value=False
        ):
            report = runtime_smoke._run_browser(
                self.executable,
                "arm64",
                profile,
                self.root,
                17,
                {"PATH": runtime_smoke.SYSTEM_PATH},
            )
        command = captured["command"]
        self.assertEqual([runtime_smoke.ARCH, "-arm64", str(self.executable)], command[:3])
        self.assertIn("--incognito", command)
        self.assertIn("--host-resolver-rules=MAP * ~NOTFOUND", command)
        self.assertIn("--disable-background-networking", command)
        self.assertTrue(command[-1].startswith("data:text/html"))
        self.assertTrue(captured["kwargs"]["start_new_session"])
        self.assertTrue(report["marker_observed"])

    def test_runtime_timeout_interrupts_then_kills_process_group(self):
        signals = []

        class Process:
            pid = 34567

            def __init__(self):
                self.waits = 0

            def wait(self, timeout):
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired("browser", timeout)
                return -signal.SIGINT

            def poll(self):
                return None if self.waits < 2 else -signal.SIGINT

        def signal_group(group, value):
            signals.append((group, value))
            return True

        profile = self.root / "timeout-profile"
        profile.mkdir(mode=0o700)
        with mock.patch.object(runtime_smoke.subprocess, "Popen", return_value=Process()), mock.patch.object(
            runtime_smoke, "_signal_group", side_effect=signal_group
        ), self.assertRaisesRegex(runtime_smoke.RuntimeSmokeError, "timed out"):
            runtime_smoke._run_browser(
                self.executable,
                "x86_64",
                profile,
                self.root,
                5,
                {"PATH": runtime_smoke.SYSTEM_PATH},
            )
        self.assertEqual(
            [(34567, signal.SIGINT), (34567, signal.SIGKILL)], signals
        )

    def test_universal_runtime_requires_rosetta_and_fresh_profiles(self):
        profiles = []

        def browser(_exe, architecture, profile, _root, timeout, _environment):
            self.assertEqual(23, timeout)
            self.assertTrue(profile.is_dir())
            self.assertEqual(0o700, stat.S_IMODE(profile.stat().st_mode))
            profiles.append(str(profile))
            return {"architecture": architecture, "marker_observed": True}

        with mock.patch.object(runtime_smoke.platform, "system", return_value="Darwin"), mock.patch.object(
            runtime_smoke.platform, "machine", return_value="arm64"
        ), mock.patch.object(
            runtime_smoke, "_read_app", return_value=(self.app, self.executable)
        ), mock.patch.object(
            runtime_smoke, "_probe_architecture"
        ) as probe, mock.patch.object(
            runtime_smoke, "_run_browser", side_effect=browser
        ):
            report = runtime_smoke.validate_universal_app_runtime(
                self.app, timeout_seconds=23
            )
        self.assertEqual([mock.call("arm64", mock.ANY), mock.call("x86_64", mock.ANY)], probe.call_args_list)
        self.assertEqual(2, len(set(profiles)))
        self.assertTrue(report["rosetta_required"])
        self.assertTrue(report["passed"])

    def test_universal_runtime_fails_closed_when_rosetta_is_unavailable(self):
        def probe(architecture, _environment):
            if architecture == "x86_64":
                raise runtime_smoke.RuntimeSmokeError("Rosetta unavailable")

        with mock.patch.object(runtime_smoke.platform, "system", return_value="Darwin"), mock.patch.object(
            runtime_smoke.platform, "machine", return_value="arm64"
        ), mock.patch.object(
            runtime_smoke, "_read_app", return_value=(self.app, self.executable)
        ), mock.patch.object(
            runtime_smoke, "_probe_architecture", side_effect=probe
        ), mock.patch.object(runtime_smoke, "_run_browser") as browser, self.assertRaisesRegex(
            runtime_smoke.RuntimeSmokeError, "Rosetta unavailable"
        ):
            runtime_smoke.validate_universal_app_runtime(self.app)
        browser.assert_not_called()

    def test_final_dmg_mounts_read_only_runs_runtime_and_detaches(self):
        dmg = self.root / "FocusBrowser.dmg"
        dmg.write_bytes(b"mock dmg")
        commands = []

        def run(command, *_args, **_kwargs):
            commands.append(list(command))
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                (mountpoint / runtime_smoke.APP_NAME).mkdir()
                os.symlink("/Applications", str(mountpoint / "Applications"))
            return b"", b""

        statvfs = SimpleNamespace(f_flag=os.ST_RDONLY)
        with mock.patch.object(runtime_smoke, "_run_capture", side_effect=run), mock.patch.object(
            runtime_smoke.os.path, "ismount", return_value=True
        ), mock.patch.object(
            runtime_smoke.os, "statvfs", return_value=statvfs
        ), mock.patch.object(
            runtime_smoke,
            "validate_universal_app_runtime",
            return_value={"passed": True},
        ) as runtime:
            report = runtime_smoke.validate_mounted_dmg_runtime(dmg)
        self.assertTrue(report["mounted_read_only"])
        self.assertTrue(report["passed"])
        self.assertIn("-readonly", commands[0])
        self.assertEqual("detach", commands[-1][1])
        runtime.assert_called_once()

    def test_final_dmg_runtime_failure_still_detaches(self):
        dmg = self.root / "FocusBrowser.dmg"
        dmg.write_bytes(b"mock dmg")
        commands = []

        def run(command, *_args, **_kwargs):
            commands.append(list(command))
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                (mountpoint / runtime_smoke.APP_NAME).mkdir()
                os.symlink("/Applications", str(mountpoint / "Applications"))
            return b"", b""

        with mock.patch.object(runtime_smoke, "_run_capture", side_effect=run), mock.patch.object(
            runtime_smoke.os.path, "ismount", return_value=True
        ), mock.patch.object(
            runtime_smoke.os,
            "statvfs",
            return_value=SimpleNamespace(f_flag=os.ST_RDONLY),
        ), mock.patch.object(
            runtime_smoke,
            "validate_universal_app_runtime",
            side_effect=runtime_smoke.RuntimeSmokeError("synthetic runtime failure"),
        ), self.assertRaisesRegex(runtime_smoke.RuntimeSmokeError, "synthetic"):
            runtime_smoke.validate_mounted_dmg_runtime(dmg)
        self.assertEqual("detach", commands[-1][1])

    def test_final_dmg_failed_normal_and_forced_detach_is_typed(self):
        dmg = self.root / "FocusBrowser.dmg"
        dmg.write_bytes(b"mock dmg")
        commands = []

        def run(command, *_args, **_kwargs):
            commands.append(list(command))
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                (mountpoint / runtime_smoke.APP_NAME).mkdir()
                os.symlink("/Applications", str(mountpoint / "Applications"))
                return b"", b""
            raise runtime_smoke.RuntimeSmokeError("synthetic detach failure")

        with mock.patch.object(runtime_smoke, "_run_capture", side_effect=run), mock.patch.object(
            runtime_smoke.os.path, "ismount", return_value=True
        ), mock.patch.object(
            runtime_smoke.os,
            "statvfs",
            return_value=SimpleNamespace(f_flag=os.ST_RDONLY),
        ), mock.patch.object(
            runtime_smoke,
            "validate_universal_app_runtime",
            return_value={"passed": True},
        ), self.assertRaisesRegex(
            runtime_smoke.DmgDetachError, "normal detach failed"
        ):
            runtime_smoke.validate_mounted_dmg_runtime(dmg)

        self.assertEqual("detach", commands[-2][1])
        self.assertEqual(["detach", "-force"], commands[-1][1:3])


if __name__ == "__main__":
    unittest.main()
