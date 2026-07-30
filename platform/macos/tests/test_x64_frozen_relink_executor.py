#!/usr/bin/env python3
"""Tests for the transactional frozen x86_64 relink executor."""

import copy
import fcntl
import hashlib
import json
import os
import shutil
import signal
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import x64_frozen_relink
import x64_frozen_relink_executor


class FakeProcess:
    def __init__(self, pid, returncode, polls=None):
        self.pid = pid
        self.returncode = returncode
        self.polls = list(polls or [returncode])

    def poll(self):
        if self.polls:
            return self.polls.pop(0)
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = -signal.SIGTERM

    def kill(self):
        self.returncode = -signal.SIGKILL


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "checkout/src"
        self.out = self.source / x64_frozen_relink.X64_OUT_SOURCE_RELATIVE
        self.out.mkdir(parents=True)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir(mode=0o700)
        self.developer = self.root / "Xcode.app/Contents/Developer"
        self.developer.mkdir(parents=True)
        self.ninja = self.source / x64_frozen_relink.PINNED_NINJA_SOURCE_RELATIVE
        self.ninja.parent.mkdir(parents=True)
        self.ninja.write_bytes(b"fake pinned Ninja\n")
        self.ninja.chmod(0o755)
        self.run_id = "unit-20260730"

        for index, relative in enumerate(x64_frozen_relink.FROZEN_OUTPUTS):
            path = self.out / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                "old-output-{}-{}\n".format(index, relative).encode("utf-8")
            )
        self.pre_output_bytes = {
            relative: (self.out / relative).read_bytes()
            for relative in x64_frozen_relink.FROZEN_OUTPUTS
        }
        self.ninja_log = self.out / ".ninja_log"
        self.ninja_log.write_bytes(
            b"# ninja log v5\n1\t2\t3\told-output\tdeadbeef\n"
        )
        self.ninja_deps = self.out / ".ninja_deps"
        self.ninja_deps.write_bytes(b"# ninjadeps\x00old\n")
        self.pre_history = {
            ".ninja_log": self.ninja_log.read_bytes(),
            ".ninja_deps": self.ninja_deps.read_bytes(),
        }

        command = {
            "kind": x64_frozen_relink.PRIVATE_COMMAND_KIND,
            "working_directory_source_relative": x64_frozen_relink.X64_OUT_SOURCE_RELATIVE,
            "executable_source_relative": x64_frozen_relink.PINNED_NINJA_SOURCE_RELATIVE,
            "arguments": [
                "-f",
                "build.ninja",
                "-n",
                *x64_frozen_relink.FROZEN_TARGETS,
            ],
            "environment": {
                "NINJA_STATUS": x64_frozen_relink.PRIVATE_STATUS_PREFIX
                + "[%f/%t] "
            },
            "unset_environment": ["NINJA_SUMMARIZE_BUILD"],
            "expected_exit_code": 0,
            "stdout_parser": "focus-macos-x64-frozen-dry-run-v1",
        }
        self.planner = {
            "schema": 1,
            "kind": x64_frozen_relink.PRIVATE_PLAN_KIND,
            "dry_run_only": True,
            "closure_sha256": "a" * 64,
            "graph_binding": {"test": "binding"},
            "ninja_sha256": "b" * 64,
            "command": command,
            "targets": list(x64_frozen_relink.FROZEN_TARGETS),
            "outputs": list(x64_frozen_relink.FROZEN_OUTPUTS),
            "safety": {
                "planner_commands_executed": 0,
                "gn_invocations": 0,
                "ninja_invocations": 0,
                "network_operations": 0,
                "gn_regeneration_forbidden": True,
                "execution_supported": False,
                "structural_observation_is_not_execution_proof": True,
            },
            "plan_id": "c" * 64,
            "closure": {"entries": []},
            "ninja": {
                "path": x64_frozen_relink.PINNED_NINJA_SOURCE_RELATIVE,
                "bytes": self.ninja.stat().st_size,
                "sha256": hashlib.sha256(self.ninja.read_bytes()).hexdigest(),
                "mode": "0755",
            },
        }
        self.alias = {
            "receipt": {
                "path": "out/FocusMacHomeAliasCompatibility.json",
                "bytes": 1,
                "sha256": "d" * 64,
            },
            "volume": {"filesystem": "apfs", "volume_uuid": "A" * 36},
            "alias": {
                "path": str(self.root),
                "target": str(self.root),
                "inode": self.root.stat().st_ino,
                "uid": os.getuid(),
                "gid": os.getgid(),
                "mode": 0o700,
                "target_identity": {},
            },
            "mappings": {
                "workspace": {
                    "logical": str(self.root),
                    "physical": str(self.root),
                    "identity": {},
                },
                "source": {
                    "logical": str(self.source),
                    "physical": str(self.source),
                    "identity": {},
                },
                "developer": {
                    "logical": str(self.developer),
                    "physical": str(self.developer),
                    "identity": {},
                },
            },
        }
        self.real_conflict_gate = (
            x64_frozen_relink_executor._assert_no_conflicting_processes
        )
        self.patchers = [
            mock.patch.object(
                x64_frozen_relink,
                "plan",
                side_effect=lambda _source: copy.deepcopy(self.planner),
            ),
            mock.patch.object(
                x64_frozen_relink,
                "revalidate_plan",
                side_effect=lambda _source, _plan: {
                    "status": "revalidated",
                    "plan_id": self.planner["plan_id"],
                    "closure_sha256": self.planner["closure_sha256"],
                    "graph_binding_sha256": hashlib.sha256(
                        json.dumps(
                            self.planner["graph_binding"],
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("ascii")
                    ).hexdigest(),
                },
            ),
            mock.patch.object(
                x64_frozen_relink_executor.onboarding_alias_compat,
                "validate_home_alias_receipt",
                side_effect=lambda _source: copy.deepcopy(self.alias),
            ),
            mock.patch.object(
                x64_frozen_relink_executor,
                "_assert_no_conflicting_processes",
                return_value={"method": "test", "conflicts": 0},
            ),
            mock.patch.object(
                x64_frozen_relink_executor,
                "_free_bytes",
                return_value=100 * x64_frozen_relink_executor.GIB,
            ),
            mock.patch.object(
                x64_frozen_relink_executor,
                "_group_exists",
                return_value=False,
            ),
            mock.patch.object(
                x64_frozen_relink_executor.os,
                "getpgid",
                side_effect=lambda pid: pid,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary.cleanup()

    def plan(self):
        return x64_frozen_relink_executor.execution_plan(
            self.source, self.evidence, self.run_id
        )

    def four_edge_output(self):
        return (
            "\n".join(
                "{}[{}/4] {}".format(
                    x64_frozen_relink.PRIVATE_STATUS_PREFIX,
                    index,
                    description,
                )
                for index, description in enumerate(
                    x64_frozen_relink._allowed_descriptions(), 1
                )
            )
            + "\n"
        ).encode("utf-8")

    def append_success_history(self):
        with self.ninja_log.open("ab") as stream:
            for index, relative in enumerate(x64_frozen_relink.FROZEN_OUTPUTS, 10):
                stream.write(
                    "{}\t{}\t{}\t{}\t{:x}\n".format(
                        index,
                        index + 1,
                        1000 + index,
                        relative,
                        0xA000 + index,
                    ).encode("utf-8")
                )

    def fake_popen(self, *, execution_returncode=0, partial=False, polls=None):
        calls = []

        def factory(argv, **kwargs):
            calls.append((list(argv), dict(kwargs)))
            descriptor = kwargs["stdout"]
            if "-n" in argv:
                os.write(descriptor, self.four_edge_output())
                return FakeProcess(4001, 0)
            if partial:
                first = self.out / x64_frozen_relink.FROZEN_OUTPUTS[0]
                first.parent.mkdir(parents=True, exist_ok=True)
                first.write_bytes(b"partial-new-output\n")
            elif execution_returncode == 0:
                for relative in x64_frozen_relink.FROZEN_OUTPUTS:
                    path = self.out / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(("new-output-" + relative + "\n").encode("utf-8"))
                self.append_success_history()
            os.write(
                descriptor,
                self.four_edge_output()
                if execution_returncode == 0
                else b"ninja: build stopped: subcommand failed.\n",
            )
            return FakeProcess(4002, execution_returncode, polls=polls)

        return factory, calls

    def execute_success(self):
        factory, calls = self.fake_popen()
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ):
            result = x64_frozen_relink_executor.execute(
                self.plan(),
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        return result, calls

    def execute_with_deferred_signal(self, evidence_name):
        plan = self.plan()
        factory, _ = self.fake_popen()
        real_atomic = x64_frozen_relink_executor._atomic_json
        fired = False

        def atomic(path, value):
            nonlocal fired
            reference = real_atomic(path, value)
            if not fired and str(path) == plan["evidence"][evidence_name]:
                fired = True
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return reference

        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), mock.patch.object(
            x64_frozen_relink_executor, "_atomic_json", side_effect=atomic
        ), self.assertRaises(x64_frozen_relink_executor.FrozenRelinkInterrupted):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        self.assertTrue(fired)
        return plan

    def rewrite_execution_evidence(self, mutate):
        plan = self.plan()
        execution, _ = x64_frozen_relink_executor._load_immutable_json(
            plan["evidence"]["execution"], "execution"
        )
        cleanup, _ = x64_frozen_relink_executor._load_immutable_json(
            plan["evidence"]["cleanup"], "cleanup"
        )
        mutate(execution)
        Path(plan["evidence"]["execution"]).unlink()
        Path(plan["evidence"]["cleanup"]).unlink()
        reference = x64_frozen_relink_executor._atomic_json(
            plan["evidence"]["execution"], execution
        )
        cleanup["execution"] = reference
        x64_frozen_relink_executor._atomic_json(
            plan["evidence"]["cleanup"], cleanup
        )
        return plan

    def publish_recovery_preflight(self, plan):
        lock = x64_frozen_relink_executor._lock_path(plan)
        if not lock.exists():
            lock.write_bytes(b"crash-preflight-lock\n")
            lock.chmod(0o600)
        ninja_log_data = self.ninja_log.read_bytes()
        ninja_log_reference = x64_frozen_relink_executor._atomic_immutable_bytes(
            plan["evidence"]["ninja_log_preimage"],
            ninja_log_data,
            max_bytes=x64_frozen_relink_executor.MAX_HISTORY_BYTES,
        )
        dry_data = self.four_edge_output()
        dry_reference = x64_frozen_relink_executor._atomic_immutable_bytes(
            plan["evidence"]["dry_run_log"],
            dry_data,
            max_bytes=x64_frozen_relink.MAX_DRY_RUN_OUTPUT_BYTES,
        )
        value = {
            "schema": 1,
            "kind": x64_frozen_relink_executor.PREFLIGHT_KIND,
            "run_id": plan["run_id"],
            "execution_plan_id": plan["execution_plan_id"],
            "created_at_ns": 1,
            "planner": plan["planner"],
            "process_gate": {"method": "test", "conflicts": 0},
            "post_dry_process_gate": {"method": "test", "conflicts": 0},
            "free_bytes": 100 * x64_frozen_relink_executor.GIB,
            "outputs": x64_frozen_relink_executor._output_snapshots(self.out),
            "history": x64_frozen_relink_executor._history_snapshots(self.out),
            "rspfiles": x64_frozen_relink_executor._require_rspfiles_absent(
                self.out
            ),
            "output_parent_inventories": (
                x64_frozen_relink_executor._capture_output_parent_inventories(
                    self.out, plan["out"]["identity"]
                )
            ),
            "ninja_log_preimage": ninja_log_reference,
            "dry_run": {
                "argv": plan["dry_run"]["argv"],
                "cwd": plan["out"]["logical"],
                "environment": plan["environment"],
                "pid": 6001,
                "pgid": 6001,
                "started_at_ns": 1,
                "finished_at_ns": 2,
                "returncode": 0,
                "process_group_absent": True,
                "stdout": dry_reference,
                "parsed": x64_frozen_relink.parse_dry_run_output(dry_data),
            },
            "revalidation": {
                "status": "revalidated",
                "plan_id": plan["planner"]["plan_id"],
                "closure_sha256": plan["planner"]["closure_sha256"],
                "graph_binding_sha256": plan["planner"]["graph_binding_sha256"],
            },
            "mutation_started": False,
        }
        reference = x64_frozen_relink_executor._atomic_json(
            plan["evidence"]["preflight"], value
        )
        x64_frozen_relink_executor._prepare_transaction_authorization(
            plan, reference, value["outputs"], value["history"]
        )
        return value, reference

    def test_plan_is_deterministic_exact_j8_and_contains_no_gn(self):
        first = self.plan()
        second = self.plan()
        self.assertEqual(first, second)
        self.assertEqual(x64_frozen_relink_executor.PLAN_KIND, first["kind"])
        self.assertEqual(8, first["execution"]["jobs"])
        self.assertEqual("-j8", first["execution"]["argv"][1])
        self.assertNotIn("-n", first["execution"]["argv"])
        self.assertIn("-n", first["dry_run"]["argv"])
        self.assertEqual(
            list(x64_frozen_relink.FROZEN_TARGETS),
            first["execution"]["argv"][-4:],
        )
        self.assertNotIn("NINJA_SUMMARIZE_BUILD", first["environment"])
        self.assertEqual(0, first["policy"]["gn_invocations"])
        self.assertEqual(0, first["policy"]["network_operations"])
        self.assertEqual(0, first["policy"]["shell_invocations"])
        self.assertRegex(first["runner"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("gn", [Path(item).name for item in first["execution"]["argv"]])
        self.assertEqual(
            list(x64_frozen_relink_executor.RSPFILE_RELATIVES),
            first["rspfiles"],
        )
        self.assertGreaterEqual(
            x64_frozen_relink_executor.MAX_HISTORY_BYTES,
            256 * 1024 * 1024,
        )

    def test_plan_rejects_unsafe_run_id_and_evidence_inside_source(self):
        for value in ("../bad", ".hidden", "bad/name", "", "x" * 81):
            with self.subTest(value=value), self.assertRaises(
                x64_frozen_relink_executor.FrozenRelinkExecutionError
            ):
                x64_frozen_relink_executor.execution_plan(
                    self.source, self.evidence, value
                )
        inside = self.source / "evidence"
        inside.mkdir()
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "outside the Chromium source",
        ):
            x64_frozen_relink_executor.execution_plan(
                self.source, inside, self.run_id
            )

    def test_plan_rejects_non_strict_planner_counters(self):
        self.planner["safety"]["gn_invocations"] = False
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "planner contract changed",
        ):
            self.plan()

    def test_execute_requires_both_explicit_confirmations(self):
        plan = self.plan()
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "requires --execute",
        ):
            x64_frozen_relink_executor.execute(plan)
        with self.assertRaises(x64_frozen_relink_executor.FrozenRelinkExecutionError):
            x64_frozen_relink_executor.execute(plan, allow_execute=True)
        self.assertFalse(any(self.evidence.iterdir()))

    def test_success_is_transactional_and_publishes_immutable_evidence(self):
        result, calls = self.execute_success()
        self.assertEqual("complete", result["status"])
        self.assertEqual(2, len(calls))
        dry_call, execution_call = calls
        self.assertIn("-n", dry_call[0])
        self.assertEqual("-j8", execution_call[0][1])
        self.assertTrue(dry_call[1]["start_new_session"])
        self.assertTrue(execution_call[1]["start_new_session"])
        self.assertEqual(subprocess_devnull(), execution_call[1]["stdin"])
        self.assertNotIn("NINJA_SUMMARIZE_BUILD", execution_call[1]["env"])
        for relative in x64_frozen_relink.FROZEN_OUTPUTS:
            self.assertTrue((self.out / relative).read_bytes().startswith(b"new-output-"))
        plan = self.plan()
        self.assertFalse(Path(plan["transaction_root"]).exists())
        lock = x64_frozen_relink_executor._lock_path(plan)
        self.assertTrue(lock.is_file())
        self.assertEqual(0o600, stat.S_IMODE(lock.stat().st_mode))
        for name in (
            "preflight",
            "dry_run_log",
            "ninja_log_preimage",
            "execution_log",
            "execution",
            "cleanup",
        ):
            path = Path(plan["evidence"][name])
            self.assertTrue(path.is_file())
            self.assertEqual(0, stat.S_IMODE(path.stat().st_mode) & 0o222)
        validated = x64_frozen_relink_executor.validate_completed_execution(
            self.source, self.evidence, self.run_id
        )
        self.assertEqual("complete", validated["status"])
        self.assertEqual(19, len(validated["ninja_log_additions"]))

    def test_success_allows_linker_driver_to_recreate_dsym_roots(self):
        base_factory, _ = self.fake_popen()
        dsym_roots = sorted(
            {
                relative.split("/", 1)[0]
                for relative in x64_frozen_relink.FROZEN_OUTPUTS
                if ".dSYM/" in relative
            }
        )

        def factory(argv, **kwargs):
            if "-n" not in argv:
                for root in dsym_roots:
                    shutil.rmtree(self.out / root)
            return base_factory(argv, **kwargs)

        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ):
            result = x64_frozen_relink_executor.execute(
                self.plan(),
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        self.assertEqual("complete", result["status"])
        completed = x64_frozen_relink_executor.validate_completed_execution(
            self.source, self.evidence, self.run_id
        )
        self.assertEqual("complete", completed["status"])

    def test_output_topology_fsync_covers_recreated_dsym_ancestors_bottom_up(self):
        expected = x64_frozen_relink_executor._output_topology_relatives()
        required = {
            ".",
            "Focus Browser.dSYM",
            "Focus Browser.dSYM/Contents",
            "Focus Browser.dSYM/Contents/Resources",
            "Focus Browser.dSYM/Contents/Resources/Relocations",
            "Focus Browser.dSYM/Contents/Resources/Relocations/x86_64",
        }
        self.assertTrue(required.issubset(set(expected)))
        with mock.patch.object(
            x64_frozen_relink_executor.os, "fsync"
        ) as fsync:
            flushed = x64_frozen_relink_executor._fsync_output_topology(
                self.out,
                x64_frozen_relink_executor._directory_identity(self.out),
            )
        self.assertEqual(expected, flushed)
        self.assertEqual(len(expected), fsync.call_count)
        depths = [len(PurePath.parts) for PurePath in map(Path, flushed)]
        self.assertEqual(sorted(depths, reverse=True), depths)

    def test_failed_ninja_restores_every_output_and_history(self):
        factory, _ = self.fake_popen(execution_returncode=1, partial=True)
        plan = self.plan()
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "exited with status 1",
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        for relative, expected in self.pre_output_bytes.items():
            self.assertEqual(expected, (self.out / relative).read_bytes())
        for relative, expected in self.pre_history.items():
            self.assertEqual(expected, (self.out / relative).read_bytes())
        self.assertFalse(Path(plan["transaction_root"]).exists())
        failure, _ = x64_frozen_relink_executor._load_immutable_json(
            plan["evidence"]["failure"], "failure"
        )
        self.assertTrue(failure["rollback"]["complete"])
        self.assertFalse(failure["execution_record_published"])
        self.assertFalse(failure["transaction_retained"])

    def test_failed_ninja_extra_artifact_fails_closed_and_retains_transaction(self):
        base_factory, _ = self.fake_popen(execution_returncode=1, partial=True)
        extra = self.out / "unexpected-linker-temporary-file"

        def factory(argv, **kwargs):
            process = base_factory(argv, **kwargs)
            if "-n" not in argv:
                extra.write_bytes(b"generated but not journaled\n")
            return process

        plan = self.plan()
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "rollback failed closed",
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        for relative, expected in self.pre_output_bytes.items():
            self.assertEqual(expected, (self.out / relative).read_bytes())
        self.assertTrue(extra.is_file())
        self.assertTrue(Path(plan["transaction_root"]).is_dir())
        failure, _ = x64_frozen_relink_executor._load_immutable_json(
            plan["evidence"]["failure"], "failure"
        )
        self.assertIn("parent inventory changed", failure["rollback_error"])
        self.assertTrue(failure["transaction_retained"])

    def test_rejects_no_work_dry_run_before_mutation(self):
        def factory(argv, **kwargs):
            os.write(kwargs["stdout"], b"ninja: no work to do.\n")
            return FakeProcess(4100, 0)

        plan = self.plan()
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "exactly four relink edges",
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        self.assertEqual(self.pre_output_bytes[x64_frozen_relink.FROZEN_OUTPUTS[0]], (self.out / x64_frozen_relink.FROZEN_OUTPUTS[0]).read_bytes())
        self.assertFalse(Path(plan["transaction_root"]).exists())

    def test_rejects_output_symlink_before_any_ninja(self):
        relative = x64_frozen_relink.FROZEN_OUTPUTS[0]
        path = self.out / relative
        real = path.with_name(path.name + ".real")
        path.rename(real)
        path.symlink_to(real.name)
        plan = self.plan()
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess,
            "Popen",
            side_effect=AssertionError("Ninja must not start"),
        ), self.assertRaises(x64_frozen_relink_executor.FrozenRelinkExecutionError):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )

    def test_rejects_hardlinked_ninja_log_before_dry_run(self):
        plan = self.plan()
        original = self.ninja_log.read_bytes()
        self.ninja_log.unlink()
        victim = self.root / "outside-history-victim.log"
        victim.write_bytes(original)
        os.link(victim, self.ninja_log)
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess,
            "Popen",
            side_effect=AssertionError("dry-run must not start"),
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "history must not be hard-linked",
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        self.assertEqual(original, victim.read_bytes())

    def test_rejects_stale_hardlinked_rspfile_before_dry_run(self):
        plan = self.plan()
        victim = self.root / "outside-rsp-victim.txt"
        original = b"preserve rsp victim\n"
        victim.write_bytes(original)
        rspfile = self.out / x64_frozen_relink_executor.RSPFILE_RELATIVES[0]
        os.link(victim, rspfile)
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess,
            "Popen",
            side_effect=AssertionError("dry-run must not start"),
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "rspfile preimage must be absent",
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        self.assertEqual(original, victim.read_bytes())

    def test_rejects_extra_file_in_managed_dsym_tree(self):
        extra = self.out / "Focus Browser.dSYM/Contents/extra.txt"
        extra.write_bytes(b"unexpected\n")
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "exact output allowlist",
        ):
            x64_frozen_relink_executor._output_snapshots(self.out)

    def test_rejects_extra_empty_directory_in_managed_dsym_tree(self):
        extra = self.out / "Focus Browser.dSYM/Contents/empty-extra"
        extra.mkdir()
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "exact output allowlist",
        ):
            x64_frozen_relink_executor._output_snapshots(self.out)

    def test_postflight_graph_drift_rolls_back(self):
        factory, _ = self.fake_popen()
        calls = 0

        def revalidate(_source, _plan):
            nonlocal calls
            calls += 1
            return {
                "status": "revalidated",
                "plan_id": self.planner["plan_id"] if calls == 1 else "e" * 64,
                "closure_sha256": self.planner["closure_sha256"],
                "graph_binding_sha256": plan["planner"]["graph_binding_sha256"],
            }

        plan = self.plan()
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), mock.patch.object(
            x64_frozen_relink, "revalidate_plan", side_effect=revalidate
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "post-execution planner identity changed",
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        for relative, expected in self.pre_output_bytes.items():
            self.assertEqual(expected, (self.out / relative).read_bytes())

    def test_unexpected_ninja_history_addition_rolls_back(self):
        original_append = self.append_success_history

        def append_with_extra():
            original_append()
            with self.ninja_log.open("ab") as stream:
                stream.write(b"1\t2\t3\tunexpected-output\tbeef\n")

        factory, _ = self.fake_popen()
        plan = self.plan()
        with mock.patch.object(self, "append_success_history", side_effect=append_with_extra), mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "exactly the frozen output additions",
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        self.assertEqual(self.pre_history[".ninja_log"], self.ninja_log.read_bytes())

    def test_signal_interrupt_terminates_group_rolls_back_and_restores_handlers(self):
        original = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        calls = 0

        class SignalProcess(FakeProcess):
            def poll(inner_self):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return 0
                handler = signal.getsignal(signal.SIGTERM)
                handler(signal.SIGTERM, None)

        def factory(argv, **kwargs):
            if "-n" in argv:
                os.write(kwargs["stdout"], self.four_edge_output())
                return SignalProcess(4201, 0)
            first = self.out / x64_frozen_relink.FROZEN_OUTPUTS[0]
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_bytes(b"partial\n")
            return SignalProcess(4202, 1)

        plan = self.plan()
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), mock.patch.object(
            x64_frozen_relink_executor, "_terminate_group", return_value={"absent": True}
        ), self.assertRaises(x64_frozen_relink_executor.FrozenRelinkInterrupted):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        for number, handler in original.items():
            self.assertIs(signal.getsignal(number), handler)
        for relative, expected in self.pre_output_bytes.items():
            self.assertEqual(expected, (self.out / relative).read_bytes())

    def test_signal_during_execution_publication_finishes_commit_then_surfaces(self):
        plan = self.execute_with_deferred_signal("execution")
        self.assertTrue(Path(plan["evidence"]["execution"]).is_file())
        self.assertTrue(Path(plan["evidence"]["cleanup"]).is_file())
        self.assertFalse(Path(plan["transaction_root"]).exists())
        completed = x64_frozen_relink_executor.validate_completed_execution(
            self.source, self.evidence, self.run_id
        )
        self.assertEqual("complete", completed["status"])
        failure, _ = x64_frozen_relink_executor._load_immutable_json(
            plan["evidence"]["failure"], "deferred signal failure"
        )
        self.assertTrue(failure["execution_record_published"])
        self.assertEqual(signal.SIGTERM, failure["signal"])

    def test_signal_during_cleanup_publication_finishes_commit_then_surfaces(self):
        plan = self.execute_with_deferred_signal("cleanup")
        self.assertTrue(Path(plan["evidence"]["cleanup"]).is_file())
        self.assertFalse(Path(plan["transaction_root"]).exists())
        completed = x64_frozen_relink_executor.validate_completed_execution(
            self.source, self.evidence, self.run_id
        )
        self.assertEqual("complete", completed["status"])

    def test_signal_during_recovery_publication_is_replayed_after_durable_result(self):
        plan = self.plan()
        self.publish_recovery_preflight(plan)
        real_atomic = x64_frozen_relink_executor._atomic_json
        fired = False

        def atomic(path, value):
            nonlocal fired
            reference = real_atomic(path, value)
            if not fired and str(path) == plan["evidence"]["recovery"]:
                fired = True
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return reference

        with mock.patch.object(
            x64_frozen_relink_executor, "_atomic_json", side_effect=atomic
        ), self.assertRaises(x64_frozen_relink_executor.FrozenRelinkInterrupted):
            x64_frozen_relink_executor.recover_transaction(
                plan, confirm_recovery=True
            )
        self.assertTrue(fired)
        self.assertTrue(Path(plan["evidence"]["recovery"]).is_file())
        second = x64_frozen_relink_executor.recover_transaction(
            plan, confirm_recovery=True
        )
        self.assertEqual("already-recovered-rollback", second["status"])

    def test_stale_advisory_lock_is_reused_and_not_deleted(self):
        plan = self.plan()
        lock = x64_frozen_relink_executor._lock_path(plan)
        lock.write_bytes(b"foreign\n")
        lock.chmod(0o600)
        with x64_frozen_relink_executor._exclusive_lock(plan):
            self.assertIn(self.run_id.encode(), lock.read_bytes())
        self.assertTrue(lock.exists())

    def test_lock_rejects_hardlink_without_truncating_other_file(self):
        plan = self.plan()
        victim = self.root / "must-not-truncate.txt"
        victim.write_bytes(b"preserve this data\n")
        victim.chmod(0o600)
        lock = x64_frozen_relink_executor._lock_path(plan)
        os.link(victim, lock)
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "lock file is unsafe",
        ):
            with x64_frozen_relink_executor._exclusive_lock(plan):
                self.fail("unsafe hardlink lock was accepted")
        self.assertEqual(b"preserve this data\n", victim.read_bytes())

    def test_active_advisory_lock_blocks_distinct_evidence_directory(self):
        plan = self.plan()
        second_evidence = self.root / "second-evidence"
        second_evidence.mkdir(mode=0o700)
        second = x64_frozen_relink_executor.execution_plan(
            self.source, second_evidence, "second-run"
        )
        descriptor = os.open(self.out, os.O_RDONLY)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaisesRegex(
                x64_frozen_relink_executor.FrozenRelinkExecutionError,
                "holds the output-directory lock",
            ):
                with x64_frozen_relink_executor._exclusive_lock(second):
                    self.fail("second executor acquired the global x64 lock")
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def test_replacing_sidecar_cannot_bypass_output_directory_lock(self):
        first = self.plan()
        second_evidence = self.root / "sidecar-race-evidence"
        second_evidence.mkdir(mode=0o700)
        second = x64_frozen_relink_executor.execution_plan(
            self.source, second_evidence, "sidecar-race"
        )
        lock = x64_frozen_relink_executor._lock_path(first)
        first_context = x64_frozen_relink_executor._exclusive_lock(first)
        first_context.__enter__()
        try:
            lock.unlink()
            lock.write_bytes(b"replacement\n")
            lock.chmod(0o600)
            with self.assertRaisesRegex(
                x64_frozen_relink_executor.FrozenRelinkExecutionError,
                "output-directory lock",
            ):
                with x64_frozen_relink_executor._exclusive_lock(second):
                    self.fail("replacing the sidecar bypassed the directory lock")
        finally:
            with self.assertRaisesRegex(
                x64_frozen_relink_executor.FrozenRelinkExecutionError,
                "lock identity changed",
            ):
                first_context.__exit__(None, None, None)

    def test_validation_rejects_tampered_execution_log(self):
        self.execute_success()
        plan = self.plan()
        log = Path(plan["evidence"]["execution_log"])
        log.chmod(0o644)
        log.write_bytes(b"tampered\n")
        log.chmod(0o444)
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "execution log",
        ):
            x64_frozen_relink_executor.validate_completed_execution(
                self.source, self.evidence, self.run_id
            )

    def test_validation_rejects_changed_output_identity(self):
        self.execute_success()
        relative = x64_frozen_relink.FROZEN_OUTPUTS[-1]
        path = self.out / relative
        data = path.read_bytes()
        replacement = path.with_name(path.name + ".replacement")
        replacement.write_bytes(data)
        os.replace(replacement, path)
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "output changed",
        ):
            x64_frozen_relink_executor.validate_completed_execution(
                self.source, self.evidence, self.run_id
            )

    def test_validation_binds_parsed_output_to_execution_log_exactly(self):
        self.execute_success()

        def mutate(execution):
            execution["post"]["parsed_output"]["descriptions"].append(
                execution["post"]["parsed_output"]["descriptions"][0]
            )

        self.rewrite_execution_evidence(mutate)
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "post-image",
        ):
            x64_frozen_relink_executor.validate_completed_execution(
                self.source, self.evidence, self.run_id
            )

    def test_validation_binds_current_ninja_history_snapshot(self):
        self.execute_success()

        def mutate(execution):
            execution["post"]["history"][0]["sha256"] = "0" * 64

        self.rewrite_execution_evidence(mutate)
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "history changed",
        ):
            x64_frozen_relink_executor.validate_completed_execution(
                self.source, self.evidence, self.run_id
            )

    def test_validation_rejects_malformed_postflight_with_domain_error(self):
        self.execute_success()

        def mutate(execution):
            execution["post"]["postflight"] = "not-an-object"

        self.rewrite_execution_evidence(mutate)
        with self.assertRaises(
            x64_frozen_relink_executor.FrozenRelinkExecutionError
        ):
            x64_frozen_relink_executor.validate_completed_execution(
                self.source, self.evidence, self.run_id
            )

    def test_validation_binds_ninja_additions_to_pre_and_post_logs(self):
        self.execute_success()

        def mutate(execution):
            addition = execution["post"]["ninja_log_additions"][0]
            addition.update(
                {
                    "start_ms": 999999,
                    "end_ms": 1000000,
                    "output_mtime_ns": 777777,
                    "command_hash": "beef",
                }
            )

        self.rewrite_execution_evidence(mutate)
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "history additions",
        ):
            x64_frozen_relink_executor.validate_completed_execution(
                self.source, self.evidence, self.run_id
            )

    def test_journal_rejects_non_object_history_source_with_domain_error(self):
        plan = self.plan()
        preflight, _ = self.publish_recovery_preflight(plan)
        _, prepared_reference = x64_frozen_relink_executor._load_immutable_json(
            plan["evidence"]["transaction_prepared"], "prepared"
        )
        paths, journal, _ = x64_frozen_relink_executor._begin_transaction(
            plan,
            preflight["outputs"],
            preflight["history"],
            prepared_reference,
        )
        journal["history"][0]["source"] = "not-an-object"
        paths["journal"].unlink()
        Path(plan["evidence"]["journal"]).unlink()
        x64_frozen_relink_executor._atomic_json(paths["journal"], journal)
        x64_frozen_relink_executor._atomic_json(
            plan["evidence"]["journal"], journal
        )
        with self.assertRaises(
            x64_frozen_relink_executor.FrozenRelinkExecutionError
        ):
            x64_frozen_relink_executor._journal_contract(plan)

    def test_history_delta_accepts_log_compaction_but_no_extra_additions(self):
        before = b"# ninja log v5\n1\t2\t3\told\tdead\n1\t2\t3\told\tdead\n"
        after = b"# ninja log v5\n1\t2\t3\told\tdead\n"
        additions = []
        for index, relative in enumerate(x64_frozen_relink.FROZEN_OUTPUTS, 1):
            additions.append(
                "{}\t{}\t{}\t{}\t{:x}".format(
                    index, index + 1, index + 2, relative, 0x100 + index
                )
            )
        result = x64_frozen_relink_executor._ninja_log_delta(
            before, after + ("\n".join(additions) + "\n").encode("utf-8")
        )
        self.assertEqual(19, len(result))

    def test_history_delta_rejects_duplicate_or_missing_output(self):
        before = b"# ninja log v5\n"
        lines = []
        outputs = list(x64_frozen_relink.FROZEN_OUTPUTS)
        outputs[-1] = outputs[0]
        for index, relative in enumerate(outputs, 1):
            lines.append(
                "{}\t{}\t{}\t{}\tbeef".format(index, index + 1, index, relative)
            )
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "outputs changed",
        ):
            x64_frozen_relink_executor._ninja_log_delta(
                before, before + ("\n".join(lines) + "\n").encode("utf-8")
            )

    def test_history_backup_rejects_same_content_inode_replacement(self):
        snapshot = x64_frozen_relink_executor._snapshot_rooted(
            self.out, ".ninja_log"
        )
        data = self.ninja_log.read_bytes()
        self.ninja_log.unlink()
        self.ninja_log.write_bytes(data)
        self.ninja_log.chmod(snapshot["mode"])
        os.utime(
            self.ninja_log,
            ns=(snapshot["mtime_ns"], snapshot["mtime_ns"]),
        )
        destination = self.root / "history-backup-destination"
        destination.mkdir(mode=0o700)
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "history source changed",
        ):
            x64_frozen_relink_executor._copy_rooted_snapshot(
                self.out,
                snapshot,
                destination,
                "ninja_log.preimage",
                destination_root_identity=(
                    x64_frozen_relink_executor._directory_identity(destination)
                ),
            )

    def test_history_is_revalidated_immediately_before_real_popen(self):
        factory, calls = self.fake_popen()
        real_move = x64_frozen_relink_executor._move_outputs_to_backup

        def move_then_replace(plan, paths, journal):
            result = real_move(plan, paths, journal)
            status = self.ninja_log.stat()
            data = self.ninja_log.read_bytes()
            self.ninja_log.unlink()
            self.ninja_log.write_bytes(data)
            self.ninja_log.chmod(stat.S_IMODE(status.st_mode))
            os.utime(
                self.ninja_log,
                ns=(status.st_mtime_ns, status.st_mtime_ns),
            )
            return result

        plan = self.plan()
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), mock.patch.object(
            x64_frozen_relink_executor,
            "_move_outputs_to_backup",
            side_effect=move_then_replace,
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "history changed before real relink",
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        self.assertEqual(1, len(calls))
        for relative, expected in self.pre_output_bytes.items():
            self.assertEqual(expected, (self.out / relative).read_bytes())

    def test_recovery_removes_prepared_only_root_without_touching_preimage(self):
        plan = self.plan()
        self.publish_recovery_preflight(plan)
        paths = x64_frozen_relink_executor._transaction_paths(plan)
        paths["root"].mkdir(mode=0o700)
        paths["outputs"].mkdir(mode=0o700)
        paths["history"].mkdir(mode=0o700)
        (paths["history"] / "ninja_log.preimage").write_bytes(
            self.ninja_log.read_bytes()[:7]
        )
        result = x64_frozen_relink_executor.recover_transaction(
            plan, confirm_recovery=True
        )
        self.assertEqual("recovered-rollback", result["status"])
        self.assertTrue(result["rollback"]["prepared_only"])
        self.assertFalse(paths["root"].exists())
        for relative, expected in self.pre_output_bytes.items():
            self.assertEqual(expected, (self.out / relative).read_bytes())

    def test_recovery_accepts_authorized_partial_preparation_shapes(self):
        for index, shape in enumerate(("root-only", "outputs-only", "journal-part")):
            with self.subTest(shape=shape):
                evidence = self.root / ("partial-evidence-{}".format(index))
                evidence.mkdir(mode=0o700)
                plan = x64_frozen_relink_executor.execution_plan(
                    self.source, evidence, "partial-{}".format(index)
                )
                self.publish_recovery_preflight(plan)
                paths = x64_frozen_relink_executor._transaction_paths(plan)
                paths["root"].mkdir(mode=0o700)
                if shape == "outputs-only":
                    paths["outputs"].mkdir(mode=0o700)
                if shape == "journal-part":
                    temporary = paths["root"] / ".journal.json.{}.{}.part".format(
                        os.getpid(), time.time_ns()
                    )
                    temporary.write_bytes(b"{\"schema\":")
                    temporary.chmod(0o600)
                result = x64_frozen_relink_executor.recover_transaction(
                    plan, confirm_recovery=True
                )
                self.assertEqual("recovered-rollback", result["status"])
                self.assertFalse(paths["root"].exists())

    def test_recovery_rolls_back_mixed_partially_moved_outputs(self):
        plan = self.plan()
        preflight, _ = self.publish_recovery_preflight(plan)
        _, prepared_reference = x64_frozen_relink_executor._load_immutable_json(
            plan["evidence"]["transaction_prepared"], "prepared"
        )
        paths, journal, _ = x64_frozen_relink_executor._begin_transaction(
            plan, preflight["outputs"], preflight["history"], prepared_reference
        )
        for item in journal["outputs"][:5]:
            x64_frozen_relink_executor._rename_rooted_regular(
                self.out,
                item["path"],
                paths["outputs"],
                item["path"],
                item,
                source_root_identity=plan["out"]["identity"],
                destination_root_identity=journal["transaction_directories"]["outputs"],
                create_destination_parents=True,
            )
        result = x64_frozen_relink_executor.recover_transaction(
            plan, confirm_recovery=True
        )
        self.assertEqual("recovered-rollback", result["status"])
        self.assertFalse(paths["root"].exists())
        for relative, expected in self.pre_output_bytes.items():
            self.assertEqual(expected, (self.out / relative).read_bytes())
        self.assertEqual(self.pre_history[".ninja_log"], self.ninja_log.read_bytes())

    def test_recovery_accepts_already_restored_root_absent_state(self):
        plan = self.plan()
        preflight, _ = self.publish_recovery_preflight(plan)
        _, prepared_reference = x64_frozen_relink_executor._load_immutable_json(
            plan["evidence"]["transaction_prepared"], "prepared"
        )
        paths, journal, _ = x64_frozen_relink_executor._begin_transaction(
            plan, preflight["outputs"], preflight["history"], prepared_reference
        )
        x64_frozen_relink_executor._move_outputs_to_backup(
            plan, paths, journal
        )
        x64_frozen_relink_executor._rollback_transaction(plan, paths, journal)
        result = x64_frozen_relink_executor.recover_transaction(
            plan, confirm_recovery=True
        )
        self.assertTrue(result["rollback"]["already_restored"])
        second = x64_frozen_relink_executor.recover_transaction(
            plan, confirm_recovery=True
        )
        self.assertEqual("already-recovered-rollback", second["status"])

    def test_rollback_refuses_to_delete_unjournaled_transaction_file(self):
        plan = self.plan()
        preflight, _ = self.publish_recovery_preflight(plan)
        _, prepared_reference = x64_frozen_relink_executor._load_immutable_json(
            plan["evidence"]["transaction_prepared"], "prepared"
        )
        paths, journal, _ = x64_frozen_relink_executor._begin_transaction(
            plan,
            preflight["outputs"],
            preflight["history"],
            prepared_reference,
        )
        foreign = paths["root"] / "foreign-user-file.txt"
        foreign.write_bytes(b"must not be deleted\n")
        with self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "file set changed",
        ):
            x64_frozen_relink_executor._rollback_transaction(
                plan, paths, journal
            )
        self.assertTrue(foreign.is_file())
        self.assertTrue(paths["root"].is_dir())

    def test_recovery_finishes_commit_after_cleanup_root_removed_before_receipt(self):
        plan = self.plan()
        factory, _ = self.fake_popen()
        real_atomic = x64_frozen_relink_executor._atomic_json

        def fail_cleanup(path, value):
            if Path(path) == Path(plan["evidence"]["cleanup"]):
                raise x64_frozen_relink_executor.FrozenRelinkExecutionError(
                    "simulated crash before cleanup receipt"
                )
            return real_atomic(path, value)

        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), mock.patch.object(
            x64_frozen_relink_executor, "_atomic_json", side_effect=fail_cleanup
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "simulated crash",
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        self.assertTrue(Path(plan["evidence"]["execution"]).exists())
        self.assertFalse(Path(plan["transaction_root"]).exists())
        result = x64_frozen_relink_executor.recover_transaction(
            plan, confirm_recovery=True
        )
        self.assertEqual("recovered-commit", result["status"])
        validated = x64_frozen_relink_executor.validate_completed_execution(
            self.source, self.evidence, self.run_id
        )
        self.assertEqual("complete", validated["status"])

    def test_published_execution_error_never_rolls_back_successful_outputs(self):
        plan = self.plan()
        factory, _ = self.fake_popen()
        real_atomic = x64_frozen_relink_executor._atomic_json

        def publish_then_fail(path, value):
            reference = real_atomic(path, value)
            if Path(path) == Path(plan["evidence"]["execution"]):
                raise x64_frozen_relink_executor.FrozenRelinkExecutionError(
                    "simulated post-link fsync error"
                )
            return reference

        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), mock.patch.object(
            x64_frozen_relink_executor, "_atomic_json", side_effect=publish_then_fail
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "post-link fsync",
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        for relative in x64_frozen_relink.FROZEN_OUTPUTS:
            self.assertTrue((self.out / relative).read_bytes().startswith(b"new-output-"))
        self.assertTrue(Path(plan["transaction_root"]).exists())
        recovered = x64_frozen_relink_executor.recover_transaction(
            plan, confirm_recovery=True
        )
        self.assertEqual("recovered-commit", recovered["status"])

    def test_recovery_rejects_corrupt_dry_run_before_committed_cleanup(self):
        plan = self.plan()
        factory, _ = self.fake_popen()
        real_atomic = x64_frozen_relink_executor._atomic_json

        def publish_then_fail(path, value):
            reference = real_atomic(path, value)
            if Path(path) == Path(plan["evidence"]["execution"]):
                raise x64_frozen_relink_executor.FrozenRelinkExecutionError(
                    "simulated crash retaining committed preimages"
                )
            return reference

        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", side_effect=factory
        ), mock.patch.object(
            x64_frozen_relink_executor, "_atomic_json", side_effect=publish_then_fail
        ), self.assertRaises(x64_frozen_relink_executor.FrozenRelinkExecutionError):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        transaction = Path(plan["transaction_root"])
        self.assertTrue(transaction.is_dir())
        dry_log = Path(plan["evidence"]["dry_run_log"])
        dry_log.chmod(0o644)
        dry_log.write_bytes(b"corrupt dry-run proof\n")
        dry_log.chmod(0o444)
        with self.assertRaises(
            x64_frozen_relink_executor.FrozenRelinkExecutionError
        ):
            x64_frozen_relink_executor.recover_transaction(
                plan, confirm_recovery=True
            )
        self.assertTrue(transaction.is_dir())
        self.assertFalse(Path(plan["evidence"]["cleanup"]).exists())

    def test_wrong_spawned_pgid_never_calls_killpg(self):
        log = self.evidence / "wrong-pgid.log"
        process = FakeProcess(5001, 0)
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess, "Popen", return_value=process
        ), mock.patch.object(
            x64_frozen_relink_executor.os, "getpgid", return_value=7777
        ), mock.patch.object(
            x64_frozen_relink_executor.os,
            "killpg",
            side_effect=AssertionError("foreign process group must not be signalled"),
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "not its process-group leader",
        ):
            x64_frozen_relink_executor._run_bounded(
                [str(self.ninja), "-n"],
                self.out,
                {"PATH": "/usr/bin:/bin"},
                log,
                1024,
                self.source,
            )

    def test_unproven_process_group_retains_transaction_without_rollback(self):
        calls = 0

        def run(argv, cwd, environment, log_path, max_bytes, disk_root):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise x64_frozen_relink_executor.FrozenRelinkUnsafeProcessGroup(
                    "linker descendants remain"
                )
            data = self.four_edge_output()
            Path(log_path).write_bytes(data)
            Path(log_path).chmod(0o444)
            return {
                "argv": list(argv),
                "cwd": str(cwd),
                "environment": dict(environment),
                "pid": 5101,
                "pgid": 5101,
                "started_at_ns": 1,
                "finished_at_ns": 2,
                "returncode": 0,
                "process_group_absent": True,
                "stdout": {
                    "path": str(log_path),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
                "data": data,
            }

        plan = self.plan()
        with mock.patch.object(
            x64_frozen_relink_executor, "_run_bounded", side_effect=run
        ), self.assertRaises(
            x64_frozen_relink_executor.FrozenRelinkUnsafeProcessGroup
        ):
            x64_frozen_relink_executor.execute(
                plan,
                allow_execute=True,
                confirm_exact_four_edges=True,
            )
        self.assertTrue(Path(plan["transaction_root"]).is_dir())
        for relative in x64_frozen_relink.FROZEN_OUTPUTS:
            self.assertFalse((self.out / relative).exists())
        failure, _ = x64_frozen_relink_executor._load_immutable_json(
            plan["evidence"]["failure"], "failure"
        )
        self.assertIsNone(failure["rollback"])
        self.assertTrue(failure["rollback_blocked_by_process_group"])
        self.assertTrue(failure["transaction_retained"])

    def test_terminate_group_fails_closed_when_group_survives_kill(self):
        process = FakeProcess(5201, 1)
        with mock.patch.object(
            x64_frozen_relink_executor, "_group_exists", return_value=True
        ), mock.patch.object(
            x64_frozen_relink_executor, "TERM_GRACE_SECONDS", 0
        ), mock.patch.object(
            x64_frozen_relink_executor.os, "killpg"
        ) as killpg, self.assertRaises(
            x64_frozen_relink_executor.FrozenRelinkUnsafeProcessGroup
        ):
            x64_frozen_relink_executor._terminate_group(process, 5201)
        self.assertEqual(
            [signal.SIGTERM, signal.SIGKILL],
            [call.args[1] for call in killpg.call_args_list],
        )

    def test_conflict_gate_detects_identical_pinned_ninja_by_executable(self):
        plan = self.plan()
        command = "{} -j8 -f build.ninja {}".format(
            plan["execution"]["argv"][0], plan["execution"]["argv"][-1]
        )
        ps = mock.Mock(returncode=0, stdout=("999 999 " + command + "\n").encode(), stderr=b"")
        lsof = mock.Mock(
            returncode=0,
            stdout=("p999\nfcwd\nn{}\n".format(self.out)).encode(),
            stderr=b"",
        )
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess,
            "run",
            side_effect=[ps, lsof],
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "another process",
        ):
            self.real_conflict_gate(plan)

    def test_conflict_gate_detects_non_ninja_process_with_output_cwd(self):
        plan = self.plan()
        ps = mock.Mock(
            returncode=0,
            stdout=b"998 998 /usr/bin/dsymutil --flat\n",
            stderr=b"",
        )
        lsof = mock.Mock(
            returncode=0,
            stdout=("p998\ncdsymutil\nfcwd\nn{}\n".format(self.out)).encode(),
            stderr=b"",
        )
        with mock.patch.object(
            x64_frozen_relink_executor.subprocess,
            "run",
            side_effect=[ps, lsof],
        ), self.assertRaisesRegex(
            x64_frozen_relink_executor.FrozenRelinkExecutionError,
            "another process",
        ):
            self.real_conflict_gate(plan)


def subprocess_devnull():
    return x64_frozen_relink_executor.subprocess.DEVNULL


if __name__ == "__main__":
    unittest.main()
