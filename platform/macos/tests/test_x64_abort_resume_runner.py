#!/usr/bin/env python3
"""Focused adversarial tests for the x64 memory-safe continuation runner."""

import copy
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

import x64_abort_resume_runner as runner


class X64AbortResumeRunnerTests(unittest.TestCase):
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
            (".ninja_log", b"# ninja log after memory abort\n"),
            (".ninja_deps", b"ninja deps after memory abort\n"),
            ("build.ninja", b"subninja toolchain.ninja\n"),
            ("toolchain.ninja", b"rule fixture\n"),
        ):
            (self.out / name).write_bytes(data)
        self.depot = self.checkout / "depot_tools"
        self.depot.mkdir()
        self.autoninja = self.depot / "autoninja"
        self.autoninja.write_text("#!/bin/bash\n", encoding="utf-8")
        self.autoninja.chmod(0o755)
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
        self.context = types.SimpleNamespace(
            logical_workspace=self.workspace,
            physical_workspace=self.workspace,
            physical_source=self.source,
            physical_developer=self.developer,
        )
        self.ninja_contract = {
            "path": str(self.ninja),
            "sha256": runner._snapshot_regular(self.ninja, "ninja")["sha256"],
            "architecture": "arm64",
        }
        self.prior_post_run = runner._pre_run_snapshot(self.out)
        self.fresh = {
            "receipt": {
                "path": str(self.source / "fresh-x64.json"),
                "bytes": 123,
                "sha256": "a" * 64,
            },
            "contract_sha256": "b" * 64,
        }
        self.prior_exit = self.logs / runner.PRIOR_EXIT_BASENAME
        prior_pre = self.logs / (
            runner.PRIOR_EXIT_BASENAME[: -len(runner.EVIDENCE_SUFFIXES["exit_status"])]
            + runner.EVIDENCE_SUFFIXES["pre_launch"]
        )
        pre_value = {"fresh_x64_preparation": self.fresh}
        pre_bytes = runner._canonical_bytes(pre_value)
        prior_pre.write_bytes(pre_bytes)
        prior_pre.chmod(0o444)
        self.prior_value = {
            "pre_launch": {
                "path": str(prior_pre),
                "sha256": runner._sha256_bytes(pre_bytes),
            },
            "post_run": self.prior_post_run,
        }
        self.prior_exit.write_bytes(runner._canonical_bytes(self.prior_value))
        self.prior_exit.chmod(0o444)

    def tearDown(self):
        self.temporary.cleanup()

    def create_plan(self):
        with mock.patch.object(runner.sys, "platform", "darwin"), mock.patch.object(
            runner, "PRIOR_EXIT_PATH", self.prior_exit
        ), mock.patch.object(
            runner.build_pipeline, "resolve_source", return_value=self.source
        ), mock.patch.object(
            runner.build_pipeline,
            "home_alias_receipt_contract",
            return_value=(self.source / "HomeAlias.json", self.alias_receipt),
        ), mock.patch.object(
            runner.build_pipeline, "_recorded_alias_context", return_value=self.context
        ), mock.patch.object(
            runner.build_pipeline,
            "x64_memory_abort_resume_contract",
            return_value=(self.prior_exit, self.prior_value),
        ) as abort_contract, mock.patch.object(
            runner.build_pipeline,
            "fresh_x64_preparation_contract",
            create=True,
        ) as forbidden_fresh, mock.patch.object(
            runner.build_pipeline, "ninja_contract", return_value=self.ninja_contract
        ):
            plan = runner.create_plan(self.source, self.developer)
        abort_contract.assert_called_once_with(
            self.source, self.developer, self.prior_exit
        )
        forbidden_fresh.assert_not_called()
        return plan

    def test_plan_is_fixed_j6_full_targets_offline_and_prior_bound(self):
        plan = self.create_plan()
        self.assertEqual(runner.RUN_STEM, plan.run_stem)
        self.assertEqual("x64", plan.architecture)
        self.assertEqual(
            (
                str(self.autoninja),
                "-j6",
                "-C",
                "out/FocusMacX64",
                "chrome",
                "chrome/installer/mac:copies",
            ),
            plan.argv,
        )
        self.assertIn("set -o pipefail", plan.shell_script)
        self.assertIn(" -j6 ", " " + plan.shell_script + " ")
        self.assertNotIn(" -j8 ", " " + plan.shell_script + " ")
        self.assertNotIn("gn gen", plan.shell_script)
        self.assertNotRegex(plan.shell_script, r"https?://")
        self.assertEqual(self.prior_post_run, plan.prior_post_run)
        self.assertEqual(self.fresh, plan.fresh_x64_preparation)
        self.assertEqual(
            runner._snapshot_regular(self.prior_exit, "prior")["sha256"],
            plan.prior_memory_abort["contract_sha256"],
        )

    def test_prelaunch_uses_new_stdout_and_exact_prior_post_history(self):
        plan = self.create_plan()
        stdout = runner._create_stdout(plan.evidence["stdout"])
        value = runner._pre_launch_value(plan, stdout)
        self.assertEqual(6, value["planned_process"]["jobs"])
        self.assertEqual("-j6", value["planned_process"]["argv"][1])
        self.assertEqual(self.prior_post_run, value["pre_run"])
        self.assertEqual(plan.prior_memory_abort, value["prior_memory_abort"])
        self.assertEqual(plan.fresh_x64_preparation, value["fresh_x64_preparation"])
        self.assertEqual(
            str(Path(runner.__file__).resolve()), value["runner"]["path"]
        )
        self.assertNotEqual(
            str(self.prior_exit.with_suffix(".log")),
            value["stdout_log"]["logical_path"],
        )
        with (self.out / ".ninja_log").open("ab") as stream:
            stream.write(b"unapproved mutation\n")
        with self.assertRaisesRegex(
            runner.RunnerError, "prior memory-abort post-run"
        ):
            runner._pre_launch_value(plan, stdout)

    def test_missing_ninja_history_fails_closed(self):
        (self.out / ".ninja_deps").unlink()
        with self.assertRaisesRegex(
            (runner.RunnerError, runner.build_pipeline.PipelineError),
            "regular \\.ninja_log and \\.ninja_deps|prior memory-abort|must be regular",
        ):
            self.create_plan()

    def test_noncanonical_prior_exit_file_is_rejected(self):
        self.prior_exit.chmod(0o644)
        self.prior_exit.write_text(
            json.dumps(self.prior_value, separators=(",", ":")), encoding="utf-8"
        )
        self.prior_exit.chmod(0o444)
        with self.assertRaisesRegex(
            runner.RunnerError, "exact canonical exit file"
        ):
            self.create_plan()

    def test_final_schema3_carries_exact_prior_chain(self):
        plan = self.create_plan()
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
        process = types.SimpleNamespace(pid=123)
        value = runner._final_record(
            plan,
            process,
            10,
            {"observed_at_ns": 11},
            {
                "logical": {},
                "identity": {},
                "pre_run": self.prior_post_run,
                "runner": {},
                "stdout_log": stdout_initial,
            },
            {"sha256": "1" * 64},
            {"sha256": "2" * 64},
            {"sha256": "3" * 64},
            {"sha256": "4" * 64},
            {
                "stdout_log": {},
                "post_run": {},
                "wait_observation": {
                    "wait_returned_at_ns": 12,
                    "returncode": 0,
                },
            },
            {"sha256": "5" * 64},
        )
        self.assertEqual(3, value["schema"])
        self.assertEqual("x64", value["architecture"])
        self.assertEqual(plan.prior_memory_abort, value["prior_memory_abort"])
        self.assertEqual(plan.fresh_x64_preparation, value["fresh_x64_preparation"])

    def test_cli_requires_explicit_resume4_confirmation(self):
        args = runner.parser().parse_args(
            [
                "run",
                "--source-root",
                str(self.source),
                "--developer-dir",
                str(self.developer),
            ]
        )
        self.assertFalse(args.execute)
        self.assertFalse(args.confirm_official_resume4)


if __name__ == "__main__":
    unittest.main()
