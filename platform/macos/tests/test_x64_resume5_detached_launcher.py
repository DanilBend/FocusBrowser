#!/usr/bin/env python3
"""Focused tests for the fixed resume5 daemon launcher."""

import json
import os
import stat
import sys
import tempfile
import types
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

    def test_process_identity_uses_darwin_kernel_session_and_real_executable(self):
        child_pid = 2468
        session_pgid = 1357
        actual_python = str(Path("/bin/sh").resolve(strict=True))
        command = "{} {}".format(
            actual_python, " ".join(launcher.ARGUMENTS[1:])
        )
        with mock.patch.object(
            launcher.os, "getpgid", return_value=session_pgid
        ), mock.patch.object(
            launcher.os, "getsid", return_value=session_pgid
        ), mock.patch.object(
            launcher.subprocess,
            "check_output",
            return_value="1 {} Thu Jul 31 01:02:03 2026 {}\n".format(
                session_pgid, command
            ),
        ), mock.patch.object(
            launcher, "_proc_pidpath", return_value=actual_python
        ):
            identity = launcher._process_identity(
                child_pid, session_pgid, actual_python
            )
        self.assertEqual(session_pgid, identity["sid"])
        self.assertEqual(actual_python, identity["executable"])
        self.assertEqual("Thu Jul 31 01:02:03 2026", identity["started"])

    def test_process_identity_rejects_wrong_runner_argv(self):
        actual_python = str(Path("/bin/sh").resolve(strict=True))
        with mock.patch.object(
            launcher.os, "getpgid", return_value=1357
        ), mock.patch.object(
            launcher.os, "getsid", return_value=1357
        ), mock.patch.object(
            launcher.subprocess,
            "check_output",
            return_value=(
                "1 1357 Thu Jul 31 01:02:03 2026 {} -c evil {}\n".format(
                    actual_python, " ".join(launcher.ARGUMENTS[1:])
                )
            ),
        ), mock.patch.object(
            launcher, "_proc_pidpath", return_value=actual_python
        ):
            with self.assertRaisesRegex(launcher.LaunchError, "argv or ancestry"):
                launcher._process_identity(2468, 1357, actual_python)

    def test_system_python_probe_reports_a_root_owned_final_image(self):
        image = Path(launcher._probe_system_python_image())
        info = launcher._regular_system(image, "probed Python image")
        self.assertTrue(image.is_absolute())
        self.assertGreater(info.st_size, 0)

    def test_handshake_token_rejects_suffix_smuggling(self):
        expected = str(Path("/bin/sh").resolve(strict=True))
        row = {
            "pid": 2468,
            "ppid": 1,
            "pgid": 1357,
            "command": "{} -c evil {}".format(
                expected, " ".join(launcher.ARGUMENTS[1:])
            ),
        }
        with mock.patch.object(
            launcher, "_process_rows", return_value=[row]
        ), mock.patch.object(
            launcher.os, "getpgrp", return_value=999
        ):
            with self.assertRaisesRegex(launcher.LaunchError, "no longer identifies"):
                launcher._capture_handshake_runner_token(2468, 1357, expected)

    def test_kernel_token_rejects_microsecond_start_mismatch(self):
        token = (2468, 100, 200, "/bin/sh", 1357)
        info = types.SimpleNamespace(
            pbi_pid=2468,
            pbi_pgid=1357,
            pbi_start_tvsec=100,
            pbi_start_tvusec=201,
        )
        with mock.patch.object(
            launcher, "_proc_bsd_info", return_value=info
        ), mock.patch.object(
            launcher, "_proc_pidpath", return_value="/bin/sh"
        ):
            self.assertFalse(launcher._token_still_live(token))

    def test_owned_groups_include_separately_sessioned_build_descendants(self):
        rows = [
            {"pid": 20, "ppid": 1, "pgid": 10, "command": "runner"},
            {"pid": 30, "ppid": 20, "pgid": 30, "command": "shell"},
            {"pid": 31, "ppid": 30, "pgid": 30, "command": "ninja"},
        ]
        runner_token = (20, 1, 2, "/runner", 10)

        def capture(row):
            return (row["pid"], 1, row["pid"], "/proc/{}".format(row["pid"]), row["pgid"])

        with mock.patch.object(
            launcher, "_token_still_live", return_value=True
        ), mock.patch.object(
            launcher, "_capture_process_token", side_effect=capture
        ), mock.patch.object(
            launcher.os, "getpgrp", return_value=999
        ):
            groups, live = launcher._owned_process_groups(rows, {runner_token})
        self.assertEqual({10, 30}, groups)
        self.assertEqual({20, 30, 31}, {token[0] for token in live})

    def test_terminate_allows_controlled_cleanup_and_proves_child_group_absent(self):
        token = (20, 1, 2, "/runner", 10)
        observations = (
            {
                "absent": False,
                "groups": {10, 30},
                "present_groups": {10, 30},
                "unproven_groups": set(),
                "live": [token],
            },
            {
                "absent": True,
                "groups": set(),
                "present_groups": set(),
                "unproven_groups": set(),
                "live": [],
            },
        )
        with mock.patch.object(
            launcher, "_process_rows", return_value=[]
        ), mock.patch.object(
            launcher, "_owned_process_groups", return_value=({10, 30}, [token])
        ), mock.patch.object(
            launcher, "_termination_observation", side_effect=observations
        ), mock.patch.object(
            launcher.time, "monotonic", side_effect=(0, 1, 2)
        ), mock.patch.object(
            launcher.time, "sleep"
        ), mock.patch.object(
            launcher, "_signal_process_group"
        ) as signal_group, mock.patch.object(
            launcher.os, "getpgrp", return_value=999
        ):
            self.assertTrue(launcher._settle_owned_processes(10, {token}))
        signal_group.assert_called_once_with(10, launcher.signal.SIGTERM)

    def test_terminate_kills_every_known_group_after_cleanup_timeout(self):
        token = (20, 1, 2, "/runner", 10)
        observations = (
            {
                "absent": False,
                "groups": {10, 30},
                "present_groups": {10, 30},
                "unproven_groups": set(),
                "live": [token],
            },
            {
                "absent": True,
                "groups": set(),
                "present_groups": set(),
                "unproven_groups": set(),
                "live": [],
            },
        )
        with mock.patch.object(
            launcher, "_process_rows", return_value=[]
        ), mock.patch.object(
            launcher, "_owned_process_groups", return_value=({10, 30}, [token])
        ), mock.patch.object(
            launcher, "_termination_observation", side_effect=observations
        ), mock.patch.object(
            launcher.time, "monotonic", side_effect=(0, 76, 100, 101)
        ), mock.patch.object(
            launcher.time, "sleep"
        ), mock.patch.object(
            launcher, "_signal_process_group"
        ) as signal_group, mock.patch.object(
            launcher.os, "getpgrp", return_value=999
        ):
            self.assertTrue(launcher._settle_owned_processes(10, {token}))
        self.assertEqual(
            [
                mock.call(10, launcher.signal.SIGTERM),
                mock.call(30, launcher.signal.SIGKILL),
                mock.call(10, launcher.signal.SIGKILL),
            ],
            signal_group.call_args_list,
        )

    def test_absence_proof_refuses_to_kill_stale_unverified_group(self):
        token = (20, 1, 2, "/runner", 10)
        observation = {
            "absent": False,
            "groups": set(),
            "present_groups": {10},
            "unproven_groups": {10},
            "live": [],
        }
        with mock.patch.object(
            launcher, "_process_rows", return_value=[]
        ), mock.patch.object(
            launcher, "_owned_process_groups", return_value=({10}, [token])
        ), mock.patch.object(
            launcher, "_termination_observation", return_value=observation
        ), mock.patch.object(
            launcher.time, "monotonic", side_effect=(0, 76, 100, 121)
        ), mock.patch.object(
            launcher.time, "sleep"
        ), mock.patch.object(
            launcher, "_signal_process_group"
        ) as signal_group, mock.patch.object(
            launcher.os, "getpgrp", return_value=999
        ):
            with self.assertRaisesRegex(launcher.LaunchError, "could not be terminated"):
                launcher._settle_owned_processes(10, {token})
        signal_group.assert_called_once_with(10, launcher.signal.SIGTERM)

    def test_spawn_handshake_failure_terminates_exact_new_session(self):
        with mock.patch.object(
            launcher.os, "pipe", return_value=(20, 21)
        ), mock.patch.object(
            launcher.os, "set_inheritable"
        ), mock.patch.object(
            launcher.os, "fork", return_value=4242
        ), mock.patch.object(
            launcher, "_read_exec_handshake", side_effect=launcher.LaunchError("bad")
        ), mock.patch.object(
            launcher, "_capture_new_session_ownership", return_value={"token"}
        ) as capture, mock.patch.object(
            launcher, "_terminate_unreaped_first_child"
        ) as terminate_child, mock.patch.object(
            launcher, "_terminate_new_session_after_handshake_failure"
        ) as terminate_session, mock.patch.object(
            launcher.os, "close"
        ):
            with self.assertRaisesRegex(launcher.LaunchError, "bad"):
                launcher._spawn_detached(10, 11, {}, "/bin/sh")
        capture.assert_called_once_with(4242)
        terminate_child.assert_called_once_with(4242)
        terminate_session.assert_called_once_with(4242, {"token"})

    def test_spawn_capture_failure_still_reaps_owned_first_child(self):
        with mock.patch.object(
            launcher.os, "pipe", return_value=(20, 21)
        ), mock.patch.object(
            launcher.os, "set_inheritable"
        ), mock.patch.object(
            launcher.os, "fork", return_value=4242
        ), mock.patch.object(
            launcher, "_read_exec_handshake", side_effect=launcher.LaunchError("bad")
        ), mock.patch.object(
            launcher,
            "_capture_new_session_ownership",
            side_effect=launcher.LaunchError("capture failed"),
        ), mock.patch.object(
            launcher, "_terminate_unreaped_first_child"
        ) as terminate_child, mock.patch.object(
            launcher.os, "close"
        ):
            with self.assertRaisesRegex(launcher.LaunchError, "capture failed"):
                launcher._spawn_detached(10, 11, {}, "/bin/sh")
        terminate_child.assert_called_once_with(4242)

    def test_unreaped_prefork_child_allows_direct_term_only_while_owned(self):
        with mock.patch.object(
            launcher.os, "waitpid", side_effect=((0, 0), (4242, 0))
        ), mock.patch.object(
            launcher.os, "getpgid", return_value=999
        ), mock.patch.object(
            launcher.os, "kill"
        ) as direct_signal, mock.patch.object(
            launcher.time, "monotonic", side_effect=(0, 1)
        ), mock.patch.object(
            launcher.time, "sleep"
        ):
            launcher._terminate_unreaped_first_child(4242)
        direct_signal.assert_called_once_with(4242, launcher.signal.SIGTERM)

    def test_stderr_reservation_failure_rolls_back_exact_stdout(self):
        preflight = {"execution_spine": {}}
        stdout_stat = types.SimpleNamespace(
            st_dev=1, st_ino=2, st_uid=os.getuid(), st_size=0
        )
        with mock.patch.object(
            launcher, "_fixed_preflight", return_value=preflight
        ), mock.patch.object(
            launcher, "_open_controller_log", side_effect=(10, OSError("denied"))
        ), mock.patch.object(
            launcher.os, "fstat", return_value=stdout_stat
        ), mock.patch.object(
            launcher.os, "close"
        ) as close, mock.patch.object(
            launcher, "_unlink_exact_empty"
        ) as rollback:
            with self.assertRaisesRegex(OSError, "denied"):
                launcher.launch(True, True)
        close.assert_called_once_with(10)
        rollback.assert_called_once_with(
            launcher.STDOUT_LOG,
            (1, 2, os.getuid(), 0),
        )

    def test_child_exec_path_rechecks_spine_and_closes_inherited_fds(self):
        source = Path(launcher.__file__).read_text()
        self.assertIn("_execution_spine_still_exact(execution_spine)", source)
        self.assertIn("os.closerange(3, write_fd)", source)
        self.assertIn("os.closerange(write_fd + 1, maximum_fd)", source)
        self.assertIn("os.set_inheritable(write_fd, False)", source)

    def test_launch_publishes_exact_detached_controller_contract(self):
        preflight = {
            "runner": {"path": "runner", "bytes": 1, "sha256": "a" * 64},
            "run_id": launcher.STEM,
            "repository_head": "d" * 40,
            "execution_spine": {"runner": {"bytes": 1, "sha256": "a" * 64}},
            "python": {"image": str(Path("/bin/sh").resolve())},
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
            launcher,
            "_spawn_detached",
            return_value=(2468, 1357, (2468, 1, 2, "/bin/sh", 1357)),
        ), mock.patch.object(
            launcher.time, "sleep"
        ), mock.patch.object(
            launcher, "_token_still_live", return_value=True
        ), mock.patch.object(
            launcher.os,
            "fstat",
            side_effect=(
                types.SimpleNamespace(st_dev=1, st_ino=2, st_uid=os.getuid(), st_size=0),
                types.SimpleNamespace(st_dev=1, st_ino=3, st_uid=os.getuid(), st_size=0),
            ),
        ), mock.patch.object(
            launcher,
            "_process_identity",
            return_value={"pid": 2468, "pgid": 1357},
        ), mock.patch.object(
            launcher, "_atomic_json", side_effect=capture
        ):
            result = launcher.launch(True, True)

        self.assertTrue(result["launched"])
        self.assertEqual(2468, result["child_pid"])
        self.assertEqual(1357, result["session_pgid"])
        self.assertEqual(2, len(publications))
        intent_path, intent = publications[0]
        self.assertEqual(launcher.CONTROLLER_INTENT, intent_path)
        self.assertTrue(intent["one_shot"])
        path, receipt = publications[1]
        self.assertEqual(launcher.CONTROLLER_RECEIPT, path)
        self.assertTrue(receipt["double_fork"])
        self.assertTrue(receipt["setsid"])
        self.assertTrue(receipt["stdio_detached"])
        self.assertEqual("cloexec-success", receipt["exec_handshake"])
        self.assertEqual(list(launcher.ARGUMENTS), receipt["arguments"])
        self.assertEqual("d" * 40, receipt["repository_head"])
        self.assertEqual(1357, receipt["session_pgid"])
        self.assertEqual(preflight, receipt["preflight"])


if __name__ == "__main__":
    unittest.main()
