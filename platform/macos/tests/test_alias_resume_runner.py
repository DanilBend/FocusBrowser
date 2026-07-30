#!/usr/bin/env python3
"""Adversarial tests for the one-shot official resume3 runner."""

import copy
import json
import os
import stat
import sys
import tempfile
import time
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import alias_resume_runner as runner


class FakeProcess:
    def __init__(self, pid, returncode, stdout_path):
        self.pid = pid
        self._returncode = returncode
        self._stdout_path = Path(stdout_path)
        self.waited = False

    def poll(self):
        return self._returncode if self.waited else None

    def wait(self, timeout=None):
        del timeout
        if not self.waited:
            with self._stdout_path.open("ab") as stream:
                stream.write(b"official resume3 fixture output\n")
            self.waited = True
        return self._returncode


class AliasResumeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.logs = self.workspace / "work/logs"
        self.logs.mkdir(parents=True)
        self.checkout = self.workspace / "work/chromium-150-macos"
        self.source = self.checkout / "src"
        self.out = self.source / runner.OUT_RELATIVE
        self.out.mkdir(parents=True)
        for name, data in (
            (".ninja_log", b"# ninja log fixture\n"),
            (".ninja_deps", b"ninja deps fixture\n"),
            ("build.ninja", b"subninja toolchain.ninja\n"),
            ("toolchain.ninja", b"rule fixture\n"),
        ):
            (self.out / name).write_bytes(data)
        self.depot = self.checkout / "depot_tools"
        self.depot.mkdir()
        self.autoninja = self.depot / "autoninja"
        self.autoninja.write_text("#!/bin/bash\n", encoding="utf-8")
        self.autoninja.chmod(0o755)
        (self.depot / "autoninja.py").write_text("# fixture\n", encoding="utf-8")
        self.python = (
            self.depot
            / runner.build_pipeline.PACKAGING_PYTHON_RELDIR
            / "python3.11"
        )
        self.python.parent.mkdir(parents=True)
        self.python.write_text("fixture python\n", encoding="utf-8")
        self.python.chmod(0o755)
        self.ninja = self.source / "pinned-ninja"
        self.ninja.write_text("fixture ninja\n", encoding="utf-8")
        self.ninja.chmod(0o755)
        self.developer = self.root / "Xcode.app/Contents/Developer"
        self.developer.mkdir(parents=True)
        self.logical_home = self.root / "logical-home"
        self.logical_home.mkdir()
        source_stat = self.source.stat()
        developer_stat = self.developer.stat()
        alias_stat = self.logical_home.stat()
        self.alias_receipt = {
            "logical_home": str(self.logical_home),
            "alias": {
                "path": str(self.logical_home),
                "target": str(self.logical_home),
                "device": alias_stat.st_dev,
                "inode": alias_stat.st_ino,
                "uid": alias_stat.st_uid,
                "gid": alias_stat.st_gid,
                "mode": stat.S_IMODE(alias_stat.st_mode),
            },
            "mappings": {
                "source": {
                    "logical": str(self.source),
                    "physical": str(self.source),
                    "identity": {
                        "device": source_stat.st_dev,
                        "inode": source_stat.st_ino,
                    },
                },
                "developer": {
                    "logical": str(self.developer),
                    "physical": str(self.developer),
                    "identity": {
                        "device": developer_stat.st_dev,
                        "inode": developer_stat.st_ino,
                    },
                },
            },
        }
        self.ninja_contract = {
            "path": str(self.ninja),
            "sha256": runner._snapshot_regular(self.ninja, "ninja")["sha256"],
            "architecture": "arm64",
        }
        evidence = {
            name: runner.EvidencePath(
                self.logs / (runner.RUN_STEM + suffix),
                self.logs / (runner.RUN_STEM + suffix),
            )
            for name, suffix in runner.EVIDENCE_SUFFIXES.items()
        }
        environment = runner._base_environment(
            self.source,
            self.developer,
            self.ninja_contract,
            self.logical_home,
        )
        argv = (
            str(self.autoninja),
            "-j8",
            "-C",
            runner.OUT_RELATIVE,
            *runner.TARGETS,
        )
        shell_script = runner._shell_script(
            argv, environment, evidence["stdout"].physical
        )
        self.plan = runner.RunPlan(
            source=self.source,
            physical_source=self.source,
            developer_dir=self.developer,
            physical_developer_dir=self.developer,
            workspace=self.workspace,
            physical_workspace=self.workspace,
            logs=self.logs,
            physical_logs=self.logs,
            out=self.out,
            physical_out=self.out,
            autoninja=self.autoninja,
            physical_autoninja=self.autoninja,
            ninja=self.ninja_contract,
            alias_receipt=self.alias_receipt,
            alias_receipt_path=self.source / "HomeAlias.json",
            argv=argv,
            environment=environment,
            shell_script=shell_script,
            evidence=evidence,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def publish(self, name, value=None):
        return runner._publish_json_no_replace(
            self.plan.evidence[name],
            value or {"schema": 1, "name": name},
            name,
        )

    def successful_monitor(self):
        value = runner._empty_monitor_report(self.plan, True)
        free = 100 * runner.build_pipeline.GIB
        memory_sample = self.memory_sample()
        value.update(
            {
                "checks": 2,
                "minimum_free_bytes": {"source": free, "logs": free},
                "last_free_bytes": {"source": free, "logs": free},
                "maximum_stdout_bytes": self.plan.evidence[
                    "stdout"
                ].physical.stat().st_size,
            }
        )
        value["memory"].update(
            {
                "samples": 2,
                "minimum_free_percent": memory_sample["free_percent"],
                "maximum_swap_used_bytes": memory_sample["swap_used_bytes"],
                "maximum_swap_total_bytes": memory_sample["swap_total_bytes"],
                "last": memory_sample,
            }
        )
        return value

    @staticmethod
    def memory_sample(free_percent=76, swap_used_bytes=64 * 1024 ** 2):
        swap_total = max(1024 ** 3, swap_used_bytes + 1024 ** 3)
        return {
            "memory_total_bytes": 16 * 1024 ** 3,
            "free_percent": free_percent,
            "swap_total_bytes": swap_total,
            "swap_used_bytes": swap_used_bytes,
            "swap_free_bytes": swap_total - swap_used_bytes,
        }

    def test_fixed_command_is_j8_pipefail_and_has_no_gn_or_network(self):
        self.assertEqual("-j8", self.plan.argv[1])
        self.assertEqual(runner.JOBS, 8)
        self.assertIn("set -o pipefail", self.plan.shell_script)
        self.assertIn("/usr/bin/env -i", self.plan.shell_script)
        self.assertIn("/usr/bin/tee -a", self.plan.shell_script)
        self.assertEqual(
            ["/bin/zsh", "-f", "-c", self.plan.shell_script],
            runner._pre_launch_value(
                self.plan,
                {
                    "device": 1,
                    "inode": 1,
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                    "mode": 0o644,
                    "bytes": 0,
                    "mtime_ns": 1,
                    "birth_time_ns": 1,
                },
            )["planned_process"]["shell_argv"],
        )
        self.assertNotIn("gn gen", self.plan.shell_script)
        self.assertNotRegex(self.plan.shell_script, r"https?://")
        launched = object()
        with mock.patch.object(
            runner.subprocess, "Popen", return_value=launched
        ) as popen:
            self.assertIs(launched, runner._launch(self.plan))
        self.assertEqual(
            ["/bin/zsh", "-f", "-c", self.plan.shell_script],
            popen.call_args.args[0],
        )
        self.assertNotIn("-l", popen.call_args.args[0])
        parsed = runner.parser().parse_args(
            [
                "run",
                "--source-root",
                str(self.source),
                "--developer-dir",
                str(self.developer),
            ]
        )
        self.assertFalse(parsed.execute)
        self.assertFalse(parsed.confirm_official_resume3)
        self.assertEqual("arm64", parsed.architecture)

    def test_x64_plan_uses_fresh_contract_fixed_out_stem_targets_and_j8(self):
        x64_out = self.source / runner.ARCHITECTURE_CONFIGS["x64"]["out_relative"]
        x64_out.mkdir()
        fresh_path = self.source / "out/FocusMacFreshX64Preparation.json"
        fresh_value = {"schema": 1, "architecture": "x64", "fresh": True}
        fresh_path.write_text(json.dumps(fresh_value), encoding="utf-8")
        fresh_path.chmod(0o644)
        context = types.SimpleNamespace(
            logical_workspace=self.workspace,
            physical_workspace=self.workspace,
            physical_source=self.source,
            physical_developer=self.developer,
        )
        with mock.patch.object(runner.sys, "platform", "darwin"), mock.patch.object(
            runner.build_pipeline, "resolve_source", return_value=self.source
        ), mock.patch.object(
            runner.build_pipeline,
            "home_alias_receipt_contract",
            return_value=(self.source / "HomeAlias.json", self.alias_receipt),
        ), mock.patch.object(
            runner.build_pipeline, "_recorded_alias_context", return_value=context
        ), mock.patch.object(
            runner.build_pipeline,
            "fresh_x64_preparation_contract",
            return_value=(fresh_path, fresh_value),
            create=True,
        ) as fresh_contract, mock.patch.object(
            runner.build_pipeline, "preparation_contract"
        ) as legacy_preparation, mock.patch.object(
            runner.build_pipeline, "onboarding_alias_root_receipt_contract"
        ) as legacy_onboarding, mock.patch.object(
            runner.build_pipeline, "ninja_contract", return_value=self.ninja_contract
        ):
            plan = runner.create_plan(self.source, self.developer, "x64")
        fresh_contract.assert_called_once_with(self.source, self.developer)
        legacy_preparation.assert_not_called()
        legacy_onboarding.assert_not_called()
        self.assertEqual("x64", plan.architecture)
        self.assertEqual("out/FocusMacX64", plan.out_relative)
        self.assertEqual(x64_out, plan.out)
        self.assertEqual(runner.X64_RUN_STEM, plan.run_stem)
        self.assertTrue(plan.run_stem.startswith("build-x64-resume3-"))
        self.assertEqual(
            (str(self.autoninja), "-j8", "-C", "out/FocusMacX64", *runner.TARGETS),
            plan.argv,
        )
        self.assertNotIn("gn gen", plan.shell_script)
        self.assertNotRegex(plan.shell_script, r"https?://")
        self.assertEqual(
            runner.X64_RUN_STEM + runner.EVIDENCE_SUFFIXES["final"],
            plan.evidence["final"].logical.name,
        )
        self.assertEqual(str(fresh_path), plan.fresh_x64_preparation["receipt"]["path"])

    def test_x64_records_are_architecture_and_fresh_receipt_bound(self):
        x64_out = self.source / runner.ARCHITECTURE_CONFIGS["x64"]["out_relative"]
        x64_out.mkdir()
        for name, data in (
            ("build.ninja", b"subninja toolchain.ninja\n"),
            ("toolchain.ninja", b"rule fixture\n"),
        ):
            (x64_out / name).write_bytes(data)
        fresh = {
            "receipt": {"path": "/fresh.json", "sha256": "a" * 64},
            "contract_sha256": "b" * 64,
        }
        plan = replace(
            self.plan,
            architecture="x64",
            out=x64_out,
            physical_out=x64_out,
            out_relative="out/FocusMacX64",
            run_stem=runner.X64_RUN_STEM,
            fresh_x64_preparation=fresh,
        )
        stdout_initial = {
            "device": 1,
            "inode": 2,
            "uid": os.getuid(),
            "gid": os.getgid(),
            "mode": 0o644,
            "bytes": 0,
            "mtime_ns": 3,
            "birth_time_ns": 4,
        }
        pre = runner._pre_launch_value(plan, stdout_initial)
        self.assertEqual("x64", pre["architecture"])
        self.assertEqual(runner.X64_RUN_STEM, pre["run_id"])
        self.assertEqual(fresh, pre["fresh_x64_preparation"])
        self.assertIsNone(pre["pre_run"]["ninja_log"])
        self.assertIsNone(pre["pre_run"]["ninja_deps"])
        (x64_out / ".ninja_log").write_bytes(b"unexpected stale history\n")
        with self.assertRaisesRegex(
            runner.RunnerError, "fresh x64 pre-run Ninja history is not absent"
        ):
            runner._pre_launch_value(plan, stdout_initial)
        (x64_out / ".ninja_log").unlink()
        process = types.SimpleNamespace(pid=123)
        with mock.patch.object(runner, "_stdout_live_snapshot", return_value={}):
            primary = runner._primary_value(
                plan, process, {"sha256": "c" * 64}, [], stdout_initial
            )
        self.assertEqual("x64", primary["architecture"])
        member_base = {"ppid": 1, "pgid": 123}
        supplement_primary = {
            "process_group": {
                "members": [
                    {**member_base, "role": role, "pid": pid}
                    for role, pid in (("autoninja_python", 10), ("pinned_ninja", 11))
                ]
            }
        }
        python_bin = (
            plan.source.parent
            / "depot_tools/python-bin/.."
            / runner.build_pipeline.PACKAGING_PYTHON_RELDIR
        )
        observed_path = os.pathsep.join(
            (str(python_bin), str(python_bin / "Scripts"), plan.environment["PATH"])
        )
        raw_environment = "PATH={} PWD={} {}".format(
            observed_path,
            plan.physical_source,
            " ".join(
                "{}={}".format(name, plan.environment[name])
                for name in runner.BASE_ENVIRONMENT_ORDER
                if name != "PATH"
            ),
        )
        with mock.patch.object(runner, "_ps_eww", return_value=raw_environment):
            supplement = runner._supplement_value(
                plan, supplement_primary, {"sha256": "d" * 64}
            )
        self.assertEqual("x64", supplement["architecture"])
        spine = [
            {
                "role": role,
                "pid": index + 20,
                "ppid": 1,
                "pgid": 123,
                "started_at_ns": 1,
                "cwd_physical": str(plan.physical_source),
                "executable": "/fixture/executable",
                "ps_command": "fixture",
            }
            for index, role in enumerate(runner.EXPECTED_ROLES)
        ]
        revalidation_primary = {"process_group": {"members": spine}}
        with mock.patch.object(
            runner, "_capture_spine", return_value=spine
        ), mock.patch.object(
            runner, "_script_identity", return_value={}
        ), mock.patch.object(
            runner, "_stdout_live_snapshot", return_value={}
        ):
            revalidation = runner._revalidation_value(
                plan,
                process,
                {"sha256": "1" * 64},
                revalidation_primary,
                {"sha256": "2" * 64},
                {"sha256": "3" * 64},
                stdout_initial,
            )
        self.assertEqual("x64", revalidation["architecture"])
        exit_value = runner._exit_status_value(
            plan,
            process,
            0,
            10,
            {"sha256": "c" * 64},
            None,
            None,
            None,
            {},
            {},
            "completed",
            None,
            {},
        )
        self.assertEqual("x64", exit_value["architecture"])
        final = runner._final_record(
            plan,
            process,
            1,
            {"observed_at_ns": 2},
            {"logical": {}, "identity": {}, "pre_run": {}, "runner": {}, "stdout_log": stdout_initial},
            {"sha256": "1" * 64},
            {"sha256": "2" * 64},
            {"sha256": "3" * 64},
            {"sha256": "4" * 64},
            {"stdout_log": {}, "post_run": {}, "wait_observation": {"wait_returned_at_ns": 5, "returncode": 0}},
            {"sha256": "5" * 64},
        )
        self.assertEqual("x64", final["architecture"])
        self.assertEqual(fresh, final["fresh_x64_preparation"])
        report = runner._plan_report(plan)
        self.assertEqual("x64", report["architecture"])
        self.assertEqual(fresh, report["fresh_x64_preparation"])

    def test_real_launched_child_inherits_no_runner_signal_mask_and_accepts_term(self):
        report = self.root / "child-signal-mask.json"
        child_code = (
            "import json,pathlib,signal,time;"
            "blocked=signal.pthread_sigmask(signal.SIG_BLOCK,set());"
            "pathlib.Path({!r}).write_text(json.dumps(sorted(int(x) for x in blocked)));"
            "time.sleep(30)"
        ).format(str(report))
        launch_plan = replace(
            self.plan,
            shell_script="exec {} -c {}".format(
                runner.shlex.quote(sys.executable),
                runner.shlex.quote(child_code),
            ),
        )
        original_mask = runner.signal.pthread_sigmask(runner.signal.SIG_BLOCK, set())
        previous, state = runner._install_owned_signal_handlers()
        process = None
        try:
            state["defer"] = True
            try:
                process = runner._launch(launch_plan)
            finally:
                state["defer"] = False
            deadline = time.monotonic() + 5
            while not report.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(report.is_file(), "real child did not report its mask")
            child_mask = set(json.loads(report.read_text()))
            controlled = {int(signum) for signum in runner.CONTROLLED_SIGNALS}
            self.assertEqual(
                {int(signum) for signum in original_mask} & controlled,
                child_mask & controlled,
            )
            os.killpg(process.pid, runner.signal.SIGTERM)
            self.assertEqual(-runner.signal.SIGTERM, process.wait(timeout=5))
            self.assertIsNone(state["signum"])
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, runner.signal.SIGKILL)
                process.wait(timeout=5)
            runner._restore_owned_signal_handlers(previous)
        self.assertEqual(
            original_mask,
            runner.signal.pthread_sigmask(runner.signal.SIG_BLOCK, set()),
        )

    def test_evidence_paths_are_fixed_and_traversal_is_rejected(self):
        evidence = runner._fixed_evidence(self.workspace, self.workspace)
        self.assertEqual(
            runner.RUN_STEM + ".execution.json", evidence["final"].logical.name
        )
        with self.assertRaisesRegex(runner.RunnerError, "escapes"):
            runner._safe_fixed_pair(
                self.workspace,
                self.workspace,
                self.workspace.parent / "escape.json",
                self.workspace.parent / "escape.json",
                "escape",
            )
        with self.assertRaisesRegex(runner.RunnerError, "projection"):
            runner._safe_fixed_pair(
                self.workspace,
                self.workspace,
                self.workspace / "one.json",
                self.workspace / "two.json",
                "mismatch",
            )

    def test_atomic_evidence_is_immutable_no_replace_and_preserves_rival(self):
        pair = self.plan.evidence["primary"]
        publication = runner._publish_json_no_replace(
            pair, {"schema": 1}, "primary"
        )
        self.assertEqual(0, publication["identity"]["mode"] & 0o222)
        self.assertEqual({"schema": 1}, json.loads(pair.logical.read_text()))
        with self.assertRaisesRegex(runner.RunnerError, "already exists"):
            runner._publish_json_no_replace(pair, {"schema": 2}, "primary")

        rival_pair = self.plan.evidence["supplement"]

        def create_rival(phase, observed_pair, _offset, _total):
            if phase == "after-temp-fsync":
                observed_pair.physical.write_bytes(b"rival evidence\n")

        with self.assertRaisesRegex(runner.RunnerError, "raced"):
            runner._publish_json_no_replace(
                rival_pair,
                {"schema": 1},
                "supplement",
                test_hook=create_rival,
            )
        self.assertEqual(b"rival evidence\n", rival_pair.physical.read_bytes())

    def test_descriptor_bound_json_rejects_path_replacement_during_read(self):
        path = self.root / "descriptor-evidence.json"
        replacement = self.root / "descriptor-replacement.json"
        path.write_text('{"schema":1,"value":"original"}\n', encoding="utf-8")
        replacement.write_text(
            '{"schema":1,"value":"replacement"}\n', encoding="utf-8"
        )
        path.chmod(0o444)
        replacement.chmod(0o444)
        real_read = os.read
        replaced = {"value": False}

        def replace_path_then_read(descriptor, count):
            if not replaced["value"]:
                os.replace(replacement, path)
                replaced["value"] = True
            return real_read(descriptor, count)

        with mock.patch.object(
            runner.build_pipeline.os, "read", side_effect=replace_path_then_read
        ):
            with self.assertRaisesRegex(
                runner.build_pipeline.PipelineError,
                "changed during descriptor-bound read|path no longer names",
            ):
                runner.build_pipeline._descriptor_bound_immutable_json(
                    path, "fixture evidence"
                )
        self.assertTrue(replaced["value"])

    def test_partial_evidence_write_never_publishes_final(self):
        pair = self.plan.evidence["revalidation"]

        def interrupt(phase, _pair, _offset, _total):
            if phase == "after-write":
                raise KeyboardInterrupt("fixture interruption")

        with self.assertRaises(KeyboardInterrupt):
            runner._publish_json_no_replace(
                pair,
                {"schema": 1, "payload": "x" * 9000},
                "revalidation",
                test_hook=interrupt,
            )
        self.assertFalse(pair.physical.exists())
        temporary = pair.physical.with_name(
            "." + pair.physical.name + ".runner.tmp"
        )
        self.assertTrue(temporary.is_file())
        self.assertGreater(temporary.stat().st_size, 0)

    def test_publish_after_link_crash_is_complete_immutable_and_retry_fails_closed(self):
        pair = self.plan.evidence["final"]
        expected = {"schema": 3, "payload": "complete"}

        def crash_after_link(phase, _pair, _offset, _total):
            if phase == "after-final-link":
                raise RuntimeError("simulated crash after durable content link")

        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            runner._publish_json_no_replace(
                pair,
                expected,
                "final",
                test_hook=crash_after_link,
            )
        before = pair.physical.read_bytes()
        self.assertEqual(expected, json.loads(before))
        self.assertEqual(0, pair.physical.stat().st_mode & 0o222)
        with self.assertRaisesRegex(runner.RunnerError, "already exists"):
            runner._publish_json_no_replace(pair, expected, "final retry")
        self.assertEqual(before, pair.physical.read_bytes())

    def test_stdout_freeze_is_bound_to_original_inode_and_never_chmods_replacement(self):
        pair = self.plan.evidence["stdout"]
        initial = runner._create_stdout(pair)
        pair.physical.unlink()
        pair.physical.write_bytes(b"rival stdout\n")
        pair.physical.chmod(0o666)
        rival = pair.physical.stat()
        with self.assertRaisesRegex(runner.RunnerError, "stdout changed"):
            runner._freeze_stdout(pair, initial, require_nonempty=False)
        after = pair.physical.stat()
        self.assertEqual((rival.st_dev, rival.st_ino), (after.st_dev, after.st_ino))
        self.assertEqual(0o666, stat.S_IMODE(after.st_mode))

    def test_pre_launch_binds_history_stdout_and_precedes_process(self):
        stdout = runner._create_stdout(self.plan.evidence["stdout"])
        value = runner._pre_launch_value(self.plan, stdout)
        publication = runner._publish_json_no_replace(
            self.plan.evidence["pre_launch"], value, "pre-launch"
        )
        start = max(
            publication["identity"]["birth_time_ns"],
            publication["identity"]["mtime_ns"],
            value["created_at_ns"],
        ) + 1
        self.assertTrue(
            runner.validate_pre_launch(
                value, self.plan, publication, before_process_start_ns=start
            )
        )
        forged = copy.deepcopy(value)
        forged["pre_run"]["ninja_log"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(runner.RunnerError, "pre-launch evidence"):
            runner.validate_pre_launch(forged, self.plan, publication)
        forged = copy.deepcopy(value)
        forged["planned_process"]["argv"][1] = "-j4"
        with self.assertRaisesRegex(runner.RunnerError, "pre-launch evidence"):
            runner.validate_pre_launch(forged, self.plan, publication)
        strict_mutations = (
            lambda item: item["planned_process"].__setitem__("jobs", True),
            lambda item: item["policy"].__setitem__("network_operations", False),
            lambda item: item["planned_process"].pop("start_new_session"),
            lambda item: item["policy"].__setitem__("unexpected", 0),
        )
        for mutate in strict_mutations:
            forged = copy.deepcopy(value)
            mutate(forged)
            with self.assertRaises(runner.RunnerError):
                runner.validate_pre_launch(forged, self.plan, publication)
        with self.assertRaisesRegex(runner.RunnerError, "before process start"):
            runner.validate_pre_launch(
                value,
                self.plan,
                publication,
                before_process_start_ns=publication["identity"]["mtime_ns"],
            )

    def members(self):
        pid = 100
        common_cwd = str(self.source)
        values = [
            {
                "role": "pipeline_shell_group_leader",
                "pid": pid,
                "ppid": 10,
                "pgid": pid,
                "executable": "/bin/zsh",
                "cwd_physical": common_cwd,
                "ps_command": "/bin/zsh -f -c set -o pipefail {} -j8 {}".format(
                    self.autoninja, self.plan.evidence["stdout"].physical
                ),
            },
            {
                "role": "autoninja_shell",
                "pid": 101,
                "ppid": pid,
                "pgid": pid,
                "executable": "/bin/bash",
                "cwd_physical": common_cwd,
                "ps_command": "bash {} -j8".format(self.autoninja),
            },
            {
                "role": "stdout_tee",
                "pid": 102,
                "ppid": pid,
                "pgid": pid,
                "executable": "/usr/bin/tee",
                "cwd_physical": common_cwd,
                "ps_command": "tee -a {}".format(
                    self.plan.evidence["stdout"].physical
                ),
            },
            {
                "role": "depot_python_launcher_shell",
                "pid": 103,
                "ppid": 101,
                "pgid": pid,
                "executable": "/bin/bash",
                "cwd_physical": common_cwd,
                "ps_command": "bash {}/python-bin/python3".format(self.depot),
            },
            {
                "role": "autoninja_python",
                "pid": 104,
                "ppid": 103,
                "pgid": pid,
                "executable": str(self.python),
                "cwd_physical": common_cwd,
                "ps_command": "{} {}/autoninja.py -j8".format(
                    self.python, self.depot
                ),
            },
            {
                "role": "pinned_ninja",
                "pid": 105,
                "ppid": 104,
                "pgid": pid,
                "executable": str(self.ninja),
                "cwd_physical": str(self.out),
                "ps_command": "{} -j8".format(self.ninja),
            },
            {
                "role": "ninja_caffeinate",
                "pid": 106,
                "ppid": 105,
                "pgid": pid,
                "executable": "/usr/bin/caffeinate",
                "cwd_physical": common_cwd,
                "ps_command": "caffeinate {} -j8".format(self.ninja),
            },
        ]
        for value in values:
            value["started_at_ns"] = 1000
        return values

    def test_process_spine_rejects_spoofed_executable_or_cwd(self):
        members = self.members()
        self.assertEqual(
            set(runner.EXPECTED_ROLES),
            set(runner._validate_spine(members, self.plan, 100)),
        )
        forged = copy.deepcopy(members)
        forged[5]["executable"] = "/bin/cat"
        with self.assertRaisesRegex(runner.RunnerError, "spoofed"):
            runner._validate_spine(forged, self.plan, 100)
        forged = copy.deepcopy(members)
        forged[4]["cwd_physical"] = str(self.root)
        with self.assertRaisesRegex(runner.RunnerError, "cwd was spoofed"):
            runner._validate_spine(forged, self.plan, 100)

    def test_process_spine_records_dynamic_descendants_and_proves_ninja_ancestry(self):
        members = self.members()
        dynamic = {
            "role": "dynamic_descendant",
            "pid": 107,
            "ppid": 105,
            "pgid": 100,
            "started_at_ns": 1001,
            "cwd_physical": str(self.out),
            "executable": "/usr/bin/clang",
            "ps_command": "clang fixture.cc",
        }
        members.append(dynamic)
        self.assertEqual(
            set(runner.EXPECTED_ROLES),
            set(runner._validate_spine(members, self.plan, 100)),
        )
        process = FakeProcess(100, 0, self.plan.evidence["stdout"].physical)
        with mock.patch.object(
            runner, "_stdout_live_snapshot", return_value={"bytes": 1}
        ):
            primary = runner._primary_value(
                self.plan,
                process,
                {"sha256": "a" * 64},
                members,
                {"inode": 1},
            )
        self.assertEqual(7, len(primary["process_group"]["members"]))
        self.assertEqual(
            [dynamic], primary["process_group"]["dynamic_descendants"]
        )
        forged = copy.deepcopy(members)
        forged[-1]["ppid"] = 999
        with self.assertRaisesRegex(runner.RunnerError, "does not reach pinned Ninja"):
            runner._validate_spine(forged, self.plan, 100)

    def prepare_exit_status(self, returncode=0):
        stdout = runner._create_stdout(self.plan.evidence["stdout"])
        self.plan.evidence["stdout"].physical.write_bytes(b"completed output\n")
        stdout_final = runner._freeze_stdout(
            self.plan.evidence["stdout"], stdout
        )
        process = FakeProcess(500, returncode, self.plan.evidence["stdout"].physical)
        process.waited = True
        pre = self.publish("pre_launch")
        primary = self.publish("primary")
        supplement = self.publish("supplement")
        revalidation = self.publish("revalidation")
        monitor = self.successful_monitor()
        failure = None
        outcome = "completed"
        if returncode:
            outcome = "process-exit-failure"
            failure = runner._failure_value(
                "popen-wait", runner.RunnerError("fixture nonzero exit")
            )
        value = runner._exit_status_value(
            self.plan,
            process,
            returncode,
            123456,
            pre,
            primary,
            supplement,
            revalidation,
            stdout_final,
            runner._pre_run_snapshot(self.out),
            outcome,
            failure,
            monitor,
        )
        publication = runner._publish_json_no_replace(
            self.plan.evidence["exit_status"], value, "exit status"
        )
        return process, pre, value, publication

    def test_exit_status_rejects_forged_popen_result_or_pre_link(self):
        process, pre, value, publication = self.prepare_exit_status()
        self.assertTrue(
            runner.validate_exit_status(
                value, self.plan, process, 0, pre, publication
            )
        )
        forged = copy.deepcopy(value)
        forged["wait_observation"]["returncode"] = 1
        with self.assertRaisesRegex(runner.RunnerError, "exit-status"):
            runner.validate_exit_status(
                forged, self.plan, process, 0, pre, publication
            )
        forged = copy.deepcopy(value)
        forged["pre_launch"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(runner.RunnerError, "exit-status"):
            runner.validate_exit_status(
                forged, self.plan, process, 0, pre, publication
            )

    def test_exit_status_strict_schema_rejects_bool_int_missing_and_extra_holes(self):
        process, pre, value, publication = self.prepare_exit_status()
        mutations = {
            "root bool schema": lambda item: item.__setitem__("schema", True),
            "wait bool returncode": lambda item: item["wait_observation"].__setitem__(
                "returncode", False
            ),
            "wait bool timestamp": lambda item: item["wait_observation"].__setitem__(
                "wait_returned_at_ns", True
            ),
            "monitor bool checks": lambda item: item["monitor"].__setitem__(
                "checks", False
            ),
            "monitor zero checks": lambda item: item["monitor"].__setitem__(
                "checks", 0
            ),
            "monitor bool floor": lambda item: item["monitor"].__setitem__(
                "hard_floor_bytes", True
            ),
            "nested free bool": lambda item: item["monitor"][
                "minimum_free_bytes"
            ].__setitem__("source", False),
            "absence int": lambda item: item["monitor"].__setitem__(
                "process_group_absent", 1
            ),
            "memory bool samples": lambda item: item["monitor"]["memory"].__setitem__(
                "samples", True
            ),
            "memory bool threshold": lambda item: item["monitor"]["memory"][
                "thresholds"
            ].__setitem__("swap_used_bytes", True),
            "memory missing last": lambda item: item["monitor"]["memory"].pop(
                "last"
            ),
            "success int": lambda item: item.__setitem__(
                "pipeline_success_derived", 1
            ),
            "network bool": lambda item: item.__setitem__(
                "network_operations", False
            ),
            "stdout nested bool": lambda item: item["stdout_log"].__setitem__(
                "bytes", True
            ),
            "post-run nested bool": lambda item: item["post_run"][
                "ninja_log"
            ].__setitem__("bytes", True),
            "missing nested": lambda item: item["wait_observation"].pop("api"),
            "extra nested": lambda item: item["monitor"].__setitem__(
                "unexpected", 0
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                forged = copy.deepcopy(value)
                mutate(forged)
                with self.assertRaises(runner.RunnerError):
                    runner.validate_exit_status(
                        forged, self.plan, process, 0, pre, publication
                    )

    def test_failure_status_rejects_nested_type_missing_and_extra_mutations(self):
        process, pre, value, publication = self.prepare_exit_status(returncode=9)
        mutations = (
            lambda item: item["failure"]["primary"].__setitem__("message", 1),
            lambda item: item["failure"]["primary"].pop("stage"),
            lambda item: item["failure"]["primary"].__setitem__("extra", False),
            lambda item: item["failure"].__setitem__("secondary", False),
        )
        for mutate in mutations:
            forged = copy.deepcopy(value)
            mutate(forged)
            with self.assertRaises(runner.RunnerError):
                runner.validate_exit_status(
                    forged, self.plan, process, 9, pre, publication
                )

    def test_partial_observation_failure_status_is_immutable_and_valid(self):
        initial = runner._create_stdout(self.plan.evidence["stdout"])
        stdout_final = runner._freeze_stdout(
            self.plan.evidence["stdout"], initial, require_nonempty=False
        )
        process = FakeProcess(503, -15, self.plan.evidence["stdout"].physical)
        process.waited = True
        pre = self.publish("pre_launch")
        value = runner._exit_status_value(
            self.plan,
            process,
            -15,
            123456,
            pre,
            None,
            None,
            None,
            stdout_final,
            runner._pre_run_snapshot(self.out),
            "observation-error",
            runner._failure_value(
                "primary-live-observation", runner.RunnerError("fixture failure")
            ),
            runner._empty_monitor_report(self.plan, True),
        )
        publication = runner._publish_json_no_replace(
            self.plan.evidence["exit_status"], value, "failure exit status"
        )
        self.assertTrue(
            runner.validate_exit_status(
                value, self.plan, process, -15, pre, publication
            )
        )
        self.assertFalse(value["evidence_complete"])
        self.assertFalse(value["pipeline_success_derived"])
        self.assertEqual(0, publication["identity"]["mode"] & 0o222)

    def test_monitor_hard_floor_stops_owned_process_and_records_exact_roots(self):
        initial = runner._create_stdout(self.plan.evidence["stdout"])
        process = FakeProcess(610, -15, self.plan.evidence["stdout"].physical)
        hard = runner.build_pipeline.HARD_FLOOR_GIB * runner.build_pipeline.GIB
        with mock.patch.object(
            runner, "_stop_process_group", side_effect=lambda observed: observed.wait()
        ) as stop, mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ) as absent:
            result = runner._monitored_wait(
                self.plan,
                process,
                initial,
                free_probe=lambda _path: hard - 1,
                memory_probe=self.memory_sample,
                poll_seconds=0,
            )
        stop.assert_called_once_with(process)
        absent.assert_called_once_with(process.pid, 5)
        self.assertEqual("disk-hard-floor-abort", result["outcome"])
        self.assertEqual(-15, result["returncode"])
        self.assertEqual(
            {"source": hard - 1, "logs": hard - 1},
            result["monitor"]["minimum_free_bytes"],
        )
        self.assertEqual(str(self.source), result["monitor"]["source_path"])
        self.assertEqual(str(self.logs), result["monitor"]["logs_path"])

    def test_monitor_stdout_bound_aborts_before_normal_completion(self):
        initial = runner._create_stdout(self.plan.evidence["stdout"])
        os.truncate(
            self.plan.evidence["stdout"].physical,
            runner.MAX_STDOUT_BYTES + 1,
        )
        process = FakeProcess(611, -9, self.plan.evidence["stdout"].physical)
        with mock.patch.object(
            runner, "_stop_process_group", side_effect=lambda observed: observed.wait()
        ) as stop, mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ):
            result = runner._monitored_wait(
                self.plan,
                process,
                initial,
                free_probe=lambda _path: 100 * runner.build_pipeline.GIB,
                memory_probe=self.memory_sample,
                poll_seconds=0,
            )
        stop.assert_called_once_with(process)
        self.assertEqual("stdout-bound-abort", result["outcome"])
        self.assertIn("StdoutBoundAbort", result["failure"])
        self.assertEqual(
            runner.MAX_STDOUT_BYTES + 1,
            result["monitor"]["maximum_stdout_bytes"],
        )

    def test_monitor_observation_error_stops_group_and_preserves_error(self):
        initial = runner._create_stdout(self.plan.evidence["stdout"])
        process = FakeProcess(614, -15, self.plan.evidence["stdout"].physical)

        def failed_probe(_path):
            raise OSError("fixture free-space observer failed")

        with mock.patch.object(
            runner, "_stop_process_group", side_effect=lambda observed: observed.wait()
        ) as stop, mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ):
            result = runner._monitored_wait(
                self.plan,
                process,
                initial,
                free_probe=failed_probe,
                memory_probe=self.memory_sample,
                poll_seconds=0,
            )
        stop.assert_called_once_with(process)
        self.assertEqual("monitor-observation-abort", result["outcome"])
        self.assertIn("fixture free-space observer failed", result["failure"])

    def test_memory_probe_parses_exact_units_and_rejects_inconsistent_output(self):
        pressure = (
            "The system has 17179869184 (1048576 pages with a page size of 16384).\n"
            "System-wide memory free percentage: 76%\n"
        )
        swap = (
            "total = 1024.00M  used = 73.44M  free = 950.56M  (encrypted)\n"
        )
        with mock.patch.object(
            runner, "_bounded_memory_capture", side_effect=[pressure, swap]
        ):
            observed = runner._memory_pressure_snapshot()
        self.assertEqual(17179869184, observed["memory_total_bytes"])
        self.assertEqual(76, observed["free_percent"])
        self.assertEqual(1024 ** 3, observed["swap_total_bytes"])
        self.assertEqual(
            runner._swap_quantity_bytes("73.44", "M"),
            observed["swap_used_bytes"],
        )
        with mock.patch.object(
            runner,
            "_bounded_memory_capture",
            side_effect=[pressure.replace("1048576", "1"), swap],
        ), self.assertRaisesRegex(runner.RunnerError, "inconsistent"):
            runner._memory_pressure_snapshot()

    def test_memory_pressure_immediate_gate_stops_owned_process(self):
        initial = runner._create_stdout(self.plan.evidence["stdout"])
        process = FakeProcess(616, -15, self.plan.evidence["stdout"].physical)
        with mock.patch.object(
            runner, "_stop_process_group", side_effect=lambda observed: observed.wait()
        ) as stop, mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ):
            result = runner._monitored_wait(
                self.plan,
                process,
                initial,
                free_probe=lambda _path: 100 * runner.build_pipeline.GIB,
                memory_probe=lambda: self.memory_sample(free_percent=5),
                poll_seconds=0,
            )
        stop.assert_called_once_with(process)
        self.assertEqual("memory-pressure-abort", result["outcome"])
        self.assertEqual(1, result["monitor"]["memory"]["samples"])
        self.assertEqual(
            5, result["monitor"]["memory"]["minimum_free_percent"]
        )

    def test_sustained_memory_pressure_requires_three_samples(self):
        initial = runner._create_stdout(self.plan.evidence["stdout"])
        process = FakeProcess(617, -15, self.plan.evidence["stdout"].physical)
        samples = iter(
            (
                self.memory_sample(free_percent=9),
                self.memory_sample(free_percent=9),
                self.memory_sample(free_percent=9),
            )
        )
        with mock.patch.object(
            runner, "_stop_process_group", side_effect=lambda observed: observed.wait()
        ), mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ):
            result = runner._monitored_wait(
                self.plan,
                process,
                initial,
                free_probe=lambda _path: 100 * runner.build_pipeline.GIB,
                memory_probe=lambda: next(samples),
                poll_seconds=0,
            )
        self.assertEqual("memory-pressure-abort", result["outcome"])
        self.assertEqual(3, result["monitor"]["memory"]["samples"])
        self.assertEqual(
            3,
            result["monitor"]["memory"][
                "maximum_critical_free_consecutive"
            ],
        )

    def test_normal_monitor_wait_proves_process_group_absence(self):
        initial = runner._create_stdout(self.plan.evidence["stdout"])
        process = FakeProcess(612, 0, self.plan.evidence["stdout"].physical)
        process.waited = True
        with mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ) as exists, mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ) as absent:
            result = runner._monitored_wait(
                self.plan,
                process,
                initial,
                free_probe=lambda _path: 100 * runner.build_pipeline.GIB,
                memory_probe=self.memory_sample,
                poll_seconds=0,
            )
        exists.assert_called_once_with(process.pid)
        absent.assert_called_once_with(process.pid, 5)
        self.assertEqual("completed", result["outcome"])
        self.assertTrue(result["monitor"]["process_group_absent"])
        self.assertEqual(2, result["monitor"]["checks"])
        self.assertEqual(
            {"source": 100 * runner.build_pipeline.GIB,
             "logs": 100 * runner.build_pipeline.GIB},
            result["monitor"]["last_free_bytes"],
        )

    def test_final_probe_can_reject_zero_exit_after_disk_crossing(self):
        initial = runner._create_stdout(self.plan.evidence["stdout"])
        process = FakeProcess(615, 0, self.plan.evidence["stdout"].physical)
        process.waited = True
        hard = runner.build_pipeline.HARD_FLOOR_GIB * runner.build_pipeline.GIB
        observations = iter((100 * runner.build_pipeline.GIB,) * 2 + (hard - 1, hard + 5))
        with mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ), mock.patch.object(runner, "_stop_process_group") as stop:
            result = runner._monitored_wait(
                self.plan,
                process,
                initial,
                free_probe=lambda _path: next(observations),
                memory_probe=self.memory_sample,
                poll_seconds=0,
            )
        stop.assert_not_called()
        self.assertEqual("disk-hard-floor-abort", result["outcome"])
        self.assertEqual(0, result["returncode"])
        self.assertEqual(2, result["monitor"]["checks"])
        self.assertEqual(hard - 1, result["monitor"]["last_free_bytes"]["source"])

    def test_stop_process_group_targets_exact_pgid_with_term_then_kill(self):
        path = self.root / "stop-fixture.log"
        path.write_bytes(b"")
        process = FakeProcess(613, -9, path)
        with mock.patch.object(
            runner.build_pipeline,
            "_process_group_exists",
            side_effect=[True, True, False],
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ), mock.patch.object(
            runner.time, "monotonic", side_effect=[0, 11]
        ), mock.patch.object(runner.time, "sleep"), mock.patch.object(
            runner.os, "killpg"
        ) as killpg:
            self.assertEqual(-9, runner._stop_process_group(process))
        self.assertEqual(
            [
                mock.call(process.pid, runner.signal.SIGTERM),
                mock.call(process.pid, runner.signal.SIGKILL),
            ],
            killpg.call_args_list,
        )
    def execute_patches(self, process):
        primary = {
            "schema": 2,
            "kind": "primary",
            "observed_at_ns": 2000,
            "process_group": {"pgid": process.pid, "members": []},
        }
        supplement = {"schema": 2, "kind": "supplement"}
        revalidation = {"schema": 2, "kind": "revalidation"}

        def monitored(_plan, observed_process, _stdout_initial):
            returncode = observed_process.wait()
            result = {
                "outcome": "completed",
                "returncode": returncode,
                "wait_returned_at_ns": time.time_ns(),
                "failure": None,
                "monitor": self.successful_monitor(),
            }
            return result

        return (
            mock.patch.object(runner, "create_plan", return_value=self.plan),
            mock.patch.object(runner.build_pipeline, "require_free"),
            mock.patch.object(runner, "_wait_until_pre_launch_is_historical"),
            mock.patch.object(runner, "_launch", return_value=process),
            mock.patch.object(runner.os, "getpgid", return_value=process.pid),
            mock.patch.object(
                runner, "_ps_start_ns", side_effect=lambda _pid: time.time_ns() + 1_000_000_000
            ),
            mock.patch.object(runner, "_capture_spine", return_value=[]),
            mock.patch.object(runner, "_primary_value", return_value=primary),
            mock.patch.object(runner, "_supplement_value", return_value=supplement),
            mock.patch.object(
                runner, "_revalidation_value", return_value=revalidation
            ),
            mock.patch.object(runner, "_monitored_wait", side_effect=monitored),
        )

    def test_nonzero_owned_popen_status_never_emits_final_success(self):
        process = FakeProcess(
            700, 9, self.plan.evidence["stdout"].physical
        )
        patches = self.execute_patches(process)
        with _patch_stack(patches):
            with self.assertRaisesRegex(runner.RunnerError, "no final success"):
                runner.execute(self.plan, True, True)
        self.assertTrue(self.plan.evidence["exit_status"].physical.is_file())
        self.assertFalse(self.plan.evidence["final"].physical.exists())
        status = json.loads(self.plan.evidence["exit_status"].physical.read_text())
        self.assertEqual(9, status["wait_observation"]["returncode"])
        self.assertFalse(status["pipeline_success_derived"])

    def test_zero_owned_popen_status_emits_schema3_and_calls_validator(self):
        process = FakeProcess(
            701, 0, self.plan.evidence["stdout"].physical
        )
        patches = self.execute_patches(process)
        validation = {"accepted": True}
        with _patch_stack(patches), mock.patch.object(
            runner.build_pipeline,
            "resume_execution_record_contract",
            return_value=validation,
        ) as validator:
            result = runner.execute(self.plan, True, True)
        self.assertEqual(validation, result["validated"])
        final = json.loads(self.plan.evidence["final"].physical.read_text())
        self.assertEqual(3, final["schema"])
        self.assertEqual(0, final["completion"]["wrapper_exit_code"])
        self.assertIn("pre_launch", final)
        self.assertIn("exit_status", final)
        validator.assert_called_once()

    def test_zero_execute_is_accepted_by_real_public_schema3_validator(self):
        process = FakeProcess(709, 0, self.plan.evidence["stdout"].physical)
        volume_uuid = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
        self.plan.alias_receipt["volume"] = {"volume_uuid": volume_uuid}
        workspace_stat = self.workspace.stat()
        self.plan.alias_receipt["mappings"]["workspace"] = {
            "logical": str(self.workspace),
            "physical": str(self.workspace),
            "identity": {
                "device": workspace_stat.st_dev,
                "inode": workspace_stat.st_ino,
            },
        }
        leader_start = []

        def observed_start(_pid):
            value = time.time_ns()
            leader_start.append(value)
            return value

        def live_members(_plan, _process):
            if not self.plan.evidence["stdout"].physical.stat().st_size:
                with self.plan.evidence["stdout"].physical.open("ab") as stream:
                    stream.write(b"live integration output\n")
            members = self.members()
            started = leader_start[-1]
            pid_map = {
                100: process.pid,
                101: process.pid + 1,
                102: process.pid + 2,
                103: process.pid + 3,
                104: process.pid + 4,
                105: process.pid + 5,
                106: process.pid + 6,
            }
            for member in members:
                member["pid"] = pid_map[member["pid"]]
                member["ppid"] = pid_map.get(member["ppid"], member["ppid"])
                member["pgid"] = process.pid
                member["started_at_ns"] = started
                snapshot = runner._snapshot_regular(
                    Path(member["executable"]), "integration executable"
                )
                member.update(
                    {
                        "executable_bytes": snapshot["bytes"],
                        "executable_inode": snapshot["inode"],
                        "executable_sha256": snapshot["sha256"],
                    }
                )
            dynamic_executable = Path("/bin/sh")
            dynamic_snapshot = runner._snapshot_regular(
                dynamic_executable, "integration dynamic executable"
            )
            members.append(
                {
                    "role": "dynamic_descendant",
                    "pid": process.pid + 7,
                    "ppid": process.pid + 5,
                    "pgid": process.pid,
                    "ps_command": "/bin/sh compiler-wrapper",
                    "started_at_ns": started,
                    "cwd_physical": str(self.out),
                    "executable": str(dynamic_executable),
                    "executable_bytes": dynamic_snapshot["bytes"],
                    "executable_inode": dynamic_snapshot["inode"],
                    "executable_sha256": dynamic_snapshot["sha256"],
                }
            )
            return members

        python_bin = (
            self.source.parent
            / "depot_tools/python-bin/.."
            / runner.build_pipeline.PACKAGING_PYTHON_RELDIR
        )
        observed_path = os.pathsep.join(
            (
                str(python_bin),
                str(python_bin / "Scripts"),
                self.plan.environment["PATH"],
            )
        )
        environment_tokens = [
            "{}={}".format(name, self.plan.environment[name])
            for name in runner.BASE_ENVIRONMENT_ORDER
            if name != "PATH"
        ]
        ps_eww = "fixture PATH={} PWD={} {}".format(
            observed_path,
            self.source,
            " ".join(environment_tokens),
        )

        def monitored(_plan, observed_process, _stdout_initial):
            with (self.out / ".ninja_log").open("ab") as stream:
                stream.write(b"1\t2\t3\tintegration\thash\n")
            returncode = observed_process.wait()
            return {
                "outcome": "completed",
                "returncode": returncode,
                "wait_returned_at_ns": time.time_ns(),
                "failure": None,
                "monitor": self.successful_monitor(),
            }

        patches = (
            mock.patch.object(runner, "create_plan", return_value=self.plan),
            mock.patch.object(runner.build_pipeline, "require_free"),
            mock.patch.object(runner, "_wait_until_pre_launch_is_historical"),
            mock.patch.object(runner, "_launch", return_value=process),
            mock.patch.object(runner.os, "getpgid", return_value=process.pid),
            mock.patch.object(runner, "_ps_start_ns", side_effect=observed_start),
            mock.patch.object(runner, "_capture_spine", side_effect=live_members),
            mock.patch.object(runner, "_ps_eww", return_value=ps_eww),
            mock.patch.object(runner, "_monitored_wait", side_effect=monitored),
            mock.patch.object(
                runner.build_pipeline,
                "_volume_identity",
                return_value={"volume_uuid": volume_uuid},
            ),
            mock.patch.object(
                runner.build_pipeline, "_process_group_exists", return_value=False
            ),
        )
        with _patch_stack(patches):
            result = runner.execute(self.plan, True, True)
        self.assertTrue(result["validated"]["pipeline_success_derived"])
        self.assertEqual(0, result["validated"]["wrapper_exit_code"])
        self.assertTrue(self.plan.evidence["final"].physical.is_file())
        for name, suffix in runner.EVIDENCE_SUFFIXES.items():
            self.assertEqual(
                runner.RUN_STEM + suffix, self.plan.evidence[name].logical.name
            )

        validator_arguments = (
            self.plan.evidence["final"].logical,
            self.plan.alias_receipt,
            self.source,
            self.developer,
            runner.ARCHITECTURE,
            self.out,
            self.ninja_contract,
        )

        def validate(process_group_exists=False):
            with mock.patch.object(
                runner.build_pipeline,
                "_volume_identity",
                return_value={"volume_uuid": volume_uuid},
            ), mock.patch.object(
                runner.build_pipeline,
                "_process_group_exists",
                return_value=process_group_exists,
            ):
                return runner.build_pipeline.resume_execution_record_contract(
                    *validator_arguments
                )

        real_load_json = runner.build_pipeline.load_json
        with mock.patch.object(
            runner.build_pipeline, "load_json", wraps=real_load_json
        ) as legacy_loader:
            self.assertTrue(validate()["pipeline_success_derived"])
        self.assertEqual(
            [],
            legacy_loader.call_args_list,
            "schema3 final and linked evidence must use descriptor-bound reads",
        )

        mutable_names = ("pre_launch", "exit_status", "final")
        originals = {
            name: {
                "bytes": self.plan.evidence[name].physical.read_bytes(),
                "stat": self.plan.evidence[name].physical.stat(),
            }
            for name in mutable_names
        }

        def rewrite(name, data):
            path = self.plan.evidence[name].physical
            path.chmod(0o644)
            path.write_bytes(data)
            observed = originals[name]["stat"]
            os.utime(path, ns=(observed.st_atime_ns, observed.st_mtime_ns))
            path.chmod(0o444)

        def restore_all():
            for name in mutable_names:
                rewrite(name, originals[name]["bytes"])

        def reject_mutation(name, final_link, mutate, pattern):
            restore_all()
            value = json.loads(originals[name]["bytes"])
            mutate(value)
            changed = runner._canonical_bytes(value)
            rewrite(name, changed)
            if final_link is not None:
                final = json.loads(originals["final"]["bytes"])
                final[final_link]["sha256"] = runner._sha256_bytes(changed)
                rewrite("final", runner._canonical_bytes(final))
            with self.assertRaisesRegex(runner.build_pipeline.PipelineError, pattern):
                validate()

        reject_mutation(
            "final",
            None,
            lambda value: value["completion"].__setitem__(
                "wrapper_exit_code", False
            ),
            "completion/status derivation",
        )
        reject_mutation(
            "pre_launch",
            "pre_launch",
            lambda value: value["planned_process"]["shell_argv"].__setitem__(
                3, value["planned_process"]["shell_argv"][3] + "\n/bin/true"
            ),
            "pre-launch provenance",
        )
        reject_mutation(
            "exit_status",
            "exit_status",
            lambda value: value["monitor"].__setitem__(
                "maximum_stdout_bytes", 0
            ),
            "final stdout identity/prefix",
        )
        reject_mutation(
            "exit_status",
            "exit_status",
            lambda value: value["monitor"]["memory"].__setitem__(
                "samples", value["monitor"]["checks"] + 1
            ),
            "memory aggregate proof",
        )
        restore_all()
        with self.assertRaisesRegex(
            runner.build_pipeline.PipelineError, "process group still exists"
        ):
            validate(process_group_exists=True)
        restore_all()

    def test_final_double_rejection_rolls_back_only_published_inode(self):
        publication = runner._publish_json_no_replace(
            self.plan.evidence["final"], {"schema": 3}, "final"
        )
        error = runner.build_pipeline.PipelineError
        with mock.patch.object(
            runner.build_pipeline,
            "resume_execution_record_contract",
            side_effect=[error("first"), error("second")],
        ) as validator:
            with self.assertRaisesRegex(runner.RunnerError, "rolled back"):
                runner._validate_final_or_remove(self.plan, publication)
        self.assertEqual(2, validator.call_count)
        self.assertFalse(self.plan.evidence["final"].physical.exists())

    def test_final_transient_rejection_revalidates_and_preserves_valid_record(self):
        publication = runner._publish_json_no_replace(
            self.plan.evidence["final"], {"schema": 3}, "final"
        )
        accepted = {"accepted": True}
        with mock.patch.object(
            runner.build_pipeline,
            "resume_execution_record_contract",
            side_effect=[runner.build_pipeline.PipelineError("transient"), accepted],
        ) as validator:
            self.assertEqual(
                accepted,
                runner._validate_final_or_remove(self.plan, publication),
            )
        self.assertEqual(2, validator.call_count)
        self.assertTrue(self.plan.evidence["final"].physical.is_file())

    def test_final_rollback_refuses_to_unlink_replacement_inode(self):
        publication = runner._publish_json_no_replace(
            self.plan.evidence["final"], {"schema": 3}, "final"
        )
        self.plan.evidence["final"].physical.unlink()
        self.plan.evidence["final"].physical.write_bytes(b"rival\n")
        with mock.patch.object(
            runner.build_pipeline,
            "resume_execution_record_contract",
            side_effect=[
                runner.build_pipeline.PipelineError("first"),
                runner.build_pipeline.PipelineError("second"),
            ],
        ):
            with self.assertRaisesRegex(runner.RunnerError, "changed before unlink"):
                runner._validate_final_or_remove(self.plan, publication)
        self.assertEqual(
            b"rival\n", self.plan.evidence["final"].physical.read_bytes()
        )

    def test_execute_runtime_abort_emits_failure_status_and_no_final(self):
        process = FakeProcess(704, -15, self.plan.evidence["stdout"].physical)
        patches = list(self.execute_patches(process))

        def runtime_abort(_plan, observed_process, _stdout_initial):
            returncode = observed_process.wait()
            return {
                "outcome": "disk-hard-floor-abort",
                "returncode": returncode,
                "wait_returned_at_ns": time.time_ns(),
                "failure": "free bytes crossed the exact hard floor",
                "monitor": runner._empty_monitor_report(self.plan, True),
            }

        patches[-1] = mock.patch.object(
            runner, "_monitored_wait", side_effect=runtime_abort
        )
        with _patch_stack(patches):
            with self.assertRaisesRegex(runner.RunnerError, "disk-hard-floor-abort"):
                runner.execute(self.plan, True, True)
        status = json.loads(
            self.plan.evidence["exit_status"].physical.read_text()
        )
        self.assertEqual("disk-hard-floor-abort", status["outcome"])
        self.assertFalse(status["pipeline_success_derived"])
        self.assertTrue(status["evidence_complete"])
        self.assertEqual(0, self.plan.evidence["exit_status"].physical.stat().st_mode & 0o222)
        self.assertFalse(self.plan.evidence["final"].physical.exists())

    def test_execute_observation_error_emits_status_before_raising(self):
        process = FakeProcess(705, -15, self.plan.evidence["stdout"].physical)
        patches = list(self.execute_patches(process))
        patches[6] = mock.patch.object(
            runner,
            "_capture_spine",
            side_effect=runner.RunnerError("fixture spine observation failed"),
        )
        with _patch_stack(patches), mock.patch.object(
            runner, "_stop_process_group", side_effect=lambda observed: observed.wait()
        ), mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ):
            with self.assertRaisesRegex(runner.RunnerError, "observation-error"):
                runner.execute(self.plan, True, True)
        status = json.loads(
            self.plan.evidence["exit_status"].physical.read_text()
        )
        self.assertEqual("observation-error", status["outcome"])
        self.assertEqual(
            "fixture spine observation failed",
            status["failure"]["primary"]["message"],
        )
        self.assertFalse(status["evidence_complete"])
        self.assertFalse(self.plan.evidence["final"].physical.exists())

    def test_interruption_stops_owned_group_and_never_emits_final(self):
        process = FakeProcess(
            702, -15, self.plan.evidence["stdout"].physical
        )
        patches = list(self.execute_patches(process))
        patches[6] = mock.patch.object(
            runner, "_capture_spine", side_effect=KeyboardInterrupt("fixture")
        )
        with _patch_stack(patches), mock.patch.object(
            runner, "_stop_process_group", side_effect=lambda observed: observed.wait()
        ) as stop, mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ):
            with self.assertRaises(KeyboardInterrupt):
                runner.execute(self.plan, True, True)
        stop.assert_called_once_with(process)
        status = json.loads(self.plan.evidence["exit_status"].physical.read_text())
        self.assertEqual("interrupted", status["outcome"])
        self.assertFalse(status["evidence_complete"])
        self.assertEqual("KeyboardInterrupt", status["failure"]["primary"]["type"])
        self.assertFalse(self.plan.evidence["final"].physical.exists())

    def test_first_sigterm_during_error_settlement_cannot_escape_cleanup(self):
        process = FakeProcess(703, -15, self.plan.evidence["stdout"].physical)
        patches = list(self.execute_patches(process))
        patches[6] = mock.patch.object(
            runner,
            "_capture_spine",
            side_effect=RuntimeError("fixture observation failed before settlement"),
        )
        group_absent = {"value": False}
        original_mask = runner.signal.pthread_sigmask(runner.signal.SIG_BLOCK, set())
        before_handlers = {
            signum: runner.signal.getsignal(signum)
            for signum in runner.CONTROLLED_SIGNALS
        }

        def signal_while_stopping(observed):
            os.kill(os.getpid(), runner.signal.SIGTERM)
            returncode = observed.wait()
            group_absent["value"] = True
            return returncode

        with _patch_stack(patches), mock.patch.object(
            runner, "_stop_process_group", side_effect=signal_while_stopping
        ) as stop, mock.patch.object(
            runner.build_pipeline,
            "_process_group_exists",
            side_effect=lambda _pgid: not group_absent["value"],
        ), mock.patch.object(
            runner.build_pipeline,
            "_wait_process_group_absent",
            side_effect=lambda _pgid, _timeout: group_absent["value"],
        ):
            with self.assertRaisesRegex(runner.RunnerError, "interrupted"):
                runner.execute(self.plan, True, True)

        stop.assert_called_once_with(process)
        self.assertTrue(group_absent["value"])
        status_path = self.plan.evidence["exit_status"].physical
        status = json.loads(status_path.read_text())
        self.assertEqual("interrupted", status["outcome"])
        self.assertTrue(status["monitor"]["process_group_absent"])
        self.assertEqual(
            "signal-SIGTERM", status["failure"]["primary"]["stage"]
        )
        self.assertEqual(
            "ControlledTermination", status["failure"]["primary"]["type"]
        )
        self.assertEqual(
            "primary-live-observation",
            status["failure"]["secondary"][0]["stage"],
        )
        self.assertEqual(
            "RuntimeError", status["failure"]["secondary"][0]["type"]
        )
        self.assertEqual(0, stat.S_IMODE(status_path.stat().st_mode) & 0o222)
        self.assertFalse(self.plan.evidence["final"].physical.exists())
        self.assertEqual(
            original_mask,
            runner.signal.pthread_sigmask(runner.signal.SIG_BLOCK, set()),
        )
        self.assertEqual(
            before_handlers,
            {
                signum: runner.signal.getsignal(signum)
                for signum in runner.CONTROLLED_SIGNALS
            },
        )

    def test_owned_signal_handlers_cover_hup_and_restore_exact_prior_handlers(self):
        before = {
            signum: runner.signal.getsignal(signum)
            for signum in (runner.signal.SIGTERM, runner.signal.SIGHUP)
        }
        previous, state = runner._install_owned_signal_handlers()
        try:
            handler = runner.signal.getsignal(runner.signal.SIGHUP)
            with self.assertRaises(runner.ControlledTermination) as raised:
                handler(runner.signal.SIGHUP, None)
            self.assertEqual(runner.signal.SIGHUP, raised.exception.signum)
            self.assertEqual(runner.signal.SIGHUP, state["signum"])
            # A repeated controlled signal is deliberately ignored while the
            # exact process group is being settled.
            handler(runner.signal.SIGTERM, None)
        finally:
            runner._restore_owned_signal_handlers(previous)
        self.assertEqual(
            before,
            {
                signum: runner.signal.getsignal(signum)
                for signum in (runner.signal.SIGTERM, runner.signal.SIGHUP)
            },
        )

    def test_sigterm_after_popen_settles_group_and_publishes_interrupted_status(self):
        process = FakeProcess(706, -15, self.plan.evidence["stdout"].physical)
        patches = self.execute_patches(process)
        before = {
            signum: runner.signal.getsignal(signum)
            for signum in (runner.signal.SIGTERM, runner.signal.SIGHUP)
        }

        def terminate_after_popen(phase, _subject, _value, _total):
            if phase == "after-popen":
                os.kill(os.getpid(), runner.signal.SIGTERM)

        with _patch_stack(patches), mock.patch.object(
            runner, "_stop_process_group", side_effect=lambda observed: observed.wait()
        ) as stop, mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ):
            with self.assertRaisesRegex(runner.RunnerError, "interrupted"):
                runner.execute(
                    self.plan,
                    True,
                    True,
                    test_hook=terminate_after_popen,
                )
        stop.assert_called_once_with(process)
        status = json.loads(
            self.plan.evidence["exit_status"].physical.read_text()
        )
        self.assertEqual("interrupted", status["outcome"])
        self.assertEqual(
            "signal-SIGTERM", status["failure"]["primary"]["stage"]
        )
        self.assertEqual(
            "ControlledTermination", status["failure"]["primary"]["type"]
        )
        self.assertFalse(self.plan.evidence["final"].physical.exists())
        self.assertEqual(
            before,
            {
                signum: runner.signal.getsignal(signum)
                for signum in (runner.signal.SIGTERM, runner.signal.SIGHUP)
            },
        )

    def test_pending_sigterm_inside_launch_is_delivered_only_after_process_assignment(self):
        process = FakeProcess(707, -15, self.plan.evidence["stdout"].physical)
        patches = list(self.execute_patches(process))

        def launch_with_pending_signal(_plan):
            os.kill(os.getpid(), runner.signal.SIGTERM)
            return process

        patches[3] = mock.patch.object(
            runner, "_launch", side_effect=launch_with_pending_signal
        )
        original_mask = runner.signal.pthread_sigmask(runner.signal.SIG_BLOCK, set())
        before_handlers = {
            signum: runner.signal.getsignal(signum)
            for signum in (runner.signal.SIGTERM, runner.signal.SIGHUP)
        }
        with _patch_stack(patches), mock.patch.object(
            runner, "_stop_process_group", side_effect=lambda observed: observed.wait()
        ) as stop, mock.patch.object(
            runner.build_pipeline, "_process_group_exists", return_value=False
        ), mock.patch.object(
            runner.build_pipeline, "_wait_process_group_absent", return_value=True
        ):
            with self.assertRaisesRegex(runner.RunnerError, "interrupted"):
                runner.execute(self.plan, True, True)
        stop.assert_called_once_with(process)
        status = json.loads(
            self.plan.evidence["exit_status"].physical.read_text()
        )
        self.assertEqual("interrupted", status["outcome"])
        self.assertEqual(
            "signal-SIGTERM", status["failure"]["primary"]["stage"]
        )
        self.assertEqual(
            original_mask,
            runner.signal.pthread_sigmask(runner.signal.SIG_BLOCK, set()),
        )
        self.assertEqual(
            before_handlers,
            {
                signum: runner.signal.getsignal(signum)
                for signum in (runner.signal.SIGTERM, runner.signal.SIGHUP)
            },
        )

    def test_signal_after_group_settle_invalidates_success_before_status_publication(self):
        process = FakeProcess(708, 0, self.plan.evidence["stdout"].physical)
        patches = self.execute_patches(process)

        def signal_after_settle(phase, _subject, _value, _total):
            if phase == "after-process-settled":
                os.kill(os.getpid(), runner.signal.SIGHUP)

        with _patch_stack(patches):
            with self.assertRaisesRegex(runner.RunnerError, "interrupted"):
                runner.execute(
                    self.plan,
                    True,
                    True,
                    test_hook=signal_after_settle,
                )
        status = json.loads(
            self.plan.evidence["exit_status"].physical.read_text()
        )
        self.assertEqual("interrupted", status["outcome"])
        self.assertEqual(
            "signal-SIGHUP", status["failure"]["primary"]["stage"]
        )
        self.assertFalse(status["pipeline_success_derived"])
        self.assertFalse(self.plan.evidence["final"].physical.exists())


class _patch_stack:
    def __init__(self, patches):
        self.patches = patches
        self.active = []

    def __enter__(self):
        for patcher in self.patches:
            self.active.append(patcher.start())
        return self.active

    def __exit__(self, exc_type, exc, traceback):
        for patcher in reversed(self.patches):
            patcher.stop()
        return False


if __name__ == "__main__":
    unittest.main()
