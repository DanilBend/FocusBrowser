"""Tests for fail-closed macOS runtime and signing acceptance."""

import errno
import http.client
import os
import plistlib
import shutil
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

    def semantic_codesign_runner(
        self,
        crashpad_entitlement=False,
        loader_library=False,
        extra_entitlement_label=None,
    ):
        loaders, protected = runtime_smoke._signing_inventory(self.app)
        loader_paths = {str(path): label for label, path in loaders.items()}
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
                label = loader_paths[path]
                if is_entitlements:
                    expected = {
                        "app": runtime_smoke.APP_ENTITLEMENTS,
                        "helper-renderer-app": runtime_smoke.JIT_LOADER_ENTITLEMENTS,
                        "helper-gpu-app": runtime_smoke.JIT_LOADER_ENTITLEMENTS,
                    }.get(label, runtime_smoke.LIBRARY_LOADING_ENTITLEMENTS)
                    value = dict(expected)
                    if extra_entitlement_label == label:
                        value["unreviewed.extra"] = True
                    return self._plist(value), b"Executable=fixture\n"
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
                if label == "crashpad":
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
                self.assertNotIn("unreviewed.extra", value["entitlement_keys"])
                self.assertTrue(all(item is True for item in value["entitlements"].values()))
        crashpad = report["products"]["crashpad"]
        for value in crashpad["architectures"].values():
            self.assertIn("library-validation", value["flags"])
            self.assertFalse(value["disable_library_validation"])
        for label in ("dylib:libEGL.dylib", "dylib:libGLESv2.dylib"):
            for value in report["products"][label]["architectures"].values():
                self.assertEqual(["adhoc"], value["flags"])
        for value in report["products"]["framework"]["architectures"].values():
            self.assertEqual(["adhoc"], value["flags"])
            self.assertEqual({}, value["entitlements"])

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
        ), self.assertRaisesRegex(runtime_smoke.RuntimeSmokeError, "must have no entitlements"):
            runtime_smoke.validate_adhoc_signing_matrix(self.app)

    def test_codesign_matrix_rejects_any_extra_loader_entitlement(self):
        with mock.patch.object(
            runtime_smoke,
            "_run_capture",
            side_effect=self.semantic_codesign_runner(
                extra_entitlement_label="helper-renderer-app"
            ),
        ), self.assertRaisesRegex(runtime_smoke.RuntimeSmokeError, "entitlements mismatch"):
            runtime_smoke.validate_adhoc_signing_matrix(self.app)

    def test_signing_inventory_rejects_updater_privileged_helper(self):
        self._touch(
            self.app
            / "Contents/Library/LaunchServices"
            / "com.focusbrowser.UpdaterPrivilegedHelper",
            executable=True,
        )
        with self.assertRaisesRegex(
            runtime_smoke.RuntimeSmokeError, "manual-update-only"
        ):
            runtime_smoke._signing_inventory(self.app)

    def test_read_app_rejects_application_update_plist_keys_before_lipo(self):
        info_path = self.app / "Contents/Info.plist"
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
        for key in ("KSUpdateURL", "SUFeedURL"):
            with self.subTest(key=key):
                updated = dict(info)
                updated[key] = "https://updates.invalid/feed"
                with info_path.open("wb") as stream:
                    plistlib.dump(updated, stream)
                with mock.patch.object(
                    runtime_smoke, "_run_capture"
                ) as run, self.assertRaisesRegex(
                    runtime_smoke.RuntimeSmokeError,
                    "prohibited Info.plist keys",
                ):
                    runtime_smoke._read_app(self.app)
                run.assert_not_called()
        with info_path.open("wb") as stream:
            plistlib.dump(info, stream)

    def test_read_app_rejects_application_update_artifacts_before_lipo(self):
        relative_paths = (
            "Contents/Frameworks/Sparkle.framework",
            "Contents/Helpers/GoogleUpdater.app",
            "Contents/Resources/ksadmin",
        )
        for relative in relative_paths:
            with self.subTest(relative=relative):
                artifact = self.app / relative
                artifact.mkdir(parents=True)
                try:
                    with mock.patch.object(
                        runtime_smoke, "_run_capture"
                    ) as run, self.assertRaisesRegex(
                        runtime_smoke.RuntimeSmokeError,
                        "prohibited updater artifact",
                    ):
                        runtime_smoke._read_app(self.app)
                    run.assert_not_called()
                finally:
                    artifact.rmdir()

    def test_autoupdate_read_requires_full_release_contract_and_provenance(self):
        sparkle_root = self.root / "sparkle-source"
        sparkle_root.mkdir()
        with mock.patch.object(
            runtime_smoke.autoupdate_contract,
            "validate_release_bundle",
            return_value={"schema": 2, "passed": True},
        ) as contract, mock.patch.object(
            runtime_smoke,
            "_run_capture",
            return_value=(b"arm64 x86_64\n", b""),
        ):
            app, executable = runtime_smoke._read_app(
                self.app,
                update_mode="autoupdate",
                sparkle_source_root=sparkle_root,
            )
        self.assertEqual(self.app, app)
        self.assertEqual(self.executable, executable)
        contract.assert_called_once_with(self.app, sparkle_root)

    def test_autoupdate_read_fails_closed_without_provenance(self):
        with mock.patch.object(
            runtime_smoke.autoupdate_contract, "validate_release_bundle"
        ) as contract, mock.patch.object(
            runtime_smoke, "_run_capture"
        ) as run, self.assertRaisesRegex(
            runtime_smoke.RuntimeSmokeError, "requires pinned Sparkle provenance"
        ):
            runtime_smoke._read_app(self.app, update_mode="autoupdate")
        contract.assert_not_called()
        run.assert_not_called()

    def test_autoupdate_read_rejects_error_or_nonpassing_release_contract(self):
        sparkle_root = self.root / "sparkle-source"
        sparkle_root.mkdir()
        cases = (
            runtime_smoke.autoupdate_contract.AutoupdateContractError("bad gate"),
            {"schema": 2, "passed": False},
        )
        for result in cases:
            with self.subTest(result=result):
                patch = (
                    mock.patch.object(
                        runtime_smoke.autoupdate_contract,
                        "validate_release_bundle",
                        side_effect=result,
                    )
                    if isinstance(result, Exception)
                    else mock.patch.object(
                        runtime_smoke.autoupdate_contract,
                        "validate_release_bundle",
                        return_value=result,
                    )
                )
                with patch, mock.patch.object(
                    runtime_smoke, "_run_capture"
                ) as run, self.assertRaisesRegex(
                    runtime_smoke.RuntimeSmokeError, "automatic-update app contract"
                ):
                    runtime_smoke._read_app(
                        self.app,
                        update_mode="autoupdate",
                        sparkle_source_root=sparkle_root,
                    )
                run.assert_not_called()

    def test_universal_runtime_forwards_update_contract_mode(self):
        sparkle_root = self.root / "sparkle-source"
        sparkle_root.mkdir()
        with mock.patch.object(
            runtime_smoke.platform, "system", return_value="Darwin"
        ), mock.patch.object(
            runtime_smoke.platform, "machine", return_value="arm64"
        ), mock.patch.object(
            runtime_smoke, "_read_app", return_value=(self.app, self.executable)
        ) as read, mock.patch.object(
            runtime_smoke, "_probe_architecture"
        ), mock.patch.object(
            runtime_smoke,
            "_run_browser",
            side_effect=lambda _exe, arch, *_args: {"architecture": arch},
        ):
            runtime_smoke.validate_universal_app_runtime(
                self.app,
                update_mode="autoupdate",
                sparkle_source_root=sparkle_root,
            )
        read.assert_called_once_with(
            self.app,
            update_mode="autoupdate",
            sparkle_source_root=sparkle_root,
        )

    def test_runtime_browser_command_is_offline_incognito_and_bounded(self):
        captured = []

        def execute(command, expected, probe_server, timeout, environment, label):
            captured.append(
                {
                    "command": list(command),
                    "expected": expected,
                    "probe_server": probe_server,
                    "timeout": timeout,
                    "environment": environment,
                    "label": label,
                }
            )
            return b"", b"", 0

        profile = self.root / "fresh-profile"
        profile.mkdir(mode=0o700)
        marker = "FOCUSBROWSER_ARM64_{}_OK".format("AB" * 12)
        control = "CONTROL_" + marker
        probe_server = SimpleNamespace(
            url="http://127.0.0.1:8123/focus-runtime-probe-test.html"
        )
        with mock.patch.object(
            runtime_smoke.secrets, "token_hex", return_value="ab" * 12
        ), mock.patch.object(
            runtime_smoke,
            "_execute_browser_probe",
            side_effect=execute,
        ):
            report = runtime_smoke._run_browser(
                self.executable,
                "arm64",
                profile,
                self.root,
                probe_server,
                17,
                {"PATH": runtime_smoke.SYSTEM_PATH},
            )
        self.assertEqual(4, len(captured))
        control_write_command, control_read_command, command, verification = (
            [item["command"] for item in captured]
        )
        self.assertNotIn("--incognito", control_write_command)
        self.assertNotIn("--incognito", control_read_command)
        self.assertEqual([runtime_smoke.ARCH, "-arm64", str(self.executable)], command[:3])
        self.assertIn("--incognito", command)
        self.assertNotIn("--incognito", verification)
        self.assertIn(
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
            command,
        )
        self.assertIn("--disable-background-networking", command)
        self.assertTrue(command[-1].startswith("http://127.0.0.1:8123/"))
        self.assertTrue(verification[-1].startswith("http://127.0.0.1:8123/"))
        self.assertEqual(command[-3], verification[-3])
        self.assertEqual(
            [
                ("WRITE_OK_" + control).encode("ascii"),
                ("LEAK_" + control).encode("ascii"),
                ("WRITE_OK_" + marker).encode("ascii"),
                ("ABSENT_" + marker).encode("ascii"),
            ],
            [item["expected"] for item in captured],
        )
        self.assertTrue(all(item["probe_server"] is probe_server for item in captured))
        self.assertTrue(all(item["timeout"] == 17 for item in captured))
        self.assertTrue(report["marker_observed"])
        self.assertTrue(report["incognito_storage_isolated"])
        self.assertTrue(report["storage_control_persistence_verified"])

    def test_runtime_browser_fails_closed_without_absent_beacon(self):
        profile = self.root / "leaking-profile"
        profile.mkdir(mode=0o700)
        probe_server = SimpleNamespace(
            url="http://127.0.0.1:8123/focus-runtime-probe-test.html"
        )
        with mock.patch.object(
            runtime_smoke.secrets, "token_hex", return_value="ab" * 12
        ), mock.patch.object(
            runtime_smoke,
            "_execute_browser_probe",
            side_effect=(
                (b"", b"", 0),
                (b"", b"", 0),
                (b"", b"", 0),
                runtime_smoke.RuntimeSmokeError(
                    "post-incognito storage smoke timed out without ABSENT beacon"
                ),
            ),
        ), self.assertRaisesRegex(
            runtime_smoke.RuntimeSmokeError, "without ABSENT beacon"
        ):
            runtime_smoke._run_browser(
                self.executable,
                "arm64",
                profile,
                self.root,
                probe_server,
                17,
                {"PATH": runtime_smoke.SYSTEM_PATH},
            )

    def test_process_group_cleanup_interrupts_then_kills(self):
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

        with mock.patch.object(
            runtime_smoke, "_signal_group", side_effect=signal_group
        ):
            runtime_smoke._clean_process_group(Process())
        self.assertEqual(
            [(34567, signal.SIGINT), (34567, signal.SIGKILL)], signals
        )

    def test_browser_stdout_marker_cannot_replace_loopback_beacon(self):
        real_popen = subprocess.Popen
        processes = []

        def marker_process(_command, **kwargs):
            process = real_popen(
                [
                    sys.executable,
                    "-c",
                    "import os, time; "
                    "os.write(1, b'EXPECTED_BEACON'); time.sleep(30)",
                ],
                **kwargs,
            )
            processes.append(process)
            return process

        probe_server = mock.Mock()
        probe_server.result_ready.return_value = False
        with mock.patch.object(
            runtime_smoke.subprocess, "Popen", side_effect=marker_process
        ), self.assertRaisesRegex(runtime_smoke.RuntimeSmokeError, "timed out"):
            runtime_smoke._execute_browser_probe(
                ["/mock/browser"],
                b"EXPECTED_BEACON",
                probe_server,
                0.2,
                {"PATH": runtime_smoke.SYSTEM_PATH},
                "marker-only browser",
            )
        probe_server.prepare_result.assert_called_once_with(b"EXPECTED_BEACON")
        probe_server.cancel_result.assert_called_once_with(b"EXPECTED_BEACON")
        self.assertEqual(1, len(processes))
        self.assertIsNotNone(processes[0].poll())

    def test_browser_probe_requires_exact_beacon_and_new_session(self):
        real_popen = subprocess.Popen
        captured = []
        expected = b"WRITE_OK_EXACT_BROWSER_BEACON"

        def popen(command, **kwargs):
            captured.append({"command": list(command), "kwargs": dict(kwargs)})
            return real_popen(command, **kwargs)

        with runtime_smoke._LoopbackProbeServer() as probe_server:
            parsed = runtime_smoke.urllib.parse.urlsplit(probe_server.url)
            script = (
                "import http.client, signal, sys, time\n"
                "signal.signal(signal.SIGINT, lambda *_args: sys.exit(0))\n"
                "connection = http.client.HTTPConnection({!r}, {}, timeout=3)\n"
                "connection.request('GET', {!r})\n"
                "probe_response = connection.getresponse()\n"
                "probe_response.read()\n"
                "sys.exit(3) if probe_response.status != 200 else None\n"
                "connection.close()\n"
                "connection = http.client.HTTPConnection({!r}, {}, timeout=3)\n"
                "connection.request('POST', {!r}, {!r}, {{"
                "'Content-Type': 'text/plain;charset=US-ASCII', "
                "'Origin': {!r}}})\n"
                "response = connection.getresponse()\n"
                "response.read()\n"
                "connection.close()\n"
                "sys.exit(4) if response.status != 204 else time.sleep(30)\n"
            ).format(
                parsed.hostname,
                parsed.port,
                parsed.path,
                parsed.hostname,
                parsed.port,
                probe_server._server.result_path,
                expected,
                probe_server._server.expected_origin,
            )
            with mock.patch.object(
                runtime_smoke.subprocess, "Popen", side_effect=popen
            ):
                stdout, stderr, returncode = runtime_smoke._execute_browser_probe(
                    [sys.executable, "-c", script],
                    expected,
                    probe_server,
                    5,
                    {"PATH": runtime_smoke.SYSTEM_PATH},
                    "exact browser beacon",
                )
        self.assertEqual((b"", b"", 0), (stdout, stderr, returncode))
        self.assertEqual(1, len(captured))
        self.assertTrue(captured[0]["kwargs"]["start_new_session"])
        self.assertFalse(captured[0]["kwargs"]["shell"])

    def test_browser_probe_accepts_expected_chromium_sigint_exit(self):
        expected = b"WRITE_OK_EXPECTED_CHROMIUM_SIGINT"
        stdout = tempfile.TemporaryFile()
        stderr = tempfile.TemporaryFile()
        process = SimpleNamespace(
            pid=54321,
            stdout=stdout,
            stderr=stderr,
            returncode=None,
            poll=lambda: None,
        )
        probe_server = mock.Mock()
        probe_server.result_ready.return_value = True

        def cleanup(observed_process):
            self.assertIs(process, observed_process)
            observed_process.returncode = 128 + signal.SIGINT

        with mock.patch.object(
            runtime_smoke.subprocess, "Popen", return_value=process
        ) as popen, mock.patch.object(
            runtime_smoke, "_clean_process_group", side_effect=cleanup
        ):
            observed = runtime_smoke._execute_browser_probe(
                ["/mock/browser"],
                expected,
                probe_server,
                5,
                {"PATH": runtime_smoke.SYSTEM_PATH},
                "Chromium SIGINT browser",
            )
        self.assertEqual((b"", b"", 128 + signal.SIGINT), observed)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        probe_server.prepare_result.assert_called_once_with(expected)
        probe_server.consume_result.assert_called_once_with(expected)
        probe_server.cancel_result.assert_not_called()

    def test_tool_capture_accepts_exact_cap_and_rejects_cap_plus_one(self):
        with mock.patch.object(runtime_smoke, "MAX_LOG_BYTES", 1024):
            stdout, stderr = runtime_smoke._run_capture(
                [
                    sys.executable,
                    "-c",
                    "import os; os.write(1, b'x' * 1024)",
                ],
                timeout_seconds=5,
            )
            self.assertEqual(1024, len(stdout))
            self.assertEqual(b"", stderr)
            with self.assertRaisesRegex(
                runtime_smoke.RuntimeSmokeError, "stdout exceeded"
            ):
                runtime_smoke._run_capture(
                    [
                        sys.executable,
                        "-c",
                        "import os, time; os.write(1, b'x' * 1025); time.sleep(30)",
                    ],
                    timeout_seconds=5,
                )

    def test_tool_capture_drains_stdout_and_stderr_concurrently(self):
        amount = 128 * 1024
        script = (
            "import os\n"
            "for fd, value in ((1, b'o'), (2, b'e')):\n"
            "    remaining = 131072\n"
            "    while remaining:\n"
            "        written = os.write(fd, value * min(4096, remaining))\n"
            "        remaining -= written\n"
        )
        with mock.patch.object(runtime_smoke, "MAX_LOG_BYTES", 256 * 1024):
            stdout, stderr = runtime_smoke._run_capture(
                [sys.executable, "-c", script], timeout_seconds=5
            )
        self.assertEqual(b"o" * amount, stdout)
        self.assertEqual(b"e" * amount, stderr)

    def test_tool_capture_does_not_wait_for_descendant_pipe_eof(self):
        script = (
            "import os, time\n"
            "if os.fork() == 0:\n"
            "    time.sleep(30)\n"
            "    os._exit(0)\n"
            "os.write(1, b'PRIMARY_EXITED')\n"
        )
        stdout, stderr = runtime_smoke._run_capture(
            [sys.executable, "-c", script], timeout_seconds=5
        )
        self.assertEqual(b"PRIMARY_EXITED", stdout)
        self.assertEqual(b"", stderr)

    def test_browser_overflow_is_bounded_and_cleans_process_group(self):
        real_popen = subprocess.Popen
        processes = []

        def noisy_process(_command, **kwargs):
            process = real_popen(
                [
                    sys.executable,
                    "-c",
                    "import os, time; os.write(2, b'x' * 1025); time.sleep(30)",
                ],
                **kwargs,
            )
            processes.append(process)
            return process

        probe_server = mock.Mock()
        probe_server.result_ready.return_value = False
        with mock.patch.object(
            runtime_smoke, "MAX_LOG_BYTES", 1024
        ), mock.patch.object(
            runtime_smoke.subprocess, "Popen", side_effect=noisy_process
        ), self.assertRaisesRegex(
            runtime_smoke.RuntimeSmokeError, "stderr exceeded"
        ):
            runtime_smoke._execute_browser_probe(
                ["/mock/browser"],
                b"EXPECTED_BEACON",
                probe_server,
                5,
                {"PATH": runtime_smoke.SYSTEM_PATH},
                "overflow browser",
            )
        self.assertEqual(1, len(processes))
        self.assertIsNotNone(processes[0].poll())

    def test_universal_runtime_requires_rosetta_and_fresh_profiles(self):
        profiles = []

        def browser(
            _exe, architecture, profile, _root, probe_server, timeout, _environment
        ):
            self.assertEqual(23, timeout)
            self.assertRegex(
                probe_server.url,
                r"^http://127\.0\.0\.1:[0-9]+/focus-runtime-probe-[0-9a-f]{48}\.html$",
            )
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

    def test_loopback_probe_server_rejects_every_other_path(self):
        with runtime_smoke._LoopbackProbeServer() as server:
            parsed = runtime_smoke.urllib.parse.urlsplit(server.url)
            expected = b"WRITE_OK_EXACT_LOOPBACK_RESULT"
            server.prepare_result(expected)
            headers = {
                "Content-Type": "text/plain;charset=US-ASCII",
                "Origin": server._server.expected_origin,
            }

            connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=5
            )
            connection.request(
                "POST", server._server.result_path, expected, headers
            )
            response = connection.getresponse()
            self.assertEqual(409, response.status)
            self.assertEqual(b"", response.read())
            connection.close()
            self.assertFalse(server.result_ready(expected))

            connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=5
            )
            connection.request("GET", parsed.path)
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            payload = response.read()
            self.assertNotIn(b"__FOCUS_RESULT_PATH__", payload)
            self.assertIn(server._server.result_path.encode("ascii"), payload)
            connection.close()

            connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=5
            )
            connection.request("GET", "/not-the-probe.html")
            response = connection.getresponse()
            self.assertEqual(404, response.status)
            self.assertEqual(b"", response.read())
            connection.close()

            connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=5
            )
            connection.request("GET", parsed.path + "?not=exact")
            response = connection.getresponse()
            self.assertEqual(404, response.status)
            self.assertEqual(b"", response.read())
            connection.close()

            missing_origin_headers = {
                "Content-Type": "text/plain;charset=US-ASCII"
            }
            connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=5
            )
            connection.request(
                "POST",
                server._server.result_path,
                expected,
                missing_origin_headers,
            )
            response = connection.getresponse()
            self.assertEqual(404, response.status)
            self.assertEqual(b"", response.read())
            connection.close()
            self.assertFalse(server.result_ready(expected))

            connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=5
            )
            connection.request(
                "POST", server._server.result_path, b"WRONG_RESULT", headers
            )
            response = connection.getresponse()
            self.assertEqual(409, response.status)
            self.assertEqual(b"", response.read())
            connection.close()
            self.assertFalse(server.result_ready(expected))

            connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=5
            )
            connection.request(
                "POST", server._server.result_path, expected, headers
            )
            response = connection.getresponse()
            self.assertEqual(204, response.status)
            self.assertEqual(b"", response.read())
            connection.close()
            self.assertTrue(server.result_ready(expected))
            server.consume_result(expected)

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
        sparkle_root = self.root / "sparkle-source"
        sparkle_root.mkdir()
        commands = []
        attach_inputs = []
        mounted = False
        mountpoint = None

        def run(command, *_args, **kwargs):
            nonlocal mounted, mountpoint
            commands.append(list(command))
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                attach_input = Path(command[-1])
                attach_inputs.append(
                    {
                        "path": attach_input,
                        "same_inode": os.path.samestat(
                            os.stat(attach_input, follow_symlinks=False),
                            os.stat(dmg, follow_symlinks=False),
                        ),
                        "root_mode": stat.S_IMODE(mountpoint.parent.stat().st_mode),
                        "pass_fds": tuple(kwargs.get("pass_fds", ())),
                    }
                )
                (mountpoint / runtime_smoke.APP_NAME).mkdir()
                os.symlink("/Applications", str(mountpoint / "Applications"))
                mounted = True
            elif command[1] == "detach":
                shutil.rmtree(mountpoint / runtime_smoke.APP_NAME)
                (mountpoint / "Applications").unlink()
                mounted = False
            return b"", b""

        statvfs = SimpleNamespace(f_flag=os.ST_RDONLY)
        with mock.patch.object(runtime_smoke, "_run_capture", side_effect=run), mock.patch.object(
            runtime_smoke.os.path, "ismount", side_effect=lambda _path: mounted
        ), mock.patch.object(
            runtime_smoke.os, "statvfs", return_value=statvfs
        ), mock.patch.object(
            runtime_smoke,
            "validate_universal_app_runtime",
            return_value={"passed": True},
        ) as runtime:
            report = runtime_smoke.validate_mounted_dmg_runtime(
                dmg,
                update_mode="autoupdate",
                sparkle_source_root=sparkle_root,
            )
        self.assertTrue(report["mounted_read_only"])
        self.assertTrue(report["descriptor_pinned"])
        self.assertTrue(report["passed"])
        self.assertIn("-readonly", commands[0])
        self.assertNotIn("/dev/fd/", commands[0][-1])
        self.assertRegex(
            Path(commands[0][-1]).name,
            r"^\.focus-runtime-input-[0-9a-f]{48}\.dmg$",
        )
        self.assertEqual(1, len(attach_inputs))
        self.assertTrue(attach_inputs[0]["same_inode"])
        self.assertEqual(0o700, attach_inputs[0]["root_mode"])
        self.assertEqual((), attach_inputs[0]["pass_fds"])
        self.assertFalse(attach_inputs[0]["path"].exists())
        self.assertEqual("detach", commands[-1][1])
        runtime.assert_called_once_with(
            mock.ANY,
            timeout_seconds=runtime_smoke.DEFAULT_TIMEOUT_SECONDS,
            update_mode="autoupdate",
            sparkle_source_root=sparkle_root,
        )

    def test_final_dmg_uses_verified_private_copy_when_hardlink_is_impossible(self):
        dmg = self.root / "FocusBrowser.dmg"
        dmg.write_bytes(b"mock dmg")
        mounted = False
        mountpoint = None
        attach_input = None
        input_contract = None

        def run(command, *_args, **kwargs):
            nonlocal mounted, mountpoint, attach_input, input_contract
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                attach_input = Path(command[-1])
                observed = attach_input.stat()
                input_contract = {
                    "bytes": attach_input.read_bytes(),
                    "mode": stat.S_IMODE(observed.st_mode),
                    "same_inode": os.path.samestat(observed, dmg.stat()),
                    "pass_fds": tuple(kwargs.get("pass_fds", ())),
                }
                (mountpoint / runtime_smoke.APP_NAME).mkdir()
                os.symlink("/Applications", str(mountpoint / "Applications"))
                mounted = True
            elif command[1] == "detach":
                shutil.rmtree(mountpoint / runtime_smoke.APP_NAME)
                (mountpoint / "Applications").unlink()
                mounted = False
            return b"", b""

        with mock.patch.object(
            runtime_smoke.os,
            "link",
            side_effect=OSError(errno.EXDEV, "cross-device fixture"),
        ), mock.patch.object(
            runtime_smoke, "_run_capture", side_effect=run
        ), mock.patch.object(
            runtime_smoke.os.path,
            "ismount",
            side_effect=lambda _path: mounted,
        ), mock.patch.object(
            runtime_smoke.os,
            "statvfs",
            return_value=SimpleNamespace(f_flag=os.ST_RDONLY),
        ), mock.patch.object(
            runtime_smoke,
            "validate_universal_app_runtime",
            return_value={"passed": True},
        ):
            report = runtime_smoke.validate_mounted_dmg_runtime(dmg)

        self.assertTrue(report["passed"])
        self.assertEqual(b"mock dmg", input_contract["bytes"])
        self.assertEqual(0o400, input_contract["mode"])
        self.assertFalse(input_contract["same_inode"])
        self.assertEqual((), input_contract["pass_fds"])
        self.assertFalse(attach_input.exists())
        self.assertFalse(attach_input.parent.exists())

    def test_final_dmg_runtime_failure_still_detaches(self):
        dmg = self.root / "FocusBrowser.dmg"
        dmg.write_bytes(b"mock dmg")
        commands = []
        mounted = False
        mountpoint = None

        def run(command, *_args, **_kwargs):
            nonlocal mounted, mountpoint
            commands.append(list(command))
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                (mountpoint / runtime_smoke.APP_NAME).mkdir()
                os.symlink("/Applications", str(mountpoint / "Applications"))
                mounted = True
            elif command[1] == "detach":
                shutil.rmtree(mountpoint / runtime_smoke.APP_NAME)
                (mountpoint / "Applications").unlink()
                mounted = False
            return b"", b""

        with mock.patch.object(runtime_smoke, "_run_capture", side_effect=run), mock.patch.object(
            runtime_smoke.os.path, "ismount", side_effect=lambda _path: mounted
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

    def test_successful_attach_with_missing_mount_still_attempts_detach(self):
        dmg = self.root / "FocusBrowser.dmg"
        dmg.write_bytes(b"mock dmg")
        commands = []
        temporary_root = None

        def run(command, *_args, **_kwargs):
            nonlocal temporary_root
            commands.append(list(command))
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                temporary_root = mountpoint.parent
            return b"", b""

        with mock.patch.object(
            runtime_smoke, "_run_capture", side_effect=run
        ), mock.patch.object(
            runtime_smoke.os.path, "ismount", return_value=False
        ), self.assertRaisesRegex(
            runtime_smoke.RuntimeSmokeError, "did not mount"
        ):
            runtime_smoke.validate_mounted_dmg_runtime(dmg)

        self.assertEqual(["attach", "detach"], [command[1] for command in commands])
        self.assertIsNotNone(temporary_root)
        self.assertFalse(temporary_root.exists())

    def test_attach_error_after_mount_still_detaches(self):
        dmg = self.root / "FocusBrowser.dmg"
        dmg.write_bytes(b"mock dmg")
        commands = []
        mounted = False
        mountpoint = None

        def run(command, *_args, **_kwargs):
            nonlocal mounted, mountpoint
            commands.append(list(command))
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                mounted = True
                raise runtime_smoke.RuntimeSmokeError(
                    "synthetic attach error after mount"
                )
            mounted = False
            return b"", b""

        with mock.patch.object(
            runtime_smoke, "_run_capture", side_effect=run
        ), mock.patch.object(
            runtime_smoke.os.path,
            "ismount",
            side_effect=lambda _path: mounted,
        ), self.assertRaisesRegex(
            runtime_smoke.RuntimeSmokeError, "attach error after mount"
        ):
            runtime_smoke.validate_mounted_dmg_runtime(dmg)

        self.assertEqual(["attach", "detach"], [command[1] for command in commands])
        self.assertFalse(mountpoint.parent.exists())

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
            runtime_smoke.DmgDetachError, "could not prove final DMG detached"
        ) as raised:
            runtime_smoke.validate_mounted_dmg_runtime(dmg)

        self.assertEqual("detach", commands[-2][1])
        self.assertEqual(["detach", "-force"], commands[-1][1:3])
        retained_root = Path(raised.exception.retained_root)
        self.assertTrue(retained_root.is_dir())
        self.assertEqual(0o700, stat.S_IMODE(retained_root.stat().st_mode))
        self.assertTrue(Path(raised.exception.mountpoint).is_dir())
        retained_inputs = list(retained_root.glob(".focus-runtime-input-*.dmg"))
        self.assertEqual(1, len(retained_inputs))
        self.assertTrue(
            os.path.samestat(retained_inputs[0].stat(), dmg.stat())
        )
        shutil.rmtree(retained_root)

    def test_final_dmg_never_unlinks_replaced_private_input(self):
        dmg = self.root / "FocusBrowser.dmg"
        dmg.write_bytes(b"mock dmg")
        mounted = False
        mountpoint = None
        attach_input = None

        def run(command, *_args, **_kwargs):
            nonlocal mounted, mountpoint, attach_input
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                attach_input = Path(command[-1])
                (mountpoint / runtime_smoke.APP_NAME).mkdir()
                os.symlink("/Applications", str(mountpoint / "Applications"))
                mounted = True
            elif command[1] == "detach":
                shutil.rmtree(mountpoint / runtime_smoke.APP_NAME)
                (mountpoint / "Applications").unlink()
                mounted = False
            return b"", b""

        def replace_private_input(_app, **_kwargs):
            attach_input.unlink()
            attach_input.write_bytes(b"foreign replacement")
            return {"passed": True}

        with mock.patch.object(
            runtime_smoke, "_run_capture", side_effect=run
        ), mock.patch.object(
            runtime_smoke.os.path,
            "ismount",
            side_effect=lambda _path: mounted,
        ), mock.patch.object(
            runtime_smoke.os,
            "statvfs",
            return_value=SimpleNamespace(f_flag=os.ST_RDONLY),
        ), mock.patch.object(
            runtime_smoke,
            "validate_universal_app_runtime",
            side_effect=replace_private_input,
        ), self.assertRaisesRegex(
            runtime_smoke.RuntimeSmokeError,
            "refusing to unlink replaced DMG private mount input",
        ):
            runtime_smoke.validate_mounted_dmg_runtime(dmg)

        self.assertEqual(b"foreign replacement", attach_input.read_bytes())
        self.assertTrue(attach_input.parent.is_dir())
        attach_input.unlink()
        mountpoint.rmdir()
        attach_input.parent.rmdir()

    def test_final_dmg_rejects_same_size_in_place_mutation(self):
        dmg = self.root / "FocusBrowser.dmg"
        dmg.write_bytes(b"mock dmg")
        mounted = False
        mountpoint = None

        def run(command, *_args, **_kwargs):
            nonlocal mounted, mountpoint
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                (mountpoint / runtime_smoke.APP_NAME).mkdir()
                os.symlink("/Applications", str(mountpoint / "Applications"))
                mounted = True
            elif command[1] == "detach":
                shutil.rmtree(mountpoint / runtime_smoke.APP_NAME)
                (mountpoint / "Applications").unlink()
                mounted = False
            return b"", b""

        def mutate(_app, **_kwargs):
            dmg.write_bytes(b"evil dmg")
            return {"passed": True}

        with mock.patch.object(
            runtime_smoke, "_run_capture", side_effect=run
        ), mock.patch.object(
            runtime_smoke.os.path, "ismount", side_effect=lambda _path: mounted
        ), mock.patch.object(
            runtime_smoke.os,
            "statvfs",
            return_value=SimpleNamespace(f_flag=os.ST_RDONLY),
        ), mock.patch.object(
            runtime_smoke,
            "validate_universal_app_runtime",
            side_effect=mutate,
        ), self.assertRaisesRegex(
            runtime_smoke.RuntimeSmokeError, "changed"
        ):
            runtime_smoke.validate_mounted_dmg_runtime(dmg)


if __name__ == "__main__":
    unittest.main()
