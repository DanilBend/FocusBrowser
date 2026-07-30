#!/usr/bin/env python3
"""Tests for the read-only frozen x86_64 relink planner."""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import x64_frozen_relink


def ninja_escape(value):
    return value.replace("$", "$$").replace(" ", "$ ").replace(":", "$:")


class FrozenRelinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "checkout/src"
        self.out = self.source / x64_frozen_relink.X64_OUT_SOURCE_RELATIVE
        self.out.mkdir(parents=True)
        self.ninja = self.source / x64_frozen_relink.PINNED_NINJA_SOURCE_RELATIVE
        self.ninja.parent.mkdir(parents=True)
        self.ninja.write_bytes(b"pinned fake Ninja\n")
        self.ninja.chmod(0o755)

        manifest_names = []
        for edge in x64_frozen_relink.FROZEN_LINK_EDGES:
            manifest = self.out / edge["manifest"]
            manifest.parent.mkdir(parents=True, exist_ok=True)
            outputs = " ".join(ninja_escape(value) for value in edge["outputs"])
            manifest.write_text(
                "build {}: {} input.o\n".format(outputs, edge["rule"]),
                encoding="utf-8",
            )
            manifest_names.append(edge["manifest"])
        self.nested_manifest = self.out / "obj/other/nested.ninja"
        self.nested_manifest.parent.mkdir(parents=True, exist_ok=True)
        self.nested_manifest.write_text("rule nested\n", encoding="utf-8")
        manifest_names.append("obj/other/nested.ninja")
        self.toolchain_names = (
            "toolchain.ninja",
            "clang_arm64/toolchain.ninja",
            "clang_arm64_for_rust_host_build_tools/toolchain.ninja",
            "clang_arm64_host_with_system_allocator/toolchain.ninja",
            "clang_arm64_v8_x64/toolchain.ninja",
            "clang_x64_with_system_allocator/toolchain.ninja",
        )
        for index, relative in enumerate(self.toolchain_names):
            toolchain = self.out / relative
            toolchain.parent.mkdir(parents=True, exist_ok=True)
            toolchain.write_text(
                "rule toolchain_{}\n".format(index), encoding="utf-8"
            )
            manifest_names.append(relative)
        self.top = self.out / "build.ninja"
        self.top.write_text(
            "ninja_required_version = 1.7.2\n"
            + "".join("subninja {}\n".format(value) for value in manifest_names),
            encoding="utf-8",
        )
        self.args_gn = self.out / "args.gn"
        self.build_ninja_d = self.out / "build.ninja.d"
        self.args_gn.write_bytes(b'target_cpu = "x64"\n')
        self.build_ninja_d.write_bytes(b"build.ninja: ../../BUILD.gn\n")
        git_dir = self.source / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text(
            x64_frozen_relink.EXPECTED_CHROMIUM_COMMIT + "\n", encoding="ascii"
        )
        entries = []
        for manifest in sorted(
            self.out.rglob("*.ninja"),
            key=lambda path: path.relative_to(self.out).as_posix().encode("utf-8"),
        ):
            data = manifest.read_bytes()
            entries.append(
                {
                    "path": manifest.relative_to(self.out).as_posix(),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        self.closure_sha256 = hashlib.sha256(
            json.dumps(
                entries, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("ascii")
        ).hexdigest()
        toolchain_entries = [
            entry for entry in entries if Path(entry["path"]).name == "toolchain.ninja"
        ]
        self.toolchain_closure_sha256 = hashlib.sha256(
            json.dumps(
                toolchain_entries,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        self.alias_contract = {
            "receipt": {
                "path": x64_frozen_relink.onboarding_alias_compat.HOME_ALIAS_RECEIPT_RELATIVE,
                "bytes": 1,
                "sha256": "d" * 64,
            },
            "volume": {"filesystem": "apfs", "volume_uuid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"},
            "alias": {"inode": 10},
            "mappings": {
                "workspace": {"physical": str(self.root)},
                "source": {"identity": {"inode": 20}},
                "developer": {"identity": {"inode": 30}},
            },
        }
        self.onboarding_contract = {
            "path": str(
                self.source
                / x64_frozen_relink.onboarding_alias_compat.RECEIPT_RELATIVE
            ),
            "bytes": 2,
            "sha256": "e" * 64,
            "value": {"home_alias_compatibility": self.alias_contract},
        }

        self.patchers = [
            mock.patch.object(
                x64_frozen_relink,
                "PINNED_NINJA_SHA256",
                hashlib.sha256(self.ninja.read_bytes()).hexdigest(),
            ),
            mock.patch.object(
                x64_frozen_relink,
                "EXPECTED_BUILD_NINJA_SHA256",
                hashlib.sha256(self.top.read_bytes()).hexdigest(),
            ),
            mock.patch.object(
                x64_frozen_relink,
                "EXPECTED_NINJA_FILE_COUNT",
                1 + len(manifest_names),
            ),
            mock.patch.object(
                x64_frozen_relink,
                "EXPECTED_SUBNINJA_REFERENCE_COUNT",
                len(manifest_names),
            ),
            mock.patch.object(
                x64_frozen_relink,
                "EXPECTED_CLOSURE_SHA256",
                self.closure_sha256,
            ),
            mock.patch.object(
                x64_frozen_relink,
                "EXPECTED_TOOLCHAIN_FILE_COUNT",
                len(self.toolchain_names),
            ),
            mock.patch.object(
                x64_frozen_relink,
                "EXPECTED_TOOLCHAIN_CLOSURE_SHA256",
                self.toolchain_closure_sha256,
            ),
            mock.patch.object(
                x64_frozen_relink,
                "EXPECTED_ARGS_GN_SHA256",
                hashlib.sha256(self.args_gn.read_bytes()).hexdigest(),
            ),
            mock.patch.object(
                x64_frozen_relink,
                "EXPECTED_BUILD_NINJA_D_SHA256",
                hashlib.sha256(self.build_ninja_d.read_bytes()).hexdigest(),
            ),
            mock.patch.object(
                x64_frozen_relink.onboarding_alias_compat,
                "validate_home_alias_receipt",
                return_value=self.alias_contract,
            ),
            mock.patch.object(
                x64_frozen_relink.onboarding_alias_compat,
                "receipt_contract",
                return_value=self.onboarding_contract,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary.cleanup()

    def snapshot(self):
        result = {}
        for path in sorted(self.source.rglob("*")):
            if path.is_file() and not path.is_symlink():
                relative = path.relative_to(self.source).as_posix()
                status = path.stat()
                result[relative] = (
                    status.st_size,
                    status.st_mtime_ns,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
        return result

    def structural_observation(self, plan, stdout):
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8")
        command = plan["command"]
        return {
            "schema": 1,
            "kind": x64_frozen_relink.STRUCTURAL_OBSERVATION_KIND,
            "structural_only": True,
            "execution_proven": False,
            "plan_id": plan["plan_id"],
            "executable": plan["ninja"],
            "working_directory_source_relative": command[
                "working_directory_source_relative"
            ],
            "arguments": command["arguments"],
            "environment": command["environment"],
            "unset_environment": command["unset_environment"],
            "exit_code": 0,
            "stdout": {
                "bytes": len(stdout),
                "sha256": hashlib.sha256(stdout).hexdigest(),
            },
            "stderr": {
                "bytes": 0,
                "sha256": hashlib.sha256(b"").hexdigest(),
            },
        }

    def test_plan_is_deterministic_read_only_and_contains_no_absolute_paths(self):
        before = self.snapshot()
        first = x64_frozen_relink.plan(self.source)
        second = x64_frozen_relink.plan(self.source)
        self.assertEqual(first, second)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(x64_frozen_relink.PRIVATE_PLAN_KIND, first["kind"])
        self.assertTrue(first["dry_run_only"])
        self.assertEqual(4, len(first["targets"]))
        self.assertEqual(19, len(first["outputs"]))
        self.assertEqual(
            x64_frozen_relink.PRIVATE_COMMAND_KIND, first["command"]["kind"]
        )
        self.assertIn("-n", first["command"]["arguments"])
        serialized = json.dumps(first, sort_keys=True)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn(str(self.source), serialized)
        self.assertEqual(
            len(self.toolchain_names),
            first["graph_binding"]["toolchains"]["files"],
        )
        self.assertEqual(
            self.toolchain_closure_sha256,
            first["graph_binding"]["toolchains"]["closure_sha256"],
        )
        self.assertEqual(0, first["safety"]["gn_invocations"])
        self.assertEqual(0, first["safety"]["network_operations"])
        self.assertTrue(first["safety"]["gn_regeneration_forbidden"])
        self.assertFalse(first["safety"]["execution_supported"])
        self.assertNotIn("gn", " ".join(first["command"]["arguments"]).lower())

    def test_plan_binds_fixed_compatibility_receipts_and_evidence_paths(self):
        receipt_contract = (
            x64_frozen_relink.onboarding_alias_compat.receipt_contract
        )
        receipt_contract.reset_mock()
        result = x64_frozen_relink.plan(self.source)
        receipt_contract.assert_called_once_with(
            self.source,
            trial_path=(
                self.root
                / "work/logs"
                / x64_frozen_relink.onboarding_alias_compat.TRIAL_REPORT_BASENAME
            ),
            failure_path=(
                self.root
                / "work/logs"
                / x64_frozen_relink.onboarding_alias_compat.FAILURE_REPORT_BASENAME
            ),
        )
        self.assertEqual(
            self.alias_contract["receipt"],
            result["graph_binding"]["home_alias_compatibility"]["receipt"],
        )
        self.assertEqual(
            x64_frozen_relink.onboarding_alias_compat.RECEIPT_RELATIVE,
            result["graph_binding"]["onboarding_alias_root_compatibility"][
                "receipt"
            ]["path"],
        )

    def test_plan_rejects_changed_top_manifest_hash(self):
        with mock.patch.object(
            x64_frozen_relink, "EXPECTED_BUILD_NINJA_SHA256", "0" * 64
        ):
            with self.assertRaisesRegex(
                x64_frozen_relink.FrozenRelinkError, "build.ninja SHA-256 changed"
            ):
                x64_frozen_relink.plan(self.source)

    def test_plan_rejects_nested_manifest_and_toolchain_mutation(self):
        nested_original = self.nested_manifest.read_bytes()
        self.nested_manifest.write_bytes(nested_original + b"# drift\n")
        try:
            with self.assertRaisesRegex(
                x64_frozen_relink.FrozenRelinkError,
                "frozen Ninja closure SHA-256 changed",
            ):
                x64_frozen_relink.plan(self.source)
        finally:
            self.nested_manifest.write_bytes(nested_original)

        toolchain = self.out / self.toolchain_names[-1]
        toolchain_original = toolchain.read_bytes()
        toolchain.write_bytes(toolchain_original + b"# toolchain drift\n")
        try:
            with self.assertRaisesRegex(
                x64_frozen_relink.FrozenRelinkError,
                "toolchain Ninja closure SHA-256 changed",
            ):
                x64_frozen_relink.plan(self.source)
        finally:
            toolchain.write_bytes(toolchain_original)

    def test_iterative_graph_rejects_disconnected_cycle_and_unreachable_nodes(self):
        disconnected_cycle = {
            "build.ninja": ("root-child.ninja",),
            "root-child.ninja": (),
            "cycle-a.ninja": ("cycle-b.ninja",),
            "cycle-b.ninja": ("cycle-a.ninja",),
        }
        with self.assertRaisesRegex(
            x64_frozen_relink.FrozenRelinkError, "cycle"
        ):
            x64_frozen_relink._verify_rooted_acyclic_closure(
                disconnected_cycle, set(disconnected_cycle)
            )

        unreachable = {
            "build.ninja": ("root-child.ninja",),
            "root-child.ninja": (),
            "unreachable.ninja": (),
        }
        with self.assertRaisesRegex(
            x64_frozen_relink.FrozenRelinkError, "not rooted"
        ):
            x64_frozen_relink._verify_rooted_acyclic_closure(
                unreachable, set(unreachable)
            )

        depth = 5000
        chain = {
            "build.ninja": ("node-0000.ninja",),
            **{
                "node-{:04d}.ninja".format(index): (
                    ("node-{:04d}.ninja".format(index + 1),)
                    if index + 1 < depth
                    else ()
                )
                for index in range(depth)
            },
        }
        result = x64_frozen_relink._verify_rooted_acyclic_closure(
            chain, set(chain)
        )
        self.assertEqual(depth + 1, result["nodes"])

    def test_logical_lines_treat_literal_double_dollar_as_non_continuation(self):
        data = b"build dollar$$: link input.o\nbuild next: phony input.o\n"
        self.assertEqual(
            ["build dollar$$: link input.o", "build next: phony input.o"],
            list(x64_frozen_relink._logical_lines(data, "fixture.ninja")),
        )
        x64_frozen_relink._verify_link_edge(
            "fixture.ninja", data, "link", ("dollar$",)
        )

    def test_plan_rejects_changed_pinned_ninja_hash(self):
        with mock.patch.object(x64_frozen_relink, "PINNED_NINJA_SHA256", "0" * 64):
            with self.assertRaisesRegex(
                x64_frozen_relink.FrozenRelinkError, "pinned Ninja SHA-256 changed"
            ):
                x64_frozen_relink.plan(self.source)

    def test_plan_rejects_include_and_builddir(self):
        for forbidden in (
            "include other.ninja\n",
            "include\tother.ninja\n",
            "builddir = elsewhere\n",
        ):
            with self.subTest(forbidden=forbidden):
                original = self.top.read_text(encoding="utf-8")
                self.top.write_text(original + forbidden, encoding="utf-8")
                try:
                    with self.assertRaisesRegex(
                        x64_frozen_relink.FrozenRelinkError, "include/builddir"
                    ):
                        x64_frozen_relink.inventory_frozen_closure(self.source)
                finally:
                    self.top.write_text(original, encoding="utf-8")

    def test_plan_rejects_unsafe_or_duplicate_subninja(self):
        original = self.top.read_text(encoding="utf-8")
        cases = (
            original + "subninja /tmp/evil.ninja\n",
            original + "subninja\t/tmp/evil.ninja\n",
            original + "subninja ../evil.ninja\n",
            original + "subninja obj/chrome/chrome_app_executable.ninja\n",
        )
        for contents in cases:
            with self.subTest(contents=contents[-50:]):
                self.top.write_text(contents, encoding="utf-8")
                try:
                    with self.assertRaises(x64_frozen_relink.FrozenRelinkError):
                        x64_frozen_relink.inventory_frozen_closure(self.source)
                finally:
                    self.top.write_text(original, encoding="utf-8")

    def test_plan_rejects_unreferenced_or_missing_manifest(self):
        extra = self.out / "unreferenced.ninja"
        extra.write_text("# unexpected\n", encoding="utf-8")
        with mock.patch.object(
            x64_frozen_relink,
            "EXPECTED_NINJA_FILE_COUNT",
            x64_frozen_relink.EXPECTED_NINJA_FILE_COUNT + 1,
        ):
            with self.assertRaisesRegex(
                x64_frozen_relink.FrozenRelinkError, "exactly close"
            ):
                x64_frozen_relink.inventory_frozen_closure(self.source)

    def test_plan_rejects_changed_edge_output_or_rule(self):
        edge = x64_frozen_relink.FROZEN_LINK_EDGES[0]
        manifest = self.out / edge["manifest"]
        original = manifest.read_text(encoding="utf-8")
        for changed in (
            original.replace(": link ", ": action "),
            original.replace("Focus$ Browser.dSYM", "Other.dSYM", 1),
        ):
            with self.subTest(changed=changed[:80]):
                manifest.write_text(changed, encoding="utf-8")
                try:
                    with self.assertRaisesRegex(
                        x64_frozen_relink.FrozenRelinkError, "frozen edge changed"
                    ):
                        x64_frozen_relink.inventory_frozen_closure(self.source)
                finally:
                    manifest.write_text(original, encoding="utf-8")

    def test_plan_rejects_symlinked_manifest_and_ninja(self):
        edge_manifest = self.out / x64_frozen_relink.FROZEN_LINK_EDGES[0]["manifest"]
        target = edge_manifest.with_suffix(".real")
        edge_manifest.rename(target)
        edge_manifest.symlink_to(target.name)
        with self.assertRaises(x64_frozen_relink.FrozenRelinkError):
            x64_frozen_relink.inventory_frozen_closure(self.source)

        edge_manifest.unlink()
        target.rename(edge_manifest)
        ninja_target = self.ninja.with_suffix(".real")
        self.ninja.rename(ninja_target)
        self.ninja.symlink_to(ninja_target.name)
        with self.assertRaisesRegex(
            x64_frozen_relink.FrozenRelinkError, "unsafe, or a symlink"
        ):
            x64_frozen_relink.plan(self.source)

    def test_pinned_ninja_rejects_symlinked_ancestor_component(self):
        ancestor = self.source / "third_party/dawn"
        real_ancestor = ancestor.with_name("dawn.real")
        ancestor.rename(real_ancestor)
        ancestor.symlink_to(real_ancestor.name, target_is_directory=True)

        with self.assertRaisesRegex(
            x64_frozen_relink.FrozenRelinkError,
            "traverses a symlink or unsafe directory component",
        ):
            x64_frozen_relink._verify_pinned_ninja(self.source)

    def test_pinned_ninja_rejects_post_read_path_and_mode_swap(self):
        replacement = self.ninja.with_name("ninja.replacement")
        replacement.write_bytes(self.ninja.read_bytes())
        replacement.chmod(0o644)
        real_read = x64_frozen_relink.os.read
        swapped = False

        def swap_after_read(descriptor, size):
            nonlocal swapped
            data = real_read(descriptor, size)
            if data and not swapped:
                swapped = True
                x64_frozen_relink.os.replace(replacement, self.ninja)
            return data

        with mock.patch.object(
            x64_frozen_relink.os, "read", side_effect=swap_after_read
        ), mock.patch.object(
            x64_frozen_relink.os,
            "access",
            side_effect=AssertionError("path-based access check is forbidden"),
        ), self.assertRaisesRegex(
            x64_frozen_relink.FrozenRelinkError,
            "changed while reading|path identity changed after reading",
        ):
            x64_frozen_relink._verify_pinned_ninja(self.source)

    def test_regular_file_read_rejects_post_read_path_swap(self):
        target = self.nested_manifest
        replacement = target.with_name("replacement.ninja")
        replacement.write_bytes(target.read_bytes())
        real_read = x64_frozen_relink.os.read
        swapped = False

        def swap_after_read(descriptor, size):
            nonlocal swapped
            data = real_read(descriptor, size)
            if data and not swapped:
                swapped = True
                x64_frozen_relink.os.replace(replacement, target)
            return data

        with mock.patch.object(
            x64_frozen_relink.os, "read", side_effect=swap_after_read
        ), self.assertRaisesRegex(
            x64_frozen_relink.FrozenRelinkError,
            "changed while reading|path identity changed",
        ):
            x64_frozen_relink._read_regular_file(target)

    def test_plan_and_revalidation_reject_args_depfile_and_head_drift(self):
        original_plan = x64_frozen_relink.plan(self.source)
        head = self.source / x64_frozen_relink.GIT_HEAD_SOURCE_RELATIVE
        cases = (
            (self.args_gn, b'target_cpu = "arm64"\n', "args.gn SHA-256 changed"),
            (self.build_ninja_d, b"build.ninja: ../../OTHER.gn\n", "build.ninja.d SHA-256 changed"),
            (head, b"0" * 40 + b"\n", "detached Chromium HEAD SHA-256 changed"),
        )
        for path, replacement, message in cases:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(replacement)
                try:
                    with self.assertRaisesRegex(
                        x64_frozen_relink.FrozenRelinkError, message
                    ):
                        x64_frozen_relink.revalidate_plan(
                            self.source, original_plan
                        )
                finally:
                    path.write_bytes(original)

    def test_revalidation_rejects_home_alias_onboarding_and_json_type_drift(self):
        original_plan = x64_frozen_relink.plan(self.source)
        changed_alias = json.loads(json.dumps(self.alias_contract))
        changed_alias["alias"]["inode"] += 1
        changed_onboarding = json.loads(json.dumps(self.onboarding_contract))
        changed_onboarding["value"]["home_alias_compatibility"] = changed_alias
        with mock.patch.object(
            x64_frozen_relink.onboarding_alias_compat,
            "validate_home_alias_receipt",
            return_value=changed_alias,
        ), mock.patch.object(
            x64_frozen_relink.onboarding_alias_compat,
            "receipt_contract",
            return_value=changed_onboarding,
        ), self.assertRaisesRegex(
            x64_frozen_relink.FrozenRelinkError, "plan changed"
        ):
            x64_frozen_relink.revalidate_plan(self.source, original_plan)

        changed_onboarding = json.loads(json.dumps(self.onboarding_contract))
        changed_onboarding["value"]["unexpected"] = True
        with mock.patch.object(
            x64_frozen_relink.onboarding_alias_compat,
            "receipt_contract",
            return_value=changed_onboarding,
        ), self.assertRaisesRegex(
            x64_frozen_relink.FrozenRelinkError, "plan changed"
        ):
            x64_frozen_relink.revalidate_plan(self.source, original_plan)

        type_changed = json.loads(json.dumps(original_plan))
        type_changed["schema"] = True
        with self.assertRaisesRegex(
            x64_frozen_relink.FrozenRelinkError, "plan changed"
        ):
            x64_frozen_relink.revalidate_plan(self.source, type_changed)

    def test_parser_accepts_exact_no_work_baseline(self):
        expected = {"status": "no-work", "edges": 0, "descriptions": []}
        self.assertEqual(
            expected,
            x64_frozen_relink.parse_dry_run_output(b"ninja: no work to do.\n"),
        )
        self.assertEqual(
            expected,
            x64_frozen_relink.parse_dry_run_output("ninja: no work to do."),
        )

    def four_edge_output(self, descriptions=None):
        descriptions = descriptions or x64_frozen_relink._allowed_descriptions()
        return "\n".join(
            "{}[{}/4] {}".format(
                x64_frozen_relink.PRIVATE_STATUS_PREFIX, index, description
            )
            for index, description in enumerate(descriptions, 1)
        ) + "\n"

    def test_parser_accepts_only_exact_four_edge_allowlist(self):
        output = self.four_edge_output()
        result = x64_frozen_relink.parse_dry_run_output(output)
        self.assertEqual("four-edge-relink", result["status"])
        self.assertEqual(4, result["edges"])
        self.assertEqual(list(x64_frozen_relink._allowed_descriptions()), result["descriptions"])

    def test_parser_rejects_gn_compile_action_copy_and_extra_edge(self):
        forbidden = (
            "GN gen out/FocusMacX64",
            "CXX obj/unexpected.o",
            "ACTION //chrome:unexpected",
            "COPY unexpected",
            "SOLINK unexpected.dylib",
        )
        allowed = list(x64_frozen_relink._allowed_descriptions())
        for description in forbidden:
            with self.subTest(description=description):
                changed = list(allowed)
                changed[2] = description
                with self.assertRaisesRegex(
                    x64_frozen_relink.FrozenRelinkError, "four-edge relink allowlist"
                ):
                    x64_frozen_relink.parse_dry_run_output(
                        self.four_edge_output(changed)
                    )
        with self.assertRaises(x64_frozen_relink.FrozenRelinkError):
            x64_frozen_relink.parse_dry_run_output(
                self.four_edge_output()
                + "{}[4/4] {}\n".format(
                    x64_frozen_relink.PRIVATE_STATUS_PREFIX, allowed[0]
                )
            )

    def test_parser_rejects_default_status_noise_duplicates_and_bad_counters(self):
        allowed = list(x64_frozen_relink._allowed_descriptions())
        cases = (
            "ninja: Entering directory `out/FocusMacX64'\nninja: no work to do.\n",
            self.four_edge_output([allowed[0], allowed[0], allowed[2], allowed[3]]),
            self.four_edge_output().replace("[2/4]", "[3/4]", 1),
            "\n",
            b"\xff",
        )
        for output in cases:
            with self.subTest(output=repr(output)[:100]):
                with self.assertRaises(x64_frozen_relink.FrozenRelinkError):
                    x64_frozen_relink.parse_dry_run_output(output)

    def test_parser_enforces_byte_line_and_line_count_bounds(self):
        cases = (
            b"x" * (x64_frozen_relink.MAX_DRY_RUN_OUTPUT_BYTES + 1),
            ("x" * (x64_frozen_relink.MAX_DRY_RUN_LINE_BYTES + 1)).encode(),
            b"x\n" * (x64_frozen_relink.MAX_DRY_RUN_OUTPUT_LINES + 1),
        )
        for output in cases:
            with self.subTest(length=len(output)):
                with self.assertRaises(x64_frozen_relink.FrozenRelinkError):
                    x64_frozen_relink.parse_dry_run_output(output)

    def test_structural_observation_revalidates_full_plan_before_and_after(self):
        plan = x64_frozen_relink.plan(self.source)
        preflight = x64_frozen_relink.structural_preflight(self.source, plan)
        stdout = b"ninja: no work to do.\n"
        observation = self.structural_observation(plan, stdout)
        real_revalidate = x64_frozen_relink.revalidate_plan
        with mock.patch.object(
            x64_frozen_relink,
            "revalidate_plan",
            wraps=real_revalidate,
        ) as revalidate:
            result = x64_frozen_relink.validate_structural_observation(
                self.source, plan, preflight, observation, stdout
            )
        self.assertEqual(2, revalidate.call_count)
        self.assertEqual("structural-only", result["status"])
        self.assertFalse(result["execution_proven"])
        self.assertFalse(preflight["execution_proven"])
        self.assertFalse(hasattr(x64_frozen_relink, "execution_preflight"))
        self.assertFalse(
            hasattr(x64_frozen_relink, "validate_execution_observation")
        )

        changed = json.loads(json.dumps(observation))
        changed["exit_code"] = True
        with self.assertRaisesRegex(
            x64_frozen_relink.FrozenRelinkError, "structural observation"
        ):
            x64_frozen_relink.validate_structural_observation(
                self.source, plan, preflight, changed, stdout
            )

    def test_structural_preflight_rejects_graph_drift_before_capture(self):
        plan = x64_frozen_relink.plan(self.source)
        preflight = x64_frozen_relink.structural_preflight(self.source, plan)
        observation = self.structural_observation(
            plan, b"ninja: no work to do.\n"
        )
        original = self.args_gn.read_bytes()
        self.args_gn.write_bytes(b'target_cpu = "arm64"\n')
        try:
            with self.assertRaisesRegex(
                x64_frozen_relink.FrozenRelinkError, "args.gn SHA-256 changed"
            ):
                x64_frozen_relink.validate_structural_observation(
                    self.source,
                    plan,
                    preflight,
                    observation,
                    b"ninja: no work to do.\n",
                )
        finally:
            self.args_gn.write_bytes(original)

    def test_structural_postflight_rejects_drift_after_output_parse(self):
        plan = x64_frozen_relink.plan(self.source)
        preflight = x64_frozen_relink.structural_preflight(self.source, plan)
        stdout = b"ninja: no work to do.\n"
        observation = self.structural_observation(plan, stdout)
        original = self.nested_manifest.read_bytes()
        real_parse = x64_frozen_relink.parse_dry_run_output

        def drift_after_parse(output):
            result = real_parse(output)
            self.nested_manifest.write_bytes(original + b"# post-capture drift\n")
            return result

        try:
            with mock.patch.object(
                x64_frozen_relink,
                "parse_dry_run_output",
                side_effect=drift_after_parse,
            ), self.assertRaisesRegex(
                x64_frozen_relink.FrozenRelinkError,
                "frozen Ninja closure SHA-256 changed",
            ):
                x64_frozen_relink.validate_structural_observation(
                    self.source, plan, preflight, observation, stdout
                )
        finally:
            self.nested_manifest.write_bytes(original)

    def test_cli_plan_and_parser_are_json_only_and_have_no_execute_mode(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = x64_frozen_relink.main(
                ["plan", "--source-root", str(self.source)]
            )
        self.assertEqual(0, result)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["result"]["dry_run_only"])

        captured = self.root / "dry-run.txt"
        captured.write_text("ninja: no work to do.\n", encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = x64_frozen_relink.main(
                ["parse-dry-run", "--input", str(captured)]
            )
        self.assertEqual(0, result)
        self.assertEqual("no-work", json.loads(stdout.getvalue())["result"]["status"])
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                x64_frozen_relink.main(["execute"])

    def test_cli_parser_rejects_oversized_file_before_reading(self):
        captured = self.root / "oversized-dry-run.txt"
        captured.write_bytes(
            b"x" * (x64_frozen_relink.MAX_DRY_RUN_OUTPUT_BYTES + 1)
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = x64_frozen_relink.main(
                ["parse-dry-run", "--input", str(captured)]
            )
        self.assertEqual(2, result)
        self.assertEqual("", stdout.getvalue())
        payload = json.loads(stderr.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("byte limit", payload["error"])


if __name__ == "__main__":
    unittest.main()
