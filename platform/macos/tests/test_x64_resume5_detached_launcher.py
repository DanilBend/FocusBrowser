#!/usr/bin/env python3
"""Focused tests for the fixed resume5 daemon launcher."""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import x64_resume5_detached_launcher as launcher


class DetachedLauncherTests(unittest.TestCase):
    def test_command_is_fixed_offline_j6_resume5(self):
        self.assertEqual("/usr/bin/python3", launcher.ARGUMENTS[0])
        self.assertEqual(str(launcher.RUNNER), launcher.ARGUMENTS[1])
        self.assertEqual("run", launcher.ARGUMENTS[2])
        self.assertIn("--execute", launcher.ARGUMENTS)
        self.assertIn("--confirm-official-resume5", launcher.ARGUMENTS)
        joined = " ".join(launcher.ARGUMENTS)
        self.assertNotIn("gn gen", joined)
        self.assertNotIn("http://", joined)
        self.assertNotIn("https://", joined)
        self.assertEqual(
            "build-x64-resume5-detached-20260731T000500MSK",
            launcher.STEM,
        )

    def test_live_launch_requires_both_confirmations(self):
        for execute, confirm in ((False, False), (True, False), (False, True)):
            with self.assertRaisesRegex(launcher.LaunchError, "both confirmations"):
                launcher.launch(execute, confirm)

    def test_atomic_controller_receipt_is_read_only_and_no_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "controller.json"
            value = {"schema": 1, "pid": 123}
            publication = launcher._atomic_json(path, value)
            expected = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
            self.assertEqual(expected, path.read_bytes())
            self.assertEqual(len(expected), publication["bytes"])
            self.assertEqual(0, stat.S_IMODE(path.stat().st_mode) & 0o222)
            with self.assertRaisesRegex(launcher.LaunchError, "already exists"):
                launcher._atomic_json(path, value)

    def test_exec_handshake_accepts_one_pid_and_rejects_error(self):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"PID 4321\n")
        os.close(write_fd)
        try:
            self.assertEqual(4321, launcher._read_exec_handshake(read_fd, 1))
        finally:
            os.close(read_fd)

        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"PID 4321\nERROR exec=denied\n")
        os.close(write_fd)
        try:
            with self.assertRaisesRegex(launcher.LaunchError, "exec=denied"):
                launcher._read_exec_handshake(read_fd, 1)
        finally:
            os.close(read_fd)

    def test_launch_publishes_exact_detached_controller_contract(self):
        preflight = {
            "runner": {"path": "runner", "bytes": 1, "sha256": "a" * 64},
            "run_id": launcher.STEM,
            "repository_head": "d" * 40,
        }
        publications = []

        def capture(path, value):
            publications.append((path, value))
            return {"path": str(path), "bytes": 10, "sha256": "b" * 64}

        with mock.patch.object(
            launcher, "_fixed_preflight", return_value=preflight
        ), mock.patch.object(
            launcher, "_open_controller_log", side_effect=(10, 11)
        ), mock.patch.object(
            launcher, "_spawn_detached", return_value=2468
        ), mock.patch.object(
            launcher.time, "sleep"
        ), mock.patch.object(
            launcher.os, "kill"
        ) as kill, mock.patch.object(
            launcher, "_atomic_json", side_effect=capture
        ):
            result = launcher.launch(True, True)

        kill.assert_called_once_with(2468, 0)
        self.assertTrue(result["launched"])
        self.assertEqual(2468, result["child_pid"])
        self.assertEqual(1, len(publications))
        path, receipt = publications[0]
        self.assertEqual(launcher.CONTROLLER_RECEIPT, path)
        self.assertTrue(receipt["double_fork"])
        self.assertTrue(receipt["setsid"])
        self.assertTrue(receipt["stdio_detached"])
        self.assertEqual("cloexec-success", receipt["exec_handshake"])
        self.assertEqual(list(launcher.ARGUMENTS), receipt["arguments"])
        self.assertEqual("d" * 40, receipt["repository_head"])
        self.assertEqual(preflight, receipt["preflight"])


if __name__ == "__main__":
    unittest.main()
