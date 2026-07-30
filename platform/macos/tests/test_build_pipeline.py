#!/usr/bin/env python3
"""Unit tests for the staged, low-space macOS build pipeline."""

import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import build_pipeline
import alias_resume_runner


class BuildPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.checkout = self.root / "checkout"
        self.source = self.checkout / "src"
        self.source.mkdir(parents=True)
        self.source = self.source.resolve()
        self.checkout = self.source.parent
        self.depot = self.checkout / "depot_tools"
        self.depot.mkdir()
        for name in ("gclient", "gn", "autoninja"):
            path = self.depot / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)
        self.developer = self.root / "Xcode.app/Contents/Developer"
        self.generator = self.source / build_pipeline.prepare_source.ONBOARDING_GENERATOR
        self.generator.parent.mkdir(parents=True)
        self.generator.write_bytes(b"generator fixture\n")
        self.generator_hash = build_pipeline.sha256_file(self.generator)
        self.generator_hash_patch = mock.patch.object(
            build_pipeline.prepare_source,
            "ONBOARDING_GENERATOR_SHA256",
            self.generator_hash,
        )
        self.generator_hash_patch.start()
        self.generated_strings = (
            self.source / build_pipeline.prepare_source.ONBOARDING_STRINGS_OUTPUT
        )

        self.generated_strings.parent.mkdir(parents=True)
        self.generated_strings.write_bytes(
            (
                build_pipeline.prepare_source.REPO_ROOT
                / "source_overrides"
                / build_pipeline.prepare_source.ONBOARDING_STRINGS_OUTPUT
            ).read_bytes()
        )
        self.ninja = self.source / build_pipeline.DAWN_NINJA_RELATIVE
        self.ninja.parent.mkdir(parents=True)
        self.ninja.write_text("#!/bin/sh\necho 1.12.1\n", encoding="utf-8")
        self.ninja.chmod(0o755)
        self.ninja_report = {
            "path": str(self.ninja),
            "relative_path": build_pipeline.DAWN_NINJA_RELATIVE,
            "architecture": "arm64",
            "sha256": "a" * 64,
            "version": build_pipeline.NINJA_VERSION,
            "cipd_package": "infra/3pp/tools/ninja/mac-arm64",
            "cipd_version": build_pipeline.NINJA_CIPD_VERSION,
            "cipd_instance": build_pipeline.NINJA_CIPD_INSTANCE_BY_HOST["arm64"],
        }
        self.linkedit_tools = {
            "selected": {
                "path": str(self.root / "Xcode-strip"),
                "relative_to_developer_dir": (
                    build_pipeline.XCODE27_LINKEDIT_STRIP_RELATIVE
                ),
                "sha256": build_pipeline.XCODE27_LINKEDIT_STRIP_SHA256,
            },
            "replaced": {"fixture": True},
        }
        self.real_ninja_contract = build_pipeline.ninja_contract
        self.ninja_patch = mock.patch.object(
            build_pipeline, "ninja_contract", return_value=self.ninja_report
        )
        self.ninja_patch.start()
        self.cache_marker_report = {
            "path": str(
                self.root
                / "dependency-cache"
                / build_pipeline.prepare_source.DEPENDENCY_CACHE_MARKER
            ),
            "sha256": "d" * 64,
            "archive_count": len(build_pipeline.prepare_source.DEPENDENCY_CONTRACTS),
            "total_bytes": 143176580,
            "archives": {
                name: value["sha256"]
                for name, value in build_pipeline.prepare_source.DEPENDENCY_CONTRACTS.items()
            },
        }
        self.post_dependency_tree = {
            "ownership_roots": list(
                build_pipeline.prepare_source.DEPENDENCY_OWNERSHIP_ROOTS
            ),
            "regular_files": build_pipeline.prepare_source.DEPENDENCY_INSTALL_REGULAR_FILES,
            "logical_bytes": build_pipeline.prepare_source.DEPENDENCY_INSTALL_LOGICAL_BYTES,
            "sha256": build_pipeline.prepare_source.DEPENDENCY_INSTALL_SHA256,
            "installed_symlinks": 0,
            "installed_special_files": 0,
        }
        self.cache_marker_patch = mock.patch.object(
            build_pipeline.prepare_source,
            "validate_dependency_cache_marker",
            return_value=self.cache_marker_report,
        )
        self.installed_tree_patch = mock.patch.object(
            build_pipeline.prepare_source,
            "installed_dependency_tree",
            return_value=self.post_dependency_tree,
        )
        self.cache_marker_patch.start()
        self.installed_tree_patch.start()
        self.onboarding_node_report = {
            "path": str(self.source / "third_party/node/mac_arm64/node"),
            "relative_path": "third_party/node/mac_arm64/node",
            "architecture": "arm64",
            "version": build_pipeline.prepare_source.ONBOARDING_NODE_VERSION,
            "sha256": "e" * 64,
        }
        self.onboarding_node_patch = mock.patch.object(
            build_pipeline.prepare_source,
            "onboarding_node_contract",
            return_value=self.onboarding_node_report,
        )
        self.onboarding_node_patch.start()
        self.write_acquisition_marker()
        self.write_tool_receipt()
        self.write_preparation_receipt()

    @staticmethod
    def is_package_command(command):
        return (
            isinstance(command, list)
            and len(command) > 1
            and Path(command[1]).name == "package_local_dmg.py"
            and "--output" in command
        )

    @staticmethod
    def package_command_output(command):
        return Path(command[command.index("--output") + 1])

    def tearDown(self):
        self.onboarding_node_patch.stop()
        self.installed_tree_patch.stop()
        self.cache_marker_patch.stop()
        self.ninja_patch.stop()
        self.generator_hash_patch.stop()
        self.temporary.cleanup()

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        return path

    def alias_context_fixture(self):
        return build_pipeline.AliasContext(
            logical_home=self.root,
            physical_home=self.root,
            logical_workspace=self.root,
            physical_workspace=self.root,
            logical_source=self.source,
            physical_source=self.source,
            logical_developer=self.developer,
            physical_developer=self.developer,
            logical_repo=build_pipeline.MACOS_DIR.parent.parent,
            physical_repo=build_pipeline.MACOS_DIR.parent.parent,
            volume_uuid="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
        )

    def onboarding_preparation_projection_fixture(self):
        return {
            "schema": 1,
            "kind": (
                build_pipeline.onboarding_alias_compat.PREPARATION_PROJECTION_KIND
            ),
            "workspace": str(self.root),
            "tree_projection": {
                "relative_path": (
                    build_pipeline.onboarding_alias_compat.SOURCE_RELATIVE
                ),
                "observed": {
                    "mode": 0o644,
                    "bytes": build_pipeline.onboarding_alias_compat.POST_BYTES,
                    "sha256": (
                        build_pipeline.onboarding_alias_compat.POST_SHA256
                    ),
                },
                "projected": {
                    "mode": 0o644,
                    "bytes": build_pipeline.onboarding_alias_compat.PRE_BYTES,
                    "sha256": (
                        build_pipeline.onboarding_alias_compat.PRE_SHA256
                    ),
                },
            },
            "transition": {
                "path": str(self.root / "transition.json"),
                "bytes": 1234,
                "sha256": "a" * 64,
                "consumed_link": {
                    "path": "out/FocusMacOnboardingAliasTransition.json",
                    "bytes": 1234,
                    "sha256": "a" * 64,
                    "inode": 42,
                },
            },
            "safety": {
                "projected_files": 1,
                "source_state": "post",
                "home_alias_validation_invocations": 0,
                "network_operations": 0,
                "gn_invocations": 0,
                "ninja_invocations": 0,
            },
        }

    def write_acquisition_marker(self):
        marker = self.checkout / build_pipeline.acquire_chromium.COMPLETE_MARKER
        return self.write_json(
            marker,
            {
                "status": "acquisition_complete",
                "execution_requested": True,
                "network_performed": True,
                "destination": str(self.checkout),
                "pins": {
                    "chromium_version": build_pipeline.acquire_chromium.CHROMIUM_VERSION,
                    "chromium_tag": build_pipeline.acquire_chromium.CHROMIUM_TAG,
                    "chromium_commit": build_pipeline.acquire_chromium.CHROMIUM_COMMIT,
                    "depot_tools_commit": build_pipeline.acquire_chromium.DEPOT_TOOLS_COMMIT,
                },
                "verification": {
                    "source_root": str(self.source),
                    "chromium_version": build_pipeline.acquire_chromium.CHROMIUM_VERSION,
                    "chromium_commit": build_pipeline.acquire_chromium.CHROMIUM_COMMIT,
                    "depot_tools_commit": build_pipeline.acquire_chromium.DEPOT_TOOLS_COMMIT,
                },
                "gclient": {
                    "target_os": ["mac"],
                    "target_os_only": True,
                    "hooks_during_acquisition": False,
                    "git_cache": False,
                    "spec_sha256": build_pipeline.acquire_chromium.GCLIENT_SPEC_SHA256,
                },
            },
        )

    def write_tool_receipt(self):
        marker = self.checkout / build_pipeline.acquire_chromium.COMPLETE_MARKER
        return self.write_json(
            self.checkout / build_pipeline.TOOL_RECEIPT,
            {
                "schema": 1,
                "hooks_complete": True,
                "chromium_commit": build_pipeline.acquire_chromium.CHROMIUM_COMMIT,
                "depot_tools_commit": build_pipeline.acquire_chromium.DEPOT_TOOLS_COMMIT,
                "source_root": str(self.source),
                "developer_dir": str(self.developer),
                "acquisition_marker_sha256": build_pipeline.sha256_file(marker),
                "gclient_command": [str(self.depot / "gclient"), "runhooks"],
                "gn_version": "150.0",
                "tool_sha256": {
                    name: build_pipeline.sha256_file(self.depot / name)
                    for name in ("gclient", "gn", "autoninja")
                },
                "post_hooks_free_bytes": 75 * build_pipeline.GIB,
                "build_executed": False,
            },
        )

    def write_preparation_receipt(self):
        prepared_paths = {
            "chrome/BUILD.gn": "chrome/BUILD.gn",
            build_pipeline.prepare_source.INSTALLER_MAC_BUILD_GN: build_pipeline.prepare_source.INSTALLER_MAC_BUILD_GN,
            "chrome/app/theme/chromium/BRANDING": "chrome/app/theme/chromium/BRANDING",
            "chrome/VERSION": "chrome/VERSION",
            build_pipeline.prepare_source.MAC_ICON_DESTINATION: build_pipeline.prepare_source.MAC_ICON_DESTINATION,
            "onboarding/strings.ts": build_pipeline.prepare_source.ONBOARDING_STRINGS_OUTPUT,
            "args_gn/arm64": build_pipeline.ARM_OUT + "/args.gn",
            "args_gn/x64": build_pipeline.X64_OUT + "/args.gn",
        }
        post_hashes = {}
        for label, relative in prepared_paths.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(label + "\n", encoding="utf-8")
            post_hashes[label] = build_pipeline.sha256_file(path)
        acquisition = self.checkout / build_pipeline.acquire_chromium.COMPLETE_MARKER
        tools = self.checkout / build_pipeline.TOOL_RECEIPT
        return self.write_json(
            self.source / build_pipeline.PREPARATION_RECEIPT,
            {
                "schema": build_pipeline.prepare_source.PREPARATION_RECEIPT_SCHEMA,
                "chromium_version": build_pipeline.focus_macos.PINNED_CHROMIUM_VERSION,
                "offline": True,
                "network_operations": 0,
                "acquisition": {
                    "path": str(acquisition),
                    "sha256": build_pipeline.sha256_file(acquisition),
                    "source_root": str(self.source),
                },
                "tool_bootstrap": {
                    "path": str(tools),
                    "sha256": build_pipeline.sha256_file(tools),
                    "source_root": str(self.source),
                },
                "patch_contract": {
                    "common_filtered_count": build_pipeline.focus_macos.EXPECTED_FULL_PATCH_BODY_COUNT,
                    "common_filtered_order_sha256": build_pipeline.focus_macos.FILTERED_COMMON_SERIES_SHA256,
                    "common_full_body_sha256": build_pipeline.focus_macos.EXPECTED_FULL_PATCH_BODY_SHA256,
                    "platform": build_pipeline.focus_macos.validate_platform_patch_series(),
                },
                "preparation_execution": (
                    build_pipeline.prepare_source.fresh_preparation_execution_report()
                ),
                "recovery_checkpoint": None,
                "dependency_contract": {
                    "manifest_sha256": build_pipeline.prepare_source.DEPS_INI_SHA256,
                    "archives": {
                        name: contract["sha256"]
                        for name, contract in build_pipeline.prepare_source.DEPENDENCY_CONTRACTS.items()
                    },
                    "cache_marker": self.cache_marker_report,
                    "install_inventory": {
                        **self.post_dependency_tree,
                        "components": list(
                            build_pipeline.prepare_source.DEPENDENCY_CONTRACTS
                        ),
                        "omitted_symlinks": {
                            "onboarding": {
                                "count": 10,
                                "sha256": build_pipeline.prepare_source.SHARED_DEPENDENCY_CONTRACTS[
                                    "onboarding"
                                ]["omitted_symlink_sha256"],
                            }
                        },
                    },
                    "post_prepare_tree": self.post_dependency_tree,
                },
                "pruning_contract": {
                    "manifest_sha256": build_pipeline.prepare_source.PRUNING_LIST_SHA256,
                    "listed_files": build_pipeline.prepare_source.PRUNING_ENTRY_COUNT,
                    "files_removed": build_pipeline.prepare_source.PRUNING_EXPECTED_REMOVAL_COUNT,
                    "already_absent_files": build_pipeline.prepare_source.PRUNING_ALREADY_ABSENT_COUNT,
                    "already_absent_sha256": build_pipeline.prepare_source.PRUNING_ALREADY_ABSENT_SHA256,
                    "contingent_paths_pruned": False,
                    "directory_pruning_executed": False,
                },
                "overlay_contract": {
                    "count": build_pipeline.focus_macos.EXPECTED_FULL_OVERLAY_BODY_COUNT,
                    "sha256": build_pipeline.focus_macos.EXPECTED_FULL_OVERLAY_BODY_SHA256,
                },
                "resource_contract": {
                    "count": build_pipeline.prepare_source.RESOURCE_BODY_COUNT,
                    "sha256": build_pipeline.prepare_source.RESOURCE_BODY_SHA256,
                },
                "icns_sha256": build_pipeline.focus_macos.FOCUS_ICNS_SHA256,
                "localized_strings_contract": {
                    "generator": build_pipeline.prepare_source.ONBOARDING_GENERATOR,
                    "generator_sha256": self.generator_hash,
                    "node": self.onboarding_node_report,
                    "output": build_pipeline.prepare_source.ONBOARDING_STRINGS_OUTPUT,
                    "baseline_bytes": build_pipeline.prepare_source.ONBOARDING_STRINGS_BASELINE_BYTES,
                    "baseline_sha256": build_pipeline.prepare_source.ONBOARDING_STRINGS_BASELINE_SHA256,
                    "output_bytes": (
                        self.source / build_pipeline.prepare_source.ONBOARDING_STRINGS_OUTPUT
                    ).stat().st_size,
                    "output_sha256": build_pipeline.sha256_file(
                        self.source / build_pipeline.prepare_source.ONBOARDING_STRINGS_OUTPUT
                    ),
                    "runs": 2,
                    "byte_identical": True,
                    "network_operations": 0,
                },
                "post_prepare_sha256": post_hashes,
                "build_executed": False,
                "signing_executed": False,
                "packaging_executed": False,
            },
        )

    def make_app(self, parent, architecture="arm64"):
        app = parent / build_pipeline.APP_NAME
        executable = app / "Contents/MacOS/Focus Browser"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(self.macho64_bytes(architecture=architecture))
        info = {
            "CFBundleIdentifier": build_pipeline.focus_macos.BUNDLE_ID,
            "CFBundleExecutable": "Focus Browser",
        }
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        return app

    def macho64_bytes(
        self, architecture="arm64", stroff=0x108, signature_offset=0x200, endian="<"
    ):
        cpu_type = {"arm64": 0x0100000C, "x86_64": 0x01000007}[architecture]
        segment = struct.pack(
            endian + "II16sQQQQiiII",
            build_pipeline._LC_SEGMENT_64,
            72,
            b"__LINKEDIT\0\0\0\0\0\0",
            0,
            0x200,
            0x100,
            0x200,
            7,
            1,
            0,
            0,
        )
        symtab = struct.pack(
            endian + "6I",
            build_pipeline._LC_SYMTAB,
            24,
            0x100,
            1,
            stroff,
            16,
        )
        signature = struct.pack(
            endian + "4I",
            build_pipeline._LC_CODE_SIGNATURE,
            16,
            signature_offset,
            16,
        )
        commands = segment + symtab + signature
        header = struct.pack(
            endian + "8I",
            0xFEEDFACF,
            cpu_type,
            0,
            2,
            3,
            len(commands),
            0,
            0,
        )
        return (header + commands).ljust(0x300, b"\0")

    def fat_macho_bytes(self, slices, endian=">", uses_64=False):
        magic = {
            (">", False): b"\xca\xfe\xba\xbe",
            ("<", False): b"\xbe\xba\xfe\xca",
            (">", True): b"\xca\xfe\xba\xbf",
            ("<", True): b"\xbf\xba\xfe\xca",
        }[(endian, uses_64)]
        entry_size = 32 if uses_64 else 20
        offset = 0x1000
        entries = []
        payload = bytearray(offset)
        for architecture, body in slices:
            cpu_type = {"arm64": 0x0100000C, "x86_64": 0x01000007}[
                architecture
            ]
            if uses_64:
                entries.append(
                    struct.pack(endian + "IIQQII", cpu_type, 0, offset, len(body), 12, 0)
                )
            else:
                entries.append(
                    struct.pack(endian + "IIIII", cpu_type, 0, offset, len(body), 12)
                )
            payload.extend(body)
            offset = (len(payload) + 0xFFF) & ~0xFFF
            payload.extend(b"\0" * (offset - len(payload)))
        header = magic + struct.pack(endian + "I", len(entries)) + b"".join(entries)
        payload[: len(header)] = header
        return bytes(payload)

    def lipo_result(self, architectures):
        return subprocess.CompletedProcess(
            ["lipo"], 0, stdout=" ".join(architectures) + "\n", stderr=""
        )

    def write_slice_receipt(self, out, architecture):
        expected = "arm64" if architecture == "arm64" else "x86_64"
        args_path = out / "args.gn"
        prep = self.source / build_pipeline.PREPARATION_RECEIPT
        xcode27 = self.source / build_pipeline.XCODE27_COMPAT_RECEIPT
        if not xcode27.exists():
            self.write_json(xcode27, {"fixture": True})
        seatbelt = self.source / build_pipeline.XCODE27_SEATBELT_RECEIPT
        if not seatbelt.exists():
            self.write_json(seatbelt, {"fixture": True})
        screen_ai = self.source / build_pipeline.SCREEN_AI_DISABLED_RECEIPT
        if not screen_ai.exists():
            self.write_json(screen_ai, {"fixture": True})
        linkedit = self.source / build_pipeline.XCODE27_LINKEDIT_STRIP_RECEIPT
        if not linkedit.exists():
            self.write_json(linkedit, {"fixture": True})
        generated_toolchain = out / "toolchain.ninja"
        generated_toolchain.write_text(
            "command = linker -Wcrl,strippath,{}\n".format(
                self.linkedit_tools["selected"]["path"]
            ),
            encoding="utf-8",
        )
        generated_linkedit = build_pipeline.generated_linkedit_strip_contract(
            out, self.linkedit_tools
        )
        return self.write_json(
            out / build_pipeline.SLICE_RECEIPT_NAME,
            {
                "schema": 1,
                "architecture": architecture,
                "mach_o_architecture": expected,
                "source_root": str(self.source),
                "app": {"architectures": [expected]},
                "args_gn_sha256": build_pipeline.sha256_file(args_path),
                "preparation_receipt_sha256": build_pipeline.sha256_file(prep),
                "xcode27_compatibility_receipt_sha256": (
                    build_pipeline.sha256_file(xcode27)
                ),
                "xcode27_seatbelt_compatibility_receipt_sha256": (
                    build_pipeline.sha256_file(seatbelt)
                ),
                "screen_ai_disabled_compatibility_receipt_sha256": (
                    build_pipeline.sha256_file(screen_ai)
                ),
                "xcode27_linkedit_strip_compatibility_receipt_sha256": (
                    build_pipeline.sha256_file(linkedit)
                ),
                "generated_linkedit_strip": generated_linkedit,
                "ninja": self.ninja_report,
                "build_complete": True,
            },
        )

    def test_contract_receipts_bind_exact_checkout_and_marker(self):
        build_pipeline.acquisition_contract(self.source)
        build_pipeline.tool_receipt_contract(self.source)
        receipt = json.loads(
            (self.checkout / build_pipeline.TOOL_RECEIPT).read_text(encoding="utf-8")
        )
        receipt["source_root"] = str(self.root / "replayed/src")
        self.write_json(self.checkout / build_pipeline.TOOL_RECEIPT, receipt)
        with self.assertRaisesRegex(build_pipeline.PipelineError, "source_root"):
            build_pipeline.tool_receipt_contract(self.source)

    def test_preparation_receipt_requires_recovery_provenance_field(self):
        receipt_path = self.source / build_pipeline.PREPARATION_RECEIPT
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt.pop("recovery_checkpoint")
        self.write_json(receipt_path, receipt)
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "lacks recovery provenance"
        ):
            build_pipeline.preparation_contract(self.source)

    def test_preparation_contract_projects_exact_consumed_post_for_downstream(self):
        context = self.alias_context_fixture()
        projection = self.onboarding_preparation_projection_fixture()
        installed = build_pipeline.prepare_source.installed_dependency_tree
        installed.reset_mock()
        with mock.patch.object(
            build_pipeline.onboarding_alias_compat,
            "preparation_dependency_tree_projection_contract",
            side_effect=(projection, projection),
        ) as projection_contract:
            path, _ = build_pipeline.preparation_contract(
                self.source, alias_context=context
            )
        self.assertEqual(self.source / build_pipeline.PREPARATION_RECEIPT, path)
        installed.assert_called_once_with(
            self.source,
            build_pipeline.prepare_source.DEPENDENCY_CONTRACTS,
            exact_file_projection=projection["tree_projection"],
        )
        self.assertEqual(2, projection_contract.call_count)
        for call in projection_contract.call_args_list:
            self.assertEqual((self.source, self.root), call.args)

    def test_preparation_projection_rejects_race_malformed_or_unrelated_drift(self):
        context = self.alias_context_fixture()
        projection = self.onboarding_preparation_projection_fixture()
        changed = json.loads(json.dumps(projection))
        changed["transition"]["sha256"] = "b" * 64
        with mock.patch.object(
            build_pipeline.onboarding_alias_compat,
            "preparation_dependency_tree_projection_contract",
            side_effect=(projection, changed),
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "changed during dependency scan"
        ):
            build_pipeline.preparation_contract(self.source, alias_context=context)

        malformed = json.loads(json.dumps(projection))
        malformed["tree_projection"]["observed"]["sha256"] = "0" * 64
        with mock.patch.object(
            build_pipeline.onboarding_alias_compat,
            "preparation_dependency_tree_projection_contract",
            return_value=malformed,
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "projection contract mismatch"
        ):
            build_pipeline.preparation_contract(self.source, alias_context=context)

        unrelated = dict(self.post_dependency_tree)
        unrelated["sha256"] = "0" * 64
        with mock.patch.object(
            build_pipeline.onboarding_alias_compat,
            "preparation_dependency_tree_projection_contract",
            side_effect=(projection, projection),
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "installed_dependency_tree",
            return_value=unrelated,
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "tree changed after preparation"
        ):
            build_pipeline.preparation_contract(self.source, alias_context=context)

    def test_gn_compat_patch_is_hash_pinned_scoped_and_semantic(self):
        patch = build_pipeline.GN_COMPAT_PATCH
        self.assertEqual(
            build_pipeline.GN_COMPAT_PATCH_SHA256,
            build_pipeline.sha256_file(patch),
        )
        text = patch.read_text(encoding="utf-8")
        self.assertEqual(
            {
                "chrome/BUILD.gn",
                "content/shell/BUILD.gn",
                "chrome/test/BUILD.gn",
            },
            {
                line.removeprefix("--- a/")
                for line in text.splitlines()
                if line.startswith("--- a/")
            },
        )
        self.assertEqual(4, text.count("if (enable_swiftshader)"))
        self.assertEqual(1, text.count("if (safe_browsing_mode != 0)"))
        self.assertNotIn("safe_browsing_mode=1", text)

    def test_xcode27_patch_is_upstream_pinned_scoped_and_semantic(self):
        patch = build_pipeline.XCODE27_COMPAT_PATCH
        self.assertEqual(
            build_pipeline.XCODE27_COMPAT_PATCH_SHA256,
            build_pipeline.sha256_file(patch),
        )
        text = patch.read_text(encoding="utf-8")
        self.assertEqual(
            {"buildtools/third_party/libc++/BUILD.gn"},
            {
                line.removeprefix("--- a/")
                for line in text.splitlines()
                if line.startswith("--- a/")
            },
        )
        self.assertEqual(1, text.count('+        ":_Builtin_float",'))
        self.assertEqual(
            "f0ccfb5933f7daa9545159afbb35bdf8951efcc4",
            build_pipeline.XCODE27_COMPAT_UPSTREAM["commit"],
        )

    def test_xcode27_linkedit_strip_patch_is_hash_pinned_and_scoped(self):
        patch = build_pipeline.XCODE27_LINKEDIT_STRIP_PATCH
        self.assertEqual(
            build_pipeline.XCODE27_LINKEDIT_STRIP_PATCH_SHA256,
            build_pipeline.sha256_file(patch),
        )
        text = patch.read_text(encoding="utf-8")
        self.assertEqual(
            {"build/toolchain/apple/toolchain.gni"},
            {
                line.removeprefix("--- a/")
                for line in text.splitlines()
                if line.startswith("--- a/")
            },
        )
        self.assertEqual(
            1,
            text.count(
                'toolchain_args.current_os == "mac" && xcode_version_int >= 2700'
            ),
        )
        self.assertEqual(1, text.count('invoker.bin_path + "strip"'))
        self.assertNotIn("use_lld=false", text)
        self.assertNotIn("use_lld = false", text)
        self.assertEqual(
            "18c1cbce6874a7341f357014befb66d4c11a04a9",
            build_pipeline.XCODE27_LINKEDIT_STRIP_UPSTREAM["fix_commit"],
        )

    def test_generated_linkedit_contract_requires_only_selected_xcode_strip(self):
        out = self.root / "generated"
        nested = out / "clang_arm64"
        nested.mkdir(parents=True)
        for path in (out / "toolchain.ninja", nested / "toolchain.ninja"):
            path.write_text(
                "command = link -Wcrl,strippath,{}\n".format(
                    self.linkedit_tools["selected"]["path"]
                ),
                encoding="utf-8",
            )
        report = build_pipeline.generated_linkedit_strip_contract(
            out, self.linkedit_tools
        )
        self.assertEqual(2, report["strip_token_count"])
        self.assertTrue(report["all_linker_rules_use_selected_strip"])
        self.assertFalse(report["llvm_strip_selected"])
        (nested / "toolchain.ninja").write_text(
            "command = link -Wcrl,strippath,../../bin/llvm-strip\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "unpinned strip"
        ):
            build_pipeline.generated_linkedit_strip_contract(
                out, self.linkedit_tools
            )

    def test_macho_linkedit_gate_accepts_aligned_and_rejects_string_pool(self):
        root = self.root / "machos"
        root.mkdir()
        aligned = root / "aligned.dylib"
        aligned.write_bytes(self.macho64_bytes(stroff=0x108))
        report = build_pipeline.macho_linkedit_alignment_report(root)
        self.assertEqual(1, report["macho_files"])
        self.assertEqual(1, report["slices"])
        self.assertTrue(report["all_64_bit_linkedit_offsets_aligned"])
        aligned.write_bytes(self.macho64_bytes(stroff=0x104))
        audit = build_pipeline.macho_linkedit_alignment_report(
            root, require_aligned=False
        )
        self.assertEqual("symtab.stroff", audit["violations"][0]["name"])
        self.assertEqual(0x104, audit["violations"][0]["offset"])
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "misaligned LINKEDIT"
        ):
            build_pipeline.macho_linkedit_alignment_report(root)

    def test_macho_linkedit_gate_requires_16_byte_code_signature_alignment(self):
        root = self.root / "signed-machos"
        root.mkdir()
        binary = root / "signed"
        binary.write_bytes(self.macho64_bytes(signature_offset=0x208))
        audit = build_pipeline.macho_linkedit_alignment_report(
            root, require_aligned=False
        )
        violation = audit["violations"][0]
        self.assertEqual("linkedit_data.0x0000001d", violation["name"])
        self.assertEqual(16, violation["required_alignment"])

    def test_macho_parser_supports_fat_cigam_and_fat_cigam64(self):
        for uses_64 in (False, True):
            with self.subTest(uses_64=uses_64):
                path = self.root / ("cigam64" if uses_64 else "cigam")
                path.write_bytes(
                    self.fat_macho_bytes(
                        [
                            ("arm64", self.macho64_bytes("arm64")),
                            ("x86_64", self.macho64_bytes("x86_64")),
                        ],
                        endian="<",
                        uses_64=uses_64,
                    )
                )
                report = build_pipeline._macho_file_report(path)
                self.assertEqual(2, len(report["slices"]))
                self.assertEqual(
                    {"arm64", "x86_64"},
                    {item["architecture"] for item in report["slices"]},
                )
                self.assertTrue(report["aligned"])

    def test_macho_gate_scans_every_binary_in_bundle_tree(self):
        root = self.root / "bundle"
        (root / "Frameworks").mkdir(parents=True)
        (root / "main").write_bytes(self.macho64_bytes("arm64"))
        (root / "Frameworks/libEGL.dylib").write_bytes(
            self.macho64_bytes("arm64", stroff=0x104)
        )
        audit = build_pipeline.macho_linkedit_alignment_report(
            root, require_aligned=False
        )
        self.assertEqual(2, audit["macho_files"])
        self.assertEqual(
            "Frameworks/libEGL.dylib",
            audit["violations"][0]["relative_path"],
        )

    def test_swiftshader_signing_patch_is_hash_pinned_scoped_and_semantic(self):
        patch = build_pipeline.SWIFTSHADER_DISABLED_SIGNING_PATCH
        self.assertEqual(
            build_pipeline.SWIFTSHADER_DISABLED_SIGNING_PATCH_SHA256,
            build_pipeline.sha256_file(patch),
        )
        text = patch.read_text(encoding="utf-8")
        self.assertEqual(
            {"chrome/installer/mac/signing/parts.py"},
            {
                line.removeprefix("--- a/")
                for line in text.splitlines()
                if line.startswith("--- a/")
            },
        )
        self.assertEqual(1, text.count("-        'libvk_swiftshader.dylib',"))
        self.assertEqual(1, text.count("         'libEGL.dylib',"))
        self.assertEqual(1, text.count("         'libGLESv2.dylib',"))
        self.assertNotIn("-'libEGL.dylib'", text)
        self.assertNotIn("-'libGLESv2.dylib'", text)

    def test_swiftshader_app_contract_requires_angle_and_forbids_vulkan(self):
        app = self.make_app(self.root)
        libraries = (
            app
            / "Contents/Frameworks/Focus Browser Framework.framework"
            / "Versions/150.0.7871.128/Libraries"
        )
        libraries.mkdir(parents=True)
        for name in build_pipeline.SWIFTSHADER_REQUIRED_ANGLE_LIBRARIES:
            (libraries / name).write_bytes((name + " fixture").encode("utf-8"))
        report = build_pipeline.swiftshader_app_library_contract(app)
        self.assertTrue(report["swiftshader_library_absent"])
        self.assertEqual(
            set(build_pipeline.SWIFTSHADER_REQUIRED_ANGLE_LIBRARIES),
            set(report["required_sha256"]),
        )
        (libraries / build_pipeline.SWIFTSHADER_VULKAN_LIBRARY).write_bytes(
            b"forbidden"
        )
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "unexpectedly bundled"
        ):
            build_pipeline.swiftshader_app_library_contract(app)

    def test_swiftshader_refresh_is_copy_signing_only_at_j8(self):
        report = build_pipeline.swiftshader_signing_refresh_contract(self.source)
        self.assertEqual("-j8", report["command"][1])
        self.assertEqual(build_pipeline.X64_OUT, report["command"][3])
        self.assertEqual(
            "chrome/installer/mac:copy_signing", report["command"][-1]
        )
        self.assertEqual(5, len(report["command"]))

    def test_swiftshader_rebind_accepts_exact_combined_adhoc_post_state(self):
        source_parts = self.source / "chrome/installer/mac/signing/parts.py"
        packaging_parts = (
            self.source
            / build_pipeline.X64_OUT
            / build_pipeline.PACKAGING_NAME
            / "signing/parts.py"
        )
        source_parts.parent.mkdir(parents=True, exist_ok=True)
        packaging_parts.parent.mkdir(parents=True, exist_ok=True)
        source_parts.write_bytes(b"combined post\n")
        packaging_parts.write_bytes(b"combined post\n")
        combined_hash = build_pipeline.sha256_file(source_parts)
        adhoc_files = dict(build_pipeline.ADHOC_RUNTIME_SIGNING_FILES)
        adhoc_files["chrome/installer/mac/signing/parts.py"] = {
            **adhoc_files["chrome/installer/mac/signing/parts.py"],
            "post_sha256": combined_hash,
        }
        patch = self.root / "swift.patch"
        patch.write_text("fixture\n", encoding="utf-8")
        receipt = self.source / build_pipeline.SWIFTSHADER_DISABLED_SIGNING_RECEIPT
        build = {
            "profiles": {},
            "build_args": {},
            "libraries": {},
            "app_tree_sha256": {"arm64": "a" * 64, "x64": "b" * 64},
            "reclaim_receipt": {"path": "reclaim", "sha256": "c" * 64},
            "x64_build_receipt": {"path": "x64", "sha256": "d" * 64},
        }
        with mock.patch.object(
            build_pipeline, "acquisition_contract"
        ), mock.patch.object(
            build_pipeline, "tool_receipt_contract"
        ), mock.patch.object(
            build_pipeline,
            "preparation_contract",
            return_value=(self.source / build_pipeline.PREPARATION_RECEIPT, {}),
        ), mock.patch.object(
            build_pipeline,
            "SWIFTSHADER_DISABLED_SIGNING_PATCH",
            patch,
        ), mock.patch.object(
            build_pipeline,
            "SWIFTSHADER_DISABLED_SIGNING_PATCH_SHA256",
            build_pipeline.sha256_file(patch),
        ), mock.patch.object(
            build_pipeline, "ADHOC_RUNTIME_SIGNING_FILES", adhoc_files
        ), mock.patch.object(
            build_pipeline,
            "swiftshader_disabled_build_contract",
            return_value=build,
        ), mock.patch.object(
            build_pipeline,
            "swiftshader_signing_refresh_contract",
            return_value={"command": ["copy_signing"], "ninja": self.ninja_report},
        ), mock.patch.object(
            build_pipeline.prepare_source, "check_patch_boundary"
        ) as boundary:
            plan = build_pipeline.swiftshader_disabled_signing_plan(
                self.source, self.developer
            )
        self.assertEqual("post-adhoc", plan["source_state"])
        self.assertEqual("post-adhoc", plan["packaging_state"])
        boundary.assert_not_called()
        self.assertEqual(str(receipt), plan["receipt"])

    def test_swiftshader_rebind_rejects_mtime_sensitive_mixed_package(self):
        path = self.root / "parts.py"
        path.write_text("fixture\n", encoding="utf-8")
        with mock.patch.object(
            build_pipeline,
            "sha256_file",
            return_value=build_pipeline.ADHOC_RUNTIME_SIGNING_FILES[
                "chrome/installer/mac/signing/parts.py"
            ]["post_sha256"],
        ):
            self.assertEqual(
                "post-adhoc",
                build_pipeline._swiftshader_signing_file_state(path, "fixture"),
            )
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "already-matching"
        ):
            build_pipeline._swiftshader_signing_state_contract(
                "post-adhoc", "pre"
            )

    def test_adhoc_runtime_signing_patch_is_hash_pinned_and_scoped(self):
        patch = build_pipeline.ADHOC_RUNTIME_SIGNING_PATCH
        self.assertEqual(
            build_pipeline.ADHOC_RUNTIME_SIGNING_PATCH_SHA256,
            build_pipeline.sha256_file(patch),
        )
        text = patch.read_text(encoding="utf-8")
        self.assertEqual(
            {
                "chrome/installer/mac/signing/parts.py",
                "chrome/installer/mac/signing/modification.py",
                "chrome/installer/mac/signing/parts_test.py",
                "chrome/installer/mac/signing/modification_test.py",
            },
            {
                line.removeprefix("--- a/")
                for line in text.splitlines()
                if line.startswith("--- a/")
            },
        )
        self.assertIn("config.identity == '-'", text)
        self.assertIn("com.apple.security.cs.disable-library-validation", text)
        self.assertIn("ADHOC_FRAMEWORK_ENTITLEMENTS", text)
        self.assertNotIn("chrome_crashpad_handler'.format", text)
        self.assertNotIn("UpdaterPrivilegedHelper", text)

    def test_adhoc_runtime_signing_tests_precede_copy_signing_at_j8(self):
        python = {"path": str(self.root / "pinned-python3.11")}
        (self.source / "chrome/installer/mac").mkdir(parents=True, exist_ok=True)
        with mock.patch.object(
            build_pipeline, "packaging_python_contract", return_value=python
        ):
            tests = build_pipeline.adhoc_runtime_signing_test_contract(
                self.source
            )
        refresh = build_pipeline.adhoc_runtime_signing_refresh_contract(
            self.source
        )
        self.assertEqual(
            ["signing.parts_test", "signing.modification_test"],
            tests["modules"],
        )
        self.assertEqual("-j8", refresh["command"][1])
        self.assertEqual(
            "chrome/installer/mac:copy_signing", refresh["command"][-1]
        )

    def test_xcode27_execution_restores_pre_fix_file_on_apply_failure(self):
        patch = self.root / "fixture-xcode27.patch"
        patch.write_text("fixture\n", encoding="utf-8")
        relative = "buildtools/third_party/libc++/BUILD.gn"
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        pre = b"pre xcode27\n"
        post = b"post xcode27\n"
        target.write_bytes(pre)
        files = {
            relative: {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            }
        }
        receipt = self.source / build_pipeline.XCODE27_COMPAT_RECEIPT
        plan = {
            "stage": "apply-xcode27-compat",
            "source_root": str(self.source),
            "receipt": str(receipt),
        }

        def fail_after_mutation(*_args, **_kwargs):
            target.write_bytes(post)
            raise build_pipeline.prepare_source.PreparationError("forced patch failure")

        with mock.patch.object(
            build_pipeline, "XCODE27_COMPAT_PATCH", patch
        ), mock.patch.object(
            build_pipeline, "XCODE27_COMPAT_FILES", files
        ), mock.patch.object(
            build_pipeline, "xcode27_compat_plan", return_value=plan
        ), mock.patch.object(
            build_pipeline, "require_free"
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "apply_patch_plan",
            side_effect=fail_after_mutation,
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "forced patch failure"):
            build_pipeline.execute_xcode27_compat(
                self.source, self.developer, plan
            )

        self.assertFalse(receipt.exists())
        self.assertEqual(files[relative]["pre_sha256"], build_pipeline.sha256_file(target))

    def test_linkedit_strip_execution_restores_source_on_apply_failure(self):
        patch = self.root / "fixture-linkedit-strip.patch"
        patch.write_text("fixture\n", encoding="utf-8")
        relative = "build/toolchain/apple/toolchain.gni"
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        pre = b"pre strip selection\n"
        post = b"post strip selection\n"
        target.write_bytes(pre)
        files = {
            relative: {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            }
        }
        receipt = self.source / build_pipeline.XCODE27_LINKEDIT_STRIP_RECEIPT
        plan = {
            "stage": "apply-xcode27-linkedit-strip-compat",
            "source_root": str(self.source),
            "receipt": str(receipt),
        }

        def fail_after_mutation(*_args, **_kwargs):
            target.write_bytes(post)
            raise build_pipeline.prepare_source.PreparationError(
                "forced LINKEDIT strip failure"
            )

        with mock.patch.object(
            build_pipeline, "XCODE27_LINKEDIT_STRIP_PATCH", patch
        ), mock.patch.object(
            build_pipeline, "XCODE27_LINKEDIT_STRIP_FILES", files
        ), mock.patch.object(
            build_pipeline,
            "xcode27_linkedit_strip_plan",
            return_value=plan,
        ), mock.patch.object(
            build_pipeline, "require_free"
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "apply_patch_plan",
            side_effect=fail_after_mutation,
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "forced LINKEDIT strip failure"
        ):
            build_pipeline.execute_xcode27_linkedit_strip(
                self.source, self.developer, plan
            )
        self.assertFalse(receipt.exists())
        self.assertEqual(pre, target.read_bytes())

    def test_xcode27_seatbelt_patch_is_upstream_pinned_scoped_and_deletion_only(self):
        patch = build_pipeline.XCODE27_SEATBELT_PATCH
        self.assertEqual(
            build_pipeline.XCODE27_SEATBELT_PATCH_SHA256,
            build_pipeline.sha256_file(patch),
        )
        text = patch.read_text(encoding="utf-8")
        self.assertEqual(
            {"sandbox/mac/seatbelt.cc", "sandbox/mac/seatbelt.h"},
            {
                line.removeprefix("--- a/")
                for line in text.splitlines()
                if line.startswith("--- a/")
            },
        )
        self.assertEqual(
            1,
            text.count(
                "-const char* Seatbelt::kProfilePureComputation = "
                "kSBXProfilePureComputation;"
            ),
        )
        self.assertEqual(
            1, text.count("-  static const char* kProfilePureComputation;")
        )
        self.assertFalse(
            any(
                line.startswith("+") and not line.startswith("+++")
                for line in text.splitlines()
            )
        )
        self.assertEqual(
            "6c0a651f9cf91d07c87be8feba854a38a311aba6",
            build_pipeline.XCODE27_SEATBELT_UPSTREAM["commit"],
        )

    def test_xcode27_seatbelt_execution_restores_both_files_on_failure(self):
        patch = self.root / "fixture-seatbelt.patch"
        patch.write_text("fixture\n", encoding="utf-8")
        files = {}
        targets = {}
        for name in ("seatbelt.cc", "seatbelt.h"):
            relative = "sandbox/mac/{}".format(name)
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            pre = ("pre {}\n".format(name)).encode("utf-8")
            post = ("post {}\n".format(name)).encode("utf-8")
            target.write_bytes(pre)
            files[relative] = {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            }
            targets[relative] = (target, post)
        receipt = self.source / build_pipeline.XCODE27_SEATBELT_RECEIPT
        plan = {
            "stage": "apply-xcode27-seatbelt-compat",
            "source_root": str(self.source),
            "receipt": str(receipt),
        }

        def fail_after_mutation(*_args, **_kwargs):
            for target, post in targets.values():
                target.write_bytes(post)
            raise build_pipeline.prepare_source.PreparationError("forced Seatbelt failure")

        with mock.patch.object(
            build_pipeline, "XCODE27_SEATBELT_PATCH", patch
        ), mock.patch.object(
            build_pipeline, "XCODE27_SEATBELT_FILES", files
        ), mock.patch.object(
            build_pipeline, "xcode27_seatbelt_plan", return_value=plan
        ), mock.patch.object(
            build_pipeline, "require_free"
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "apply_patch_plan",
            side_effect=fail_after_mutation,
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "forced Seatbelt failure"):
            build_pipeline.execute_xcode27_seatbelt(
                self.source, self.developer, plan
            )

        self.assertFalse(receipt.exists())
        for relative, (target, _post) in targets.items():
            self.assertEqual(
                files[relative]["pre_sha256"], build_pipeline.sha256_file(target)
            )

    def test_screen_ai_disabled_patch_is_pinned_scoped_and_guard_only(self):
        patch = build_pipeline.SCREEN_AI_DISABLED_PATCH
        self.assertEqual(
            build_pipeline.SCREEN_AI_DISABLED_PATCH_SHA256,
            build_pipeline.sha256_file(patch),
        )
        text = patch.read_text(encoding="utf-8")
        self.assertEqual(
            {"chrome/browser/chrome_content_browser_client.cc"},
            {
                line.removeprefix("--- a/")
                for line in text.splitlines()
                if line.startswith("--- a/")
            },
        )
        self.assertIn(
            '+#include "services/screen_ai/buildflags/buildflags.h"', text
        )
        self.assertEqual(
            2, text.count("+#if BUILDFLAG(ENABLE_SCREEN_AI_SERVICE)")
        )
        self.assertEqual(2, text.count("+#endif"))
        self.assertNotIn("enable_screen_ai_service=true", text)
        self.assertEqual(
            "c5de29a7cd701daec46a7bf042dd0551e5e8c5c3",
            build_pipeline.SCREEN_AI_DISABLED_UPSTREAM["introduced_commit"],
        )
        self.assertEqual(
            "Iba8cd5583026a993e3236f1fe4bb48e822368b54",
            build_pipeline.SCREEN_AI_DISABLED_UPSTREAM["change_id"],
        )
        self.assertEqual(
            5762356,
            build_pipeline.SCREEN_AI_DISABLED_UPSTREAM["change_number"],
        )
        self.assertEqual(2, build_pipeline.SCREEN_AI_DISABLED_RECEIPT_SCHEMA)
        self.assertEqual(
            "5762356",
            build_pipeline.SCREEN_AI_DISABLED_LEGACY_UPSTREAM["change_id"],
        )

    def test_screen_ai_disabled_config_pins_hash_and_explicit_false(self):
        relative = "services/screen_ai/buildflags/features.gni"
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        disabled = b"declare_args() {\n  enable_screen_ai_service = false\n}\n"
        enabled = b"declare_args() {\n  enable_screen_ai_service = true\n}\n"
        target.write_bytes(disabled)
        disabled_hash = hashlib.sha256(disabled).hexdigest()
        with mock.patch.object(
            build_pipeline,
            "SCREEN_AI_DISABLED_CONFIG_FILES",
            {relative: disabled_hash},
        ):
            self.assertEqual(
                {relative: disabled_hash},
                build_pipeline.screen_ai_disabled_config_contract(self.source),
            )
            target.write_bytes(enabled)
            with self.assertRaisesRegex(
                build_pipeline.PipelineError, "config hash mismatch"
            ):
                build_pipeline.screen_ai_disabled_config_contract(self.source)

        enabled_hash = hashlib.sha256(enabled).hexdigest()
        with mock.patch.object(
            build_pipeline,
            "SCREEN_AI_DISABLED_CONFIG_FILES",
            {relative: enabled_hash},
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "not explicitly disabled"
        ):
            build_pipeline.screen_ai_disabled_config_contract(self.source)

    def test_screen_ai_disabled_receipt_schema_matrix_and_legacy_config_gate(self):
        patch = self.root / "fixture-screen-ai.patch"
        patch.write_text("fixture\n", encoding="utf-8")
        patch_hash = build_pipeline.sha256_file(patch)
        relative = "chrome/browser/chrome_content_browser_client.cc"
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        post = b"post screen ai\n"
        target.write_bytes(post)
        files = {
            relative: {
                "pre_sha256": hashlib.sha256(b"pre screen ai\n").hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            }
        }
        config_relative = "services/screen_ai/buildflags/features.gni"
        config = self.source / config_relative
        config.parent.mkdir(parents=True, exist_ok=True)
        disabled = b"enable_screen_ai_service = false\n"
        config.write_bytes(disabled)
        config_files = {config_relative: hashlib.sha256(disabled).hexdigest()}
        seatbelt_link = {"path": "fixture-seatbelt.json", "sha256": "a" * 64}
        receipt_path = self.source / build_pipeline.SCREEN_AI_DISABLED_RECEIPT

        def receipt_value(schema, upstream):
            value = {
                "schema": schema,
                "source_root": str(self.source),
                "xcode27_seatbelt_compatibility_receipt": seatbelt_link,
                "upstream": upstream,
                "patch": {"path": str(patch), "sha256": patch_hash},
                "files": files,
                "enable_screen_ai_service": False,
                "offline": True,
                "network_operations": 0,
                "build_executed": False,
                "signing_executed": False,
                "packaging_executed": False,
            }
            if schema == build_pipeline.SCREEN_AI_DISABLED_RECEIPT_SCHEMA:
                value["config_files"] = config_files
            return value

        def publish(value):
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(
                json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
            )

        with mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_PATCH", patch
        ), mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_PATCH_SHA256", patch_hash
        ), mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_FILES", files
        ), mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_CONFIG_FILES", config_files
        ), mock.patch.object(
            build_pipeline,
            "screen_ai_disabled_provenance_link",
            return_value=seatbelt_link,
        ):
            publish(
                receipt_value(1, build_pipeline.SCREEN_AI_DISABLED_LEGACY_UPSTREAM)
            )
            build_pipeline.screen_ai_disabled_receipt_contract(
                self.source, self.developer
            )

            publish(
                receipt_value(
                    build_pipeline.SCREEN_AI_DISABLED_RECEIPT_SCHEMA,
                    build_pipeline.SCREEN_AI_DISABLED_UPSTREAM,
                )
            )
            build_pipeline.screen_ai_disabled_receipt_contract(
                self.source, self.developer
            )

            publish(receipt_value(1, build_pipeline.SCREEN_AI_DISABLED_UPSTREAM))
            with self.assertRaisesRegex(
                build_pipeline.PipelineError, "receipt schema mismatch"
            ):
                build_pipeline.screen_ai_disabled_receipt_contract(
                    self.source, self.developer
                )

            publish(
                receipt_value(
                    build_pipeline.SCREEN_AI_DISABLED_RECEIPT_SCHEMA,
                    build_pipeline.SCREEN_AI_DISABLED_LEGACY_UPSTREAM,
                )
            )
            with self.assertRaisesRegex(
                build_pipeline.PipelineError, "receipt schema mismatch"
            ):
                build_pipeline.screen_ai_disabled_receipt_contract(
                    self.source, self.developer
                )

            publish(
                receipt_value(1, build_pipeline.SCREEN_AI_DISABLED_LEGACY_UPSTREAM)
            )
            config.write_text(
                "enable_screen_ai_service = true\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                build_pipeline.PipelineError, "config hash mismatch"
            ):
                build_pipeline.screen_ai_disabled_receipt_contract(
                    self.source, self.developer
                )

    def test_screen_ai_disabled_execution_restores_caller_on_failure(self):
        patch = self.root / "fixture-screen-ai.patch"
        patch.write_text("fixture\n", encoding="utf-8")
        pinned_patch_hash = build_pipeline.sha256_file(patch)
        relative = "chrome/browser/chrome_content_browser_client.cc"
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        pre = b"pre screen ai\n"
        post = b"post screen ai\n"
        target.write_bytes(pre)
        files = {
            relative: {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            }
        }
        receipt = self.source / build_pipeline.SCREEN_AI_DISABLED_RECEIPT
        plan = {
            "stage": "apply-screen-ai-disabled-compat",
            "source_root": str(self.source),
            "source_state": "pre",
            "config_files": {},
            "receipt": str(receipt),
        }

        def fail_after_mutation(_source, patch_plan, **_kwargs):
            self.assertNotEqual(patch, patch_plan[0])
            patch.write_text("mutated canonical fixture\n", encoding="utf-8")
            self.assertEqual(
                pinned_patch_hash,
                build_pipeline.sha256_file(patch_plan[0]),
            )
            target.write_bytes(post)
            raise build_pipeline.prepare_source.PreparationError(
                "forced ScreenAI failure"
            )

        with mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_PATCH", patch
        ), mock.patch.object(
            build_pipeline,
            "SCREEN_AI_DISABLED_PATCH_SHA256",
            pinned_patch_hash,
        ), mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_FILES", files
        ), mock.patch.object(
            build_pipeline, "screen_ai_disabled_plan", return_value=plan
        ), mock.patch.object(
            build_pipeline, "require_free"
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "check_patch_boundary",
            return_value={"applicable": True},
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "apply_patch_plan",
            side_effect=fail_after_mutation,
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "forced ScreenAI"):
            build_pipeline.execute_screen_ai_disabled(
                self.source, self.developer, plan
            )

        self.assertFalse(receipt.exists())
        self.assertEqual(
            files[relative]["pre_sha256"], build_pipeline.sha256_file(target)
        )

    def test_screen_ai_disabled_real_planner_recovers_exact_post_image(self):
        patch = self.root / "fixture-screen-ai.patch"
        patch.write_text("fixture\n", encoding="utf-8")
        relative = "chrome/browser/chrome_content_browser_client.cc"
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        pre = b"pre screen ai\n"
        post = b"post screen ai\n"
        target.write_bytes(post)
        files = {
            relative: {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            }
        }
        boundary = {"patch": str(patch), "reverse": True}
        with mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_PATCH", patch
        ), mock.patch.object(
            build_pipeline,
            "SCREEN_AI_DISABLED_PATCH_SHA256",
            build_pipeline.sha256_file(patch),
        ), mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_FILES", files
        ), mock.patch.object(
            build_pipeline,
            "screen_ai_disabled_provenance_link",
            return_value={"fixture": True},
        ), mock.patch.object(
            build_pipeline,
            "screen_ai_disabled_config_contract",
            return_value={},
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "check_patch_boundary",
            return_value=boundary,
        ) as check_boundary:
            plan = build_pipeline.screen_ai_disabled_plan(
                self.source, self.developer
            )

        self.assertEqual("post", plan["source_state"])
        self.assertEqual(boundary, plan["patch"])
        check_boundary.assert_called_once_with(self.source, patch, reverse=True)

    def test_screen_ai_disabled_exact_post_image_finalizes_without_reapply(self):
        relative = "chrome/browser/chrome_content_browser_client.cc"
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        pre = b"pre screen ai\n"
        post = b"post screen ai\n"
        target.write_bytes(post)
        files = {
            relative: {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            }
        }
        receipt = self.source / build_pipeline.SCREEN_AI_DISABLED_RECEIPT
        plan = {
            "stage": "apply-screen-ai-disabled-compat",
            "source_root": str(self.source),
            "source_state": "post",
            "config_files": {},
            "xcode27_seatbelt_compatibility_receipt": {"fixture": True},
            "receipt": str(receipt),
        }
        receipt_report = {"path": str(receipt), "sha256": "f" * 64}
        with mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_FILES", files
        ), mock.patch.object(
            build_pipeline, "screen_ai_disabled_plan", return_value=plan
        ), mock.patch.object(
            build_pipeline, "require_free"
        ), mock.patch.object(
            build_pipeline, "atomic_json", return_value=receipt_report
        ) as publish, mock.patch.object(
            build_pipeline, "screen_ai_disabled_receipt_contract"
        ), mock.patch.object(
            build_pipeline.prepare_source, "apply_patch_plan"
        ) as apply_patch:
            report = build_pipeline.execute_screen_ai_disabled(
                self.source, self.developer, plan
            )

        apply_patch.assert_not_called()
        publish.assert_called_once()
        self.assertTrue(report["resumed_from_exact_post_image"])

    def test_screen_ai_disabled_cleanup_failure_is_nonfatal_after_commit(self):
        patch = self.root / "fixture-screen-ai.patch"
        patch.write_text("fixture\n", encoding="utf-8")
        relative = "chrome/browser/chrome_content_browser_client.cc"
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        pre = b"pre screen ai\n"
        post = b"post screen ai\n"
        target.write_bytes(pre)
        files = {
            relative: {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            }
        }
        receipt = self.source / build_pipeline.SCREEN_AI_DISABLED_RECEIPT
        plan = {
            "stage": "apply-screen-ai-disabled-compat",
            "source_root": str(self.source),
            "source_state": "pre",
            "config_files": {},
            "xcode27_seatbelt_compatibility_receipt": {"fixture": True},
            "receipt": str(receipt),
        }

        def apply_success(_source, patch_plan, **_kwargs):
            self.assertNotEqual(patch, patch_plan[0])
            target.write_bytes(post)

        with mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_PATCH", patch
        ), mock.patch.object(
            build_pipeline,
            "SCREEN_AI_DISABLED_PATCH_SHA256",
            build_pipeline.sha256_file(patch),
        ), mock.patch.object(
            build_pipeline, "SCREEN_AI_DISABLED_FILES", files
        ), mock.patch.object(
            build_pipeline, "screen_ai_disabled_plan", return_value=plan
        ), mock.patch.object(
            build_pipeline, "require_free"
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "check_patch_boundary",
            return_value={"applicable": True},
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "apply_patch_plan",
            side_effect=apply_success,
        ), mock.patch.object(
            build_pipeline,
            "atomic_json",
            return_value={"path": str(receipt), "sha256": "f" * 64},
        ), mock.patch.object(
            build_pipeline, "screen_ai_disabled_receipt_contract"
        ), mock.patch.object(
            build_pipeline, "best_effort_remove_tree", return_value=False
        ):
            report = build_pipeline.execute_screen_ai_disabled(
                self.source, self.developer, plan
            )

        self.assertFalse(report["snapshot_cleanup_complete"])
        self.assertFalse(report["resumed_from_exact_post_image"])
        self.assertEqual(files[relative]["post_sha256"], build_pipeline.sha256_file(target))

    def test_best_effort_remove_tree_handles_permission_error(self):
        with mock.patch.object(
            build_pipeline.shutil,
            "rmtree",
            side_effect=PermissionError("fixture cleanup denied"),
        ):
            self.assertFalse(build_pipeline.best_effort_remove_tree(self.root))

    def test_disabled_profiles_require_gn_compat_receipt(self):
        for relative in (
            build_pipeline.ARM_OUT + "/args.gn",
            build_pipeline.X64_OUT + "/args.gn",
        ):
            path = self.source / relative
            path.write_text(
                "enable_swiftshader=false\nsafe_browsing_mode=0\n",
                encoding="utf-8",
            )
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "GN compatibility receipt is required"
        ):
            build_pipeline.preparation_contract(self.source)

    def test_gn_compat_execution_restores_pre_fix_files_on_apply_failure(self):
        patch = self.root / "fixture-compat.patch"
        patch.write_text("fixture\n", encoding="utf-8")
        files = {}
        post_bodies = {}
        for relative in (
            "chrome/BUILD.gn",
            "content/shell/BUILD.gn",
            "chrome/test/BUILD.gn",
        ):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            pre = ("pre " + relative + "\n").encode("utf-8")
            post = ("post " + relative + "\n").encode("utf-8")
            path.write_bytes(pre)
            files[relative] = {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            }
            post_bodies[relative] = post
        receipt = self.source / build_pipeline.GN_COMPAT_RECEIPT
        prep = self.source / build_pipeline.PREPARATION_RECEIPT
        plan = {
            "stage": "apply-gn-compat",
            "source_root": str(self.source),
            "preparation_receipt": {
                "path": str(prep),
                "sha256": build_pipeline.sha256_file(prep),
            },
            "patch": {"path": str(patch)},
            "files": files,
            "receipt": str(receipt),
            "offline": True,
            "network_operations": 0,
        }

        def fail_after_mutation(*_args, **_kwargs):
            for relative, body in post_bodies.items():
                (self.source / relative).write_bytes(body)
            raise build_pipeline.prepare_source.PreparationError("forced patch failure")

        with mock.patch.object(
            build_pipeline, "GN_COMPAT_PATCH", patch
        ), mock.patch.object(
            build_pipeline, "GN_COMPAT_FILES", files
        ), mock.patch.object(
            build_pipeline, "gn_compat_plan", return_value=plan
        ), mock.patch.object(
            build_pipeline, "require_free"
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "apply_patch_plan",
            side_effect=fail_after_mutation,
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "forced patch failure"):
            build_pipeline.execute_gn_compat(self.source, plan)

        self.assertFalse(receipt.exists())
        for relative, hashes in files.items():
            self.assertEqual(
                hashes["pre_sha256"],
                build_pipeline.sha256_file(self.source / relative),
            )

    def test_safe_environment_is_child_only_and_macos_only(self):
        inherited = {
            "PATH": "/bin",
            "SDKROOT": "/iPhoneOS.sdk",
            "IPHONEOS_DEPLOYMENT_TARGET": "27.0",
            "GIT_CACHE_PATH": "/cache",
            "MACOSX_DEPLOYMENT_TARGET": "99.0",
            "NODE_OPTIONS": "--require=/tmp/inject.js",
            "npm_config_arch": "ia32",
            "DYLD_INSERT_LIBRARIES": "/tmp/inject.dylib",
            "BASH_ENV": "/tmp/inject.sh",
            "ENV": "/tmp/inject.sh",
            "KEEP": "yes",
        }
        developer = self.root / "Xcode.app/Contents/Developer"
        result = build_pipeline.safe_environment(self.source, developer, inherited)
        self.assertNotIn("SDKROOT", result)
        self.assertNotIn("IPHONEOS_DEPLOYMENT_TARGET", result)
        self.assertNotIn("GIT_CACHE_PATH", result)
        self.assertNotIn("MACOSX_DEPLOYMENT_TARGET", result)
        self.assertNotIn("NODE_OPTIONS", result)
        self.assertNotIn("npm_config_arch", result)
        self.assertNotIn("DYLD_INSERT_LIBRARIES", result)
        self.assertNotIn("BASH_ENV", result)
        self.assertNotIn("ENV", result)
        self.assertNotIn("KEEP", result)
        self.assertEqual(str(developer), result["DEVELOPER_DIR"])
        self.assertEqual(
            str(self.depot) + ":" + build_pipeline.SYSTEM_PATH,
            result["PATH"],
        )

    def test_safe_environment_adds_pinned_ninja_after_depot_tools(self):
        result = build_pipeline.safe_environment(
            self.source,
            self.developer,
            {"PATH": "/bin"},
            build_ninja=self.ninja,
        )
        self.assertEqual(
            str(self.depot)
            + ":"
            + str(self.ninja.parent)
            + ":"
            + build_pipeline.SYSTEM_PATH,
            result["PATH"],
        )
        with self.assertRaisesRegex(build_pipeline.PipelineError, "pinned Dawn Ninja"):
            build_pipeline.safe_environment(
                self.source,
                self.developer,
                {"PATH": "/bin"},
                build_ninja=self.source / "third_party/ninja/ninja",
            )

    def test_safe_environment_accepts_only_the_revalidated_exact_home_alias(self):
        physical_home = self.root / "physical-home"
        physical_workspace = physical_home / "workspace"
        physical_source = physical_workspace / "checkout/src"
        physical_developer = physical_home / "Xcode.app/Contents/Developer"
        physical_repo = physical_workspace / "repo"
        for path in (physical_source, physical_developer, physical_repo):
            path.mkdir(parents=True)
        logical_home = self.root / "logical-home"
        logical_home.symlink_to(physical_home, target_is_directory=True)
        logical_workspace = logical_home / "workspace"
        logical_source = logical_workspace / "checkout/src"
        logical_developer = logical_home / "Xcode.app/Contents/Developer"
        logical_repo = logical_workspace / "repo"
        context = build_pipeline.AliasContext(
            logical_home=logical_home,
            physical_home=physical_home,
            logical_workspace=logical_workspace,
            physical_workspace=physical_workspace,
            logical_source=logical_source,
            physical_source=physical_source,
            logical_developer=logical_developer,
            physical_developer=physical_developer,
            logical_repo=logical_repo,
            physical_repo=physical_repo,
            volume_uuid="AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
        )
        with mock.patch.object(
            build_pipeline, "_recorded_alias_context", return_value=context
        ):
            result = build_pipeline.safe_environment(
                logical_source,
                logical_developer,
                {"HOME": str(logical_home)},
                alias_context=context,
            )
        self.assertEqual(str(logical_home), result["HOME"])
        self.assertEqual(str(logical_developer), result["DEVELOPER_DIR"])

        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "explicit validated AliasContext"
        ):
            build_pipeline.safe_environment(
                logical_source,
                logical_developer,
                {"HOME": str(logical_home)},
            )
        with mock.patch.object(
            build_pipeline,
            "_recorded_alias_context",
            side_effect=build_pipeline.PipelineError("revalidation failed"),
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "revalidation failed"
        ):
            build_pipeline.safe_environment(
                logical_source,
                logical_developer,
                {"HOME": str(logical_home)},
                alias_context=context,
            )
        attacker_home = self.root / "attacker-home"
        attacker_home.symlink_to(physical_home, target_is_directory=True)
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "not the revalidated recorded home alias"
        ):
            build_pipeline.safe_environment(
                logical_source,
                logical_developer,
                {"HOME": str(attacker_home)},
                alias_context=context,
            )

    def test_ninja_contract_binds_hash_arch_version_and_cipd_pin(self):
        with mock.patch.object(
            build_pipeline.platform, "machine", return_value="arm64"
        ), mock.patch.object(
            build_pipeline,
            "sha256_file",
            return_value=build_pipeline.NINJA_SHA256_BY_HOST["arm64"],
        ), mock.patch.object(
            build_pipeline, "capture", side_effect=("arm64", "1.12.1")
        ) as capture:
            report = self.real_ninja_contract(self.source)
        self.assertEqual("arm64", report["architecture"])
        self.assertEqual(build_pipeline.NINJA_CIPD_VERSION, report["cipd_version"])
        self.assertEqual(
            build_pipeline.NINJA_CIPD_INSTANCE_BY_HOST["arm64"],
            report["cipd_instance"],
        )
        self.assertEqual(2, capture.call_count)

    def test_packaging_python_contract_requires_pinned_python311_task_group(self):
        wrapper = self.depot / "python-bin/python3"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        reldir = "bootstrap-test/python3/bin"
        (self.depot / "python3_bin_reldir.txt").write_text(
            reldir, encoding="utf-8"
        )
        executable = self.depot / reldir / "python3.11"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture pinned python 3.11\n")
        executable.chmod(0o755)
        instance = "fixture-cipd-instance"
        slot = self.depot / "bootstrap-test/.cipd/pkgs/0"
        slot.mkdir(parents=True)
        self.write_json(
            slot / "description.json",
            {
                "subdir": "python3",
                "package_name": "infra/3pp/tools/cpython3/mac-arm64",
            },
        )
        (slot / instance).mkdir()
        (slot / "_current").symlink_to(instance)
        identity = {
            "machine": "arm64",
            "task_group": True,
            "version": [3, 11, 8],
        }

        def capture_identity(command, _cwd, _environment, **_kwargs):
            if command[0] == "/usr/bin/lipo":
                return "arm64"
            return json.dumps(identity, sort_keys=True)

        patches = (
            mock.patch.object(
                build_pipeline,
                "PACKAGING_PYTHON_WRAPPER_SHA256",
                build_pipeline.sha256_file(wrapper),
            ),
            mock.patch.object(
                build_pipeline, "PACKAGING_PYTHON_RELDIR", reldir
            ),
            mock.patch.object(
                build_pipeline,
                "PACKAGING_PYTHON_RELDIR_SHA256",
                build_pipeline.sha256_file(self.depot / "python3_bin_reldir.txt"),
            ),
            mock.patch.object(
                build_pipeline,
                "PACKAGING_PYTHON_SHA256_BY_HOST",
                {"arm64": build_pipeline.sha256_file(executable)},
            ),
            mock.patch.object(
                build_pipeline,
                "PACKAGING_PYTHON_CIPD_INSTANCE_BY_HOST",
                {"arm64": instance},
            ),
            mock.patch.object(build_pipeline.platform, "machine", return_value="arm64"),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.object(
            build_pipeline, "capture", side_effect=capture_identity
        ):
            report = build_pipeline.packaging_python_contract(self.source)
        self.assertEqual(str(executable), report["path"])
        self.assertEqual("3.11.8", report["version"])
        self.assertTrue(report["asyncio_task_group"])

        identity["task_group"] = False
        identity["version"] = [3, 9, 6]
        with mock.patch.object(
            build_pipeline, "capture", side_effect=capture_identity
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "identity mismatch"
        ):
            build_pipeline.packaging_python_contract(self.source)

    def test_bootstrap_is_hook_only_and_must_precede_preparation(self):
        (self.checkout / build_pipeline.TOOL_RECEIPT).unlink()
        (self.source / build_pipeline.PREPARATION_RECEIPT).unlink()
        ensure = self.depot / "ensure_bootstrap"
        ensure.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        ensure.chmod(0o755)
        with mock.patch.object(
            build_pipeline, "free_bytes", return_value=80 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline, "ensure_bootstrap_path", return_value=ensure
        ):
            plan = build_pipeline.bootstrap_plan(self.source, self.developer)
        self.assertEqual(
            [str(self.depot / "gclient"), "runhooks"], plan["command"]
        )
        self.assertEqual(
            [str(self.depot / "ensure_bootstrap")], plan["bootstrap_command"]
        )
        self.assertEqual(str(self.checkout), plan["cwd"])
        self.write_json(
            self.source / build_pipeline.PREPARATION_RECEIPT, {"present": True}
        )
        with mock.patch.object(
            build_pipeline, "free_bytes", return_value=80 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline, "ensure_bootstrap_path", return_value=ensure
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "before source preparation"):
            build_pipeline.bootstrap_plan(self.source, self.developer)

    def test_execute_bootstrap_writes_source_bound_receipt(self):
        (self.checkout / build_pipeline.TOOL_RECEIPT).unlink()
        (self.source / build_pipeline.PREPARATION_RECEIPT).unlink()
        ensure = self.depot / "ensure_bootstrap"
        ensure.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        ensure.chmod(0o755)
        plan = {
            "bootstrap_command": [str(ensure)],
            "command": [str(self.depot / "gclient"), "runhooks"],
        }
        with mock.patch.object(
            build_pipeline, "verify_pristine_bootstrap_source"
        ) as pristine, mock.patch.object(build_pipeline, "run_monitored") as run, mock.patch.object(
            build_pipeline, "capture", return_value="150.0"
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=75 * build_pipeline.GIB
        ):
            report = build_pipeline.execute_bootstrap(
                self.source, self.developer, plan
            )
        self.assertEqual(2, run.call_count)
        self.assertEqual(2, pristine.call_count)
        receipt = json.loads(Path(report["path"]).read_text(encoding="utf-8"))
        self.assertEqual(str(self.source), receipt["source_root"])
        self.assertTrue(receipt["hooks_complete"])
        self.assertFalse(receipt["build_executed"])

    def test_build_plan_is_sequential_local_and_eight_jobs(self):
        out = self.source / build_pipeline.ARM_OUT
        out.mkdir(parents=True, exist_ok=True)
        xcode27 = self.write_json(
            self.source / build_pipeline.XCODE27_COMPAT_RECEIPT,
            {"fixture": True},
        )
        seatbelt = self.write_json(
            self.source / build_pipeline.XCODE27_SEATBELT_RECEIPT,
            {"fixture": True},
        )
        screen_ai = self.write_json(
            self.source / build_pipeline.SCREEN_AI_DISABLED_RECEIPT,
            {"fixture": True},
        )
        linkedit = self.write_json(
            self.source / build_pipeline.XCODE27_LINKEDIT_STRIP_RECEIPT,
            {"fixture": True},
        )
        with mock.patch.object(
            build_pipeline,
            "xcode27_compat_receipt_contract",
            return_value=(xcode27, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "xcode27_seatbelt_receipt_contract",
            return_value=(seatbelt, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "screen_ai_disabled_receipt_contract",
            return_value=(screen_ai, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "xcode27_linkedit_strip_receipt_contract",
            return_value=(linkedit, {"tools": self.linkedit_tools}),
        ):
            plan = build_pipeline.build_plan(
                self.source, self.developer, "arm64"
            )
        self.assertEqual("build-arm64", plan["stage"])
        self.assertEqual(self.ninja_report, plan["ninja"])
        self.assertEqual("-j8", plan["commands"][1][1])
        self.assertEqual(
            ["chrome", "chrome/installer/mac:copies"], plan["commands"][1][-2:]
        )
        flattened = "\n".join(" ".join(command) for command in plan["commands"])
        for forbidden in ("android", "iphone", "windows", "remoteexec", "siso"):
            self.assertNotIn(forbidden, flattened.lower())

    def test_x64_build_plan_threads_reclaimed_arm_through_receipt_chain(self):
        xcode27 = self.write_json(
            self.source / build_pipeline.XCODE27_COMPAT_RECEIPT, {"fixture": True}
        )
        seatbelt = self.write_json(
            self.source / build_pipeline.XCODE27_SEATBELT_RECEIPT,
            {"fixture": True},
        )
        screen_ai = self.write_json(
            self.source / build_pipeline.SCREEN_AI_DISABLED_RECEIPT,
            {"fixture": True},
        )
        linkedit = self.write_json(
            self.source / build_pipeline.XCODE27_LINKEDIT_STRIP_RECEIPT,
            {"fixture": True},
        )
        with mock.patch.object(
            build_pipeline, "acquisition_contract"
        ), mock.patch.object(
            build_pipeline, "tool_receipt_contract"
        ), mock.patch.object(
            build_pipeline, "reclaim_contract"
        ), mock.patch.object(
            build_pipeline, "preparation_contract"
        ) as preparation, mock.patch.object(
            build_pipeline,
            "xcode27_compat_receipt_contract",
            return_value=(xcode27, {"fixture": True}),
        ) as module_contract, mock.patch.object(
            build_pipeline,
            "xcode27_seatbelt_receipt_contract",
            return_value=(seatbelt, {"fixture": True}),
        ) as seatbelt_contract, mock.patch.object(
            build_pipeline,
            "screen_ai_disabled_receipt_contract",
            return_value=(screen_ai, {"fixture": True}),
        ) as screen_contract, mock.patch.object(
            build_pipeline,
            "xcode27_linkedit_strip_receipt_contract",
            return_value=(linkedit, {"tools": self.linkedit_tools}),
        ) as linkedit_contract:
            plan = build_pipeline.build_plan(self.source, self.developer, "x64")

        self.assertEqual("build-x64", plan["stage"])
        preparation.assert_called_once_with(
            self.source, allow_reclaimed_arm=True
        )
        for contract in (
            module_contract,
            seatbelt_contract,
            screen_contract,
            linkedit_contract,
        ):
            self.assertTrue(contract.call_args.kwargs["allow_reclaimed_arm"])

    def test_execute_build_writes_a_provenance_receipt(self):
        out = self.source / build_pipeline.ARM_OUT
        out.mkdir(parents=True, exist_ok=True)
        (out / "args.gn").write_text('target_cpu="arm64"\n', encoding="utf-8")
        self.make_app(out)
        packaging = out / build_pipeline.PACKAGING_NAME
        packaging.mkdir()
        sign = packaging / "sign_chrome.py"
        sign.write_bytes(b"sign")
        sign_hash = hashlib.sha256(b"sign").hexdigest()
        xcode27 = self.write_json(
            self.source / build_pipeline.XCODE27_COMPAT_RECEIPT,
            {"fixture": True},
        )
        seatbelt = self.write_json(
            self.source / build_pipeline.XCODE27_SEATBELT_RECEIPT,
            {"fixture": True},
        )
        screen_ai = self.write_json(
            self.source / build_pipeline.SCREEN_AI_DISABLED_RECEIPT,
            {"fixture": True},
        )
        linkedit = self.write_json(
            self.source / build_pipeline.XCODE27_LINKEDIT_STRIP_RECEIPT,
            {"fixture": True},
        )
        generated_linkedit = {
            "selected_strip": self.linkedit_tools["selected"],
            "all_linker_rules_use_selected_strip": True,
        }
        plan = {
            "architecture": "arm64",
            "out": str(out),
            "commands": [["gn", "gen"], ["autoninja", "chrome"]],
            "receipt": str(out / build_pipeline.SLICE_RECEIPT_NAME),
            "ninja": self.ninja_report,
            "xcode27_compatibility": {
                "path": str(xcode27),
                "sha256": build_pipeline.sha256_file(xcode27),
            },
            "xcode27_seatbelt_compatibility": {
                "path": str(seatbelt),
                "sha256": build_pipeline.sha256_file(seatbelt),
            },
            "screen_ai_disabled_compatibility": {
                "path": str(screen_ai),
                "sha256": build_pipeline.sha256_file(screen_ai),
            },
            "xcode27_linkedit_strip_compatibility": {
                "path": str(linkedit),
                "sha256": build_pipeline.sha256_file(linkedit),
            },
            "linkedit_strip_tools": self.linkedit_tools,
        }
        app_report = {
            "app": str(out / build_pipeline.APP_NAME),
            "bundle_id": build_pipeline.focus_macos.BUNDLE_ID,
            "executable": "Focus Browser",
            "architectures": ["arm64"],
        }
        with mock.patch.object(build_pipeline, "SIGN_CHROME_SHA256", sign_hash), mock.patch.object(
            build_pipeline, "require_free", return_value=80 * build_pipeline.GIB
        ), mock.patch.object(build_pipeline, "run_monitored") as run, mock.patch.object(
            build_pipeline, "app_report", return_value=app_report
        ), mock.patch.object(
            build_pipeline,
            "xcode27_compat_receipt_contract",
            return_value=(xcode27, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "xcode27_seatbelt_receipt_contract",
            return_value=(seatbelt, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "screen_ai_disabled_receipt_contract",
            return_value=(screen_ai, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "xcode27_linkedit_strip_receipt_contract",
            return_value=(linkedit, {"tools": self.linkedit_tools}),
        ), mock.patch.object(
            build_pipeline,
            "generated_linkedit_strip_contract",
            return_value=generated_linkedit,
        ):
            report = build_pipeline.execute_build(
                self.source, self.developer, plan
            )
        self.assertEqual(2, run.call_count)
        receipt = json.loads(Path(report["path"]).read_text(encoding="utf-8"))
        self.assertTrue(receipt["build_complete"])
        self.assertEqual("arm64", receipt["mach_o_architecture"])
        self.assertEqual(sign_hash, receipt["sign_chrome_sha256"])
        self.assertEqual(self.ninja_report, receipt["ninja"])
        self.assertEqual(
            build_pipeline.sha256_file(xcode27),
            receipt["xcode27_compatibility_receipt_sha256"],
        )
        self.assertEqual(
            build_pipeline.sha256_file(seatbelt),
            receipt["xcode27_seatbelt_compatibility_receipt_sha256"],
        )
        self.assertEqual(
            build_pipeline.sha256_file(screen_ai),
            receipt["screen_ai_disabled_compatibility_receipt_sha256"],
        )
        self.assertEqual(
            build_pipeline.sha256_file(linkedit),
            receipt[
                "xcode27_linkedit_strip_compatibility_receipt_sha256"
            ],
        )
        self.assertEqual(generated_linkedit, receipt["generated_linkedit_strip"])

    def test_stage_reclaims_only_exact_arm_output_after_verified_copy(self):
        arm_out = self.source / build_pipeline.ARM_OUT
        arm_out.mkdir(parents=True, exist_ok=True)
        app = self.make_app(arm_out)
        (arm_out / "args.gn").write_text("arm\n", encoding="utf-8")
        build_receipt = self.write_slice_receipt(arm_out, "arm64")
        build_receipt_hash = build_pipeline.sha256_file(build_receipt)
        x64_sentinel = self.source / build_pipeline.X64_OUT / "keep.txt"
        x64_sentinel.parent.mkdir(parents=True, exist_ok=True)
        x64_sentinel.write_text("keep", encoding="utf-8")
        staged = self.source / build_pipeline.STAGED_ARM_APP
        partial_root = self.source / build_pipeline.STAGING_ROOT / ".arm64.part"
        partial_app = partial_root / build_pipeline.APP_NAME
        plan = {
            "source_app": str(app),
            "staged_app": str(staged),
            "arm_out": str(arm_out),
            "receipt": str(self.source / build_pipeline.STAGE_RECEIPT),
            "reclaim_receipt": str(self.source / build_pipeline.RECLAIM_RECEIPT),
            "build_receipt": str(build_receipt),
            "partial_root": str(partial_root),
            "partial_app": str(partial_app),
            "ditto_command": ["/usr/bin/ditto", str(app), str(partial_app)],
        }

        def fake_ditto(_command, _cwd, _environment, watched_paths=None):
            self.assertEqual((self.source,), watched_paths)
            shutil.copytree(app, partial_app, symlinks=True)

        with mock.patch.object(
            build_pipeline, "run_monitored", side_effect=fake_ditto
        ), mock.patch.object(
            build_pipeline, "stage_arm_plan", return_value=plan
        ), mock.patch.object(
            build_pipeline, "app_report", return_value={"architectures": ["arm64"]}
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=60 * build_pipeline.GIB
        ):
            report = build_pipeline.execute_stage_arm(self.source, plan, True)
        self.assertFalse(arm_out.exists())
        self.assertEqual("keep", x64_sentinel.read_text(encoding="utf-8"))
        receipt = json.loads(Path(report["path"]).read_text(encoding="utf-8"))
        self.assertTrue(receipt["reclaim_complete"])
        self.assertEqual(
            build_receipt_hash,
            json.loads(
                (self.source / build_pipeline.STAGE_RECEIPT).read_text(encoding="utf-8")
            )["build_receipt_sha256"],
        )

    def prepare_merge_fixture(self):
        arm_app = self.source / build_pipeline.STAGED_ARM_APP
        self.make_app(arm_app.parent, "arm64")
        digest = build_pipeline.tree_digest(arm_app)
        stage_receipt = self.write_json(
            self.source / build_pipeline.STAGE_RECEIPT,
            {"tree_sha256": digest},
        )
        prep = json.loads(
            (self.source / build_pipeline.PREPARATION_RECEIPT).read_text(encoding="utf-8")
        )
        prep["post_prepare_sha256"][
            build_pipeline.prepare_source.INSTALLER_MAC_BUILD_GN
        ] = build_pipeline.INSTALLER_BUILD_GN_SHA256
        self.write_json(self.source / build_pipeline.PREPARATION_RECEIPT, prep)
        arm_args_hash = prep["post_prepare_sha256"]["args_gn/arm64"]
        arm_out = self.source / build_pipeline.ARM_OUT
        if arm_out.exists():
            shutil.rmtree(arm_out)
        self.write_json(
            self.source / build_pipeline.RECLAIM_RECEIPT,
            {
                "schema": 1,
                "reclaim_complete": True,
                "source_root": str(self.source),
                "staged_app": str(arm_app),
                "tree_sha256": digest,
                "reclaimed_out": str(arm_out),
                "reclaimed_out_bytes": 100,
                "arm_args_gn_sha256": arm_args_hash,
                "stage_receipt_sha256": build_pipeline.sha256_file(stage_receipt),
            },
        )
        x64_out = self.source / build_pipeline.X64_OUT
        x64_out.mkdir(parents=True, exist_ok=True)
        self.make_app(x64_out, "x86_64")
        packaging = x64_out / build_pipeline.PACKAGING_NAME
        packaging.mkdir()
        (packaging / "sign_chrome.py").write_text("sign\n", encoding="utf-8")
        self.write_slice_receipt(x64_out, "x64")
        signing_root = self.source / "chrome/installer/mac"
        (signing_root / "sign_chrome.py").write_text("source sign\n", encoding="utf-8")
        (signing_root / "mac_signing_sources.gni").write_text(
            "sources = []\n", encoding="utf-8"
        )
        universalizer = self.source / build_pipeline.focus_macos.CHROMIUM_UNIVERSALIZER
        universalizer.parent.mkdir(parents=True, exist_ok=True)
        universalizer.write_text("universalize\n", encoding="utf-8")
        return arm_app, x64_out, universalizer

    def prepare_execute_merge_fixture(self):
        python = {"path": str(self.root / "pinned-python3.11")}
        output = self.root / "FocusBrowser-runtime-gated.dmg"
        receipts = {}
        for label, relative in (
            ("linkedit", build_pipeline.XCODE27_LINKEDIT_STRIP_RECEIPT),
            ("swiftshader", build_pipeline.SWIFTSHADER_DISABLED_SIGNING_RECEIPT),
            ("adhoc", build_pipeline.ADHOC_RUNTIME_SIGNING_RECEIPT),
        ):
            receipts[label] = self.write_json(
                self.source / relative, {"fixture": label}
            )
        digest = "a" * 64
        plan = {
            "packaging_python": python,
            "commands": {
                "copy_packaging": ["/usr/bin/ditto", "packaging", "unsigned"],
                "universalize": [python["path"], "universalize.py"],
                "sign": [python["path"], "sign_chrome.py"],
                "package": [
                    python["path"],
                    "package_local_dmg.py",
                    "--output",
                    str(output),
                ],
            },
            "arm_app": str(self.root / "arm64/Focus Browser.app"),
            "x64_app": str(self.root / "x64/Focus Browser.app"),
            "unsigned_root": str(self.source / build_pipeline.UNSIGNED_ROOT),
            "signed_root": str(self.source / build_pipeline.SIGNED_ROOT),
            "dmg_output": str(output),
            "xcode27_linkedit_strip_compatibility": {
                "path": str(receipts["linkedit"]),
                "sha256": digest,
            },
            "swiftshader_disabled_signing": {
                "path": str(receipts["swiftshader"]),
                "sha256": digest,
            },
            "adhoc_runtime_signing": {
                "path": str(receipts["adhoc"]),
                "sha256": digest,
            },
        }
        return {
            "plan": plan,
            "python": python,
            "output": output,
            "receipts": receipts,
            "digest": digest,
        }

    @contextmanager
    def mocked_merge_dependencies(
        self,
        fixture,
        monitored,
        signed_runtime_side_effect=None,
        mounted_runtime_side_effect=None,
    ):
        generated_hashes = {
            Path(relative).name: hashes["post_sha256"]
            for relative, hashes in (
                build_pipeline.ADHOC_RUNTIME_SIGNING_GENERATED_FILES.items()
            )
        }

        def pinned_hash(path):
            path = Path(path)
            if path.name == "sign_chrome.py":
                return build_pipeline.SIGN_CHROME_SHA256
            if path.name in generated_hashes:
                return generated_hashes[path.name]
            return fixture["digest"]

        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    build_pipeline,
                    "packaging_python_contract",
                    return_value=fixture["python"],
                )
            )
            stack.enter_context(
                mock.patch.object(
                    build_pipeline,
                    "xcode27_linkedit_strip_receipt_contract",
                    return_value=(fixture["receipts"]["linkedit"], {}),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    build_pipeline,
                    "swiftshader_disabled_signing_receipt_contract",
                    return_value=(fixture["receipts"]["swiftshader"], {}),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    build_pipeline,
                    "adhoc_runtime_signing_receipt_contract",
                    return_value=(fixture["receipts"]["adhoc"], {}),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    build_pipeline, "sha256_file", side_effect=pinned_hash
                )
            )
            stack.enter_context(
                mock.patch.object(build_pipeline, "physical_size", return_value=1024)
            )
            stack.enter_context(
                mock.patch.object(
                    build_pipeline, "require_free", return_value=100 * build_pipeline.GIB
                )
            )
            stack.enter_context(
                mock.patch.object(build_pipeline, "safe_environment", return_value={})
            )
            stack.enter_context(
                mock.patch.object(build_pipeline, "run_monitored", side_effect=monitored)
            )
            stack.enter_context(
                mock.patch.object(
                    build_pipeline,
                    "app_report",
                    return_value={"architectures": ["arm64", "x86_64"]},
                )
            )
            stack.enter_context(
                mock.patch.object(
                    build_pipeline, "capture", return_value="Signature=adhoc"
                )
            )
            stack.enter_context(
                mock.patch.object(build_pipeline, "tree_digest", return_value="b" * 64)
            )
            stack.enter_context(
                mock.patch.object(
                    build_pipeline.package_local_dmg,
                    "validate_app",
                    return_value={
                        "bundle_id": build_pipeline.focus_macos.BUNDLE_ID,
                        "executable": "Focus Browser",
                        "architectures": ["arm64", "x86_64"],
                    },
                )
            )
            matrix = stack.enter_context(
                mock.patch.object(
                    build_pipeline.runtime_smoke,
                    "validate_adhoc_signing_matrix",
                    return_value={"passed": True},
                )
            )
            signed_kwargs = (
                {"side_effect": signed_runtime_side_effect}
                if signed_runtime_side_effect is not None
                else {"return_value": {"passed": True, "location": "signed"}}
            )
            signed = stack.enter_context(
                mock.patch.object(
                    build_pipeline.runtime_smoke,
                    "validate_universal_app_runtime",
                    **signed_kwargs,
                )
            )
            mounted_kwargs = (
                {"side_effect": mounted_runtime_side_effect}
                if mounted_runtime_side_effect is not None
                else {
                    "side_effect": lambda path: {
                        "passed": True,
                        "location": "mounted",
                        "size_bytes": Path(path).stat().st_size,
                        "sha256": build_pipeline.package_local_dmg.sha256_file(
                            Path(path)
                        ),
                    }
                }
            )
            mounted = stack.enter_context(
                mock.patch.object(
                    build_pipeline.runtime_smoke,
                    "validate_mounted_dmg_runtime",
                    **mounted_kwargs,
                )
            )
            yield {"matrix": matrix, "signed": signed, "mounted": mounted}

    def prepare_swiftshader_execute_fixture(self):
        source_parts = self.source / "chrome/installer/mac/signing/parts.py"
        source_parts.parent.mkdir(parents=True, exist_ok=True)
        packaging_parts = (
            self.source
            / build_pipeline.X64_OUT
            / build_pipeline.PACKAGING_NAME
            / "signing/parts.py"
        )
        packaging_parts.parent.mkdir(parents=True, exist_ok=True)
        pre = b"pre signing parts\n"
        post = b"post signing parts\n"
        source_parts.write_bytes(pre)
        packaging_parts.write_bytes(pre)
        patch = self.root / "swiftshader-signing.patch"
        patch.write_text("fixture patch\n", encoding="utf-8")
        receipt = self.source / build_pipeline.SWIFTSHADER_DISABLED_SIGNING_RECEIPT
        files = {
            "chrome/installer/mac/signing/parts.py": {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            }
        }
        app_trees = {"arm64": "a" * 64, "x64": "b" * 64}
        plan = {
            "source_parts": str(source_parts),
            "packaging_parts": str(packaging_parts),
            "source_state": "pre",
            "packaging_state": "pre",
            "app_tree_sha256": app_trees,
            "refresh": {
                "command": ["autoninja", "-j8", "-C", "out", "copy_signing"],
                "ninja": {"path": str(self.ninja)},
            },
            "receipt": str(receipt),
            "preparation_receipt": {"path": "prep", "sha256": "c" * 64},
            "reclaim_receipt": {"path": "reclaim", "sha256": "d" * 64},
            "x64_build_receipt": {"path": "x64", "sha256": "e" * 64},
            "profiles": {"arm64": {}, "x64": {}},
            "build_args": {"arm64": {}, "x64": {}},
            "libraries": {"arm64": {}, "x64": {}},
            "patch": {"path": str(patch), "sha256": "f" * 64},
        }
        return plan, files, patch, source_parts, packaging_parts, pre, post

    def prepare_adhoc_runtime_execute_fixture(self):
        source_root = self.source / "chrome/installer/mac/signing"
        packaging_root = (
            self.source
            / build_pipeline.X64_OUT
            / build_pipeline.PACKAGING_NAME
            / "signing"
        )
        source_root.mkdir(parents=True, exist_ok=True)
        packaging_root.mkdir(parents=True, exist_ok=True)
        pre = b"pre ad-hoc signing\n"
        post = b"post ad-hoc signing\n"
        source_relative = "chrome/installer/mac/signing/parts.py"
        test_relative = "chrome/installer/mac/signing/parts_test.py"
        source_parts = self.source / source_relative
        source_test = self.source / test_relative
        packaging_parts = packaging_root / "parts.py"
        for path in (source_parts, source_test, packaging_parts):
            path.write_bytes(pre)
        files = {
            source_relative: {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            },
            test_relative: {
                "pre_sha256": hashlib.sha256(pre).hexdigest(),
                "post_sha256": hashlib.sha256(post).hexdigest(),
            },
        }
        generated = {source_relative: files[source_relative]}
        patch = self.root / "adhoc-runtime-signing.patch"
        patch.write_text("fixture patch\n", encoding="utf-8")
        receipt = self.source / build_pipeline.ADHOC_RUNTIME_SIGNING_RECEIPT
        app_trees = {"arm64": "a" * 64, "x64": "b" * 64}
        plan = {
            "source_paths": {
                source_relative: str(source_parts),
                test_relative: str(source_test),
            },
            "packaging_paths": {source_relative: str(packaging_parts)},
            "source_state": "pre",
            "packaging_state": "pre",
            "app_tree_sha256": app_trees,
            "tests": {
                "command": ["python3.11", "-m", "unittest", "signing.parts_test"],
                "working_directory": str(source_root.parent),
                "python": {"path": "python3.11"},
                "modules": ["signing.parts_test"],
            },
            "refresh": {
                "command": ["autoninja", "-j8", "-C", "out", "copy_signing"],
                "ninja": {"path": str(self.ninja)},
            },
            "refresh_strategy": {
                "mtime_independent": True,
                "forced_missing_outputs": [str(packaging_parts)],
            },
            "receipt": str(receipt),
            "preparation_receipt": {"path": "prep", "sha256": "c" * 64},
            "swiftshader_disabled_signing": {
                "path": "swiftshader",
                "sha256": "d" * 64,
            },
            "reclaim_receipt": {"path": "reclaim", "sha256": "e" * 64},
            "x64_build_receipt": {"path": "x64", "sha256": "f" * 64},
            "patch": {"path": str(patch), "sha256": "1" * 64},
        }
        return {
            "plan": plan,
            "files": files,
            "generated": generated,
            "patch": patch,
            "source_parts": source_parts,
            "source_test": source_test,
            "packaging_parts": packaging_parts,
            "pre": pre,
            "post": post,
        }

    def test_swiftshader_execute_publishes_receipt_after_exact_refresh(self):
        (
            plan,
            files,
            patch,
            source_parts,
            packaging_parts,
            _pre,
            post,
        ) = self.prepare_swiftshader_execute_fixture()

        def apply_source(*_args, **_kwargs):
            source_parts.write_bytes(post)

        def refresh_package(*_args, **_kwargs):
            packaging_parts.write_bytes(post)

        with mock.patch.object(
            build_pipeline,
            "swiftshader_disabled_signing_plan",
            return_value=plan,
        ), mock.patch.object(
            build_pipeline, "SWIFTSHADER_DISABLED_SIGNING_FILES", files
        ), mock.patch.object(
            build_pipeline, "SWIFTSHADER_DISABLED_SIGNING_PATCH", patch
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "apply_patch_plan",
            side_effect=apply_source,
        ), mock.patch.object(
            build_pipeline, "run_monitored", side_effect=refresh_package
        ), mock.patch.object(
            build_pipeline, "safe_environment", return_value={}
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=50 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline,
            "swiftshader_disabled_build_contract",
            return_value={"app_tree_sha256": plan["app_tree_sha256"]},
        ), mock.patch.object(
            build_pipeline, "swiftshader_disabled_signing_receipt_contract"
        ) as receipt_contract:
            report = build_pipeline.execute_swiftshader_disabled_signing(
                self.source, self.developer, plan
            )
        self.assertEqual(post, source_parts.read_bytes())
        self.assertEqual(post, packaging_parts.read_bytes())
        receipt = Path(plan["receipt"])
        self.assertTrue(receipt.is_file())
        value = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertTrue(value["signing_scripts_refreshed"])
        self.assertFalse(value["app_build_executed"])
        self.assertTrue(report["applied"])
        receipt_contract.assert_called_once_with(self.source, self.developer)

    def test_swiftshader_execute_rolls_back_both_files_on_refresh_failure(self):
        (
            plan,
            files,
            patch,
            source_parts,
            packaging_parts,
            pre,
            post,
        ) = self.prepare_swiftshader_execute_fixture()

        def apply_source(*_args, **_kwargs):
            source_parts.write_bytes(post)

        with mock.patch.object(
            build_pipeline,
            "swiftshader_disabled_signing_plan",
            return_value=plan,
        ), mock.patch.object(
            build_pipeline, "SWIFTSHADER_DISABLED_SIGNING_FILES", files
        ), mock.patch.object(
            build_pipeline, "SWIFTSHADER_DISABLED_SIGNING_PATCH", patch
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "apply_patch_plan",
            side_effect=apply_source,
        ), mock.patch.object(
            build_pipeline,
            "run_monitored",
            side_effect=build_pipeline.PipelineError("forced refresh failure"),
        ), mock.patch.object(
            build_pipeline, "safe_environment", return_value={}
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=50 * build_pipeline.GIB
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "forced refresh failure"
        ):
            build_pipeline.execute_swiftshader_disabled_signing(
                self.source, self.developer, plan
            )
        self.assertEqual(pre, source_parts.read_bytes())
        self.assertEqual(pre, packaging_parts.read_bytes())
        self.assertFalse(Path(plan["receipt"]).exists())

    def test_adhoc_runtime_execute_tests_then_refreshes_and_receipts(self):
        fixture = self.prepare_adhoc_runtime_execute_fixture()
        calls = []

        def apply_source(*_args, **_kwargs):
            fixture["source_parts"].write_bytes(fixture["post"])
            fixture["source_test"].write_bytes(fixture["post"])

        def monitored(command, *_args, **_kwargs):
            calls.append(command)
            if command == fixture["plan"]["refresh"]["command"]:
                fixture["packaging_parts"].write_bytes(fixture["post"])

        with mock.patch.object(
            build_pipeline,
            "adhoc_runtime_signing_plan",
            return_value=fixture["plan"],
        ), mock.patch.object(
            build_pipeline, "ADHOC_RUNTIME_SIGNING_FILES", fixture["files"]
        ), mock.patch.object(
            build_pipeline,
            "ADHOC_RUNTIME_SIGNING_GENERATED_FILES",
            fixture["generated"],
        ), mock.patch.object(
            build_pipeline, "ADHOC_RUNTIME_SIGNING_PATCH", fixture["patch"]
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "apply_patch_plan",
            side_effect=apply_source,
        ), mock.patch.object(
            build_pipeline, "run_monitored", side_effect=monitored
        ), mock.patch.object(
            build_pipeline, "safe_environment", return_value={}
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=50 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline,
            "swiftshader_disabled_build_contract",
            return_value={"app_tree_sha256": fixture["plan"]["app_tree_sha256"]},
        ), mock.patch.object(
            build_pipeline, "adhoc_runtime_signing_receipt_contract"
        ) as receipt_contract:
            report = build_pipeline.execute_adhoc_runtime_signing(
                self.source, self.developer, fixture["plan"]
            )

        self.assertEqual(
            [
                fixture["plan"]["tests"]["command"],
                fixture["plan"]["refresh"]["command"],
            ],
            calls,
        )
        receipt = Path(fixture["plan"]["receipt"])
        value = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertTrue(value["chromium_tests_passed"])
        self.assertTrue(value["signing_scripts_refreshed"])
        self.assertFalse(value["app_build_executed"])
        self.assertTrue(report["applied"])
        receipt_contract.assert_called_once_with(self.source, self.developer)

    def test_adhoc_runtime_execute_rolls_back_source_and_package_on_failure(self):
        fixture = self.prepare_adhoc_runtime_execute_fixture()
        calls = 0

        def apply_source(*_args, **_kwargs):
            fixture["source_parts"].write_bytes(fixture["post"])
            fixture["source_test"].write_bytes(fixture["post"])

        def fail_refresh(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise build_pipeline.PipelineError("forced ad-hoc refresh failure")

        with mock.patch.object(
            build_pipeline,
            "adhoc_runtime_signing_plan",
            return_value=fixture["plan"],
        ), mock.patch.object(
            build_pipeline, "ADHOC_RUNTIME_SIGNING_FILES", fixture["files"]
        ), mock.patch.object(
            build_pipeline,
            "ADHOC_RUNTIME_SIGNING_GENERATED_FILES",
            fixture["generated"],
        ), mock.patch.object(
            build_pipeline, "ADHOC_RUNTIME_SIGNING_PATCH", fixture["patch"]
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "apply_patch_plan",
            side_effect=apply_source,
        ), mock.patch.object(
            build_pipeline, "run_monitored", side_effect=fail_refresh
        ), mock.patch.object(
            build_pipeline, "safe_environment", return_value={}
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=50 * build_pipeline.GIB
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "forced ad-hoc refresh failure"
        ):
            build_pipeline.execute_adhoc_runtime_signing(
                self.source, self.developer, fixture["plan"]
            )

        self.assertEqual(fixture["pre"], fixture["source_parts"].read_bytes())
        self.assertEqual(fixture["pre"], fixture["source_test"].read_bytes())
        self.assertEqual(fixture["pre"], fixture["packaging_parts"].read_bytes())
        self.assertFalse(Path(fixture["plan"]["receipt"]).exists())

    def test_durable_signing_journal_restores_exact_pre_crash_state(self):
        alias_receipt = self.source / build_pipeline.HOME_ALIAS_RECEIPT
        self.write_json(alias_receipt, {"fixture": True})
        targets = {}
        for label in ("source", "packaging", "ninja_log", "ninja_deps"):
            path = self.source / "fixture-signing" / label
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((label + " original\n").encode("utf-8"))
            path.chmod(0o640)
            targets[label] = path
        original_snapshots = {
            label: build_pipeline._regular_file_snapshot(path)
            for label, path in targets.items()
        }
        original_modes = {
            label: stat.S_IMODE(path.stat().st_mode)
            for label, path in targets.items()
        }
        with mock.patch.object(
            build_pipeline, "_home_alias_is_active", return_value=True
        ), mock.patch.object(
            build_pipeline, "_signing_transaction_targets", return_value=targets
        ):
            for stage in ("swiftshader", "adhoc"):
                with self.subTest(stage=stage):
                    receipt = self.source / "out/fixture-{}-receipt.json".format(
                        stage
                    )
                    transaction = build_pipeline._begin_durable_signing_transaction(
                        self.source, stage, receipt
                    )
                    self.assertIsNotNone(transaction)
                    transaction = Path(transaction)
                    journal = transaction / "journal.json"
                    value = json.loads(journal.read_text(encoding="utf-8"))
                    self.assertTrue(value["prepared_before_mutation"])
                    self.assertFalse(
                        stat.S_IMODE(journal.stat().st_mode) & 0o222
                    )
                    self.assertEqual(
                        targets["source"].stat().st_dev,
                        transaction.stat().st_dev,
                    )
                    for item in value["files"]:
                        backup = Path(item["backup"])
                        self.assertFalse(
                            stat.S_IMODE(backup.stat().st_mode) & 0o222
                        )

                    targets["source"].write_bytes(b"mutated source\n")
                    targets["packaging"].unlink()
                    targets["ninja_log"].write_bytes(b"compacted history\n")
                    targets["ninja_deps"].chmod(0o600)
                    recovery = build_pipeline._load_durable_signing_transaction(
                        self.source, stage, receipt
                    )
                    self.assertFalse(recovery["receipt_published"])
                    build_pipeline._restore_durable_signing_transaction(
                        self.source, stage, receipt
                    )

                    self.assertFalse(transaction.exists())
                    for label, path in targets.items():
                        self.assertEqual(
                            original_snapshots[label],
                            build_pipeline._regular_file_snapshot(path),
                        )
                        self.assertEqual(
                            original_modes[label],
                            stat.S_IMODE(path.stat().st_mode),
                        )
                    build_pipeline._fsync_durable_signing_targets(
                        self.source, stage
                    )

                    second = build_pipeline._begin_durable_signing_transaction(
                        self.source, stage, receipt
                    )
                    self.write_json(receipt, {"committed": True})
                    committed = build_pipeline._load_durable_signing_transaction(
                        self.source, stage, receipt
                    )
                    self.assertTrue(committed["receipt_published"])
                    self.assertEqual(
                        receipt.stat().st_ino,
                        committed["receipt_identity"]["inode"],
                    )
                    build_pipeline._discard_durable_signing_transaction(
                        self.source, stage
                    )
                    self.assertFalse(Path(second).exists())

    def test_partial_signing_journal_is_idempotently_classified(self):
        alias_receipt = self.source / build_pipeline.HOME_ALIAS_RECEIPT
        self.write_json(alias_receipt, {"fixture": True})
        targets = {"source": self.source / "partial-target"}
        targets["source"].write_bytes(b"unchanged\n")
        receipt = self.source / "out/partial-signing-receipt.json"
        with mock.patch.object(
            build_pipeline, "_home_alias_is_active", return_value=True
        ), mock.patch.object(
            build_pipeline, "_signing_transaction_targets", return_value=targets
        ):
            root = Path(
                build_pipeline._signing_transaction_path(
                    self.source, "swiftshader"
                )
            )
            root.mkdir(parents=True)
            self.assertIsNone(
                build_pipeline._load_durable_signing_transaction(
                    self.source, "swiftshader", receipt
                )
            )
            self.assertFalse(root.exists())

            cleanup = build_pipeline._signing_transaction_cleanup_path(
                self.source, "swiftshader"
            )
            root.mkdir(parents=True)
            (root / "partially-removed-backup").write_bytes(b"stale\n")
            root_identity = build_pipeline._stable_directory_identity(root)
            build_pipeline._publish_signing_cleanup_authorization(
                self.source, "swiftshader", root_identity
            )
            os.replace(root, cleanup)
            self.assertIsNone(
                build_pipeline._load_durable_signing_transaction(
                    self.source, "swiftshader", receipt
                )
            )
            self.assertFalse(cleanup.exists())

            root.mkdir(parents=True)
            self.write_json(receipt, {"committed": True})
            recovery = build_pipeline._load_durable_signing_transaction(
                self.source, "swiftshader", receipt
            )
            self.assertTrue(recovery["receipt_published"])
            self.assertTrue(recovery["cleanup_only"])
            build_pipeline._discard_durable_signing_transaction(
                self.source, "swiftshader"
            )

    def test_invalid_published_signing_receipt_rolls_back_before_retry(self):
        cases = (
            (
                "swiftshader",
                "execute_swiftshader_disabled_signing",
                "swiftshader_disabled_signing_plan",
                "swiftshader_disabled_signing_receipt_contract",
            ),
            (
                "adhoc",
                "execute_adhoc_runtime_signing",
                "adhoc_runtime_signing_plan",
                "adhoc_runtime_signing_receipt_contract",
            ),
        )
        for stage, execute_name, plan_name, contract_name in cases:
            with self.subTest(stage=stage):
                receipt = self.source / "out/invalid-{}-receipt.json".format(stage)
                recovery_plan = {
                    "receipt": str(receipt),
                    "crash_recovery": {
                        "receipt_published": True,
                        "receipt_identity": {"fixture": True},
                    },
                }
                fresh_plan = {"stage": "fresh-{}".format(stage)}
                original_execute = getattr(build_pipeline, execute_name)
                with mock.patch.object(
                    build_pipeline,
                    plan_name,
                    side_effect=(recovery_plan, fresh_plan),
                ), mock.patch.object(
                    build_pipeline,
                    contract_name,
                    side_effect=build_pipeline.PipelineError("invalid receipt"),
                ), mock.patch.object(
                    build_pipeline, "_remove_invalid_transaction_receipt"
                ) as remove, mock.patch.object(
                    build_pipeline, "_restore_durable_signing_transaction"
                ) as restore, mock.patch.object(
                    build_pipeline,
                    execute_name,
                    return_value={"applied": True},
                ) as recursive_execute:
                    result = original_execute(
                        self.source, self.developer, recovery_plan
                    )
                self.assertTrue(result["invalid_receipt_rolled_back"])
                remove.assert_called_once_with(receipt, {"fixture": True})
                restore.assert_called_once_with(self.source, stage, receipt)
                recursive_execute.assert_called_once_with(
                    self.source, self.developer, fresh_plan
                )

    def test_cleanup_authorization_refuses_a_replaced_tombstone(self):
        self.write_json(
            self.source / build_pipeline.HOME_ALIAS_RECEIPT,
            {"fixture": True},
        )
        stage = "adhoc"
        root = build_pipeline._signing_transaction_path(self.source, stage)
        root.mkdir(parents=True)
        (root / "authorized-data").write_bytes(b"authorized\n")
        identity = build_pipeline._stable_directory_identity(root)
        build_pipeline._publish_signing_cleanup_authorization(
            self.source, stage, identity
        )
        cleanup = build_pipeline._signing_transaction_cleanup_path(
            self.source, stage
        )
        os.replace(root, cleanup)
        preserved = self.root / "authorized-tombstone-preserved"
        os.replace(cleanup, preserved)
        cleanup.mkdir()
        rival = cleanup / "rival-data"
        rival.write_bytes(b"must survive\n")
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "tombstone was replaced"
        ):
            build_pipeline._cleanup_signing_transaction_tombstone(
                self.source, stage
            )
        self.assertEqual(b"must survive\n", rival.read_bytes())
        self.assertEqual(
            b"authorized\n", (preserved / "authorized-data").read_bytes()
        )

    def test_cleanup_authorization_uses_current_device_after_reboot(self):
        self.write_json(
            self.source / build_pipeline.HOME_ALIAS_RECEIPT,
            {"fixture": True},
        )
        stage = "swiftshader"
        root = build_pipeline._signing_transaction_path(self.source, stage)
        root.mkdir(parents=True)
        (root / "journal-fragment").write_bytes(b"private\n")
        observed = build_pipeline._live_directory_identity(root)
        durable = {
            "volume_uuid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
            "inode": observed["inode"],
            "uid": observed["uid"],
            "gid": observed["gid"],
            "mode": observed["mode"],
        }
        build_pipeline._publish_signing_cleanup_authorization(
            self.source, stage, durable
        )
        authorization = json.loads(
            build_pipeline._signing_transaction_cleanup_marker_path(
                self.source, stage
            ).read_text()
        )
        self.assertEqual(durable, authorization["root_identity"])
        self.assertNotIn("device", authorization["root_identity"])

        reboot_device = observed["device"] + 1000
        reboot_live = {**observed, "device": reboot_device}

        def remove_with_current_device(path, expected, _label):
            self.assertEqual(reboot_live, expected)
            shutil.rmtree(path)

        with mock.patch.object(
            build_pipeline, "_stable_directory_identity", return_value=durable
        ), mock.patch.object(
            build_pipeline,
            "_live_directory_identity",
            return_value=reboot_live,
        ), mock.patch.object(
            build_pipeline,
            "_remove_directory_inode",
            side_effect=remove_with_current_device,
        ):
            build_pipeline._cleanup_signing_transaction_tombstone(
                self.source, stage
            )
        self.assertFalse(
            build_pipeline._signing_transaction_cleanup_marker_path(
                self.source, stage
            ).exists()
        )

    def test_cleanup_never_deletes_an_unknown_fixed_temporary(self):
        stage = "adhoc"
        marker = build_pipeline._signing_transaction_cleanup_marker_path(
            self.source, stage
        )
        unknown = marker.with_name("." + marker.name + ".tmp")
        unknown.parent.mkdir(parents=True, exist_ok=True)
        unknown.write_bytes(b"unrelated evidence\n")
        unknown.chmod(0o444)
        build_pipeline._cleanup_signing_transaction_tombstone(
            self.source, stage
        )
        self.assertEqual(b"unrelated evidence\n", unknown.read_bytes())

    def test_failed_execution_receipt_removes_only_its_invalid_inode(self):
        receipt = self.source / "out/transactional-receipt.json"
        report = build_pipeline.atomic_json(receipt, {"state": "invalid"})

        def invalid():
            raise build_pipeline.PipelineError("invalid receipt")

        self.assertTrue(
            build_pipeline._remove_failed_execution_receipt(
                receipt,
                report.publication_identity,
                invalid,
                "fixture receipt",
            )
        )
        self.assertFalse(receipt.exists())

        valid_report = build_pipeline.atomic_json(receipt, {"state": "valid"})
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "valid committed receipt"
        ):
            build_pipeline._remove_failed_execution_receipt(
                receipt,
                valid_report.publication_identity,
                lambda: True,
                "fixture receipt",
            )
        self.assertEqual({"state": "valid"}, json.loads(receipt.read_text()))

        receipt.unlink()
        raced_report = build_pipeline.atomic_json(receipt, {"state": "ours"})
        preserved = receipt.with_name("our-receipt-preserved.json")
        os.replace(receipt, preserved)
        self.write_json(receipt, {"state": "rival"})
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "identity changed"
        ):
            build_pipeline._remove_failed_execution_receipt(
                receipt,
                raced_report.publication_identity,
                invalid,
                "fixture receipt",
            )
        self.assertEqual({"state": "rival"}, json.loads(receipt.read_text()))
        self.assertEqual({"state": "ours"}, json.loads(preserved.read_text()))

    def test_durable_fsync_rejects_atomic_target_path_replacement(self):
        target = self.source / "fsync-target"
        replacement = self.source / "fsync-replacement"
        target.write_bytes(b"old inode\n")
        replacement.write_bytes(b"new inode\n")
        real_fsync = os.fsync
        calls = 0

        def replace_on_first_fsync(descriptor):
            nonlocal calls
            calls += 1
            real_fsync(descriptor)
            if calls == 1:
                os.replace(replacement, target)

        with mock.patch.object(
            build_pipeline,
            "_signing_transaction_targets",
            return_value={"target": target},
        ), mock.patch.object(
            build_pipeline.os, "fsync", side_effect=replace_on_first_fsync
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "changed"):
            build_pipeline._fsync_durable_signing_targets(
                self.source, "swiftshader"
            )
        self.assertEqual(b"new inode\n", target.read_bytes())

    def test_invalid_receipt_removal_preserves_a_replacement_inode(self):
        receipt = self.source / "out/raced-invalid-receipt.json"
        self.write_json(receipt, {"original": True})
        identity = build_pipeline._lstat_identity(receipt)
        preserved = receipt.with_name("original-preserved.json")
        os.replace(receipt, preserved)
        self.write_json(receipt, {"rival": True})
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "unsafe"
        ):
            build_pipeline._remove_invalid_transaction_receipt(
                receipt, identity
            )
        self.assertEqual({"rival": True}, json.loads(receipt.read_text()))
        self.assertEqual(
            {"original": True}, json.loads(preserved.read_text())
        )

    def test_adhoc_runtime_refresh_forces_missing_output_when_source_is_older(self):
        fixture = self.prepare_adhoc_runtime_execute_fixture()
        fixture["source_parts"].write_bytes(fixture["post"])
        fixture["source_test"].write_bytes(fixture["post"])
        fixture["packaging_parts"].write_bytes(fixture["pre"])
        fixture["plan"]["source_state"] = "post"
        fixture["plan"]["packaging_state"] = "pre"
        old_ns = 1_000_000_000
        newer_ns = 2_000_000_000
        os.utime(fixture["source_parts"], ns=(old_ns, old_ns))
        os.utime(fixture["source_test"], ns=(old_ns, old_ns))
        os.utime(fixture["packaging_parts"], ns=(newer_ns, newer_ns))
        self.assertLess(
            fixture["source_parts"].stat().st_mtime_ns,
            fixture["packaging_parts"].stat().st_mtime_ns,
        )
        calls = []

        def monitored(command, *_args, **_kwargs):
            calls.append(command)
            if command == fixture["plan"]["refresh"]["command"]:
                self.assertFalse(
                    os.path.lexists(str(fixture["packaging_parts"]))
                )
                fixture["packaging_parts"].write_bytes(fixture["post"])

        with mock.patch.object(
            build_pipeline,
            "adhoc_runtime_signing_plan",
            return_value=fixture["plan"],
        ), mock.patch.object(
            build_pipeline, "ADHOC_RUNTIME_SIGNING_FILES", fixture["files"]
        ), mock.patch.object(
            build_pipeline,
            "ADHOC_RUNTIME_SIGNING_GENERATED_FILES",
            fixture["generated"],
        ), mock.patch.object(
            build_pipeline, "ADHOC_RUNTIME_SIGNING_PATCH", fixture["patch"]
        ), mock.patch.object(
            build_pipeline.prepare_source, "apply_patch_plan"
        ) as apply_patch, mock.patch.object(
            build_pipeline, "run_monitored", side_effect=monitored
        ), mock.patch.object(
            build_pipeline, "safe_environment", return_value={}
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=50 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline,
            "swiftshader_disabled_build_contract",
            return_value={"app_tree_sha256": fixture["plan"]["app_tree_sha256"]},
        ), mock.patch.object(
            build_pipeline, "adhoc_runtime_signing_receipt_contract"
        ):
            build_pipeline.execute_adhoc_runtime_signing(
                self.source, self.developer, fixture["plan"]
            )

        apply_patch.assert_not_called()
        self.assertEqual(fixture["post"], fixture["packaging_parts"].read_bytes())
        self.assertEqual(
            [
                fixture["plan"]["tests"]["command"],
                fixture["plan"]["refresh"]["command"],
            ],
            calls,
        )
        receipt = json.loads(
            Path(fixture["plan"]["receipt"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"source": "post", "packaging": "pre"},
            receipt["recovery_state"],
        )
        self.assertTrue(receipt["refresh_strategy"]["mtime_independent"])

    def test_merge_plan_uses_chromium_x64_first_and_ad_hoc_signing(self):
        _, x64_out, universalizer = self.prepare_merge_fixture()
        linkedit_receipt = (
            self.source / build_pipeline.XCODE27_LINKEDIT_STRIP_RECEIPT
        )
        output = self.root / "FocusBrowser.dmg"
        swiftshader_receipt = self.write_json(
            self.source / build_pipeline.SWIFTSHADER_DISABLED_SIGNING_RECEIPT,
            {"fixture": True},
        )
        adhoc_receipt = self.write_json(
            self.source / build_pipeline.ADHOC_RUNTIME_SIGNING_RECEIPT,
            {"fixture": True},
        )
        packaging_python = {
            "path": str(self.root / "pinned-python3.11"),
            "version": "3.11.8",
        }
        real_hash = build_pipeline.sha256_file

        def pinned_hash(path):
            path = Path(path)
            if path.name == "sign_chrome.py":
                return build_pipeline.SIGN_CHROME_SHA256
            if path == universalizer:
                return build_pipeline.focus_macos.PINNED_CHROMIUM_UNIVERSALIZER_SHA256
            if path == self.source / "chrome/installer/mac/BUILD.gn":
                return build_pipeline.INSTALLER_BUILD_GN_SHA256
            if path.name == "mac_signing_sources.gni":
                return build_pipeline.MAC_SIGNING_SOURCES_GNI_SHA256
            return real_hash(path)

        with mock.patch.object(
            build_pipeline, "app_report", return_value={"architectures": ["arm64"]}
        ), mock.patch.object(
            build_pipeline, "sha256_file", side_effect=pinned_hash
        ), mock.patch.object(
            build_pipeline,
            "packaging_python_contract",
            return_value=packaging_python,
        ), mock.patch.object(
            build_pipeline,
            "swiftshader_disabled_signing_receipt_contract",
            return_value=(swiftshader_receipt, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "adhoc_runtime_signing_receipt_contract",
            return_value=(adhoc_receipt, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "xcode27_linkedit_strip_receipt_contract",
            return_value=(
                linkedit_receipt,
                {"tools": self.linkedit_tools},
            ),
        ):
            plan = build_pipeline.merge_plan(
                self.source, self.developer, output
            )
        self.assertEqual(packaging_python, plan["packaging_python"])
        self.assertEqual(
            build_pipeline.sha256_file(swiftshader_receipt),
            plan["swiftshader_disabled_signing"]["sha256"],
        )
        self.assertEqual(
            build_pipeline.sha256_file(adhoc_receipt),
            plan["adhoc_runtime_signing"]["sha256"],
        )
        self.assertEqual(
            build_pipeline.sha256_file(linkedit_receipt),
            plan["xcode27_linkedit_strip_compatibility"]["sha256"],
        )
        for name in ("universalize", "sign", "package"):
            self.assertEqual(packaging_python["path"], plan["commands"][name][0])
        universalize = plan["commands"]["universalize"]
        self.assertEqual(str(x64_out / build_pipeline.APP_NAME), universalize[-3])
        self.assertEqual(str(self.source / build_pipeline.STAGED_ARM_APP), universalize[-2])
        sign = plan["commands"]["sign"]
        self.assertIn("--identity", sign)
        self.assertEqual("-", sign[sign.index("--identity") + 1])
        self.assertIn("--development", sign)
        self.assertIn("--disable-packaging", sign)
        self.assertEqual("none", sign[sign.index("--notarize") + 1])
        package = plan["commands"]["package"]
        self.assertEqual(
            str(
                self.source
                / build_pipeline.SIGNED_ROOT
                / build_pipeline.SIGNED_DISTRIBUTION_DIR
                / build_pipeline.APP_NAME
            ),
            package[package.index("--app") + 1],
        )
        runtime = plan["runtime_acceptance"]
        self.assertTrue(runtime["signed_app_before_packaging"])
        self.assertTrue(runtime["mounted_final_dmg"])
        self.assertEqual("0700", runtime["private_candidate_mode"])
        self.assertTrue(runtime["final_output_absent_until_runtime_passes"])
        self.assertTrue(runtime["atomic_no_overwrite_publish"])
        self.assertTrue(runtime["descriptor_pinned_publish"])
        self.assertTrue(runtime["durable_final_entry_before_candidate_unlink"])
        self.assertFalse(runtime["persistent_publish_recovery_journal"])
        self.assertTrue(runtime["native_arm64_required"])
        self.assertTrue(runtime["rosetta_x86_64_required"])
        self.assertEqual(["arm64", "x86_64"], runtime["architectures"])
        self.assertEqual("data:text/html", runtime["offline_navigation"])
        self.assertTrue(runtime["incognito"])
        joined = " ".join(sign).lower()
        self.assertNotIn("developer id", joined)
        self.assertNotIn("notarytool", joined)

    def test_merge_rejects_relative_dmg_output(self):
        _, _, universalizer = self.prepare_merge_fixture()
        linkedit_receipt = (
            self.source / build_pipeline.XCODE27_LINKEDIT_STRIP_RECEIPT
        )
        swiftshader_receipt = self.write_json(
            self.source / build_pipeline.SWIFTSHADER_DISABLED_SIGNING_RECEIPT,
            {"fixture": True},
        )
        adhoc_receipt = self.write_json(
            self.source / build_pipeline.ADHOC_RUNTIME_SIGNING_RECEIPT,
            {"fixture": True},
        )
        real_hash = build_pipeline.sha256_file

        def pinned_hash(path):
            path = Path(path)
            if path.name == "sign_chrome.py":
                return build_pipeline.SIGN_CHROME_SHA256
            if path == universalizer:
                return build_pipeline.focus_macos.PINNED_CHROMIUM_UNIVERSALIZER_SHA256
            if path == self.source / "chrome/installer/mac/BUILD.gn":
                return build_pipeline.INSTALLER_BUILD_GN_SHA256
            if path.name == "mac_signing_sources.gni":
                return build_pipeline.MAC_SIGNING_SOURCES_GNI_SHA256
            return real_hash(path)

        with mock.patch.object(
            build_pipeline, "app_report", return_value={"architectures": ["arm64"]}
        ), mock.patch.object(
            build_pipeline, "sha256_file", side_effect=pinned_hash
        ), mock.patch.object(
            build_pipeline,
            "packaging_python_contract",
            return_value={"path": str(self.root / "pinned-python3.11")},
        ), mock.patch.object(
            build_pipeline,
            "swiftshader_disabled_signing_receipt_contract",
            return_value=(swiftshader_receipt, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "adhoc_runtime_signing_receipt_contract",
            return_value=(adhoc_receipt, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "xcode27_linkedit_strip_receipt_contract",
            return_value=(
                linkedit_receipt,
                {"tools": self.linkedit_tools},
            ),
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "absolute"
        ):
            build_pipeline.merge_plan(
                self.source, self.developer, Path("relative.dmg")
            )

    def test_merge_execution_rejects_python_command_drift_before_mutation(self):
        python = {"path": str(self.root / "pinned-python3.11")}
        unsigned = self.source / build_pipeline.UNSIGNED_ROOT
        plan = {
            "packaging_python": python,
            "commands": {
                "universalize": [python["path"], "universalizer.py"],
                "sign": ["/usr/bin/python3", "sign_chrome.py"],
                "package": [python["path"], "package_local_dmg.py"],
            },
        }
        with mock.patch.object(
            build_pipeline, "packaging_python_contract", return_value=python
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "pinned packaging Python"
        ):
            build_pipeline.execute_merge(self.source, self.developer, plan)
        self.assertFalse(unsigned.exists())

    def test_merge_runtime_gates_signed_app_before_package_then_mounted_dmg(self):
        fixture = self.prepare_execute_merge_fixture()
        events = []
        candidates = []
        candidate_identities = []

        def monitored(command, *_args, **_kwargs):
            if self.is_package_command(command):
                events.append("package")
                candidate = self.package_command_output(command)
                candidates.append(candidate)
                self.assertFalse(fixture["output"].exists())
                self.assertEqual(
                    0o700, stat.S_IMODE(os.lstat(str(candidate.parent)).st_mode)
                )
                candidate.write_bytes(b"runtime-gated dmg")
                observed = os.lstat(str(candidate))
                candidate_identities.append((observed.st_dev, observed.st_ino))

        def mounted_runtime(_path):
            events.append("mounted-runtime")
            self.assertFalse(fixture["output"].exists())
            return {
                "passed": True,
                "location": "mounted",
                "size_bytes": candidates[0].stat().st_size,
                "sha256": build_pipeline.package_local_dmg.sha256_file(
                    candidates[0]
                ),
            }

        with self.mocked_merge_dependencies(
            fixture, monitored
        ) as dependencies:
            dependencies["matrix"].side_effect = lambda *_args: (
                events.append("matrix") or {"passed": True}
            )
            dependencies["signed"].side_effect = lambda *_args: (
                events.append("signed-runtime")
                or {"passed": True, "location": "signed"}
            )
            dependencies["mounted"].side_effect = mounted_runtime
            report = build_pipeline.execute_merge(
                self.source, self.developer, fixture["plan"]
            )

        self.assertEqual(
            ["matrix", "signed-runtime", "package", "mounted-runtime"],
            events,
        )
        self.assertEqual(1, len(candidates))
        self.assertFalse(candidates[0].parent.exists())
        self.assertTrue(fixture["output"].is_file())
        published = os.lstat(str(fixture["output"]))
        self.assertEqual(1, published.st_nlink)
        self.assertEqual(
            candidate_identities[0], (published.st_dev, published.st_ino)
        )
        self.assertTrue(report["codesign_matrix"]["passed"])
        self.assertEqual(
            "signed", report["runtime_acceptance"]["signed_app"]["location"]
        )
        self.assertEqual(
            "mounted",
            report["runtime_acceptance"]["mounted_final_dmg"]["location"],
        )
        self.assertTrue(
            report["runtime_acceptance"]["mounted_final_dmg"][
                "published_same_inode"
            ]
        )
        publication = report["runtime_acceptance"]["mounted_final_dmg"][
            "publication"
        ]
        self.assertEqual(
            "descriptor-pinned output-parent fsync",
            publication["commit_boundary"],
        )
        self.assertEqual(1, publication["final_link_count"])
        self.assertTrue(publication["candidate_unlinked_after_commit"])
        self.assertTrue(publication["private_root_cleanup_complete"])
        self.assertFalse(publication["persistent_recovery_journal"])

    def test_signed_app_runtime_failure_prevents_dmg_packaging(self):
        fixture = self.prepare_execute_merge_fixture()
        commands = []

        def monitored(command, *_args, **_kwargs):
            commands.append(command)
            if self.is_package_command(command):
                self.package_command_output(command).write_bytes(
                    b"must not be created"
                )

        failure = build_pipeline.runtime_smoke.RuntimeSmokeError(
            "synthetic signed runtime failure"
        )
        with self.mocked_merge_dependencies(
            fixture,
            monitored,
            signed_runtime_side_effect=failure,
        ), self.assertRaisesRegex(
            build_pipeline.runtime_smoke.RuntimeSmokeError,
            "synthetic signed runtime failure",
        ):
            build_pipeline.execute_merge(
                self.source, self.developer, fixture["plan"]
            )

        self.assertFalse(any(self.is_package_command(command) for command in commands))
        self.assertFalse(fixture["output"].exists())

    def test_mounted_dmg_runtime_failure_unlinks_exact_created_inode(self):
        fixture = self.prepare_execute_merge_fixture()
        candidates = []

        def monitored(command, *_args, **_kwargs):
            if self.is_package_command(command):
                candidate = self.package_command_output(command)
                candidates.append(candidate)
                candidate.write_bytes(b"rejected dmg")

        failure = build_pipeline.runtime_smoke.RuntimeSmokeError(
            "synthetic mounted runtime failure"
        )
        with self.mocked_merge_dependencies(
            fixture,
            monitored,
            mounted_runtime_side_effect=failure,
        ), self.assertRaisesRegex(
            build_pipeline.runtime_smoke.RuntimeSmokeError,
            "synthetic mounted runtime failure",
        ):
            build_pipeline.execute_merge(
                self.source, self.developer, fixture["plan"]
            )

        self.assertFalse(fixture["output"].exists())
        self.assertEqual(1, len(candidates))
        self.assertFalse(candidates[0].parent.exists())

    def test_package_post_run_failure_cleans_private_candidate(self):
        fixture = self.prepare_execute_merge_fixture()
        candidates = []

        def monitored(command, *_args, **_kwargs):
            if not self.is_package_command(command):
                return
            candidate = self.package_command_output(command)
            candidates.append(candidate)
            candidate.write_bytes(b"packager completed before monitor failed")
            raise build_pipeline.PipelineError("synthetic post-run disk gate")

        with self.mocked_merge_dependencies(
            fixture, monitored
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "synthetic post-run disk gate"
        ):
            build_pipeline.execute_merge(
                self.source, self.developer, fixture["plan"]
            )

        self.assertFalse(fixture["output"].exists())
        self.assertEqual(1, len(candidates))
        self.assertFalse(candidates[0].parent.exists())

    def test_atomic_publish_race_preserves_rival_and_cleans_candidate(self):
        fixture = self.prepare_execute_merge_fixture()
        candidates = []

        def monitored(command, *_args, **_kwargs):
            if self.is_package_command(command):
                candidate = self.package_command_output(command)
                candidates.append(candidate)
                candidate.write_bytes(b"accepted candidate")

        def mounted(path):
            path = Path(path)
            fixture["output"].write_bytes(b"unrelated rival")
            return {
                "passed": True,
                "location": "mounted",
                "size_bytes": path.stat().st_size,
                "sha256": build_pipeline.package_local_dmg.sha256_file(path),
            }

        with self.mocked_merge_dependencies(
            fixture, monitored, mounted_runtime_side_effect=mounted
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "refusing to overwrite"
        ):
            build_pipeline.execute_merge(
                self.source, self.developer, fixture["plan"]
            )

        self.assertEqual(b"unrelated rival", fixture["output"].read_bytes())
        self.assertEqual(1, len(candidates))
        self.assertFalse(candidates[0].parent.exists())

    def test_post_commit_root_cleanup_warning_never_removes_final_output(self):
        fixture = self.prepare_execute_merge_fixture()
        candidates = []
        real_cleanup = build_pipeline._cleanup_private_dmg_candidate
        cleanup_calls = 0

        def monitored(command, *_args, **_kwargs):
            if self.is_package_command(command):
                candidate = self.package_command_output(command)
                candidates.append(candidate)
                candidate.write_bytes(b"accepted candidate")

        def cleanup_then_fail(*args, **kwargs):
            nonlocal cleanup_calls
            cleanup_calls += 1
            real_cleanup(*args, **kwargs)
            raise build_pipeline.PipelineError("synthetic post-publish failure")

        with self.mocked_merge_dependencies(fixture, monitored), mock.patch.object(
            build_pipeline,
            "_cleanup_private_dmg_candidate",
            side_effect=cleanup_then_fail,
        ):
            report = build_pipeline.execute_merge(
                self.source, self.developer, fixture["plan"]
            )

        self.assertEqual(1, cleanup_calls)
        self.assertEqual(b"accepted candidate", fixture["output"].read_bytes())
        self.assertEqual(1, os.lstat(str(fixture["output"])).st_nlink)
        self.assertEqual(1, len(candidates))
        self.assertFalse(candidates[0].parent.exists())
        publication = report["runtime_acceptance"]["mounted_final_dmg"][
            "publication"
        ]
        self.assertFalse(publication["private_root_cleanup_complete"])
        self.assertIn("synthetic post-publish failure", publication["cleanup_warnings"][0])

    def test_committed_candidate_cleanup_error_is_recovered_without_rollback(self):
        fixture = self.prepare_execute_merge_fixture()
        candidates = []
        real_unlink = os.unlink
        rejected_once = False

        def monitored(command, *_args, **_kwargs):
            if self.is_package_command(command):
                candidate = self.package_command_output(command)
                candidates.append(candidate)
                candidate.write_bytes(b"accepted candidate")

        def reject_first_descriptor_relative_unlink(path, *args, **kwargs):
            nonlocal rejected_once
            if (
                not rejected_once
                and kwargs.get("dir_fd") is not None
                and Path(path).name == fixture["output"].name
            ):
                rejected_once = True
                raise OSError("synthetic descriptor-relative cleanup failure")
            return real_unlink(path, *args, **kwargs)

        with self.mocked_merge_dependencies(fixture, monitored), mock.patch(
            "os.unlink", side_effect=reject_first_descriptor_relative_unlink
        ):
            report = build_pipeline.execute_merge(
                self.source, self.developer, fixture["plan"]
            )

        self.assertTrue(rejected_once)
        self.assertEqual(b"accepted candidate", fixture["output"].read_bytes())
        self.assertEqual(1, os.lstat(str(fixture["output"])).st_nlink)
        self.assertFalse(candidates[0].parent.exists())
        publication = report["runtime_acceptance"]["mounted_final_dmg"][
            "publication"
        ]
        self.assertTrue(publication["private_root_cleanup_complete"])
        self.assertIn("durably committed", publication["cleanup_warnings"][0])

    def test_post_commit_final_verification_failure_retains_exact_output(self):
        fixture = self.prepare_execute_merge_fixture()
        candidates = []
        real_cleanup = build_pipeline._cleanup_private_dmg_candidate

        def monitored(command, *_args, **_kwargs):
            if self.is_package_command(command):
                candidate = self.package_command_output(command)
                candidates.append(candidate)
                candidate.write_bytes(b"accepted candidate")

        def cleanup_then_tamper(*args, **kwargs):
            real_cleanup(*args, **kwargs)
            fixture["output"].write_bytes(b"post-commit tamper")

        with self.mocked_merge_dependencies(fixture, monitored), mock.patch.object(
            build_pipeline,
            "_cleanup_private_dmg_candidate",
            side_effect=cleanup_then_tamper,
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "durable commit boundary"
        ):
            build_pipeline.execute_merge(
                self.source, self.developer, fixture["plan"]
            )

        self.assertEqual(b"post-commit tamper", fixture["output"].read_bytes())
        self.assertEqual(1, os.lstat(str(fixture["output"])).st_nlink)
        self.assertFalse(candidates[0].parent.exists())

    def test_interrupt_after_durable_helper_return_preserves_final_output(self):
        fixture = self.prepare_execute_merge_fixture()
        candidates = []
        real_publish = build_pipeline.package_local_dmg.durable_publish_candidate

        def monitored(command, *_args, **_kwargs):
            if self.is_package_command(command):
                candidate = self.package_command_output(command)
                candidates.append(candidate)
                candidate.write_bytes(b"accepted candidate")

        def publish_then_interrupt(*args, **kwargs):
            real_publish(*args, **kwargs)
            raise KeyboardInterrupt("synthetic interrupt after durable return")

        with self.mocked_merge_dependencies(fixture, monitored), mock.patch.object(
            build_pipeline.package_local_dmg,
            "durable_publish_candidate",
            side_effect=publish_then_interrupt,
        ), self.assertRaisesRegex(
            KeyboardInterrupt, "after durable return"
        ):
            build_pipeline.execute_merge(
                self.source, self.developer, fixture["plan"]
            )

        self.assertEqual(b"accepted candidate", fixture["output"].read_bytes())
        self.assertEqual(1, os.lstat(str(fixture["output"])).st_nlink)
        self.assertFalse(candidates[0].parent.exists())

    def test_mounted_dmg_detach_failure_retains_exact_created_inode(self):
        fixture = self.prepare_execute_merge_fixture()
        candidates = []

        def monitored(command, *_args, **_kwargs):
            if self.is_package_command(command):
                candidate = self.package_command_output(command)
                candidates.append(candidate)
                candidate.write_bytes(b"still mounted dmg")

        failure = build_pipeline.runtime_smoke.DmgDetachError(
            "synthetic normal and forced detach failure"
        )
        with self.mocked_merge_dependencies(
            fixture,
            monitored,
            mounted_runtime_side_effect=failure,
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError,
            "private backing candidate was retained",
        ):
            build_pipeline.execute_merge(
                self.source, self.developer, fixture["plan"]
            )

        self.assertFalse(fixture["output"].exists())
        self.assertEqual(1, len(candidates))
        self.assertEqual(b"still mounted dmg", candidates[0].read_bytes())
        self.assertEqual(
            0o700, stat.S_IMODE(os.lstat(str(candidates[0].parent)).st_mode)
        )
        shutil.rmtree(candidates[0].parent)

    def test_rejected_dmg_cleanup_refuses_replaced_inode(self):
        output = self.root / "FocusBrowser-replaced.dmg"
        output.write_bytes(b"created by pipeline")
        created = os.lstat(str(output))
        identity = (created.st_dev, created.st_ino)
        output.unlink()
        output.write_bytes(b"unrelated replacement")
        with self.assertRaisesRegex(build_pipeline.PipelineError, "changed DMG"):
            build_pipeline._unlink_created_dmg(output, identity)
        self.assertEqual(b"unrelated replacement", output.read_bytes())

    def test_recursive_reclamation_requires_explicit_flag(self):
        with self.assertRaisesRegex(build_pipeline.PipelineError, "allow-reclaim"):
            build_pipeline.execute_stage_arm(self.source, {}, False)

    def prepare_minimal_linkedit_recovery_execute_fixture(self):
        artifact_source = self.source / build_pipeline.STAGED_ARM_APP
        payload = artifact_source / "Contents/evidence.bin"
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(b"legacy invalid evidence\n")
        arm_out = self.source / build_pipeline.ARM_OUT
        if arm_out.exists():
            shutil.rmtree(arm_out)
        partial_root = self.source / build_pipeline.LINKEDIT_RECOVERY_PARTIAL
        final_root = self.source / build_pipeline.LINKEDIT_RECOVERY_ROOT
        profiles = build_pipeline.focus_macos.validate_gn_profiles()["profiles"]
        arm_args_text = profiles["arm64"]["args_gn"]
        artifact = {
            "relative_path": build_pipeline.STAGED_ARM_APP,
            "source": str(artifact_source),
            "archive_relative_path": (
                "artifacts/" + build_pipeline.STAGED_ARM_APP
            ),
            "kind": "tree",
            "sha256": build_pipeline.tree_digest(artifact_source),
            "bytes": build_pipeline.physical_size(artifact_source),
        }
        plan = {
            "partial_root": str(partial_root),
            "recovery_root": str(final_root),
            "artifacts": [artifact],
            "restore_arm_args": {
                "path": str(arm_out / "args.gn"),
                "sha256": hashlib.sha256(
                    arm_args_text.encode("utf-8")
                ).hexdigest(),
                "bytes": len(arm_args_text.encode("utf-8")),
            },
            "linkedit_strip_receipt": {"path": "fixture", "sha256": "a" * 64},
            "legacy_alignment": {"arm64": {}, "x86_64": {}},
            "signing_state": {"source": "post", "packaging": "post"},
            "preserve_x64_objects": {"incremental_relink": True},
            "required_followup_stages": ["build-arm64"],
        }
        return {
            "plan": plan,
            "artifact_source": artifact_source,
            "payload": payload,
            "partial_root": partial_root,
            "final_root": final_root,
            "arm_out": arm_out,
        }

    def test_linkedit_recovery_rehashes_moved_artifact_before_publication(self):
        fixture = self.prepare_minimal_linkedit_recovery_execute_fixture()
        real_publish = build_pipeline.prepare_source.atomic_publish_text
        moved_payload = (
            fixture["partial_root"]
            / fixture["plan"]["artifacts"][0]["archive_relative_path"]
            / "Contents/evidence.bin"
        )

        def publish_then_tamper(destination, text):
            result = real_publish(destination, text)
            moved_payload.write_bytes(b"concurrent mutation\n")
            return result

        with mock.patch.object(
            build_pipeline,
            "linkedit_recovery_plan",
            return_value=fixture["plan"],
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=60 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline.prepare_source,
            "atomic_publish_text",
            side_effect=publish_then_tamper,
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "changed before publication"
        ):
            build_pipeline.execute_linkedit_recovery(
                self.source, self.developer, fixture["plan"], True
            )

        self.assertFalse(fixture["final_root"].exists())
        self.assertTrue(fixture["partial_root"].is_dir())
        self.assertTrue(fixture["arm_out"].is_dir())
        self.assertFalse(fixture["artifact_source"].exists())
        self.assertEqual(
            b"concurrent mutation\n",
            moved_payload.read_bytes(),
        )

    def test_linkedit_recovery_rolls_back_interrupt_after_artifact_rename(self):
        fixture = self.prepare_minimal_linkedit_recovery_execute_fixture()
        untouched_source = self.source / build_pipeline.STAGE_RECEIPT
        untouched_source.write_bytes(b"untouched receipt evidence\n")
        fixture["plan"]["artifacts"].append(
            {
                "relative_path": build_pipeline.STAGE_RECEIPT,
                "source": str(untouched_source),
                "archive_relative_path": (
                    "artifacts/" + build_pipeline.STAGE_RECEIPT
                ),
                "kind": "file",
                "sha256": build_pipeline.sha256_file(untouched_source),
                "bytes": untouched_source.stat().st_size,
            }
        )
        real_replace = os.replace
        interrupted = False
        artifact = fixture["plan"]["artifacts"][0]
        archive_destination = (
            fixture["partial_root"] / artifact["archive_relative_path"]
        )

        def replace_then_interrupt(source, destination):
            nonlocal interrupted
            result = real_replace(source, destination)
            if (
                not interrupted
                and Path(source) == fixture["artifact_source"]
                and Path(destination) == archive_destination
            ):
                interrupted = True
                raise KeyboardInterrupt("synthetic post-artifact interrupt")
            return result

        with mock.patch.object(
            build_pipeline,
            "linkedit_recovery_plan",
            return_value=fixture["plan"],
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=60 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline.os, "replace", side_effect=replace_then_interrupt
        ), self.assertRaisesRegex(
            KeyboardInterrupt, "synthetic post-artifact interrupt"
        ):
            build_pipeline.execute_linkedit_recovery(
                self.source, self.developer, fixture["plan"], True
            )

        self.assertTrue(interrupted)
        self.assertFalse(fixture["final_root"].exists())
        self.assertFalse(fixture["partial_root"].exists())
        self.assertFalse(fixture["arm_out"].exists())
        self.assertEqual(
            b"legacy invalid evidence\n",
            fixture["payload"].read_bytes(),
        )
        self.assertEqual(b"untouched receipt evidence\n", untouched_source.read_bytes())

    def test_linkedit_recovery_rolls_back_interrupt_after_final_rename(self):
        fixture = self.prepare_minimal_linkedit_recovery_execute_fixture()
        real_replace = os.replace
        interrupted = False

        def replace_then_interrupt(source, destination):
            nonlocal interrupted
            result = real_replace(source, destination)
            if (
                not interrupted
                and Path(source) == fixture["partial_root"]
                and Path(destination) == fixture["final_root"]
            ):
                interrupted = True
                raise KeyboardInterrupt("synthetic post-publication interrupt")
            return result

        with mock.patch.object(
            build_pipeline,
            "linkedit_recovery_plan",
            return_value=fixture["plan"],
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=60 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline.os, "replace", side_effect=replace_then_interrupt
        ), self.assertRaisesRegex(
            KeyboardInterrupt, "synthetic post-publication interrupt"
        ):
            build_pipeline.execute_linkedit_recovery(
                self.source, self.developer, fixture["plan"], True
            )

        self.assertTrue(interrupted)
        self.assertFalse(fixture["final_root"].exists())
        self.assertFalse(fixture["partial_root"].exists())
        self.assertFalse(fixture["arm_out"].exists())
        self.assertEqual(
            b"legacy invalid evidence\n",
            fixture["payload"].read_bytes(),
        )

    def test_linkedit_recovery_reports_final_root_when_normalization_is_unsafe(self):
        fixture = self.prepare_minimal_linkedit_recovery_execute_fixture()
        real_replace = os.replace
        interrupted = False

        def replace_tamper_then_interrupt(source, destination):
            nonlocal interrupted
            result = real_replace(source, destination)
            if (
                not interrupted
                and Path(source) == fixture["partial_root"]
                and Path(destination) == fixture["final_root"]
            ):
                interrupted = True
                (fixture["final_root"] / "manifest.json").write_bytes(
                    b"unsafe replacement\n"
                )
                raise KeyboardInterrupt("synthetic unsafe publication")
            return result

        with mock.patch.object(
            build_pipeline,
            "linkedit_recovery_plan",
            return_value=fixture["plan"],
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=60 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline.os, "replace", side_effect=replace_tamper_then_interrupt
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError,
            re.escape(str(fixture["final_root"])),
        ):
            build_pipeline.execute_linkedit_recovery(
                self.source, self.developer, fixture["plan"], True
            )

        self.assertTrue(interrupted)
        self.assertTrue(fixture["final_root"].is_dir())
        self.assertFalse(fixture["partial_root"].exists())

    def test_linkedit_recovery_archives_invalid_evidence_and_preserves_x64_objects(self):
        arm_out = self.source / build_pipeline.ARM_OUT
        shutil.rmtree(arm_out)
        staged_app = self.make_app(
            (self.source / build_pipeline.STAGED_ARM_APP).parent, "arm64"
        )
        (staged_app / "Contents/MacOS/Focus Browser").write_bytes(
            self.macho64_bytes("arm64", stroff=0x104)
        )
        x64_out = self.source / build_pipeline.X64_OUT
        x64_app = self.make_app(x64_out, "x86_64")
        (x64_app / "Contents/MacOS/Focus Browser").write_bytes(
            self.macho64_bytes("x86_64", stroff=0x104)
        )
        profiles = build_pipeline.focus_macos.validate_gn_profiles()["profiles"]
        (x64_out / "args.gn").write_text(
            profiles["x64"]["args_gn"], encoding="utf-8"
        )
        obj_sentinel = x64_out / "obj/keep.o"
        obj_sentinel.parent.mkdir(parents=True)
        obj_sentinel.write_bytes(b"preserve object\n")
        for relative, body in (
            (build_pipeline.STAGE_RECEIPT, b"stage\n"),
            (build_pipeline.RECLAIM_RECEIPT, b"reclaim\n"),
            (
                build_pipeline.X64_OUT
                + "/"
                + build_pipeline.SLICE_RECEIPT_NAME,
                b"x64 receipt\n",
            ),
            (
                build_pipeline.SWIFTSHADER_DISABLED_SIGNING_RECEIPT,
                b"swift receipt\n",
            ),
        ):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        artifact_paths = (
            build_pipeline.STAGED_ARM_APP,
            build_pipeline.STAGE_RECEIPT,
            build_pipeline.RECLAIM_RECEIPT,
            build_pipeline.X64_OUT + "/" + build_pipeline.APP_NAME,
            build_pipeline.X64_OUT
            + "/"
            + build_pipeline.SLICE_RECEIPT_NAME,
            build_pipeline.SWIFTSHADER_DISABLED_SIGNING_RECEIPT,
        )
        contracts = {}
        for relative in artifact_paths:
            path = self.source / relative
            if path.is_dir():
                contracts[relative] = {
                    "kind": "tree",
                    "sha256": build_pipeline.tree_digest(path),
                }
            else:
                contracts[relative] = {
                    "kind": "file",
                    "sha256": build_pipeline.sha256_file(path),
                }
        linkedit_receipt = self.write_json(
            self.source / build_pipeline.XCODE27_LINKEDIT_STRIP_RECEIPT,
            {"fixture": True},
        )
        with mock.patch.object(
            build_pipeline,
            "LINKEDIT_RECOVERY_LEGACY_ARTIFACTS",
            contracts,
        ), mock.patch.object(
            build_pipeline, "acquisition_contract"
        ), mock.patch.object(
            build_pipeline, "tool_receipt_contract"
        ), mock.patch.object(
            build_pipeline, "preparation_contract"
        ), mock.patch.object(
            build_pipeline,
            "xcode27_linkedit_strip_receipt_contract",
            return_value=(linkedit_receipt, {"fixture": True}),
        ), mock.patch.object(
            build_pipeline,
            "_adhoc_runtime_signing_paths",
            return_value=({}, {}),
        ), mock.patch.object(
            build_pipeline,
            "_adhoc_runtime_signing_set_state",
            return_value="post",
        ), mock.patch.object(
            build_pipeline, "require_free", return_value=60 * build_pipeline.GIB
        ):
            plan = build_pipeline.linkedit_recovery_plan(
                self.source, self.developer
            )
            with self.assertRaisesRegex(
                build_pipeline.PipelineError, "allow-recovery-move"
            ):
                build_pipeline.execute_linkedit_recovery(
                    self.source, self.developer, plan, False
                )
            report = build_pipeline.execute_linkedit_recovery(
                self.source, self.developer, plan, True
            )
        self.assertFalse(plan["postprocess_existing_binaries"])
        self.assertGreater(
            len(plan["legacy_alignment"]["arm64"]["violations"]), 0
        )
        self.assertGreater(
            len(plan["legacy_alignment"]["x86_64"]["violations"]), 0
        )
        self.assertTrue((arm_out / "args.gn").is_file())
        self.assertEqual(
            build_pipeline.SWIFTSHADER_DISABLED_ARGS_SHA256["arm64"],
            build_pipeline.sha256_file(arm_out / "args.gn"),
        )
        self.assertEqual(b"preserve object\n", obj_sentinel.read_bytes())
        recovery_root = Path(report["recovery_root"])
        self.assertTrue((recovery_root / "manifest.json").is_file())
        for relative in artifact_paths:
            self.assertFalse((self.source / relative).exists())
            self.assertTrue((recovery_root / "artifacts" / relative).exists())
        self.assertEqual(
            [
                "build-arm64",
                "stage-arm64",
                "build-x64",
                "apply-swiftshader-disabled-signing-compat",
                "apply-adhoc-runtime-signing-compat",
                "merge-sign-package",
            ],
            report["required_followup_stages"],
        )

    def test_forbidden_privileged_build_program_is_rejected(self):
        with self.assertRaisesRegex(build_pipeline.PipelineError, "forbidden"):
            build_pipeline.run_monitored(
                ["sudo", "true"], self.source, {}, poll_seconds=0
            )

    def test_monitor_checks_soft_floor_before_spawning(self):
        with mock.patch.object(
            build_pipeline, "free_bytes", return_value=34 * build_pipeline.GIB
        ), mock.patch.object(build_pipeline.subprocess, "Popen") as popen, self.assertRaisesRegex(
            build_pipeline.PipelineError, "pre-command"
        ):
            build_pipeline.run_monitored(
                ["/usr/bin/true"], self.source, {}, poll_seconds=0
            )
        popen.assert_not_called()

    def test_monitor_kills_descendants_when_the_group_leader_exits(self):
        identity = self.root / "leader-exit-process-group.txt"
        script = (
            "import os,signal,time; "
            "signal.signal(signal.SIGINT,lambda *_:os._exit(130)); "
            "child=os.fork(); "
            "child == 0 and time.sleep(30); "
            "open({!r},'w').write(str(os.getpgrp())+' '+str(child)); "
            "os._exit(0)"
        ).format(str(identity))
        with mock.patch.object(
            build_pipeline, "require_free", return_value=100 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline, "free_bytes", return_value=100 * build_pipeline.GIB
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "descendants remained"
        ):
            build_pipeline.run_monitored(
                ["/usr/bin/python3", "-c", script],
                self.source,
                {},
                poll_seconds=0.01,
            )
        self.assertTrue(identity.is_file())
        pgid = int(identity.read_text(encoding="utf-8").split()[0])
        self.assertFalse(build_pipeline._process_group_exists(pgid))

    def test_monitor_keyboard_interrupt_stops_the_entire_process_group(self):
        identity = self.root / "interrupted-process-group.txt"
        script = (
            "import os,signal,time; "
            "signal.signal(signal.SIGINT,lambda *_:os._exit(130)); "
            "child=os.fork(); "
            "open({!r},'w').write(str(os.getpgrp())+' '+str(child)); "
            "time.sleep(30)"
        ).format(str(identity))
        real_sleep = time.sleep
        sleep_calls = 0

        def interrupt_first_poll(seconds):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls == 1:
                real_sleep(0.15)
                raise KeyboardInterrupt()
            real_sleep(min(seconds, 0.01))

        with mock.patch.object(
            build_pipeline, "require_free", return_value=100 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline, "free_bytes", return_value=100 * build_pipeline.GIB
        ), mock.patch.object(
            build_pipeline.time, "sleep", side_effect=interrupt_first_poll
        ), self.assertRaises(KeyboardInterrupt):
            build_pipeline.run_monitored(
                ["/usr/bin/python3", "-c", script],
                self.source,
                {},
                poll_seconds=0.01,
            )
        self.assertTrue(identity.is_file())
        pgid = int(identity.read_text(encoding="utf-8").split()[0])
        self.assertFalse(build_pipeline._process_group_exists(pgid))

    def test_bounded_probe_kills_a_descendant_holding_its_stdout_pipe(self):
        identity = self.root / "probe-process-group.txt"
        script = (
            "import os,signal,time; "
            "signal.signal(signal.SIGINT,lambda *_:os._exit(130)); "
            "child=os.fork(); "
            "open({!r},'w').write(str(os.getpgrp())+' '+str(child)); "
            "os._exit(0) if child else time.sleep(30)"
        ).format(str(identity))
        process = subprocess.Popen(
            ["/usr/bin/python3", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "timed out"
        ):
            build_pipeline._collect_bounded_probe_output(
                process, timeout_seconds=0.2
            )
        self.assertTrue(identity.is_file())
        pgid = int(identity.read_text(encoding="utf-8").split()[0])
        self.assertFalse(build_pipeline._process_group_exists(pgid))

    def test_reclaim_receipt_is_false_if_arm_output_reappears(self):
        self.prepare_merge_fixture()
        arm_out = self.source / build_pipeline.ARM_OUT
        arm_out.mkdir(parents=True)
        with self.assertRaisesRegex(build_pipeline.PipelineError, "still exists"):
            build_pipeline.reclaim_contract(self.source)

    def test_json_receipts_reject_duplicate_keys(self):
        receipt = self.root / "duplicate.json"
        receipt.write_text('{"schema": 1, "schema": 2}\n', encoding="utf-8")
        with self.assertRaisesRegex(build_pipeline.PipelineError, "duplicate"):
            build_pipeline.load_json(receipt, "fixture receipt")

    def test_dmg_output_rejects_symlinked_ancestor(self):
        real = self.root / "real-output"
        real.mkdir()
        alias = self.root / "output-alias"
        alias.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(build_pipeline.PipelineError, "symlink"):
            build_pipeline.resolve_absent_dmg(alias / "FocusBrowser.dmg")

    def test_cli_defaults_to_dry_run(self):
        args = build_pipeline.parser().parse_args(
            [
                "build-arm64",
                "--source-root",
                str(self.source),
                "--developer-dir",
                "/Xcode.app/Contents/Developer",
            ]
        )
        self.assertFalse(args.execute)
        self.assertEqual("build-arm64", args.command)

    def test_linkedit_recovery_cli_has_separate_move_confirmation(self):
        base = [
            "prepare-xcode27-linkedit-recovery",
            "--source-root",
            str(self.source),
            "--developer-dir",
            str(self.developer),
        ]
        dry = build_pipeline.parser().parse_args(base)
        self.assertFalse(dry.execute)
        self.assertFalse(dry.allow_recovery_move)
        execute = build_pipeline.parser().parse_args(
            base + ["--execute", "--allow-recovery-move"]
        )
        self.assertTrue(execute.execute)
        self.assertTrue(execute.allow_recovery_move)

    @unittest.skipUnless(
        sys.platform == "darwin",
        "requires the real macOS Data volume fixture",
    )
    def test_volume_identity_resolves_users_path_to_apfs_data_volume(self):
        physical_home = Path.home().resolve(strict=True)
        identity = build_pipeline._volume_identity(physical_home)
        self.assertEqual("/System/Volumes/Data", identity["mount_point"])
        self.assertRegex(identity["device_node"], r"^/dev/[A-Za-z0-9._-]+$")
        self.assertRegex(
            identity["volume_uuid"],
            r"^[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}$",
        )
        self.assertEqual(
            os.stat(physical_home).st_dev,
            os.stat(identity["mount_point"]).st_dev,
        )

    def test_volume_identity_rejects_malformed_df_and_diskutil_reports(self):
        valid_df = b"Filesystem 512-blocks Used Available Capacity Mounted on\n/dev/test 1 1 1 1% /\n"
        valid_plist = {
            "DeviceNode": "/dev/test",
            "FilesystemType": "apfs",
            "MountPoint": str(self.root),
            "VolumeUUID": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
        }
        malformed_df = (
            b"",
            b"localized header\n/dev/test 1 1 1 1% /\n",
            b"Filesystem x\n/dev/test 1\n",
            b"Filesystem x\n/dev/test 1 1 1 1% /\n/dev/other 1 1 1 1% /x\n",
            b"Filesystem x\nnot-a-device 1 1 1 1% /\n",
            b"\xff\n",
        )
        for body in malformed_df:
            with self.subTest(df=body), mock.patch.object(
                build_pipeline, "_run_bounded_output", return_value=body
            ), self.assertRaises(build_pipeline.PipelineError):
                build_pipeline._volume_identity(self.source)
        invalid_plists = (
            plistlib.dumps(["not", "a", "dict"]),
            plistlib.dumps({**valid_plist, "DeviceNode": "/dev/other"}),
            plistlib.dumps({**valid_plist, "FilesystemType": "hfs"}),
            plistlib.dumps({**valid_plist, "MountPoint": "relative"}),
            plistlib.dumps({**valid_plist, "VolumeUUID": "bad"}),
            b"not a plist",
        )
        for body in invalid_plists:
            with self.subTest(plist=body[:20]), mock.patch.object(
                build_pipeline,
                "_run_bounded_output",
                side_effect=(valid_df, body),
            ), self.assertRaises(build_pipeline.PipelineError):
                build_pipeline._volume_identity(self.source)

    def test_volume_identity_rejects_cross_device_and_leaf_symlink(self):
        valid_df = b"Filesystem 512-blocks Used Available Capacity Mounted on\n/dev/test 1 1 1 1% /\n"
        valid_plist = plistlib.dumps(
            {
                "DeviceNode": "/dev/test",
                "FilesystemType": "apfs",
                "MountPoint": str(self.root),
                "VolumeUUID": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
            }
        )
        before = os.lstat(self.source)
        wrong_mount = mock.Mock(
            st_mode=stat.S_IFDIR | 0o755,
            st_dev=before.st_dev + 1,
            st_ino=before.st_ino + 1,
        )
        with mock.patch.object(
            build_pipeline, "_run_bounded_output", side_effect=(valid_df, valid_plist)
        ), mock.patch.object(
            build_pipeline.os, "lstat", side_effect=(before, wrong_mount, before)
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "do not match"):
            build_pipeline._volume_identity(self.source)
        alias = self.root / "volume-alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(build_pipeline.PipelineError, "existing"):
            build_pipeline._volume_identity(alias)

    def test_bounded_command_rejects_output_cap_and_timeout(self):
        with self.assertRaisesRegex(build_pipeline.PipelineError, "exceeded"):
            build_pipeline._run_bounded_output(
                ["/usr/bin/python3", "-c", "import os; os.write(1, b'x' * 4097)"],
                4096,
                5,
                "bounded fixture",
            )
        started = time.monotonic()
        with self.assertRaisesRegex(build_pipeline.PipelineError, "timed out"):
            build_pipeline._run_bounded_output(
                ["/usr/bin/python3", "-c", "import time; time.sleep(30)"],
                4096,
                0.2,
                "timeout fixture",
            )
        self.assertLess(time.monotonic() - started, 3)

    def test_home_alias_requires_root_owned_direct_users_symlink(self):
        fake = mock.Mock(
            st_mode=stat.S_IFLNK | 0o755,
            st_uid=501,
            st_gid=20,
            st_dev=1,
            st_ino=2,
        )
        with mock.patch.object(build_pipeline.os, "lstat", return_value=fake), self.assertRaisesRegex(
            build_pipeline.PipelineError, "root-owned"
        ):
            build_pipeline._home_alias_value(
                Path("/Users/legacy/work/src"),
                Path("/Users/legacy/Xcode.app/Contents/Developer"),
                Path("/Users/legacy"),
                Path("/Users/legacy/work"),
            )

    def test_home_alias_rejects_relative_or_swapped_target(self):
        fake = mock.Mock(
            st_mode=stat.S_IFLNK | 0o755,
            st_uid=0,
            st_gid=80,
            st_dev=1,
            st_ino=2,
        )
        with mock.patch.object(
            build_pipeline.os, "lstat", return_value=fake
        ), mock.patch.object(
            build_pipeline.os, "readlink", return_value="../attacker"
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "target must be absolute"
        ):
            build_pipeline._home_alias_value(
                Path("/Users/legacy/work/src"),
                Path("/Users/legacy/Xcode.app/Contents/Developer"),
                Path("/Users/legacy"),
                Path("/Users/legacy/work"),
            )

    def test_home_alias_requires_same_workspace_and_xcode_inode(self):
        with mock.patch.object(
            build_pipeline,
            "_path_identity",
            side_effect=(
                {"device": 1, "inode": 10},
                {"device": 1, "inode": 11},
            ),
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "same inode"):
            build_pipeline._same_inode_mapping(
                Path("/Users/legacy/work"),
                Path("/Users/current/work"),
                "workspace",
            )

    def test_resolve_source_preserves_only_a_recorded_logical_alias(self):
        physical = self.root / "physical"
        physical_source = physical / "src"
        physical_source.mkdir(parents=True)
        logical = self.root / "logical"
        logical.symlink_to(physical, target_is_directory=True)
        logical_source = logical / "src"
        with mock.patch.object(
            build_pipeline.focus_macos,
            "resolve_source_root",
            return_value=(physical_source, build_pipeline.focus_macos.PINNED_CHROMIUM_VERSION),
        ):
            with self.assertRaisesRegex(
                build_pipeline.PipelineError, "explicit home-alias"
            ):
                build_pipeline.resolve_source(logical_source)
            with self.assertRaisesRegex(
                build_pipeline.PipelineError, "receipt is missing"
            ):
                build_pipeline.resolve_source(
                    logical_source, allow_recorded_home_alias=True
                )
            receipt = physical_source / build_pipeline.HOME_ALIAS_RECEIPT
            receipt.parent.mkdir(parents=True)
            receipt.write_text("{}\n", encoding="utf-8")
            self.assertEqual(
                logical_source,
                build_pipeline.resolve_source(
                    logical_source, allow_recorded_home_alias=True
                ),
            )

    def test_home_alias_receipt_detects_identity_or_legacy_hash_change(self):
        receipt_path = self.source / build_pipeline.HOME_ALIAS_RECEIPT
        def mapping(name, inode, device=11):
            return {
                "logical": "/Users/legacy/{}".format(name),
                "physical": "/Users/current/{}".format(name),
                "identity": {
                    "volume_uuid": "A" * 8 + "-AAAA-AAAA-AAAA-" + "A" * 12,
                    "device": device,
                    "inode": inode,
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                    "mode": 0o755,
                },
            }

        value = {
            "schema": build_pipeline.HOME_ALIAS_RECEIPT_SCHEMA,
            "logical_home": "/Users/legacy",
            "physical_home": "/Users/current",
            "volume": {
                "filesystem": "apfs",
                "volume_uuid": "A" * 8 + "-AAAA-AAAA-AAAA-" + "A" * 12,
            },
            "alias": {
                "path": "/Users/legacy",
                "target": "/Users/current",
                "device": 11,
                "inode": 100,
                "uid": 0,
                "gid": 80,
                "mode": 0o755,
                "root_owned": True,
                "absolute_exact_target": True,
                "target_identity": {
                    "volume_uuid": "A" * 8 + "-AAAA-AAAA-AAAA-" + "A" * 12,
                    "device": 11,
                    "inode": 200,
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                    "mode": 0o755,
                },
            },
            "mappings": {
                "workspace": mapping("work", 201),
                "source": mapping("work/src", 202),
                "developer": mapping("Xcode/Developer", 203),
                "repo": mapping("repo", 204),
            },
            "legacy_receipts": {"preparation": {"sha256": "a" * 64}},
            "legacy_receipts_rewritten": False,
            "gn_gen_executed": False,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
            "offline": True,
            "network_operations": 0,
        }
        self.write_json(receipt_path, value)
        renumbered = json.loads(json.dumps(value))
        renumbered["alias"]["device"] = 99
        renumbered["alias"]["target_identity"]["device"] = 99
        for item in renumbered["mappings"].values():
            item["identity"]["device"] = 99
        with mock.patch.object(
            build_pipeline, "_home_alias_value", return_value=renumbered
        ):
            path, observed = build_pipeline.home_alias_receipt_contract(
                self.source, self.developer
            )
        self.assertEqual(receipt_path, path)
        self.assertEqual(value, observed)
        changed = json.loads(json.dumps(renumbered))
        changed["mappings"]["source"]["identity"]["inode"] += 1
        with mock.patch.object(
            build_pipeline, "_home_alias_value", return_value=changed
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "chain changed"):
            build_pipeline.home_alias_receipt_contract(self.source, self.developer)

    def test_changed_path_scan_accepts_logical_alias_and_rejects_physical_leak(self):
        out = self.root / "scan-out"
        out.mkdir()
        logical = Path("/Users/legacy")
        physical = Path("/Users/current")
        safe = out / "safe.bin"
        safe.write_bytes(b"prefix " + str(logical).encode() + b" suffix")
        report = build_pipeline.changed_path_scan(
            out, 0, logical, physical
        )
        self.assertEqual(1, report["logical_home_occurrences"])
        self.assertEqual(0, report["physical_home_occurrences"])
        leaked = out / "leaked.bin"
        leaked.write_bytes(
            b"x" * (1024 * 1024 - 5) + str(physical).encode() + b" end"
        )
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "physical home leaked"
        ):
            build_pipeline.changed_path_scan(out, 0, logical, physical)

    def test_changed_path_scan_rejects_physical_symlink_target(self):
        out = self.root / "scan-symlink"
        out.mkdir()
        (out / "escape").symlink_to("/Users/current/private")
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "absolute symlink"
        ):
            build_pipeline.changed_path_scan(
                out, 0, Path("/Users/legacy"), Path("/Users/current")
            )

    def test_changed_path_scan_checks_old_files_and_rejects_special_nodes(self):
        out = self.root / "scan-old-leak"
        out.mkdir()
        leaked = out / "preexisting.bin"
        leaked.write_bytes(b"/Users/current/private")
        old_ns = 1_000_000_000
        os.utime(leaked, ns=(old_ns, old_ns))
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "physical home leaked"
        ):
            build_pipeline.changed_path_scan(
                out,
                time.time_ns() + 10_000_000_000,
                Path("/Users/legacy"),
                Path("/Users/current"),
            )

        leaked.unlink()
        fifo = out / "unsafe.fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "special file"
        ):
            build_pipeline.changed_path_scan(
                out,
                0,
                Path("/Users/legacy"),
                Path("/Users/current"),
            )

    def test_execution_evidence_is_workspace_bound_immutable_and_hash_sensitive(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        evidence = self.write_json(workspace / "evidence.json", {"schema": 1})
        evidence.chmod(0o444)
        volume_uuid = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
        alias = {
            "volume": {"volume_uuid": volume_uuid},
            "mappings": {
                "workspace": {
                    "logical": str(workspace),
                    "physical": str(workspace),
                }
            },
        }
        link = {
            "path": str(evidence),
            "sha256": build_pipeline.sha256_file(evidence),
        }
        with mock.patch.object(
            build_pipeline,
            "_volume_identity",
            return_value={"volume_uuid": volume_uuid},
        ):
            path, value = build_pipeline._linked_execution_evidence(
                link, alias, "fixture evidence"
            )
        self.assertEqual(evidence, path)
        self.assertEqual({"schema": 1}, value)
        evidence.chmod(0o644)
        with mock.patch.object(
            build_pipeline,
            "_volume_identity",
            return_value={"volume_uuid": volume_uuid},
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "mode is unsafe"):
            build_pipeline._linked_execution_evidence(
                link, alias, "fixture evidence"
            )
        evidence.write_text('{"schema": 2}\n', encoding="utf-8")
        evidence.chmod(0o444)
        with mock.patch.object(
            build_pipeline,
            "_volume_identity",
            return_value={"volume_uuid": volume_uuid},
        ), self.assertRaisesRegex(build_pipeline.PipelineError, "hash changed"):
            build_pipeline._linked_execution_evidence(
                link, alias, "fixture evidence"
            )
        outside = self.write_json(self.root / "outside.json", {"schema": 1})
        outside.chmod(0o444)
        with self.assertRaisesRegex(build_pipeline.PipelineError, "outside"):
            build_pipeline._execution_evidence_path(
                outside, alias, "outside evidence"
            )

    def test_onboarding_alias_root_receipt_is_a_required_exact_link(self):
        receipt_path = (
            self.source / build_pipeline.onboarding_alias_compat.RECEIPT_RELATIVE
        )
        workspace = self.root / "physical-workspace"
        trial_path = (
            workspace
            / "work/logs"
            / build_pipeline.onboarding_alias_compat.TRIAL_REPORT_BASENAME
        )
        failure_path = (
            workspace
            / "work/logs"
            / build_pipeline.onboarding_alias_compat.FAILURE_REPORT_BASENAME
        )
        receipt = {
            "trial_evidence": {
                "trial_report": {"path": str(trial_path)},
                "failure_report": {"path": str(failure_path)},
            }
        }
        self.write_json(receipt_path, receipt)
        expected = {
            "path": str(receipt_path),
            "bytes": receipt_path.stat().st_size,
            "sha256": build_pipeline.sha256_file(receipt_path),
            "value": receipt,
        }
        with mock.patch.object(
            build_pipeline.onboarding_alias_compat,
            "validate_home_alias_receipt",
            return_value={
                "mappings": {"workspace": {"physical": str(workspace)}}
            },
        ), mock.patch.object(
            build_pipeline.onboarding_alias_compat,
            "receipt_contract",
            return_value=expected,
        ) as contract:
            path, value, link = (
                build_pipeline.onboarding_alias_root_receipt_contract(
                    self.source
                )
            )
        self.assertEqual(receipt_path, path)
        self.assertEqual(receipt, value)
        self.assertEqual(
            {
                "path": str(receipt_path),
                "bytes": receipt_path.stat().st_size,
                "sha256": build_pipeline.sha256_file(receipt_path),
            },
            link,
        )
        contract.assert_called_once_with(
            self.source,
            trial_path=trial_path,
            failure_path=failure_path,
        )

        bad = dict(expected)
        bad["sha256"] = "0" * 64
        with mock.patch.object(
            build_pipeline.onboarding_alias_compat,
            "validate_home_alias_receipt",
            return_value={
                "mappings": {"workspace": {"physical": str(workspace)}}
            },
        ), mock.patch.object(
            build_pipeline.onboarding_alias_compat,
            "receipt_contract",
            return_value=bad,
        ), self.assertRaisesRegex(
            build_pipeline.PipelineError, "contract mismatch"
        ):
            build_pipeline.onboarding_alias_root_receipt_contract(self.source)

    def test_onboarding_alias_root_wrapper_revalidates_real_receipt_contract(self):
        # Reuse the owning module's full graph/trial/HomeAlias fixture, but do
        # not mock either receipt validator across this integration boundary.
        from test_onboarding_alias_compat import OnboardingAliasCompatTests

        fixture = OnboardingAliasCompatTests(
            methodName="test_execute_pre_to_post_and_verify_immutable_receipt"
        )
        fixture.setUp()
        try:
            fixture.execute()
            path, value, link = (
                build_pipeline.onboarding_alias_root_receipt_contract(
                    fixture.source
                )
            )
            self.assertEqual(fixture.receipt, path)
            self.assertEqual(
                str(fixture.trial_path),
                value["trial_evidence"]["trial_report"]["path"],
            )
            self.assertEqual(
                str(fixture.failure_path),
                value["trial_evidence"]["failure_report"]["path"],
            )
            self.assertEqual(
                {
                    "path": str(fixture.receipt),
                    "bytes": fixture.receipt.stat().st_size,
                    "sha256": build_pipeline.sha256_file(fixture.receipt),
                },
                link,
            )
        finally:
            fixture.tearDown()

    def test_resume_execution_basename_rejects_failed_arm_resume_two(self):
        accepted = Path(
            "build-arm64-resume3-home-alias-20260730T170000MSK.execution.json"
        )
        self.assertEqual(
            accepted.name + ".part",
            build_pipeline._resume_execution_initial_basename(
                accepted, "arm64"
            ),
        )
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "authorized fresh run"
        ):
            build_pipeline._resume_execution_initial_basename(
                Path(
                    "build-arm64-resume2-home-alias-20260730T1442MSK.execution.json"
                ),
                "arm64",
            )

    def test_live_process_validator_requires_literal_ps_newline_escape(self):
        out = self.source / build_pipeline.ARM_OUT
        out.mkdir(parents=True, exist_ok=True)
        autoninja_py = self.depot / "autoninja.py"
        autoninja_py.write_bytes(b"fixture autoninja\n")
        pinned_python = (
            self.depot
            / build_pipeline.PACKAGING_PYTHON_RELDIR
            / "python3.11"
        )
        pinned_python.parent.mkdir(parents=True)
        pinned_python.write_bytes(b"fixture python\n")
        stdout = self.root / "resume.stdout.log"
        stdout.write_bytes(b"completed build output\n")
        initial = self.root / "resume.execution.json.part"
        initial.write_bytes(b'{"fixture":true}\n')
        initial.chmod(0o444)
        initial_stat = initial.stat()
        stdout_stat = stdout.stat()
        observed_at = max(initial_stat.st_mtime_ns, stdout_stat.st_mtime_ns) + 1
        environment = {
            "HOME": str(self.root),
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
            "DEVELOPER_DIR": str(self.developer),
            "PATH": str(self.depot) + ":" + build_pipeline.SYSTEM_PATH,
            "DEPOT_TOOLS_UPDATE": "0",
            "DEPOT_TOOLS_METRICS": "0",
            "GCLIENT_FILE": str(self.checkout / ".gclient"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "NINJA_SUMMARIZE_BUILD": "1",
        }
        argv = [
            str(self.depot / "autoninja"),
            "-j8",
            "-C",
            build_pipeline.ARM_OUT,
            "chrome",
            "chrome/installer/mac:copies",
        ]
        record = {
            "logical": {"source": str(self.source), "out": str(out)},
            "process": {
                "pid": 5000,
                "pgid": 5000,
                "argv": argv,
                "environment": environment,
                "started_at_ns": observed_at - 1,
                "observed_live_at_ns": observed_at,
            },
            "completion": {"ended_at_ns": observed_at + 1},
            "stdout_log": {
                "path": str(stdout),
                "inode": stdout_stat.st_ino,
                "birth_time_ns": stdout_stat.st_mtime_ns,
            },
        }
        alias = {
            "volume": {"volume_uuid": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"},
            "mappings": {
                "workspace": {
                    "logical": str(self.root),
                    "physical": str(self.root),
                }
            },
        }
        ninja = dict(self.ninja_report)
        ninja["sha256"] = build_pipeline.sha256_file(self.ninja)
        environment_order = (
            "HOME",
            "LANG",
            "LC_ALL",
            "TZ",
            "DEVELOPER_DIR",
            "PATH",
            "DEPOT_TOOLS_UPDATE",
            "DEPOT_TOOLS_METRICS",
            "GCLIENT_FILE",
            "PYTHONDONTWRITEBYTECODE",
            "NINJA_SUMMARIZE_BUILD",
        )
        environment_command = " ".join(
            "{}={}".format(name, environment[name]) for name in environment_order
        )
        declared_args = " ".join(argv)
        trailing_args = " ".join(argv[1:])
        pinned_command = "{} -d stats {}".format(self.ninja, trailing_args)
        commands = {
            "pipeline_shell_group_leader": (
                "/bin/zsh -lc set -o pipefail\\012/usr/bin/env -i {} {} 2>&1 | "
                "/usr/bin/tee -a {}"
            ).format(environment_command, declared_args, stdout),
            "autoninja_shell": "bash {} {}".format(argv[0], trailing_args),
            "stdout_tee": "/usr/bin/tee -a {}".format(stdout),
            "depot_python_launcher_shell": "bash {} {} {}".format(
                self.depot / "python-bin/python3", autoninja_py, trailing_args
            ),
            "autoninja_python": "{} {} {}".format(
                self.depot
                / "python-bin"
                / ".."
                / build_pipeline.PACKAGING_PYTHON_RELDIR
                / "python3",
                autoninja_py,
                trailing_args,
            ),
            "pinned_ninja": pinned_command,
            "ninja_caffeinate": "caffeinate {}".format(pinned_command),
        }
        executables = {
            "pipeline_shell_group_leader": Path("/bin/zsh"),
            "autoninja_shell": Path("/bin/bash"),
            "stdout_tee": Path("/usr/bin/tee"),
            "depot_python_launcher_shell": Path("/bin/bash"),
            "autoninja_python": pinned_python,
            "pinned_ninja": self.ninja,
            "ninja_caffeinate": Path("/usr/bin/caffeinate"),
        }
        parents = {
            "pipeline_shell_group_leader": 99,
            "autoninja_shell": 5000,
            "stdout_tee": 5000,
            "depot_python_launcher_shell": 5001,
            "autoninja_python": 5003,
            "pinned_ninja": 5004,
            "ninja_caffeinate": 5005,
        }
        pids = {
            "pipeline_shell_group_leader": 5000,
            "autoninja_shell": 5001,
            "stdout_tee": 5002,
            "depot_python_launcher_shell": 5003,
            "autoninja_python": 5004,
            "pinned_ninja": 5005,
            "ninja_caffeinate": 5006,
        }
        allowlisted = dict(environment)
        allowlisted.pop("PATH")
        allowlisted["PWD"] = str(self.source)
        members = []
        for role in pids:
            executable = executables[role]
            member = {
                "role": role,
                "pid": pids[role],
                "ppid": parents[role],
                "pgid": 5000,
                "ps_command": commands[role],
                "cwd_physical": str(out if role == "pinned_ninja" else self.source),
                "executable": str(executable),
                "executable_sha256": build_pipeline.sha256_file(executable),
                "started_at_local_second": "2026-07-30 14:42:00",
            }
            if role == "autoninja_shell":
                member.update(
                    {
                        "script": str(self.depot / "autoninja"),
                        "script_sha256": build_pipeline.sha256_file(
                            self.depot / "autoninja"
                        ),
                    }
                )
            if role in {"autoninja_python", "pinned_ninja"}:
                member["allowlisted_environment"] = allowlisted
            if role == "pinned_ninja":
                member["executable_bytes"] = self.ninja.stat().st_size
            members.append(member)
        observation = {
            "schema": 1,
            "kind": "focus-macos-alias-raw-ninja-live-process-chain-observation",
            "observed_at_ns": observed_at,
            "existing_execution_part": {
                "path": str(initial),
                "bytes": initial_stat.st_size,
                "mtime_ns": initial_stat.st_mtime_ns,
                "sha256": build_pipeline.sha256_file(initial),
                "device": initial_stat.st_dev,
                "inode": initial_stat.st_ino,
                "uid": initial_stat.st_uid,
                "gid": initial_stat.st_gid,
                "mode": stat.S_IMODE(initial_stat.st_mode),
            },
            "process_group": {"pgid": 5000, "members": members},
            "stdout_log_live_snapshot": {
                "path": str(stdout),
                "bytes": stdout_stat.st_size,
                "mtime_ns": stdout_stat.st_mtime_ns,
                "sha256": build_pipeline.sha256_file(stdout),
                "device": stdout_stat.st_dev,
                "inode": stdout_stat.st_ino,
                "uid": stdout_stat.st_uid,
                "gid": stdout_stat.st_gid,
                "mode": stat.S_IMODE(stdout_stat.st_mode),
            },
        }
        build_pipeline._validate_live_process_observation(
            observation,
            initial,
            build_pipeline.sha256_file(initial),
            record,
            alias,
            ninja,
        )
        leader = next(
            member
            for member in members
            if member["role"] == "pipeline_shell_group_leader"
        )
        self.assertIn("\\012", leader["ps_command"])
        self.assertNotIn("\n", leader["ps_command"])
        leader["ps_command"] = leader["ps_command"].replace("\\012", "\n")
        with self.assertRaisesRegex(
            build_pipeline.PipelineError, "leader identity mismatch"
        ):
            build_pipeline._validate_live_process_observation(
                observation,
                initial,
                build_pipeline.sha256_file(initial),
                record,
                alias,
                ninja,
            )

    def test_no_work_probe_rejects_pending_or_failed_ninja_graph(self):
        ninja = dict(self.ninja_report)
        invalid = (
            (b"[1/1] pending edge\n", "not complete"),
            (b"warning\nninja: no work to do.\n", "not complete"),
            (
                b"ninja: Entering directory `out/forged'\n"
                b"ninja: no work to do.\n",
                "not complete",
            ),
            (
                b"ninja: Entering directory `out/FocusMacArm64'\n"
                b"ninja: Entering directory `out/FocusMacArm64'\n"
                b"ninja: no work to do.\n",
                "not complete",
            ),
            (b"\xffninja: no work to do.\n", "not UTF-8"),
        )
        for payload, message in invalid:
            with self.subTest(payload=payload):
                process = mock.Mock(returncode=0)
                with mock.patch.object(
                    build_pipeline, "safe_environment", return_value={}
                ), mock.patch.object(
                    build_pipeline.subprocess, "Popen", return_value=process
                ), mock.patch.object(
                    build_pipeline,
                    "_collect_bounded_probe_output",
                    return_value=payload,
                ), self.assertRaisesRegex(build_pipeline.PipelineError, message):
                    build_pipeline._ninja_no_work_contract(
                        self.source,
                        self.developer,
                        build_pipeline.ARM_OUT,
                        ninja,
                    )
        process = mock.Mock(returncode=0)
        valid = (
            b"ninja: Entering directory `out/FocusMacArm64'\n"
            b"ninja: no work to do.\n"
        )
        with mock.patch.object(
            build_pipeline, "safe_environment", return_value={}
        ), mock.patch.object(
            build_pipeline.subprocess, "Popen", return_value=process
        ), mock.patch.object(
            build_pipeline, "_collect_bounded_probe_output", return_value=valid
        ):
            report = build_pipeline._ninja_no_work_contract(
                self.source, self.developer, build_pipeline.ARM_OUT, ninja
            )
        self.assertTrue(report["no_work"])

    def test_downstream_no_work_rechecks_authorized_history_after_probe(self):
        self.write_json(
            self.source / build_pipeline.HOME_ALIAS_RECEIPT,
            {"fixture": True},
        )
        out = self.source / build_pipeline.X64_OUT
        out.mkdir(parents=True, exist_ok=True)
        authorized = {"ninja_log": {}, "ninja_deps": {}}
        context = mock.Mock()
        with mock.patch.object(
            build_pipeline,
            "slice_receipt_contract",
            return_value=(self.root / "slice.json", {"schema": 2}),
        ), mock.patch.object(
            build_pipeline, "_ninja_history_exact_contract"
        ) as history, mock.patch.object(
            build_pipeline,
            "_ninja_no_work_contract",
            return_value={"no_work": True},
        ), mock.patch.object(
            build_pipeline, "ninja_contract", return_value=self.ninja_report
        ), mock.patch.object(
            build_pipeline, "_recorded_alias_context", return_value=context
        ):
            report = build_pipeline._live_alias_slice_no_work(
                self.source,
                self.developer,
                "x64",
                authorized_history=authorized,
            )
        self.assertTrue(report["no_work"])
        self.assertEqual(2, history.call_count)
        self.assertEqual(
            "authorized downstream signing", history.call_args_list[0].args[2]
        )
        self.assertEqual(
            "post-probe authorized downstream signing",
            history.call_args_list[1].args[2],
        )

    def test_first_downstream_no_work_freezes_current_history_around_probe(self):
        self.write_json(
            self.source / build_pipeline.HOME_ALIAS_RECEIPT,
            {"fixture": True},
        )
        out = self.source / build_pipeline.X64_OUT
        out.mkdir(parents=True, exist_ok=True)
        snapshot = {"ninja_log": {"sha256": "a"}, "ninja_deps": {"sha256": "b"}}
        with mock.patch.object(
            build_pipeline,
            "slice_receipt_contract",
            return_value=(self.root / "slice.json", {"schema": 2}),
        ), mock.patch.object(
            build_pipeline, "_ninja_history_snapshot", return_value=snapshot
        ) as capture_history, mock.patch.object(
            build_pipeline, "_ninja_history_exact_contract"
        ) as exact_history, mock.patch.object(
            build_pipeline,
            "_ninja_no_work_contract",
            return_value={"no_work": True},
        ), mock.patch.object(
            build_pipeline, "ninja_contract", return_value=self.ninja_report
        ), mock.patch.object(
            build_pipeline, "_recorded_alias_context", return_value=mock.Mock()
        ):
            report = build_pipeline._live_alias_slice_no_work(
                self.source, self.developer, "x64"
            )
        self.assertTrue(report["no_work"])
        capture_history.assert_called_once_with(out)
        self.assertEqual(2, exact_history.call_count)
        self.assertIs(snapshot, exact_history.call_args_list[0].args[0])
        self.assertIs(snapshot, exact_history.call_args_list[1].args[0])

    def test_execute_resumed_slice_only_atomically_publishes_schema_two_receipt(self):
        out = self.source / build_pipeline.ARM_OUT
        out.mkdir(parents=True, exist_ok=True)
        receipt_path = out / build_pipeline.SLICE_RECEIPT_NAME
        alias_path = self.source / build_pipeline.HOME_ALIAS_RECEIPT
        self.write_json(alias_path, {"fixture": True})
        plan = {
            "stage": "finalize-resumed-arm64",
            "architecture": "arm64",
            "out": str(out),
            "receipt": str(receipt_path),
            "app": {"architectures": ["arm64"]},
            "app_tree_sha256": "0" * 64,
            "args_gn_sha256": "a" * 64,
            "home_alias_compatibility": {
                "path": str(alias_path),
                "sha256": build_pipeline.sha256_file(alias_path),
            },
            "onboarding_alias_root_compatibility": {
                "path": str(self.source / ".focus-macos-onboarding-alias-root.json"),
                "bytes": 1234,
                "sha256": "9" * 64,
            },
            "resume_execution": {
                "path": str(self.root / "resume.execution.json"),
                "sha256": "b" * 64,
            },
            "mixed_path_scan": {"mixed_paths": False},
            "no_work_probe_command": ["ninja", "-n"],
            "ninja": self.ninja_report,
            "generated_linkedit_strip": {"all_linker_rules_use_selected_strip": True},
            "xcode27_compatibility_receipt_sha256": "c" * 64,
            "xcode27_seatbelt_compatibility_receipt_sha256": "d" * 64,
            "screen_ai_disabled_compatibility_receipt_sha256": "e" * 64,
            "xcode27_linkedit_strip_compatibility_receipt_sha256": "f" * 64,
        }
        no_work = {"no_work": True, "command": ["ninja", "-n"]}
        with mock.patch.object(
            build_pipeline, "resumed_slice_plan", return_value=plan
        ), mock.patch.object(
            build_pipeline, "_ninja_no_work_contract", return_value=no_work
        ), mock.patch.object(
            build_pipeline, "_recorded_alias_context", return_value=mock.Mock()
        ), mock.patch.object(
            build_pipeline, "home_alias_receipt_contract"
        ), mock.patch.object(
            build_pipeline, "slice_receipt_contract"
        ):
            with self.assertRaisesRegex(
                build_pipeline.PipelineError, "confirm-resumed-slice"
            ):
                build_pipeline.execute_resumed_slice(
                    self.source,
                    self.developer,
                    "arm64",
                    self.root / "resume.execution.json",
                    plan,
                    False,
                )
            result = build_pipeline.execute_resumed_slice(
                self.source,
                self.developer,
                "arm64",
                self.root / "resume.execution.json",
                plan,
                True,
            )
            receipt = json.loads(
                Path(result["path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                build_pipeline.RESUMED_SLICE_RECEIPT_SCHEMA,
                receipt["schema"],
            )
            self.assertTrue(receipt["raw_ninja_completed"])
            self.assertFalse(receipt["gn_gen_executed_by_finalizer"])
            self.assertFalse(receipt["build_command_executed_by_finalizer"])
            self.assertEqual(
                plan["onboarding_alias_root_compatibility"],
                receipt["onboarding_alias_root_compatibility"],
            )
            with self.assertRaisesRegex(
                build_pipeline.PipelineError, "refusing to replace receipt"
            ):
                build_pipeline.execute_resumed_slice(
                    self.source,
                    self.developer,
                    "arm64",
                    self.root / "resume.execution.json",
                    plan,
                    True,
                )

    def write_invalid_legacy_x64_graph(self):
        out = self.source / build_pipeline.X64_OUT
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        args_text = 'target_cpu = "x64"\nis_component_build = false\n'
        (out / "args.gn").write_text(args_text, encoding="utf-8")
        for index in range(build_pipeline.LEGACY_X64_TOOLCHAIN_FILE_COUNT):
            toolchain = out / "obj" / "tool{}".format(index) / "toolchain.ninja"
            toolchain.parent.mkdir(parents=True)
            toolchain.write_text(
                "command = clang -Wcrl,strippath,{} input\n".format(
                    build_pipeline.LEGACY_LLVM_STRIP_TOKEN
                ),
                encoding="utf-8",
            )
        return out, args_text

    def fresh_x64_execution_plan(self, args_text):
        paths = build_pipeline._fresh_x64_fixed_paths(self.source)
        legacy = build_pipeline._legacy_x64_invalid_strip_contract(paths["out"])
        args_digest = hashlib.sha256(args_text.encode("utf-8")).hexdigest()
        legacy_out = paths["legacy_root"] / Path(build_pipeline.X64_OUT).name
        transaction_out = (
            paths["transaction_root"] / Path(build_pipeline.X64_OUT).name
        )
        return {
            "stage": "prepare-fresh-x64",
            "source_root": str(self.source),
            "developer_dir": str(self.developer),
            "out": str(paths["out"]),
            "receipt": str(paths["receipt"]),
            "legacy_root": str(paths["legacy_root"]),
            "legacy_out": str(legacy_out),
            "transaction_root": str(paths["transaction_root"]),
            "transaction_legacy_out": str(transaction_out),
            "transaction_prepared": str(
                paths["transaction_root"]
                / build_pipeline.FRESH_X64_TRANSACTION_PREPARED
            ),
            "fresh_failed": str(paths["fresh_failed"]),
            "transaction_failed": str(paths["transaction_failed"]),
            "receipt_failed": str(paths["receipt_failed"]),
            "legacy_inventory": legacy,
            "fresh_profile": {
                "flags_file": "fixture-x64.gn",
                "arg_names": ["is_component_build", "target_cpu"],
                "args_gn_bytes": len(args_text.encode("utf-8")),
                "args_gn_sha256": args_digest,
            },
            "gn_command": [
                str(self.depot / "gn"),
                "gen",
                build_pipeline.X64_OUT,
                "--fail-on-unused-args",
            ],
            "acquisition_receipt": {"path": "acquisition", "sha256": "a" * 64},
            "tool_receipt": {"path": "tools", "sha256": "b" * 64},
            "preparation_receipt": {"path": "preparation", "sha256": "c" * 64},
            "reclaimed_arm_onboarding": {"kind": "fixture"},
            "xcode27_compatibility_receipt_sha256": "d" * 64,
            "xcode27_seatbelt_compatibility_receipt_sha256": "e" * 64,
            "screen_ai_disabled_compatibility_receipt_sha256": "f" * 64,
            "xcode27_linkedit_strip_compatibility_receipt_sha256": "0" * 64,
            "linkedit_strip_tools": self.linkedit_tools,
            "legacy_preserved": True,
            "gn_invocations": 1,
            "ninja_invocations": 0,
            "offline": True,
            "network_operations": 0,
        }

    def test_fresh_x64_legacy_gate_binds_path_hash_and_never_deletes(self):
        out, args_text = self.write_invalid_legacy_x64_graph()
        expected_hashes = dict(build_pipeline.SWIFTSHADER_DISABLED_ARGS_SHA256)
        expected_hashes["x64"] = hashlib.sha256(
            args_text.encode("utf-8")
        ).hexdigest()
        with mock.patch.object(
            build_pipeline, "SWIFTSHADER_DISABLED_ARGS_SHA256", expected_hashes
        ):
            report = build_pipeline._legacy_x64_invalid_strip_contract(out)
            self.assertEqual(str(out), report["root"])
            self.assertEqual(
                build_pipeline.LEGACY_X64_TOOLCHAIN_FILE_COUNT,
                report["toolchain_file_count"],
            )
            first = out / report["toolchain_files"][0]["path"]
            first.write_text("command = llvm-strip\n", encoding="utf-8")
            with self.assertRaisesRegex(
                build_pipeline.PipelineError, "exact llvm-strip graph"
            ):
                build_pipeline._legacy_x64_invalid_strip_contract(out)
        self.assertTrue(out.is_dir())
        self.assertTrue(first.is_file())

    def test_fresh_x64_generated_graph_rejects_any_llvm_strip(self):
        out = self.source / build_pipeline.X64_OUT
        shutil.rmtree(out)
        toolchain = out / "obj" / "default" / "toolchain.ninja"
        toolchain.parent.mkdir(parents=True)
        (out / "args.gn").write_text('target_cpu = "x64"\n', encoding="utf-8")
        (out / "build.ninja").write_text("subninja obj/default/toolchain.ninja\n")
        (out / "build.ninja.d").write_text("build.ninja: args.gn\n")
        toolchain.write_text(
            "command = clang -Wcrl,strippath,{} input\n".format(
                self.linkedit_tools["selected"]["path"]
            ),
            encoding="utf-8",
        )
        report = build_pipeline._fresh_x64_generated_graph_contract(
            out, self.linkedit_tools
        )
        self.assertEqual(0, report["llvm_strip_occurrences"])
        toolchain.write_text(
            toolchain.read_text(encoding="utf-8") + "# llvm-strip forbidden\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(build_pipeline.PipelineError, "llvm-strip"):
            build_pipeline._fresh_x64_generated_graph_contract(
                out, self.linkedit_tools
            )

    def test_fresh_x64_execution_rolls_back_without_deleting_legacy_graph(self):
        out, args_text = self.write_invalid_legacy_x64_graph()
        marker = out / "keep-me.bin"
        marker.write_bytes(b"preserved legacy bytes")
        expected_hashes = dict(build_pipeline.SWIFTSHADER_DISABLED_ARGS_SHA256)
        expected_hashes["x64"] = hashlib.sha256(
            args_text.encode("utf-8")
        ).hexdigest()
        profiles = {"profiles": {"x64": {"args_gn": args_text}}}
        with mock.patch.object(
            build_pipeline, "SWIFTSHADER_DISABLED_ARGS_SHA256", expected_hashes
        ):
            plan = self.fresh_x64_execution_plan(args_text)
            with mock.patch.object(
                build_pipeline, "fresh_x64_preparation_plan", return_value=plan
            ), mock.patch.object(
                build_pipeline, "require_free"
            ), mock.patch.object(
                build_pipeline.focus_macos,
                "validate_gn_profiles",
                return_value=profiles,
            ), mock.patch.object(
                build_pipeline,
                "_recorded_alias_context",
                return_value=self.alias_context_fixture(),
            ), mock.patch.object(
                build_pipeline, "safe_environment", return_value={}
            ), mock.patch.object(
                build_pipeline,
                "run_monitored",
                side_effect=build_pipeline.PipelineError("GN fixture failure"),
            ) as command:
                with self.assertRaisesRegex(
                    build_pipeline.PipelineError, "GN fixture failure"
                ):
                    build_pipeline.execute_fresh_x64_preparation(
                        self.source, self.developer, plan, True
                    )
        command.assert_called_once()
        self.assertEqual(plan["gn_command"], command.call_args.args[0])
        self.assertEqual(b"preserved legacy bytes", marker.read_bytes())
        paths = build_pipeline._fresh_x64_fixed_paths(self.source)
        self.assertFalse(os.path.lexists(str(paths["legacy_root"])))
        self.assertFalse(os.path.lexists(str(paths["transaction_root"])))
        self.assertFalse(os.path.lexists(str(paths["receipt"])))
        self.assertTrue(paths["fresh_failed"].is_dir())
        self.assertTrue(paths["transaction_failed"].is_dir())

    def test_fresh_x64_exclusive_rename_never_replaces_rival(self):
        source = self.root / "rename-source"
        destination = self.root / "rename-destination"
        source.mkdir()
        destination.mkdir()
        (source / "source-marker").write_bytes(b"source")
        (destination / "rival-marker").write_bytes(b"rival")
        identity = build_pipeline._fresh_x64_directory_identity(
            source, "rename fixture"
        )
        with self.assertRaisesRegex(build_pipeline.PipelineError, "destination exists"):
            build_pipeline._rename_owned_directory(
                source, destination, identity, "rename fixture"
            )
        self.assertEqual(b"source", (source / "source-marker").read_bytes())
        self.assertEqual(b"rival", (destination / "rival-marker").read_bytes())

    def test_fresh_x64_post_rename_failure_recovers_legacy_by_inode(self):
        out, args_text = self.write_invalid_legacy_x64_graph()
        marker = out / "keep-me.bin"
        marker.write_bytes(b"legacy survives post-rename exception")
        expected_hashes = dict(build_pipeline.SWIFTSHADER_DISABLED_ARGS_SHA256)
        expected_hashes["x64"] = hashlib.sha256(
            args_text.encode("utf-8")
        ).hexdigest()
        real_rename = build_pipeline._rename_owned_directory
        calls = {"count": 0}

        def fail_after_first_rename(*args):
            calls["count"] += 1
            result = real_rename(*args)
            if calls["count"] == 1:
                raise build_pipeline.PipelineError("injected post-rename failure")
            return result

        with mock.patch.object(
            build_pipeline, "SWIFTSHADER_DISABLED_ARGS_SHA256", expected_hashes
        ):
            plan = self.fresh_x64_execution_plan(args_text)
            with mock.patch.object(
                build_pipeline, "fresh_x64_preparation_plan", return_value=plan
            ), mock.patch.object(
                build_pipeline, "require_free"
            ), mock.patch.object(
                build_pipeline,
                "_rename_owned_directory",
                side_effect=fail_after_first_rename,
            ):
                with self.assertRaisesRegex(
                    build_pipeline.PipelineError, "post-rename failure"
                ):
                    build_pipeline.execute_fresh_x64_preparation(
                        self.source, self.developer, plan, True
                    )
        self.assertEqual(
            b"legacy survives post-rename exception", marker.read_bytes()
        )
        paths = build_pipeline._fresh_x64_fixed_paths(self.source)
        self.assertFalse(os.path.lexists(str(paths["transaction_root"])))
        self.assertTrue(paths["transaction_failed"].is_dir())

    def test_fresh_x64_marker_failure_quarantines_transaction_and_keeps_legacy(self):
        out, args_text = self.write_invalid_legacy_x64_graph()
        marker = out / "keep-me.bin"
        marker.write_bytes(b"legacy survives marker failure")
        expected_hashes = dict(build_pipeline.SWIFTSHADER_DISABLED_ARGS_SHA256)
        expected_hashes["x64"] = hashlib.sha256(
            args_text.encode("utf-8")
        ).hexdigest()
        with mock.patch.object(
            build_pipeline, "SWIFTSHADER_DISABLED_ARGS_SHA256", expected_hashes
        ):
            plan = self.fresh_x64_execution_plan(args_text)
            with mock.patch.object(
                build_pipeline, "fresh_x64_preparation_plan", return_value=plan
            ), mock.patch.object(
                build_pipeline, "require_free"
            ), mock.patch.object(
                build_pipeline,
                "atomic_json",
                side_effect=build_pipeline.PipelineError("injected marker failure"),
            ):
                with self.assertRaisesRegex(
                    build_pipeline.PipelineError, "marker failure"
                ):
                    build_pipeline.execute_fresh_x64_preparation(
                        self.source, self.developer, plan, True
                    )
        self.assertEqual(b"legacy survives marker failure", marker.read_bytes())
        paths = build_pipeline._fresh_x64_fixed_paths(self.source)
        self.assertFalse(os.path.lexists(str(paths["transaction_root"])))
        self.assertTrue(paths["transaction_failed"].is_dir())

    def test_fresh_x64_execution_runs_only_gn_and_preserves_legacy_graph(self):
        out, args_text = self.write_invalid_legacy_x64_graph()
        marker = out / "keep-me.bin"
        marker.write_bytes(b"preserved legacy bytes")
        expected_hashes = dict(build_pipeline.SWIFTSHADER_DISABLED_ARGS_SHA256)
        expected_hashes["x64"] = hashlib.sha256(
            args_text.encode("utf-8")
        ).hexdigest()
        profiles = {"profiles": {"x64": {"args_gn": args_text}}}

        def generate_graph(*_args, **_kwargs):
            fresh = self.source / build_pipeline.X64_OUT
            (fresh / "build.ninja").write_text("fixture\n", encoding="utf-8")
            (fresh / "build.ninja.d").write_text(
                "build.ninja: args.gn\n", encoding="utf-8"
            )
            toolchain = fresh / "obj" / "default" / "toolchain.ninja"
            toolchain.parent.mkdir(parents=True)
            toolchain.write_text(
                "command = clang -Wcrl,strippath,{} input\n".format(
                    self.linkedit_tools["selected"]["path"]
                ),
                encoding="utf-8",
            )

        with mock.patch.object(
            build_pipeline, "SWIFTSHADER_DISABLED_ARGS_SHA256", expected_hashes
        ):
            plan = self.fresh_x64_execution_plan(args_text)
            with mock.patch.object(
                build_pipeline, "fresh_x64_preparation_plan", return_value=plan
            ), mock.patch.object(
                build_pipeline, "require_free"
            ), mock.patch.object(
                build_pipeline.focus_macos,
                "validate_gn_profiles",
                return_value=profiles,
            ), mock.patch.object(
                build_pipeline,
                "_recorded_alias_context",
                return_value=self.alias_context_fixture(),
            ), mock.patch.object(
                build_pipeline, "safe_environment", return_value={}
            ), mock.patch.object(
                build_pipeline, "run_monitored", side_effect=generate_graph
            ) as command, mock.patch.object(
                build_pipeline, "fresh_x64_preparation_contract"
            ):
                result = build_pipeline.execute_fresh_x64_preparation(
                    self.source, self.developer, plan, True
                )
        command.assert_called_once()
        self.assertEqual(plan["gn_command"], command.call_args.args[0])
        receipt = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertFalse(receipt["ninja_executed"])
        self.assertFalse(receipt["build_executed"])
        self.assertEqual(0, receipt["generated_graph"]["llvm_strip_occurrences"])
        preserved = Path(plan["legacy_out"]) / marker.name
        self.assertEqual(b"preserved legacy bytes", preserved.read_bytes())
        self.assertFalse(os.path.lexists(str(self.source / build_pipeline.X64_OUT / ".ninja_log")))

    def test_fresh_x64_resume_binding_requires_exact_receipt_and_pre_graph(self):
        out = self.source / build_pipeline.X64_OUT
        shutil.rmtree(out)
        toolchain = out / "obj" / "default" / "toolchain.ninja"
        toolchain.parent.mkdir(parents=True)
        (out / "args.gn").write_text('target_cpu = "x64"\n', encoding="utf-8")
        (out / "build.ninja").write_text("fixture graph\n", encoding="utf-8")
        (out / "build.ninja.d").write_text(
            "build.ninja: args.gn\n", encoding="utf-8"
        )
        toolchain.write_text(
            "command = clang -Wcrl,strippath,{} input\n".format(
                self.linkedit_tools["selected"]["path"]
            ),
            encoding="utf-8",
        )
        graph = build_pipeline._fresh_x64_generated_graph_contract(
            out, self.linkedit_tools
        )
        receipt = {
            "schema": 1,
            "stage": "prepare-fresh-x64",
            "source_root": str(self.source),
            "developer_dir": str(self.developer),
            "legacy_root": str(self.source / build_pipeline.FRESH_X64_LEGACY_ROOT),
            "legacy_out": str(
                self.source
                / build_pipeline.FRESH_X64_LEGACY_ROOT
                / Path(build_pipeline.X64_OUT).name
            ),
            "legacy_inventory": {"fixture": True},
            "prepared_evidence": {"fixture": True},
            "fresh_out": str(out),
            "fresh_out_identity": build_pipeline._fresh_x64_directory_identity(
                out, "fixture fresh output"
            ),
            "fresh_profile": {"args_gn_sha256": graph["args_gn"]["sha256"]},
            "generated_graph": graph,
            "gn_command": ["gn", "gen", build_pipeline.X64_OUT],
            "acquisition_receipt": {"fixture": True},
            "tool_receipt": {"fixture": True},
            "preparation_receipt": {"fixture": True},
            "reclaimed_arm_onboarding": {"fixture": True},
            "xcode27_compatibility_receipt_sha256": "a" * 64,
            "xcode27_seatbelt_compatibility_receipt_sha256": "b" * 64,
            "screen_ai_disabled_compatibility_receipt_sha256": "c" * 64,
            "xcode27_linkedit_strip_compatibility_receipt_sha256": "d" * 64,
            "linkedit_strip_tools": self.linkedit_tools,
            "legacy_preserved": True,
            "legacy_deleted": False,
            "gn_gen_executed": True,
            "gn_gen_succeeded": True,
            "ninja_executed": False,
            "build_executed": False,
            "signing_executed": False,
            "packaging_executed": False,
            "offline": True,
            "network_operations": 0,
        }
        receipt_path = self.source / build_pipeline.FRESH_X64_PREPARATION_RECEIPT
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        supplied = {
            "receipt": {
                "path": str(receipt_path),
                "bytes": receipt_path.stat().st_size,
                "sha256": build_pipeline.sha256_file(receipt_path),
            },
            "contract_sha256": hashlib.sha256(
                alias_resume_runner._canonical_bytes(receipt)
            ).hexdigest(),
        }
        pre_run = {
            "ninja_log": None,
            "ninja_deps": None,
            "build_ninja": build_pipeline._regular_file_snapshot(
                out / "build.ninja"
            ),
            "toolchain_inventory": build_pipeline._toolchain_inventory(out),
        }
        with mock.patch.object(
            build_pipeline, "_verify_legacy_x64_inventory"
        ) as legacy:
            report = build_pipeline._fresh_x64_resume_preparation_binding(
                self.source, self.developer, out, supplied, pre_run
            )
        legacy.assert_called_once_with(
            Path(receipt["legacy_out"]), receipt["legacy_inventory"]
        )
        self.assertEqual(graph, report["graph"])
        forged = dict(supplied)
        forged["contract_sha256"] = "0" * 64
        with self.assertRaisesRegex(build_pipeline.PipelineError, "contract hash"):
            build_pipeline._fresh_x64_resume_preparation_binding(
                self.source, self.developer, out, forged, pre_run
            )
        toolchain.write_text("command = llvm-strip\n", encoding="utf-8")
        with mock.patch.object(
            build_pipeline, "_verify_legacy_x64_inventory"
        ), self.assertRaises(build_pipeline.PipelineError):
            build_pipeline._fresh_x64_resume_preparation_binding(
                self.source, self.developer, out, supplied, pre_run
            )

    def test_resume3_x64_history_requires_none_to_created_transition(self):
        graph = {"fixture": True}
        pre_run = {
            "ninja_log": None,
            "ninja_deps": None,
            "build_ninja": graph,
            "toolchain_inventory": graph,
        }
        post = {
            "ninja_log": {"bytes": 10, "mtime_ns": 101, "sha256": "a" * 64},
            "ninja_deps": {"bytes": 20, "mtime_ns": 102, "sha256": "b" * 64},
            "build_ninja": graph,
            "toolchain_inventory": graph,
        }
        self.assertTrue(
            build_pipeline._resume3_ninja_history_transition_contract(
                pre_run, post, "x64", 100
            )
        )
        pre_run["ninja_log"] = dict(post["ninja_log"])
        with self.assertRaisesRegex(build_pipeline.PipelineError, "not absent"):
            build_pipeline._resume3_ninja_history_transition_contract(
                pre_run, post, "x64", 100
            )
        pre_run["ninja_log"] = None
        post["ninja_deps"]["mtime_ns"] = 99
        with self.assertRaisesRegex(build_pipeline.PipelineError, "created by Ninja"):
            build_pipeline._resume3_ninja_history_transition_contract(
                pre_run, post, "x64", 100
            )
        arm_pre = {
            "ninja_log": {"sha256": "1" * 64, "mtime_ns": 200},
            "ninja_deps": {"sha256": "2" * 64, "mtime_ns": 200},
            "build_ninja": graph,
            "toolchain_inventory": graph,
        }
        arm_post = {
            "ninja_log": {"sha256": "3" * 64, "mtime_ns": 201},
            "ninja_deps": dict(arm_pre["ninja_deps"]),
            "build_ninja": graph,
            "toolchain_inventory": graph,
        }
        self.assertTrue(
            build_pipeline._resume3_ninja_history_transition_contract(
                arm_pre, arm_post, "arm64", 100
            )
        )

    def test_home_alias_resume_cli_is_explicit_and_has_no_gn_option(self):
        adopt = build_pipeline.parser().parse_args(
            [
                "adopt-home-alias",
                "--source-root",
                "/Users/legacy/work/src",
                "--developer-dir",
                "/Users/legacy/Xcode.app/Contents/Developer",
                "--logical-home",
                "/Users/legacy",
                "--logical-workspace-root",
                "/Users/legacy/work",
            ]
        )
        self.assertFalse(adopt.execute)
        resume = build_pipeline.parser().parse_args(
            [
                "finalize-resumed-x64",
                "--source-root",
                "/Users/legacy/work/src",
                "--developer-dir",
                "/Users/legacy/Xcode.app/Contents/Developer",
                "--resume-record",
                "/Users/legacy/work/logs/x64.execution.json",
            ]
        )
        self.assertEqual("finalize-resumed-x64", resume.command)
        self.assertFalse(resume.confirm_resumed_slice)
        self.assertFalse(hasattr(resume, "allow_recovery_move"))
        fresh = build_pipeline.parser().parse_args(
            [
                "prepare-fresh-x64",
                "--source-root",
                "/Users/legacy/work/src",
                "--developer-dir",
                "/Users/legacy/Xcode.app/Contents/Developer",
            ]
        )
        self.assertFalse(fresh.execute)
        self.assertFalse(fresh.confirm_exact_legacy_move)
        self.assertFalse(hasattr(fresh, "resume_record"))


if __name__ == "__main__":
    unittest.main()
