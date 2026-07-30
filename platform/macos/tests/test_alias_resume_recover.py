import copy
import os
import stat
import tempfile
import unittest
from pathlib import Path


import alias_resume_recover as recover


class AliasResumeRecoverTests(unittest.TestCase):
    def fixture(self):
        stem = "build-x64-resume3-fixture"
        paths = recover._evidence_paths(Path("/logical/work/logs"), stem)
        pre = {
            "run_id": stem,
            "architecture": "x64",
            "logical": {"source": "/logical/src"},
            "identity": {"fixture": True},
            "pre_run": {"ninja_log": None, "ninja_deps": None},
            "runner": {"path": "/runner.py", "bytes": 1, "sha256": "a" * 64},
            "fresh_x64_preparation": {
                "receipt": {"path": "/fresh.json", "bytes": 1, "sha256": "b" * 64},
                "contract_sha256": "b" * 64,
            },
            "planned_process": {
                "cwd": "/logical/src",
                "argv": ["autoninja", "-j8"],
                "environment": {"HOME": "/logical"},
            },
            "stdout_log": {"device": 1, "inode": 2, "birth_time_ns": 3},
        }
        primary = {
            "run_id": stem,
            "architecture": "x64",
            "observed_at_ns": 150,
            "process_group": {
                "members": [
                    {
                        "role": "pipeline_shell_group_leader",
                        "pid": 42,
                        "started_at_ns": 100,
                    }
                ]
            },
        }
        base = {"run_id": stem, "architecture": "x64"}
        status = {
            **base,
            "pid": 42,
            "pgid": 42,
            "outcome": "completed",
            "failure": None,
            "pipeline_success_derived": True,
            "evidence_complete": True,
            "pipefail": True,
            "explicit_gn_gen_command": False,
            "network_operations": 0,
            "wait_observation": {
                "api": "subprocess.Popen.wait",
                "returncode": 0,
                "wait_returned_at_ns": 200,
            },
            "stdout_log": {"path": str(paths["stdout"]), "bytes": 10},
            "post_run": {"ninja_log": {"bytes": 1}},
        }
        values = {
            "pre": pre,
            "primary": primary,
            "supplement": dict(base),
            "revalidation": dict(base),
            "status": status,
        }
        evidence = {
            name: {"value": value, "sha256": str(index) * 64}
            for index, (name, value) in enumerate(values.items(), 1)
        }
        return stem, paths, evidence

    def test_derives_exact_schema_three_record_without_build_command(self):
        stem, paths, evidence = self.fixture()
        record = recover.recovery_record_from_values(
            stem, paths, evidence, observed_at_ns=250
        )
        self.assertEqual(3, record["schema"])
        self.assertEqual("x64", record["architecture"])
        self.assertEqual(42, record["process"]["pid"])
        self.assertEqual(100, record["process"]["started_at_ns"])
        self.assertEqual(200, record["completion"]["ended_at_ns"])
        self.assertEqual(250, record["completion"]["observed_at_ns"])
        self.assertEqual(
            evidence["status"]["sha256"], record["exit_status"]["sha256"]
        )
        self.assertEqual(
            evidence["pre"]["value"]["fresh_x64_preparation"],
            record["fresh_x64_preparation"],
        )

    def test_rejects_non_success_or_ambiguous_leader(self):
        stem, paths, evidence = self.fixture()
        failed = copy.deepcopy(evidence)
        failed["status"]["value"]["pipeline_success_derived"] = False
        with self.assertRaises(recover.RecoveryError):
            recover.recovery_record_from_values(stem, paths, failed, 250)
        ambiguous = copy.deepcopy(evidence)
        ambiguous["primary"]["value"]["process_group"]["members"].append(
            dict(
                ambiguous["primary"]["value"]["process_group"]["members"][0]
            )
        )
        with self.assertRaises(recover.RecoveryError):
            recover.recovery_record_from_values(stem, paths, ambiguous, 250)

    def test_publication_is_immutable_and_no_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "record.execution.json"
            value = {"schema": 3, "fixture": True}
            report = recover._publish_no_replace(path, path, value)
            observed = os.stat(str(path), follow_symlinks=False)
            self.assertEqual(0, stat.S_IMODE(observed.st_mode) & 0o222)
            self.assertEqual(report["sha256"], recover.build_pipeline.sha256_file(path))
            with self.assertRaises(recover.RecoveryError):
                recover._publish_no_replace(path, path, value)


if __name__ == "__main__":
    unittest.main()
