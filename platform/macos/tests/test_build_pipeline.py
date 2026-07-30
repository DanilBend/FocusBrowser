#!/usr/bin/env python3
"""Unit tests for the staged, low-space macOS build pipeline."""

import hashlib
import json
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import build_pipeline


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

    def test_recursive_reclamation_requires_explicit_flag(self):
        with self.assertRaisesRegex(build_pipeline.PipelineError, "allow-reclaim"):
            build_pipeline.execute_stage_arm(self.source, {}, False)

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


if __name__ == "__main__":
    unittest.main()
