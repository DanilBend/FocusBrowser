#!/usr/bin/env python3
"""Tests for the onboarding logical/physical root compatibility receipt."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import onboarding_alias_compat


REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE = REPO_ROOT / "source_overrides" / onboarding_alias_compat.SOURCE_RELATIVE


class OnboardingAliasCompatTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.physical_home = self.root / "physical-home"
        self.physical_home.mkdir()
        self.logical_home = self.root / "logical-home"
        self.logical_home.symlink_to(self.physical_home, target_is_directory=True)
        self.source = self.physical_home / "workspace/checkout/src"
        self.target = self.source / onboarding_alias_compat.SOURCE_RELATIVE
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(BASELINE.read_bytes())
        self.target.chmod(0o644)

        self.out = self.source / "out/FocusMacArm64"
        self.out.mkdir(parents=True)
        self.build_ninja = self.out / "build.ninja"
        self.build_ninja.write_bytes(b"subninja toolchain.ninja\n")
        (self.out / "build.ninja.d").write_bytes(b"build.ninja: ../../BUILD.gn\n")
        (self.out / "args.gn").write_bytes(b'target_cpu = "arm64"\n')
        (self.out / "toolchain.ninja").write_bytes(b"rule cc\n")
        cross = self.out / "clang_arm64"
        cross.mkdir()
        (cross / "toolchain.ninja").write_bytes(b"rule cross_cc\n")
        self.inventory = onboarding_alias_compat.capture_graph_inventory(
            self.source, "out/FocusMacArm64/build.ninja"
        )

        self.logical_root = (
            self.logical_home
            / "workspace/checkout/src/components/focus_onboarding"
        )
        output = {
            "root": str(self.logical_root),
            "output_root": str(self.root / "logical-dist"),
            "exit_code": 0,
            "regular_files": 6,
            "bytes": 4096,
            "tree_sha256": "a" * 64,
            "index_html_sha256": "b" * 64,
        }
        physical_output = dict(output)
        physical_output["root"] = str(self.target.parent)
        physical_output["output_root"] = str(self.root / "physical-dist")
        toolchain_hashes = {
            entry["path"].split("out/FocusMacArm64/", 1)[1]: entry["sha256"]
            for entry in self.inventory["toolchains"]
        }
        self.failure = {
            "schema": 1,
            "kind": "focus-macos-alias-raw-ninja-failure",
            "architecture": "arm64",
            "process_logical_start_ns": 1,
            "process_output_ended_at_ns": 2,
            "exit_observed_at_ns": 3,
            "wrapper_exit_code": 1,
            "pipefail": True,
            "pipeline_failure_derived": True,
            "failure": {
                "classification": "logical-and-physical-home-root-mismatch",
                "diagnostic": "fixture",
                "label": "//components/focus_onboarding:build(//build/toolchain/mac:clang_arm64)",
                "target": "gen/components/focus_onboarding/dist/index.html",
                "tool": "Vite 7.1.5 / Rollup",
            },
            "progress": {},
            "stdout_log": {},
            "pre_run_ninja": {},
            "post_run_ninja": {},
            "generated_graph": {
                "args_gn_sha256": self.inventory["args_gn"]["sha256"],
                "build_ninja_d_sha256": self.inventory["build_ninja_d"]["sha256"],
                "build_ninja_sha256": self.inventory["build_ninja"]["sha256"],
                "toolchain_ninja_sha256": toolchain_hashes,
            },
            "immutable_evidence": {},
            "acceptance": {
                "failed_run_must_never_be_reclassified_as_success": True,
                "resume_required": True,
                "successful_slice_receipt_allowed": False,
            },
        }
        self.logs = self.physical_home / "workspace/work/logs"
        self.logs.mkdir(parents=True)
        self.failure_path = self.logs / onboarding_alias_compat.FAILURE_REPORT_BASENAME
        failure_bytes = json.dumps(self.failure, sort_keys=True).encode() + b"\n"
        self.failure_path.write_bytes(failure_bytes)
        self.failure_path.chmod(0o444)
        self.failure_record = {
            "bytes": len(failure_bytes),
            "sha256": hashlib.sha256(failure_bytes).hexdigest(),
        }
        self.trial = {
            "schema": 1,
            "kind": onboarding_alias_compat.TRIAL_KIND,
            "source_pre_sha256": onboarding_alias_compat.PRE_SHA256,
            "source_post_sha256": onboarding_alias_compat.POST_SHA256,
            "source_relative_path": onboarding_alias_compat.SOURCE_RELATIVE,
            "fix_contract": {
                "plugin_name": "canonical-build-root",
                "enforce": "pre",
                "apply": "build",
                "operation": "config.root = realpathSync(config.root)",
                "development_server_behavior_changed": False,
            },
            "logical_root_with_fix": output,
            "physical_root_control": physical_output,
            "comparison": {
                "relative_path_sets_equal": True,
                "all_file_sha256_equal": True,
                "all_file_bytes_equal": True,
                "semantic_output_change": False,
            },
            "failed_build_evidence": {
                "path": str(self.failure_path),
                "sha256": self.failure_record["sha256"],
                "exit_code": 1,
                "logical_root": str(self.logical_root),
                "diagnostic_contains": 'received "../../gicza/Documents/Codex/index.html"',
            },
        }
        self.trial_path = self.logs / onboarding_alias_compat.TRIAL_REPORT_BASENAME
        trial_bytes = json.dumps(self.trial, sort_keys=True).encode() + b"\n"
        self.trial_path.write_bytes(trial_bytes)
        self.trial_path.chmod(0o444)
        self.trial_record = {
            "bytes": len(trial_bytes),
            "sha256": hashlib.sha256(trial_bytes).hexdigest(),
        }
        self.hash_patchers = [
            mock.patch.object(
                onboarding_alias_compat,
                "TRIAL_REPORT_SHA256",
                self.trial_record["sha256"],
            ),
            mock.patch.object(
                onboarding_alias_compat,
                "FAILURE_REPORT_SHA256",
                self.failure_record["sha256"],
            ),
        ]
        for patcher in self.hash_patchers:
            patcher.start()
        self.trial["failed_build_evidence"]["sha256"] = onboarding_alias_compat.FAILURE_REPORT_SHA256
        self.target.write_bytes(self.postimage())
        self.vite_temp = self.source / onboarding_alias_compat.VITE_TEMP_RELATIVE
        self.vite_temp.mkdir(parents=True)
        self.vite_temp.chmod(0o755)
        self.transition = onboarding_alias_compat.prepare_home_alias_adoption(
            self.source,
            self.physical_home / "workspace",
            self.inventory,
            self.trial,
            trial_record=self.trial_record,
            trial_path=self.trial_path,
            failure_report=self.failure,
            failure_record=self.failure_record,
            failure_path=self.failure_path,
            prepare_requested=True,
            confirm_home_alias_adoption=True,
        )
        self.home_alias_path, self.home_alias_value = self._write_home_alias_receipt()
        self.canonical_alias_patcher = mock.patch.object(
            onboarding_alias_compat,
            "_canonical_home_alias_contract",
            return_value=(self.home_alias_path, self.home_alias_value),
        )
        self.canonical_alias_patcher.start()

    def tearDown(self):
        self.canonical_alias_patcher.stop()
        for patcher in reversed(self.hash_patchers):
            patcher.stop()
        self.temporary.cleanup()

    @property
    def receipt(self):
        return self.source / onboarding_alias_compat.RECEIPT_RELATIVE

    def execute(self, **kwargs):
        return onboarding_alias_compat.execute(
            self.source,
            self.inventory,
            self.trial,
            trial_record=self.trial_record,
            trial_path=self.trial_path,
            failure_report=self.failure,
            failure_record=self.failure_record,
            failure_path=self.failure_path,
            execute_requested=True,
            confirm_alias_root_compat=True,
            **kwargs,
        )

    def prepare_transition(self, **kwargs):
        return onboarding_alias_compat.prepare_home_alias_adoption(
            self.source,
            self.physical_home / "workspace",
            self.inventory,
            self.trial,
            trial_record=self.trial_record,
            trial_path=self.trial_path,
            failure_report=self.failure,
            failure_record=self.failure_record,
            failure_path=self.failure_path,
            prepare_requested=True,
            confirm_home_alias_adoption=True,
            **kwargs,
        )

    def reset_pending_transition(self):
        if self.home_alias_path.exists() or self.home_alias_path.is_symlink():
            self.home_alias_path.unlink()
        transition_path = Path(self.transition["path"])
        if transition_path.exists() or transition_path.is_symlink():
            transition_path.unlink()
        consumed = self.source / onboarding_alias_compat.TRANSITION_CONSUMED_RELATIVE
        if consumed.exists() or consumed.is_symlink():
            consumed.unlink()
        self.target.write_bytes(self.postimage())
        self.vite_temp.mkdir(parents=True)
        self.vite_temp.chmod(0o755)

    def numeric_json_mutations(self, value):
        mutations = []

        def walk(current, path):
            if isinstance(current, dict):
                for key, child in current.items():
                    walk(child, path + (key,))
            elif isinstance(current, list):
                for index, child in enumerate(current):
                    walk(child, path + (index,))
            elif type(current) is bool:
                mutations.append((path, int(current)))
                mutations.append((path, float(int(current))))
            elif type(current) is int:
                mutations.append((path, bool(current)))
                mutations.append((path, float(current)))
            elif type(current) is float:
                mutations.append((path, int(current)))
                mutations.append((path, bool(current)))

        walk(value, ())
        return mutations

    def replace_json_path(self, value, path, replacement):
        cursor = value
        for part in path[:-1]:
            cursor = cursor[part]
        cursor[path[-1]] = replacement

    def plan(self, trial=None, inventory=None):
        return onboarding_alias_compat.plan(
            self.source,
            inventory or self.inventory,
            trial or self.trial,
            trial_record=self.trial_record,
            trial_path=self.trial_path,
            failure_report=self.failure,
            failure_record=self.failure_record,
            failure_path=self.failure_path,
        )

    def _identity(self, logical, physical, volume_uuid):
        logical_status = os.stat(logical)
        physical_status = os.stat(physical, follow_symlinks=False)
        self.assertEqual(logical_status.st_ino, physical_status.st_ino)
        return {
            "logical": str(logical),
            "physical": str(physical),
            "identity": {
                "volume_uuid": volume_uuid,
                "device": physical_status.st_dev,
                "inode": physical_status.st_ino,
                "uid": physical_status.st_uid,
                "gid": physical_status.st_gid,
                "mode": stat.S_IMODE(physical_status.st_mode),
            },
        }

    def _write_home_alias_receipt(self):
        volume_uuid = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        workspace = self.physical_home / "workspace"
        logical_workspace = self.logical_home / "workspace"
        developer = self.physical_home / "Xcode.app/Contents/Developer"
        developer.mkdir(parents=True)
        logical_developer = self.logical_home / "Xcode.app/Contents/Developer"
        repo = workspace / "repo"
        repo.mkdir()
        logical_repo = logical_workspace / "repo"
        alias_status = os.lstat(self.logical_home)
        home_status = os.stat(self.physical_home, follow_symlinks=False)
        mappings = {
            "workspace": self._identity(logical_workspace, workspace, volume_uuid),
            "source": self._identity(
                self.logical_home / "workspace/checkout/src", self.source, volume_uuid
            ),
            "developer": self._identity(logical_developer, developer, volume_uuid),
            "repo": self._identity(logical_repo, repo, volume_uuid),
        }
        legacy_names = {
            "acquisition",
            "tool_bootstrap",
            "dependency_cache",
            "preparation",
            "gn_compatibility",
            "xcode27_compatibility",
            "xcode27_seatbelt_compatibility",
            "screen_ai_disabled_compatibility",
            "xcode27_linkedit_strip_compatibility",
        }
        receipt = {
            "schema": 2,
            "logical_home": str(self.logical_home),
            "physical_home": str(self.physical_home),
            "volume": {"filesystem": "apfs", "volume_uuid": volume_uuid},
            "alias": {
                "path": str(self.logical_home),
                "target": str(self.physical_home),
                "device": alias_status.st_dev,
                "inode": alias_status.st_ino,
                "uid": alias_status.st_uid,
                "gid": alias_status.st_gid,
                "mode": stat.S_IMODE(alias_status.st_mode),
                "root_owned": True,
                "absolute_exact_target": True,
                "target_identity": {
                    "volume_uuid": volume_uuid,
                    "device": home_status.st_dev,
                    "inode": home_status.st_ino,
                    "uid": home_status.st_uid,
                    "gid": home_status.st_gid,
                    "mode": stat.S_IMODE(home_status.st_mode),
                },
            },
            "mappings": mappings,
            "legacy_receipts": {
                name: {"path": "/fixture/" + name, "sha256": "c" * 64}
                for name in legacy_names
            },
            "legacy_receipts_rewritten": False,
            "gn_gen_executed": False,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
            "offline": True,
            "network_operations": 0,
        }
        path = self.source / onboarding_alias_compat.HOME_ALIAS_RECEIPT_RELATIVE
        path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
        return path, receipt

    def postimage(self):
        contract = onboarding_alias_compat._source_contract(self.source)
        self.assertEqual("pre", contract["state"])
        return contract["post"]

    def reclaimed_arm_evidence(self):
        if not self.receipt.exists():
            self.execute()
        home_alias = onboarding_alias_compat.validate_home_alias_receipt(
            self.source
        )
        logical_source = Path(
            home_alias["mappings"]["source"]["logical"]
        )
        staging = self.source / "out/FocusMacStaging"
        staged_app = staging / "arm64/Focus Browser.app"
        staged_app.mkdir(parents=True)
        stage_path = self.source / onboarding_alias_compat.ARM_STAGE_RECEIPT_RELATIVE
        reclaim_path = (
            self.source / onboarding_alias_compat.ARM_RECLAIM_RECEIPT_RELATIVE
        )
        stage = {
            "schema": 2,
            "architecture": "arm64",
            "source_root": str(logical_source),
            "staged_app": str(
                logical_source / onboarding_alias_compat.ARM_STAGED_APP_RELATIVE
            ),
            "tree_sha256": "8" * 64,
            "app_allocated_bytes": 4096,
            "reclaim_requested_out": str(
                logical_source / onboarding_alias_compat.ARM_OUT_RELATIVE
            ),
            "reclaim_requested_bytes": 8192,
            "arm_args_gn_sha256": self.inventory["args_gn"]["sha256"],
            "build_receipt_sha256": "9" * 64,
            "upstream_no_work_probe": {
                "command": [
                    str(
                        logical_source
                        / "third_party/dawn/third_party/ninja/ninja"
                    ),
                    "-n", "-C", onboarding_alias_compat.ARM_OUT_RELATIVE,
                    "chrome", "chrome/installer/mac:copies",
                ],
                "returncode": 0,
                "output_bytes": 1,
                "output_sha256": "7" * 64,
                "bounded_output_limit": 1024 * 1024,
                "no_work": True,
            },
        }
        stage_data = (
            json.dumps(stage, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("ascii")
        stage_path.write_bytes(stage_data)
        reclaim = {
            "schema": 1,
            "reclaim_complete": True,
            "source_root": str(logical_source),
            "staged_app": stage["staged_app"],
            "tree_sha256": stage["tree_sha256"],
            "reclaimed_out": stage["reclaim_requested_out"],
            "reclaimed_out_bytes": stage["reclaim_requested_bytes"],
            "arm_args_gn_sha256": stage["arm_args_gn_sha256"],
            "stage_receipt_sha256": hashlib.sha256(stage_data).hexdigest(),
        }
        reclaim_data = (
            json.dumps(reclaim, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("ascii")
        reclaim_path.write_bytes(reclaim_data)
        evidence = {
            "schema": 1,
            "kind": onboarding_alias_compat.RECLAIMED_ARM_EVIDENCE_KIND,
            "home_alias_compatibility": home_alias,
            "graph_inventory_sha256": self.inventory["aggregate_sha256"],
            "stage_receipt": {
                "path": onboarding_alias_compat.ARM_STAGE_RECEIPT_RELATIVE,
                "bytes": len(stage_data),
                "sha256": hashlib.sha256(stage_data).hexdigest(),
            },
            "reclaim_receipt": {
                "path": onboarding_alias_compat.ARM_RECLAIM_RECEIPT_RELATIVE,
                "bytes": len(reclaim_data),
                "sha256": hashlib.sha256(reclaim_data).hexdigest(),
            },
            "staged_app": {
                "path": onboarding_alias_compat.ARM_STAGED_APP_RELATIVE,
                "tree_sha256": stage["tree_sha256"],
            },
            "reclaimed_out": onboarding_alias_compat.ARM_OUT_RELATIVE,
        }
        shutil.rmtree(self.out)
        return evidence

    def test_pinned_patch_derives_exact_pre_and_post_hashes(self):
        contract = onboarding_alias_compat._source_contract(self.source)
        self.assertEqual("pre", contract["state"])
        self.assertEqual(
            onboarding_alias_compat.PRE_SHA256,
            hashlib.sha256(contract["pre"]).hexdigest(),
        )
        self.assertEqual(
            onboarding_alias_compat.POST_SHA256,
            hashlib.sha256(contract["post"]).hexdigest(),
        )
        self.assertEqual(
            onboarding_alias_compat.PATCH_SHA256,
            hashlib.sha256(onboarding_alias_compat.PATCH_PATH.read_bytes()).hexdigest(),
        )

    def test_default_plan_is_read_only(self):
        before_source = self.target.read_bytes()
        before_graph = json.dumps(self.inventory, sort_keys=True)
        result = self.plan()
        self.assertTrue(result["read_only"])
        self.assertEqual("apply-and-receipt", result["action"])
        self.assertEqual(before_source, self.target.read_bytes())
        self.assertFalse(self.receipt.exists())
        self.assertEqual(before_graph, json.dumps(self.inventory, sort_keys=True))

    def test_execute_pre_to_post_and_verify_immutable_receipt(self):
        graph_before = onboarding_alias_compat.capture_graph_inventory(
            self.source, "out/FocusMacArm64/build.ninja"
        )
        result = self.execute()
        self.assertEqual("applied", result["status"])
        self.assertTrue(result["source_changed"])
        self.assertEqual(onboarding_alias_compat.POST_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertEqual(0, stat.S_IMODE(self.receipt.stat().st_mode) & 0o222)
        self.assertEqual(
            graph_before,
            onboarding_alias_compat.capture_graph_inventory(
                self.source, "out/FocusMacArm64/build.ninja"
            ),
        )
        verified = self.plan()
        self.assertEqual("verify-existing", verified["action"])
        self.assertIsNotNone(verified["receipt"])
        contract = onboarding_alias_compat.receipt_contract(
            self.source,
            trial_path=self.trial_path,
            failure_path=self.failure_path,
        )
        self.assertEqual(result["receipt"]["sha256"], contract["sha256"])
        self.assertEqual(self.inventory, contract["value"]["graph_inventory"])

    def test_reclaimed_contract_validates_without_live_arm_graph(self):
        evidence = self.reclaimed_arm_evidence()
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "build.ninja is missing"
        ):
            onboarding_alias_compat.receipt_contract(
                self.source,
                trial_path=self.trial_path,
                failure_path=self.failure_path,
            )
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "build.ninja is missing"
        ):
            onboarding_alias_compat.preparation_dependency_tree_projection_contract(
                self.source, self.physical_home / "workspace"
            )
        contract = onboarding_alias_compat.receipt_contract(
            self.source,
            trial_path=self.trial_path,
            failure_path=self.failure_path,
            reclaimed_arm=evidence,
        )
        self.assertEqual(self.inventory, contract["value"]["graph_inventory"])
        projection = (
            onboarding_alias_compat.preparation_dependency_tree_projection_contract(
                self.source,
                self.physical_home / "workspace",
                reclaimed_arm=evidence,
            )
        )
        self.assertEqual(
            onboarding_alias_compat.PREPARATION_PROJECTION_KIND,
            projection["kind"],
        )

    def test_reclaimed_contract_rejects_graph_and_reclaim_drift(self):
        evidence = self.reclaimed_arm_evidence()
        cases = []
        wrong_aggregate = json.loads(json.dumps(evidence))
        wrong_aggregate["graph_inventory_sha256"] = "0" * 64
        cases.append(("evidence-aggregate", wrong_aggregate))
        wrong_stage = json.loads(json.dumps(evidence))
        wrong_stage["stage_receipt"]["sha256"] = "0" * 64
        cases.append(("stage-receipt", wrong_stage))
        wrong_tree = json.loads(json.dumps(evidence))
        wrong_tree["staged_app"]["tree_sha256"] = "0" * 64
        cases.append(("staged-tree", wrong_tree))
        wrong_alias = json.loads(json.dumps(evidence))
        wrong_alias["home_alias_compatibility"]["alias"]["inode"] += 1
        cases.append(("current-alias", wrong_alias))
        for name, changed in cases:
            with self.subTest(case=name):
                with self.assertRaises(onboarding_alias_compat.AliasCompatError):
                    onboarding_alias_compat.receipt_contract(
                        self.source,
                        trial_path=self.trial_path,
                        failure_path=self.failure_path,
                        reclaimed_arm=changed,
                    )

    def test_reclaimed_contract_rejects_embedded_graph_shape_and_args_drift(self):
        evidence = self.reclaimed_arm_evidence()
        original = self.receipt.read_bytes()
        for name, mutate in (
            (
                "toolchain-order",
                lambda value: value["graph_inventory"]["toolchains"].reverse(),
            ),
            (
                "args-hash",
                lambda value: value["graph_inventory"]["args_gn"].__setitem__(
                    "sha256", "0" * 64
                ),
            ),
        ):
            with self.subTest(case=name):
                value = json.loads(original)
                mutate(value)
                core = {
                    key: value["graph_inventory"][key]
                    for key in (
                        "schema", "kind", "build_ninja", "build_ninja_d",
                        "args_gn", "toolchains",
                    )
                }
                value["graph_inventory"]["aggregate_sha256"] = hashlib.sha256(
                    json.dumps(
                        core,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("ascii")
                ).hexdigest()
                evidence["graph_inventory_sha256"] = value["graph_inventory"][
                    "aggregate_sha256"
                ]
                self.receipt.chmod(0o644)
                self.receipt.write_text(
                    json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
                    + "\n",
                    encoding="ascii",
                )
                self.receipt.chmod(0o444)
                try:
                    with self.assertRaises(onboarding_alias_compat.AliasCompatError):
                        onboarding_alias_compat.receipt_contract(
                            self.source,
                            trial_path=self.trial_path,
                            failure_path=self.failure_path,
                            reclaimed_arm=evidence,
                        )
                finally:
                    self.receipt.chmod(0o644)
                    self.receipt.write_bytes(original)
                    self.receipt.chmod(0o444)

    def test_execute_recovers_already_post_source_without_rewriting_it(self):
        self.target.write_bytes(self.postimage())
        before = self.target.stat()
        result = self.execute()
        after = self.target.stat()
        self.assertEqual("post-recovery-receipted", result["status"])
        self.assertFalse(result["source_changed"])
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertTrue(self.receipt.is_file())

    def test_execute_requires_both_explicit_flags(self):
        combinations = ((False, False), (True, False), (False, True))
        for execute_requested, confirmation in combinations:
            with self.subTest(execute=execute_requested, confirmation=confirmation):
                with self.assertRaisesRegex(
                    onboarding_alias_compat.AliasCompatError, "requires --execute"
                ):
                    onboarding_alias_compat.execute(
                        self.source,
                        self.inventory,
                        self.trial,
                        trial_record=self.trial_record,
                        trial_path=self.trial_path,
                        failure_report=self.failure,
                        failure_record=self.failure_record,
                        failure_path=self.failure_path,
                        execute_requested=execute_requested,
                        confirm_alias_root_compat=confirmation,
                    )
        self.assertEqual(onboarding_alias_compat.PRE_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())

    def test_drift_and_symlink_target_fail_closed(self):
        self.target.write_bytes(b"drift\n")
        with self.assertRaisesRegex(onboarding_alias_compat.AliasCompatError, "neither the exact"):
            self.plan()

        self.target.unlink()
        real = self.target.with_suffix(".real")
        real.write_bytes(BASELINE.read_bytes())
        self.target.symlink_to(real.name)
        with self.assertRaisesRegex(onboarding_alias_compat.AliasCompatError, "symlink"):
            self.plan()

    def test_source_race_after_plan_is_rejected_without_receipt(self):
        def hook(phase):
            if phase == "after-plan":
                self.target.write_bytes(b"raced\n")

        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError,
            "neither the exact preimage nor postimage",
        ):
            self.execute(_test_hook=hook)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(b"raced\n", self.target.read_bytes())

    def test_graph_race_rolls_source_back_and_publishes_no_receipt(self):
        def hook(phase):
            if phase == "after-source-update":
                self.build_ninja.write_bytes(b"graph drift\n")

        with self.assertRaisesRegex(onboarding_alias_compat.AliasCompatError, "explicit inventory"):
            self.execute(_test_hook=hook)
        self.assertEqual(onboarding_alias_compat.PRE_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(self.receipt.exists())

    def test_receipt_publication_race_never_overwrites_and_rolls_back(self):
        raced = b'{"racing":"receipt"}\n'

        def hook(phase):
            if phase == "before-receipt-publish":
                self.receipt.write_bytes(raced)

        with self.assertRaisesRegex(onboarding_alias_compat.AliasCompatError, "appeared before publication"):
            self.execute(_test_hook=hook)
        self.assertEqual(raced, self.receipt.read_bytes())
        self.assertEqual(onboarding_alias_compat.PRE_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())

    def test_existing_valid_receipt_is_idempotently_verified_not_overwritten(self):
        first = self.execute()
        receipt_before = self.receipt.read_bytes()
        inode_before = self.receipt.stat().st_ino
        second = self.execute()
        self.assertEqual("already-verified", second["status"])
        self.assertEqual(receipt_before, self.receipt.read_bytes())
        self.assertEqual(inode_before, self.receipt.stat().st_ino)
        self.assertEqual(first["receipt"]["sha256"], second["receipt"]["sha256"])

    def test_trial_shape_and_graph_hash_drift_are_rejected(self):
        bad_trial = json.loads(json.dumps(self.trial))
        bad_trial["comparison"]["invalid_emitted_asset_paths"] = 1
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "trial provenance"
        ):
            self.plan(trial=bad_trial)

        self.build_ninja.write_bytes(b"changed\n")
        with self.assertRaisesRegex(onboarding_alias_compat.AliasCompatError, "explicit inventory"):
            self.plan()

    def test_canonical_home_alias_failure_has_no_self_signed_fallback(self):
        self.assertNotEqual(0, self.home_alias_value["alias"]["uid"])
        with mock.patch.object(
            onboarding_alias_compat,
            "_canonical_home_alias_contract",
            side_effect=onboarding_alias_compat.AliasCompatError(
                "logical home alias must be a root-owned symbolic link"
            ),
        ):
            with self.assertRaisesRegex(
                onboarding_alias_compat.AliasCompatError, "root-owned"
            ):
                self.plan()

        self.home_alias_path.unlink()
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "home-alias receipt is missing"
        ):
            self.plan()

    def test_trial_evidence_requires_fixed_path_hash_and_read_only_mode(self):
        alternate = self.logs / ("alternate-" + onboarding_alias_compat.TRIAL_REPORT_BASENAME)
        alternate.write_bytes(self.trial_path.read_bytes())
        alternate.chmod(0o444)
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "fixed workspace/logs"
        ):
            onboarding_alias_compat.plan(
                self.source,
                self.inventory,
                self.trial,
                trial_record=self.trial_record,
                trial_path=alternate,
                failure_report=self.failure,
                failure_record=self.failure_record,
                failure_path=self.failure_path,
            )

        self.trial_path.chmod(0o644)
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "trial provenance"
        ):
            self.plan()

    def test_every_frozen_graph_component_drift_is_rejected(self):
        paths = [
            self.out / "args.gn",
            self.out / "build.ninja.d",
            self.out / "toolchain.ninja",
            self.out / "clang_arm64/toolchain.ninja",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"drift\n")
                with self.assertRaisesRegex(
                    onboarding_alias_compat.AliasCompatError, "explicit inventory"
                ):
                    self.plan()
                path.write_bytes(original)

    def test_receipt_contract_rejects_writable_and_tampered_receipts(self):
        self.execute()
        self.receipt.chmod(0o644)
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "not immutable"
        ):
            onboarding_alias_compat.receipt_contract(
                self.source,
                trial_path=self.trial_path,
                failure_path=self.failure_path,
            )
        self.receipt.write_bytes(b'{"tampered":true}\n')
        self.receipt.chmod(0o444)
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "schema mismatch"
        ):
            onboarding_alias_compat.receipt_contract(
                self.source,
                trial_path=self.trial_path,
                failure_path=self.failure_path,
            )

    def test_regular_file_post_read_path_swap_is_rejected(self):
        candidate = self.root / "candidate.json"
        replacement = self.root / "replacement.json"
        moved = self.root / "moved.json"
        candidate.write_bytes(b"one\n")
        replacement.write_bytes(b"two\n")

        def hook(phase):
            if phase == "after-regular-file-read":
                candidate.rename(moved)
                replacement.rename(candidate)

        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "path identity changed"
        ):
            onboarding_alias_compat._read_regular(
                candidate, "swap fixture", _test_hook=hook
            )

    def test_interruption_after_receipt_link_never_rolls_back_source(self):
        def hook(phase):
            if phase == "after-receipt-link":
                raise KeyboardInterrupt("fixture interruption")

        with self.assertRaises(KeyboardInterrupt):
            self.execute(_test_hook=hook)
        self.assertEqual(
            onboarding_alias_compat.POST_SHA256,
            hashlib.sha256(self.target.read_bytes()).hexdigest(),
        )
        self.assertEqual(0, stat.S_IMODE(self.receipt.stat().st_mode) & 0o222)
        onboarding_alias_compat.receipt_contract(
            self.source,
            trial_path=self.trial_path,
            failure_path=self.failure_path,
        )

    def test_graph_race_after_receipt_link_is_fail_closed_post_plus_receipt(self):
        def hook(phase):
            if phase == "after-receipt-link":
                (self.out / "build.ninja.d").write_bytes(b"graph raced\n")

        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "explicit inventory"
        ):
            self.execute(_test_hook=hook)
        self.assertEqual(
            onboarding_alias_compat.POST_SHA256,
            hashlib.sha256(self.target.read_bytes()).hexdigest(),
        )
        self.assertTrue(self.receipt.is_file())

    def test_rollback_refuses_postimage_metadata_or_xattr_drift(self):
        def hook(phase):
            if phase == "after-source-update":
                self.target.chmod(0o600)

        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "cannot safely roll back"
        ):
            self.execute(_test_hook=hook)
        self.assertEqual(
            onboarding_alias_compat.POST_SHA256,
            hashlib.sha256(self.target.read_bytes()).hexdigest(),
        )
        self.assertFalse(self.receipt.exists())

    def test_transition_is_immutable_idempotent_and_preserves_real_xattr(self):
        transition_path = Path(self.transition["path"])
        self.assertEqual(0o444, stat.S_IMODE(transition_path.stat().st_mode))
        self.assertFalse(self.vite_temp.exists())
        before = self.transition["value"]["source"]["post_before"]
        current = onboarding_alias_compat._read_regular(
            self.target, "transition xattr fixture"
        )
        recorded_xattrs = onboarding_alias_compat._xattrs_from_record(
            before["xattrs"], "transition xattr fixture"
        )
        self.assertIn(b"com.apple.provenance", {name for name, _ in recorded_xattrs})
        self.assertEqual(recorded_xattrs, current["xattrs"])
        self.assertNotEqual(before["inode"], current["identity"][1])

        self.home_alias_path.unlink()
        before_inode = transition_path.stat().st_ino
        repeated = self.prepare_transition()
        self.assertEqual(self.transition["sha256"], repeated["sha256"])
        self.assertEqual(before_inode, transition_path.stat().st_ino)

    def test_preparation_projection_is_none_at_pre_and_exact_at_consumed_post(self):
        workspace = self.physical_home / "workspace"
        self.assertIsNone(
            onboarding_alias_compat.preparation_dependency_tree_projection_contract(
                self.source, workspace
            )
        )
        self.execute()
        projection = (
            onboarding_alias_compat.preparation_dependency_tree_projection_contract(
                self.source, workspace
            )
        )
        self.assertEqual(
            onboarding_alias_compat.PREPARATION_PROJECTION_KIND,
            projection["kind"],
        )
        self.assertEqual(
            {
                "relative_path": onboarding_alias_compat.SOURCE_RELATIVE,
                "observed": {
                    "mode": 0o644,
                    "bytes": onboarding_alias_compat.POST_BYTES,
                    "sha256": onboarding_alias_compat.POST_SHA256,
                },
                "projected": {
                    "mode": 0o644,
                    "bytes": onboarding_alias_compat.PRE_BYTES,
                    "sha256": onboarding_alias_compat.PRE_SHA256,
                },
            },
            projection["tree_projection"],
        )
        original = Path(projection["transition"]["path"])
        consumed = self.source / onboarding_alias_compat.TRANSITION_CONSUMED_RELATIVE
        self.assertEqual(original.stat().st_ino, consumed.stat().st_ino)

    def test_preparation_projection_rejects_missing_or_copied_consumed_link(self):
        workspace = self.physical_home / "workspace"
        self.target.write_bytes(self.postimage())
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "consumed transition"
        ):
            onboarding_alias_compat.preparation_dependency_tree_projection_contract(
                self.source, workspace
            )

        consumed = self.source / onboarding_alias_compat.TRANSITION_CONSUMED_RELATIVE
        consumed.write_bytes(Path(self.transition["path"]).read_bytes())
        consumed.chmod(0o444)
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "link identity changed"
        ):
            onboarding_alias_compat.preparation_dependency_tree_projection_contract(
                self.source, workspace
            )

    def test_transition_requires_both_explicit_flags(self):
        for requested, confirmed in ((False, False), (True, False), (False, True)):
            with self.subTest(requested=requested, confirmed=confirmed):
                with self.assertRaisesRegex(
                    onboarding_alias_compat.AliasCompatError,
                    "requires --prepare-home-alias-adoption",
                ):
                    onboarding_alias_compat.prepare_home_alias_adoption(
                        self.source,
                        self.physical_home / "workspace",
                        self.inventory,
                        self.trial,
                        trial_record=self.trial_record,
                        trial_path=self.trial_path,
                        failure_report=self.failure,
                        failure_record=self.failure_record,
                        failure_path=self.failure_path,
                        prepare_requested=requested,
                        confirm_home_alias_adoption=confirmed,
                    )

    def test_transition_recovers_interruption_after_immutable_journal_link(self):
        self.reset_pending_transition()

        def hook(phase):
            if phase == "after-transition-receipt-link":
                raise KeyboardInterrupt("transition journal interruption")

        with self.assertRaises(KeyboardInterrupt):
            self.prepare_transition(_test_hook=hook)
        self.assertEqual(
            onboarding_alias_compat.POST_SHA256,
            hashlib.sha256(self.target.read_bytes()).hexdigest(),
        )
        self.assertTrue(self.vite_temp.is_dir())
        recovered = self.prepare_transition()
        self.assertEqual(onboarding_alias_compat.PRE_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(self.vite_temp.exists())
        self.assertEqual(0o444, stat.S_IMODE(Path(recovered["path"]).stat().st_mode))

    def test_transition_rolls_back_then_recovers_after_source_update_failure(self):
        self.reset_pending_transition()

        def hook(phase):
            if phase == "after-transition-source-update":
                raise RuntimeError("fixture failure")

        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            self.prepare_transition(_test_hook=hook)
        self.assertEqual(onboarding_alias_compat.POST_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertTrue(self.vite_temp.is_dir())
        self.prepare_transition()
        self.assertEqual(onboarding_alias_compat.PRE_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(self.vite_temp.exists())

    def test_transition_recovers_completed_rmdir_after_interruption(self):
        self.reset_pending_transition()

        def hook(phase):
            if phase == "after-vite-temp-rmdir":
                raise KeyboardInterrupt("after exact rmdir")

        with self.assertRaises(KeyboardInterrupt):
            self.prepare_transition(_test_hook=hook)
        self.assertEqual(onboarding_alias_compat.PRE_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(self.vite_temp.exists())
        expected_sha = hashlib.sha256(
            Path(self.transition["path"]).read_bytes()
        ).hexdigest()
        recovered = self.prepare_transition()
        self.assertEqual(expected_sha, recovered["sha256"])

    def test_transition_recovers_journaled_quarantine_after_power_loss(self):
        self.reset_pending_transition()

        def hook(phase):
            if phase == "after-vite-temp-quarantine":
                raise KeyboardInterrupt("power loss after deterministic rename")

        with self.assertRaises(KeyboardInterrupt):
            self.prepare_transition(_test_hook=hook)
        self.assertEqual(onboarding_alias_compat.PRE_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(self.vite_temp.exists())
        value = json.loads(Path(self.transition["path"]).read_text())
        quarantine = self.source / value["vite_temp"]["quarantine_path"]
        self.assertTrue(quarantine.is_dir())
        expected_inode = value["vite_temp"]["inode"]
        self.assertEqual(expected_inode, quarantine.stat().st_ino)

        self.prepare_transition()
        self.assertFalse(quarantine.exists())
        self.assertFalse(self.vite_temp.exists())

    def test_transition_post_mutation_graph_race_fails_closed(self):
        self.reset_pending_transition()

        def hook(phase):
            if phase == "after-vite-temp-rmdir":
                (self.out / "args.gn").write_bytes(b"graph raced after rmdir\n")

        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "explicit inventory"
        ):
            self.prepare_transition(_test_hook=hook)
        self.assertEqual(onboarding_alias_compat.PRE_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(self.vite_temp.exists())
        self.assertTrue(Path(self.transition["path"]).is_file())

    def test_transition_rejects_nonempty_vite_temp_without_mutation(self):
        self.reset_pending_transition()
        (self.vite_temp / "unexpected").write_bytes(b"not empty\n")
        with self.assertRaisesRegex(
            onboarding_alias_compat.AliasCompatError, "not empty"
        ):
            self.prepare_transition()
        self.assertEqual(onboarding_alias_compat.POST_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(Path(self.transition["path"]).exists())

    def test_completed_transition_rejects_every_corrupt_vite_evidence_field(self):
        path = Path(self.transition["path"])
        original = path.read_bytes()
        original_value = json.loads(original)
        cases = {
            "children": 99,
            "path": "components/focus_onboarding/node_modules/wrong",
            "quarantine_path": "components/focus_onboarding/node_modules/wrong.part",
            "file_type": "file",
            "device_at_capture": original_value["vite_temp"]["device_at_capture"] + 1,
            "inode": 0,
            "uid": os.getuid() + 1,
            "gid": os.getgid() + 1,
            "mode": 0o777,
            "birth_time_ns": 0,
            "mtime_ns": 0,
            "xattrs": [],
        }
        for field, replacement in cases.items():
            with self.subTest(field=field):
                value = json.loads(original)
                value["vite_temp"][field] = replacement
                path.chmod(0o644)
                path.write_text(
                    json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
                    + "\n",
                    encoding="ascii",
                )
                path.chmod(0o444)
                with self.assertRaisesRegex(
                    onboarding_alias_compat.AliasCompatError,
                    "transition Vite temp",
                ):
                    onboarding_alias_compat.transition_receipt_contract(
                        self.source,
                        self.physical_home / "workspace",
                        self.inventory,
                        self.transition["value"]["trial_evidence"],
                        require_complete=True,
                    )
                path.chmod(0o644)
                path.write_bytes(original)
                path.chmod(0o444)

        original_xattrs = original_value["vite_temp"]["xattrs"]
        provenance_name = b"com.apple.provenance".hex()
        provenance_indexes = [
            index
            for index, entry in enumerate(original_xattrs)
            if entry["name_hex"] == provenance_name
        ]
        self.assertEqual([0], provenance_indexes)

        empty_value = json.loads(json.dumps(original_xattrs))
        empty_value[0]["value_hex"] = ""
        changed_value = json.loads(json.dumps(original_xattrs))
        changed_value[0]["value_hex"] = "00"
        arbitrary_value = json.loads(json.dumps(original_xattrs))
        arbitrary_value[0]["value_hex"] = "a1b2c3d4"
        extra_xattr = json.loads(json.dumps(original_xattrs))
        extra_xattr.append({"name_hex": b"user.extra".hex(), "value_hex": "00"})
        extra_xattr.sort(key=lambda entry: entry["name_hex"])

        for name, replacement in (
            ("empty-provenance", empty_value),
            ("changed-provenance", changed_value),
            ("arbitrary-provenance", arbitrary_value),
            ("extra-xattr", extra_xattr),
        ):
            with self.subTest(case=name):
                value = json.loads(original)
                value["vite_temp"]["xattrs"] = replacement
                path.chmod(0o644)
                path.write_text(
                    json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
                    + "\n",
                    encoding="ascii",
                )
                path.chmod(0o444)
                with self.assertRaisesRegex(
                    onboarding_alias_compat.AliasCompatError,
                    "transition Vite temp",
                ):
                    onboarding_alias_compat.transition_receipt_contract(
                        self.source,
                        self.physical_home / "workspace",
                        self.inventory,
                        self.transition["value"]["trial_evidence"],
                        require_complete=True,
                    )
                path.chmod(0o644)
                path.write_bytes(original)
                path.chmod(0o444)

    def test_completed_transition_rejects_corrupt_source_evidence_matrix(self):
        path = Path(self.transition["path"])
        original = path.read_bytes()

        def set_value(*parts_and_value):
            *parts, replacement = parts_and_value

            def mutate(value):
                cursor = value
                for part in parts[:-1]:
                    cursor = cursor[part]
                cursor[parts[-1]] = replacement

            return mutate

        def add_value(*parts):
            def mutate(value):
                cursor = value
                for part in parts:
                    cursor = cursor[part]
                cursor["unexpected"] = True

            return mutate

        def remove_value(*parts):
            def mutate(value):
                cursor = value
                for part in parts[:-1]:
                    cursor = cursor[part]
                cursor.pop(parts[-1])

            return mutate

        original_value = json.loads(original)
        post = original_value["source"]["post_before"]
        pre = original_value["source"]["pre_after"]
        changed_pre_xattrs = json.loads(json.dumps(pre["xattrs"]))
        changed_pre_xattrs[0]["value_hex"] = "00"
        cases = [
            ("source-extra", add_value("source")),
            ("source-missing", remove_value("source", "physical")),
            ("source-physical", set_value("source", "physical", "/wrong/source")),
            ("source-relative", set_value("source", "workspace_relative", "wrong")),
            ("post-extra", add_value("source", "post_before")),
            ("post-missing", remove_value("source", "post_before", "inode")),
            ("pre-extra", add_value("source", "pre_after")),
            ("pre-missing", remove_value("source", "pre_after", "uid")),
            ("post-bool", set_value("source", "post_before", "inode", True)),
            ("pre-bool", set_value("source", "pre_after", "bytes", True)),
            ("device-zero", set_value("source", "post_before", "device_at_capture", 0)),
            ("device-mismatch", set_value("source", "post_before", "device_at_capture", post["device_at_capture"] + 1)),
            ("inode-zero", set_value("source", "post_before", "inode", 0)),
            ("inode-negative", set_value("source", "post_before", "inode", -1)),
            ("mtime-zero", set_value("source", "post_before", "mtime_ns", 0)),
            ("ctime-negative", set_value("source", "post_before", "ctime_ns", -1)),
            ("post-uid", set_value("source", "post_before", "uid", os.getuid() + 1)),
            ("post-gid", set_value("source", "post_before", "gid", os.getgid() + 1)),
            ("post-mode", set_value("source", "post_before", "mode", 0o600)),
            ("pre-uid", set_value("source", "pre_after", "uid", os.getuid() + 1)),
            ("pre-gid", set_value("source", "pre_after", "gid", os.getgid() + 1)),
            ("pre-mode", set_value("source", "pre_after", "mode", 0o600)),
            ("post-bytes", set_value("source", "post_before", "bytes", post["bytes"] + 1)),
            ("post-sha", set_value("source", "post_before", "sha256", "0" * 64)),
            ("pre-bytes", set_value("source", "pre_after", "bytes", pre["bytes"] + 1)),
            ("pre-sha", set_value("source", "pre_after", "sha256", "0" * 64)),
            ("post-xattrs-empty", set_value("source", "post_before", "xattrs", [])),
            ("pre-xattrs-empty", set_value("source", "pre_after", "xattrs", [])),
            ("xattrs-mismatch", set_value("source", "pre_after", "xattrs", changed_pre_xattrs)),
        ]
        for name, mutate in cases:
            with self.subTest(case=name):
                value = json.loads(original)
                mutate(value)
                path.chmod(0o644)
                path.write_text(
                    json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
                    + "\n",
                    encoding="ascii",
                )
                path.chmod(0o444)
                with self.assertRaises(onboarding_alias_compat.AliasCompatError):
                    onboarding_alias_compat.transition_receipt_contract(
                        self.source,
                        self.physical_home / "workspace",
                        self.inventory,
                        self.transition["value"]["trial_evidence"],
                        require_complete=True,
                    )
                path.chmod(0o644)
                path.write_bytes(original)
                path.chmod(0o444)

    def test_transition_rejects_recursive_bool_int_float_equivalence(self):
        path = Path(self.transition["path"])
        original = path.read_bytes()
        parsed = json.loads(original)
        mutations = self.numeric_json_mutations(parsed)
        self.assertGreater(len(mutations), 50)
        for json_path, replacement in mutations:
            label = "/".join(map(str, json_path)) + "->" + type(replacement).__name__
            with self.subTest(path=label):
                value = json.loads(original)
                self.replace_json_path(value, json_path, replacement)
                path.chmod(0o644)
                path.write_text(
                    json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
                    + "\n",
                    encoding="ascii",
                )
                path.chmod(0o444)
                try:
                    with self.assertRaises(onboarding_alias_compat.AliasCompatError):
                        onboarding_alias_compat.transition_receipt_contract(
                            self.source,
                            self.physical_home / "workspace",
                            self.inventory,
                            self.transition["value"]["trial_evidence"],
                            require_complete=True,
                        )
                finally:
                    path.chmod(0o644)
                    path.write_bytes(original)
                    path.chmod(0o444)

    def test_final_receipt_rejects_recursive_bool_int_float_equivalence(self):
        self.execute()
        original = self.receipt.read_bytes()
        parsed = json.loads(original)
        mutations = self.numeric_json_mutations(parsed)
        self.assertGreater(len(mutations), 80)
        for json_path, replacement in mutations:
            label = "/".join(map(str, json_path)) + "->" + type(replacement).__name__
            with self.subTest(path=label):
                value = json.loads(original)
                self.replace_json_path(value, json_path, replacement)
                self.receipt.chmod(0o644)
                self.receipt.write_text(
                    json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
                    + "\n",
                    encoding="ascii",
                )
                self.receipt.chmod(0o444)
                try:
                    with self.assertRaises(onboarding_alias_compat.AliasCompatError):
                        onboarding_alias_compat.receipt_contract(
                            self.source,
                            trial_path=self.trial_path,
                            failure_path=self.failure_path,
                        )
                finally:
                    self.receipt.chmod(0o644)
                    self.receipt.write_bytes(original)
                    self.receipt.chmod(0o444)

    def test_build_pipeline_wrapper_revalidates_real_receipt_contract(self):
        result = self.execute()
        import build_pipeline

        receipt_path, value, summary = (
            build_pipeline.onboarding_alias_root_receipt_contract(self.source)
        )
        self.assertEqual(self.receipt, receipt_path)
        self.assertEqual(json.loads(self.receipt.read_text()), value)
        self.assertEqual(result["receipt"]["sha256"], summary["sha256"])
        consumed = self.source / onboarding_alias_compat.TRANSITION_CONSUMED_RELATIVE
        self.assertTrue(consumed.is_file())
        self.assertEqual(
            Path(self.transition["path"]).stat().st_ino, consumed.stat().st_ino
        )

    def test_transition_cli_executes_only_with_workspace_and_confirmation(self):
        self.reset_pending_transition()
        inventory_path = self.logs / "transition-inventory.json"
        inventory_path.write_text(json.dumps(self.inventory), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = onboarding_alias_compat.main(
                [
                    "--source-root",
                    str(self.source),
                    "--workspace-root",
                    str(self.physical_home / "workspace"),
                    "--expected-inventory",
                    str(inventory_path),
                    "--trial-report",
                    str(self.trial_path),
                    "--resume-failure-report",
                    str(self.failure_path),
                    "--prepare-home-alias-adoption",
                    "--confirm-home-alias-adoption",
                ]
            )
        self.assertEqual(0, code)
        result = json.loads(stdout.getvalue())["result"]
        self.assertEqual(onboarding_alias_compat.TRANSITION_KIND, result["value"]["kind"])
        self.assertEqual(onboarding_alias_compat.PRE_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(self.vite_temp.exists())

    def test_cli_default_is_json_read_only_and_execute_needs_confirmation(self):
        inventory_path = self.root / "inventory.json"
        inventory_path.write_text(json.dumps(self.inventory), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = onboarding_alias_compat.main(
                [
                    "--source-root",
                    str(self.source),
                    "--expected-inventory",
                    str(inventory_path),
                    "--trial-report",
                    str(self.trial_path),
                    "--resume-failure-report",
                    str(self.failure_path),
                ]
            )
        self.assertEqual(0, code)
        self.assertTrue(json.loads(stdout.getvalue())["result"]["read_only"])
        self.assertEqual(onboarding_alias_compat.PRE_SHA256, hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(self.receipt.exists())

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = onboarding_alias_compat.main(
                [
                    "--source-root",
                    str(self.source),
                    "--expected-inventory",
                    str(inventory_path),
                    "--trial-report",
                    str(self.trial_path),
                    "--resume-failure-report",
                    str(self.failure_path),
                    "--execute",
                ]
            )
        self.assertEqual(2, code)
        self.assertIn("confirm-alias-root-compat", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
