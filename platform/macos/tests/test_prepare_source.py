#!/usr/bin/env python3
"""Unit tests for the offline macOS Chromium source preparer."""

import contextlib
import hashlib
import io
import json
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from collections import OrderedDict
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import prepare_source


class PrepareSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.disk_usage_patcher = mock.patch.object(
            prepare_source.shutil,
            "disk_usage",
            return_value=mock.Mock(free=64 * prepare_source.GIB),
        )
        self.disk_usage_patcher.start()

    def tearDown(self):
        self.disk_usage_patcher.stop()
        self.temporary.cleanup()

    def write_acquisition_marker(self, source, overrides=None):
        source = source.resolve()
        payload = {
            "status": "acquisition_complete",
            "execution_requested": True,
            "destination": str(source.parent),
            "pins": {
                "chromium_version": prepare_source.focus_macos.PINNED_CHROMIUM_VERSION,
                "chromium_tag": prepare_source.focus_macos.PINNED_CHROMIUM_VERSION,
                "chromium_commit": prepare_source.ACQUISITION_CHROMIUM_COMMIT,
                "depot_tools_commit": prepare_source.ACQUISITION_DEPOT_TOOLS_COMMIT,
            },
            "verification": {
                "chromium_version": prepare_source.focus_macos.PINNED_CHROMIUM_VERSION,
                "chromium_commit": prepare_source.ACQUISITION_CHROMIUM_COMMIT,
                "depot_tools_commit": prepare_source.ACQUISITION_DEPOT_TOOLS_COMMIT,
                "source_root": str(source),
            },
            "gclient": {
                "target_os": ["mac"],
                "target_os_only": True,
                "hooks_during_acquisition": False,
                "spec_sha256": prepare_source.ACQUISITION_GCLIENT_SPEC_SHA256,
            },
        }
        if overrides:
            overrides(payload)
        marker = source.parent / prepare_source.ACQUISITION_MARKER
        marker.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return marker, payload

    def write_tool_bootstrap_marker(self, source, acquisition_sha256, overrides=None):
        source = source.resolve()
        developer_dir = self.root / "Applications/Xcode-beta.app/Contents/Developer"
        developer_dir.mkdir(parents=True, exist_ok=True)
        developer_dir = developer_dir.resolve()
        depot_tools = source.parent / "depot_tools"
        depot_tools.mkdir(exist_ok=True)
        tool_hashes = {}
        for name in ("gclient", "gn", "autoninja"):
            tool = depot_tools / name
            tool.write_bytes((name + "\n").encode("ascii"))
            tool.chmod(0o755)
            tool_hashes[name] = prepare_source.sha256_file(tool)
        payload = {
            "schema": 1,
            "hooks_complete": True,
            "chromium_commit": prepare_source.ACQUISITION_CHROMIUM_COMMIT,
            "depot_tools_commit": prepare_source.ACQUISITION_DEPOT_TOOLS_COMMIT,
            "source_root": str(source),
            "developer_dir": str(developer_dir),
            "acquisition_marker_sha256": acquisition_sha256,
            "gclient_command": [
                str(source.parent / "depot_tools/gclient"),
                "runhooks",
            ],
            "gn_version": "150.0.0",
            "tool_sha256": tool_hashes,
            "post_hooks_free_bytes": 70 * prepare_source.GIB,
            "build_executed": False,
        }
        if overrides:
            overrides(payload)
        marker = source.parent / prepare_source.TOOL_BOOTSTRAP_MARKER
        marker.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return marker, payload

    def test_disk_floor_accepts_exact_30_gib_and_rejects_one_byte_less(self):
        accepted = prepare_source.require_disk_floor(
            [self.root],
            "fixture",
            disk_usage=lambda _path: mock.Mock(
                free=prepare_source.HARD_DISK_FLOOR_BYTES
            ),
        )
        self.assertEqual(30, accepted["required_free_gib"])
        with self.assertRaisesRegex(prepare_source.PreparationError, "disk floor"):
            prepare_source.require_disk_floor(
                [self.root],
                "fixture",
                disk_usage=lambda _path: mock.Mock(
                    free=prepare_source.HARD_DISK_FLOOR_BYTES - 1
                ),
            )

    def test_valid_acquisition_marker_binds_exact_source_and_pins(self):
        source = self.root / "acquisition/src"
        source.mkdir(parents=True)
        marker, _ = self.write_acquisition_marker(source)
        report = prepare_source.validate_acquisition_marker(source)
        self.assertEqual(str(marker.resolve()), report["path"])
        self.assertEqual(str(source.resolve()), report["source_root"])
        self.assertEqual(
            prepare_source.ACQUISITION_CHROMIUM_COMMIT,
            report["chromium_commit"],
        )

    def test_acquisition_marker_rejects_symlink(self):
        source = self.root / "acquisition/src"
        source.mkdir(parents=True)
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        (source.parent / prepare_source.ACQUISITION_MARKER).symlink_to(outside)
        with self.assertRaisesRegex(prepare_source.PreparationError, "regular"):
            prepare_source.validate_acquisition_marker(source)

    def test_acquisition_marker_rejects_tampered_status_and_pin(self):
        source = self.root / "acquisition/src"
        source.mkdir(parents=True)
        marker, payload = self.write_acquisition_marker(source)
        payload["status"] = "preflight_only"
        marker.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(prepare_source.PreparationError, "not complete"):
            prepare_source.validate_acquisition_marker(source)
        payload["status"] = "acquisition_complete"
        payload["pins"]["chromium_commit"] = "0" * 40
        marker.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(prepare_source.PreparationError, "pins"):
            prepare_source.validate_acquisition_marker(source)

    def test_acquisition_marker_rejects_source_path_replay(self):
        source = self.root / "acquisition/src"
        source.mkdir(parents=True)
        marker, payload = self.write_acquisition_marker(source)
        payload["verification"]["source_root"] = str(self.root / "other/src")
        marker.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(
            prepare_source.PreparationError, "verification mismatch"
        ):
            prepare_source.validate_acquisition_marker(source)

    def test_valid_tool_bootstrap_marker_is_exact_and_source_bound(self):
        source = self.root / "acquisition/src"
        source.mkdir(parents=True)
        acquisition, _ = self.write_acquisition_marker(source)
        acquisition_sha = prepare_source.sha256_file(acquisition)
        marker, _ = self.write_tool_bootstrap_marker(source, acquisition_sha)
        report = prepare_source.validate_tool_bootstrap_marker(
            source,
            {"sha256": acquisition_sha},
        )
        self.assertEqual(str(marker.resolve()), report["path"])
        self.assertEqual(str(source.resolve()), report["source_root"])
        self.assertTrue(report["hooks_complete"])
        self.assertFalse(report["build_executed"])
        self.assertTrue(report["developer_dir"].endswith(".app/Contents/Developer"))
        self.assertEqual({"gclient", "gn", "autoninja"}, set(report["tool_sha256"]))

    def test_tool_bootstrap_marker_rejects_symlink_and_schema_drift(self):
        source = self.root / "acquisition/src"
        source.mkdir(parents=True)
        outside = self.root / "outside-bootstrap.json"
        outside.write_text("{}", encoding="utf-8")
        marker = source.parent / prepare_source.TOOL_BOOTSTRAP_MARKER
        marker.symlink_to(outside)
        with self.assertRaisesRegex(prepare_source.PreparationError, "regular"):
            prepare_source.validate_tool_bootstrap_marker(
                source, {"sha256": "a" * 64}
            )
        marker.unlink()
        marker, payload = self.write_tool_bootstrap_marker(source, "a" * 64)
        payload["unexpected"] = True
        marker.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(prepare_source.PreparationError, "schema"):
            prepare_source.validate_tool_bootstrap_marker(
                source, {"sha256": "a" * 64}
            )

    def test_tool_bootstrap_marker_rejects_tampered_provenance(self):
        source = self.root / "acquisition/src"
        source.mkdir(parents=True)
        cases = (
            ("hooks", lambda value: value.update(hooks_complete=False), "incomplete"),
            (
                "source",
                lambda value: value.update(source_root=str(self.root / "other/src")),
                "source_root",
            ),
            (
                "chromium",
                lambda value: value.update(chromium_commit="0" * 40),
                "Chromium commit",
            ),
            (
                "depot",
                lambda value: value.update(depot_tools_commit="0" * 40),
                "depot_tools commit",
            ),
            (
                "acquisition",
                lambda value: value.update(acquisition_marker_sha256="0" * 64),
                "acquisition marker",
            ),
            (
                "developer",
                lambda value: value.update(developer_dir="Xcode.app/Contents/Developer"),
                "developer_dir",
            ),
            (
                "tool hash",
                lambda value: value["tool_sha256"].update(gn="not-a-hash"),
                "SHA-256",
            ),
            ("build", lambda value: value.update(build_executed=True), "reports a build"),
        )
        for label, mutate, message in cases:
            marker = source.parent / prepare_source.TOOL_BOOTSTRAP_MARKER
            if marker.exists():
                marker.unlink()
            self.write_tool_bootstrap_marker(source, "a" * 64, mutate)
            with self.subTest(label=label), self.assertRaisesRegex(
                prepare_source.PreparationError, message
            ):
                prepare_source.validate_tool_bootstrap_marker(
                    source, {"sha256": "a" * 64}
                )

    def test_tool_bootstrap_marker_rehashes_current_regular_executables(self):
        source = self.root / "acquisition/src"
        source.mkdir(parents=True)
        marker, _ = self.write_tool_bootstrap_marker(source, "a" * 64)
        gn = source.parent / "depot_tools/gn"
        gn.write_bytes(b"changed\n")
        with self.assertRaisesRegex(prepare_source.PreparationError, "hash changed"):
            prepare_source.validate_tool_bootstrap_marker(
                source, {"sha256": "a" * 64}
            )
        marker.unlink()
        self.write_tool_bootstrap_marker(source, "a" * 64)
        autoninja = source.parent / "depot_tools/autoninja"
        autoninja.unlink()
        outside = self.root / "outside-tool"
        outside.write_bytes(b"autoninja\n")
        outside.chmod(0o755)
        autoninja.symlink_to(outside)
        with self.assertRaisesRegex(prepare_source.PreparationError, "symlink"):
            prepare_source.validate_tool_bootstrap_marker(
                source, {"sha256": "a" * 64}
            )

    def test_preflight_requires_bootstrap_before_later_source_checks(self):
        source = self.root / "acquisition/src"
        source.mkdir(parents=True)
        with mock.patch.object(
            prepare_source.focus_macos,
            "resolve_source_root",
            return_value=(source.resolve(), "150.0.7871.128"),
        ), mock.patch.object(
            prepare_source,
            "validate_acquisition_marker",
            return_value={"sha256": "a" * 64},
        ), mock.patch.object(
            prepare_source,
            "validate_tool_bootstrap_marker",
            side_effect=prepare_source.PreparationError("bootstrap blocked"),
        ), mock.patch.object(
            prepare_source, "validate_upstream_source_contracts"
        ) as upstream:
            with self.assertRaisesRegex(
                prepare_source.PreparationError, "bootstrap blocked"
            ):
                prepare_source.preflight(source, self.root)
        upstream.assert_not_called()

    def make_zip(self, name="safe.zip", members=None):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as stream:
            for member, content in (members or [("root/file.txt", b"payload")]):
                stream.writestr(member, content)
        return path

    def make_tar(self, name="safe.tar.gz", members=None):
        path = self.root / name
        with tarfile.open(path, "w:gz") as stream:
            for member, content in (members or [("file.txt", b"payload")]):
                info = tarfile.TarInfo(member)
                info.size = len(content)
                info.mode = 0o644
                stream.addfile(info, io.BytesIO(content))
        return path

    def test_safe_relative_accepts_only_canonical_posix_paths(self):
        self.assertEqual("a/b.txt", prepare_source.safe_relative("a/b.txt", "test"))
        for unsafe in ("", ".", "./a", "../a", "/a", "a\\b", "a/../b", " a"):
            with self.subTest(unsafe=unsafe), self.assertRaises(prepare_source.PreparationError):
                prepare_source.safe_relative(unsafe, "test")

    def test_dependency_manifest_is_exact_and_offline(self):
        contracts = prepare_source.validate_dependency_manifest()
        self.assertEqual(
            [
                "search_engines_data",
                "onboarding",
                "ublock_origin",
                "chromium_node_arm64",
                "chromium_node_x64",
                "chromium_node_modules",
                "esbuild_darwin_arm64",
                "esbuild_darwin_x64",
                "rollup_darwin_arm64",
                "rollup_darwin_x64",
            ],
            list(contracts),
        )
        self.assertEqual(
            "ublock-origin-1.72.2.zip",
            contracts["ublock_origin"]["download_filename"],
        )
        self.assertEqual(
            "package", contracts["esbuild_darwin_arm64"]["strip_leading_dirs"]
        )

    def test_offline_cache_accepts_exact_custom_hash(self):
        cache = self.root / "cache"
        cache.mkdir()
        archive = cache / "dep.zip"
        archive.write_bytes(b"offline")
        contracts = OrderedDict(
            (
                (
                    "dep",
                    {
                        "download_filename": "dep.zip",
                        "sha256": hashlib.sha256(b"offline").hexdigest(),
                    },
                ),
            )
        )
        resolved, report = prepare_source.validate_offline_cache(cache, contracts)
        self.assertEqual(cache.resolve(), resolved)
        self.assertEqual(7, report[0]["bytes"])

    def test_offline_cache_rejects_hash_mismatch(self):
        cache = self.root / "cache"
        cache.mkdir()
        (cache / "dep.zip").write_bytes(b"changed")
        contracts = OrderedDict(
            (("dep", {"download_filename": "dep.zip", "sha256": "0" * 64}),)
        )
        with self.assertRaisesRegex(prepare_source.PreparationError, "hash mismatch"):
            prepare_source.validate_offline_cache(cache, contracts)

    def test_zip_inspection_strips_only_declared_prefix(self):
        archive = self.make_zip()
        entries = prepare_source.inspect_archive(
            archive, {"kind": "zip", "strip_leading_dirs": "root"}
        )
        self.assertEqual("file.txt", entries[0][0])

    def test_tar_inspection_accepts_conventional_dot_slash_root(self):
        archive = self.root / "dot-root.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            directory = tarfile.TarInfo("./")
            directory.type = tarfile.DIRTYPE
            stream.addfile(directory)
            payload = b"safe"
            info = tarfile.TarInfo("./folder/file.txt")
            info.size = len(payload)
            info.mode = 0o644
            stream.addfile(info, io.BytesIO(payload))
        entries = prepare_source.inspect_archive(
            archive, {"kind": "tar", "strip_leading_dirs": None}
        )
        self.assertEqual("folder/file.txt", entries[0][0])

    def test_dot_slash_normalization_does_not_allow_parent_traversal(self):
        archive = self.make_tar(members=[("./../escape", b"bad")])
        with self.assertRaises(prepare_source.PreparationError):
            prepare_source.inspect_archive(
                archive, {"kind": "tar", "strip_leading_dirs": None}
            )

    def test_zip_inspection_rejects_traversal(self):
        archive = self.make_zip(members=[("../escape", b"bad")])
        with self.assertRaises(prepare_source.PreparationError):
            prepare_source.inspect_archive(
                archive, {"kind": "zip", "strip_leading_dirs": None}
            )

    def test_zip_inspection_rejects_symlink(self):
        archive = self.root / "link.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            stream.writestr(info, "target")
        with self.assertRaisesRegex(prepare_source.PreparationError, "regular"):
            prepare_source.inspect_archive(
                archive, {"kind": "zip", "strip_leading_dirs": None}
            )

    def test_tar_inspection_rejects_symbolic_link(self):
        archive = self.root / "link.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "outside"
            stream.addfile(info)
        with self.assertRaisesRegex(prepare_source.PreparationError, "symbolic link"):
            prepare_source.inspect_archive(
                archive, {"kind": "tar", "strip_leading_dirs": None}
            )

    def test_tar_node_bin_symlink_is_validated_then_omitted(self):
        archive = self.root / "node-bin.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            payload = b"#!/usr/bin/env node\n"
            target = tarfile.TarInfo("./package/node_modules/tool/bin/tool.js")
            target.size = len(payload)
            target.mode = 0o755
            stream.addfile(target, io.BytesIO(payload))
            link = tarfile.TarInfo("./package/node_modules/.bin/tool")
            link.type = tarfile.SYMTYPE
            link.linkname = "../tool/bin/tool.js"
            stream.addfile(link)
        omitted = "node_modules/.bin/tool\t../tool/bin/tool.js\n"
        contract = {
            "kind": "tar",
            "strip_leading_dirs": "package",
            "omitted_symlink_count": 1,
            "omitted_symlink_sha256": hashlib.sha256(
                omitted.encode("utf-8")
            ).hexdigest(),
        }
        entries = prepare_source.inspect_archive(
            archive, contract
        )
        self.assertEqual(
            ["node_modules/tool/bin/tool.js"], [item[0] for item in entries]
        )
        destination = self.root / "materialized"
        prepare_source.extract_archive_to_stage(archive, contract, destination)
        self.assertFalse((destination / "node_modules/.bin/tool").exists())
        self.assertEqual(
            payload,
            (destination / "node_modules/tool/bin/tool.js").read_bytes(),
        )

    def test_tar_node_modules_hardlink_is_materialized(self):
        archive = self.root / "hardlink.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            payload = b"license\n"
            target = tarfile.TarInfo("./node_modules/pkg/LICENSE")
            target.size = len(payload)
            target.mode = 0o644
            stream.addfile(target, io.BytesIO(payload))
            link = tarfile.TarInfo("./node_modules/pkg/COPYING")
            link.type = tarfile.LNKTYPE
            link.linkname = "./node_modules/pkg/LICENSE"
            stream.addfile(link)
        destination = self.root / "hardlink-materialized"
        prepare_source.extract_archive_to_stage(
            archive,
            {"kind": "tar", "strip_leading_dirs": None},
            destination,
        )
        copied = destination / "node_modules/pkg/COPYING"
        self.assertTrue(copied.is_file())
        self.assertFalse(copied.is_symlink())
        self.assertEqual(payload, copied.read_bytes())

    def test_tar_link_target_cannot_escape_archive_or_node_modules(self):
        archive = self.root / "escaping-link.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            link = tarfile.TarInfo("./node_modules/.bin/escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../../outside"
            stream.addfile(link)
        with self.assertRaises(prepare_source.PreparationError):
            prepare_source.inspect_archive(
                archive, {"kind": "tar", "strip_leading_dirs": None}
            )

    def test_archive_extract_and_merge_uses_declared_output(self):
        source = self.root / "src"
        source.mkdir()
        stage = self.root / "stage"
        archive = self.make_zip()
        contract = {
            "kind": "zip",
            "strip_leading_dirs": "root",
            "output_path": "third_party/example",
        }
        prepare_source.extract_archive_to_stage(archive, contract, stage / "dep")
        report = prepare_source.merge_staged_dependencies(
            source, stage, OrderedDict((("dep", contract),))
        )
        self.assertEqual(1, report["files_copied"])
        self.assertEqual(
            b"payload", (source / "third_party/example/file.txt").read_bytes()
        )

    def test_dependency_merge_rejects_preexisting_extra_or_empty_child(self):
        contract = OrderedDict(
            (("dep", {"output_path": "third_party/example"}),)
        )
        source = self.root / "owned-src"
        source.mkdir()
        root = source / "third_party/example"
        root.mkdir(parents=True)
        (root / "extra.txt").write_text("extra", encoding="utf-8")
        with self.assertRaisesRegex(prepare_source.PreparationError, "not empty"):
            prepare_source.require_empty_dependency_roots(source, contract)
        (root / "extra.txt").unlink()
        (root / "empty-child").mkdir()
        with self.assertRaisesRegex(prepare_source.PreparationError, "not empty"):
            prepare_source.require_empty_dependency_roots(source, contract)

    def test_dependency_merge_rejects_cross_contract_path_collision(self):
        source = self.root / "collision-src"
        source.mkdir()
        stage = self.root / "collision-stage"
        (stage / "first").mkdir(parents=True)
        (stage / "second/child").mkdir(parents=True)
        (stage / "first/file").write_bytes(b"one")
        (stage / "second/child/file").write_bytes(b"two")
        contracts = OrderedDict(
            (
                ("first", {"output_path": "third_party/example"}),
                ("second", {"output_path": "third_party/example/file"}),
            )
        )
        with self.assertRaisesRegex(prepare_source.PreparationError, "colliding"):
            prepare_source.merge_staged_dependencies(source, stage, contracts)

    def test_dependency_cache_marker_binds_exact_order_path_size_and_hash(self):
        cache = self.root / "marker-cache"
        cache.mkdir()
        payload = b"archive"
        archive = cache / "dep.tgz"
        archive.write_bytes(payload)
        contracts = OrderedDict(
            (
                (
                    "dep",
                    {
                        "download_filename": "dep.tgz",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    },
                ),
            )
        )
        marker = cache / prepare_source.DEPENDENCY_CACHE_MARKER
        marker.write_text(
            json.dumps(
                {
                    "archives": [
                        {
                            "bytes": len(payload),
                            "name": "dep",
                            "path": str(archive.resolve()),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    ],
                    "deps_ini_sha256": prepare_source.DEPS_INI_SHA256,
                    "source_mutated": False,
                    "unpacked": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        report = prepare_source.validate_dependency_cache_marker(cache, contracts)
        self.assertEqual(1, report["archive_count"])
        changed = json.loads(marker.read_text(encoding="utf-8"))
        changed["archives"][0]["bytes"] += 1
        marker.write_text(json.dumps(changed) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(prepare_source.PreparationError, "inventory"):
            prepare_source.validate_dependency_cache_marker(cache, contracts)

    def test_onboarding_strings_are_generated_twice_byte_identically(self):
        source = self.root / "generator-src"
        generator = source / prepare_source.ONBOARDING_GENERATOR
        generator.parent.mkdir(parents=True)
        generator.write_bytes(b"generator fixture\n")
        output = source / prepare_source.ONBOARDING_STRINGS_OUTPUT
        output.parent.mkdir(parents=True)
        output.write_bytes(b"deterministic output\n")
        calls = []

        def runner(*_args, **_kwargs):
            calls.append(True)
            output.write_bytes(b"deterministic output\n")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(
            prepare_source,
            "ONBOARDING_GENERATOR_SHA256",
            hashlib.sha256(b"generator fixture\n").hexdigest(),
        ), mock.patch.object(
            prepare_source,
            "ONBOARDING_STRINGS_BASELINE_BYTES",
            len(b"deterministic output\n"),
        ), mock.patch.object(
            prepare_source,
            "ONBOARDING_STRINGS_BASELINE_SHA256",
            hashlib.sha256(b"deterministic output\n").hexdigest(),
        ), mock.patch.object(
            prepare_source,
            "onboarding_node_contract",
            return_value={"path": "/fixed/node", "architecture": "arm64"},
        ):
            report = prepare_source.generate_onboarding_strings(
                source, runner=runner
            )
        self.assertEqual(2, len(calls))
        self.assertTrue(report["byte_identical"])
        self.assertEqual(
            hashlib.sha256(b"deterministic output\n").hexdigest(),
            report["output_sha256"],
        )

    def write_pruning_manifest(self, text):
        manifest = self.root / "pruning.list"
        manifest.write_text(text, encoding="utf-8")
        return manifest, hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_pruning_manifest_hash_count_and_uniqueness_are_pinned(self):
        manifest = prepare_source.PRUNING_LIST
        self.assertEqual(
            prepare_source.PRUNING_LIST_SHA256,
            prepare_source.sha256_file(manifest),
        )
        entries = [line for line in manifest.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(prepare_source.PRUNING_ENTRY_COUNT, len(entries))
        self.assertEqual(len(entries), len(set(entries)))

        absent = prepare_source.load_expected_absent_pruning()
        self.assertEqual(prepare_source.PRUNING_ALREADY_ABSENT_COUNT, len(absent))
        self.assertEqual(len(absent), len(set(absent)))
        self.assertEqual(
            prepare_source.PRUNING_ALREADY_ABSENT_SHA256,
            prepare_source.sha256_file(
                prepare_source.PRUNING_ALREADY_ABSENT_LIST
            ),
        )

    def test_pruning_removes_only_exact_prevalidated_files(self):
        source = self.root / "src"
        listed = source / "data/remove.bin"
        retained = source / "data/keep.pydeps"
        listed.parent.mkdir(parents=True)
        listed.write_bytes(b"binary")
        retained.write_text("keep", encoding="utf-8")
        manifest, digest = self.write_pruning_manifest("data/remove.bin\n")
        plan = prepare_source.build_prune_plan(
            source, manifest=manifest, expected_hash=digest, expected_count=1
        )
        report = prepare_source.apply_prune_plan(source, plan)
        self.assertFalse(listed.exists())
        self.assertTrue(retained.is_file())
        self.assertTrue(listed.parent.is_dir())
        self.assertEqual(1, report["files_removed"])
        self.assertFalse(report["contingent_paths_pruned"])
        self.assertFalse(report["directory_pruning_executed"])

    def test_pruning_fails_closed_on_missing_target(self):
        source = self.root / "src"
        source.mkdir()
        manifest, digest = self.write_pruning_manifest("missing.bin\n")
        with self.assertRaisesRegex(prepare_source.PreparationError, "missing regular"):
            prepare_source.build_prune_plan(
                source, manifest=manifest, expected_hash=digest, expected_count=1
            )

    def test_pruning_preflight_allows_only_exact_archive_proven_path(self):
        source = self.root / "src"
        source.mkdir()
        manifest, digest = self.write_pruning_manifest("future/remove.bin\n")
        plan = prepare_source.build_prune_plan(
            source,
            manifest=manifest,
            expected_hash=digest,
            expected_count=1,
            allowed_missing={"future/remove.bin"},
        )
        self.assertTrue(plan[0]["future_archive_file"])
        with self.assertRaisesRegex(
            prepare_source.PreparationError, "unmaterialized archive"
        ):
            prepare_source.apply_prune_plan(source, plan)
        with self.assertRaisesRegex(prepare_source.PreparationError, "missing regular"):
            prepare_source.build_prune_plan(
                source,
                manifest=manifest,
                expected_hash=digest,
                expected_count=1,
                allowed_missing={"different/path.bin"},
            )

    def test_pruning_skips_only_exact_pinned_absence_set(self):
        source = self.root / "src"
        source.mkdir()
        manifest, digest = self.write_pruning_manifest("expected/missing.bin\n")
        plan = prepare_source.build_prune_plan(
            source,
            manifest=manifest,
            expected_hash=digest,
            expected_count=1,
            expected_absent_paths=("expected/missing.bin",),
        )
        self.assertTrue(plan[0]["already_absent"])
        with self.assertRaisesRegex(
            prepare_source.PreparationError, "absence set changed"
        ):
            prepare_source.apply_prune_plan(source, plan)
        report = prepare_source.apply_prune_plan(
            source, plan, expected_absent_paths=("expected/missing.bin",)
        )
        self.assertEqual(0, report["files_removed"])
        self.assertEqual(1, report["already_absent_files"])
        self.assertEqual(
            hashlib.sha256(b"expected/missing.bin\n").hexdigest(),
            report["already_absent_sha256"],
        )
        with self.assertRaisesRegex(
            prepare_source.PreparationError, "missing regular"
        ):
            prepare_source.build_prune_plan(
                source,
                manifest=manifest,
                expected_hash=digest,
                expected_count=1,
                expected_absent_paths=("different/missing.bin",),
            )
        with self.assertRaisesRegex(prepare_source.PreparationError, "both future"):
            prepare_source.build_prune_plan(
                source,
                manifest=manifest,
                expected_hash=digest,
                expected_count=1,
                allowed_missing={"expected/missing.bin"},
                expected_absent_paths=("expected/missing.bin",),
            )

    def test_pruning_fails_closed_on_symlink_target(self):
        source = self.root / "src"
        source.mkdir()
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside")
        (source / "linked.bin").symlink_to(outside)
        manifest, digest = self.write_pruning_manifest("linked.bin\n")
        with self.assertRaisesRegex(prepare_source.PreparationError, "symlink"):
            prepare_source.build_prune_plan(
                source, manifest=manifest, expected_hash=digest, expected_count=1
            )
        self.assertEqual(b"outside", outside.read_bytes())

    def test_pruning_fails_closed_on_escape_entry(self):
        source = self.root / "src"
        source.mkdir()
        manifest, digest = self.write_pruning_manifest("../escape.bin\n")
        with self.assertRaisesRegex(prepare_source.PreparationError, "unsafe"):
            prepare_source.build_prune_plan(
                source, manifest=manifest, expected_hash=digest, expected_count=1
            )

    def test_atomic_copy_rejects_destination_symlink(self):
        source = self.root / "source"
        source.write_text("safe", encoding="utf-8")
        outside = self.root / "outside"
        outside.write_text("outside", encoding="utf-8")
        destination = self.root / "destination"
        destination.symlink_to(outside)
        with self.assertRaisesRegex(prepare_source.PreparationError, "symlink"):
            prepare_source.atomic_copy(source, destination)
        self.assertEqual("outside", outside.read_text(encoding="utf-8"))

    def test_atomic_text_publication_never_leaves_partial_final_path(self):
        directory = self.root / "publication"
        directory.mkdir()
        destination = directory / "receipt.json"
        with mock.patch.object(
            prepare_source.os, "fsync", side_effect=OSError("injected write failure")
        ), self.assertRaises(OSError):
            prepare_source.atomic_publish_text(destination, "complete\n")
        self.assertFalse(destination.exists())
        self.assertEqual([], list(directory.glob(".receipt.json-*.tmp")))

        prepare_source.atomic_publish_text(destination, "complete\n")
        self.assertEqual("complete\n", destination.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(prepare_source.PreparationError, "overwrite"):
            prepare_source.atomic_publish_text(destination, "replacement\n")
        self.assertEqual("complete\n", destination.read_text(encoding="utf-8"))

    def test_complete_patch_plan_is_321_common_then_3_platform(self):
        plan = prepare_source.build_patch_plan()
        self.assertEqual(324, len(plan))
        self.assertTrue(str(plan[-1]).endswith("native-incognito-contract.patch"))
        self.assertNotIn(
            "windows-first-run-locale.patch", "\n".join(str(path) for path in plan)
        )

    def test_patch_command_uses_system_patch_check_and_exact_zero_fuzz(self):
        command = prepare_source.patch_command(
            prepare_source.SYSTEM_PATCH, Path("/patch.patch"), Path("/src"), True
        )
        self.assertEqual("/usr/bin/patch", command[0])
        self.assertIn("-C", command)
        self.assertIn("-E", command)
        self.assertEqual("0", command[command.index("-F") + 1])
        self.assertNotIn("--ignore-whitespace", command)

        patch = self.root / "reverse-command.patch"
        patch.write_text("fixture\n", encoding="utf-8")
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )
        prepare_source.check_patch_boundary(
            self.root, patch, reverse=True, runner=runner
        )
        reverse_command = runner.call_args.args[0]
        self.assertEqual(str(prepare_source.SYSTEM_PATCH), reverse_command[0])
        self.assertIn("-C", reverse_command)
        self.assertIn("-R", reverse_command)
        self.assertIn("-E", reverse_command)
        self.assertNotIn("apply", reverse_command)

    def test_system_patch_applies_fixture_after_check_only_pass(self):
        source = self.root / "patch-src"
        source.mkdir()
        (source / "example.txt").write_text("old\n", encoding="utf-8")
        patch = self.root / "fixture.patch"
        patch.write_text(
            "--- a/example.txt\n"
            "+++ b/example.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n",
            encoding="utf-8",
        )
        applied = prepare_source.apply_patch_plan(source, [patch])
        self.assertEqual([str(patch)], applied)
        self.assertEqual("new\n", (source / "example.txt").read_text(encoding="utf-8"))
        self.assertFalse((source / "example.txt.orig").exists())

    def test_system_patch_full_delete_create_forward_topology(self):
        source = self.root / "rename-src"
        source.mkdir()
        old = source / "old.txt"
        new = source / "new.txt"
        old.write_text("old first\nold second\n", encoding="utf-8")
        patch = self.root / "delete-create.patch"
        patch.write_text(
            "--- a/old.txt\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-old first\n"
            "-old second\n"
            "--- /dev/null\n"
            "+++ b/new.txt\n"
            "@@ -0,0 +1,2 @@\n"
            "+new first\n"
            "+new second\n",
            encoding="utf-8",
        )

        prepare_source.check_patch_boundary(source, patch)
        self.assertEqual("old first\nold second\n", old.read_text(encoding="utf-8"))
        self.assertFalse(new.exists())

        self.assertEqual(
            [str(patch)], prepare_source.apply_patch_plan(source, [patch])
        )
        self.assertFalse(old.exists())
        self.assertEqual("new first\nnew second\n", new.read_text(encoding="utf-8"))
        reverse_runner = mock.Mock(side_effect=subprocess.run)
        reverse = prepare_source.check_patch_boundary(
            source, patch, reverse=True, runner=reverse_runner
        )
        self.assertEqual("reverse", reverse["direction"])
        reverse_command = reverse_runner.call_args.args[0]
        self.assertEqual(str(prepare_source.SYSTEM_GIT), reverse_command[0])
        self.assertEqual("apply", reverse_command[3])
        self.assertIn("--check", reverse_command)
        self.assertIn("--reverse", reverse_command)
        self.assertNotIn("--unsafe-paths", reverse_command)
        self.assertFalse(old.exists())
        self.assertEqual("new first\nnew second\n", new.read_text(encoding="utf-8"))

    def test_explicit_deletion_detector_ignores_header_like_hunk_payload(self):
        patch = self.root / "header-like-payload.patch"
        patch.write_text(
            "diff --git a/value.txt b/value.txt\n"
            "--- a/value.txt\n"
            "+++ b/value.txt\n"
            "@@ -1 +1 @@\n"
            "--- /dev/null\n"
            "+++ /dev/null\n",
            encoding="utf-8",
        )
        self.assertFalse(prepare_source.patch_has_paired_explicit_deletion(patch))

    def test_git_reverse_handles_one_deletion_and_two_creations(self):
        source = self.root / "uneven-delete-create-src"
        source.mkdir()
        old = source / "old.txt"
        first = source / "first.txt"
        second = source / "second.txt"
        old.write_text("old\n", encoding="utf-8")
        patch = self.root / "uneven-delete-create.patch"
        patch.write_text(
            "--- a/old.txt\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-old\n"
            "--- /dev/null\n"
            "+++ b/first.txt\n"
            "@@ -0,0 +1 @@\n"
            "+first\n"
            "--- /dev/null\n"
            "+++ b/second.txt\n"
            "@@ -0,0 +1 @@\n"
            "+second\n",
            encoding="utf-8",
        )

        self.assertTrue(prepare_source.patch_has_paired_explicit_deletion(patch))
        prepare_source.apply_patch_plan(source, [patch])
        self.assertFalse(old.exists())
        self.assertEqual("first\n", first.read_text(encoding="utf-8"))
        self.assertEqual("second\n", second.read_text(encoding="utf-8"))

        reverse_runner = mock.Mock(side_effect=subprocess.run)
        prepare_source.check_patch_boundary(
            source, patch, reverse=True, runner=reverse_runner
        )
        reverse_command = reverse_runner.call_args.args[0]
        self.assertEqual(str(prepare_source.SYSTEM_GIT), reverse_command[0])
        self.assertIn("--check", reverse_command)
        self.assertIn("--reverse", reverse_command)
        self.assertFalse(old.exists())
        self.assertEqual("first\n", first.read_text(encoding="utf-8"))
        self.assertEqual("second\n", second.read_text(encoding="utf-8"))

    def test_patch_failure_leaves_current_patch_unapplied(self):
        source = self.root / "patch-src"
        source.mkdir()
        target = source / "example.txt"
        target.write_text("different\n", encoding="utf-8")
        patch = self.root / "fixture.patch"
        patch.write_text(
            "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+new\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(prepare_source.PreparationError, "during check"):
            prepare_source.apply_patch_plan(source, [patch])
        self.assertEqual("different\n", target.read_text(encoding="utf-8"))

    def test_filtered_overlay_inventory_has_no_windows_path(self):
        files, cleanup, prefixes = prepare_source.build_overlay_plan()
        self.assertEqual(2531, len(files))
        self.assertEqual(20, len(cleanup))
        self.assertEqual(3, len(prefixes))
        for _, relative in files:
            self.assertNotIn("/win/", "/{}/".format(relative.lower()))

    def test_overlay_cleanup_and_copy_are_scoped_to_source(self):
        source = self.root / "src"
        source.mkdir()
        stale = source / "old.txt"
        stale.write_text("old", encoding="utf-8")
        overlay = self.root / "new.txt"
        overlay.write_text("new", encoding="utf-8")
        report = prepare_source.apply_overlay(
            source, [(overlay, "nested/new.txt")], ["old.txt", "missing.txt"]
        )
        self.assertFalse(stale.exists())
        self.assertEqual("new", (source / "nested/new.txt").read_text(encoding="utf-8"))
        self.assertEqual(1, report["cleanup_removed"])
        self.assertEqual(1, report["cleanup_missing"])

    def test_common_resource_plan_is_full_body_hash_pinned(self):
        plan = prepare_source.parse_resource_plan()
        self.assertEqual(prepare_source.RESOURCE_BODY_COUNT, len(plan))
        self.assertEqual(len(plan), len({destination for _, destination in plan}))

    def test_focus_version_is_appended_once(self):
        source = self.root / "src"
        version = source / "chrome/VERSION"
        version.parent.mkdir(parents=True)
        version.write_text(
            "MAJOR=150\nMINOR=0\nBUILD=7871\nPATCH=128\n", encoding="utf-8"
        )
        self.assertEqual("1.0.5.0", prepare_source.append_focus_version_once(source))
        text = version.read_text(encoding="utf-8")
        self.assertEqual(1, text.count("FOCUS_MAJOR="))
        with self.assertRaises(ValueError):
            prepare_source.append_focus_version_once(source)

    def test_icns_install_requires_build_path_and_focus_branding(self):
        source = self.root / "src"
        build_file = source / "chrome/BUILD.gn"
        build_file.parent.mkdir(parents=True)
        build_file.write_text(
            'sources = [ "app/theme/$branding_path_component/mac/app.icns" ]\n',
            encoding="utf-8",
        )
        icon = source / prepare_source.MAC_ICON_DESTINATION
        icon.parent.mkdir(parents=True)
        icon.write_bytes(b"old")
        branding = source / "chrome/app/theme/chromium/BRANDING"
        branding.write_text(
            "PRODUCT_FULLNAME=Focus Browser\n"
            "PRODUCT_SHORTNAME=Focus Browser\n"
            "MAC_BUNDLE_ID=com.focusbrowser.browser\n",
            encoding="utf-8",
        )
        installed = prepare_source.install_focus_icns(source)
        self.assertEqual(str(icon.resolve()), installed)
        self.assertEqual(
            prepare_source.focus_macos.FOCUS_ICNS_SHA256,
            prepare_source.sha256_file(icon),
        )

    def test_icns_install_rejects_unbranded_source(self):
        source = self.root / "src"
        build_file = source / "chrome/BUILD.gn"
        build_file.parent.mkdir(parents=True)
        build_file.write_text(prepare_source.MAC_ICON_BUILD_TOKEN, encoding="utf-8")
        icon = source / prepare_source.MAC_ICON_DESTINATION
        icon.parent.mkdir(parents=True)
        icon.write_bytes(b"old")
        (source / "chrome/app/theme/chromium/BRANDING").write_text(
            "PRODUCT_FULLNAME=Chromium\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(prepare_source.PreparationError, "branding"):
            prepare_source.install_focus_icns(source)

    def test_args_gn_are_written_for_both_architectures_without_overwrite(self):
        source = self.root / "src"
        source.mkdir()
        plan = OrderedDict(
            (
                ("arm64", ("out/Arm", 'target_cpu="arm64"\n')),
                ("x64", ("out/X64", 'target_cpu="x64"\n')),
            )
        )
        paths = prepare_source.write_args_gn(source, plan)
        self.assertEqual('target_cpu="arm64"\n', Path(paths["arm64"]).read_text())
        with self.assertRaisesRegex(prepare_source.PreparationError, "overwrite"):
            prepare_source.write_args_gn(source, plan)

    def test_receipt_records_post_hashes_and_no_build_or_network(self):
        source = self.root / "src"
        fixture_files = {
            "chrome/BUILD.gn": "build\n",
            prepare_source.INSTALLER_MAC_BUILD_GN: "installer\n",
            "chrome/app/theme/chromium/BRANDING": "brand\n",
            "chrome/VERSION": "version\n",
            prepare_source.MAC_ICON_DESTINATION: "icon\n",
            prepare_source.ONBOARDING_STRINGS_OUTPUT: (
                prepare_source.REPO_ROOT
                / "source_overrides"
                / prepare_source.ONBOARDING_STRINGS_OUTPUT
            ).read_text(encoding="utf-8"),
            "out/Arm/args.gn": "arm\n",
            "out/X64/args.gn": "x64\n",
        }
        for relative, content in fixture_files.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        dependency_report = {
            "ownership_roots": list(prepare_source.DEPENDENCY_OWNERSHIP_ROOTS),
            "regular_files": prepare_source.DEPENDENCY_INSTALL_REGULAR_FILES,
            "logical_bytes": prepare_source.DEPENDENCY_INSTALL_LOGICAL_BYTES,
            "sha256": prepare_source.DEPENDENCY_INSTALL_SHA256,
            "installed_symlinks": 0,
            "installed_special_files": 0,
            "files_copied": prepare_source.DEPENDENCY_INSTALL_REGULAR_FILES,
            "components": list(prepare_source.DEPENDENCY_CONTRACTS),
            "omitted_symlinks": {
                "onboarding": {
                    "count": 10,
                    "sha256": prepare_source.SHARED_DEPENDENCY_CONTRACTS[
                        "onboarding"
                    ]["omitted_symlink_sha256"],
                }
            },
        }
        post_tree = {
            key: dependency_report[key]
            for key in (
                "ownership_roots",
                "regular_files",
                "logical_bytes",
                "sha256",
                "installed_symlinks",
                "installed_special_files",
            )
        }
        localized_report = {
            "generator": prepare_source.ONBOARDING_GENERATOR,
            "generator_sha256": prepare_source.ONBOARDING_GENERATOR_SHA256,
            "node": {"architecture": "arm64"},
            "output": prepare_source.ONBOARDING_STRINGS_OUTPUT,
            "baseline_bytes": prepare_source.ONBOARDING_STRINGS_BASELINE_BYTES,
            "baseline_sha256": prepare_source.ONBOARDING_STRINGS_BASELINE_SHA256,
            "output_bytes": prepare_source.ONBOARDING_STRINGS_BASELINE_BYTES,
            "output_sha256": prepare_source.ONBOARDING_STRINGS_BASELINE_SHA256,
            "runs": 2,
            "byte_identical": True,
            "network_operations": 0,
        }
        preflight = {
                "acquisition": {
                    "status": "acquisition_complete",
                    "sha256": "b" * 64,
                },
                "tool_bootstrap": {
                    "hooks_complete": True,
                    "sha256": "c" * 64,
                },
                "upstream_baseline_sha256": {"chrome/BUILD.gn": "a" * 64},
                "dependency_cache_marker": {
                    "path": "/cache/.focus-project-dependencies.json",
                    "sha256": "d" * 64,
                    "archive_count": 10,
                    "total_bytes": 1,
                    "archives": {
                        name: value["sha256"]
                        for name, value in prepare_source.DEPENDENCY_CONTRACTS.items()
                    },
                },
            }
        with mock.patch.object(
            prepare_source, "installed_dependency_tree", return_value=post_tree
        ):
            report = prepare_source.write_preparation_receipt(
            source,
            preflight,
            {
                "arm64": str((source / "out/Arm/args.gn").resolve()),
                "x64": str((source / "out/X64/args.gn").resolve()),
            },
            {
                "files_removed": prepare_source.PRUNING_EXPECTED_REMOVAL_COUNT,
                "already_absent_files": prepare_source.PRUNING_ALREADY_ABSENT_COUNT,
                "already_absent_sha256": prepare_source.PRUNING_ALREADY_ABSENT_SHA256,
            },
            dependency_report,
            localized_report,
            )
        receipt = Path(report["path"])
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(prepare_source.PREPARATION_RECEIPT_SCHEMA, payload["schema"])
        self.assertEqual(
            prepare_source.fresh_preparation_execution_report(),
            payload["preparation_execution"],
        )
        self.assertTrue(payload["offline"])
        self.assertEqual(0, payload["network_operations"])
        self.assertFalse(payload["build_executed"])
        self.assertEqual(
            hashlib.sha256(b"arm\n").hexdigest(),
            payload["post_prepare_sha256"]["args_gn/arm64"],
        )
        with self.assertRaisesRegex(prepare_source.PreparationError, "already exists"):
            prepare_source.write_preparation_receipt(
                source,
                {
                    "acquisition": {},
                    "tool_bootstrap": {},
                    "upstream_baseline_sha256": {},
                },
                {
                    "arm64": str(source / "out/Arm/args.gn"),
                    "x64": str(source / "out/X64/args.gn"),
                },
                {
                    "files_removed": prepare_source.PRUNING_EXPECTED_REMOVAL_COUNT,
                    "already_absent_files": prepare_source.PRUNING_ALREADY_ABSENT_COUNT,
                    "already_absent_sha256": prepare_source.PRUNING_ALREADY_ABSENT_SHA256,
                },
                dependency_report,
                localized_report,
            )

    def test_common_transformations_run_in_domain_name_i18n_order(self):
        source = self.root / "src"
        source.mkdir()
        calls = []
        domain_plan = (
            {
                "listed": 3,
                "regular": 2,
                "expected_absent": 1,
                "expected_absent_bytes": 10,
                "expected_absent_sha256": "a" * 64,
            },
            b"regex\n",
            b"one\ntwo\n",
            "b" * 64,
        )
        with mock.patch.object(
            prepare_source,
            "validate_domain_targets",
            return_value=domain_plan,
        ), mock.patch.object(
            prepare_source.domain_substitution,
            "apply_substitution",
            side_effect=lambda *args: calls.append("domain"),
        ), mock.patch.object(
            prepare_source, "validate_name_targets", return_value=4
        ), mock.patch.object(
            prepare_source.name_substitution, "replacement_sanity"
        ), mock.patch.object(
            prepare_source.name_substitution,
            "do_substitution",
            side_effect=lambda *args, **kwargs: calls.append("name"),
        ), mock.patch.object(
            prepare_source, "validate_i18n_targets", return_value=(5, 6)
        ), mock.patch.object(
            prepare_source.i18n_apply,
            "apply_translations",
            side_effect=lambda *args: calls.append("i18n"),
        ):
            report = prepare_source.apply_common_transformations(source, workers=1)
        self.assertEqual(["domain", "name", "i18n"], calls)
        self.assertEqual(3, report["domain_targets"])
        self.assertEqual(2, report["domain_regular_targets"])
        self.assertEqual(1, report["domain_expected_absent_targets"])
        self.assertEqual(6, report["i18n_xtb_targets"])

    def test_domain_targets_accept_only_exact_pinned_macos_absence(self):
        source = self.root / "domain-source"
        source.mkdir()
        (source / "present.txt").write_text("present\n", encoding="utf-8")
        regex_path = self.root / "domain-regex.list"
        files_path = self.root / "domain-files.list"
        regex_path.write_text("example\\.com#example.invalid\n", encoding="utf-8")
        files_path.write_text("present.txt\nmissing.txt\n", encoding="utf-8")
        missing_body = b"missing.txt\n"

        constants = {
            "DOMAIN_LIST_ENTRY_COUNT": 2,
            "MACOS_DOMAIN_REGULAR_TARGET_COUNT": 1,
            "MACOS_DOMAIN_MISSING_TARGET_COUNT": 1,
            "MACOS_DOMAIN_MISSING_MANIFEST_BYTES": len(missing_body),
            "MACOS_DOMAIN_MISSING_MANIFEST_SHA256": hashlib.sha256(
                missing_body
            ).hexdigest(),
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "_read_pinned_file_snapshot",
                    side_effect=lambda *args: (
                        (regex_path, regex_path.read_bytes())
                        if "regex" in args[2]
                        else (files_path, files_path.read_bytes())
                    ),
                )
            )
            for name, value in constants.items():
                stack.enter_context(mock.patch.object(prepare_source, name, value))
            report, _, _, _ = prepare_source.validate_domain_targets(source)
            self.assertEqual(
                {
                    "listed": 2,
                    "regular": 1,
                    "expected_absent": 1,
                    "expected_absent_bytes": len(missing_body),
                    "expected_absent_sha256": hashlib.sha256(
                        missing_body
                    ).hexdigest(),
                },
                report,
            )

            (source / "missing.txt").write_text("unexpected\n", encoding="utf-8")
            with self.assertRaisesRegex(
                prepare_source.PreparationError, "inventory changed"
            ):
                prepare_source.validate_domain_targets(source)

    def test_domain_targets_reject_unexpected_missing_and_symlink(self):
        source = self.root / "domain-drift-source"
        source.mkdir()
        (source / "present.txt").write_text("present\n", encoding="utf-8")
        regex_path = self.root / "drift-regex.list"
        files_path = self.root / "drift-files.list"
        regex_path.write_text("regex\n", encoding="utf-8")
        files_path.write_text("present.txt\nmissing.txt\n", encoding="utf-8")
        missing_body = b"missing.txt\n"
        constants = {
            "DOMAIN_LIST_ENTRY_COUNT": 2,
            "MACOS_DOMAIN_REGULAR_TARGET_COUNT": 1,
            "MACOS_DOMAIN_MISSING_TARGET_COUNT": 1,
            "MACOS_DOMAIN_MISSING_MANIFEST_BYTES": len(missing_body),
            "MACOS_DOMAIN_MISSING_MANIFEST_SHA256": hashlib.sha256(
                missing_body
            ).hexdigest(),
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "_read_pinned_file_snapshot",
                    side_effect=lambda *args: (
                        (regex_path, regex_path.read_bytes())
                        if "regex" in args[2]
                        else (files_path, files_path.read_bytes())
                    ),
                )
            )
            for name, value in constants.items():
                stack.enter_context(mock.patch.object(prepare_source, name, value))

            (source / "present.txt").unlink()
            with self.assertRaisesRegex(
                prepare_source.PreparationError, "inventory changed"
            ):
                prepare_source.validate_domain_targets(source)

            (source / "present.txt").symlink_to("missing.txt")
            with self.assertRaisesRegex(
                prepare_source.PreparationError, "symlink"
            ):
                prepare_source.validate_domain_targets(source)

    def test_common_transformations_reject_domain_skip_warning(self):
        source = self.root / "domain-warning-source"
        source.mkdir()
        inventory = {
            "listed": 1,
            "regular": 1,
            "expected_absent": 0,
            "expected_absent_bytes": 0,
            "expected_absent_sha256": hashlib.sha256(b"").hexdigest(),
        }
        plan = (inventory, b"regex\n", b"target.txt\n", "a" * 64)
        logger = prepare_source.domain_substitution.get_logger()

        def warn_about_skip(*args):
            logger.warning("Skipping non-existent path: target.txt")

        with mock.patch.object(
            prepare_source, "validate_domain_targets", return_value=plan
        ), mock.patch.object(
            prepare_source.domain_substitution,
            "apply_substitution",
            side_effect=warn_about_skip,
        ):
            with self.assertRaisesRegex(
                prepare_source.PreparationError, "skipped a validated target"
            ):
                prepare_source.apply_common_transformations(source)

    def test_common_transformations_reject_domain_identity_race(self):
        source = self.root / "domain-identity-source"
        source.mkdir()
        inventory = {
            "listed": 1,
            "regular": 1,
            "expected_absent": 0,
            "expected_absent_bytes": 0,
            "expected_absent_sha256": hashlib.sha256(b"").hexdigest(),
        }
        before = (inventory, b"regex\n", b"target.txt\n", "a" * 64)
        after = (inventory, b"regex\n", b"target.txt\n", "b" * 64)
        with mock.patch.object(
            prepare_source,
            "validate_domain_targets",
            side_effect=(before, after),
        ), mock.patch.object(
            prepare_source.domain_substitution, "apply_substitution"
        ):
            with self.assertRaisesRegex(
                prepare_source.PreparationError, "changed during substitution"
            ):
                prepare_source.apply_common_transformations(source)

    def test_prepare_gates_source_and_cache_before_every_phase_and_after(self):
        source = self.root / "source"
        cache = self.root / "cache"
        source.mkdir()
        cache.mkdir()
        phases = []

        def fake_gate(paths, phase):
            self.assertEqual((source, cache.resolve()), paths)
            phases.append(phase)
            return {"phase": phase}

        with mock.patch.object(
            prepare_source,
            "preflight",
            return_value={"source_root": str(source)},
        ), mock.patch.object(
            prepare_source, "validate_dependency_manifest", return_value=OrderedDict()
        ), mock.patch.object(
            prepare_source,
            "validate_offline_cache",
            return_value=(cache.resolve(), []),
        ), mock.patch.object(
            prepare_source, "merge_staged_dependencies", return_value={}
        ), mock.patch.object(
            prepare_source, "build_prune_plan", return_value=[]
        ), mock.patch.object(
            prepare_source, "apply_prune_plan", return_value={}
        ), mock.patch.object(
            prepare_source, "build_patch_plan", return_value=[]
        ), mock.patch.object(
            prepare_source, "apply_patch_plan", return_value=[]
        ), mock.patch.object(
            prepare_source, "apply_common_transformations", return_value={}
        ), mock.patch.object(
            prepare_source, "build_overlay_plan", return_value=([], [], [])
        ), mock.patch.object(
            prepare_source, "apply_overlay", return_value={}
        ), mock.patch.object(
            prepare_source, "append_focus_version_once", return_value="1.0.5.0"
        ), mock.patch.object(
            prepare_source, "parse_resource_plan", return_value=[]
        ), mock.patch.object(
            prepare_source, "copy_common_resources", return_value=0
        ), mock.patch.object(
            prepare_source, "install_focus_icns", return_value="icon"
        ), mock.patch.object(
            prepare_source, "write_args_gn", return_value={}
        ), mock.patch.object(
            prepare_source, "generate_onboarding_strings", return_value={}
        ), mock.patch.object(
            prepare_source, "write_preparation_receipt", return_value={}
        ), mock.patch.object(
            prepare_source, "require_disk_floor", side_effect=fake_gate
        ):
            report = prepare_source.prepare(source, cache)

        self.assertEqual(
            [
                "dependency staging",
                "dependency merge",
                "file-only binary pruning",
                "324-patch batch",
                "domain/name/i18n transformations",
                "filtered overlay and cleanup",
                "Focus version append",
                "common resource copy",
                "pinned ICNS install",
                "arm64/x64 args.gn write",
                "deterministic onboarding strings generation",
                "preparation completion",
            ],
            phases,
        )
        self.assertEqual(len(phases), len(report["disk_gates"]))
        self.assertEqual(30, report["hard_disk_floor_gib"])

    def test_prepare_cli_requires_explicit_mutation_confirmation(self):
        with mock.patch("sys.stderr", new=io.StringIO()), self.assertRaises(
            SystemExit
        ) as context:
            prepare_source.main(
                ["prepare", "--source-root", str(self.root), "--cache", str(self.root)]
            )
        self.assertEqual(2, context.exception.code)

    def test_working_tree_inventory_hashes_mode_size_and_content(self):
        source = self.root / "git-source"
        subprocess.run(
            [str(prepare_source.SYSTEM_GIT), "init", str(source)],
            check=True,
            capture_output=True,
        )
        (source / "modified.txt").write_bytes(b"original\n")
        (source / "deleted.txt").write_bytes(b"delete me\n")
        (source / ".gitignore").write_text("ignored*\n", encoding="utf-8")
        subprocess.run(
            [str(prepare_source.SYSTEM_GIT), "-C", str(source), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                str(prepare_source.SYSTEM_GIT),
                "-C",
                str(source),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                "fixture",
            ],
            check=True,
            capture_output=True,
        )
        (source / "modified.txt").write_bytes(b"changed\n")
        (source / "deleted.txt").unlink()
        (source / "untracked.txt").write_bytes(b"new\n")
        (source / "ignored.bin").write_bytes(b"ignored\n")
        (source / "ignored-link").symlink_to("ignored.bin")
        report = prepare_source.working_tree_inventory(source)
        lines = []
        for status_value, relative, payload in (
            (" D", "deleted.txt", None),
            (" M", "modified.txt", b"changed\n"),
            ("??", "untracked.txt", b"new\n"),
        ):
            if payload is None:
                lines.append("{}\t{}\tABSENT\n".format(status_value, relative))
            else:
                path = source / relative
                lines.append(
                    "{}\t{}\t{:04o}\t{}\t{}\n".format(
                        status_value,
                        relative,
                        stat.S_IMODE(path.stat().st_mode),
                        len(payload),
                        hashlib.sha256(payload).hexdigest(),
                    )
                )
        self.assertEqual(3, report["records"])
        self.assertEqual(
            hashlib.sha256("".join(lines).encode("utf-8")).hexdigest(),
            report["sha256"],
        )

        ignored = prepare_source.ignored_working_tree_inventory(source)
        ignored_path = source / "ignored.bin"
        ignored_link = source / "ignored-link"
        link_target = b"ignored.bin"
        ignored_records = [
            (
                b"ignored-link",
                b"\0".join(
                    (
                        b"SYMLINK",
                        b"ignored-link",
                        "{:04o}".format(
                            stat.S_IMODE(ignored_link.lstat().st_mode)
                        ).encode("ascii"),
                        str(len(link_target)).encode("ascii"),
                        link_target,
                    )
                )
                + b"\n",
            ),
            (
                b"ignored.bin",
                b"\0".join(
                    (
                        b"REG",
                        b"ignored.bin",
                        "{:04o}".format(
                            stat.S_IMODE(ignored_path.stat().st_mode)
                        ).encode("ascii"),
                        str(ignored_path.stat().st_size).encode("ascii"),
                        hashlib.sha256(b"ignored\n").hexdigest().encode("ascii"),
                    )
                )
                + b"\n",
            ),
        ]
        ignored_body = b"".join(line for _, line in sorted(ignored_records))
        self.assertEqual(2, ignored["records"])
        self.assertEqual(1, ignored["regular_files"])
        self.assertEqual(1, ignored["symlinks"])
        self.assertEqual(hashlib.sha256(ignored_body).hexdigest(), ignored["sha256"])

    def test_resume_accepts_only_explicitly_audited_checkpoints(self):
        self.assertEqual(
            4673, prepare_source.expected_resume_working_tree(98)["records"]
        )
        self.assertEqual(
            4780, prepare_source.expected_resume_working_tree(138)["records"]
        )
        self.assertEqual(
            {
                "records": 5293,
                "sha256": "7225019e77e7eecddeaeaece124ccbf30957fa2a965b9020c56ec60d8664639e",
                "status_counts": {" M": 1219, " D": 3189, "??": 885},
            },
            prepare_source.expected_resume_working_tree(324),
        )
        self.assertEqual(
            {
                "ownership_roots": list(prepare_source.DEPENDENCY_OWNERSHIP_ROOTS),
                "regular_files": 13214,
                "logical_bytes": 527367518,
                "sha256": "38ebf05e4f17c4e8c2545bf9a93b446c0e182404d8e86617f0f811b60d8da0db",
                "installed_symlinks": 0,
                "installed_special_files": 0,
            },
            prepare_source.expected_resume_dependency_tree(138),
        )
        self.assertEqual(
            {
                "ownership_roots": list(prepare_source.DEPENDENCY_OWNERSHIP_ROOTS),
                "regular_files": 13217,
                "logical_bytes": 527368134,
                "sha256": "b6d7bc835bed4516a353590dc51da263acb2fa92a8970c35e8353856d6c35eeb",
                "installed_symlinks": 0,
                "installed_special_files": 0,
            },
            prepare_source.expected_resume_dependency_tree(324),
        )
        for value in (0, 97, 99, 137, 139, 323, 325):
            with self.subTest(value=value), self.assertRaisesRegex(
                prepare_source.PreparationError, "only audited"
            ):
                prepare_source.resume_preflight_exact(self.root, self.root, value)

    def test_checkpoint_138_prefix_boundaries_and_receipt_execution_validate(self):
        repository = self.root / "focus-repository"
        patch_root = repository / "patches"
        patch_root.mkdir(parents=True)
        patch_plan = []
        for position in range(1, 325):
            path = patch_root / "{:03d}.patch".format(position)
            path.write_text("# fixture {}\n".format(position), encoding="utf-8")
            patch_plan.append(path)

        source = self.root / "boundary-source"
        source.mkdir()
        (source / "value.txt").write_text("after-138\n", encoding="utf-8")
        patch_plan[137].write_text(
            "--- a/value.txt\n+++ b/value.txt\n"
            "@@ -1 +1 @@\n-before-138\n+after-138\n",
            encoding="utf-8",
        )
        patch_plan[138].write_text(
            "--- a/value.txt\n+++ b/value.txt\n"
            "@@ -1 +1 @@\n-after-138\n+after-139\n",
            encoding="utf-8",
        )
        prepare_source.check_patch_boundary(source, patch_plan[137], reverse=True)
        prepare_source.check_patch_boundary(source, patch_plan[138], reverse=False)

        with mock.patch.object(prepare_source, "REPO_ROOT", repository.resolve()):
            prefix = prepare_source.patch_slice_inventory(patch_plan, 0, 138)
            execution = {
                "mode": "resume_exact_prefix",
                "initial_applied_patch_count": 138,
                "patches_applied_this_run": 186,
                "total_patches": 324,
                "resume_checkpoint": {
                    "git_head": prepare_source.ACQUISITION_CHROMIUM_COMMIT,
                    "working_tree": prepare_source.expected_resume_working_tree(138),
                    "ignored_tree": (
                        prepare_source.expected_ignored_working_tree_inventory()
                    ),
                    "dependency_tree": (
                        prepare_source.expected_resume_dependency_tree(138)
                    ),
                    "pruning": {
                        "manifest_sha256": prepare_source.PRUNING_LIST_SHA256,
                        "listed_files": prepare_source.PRUNING_ENTRY_COUNT,
                        "all_targets_absent": True,
                        "absent_files": prepare_source.PRUNING_ENTRY_COUNT,
                        "symlink_targets": 0,
                    },
                    "applied_prefix": prefix,
                    "last_applied_patch": {
                        "position": 138,
                        "path": str(patch_plan[137]),
                        "sha256": prepare_source.sha256_file(patch_plan[137]),
                        "reverse_applicable": True,
                    },
                    "next_patch": {
                        "position": 139,
                        "path": str(patch_plan[138]),
                        "sha256": prepare_source.sha256_file(patch_plan[138]),
                        "forward_applicable": True,
                    },
                },
            }
            with mock.patch.object(
                prepare_source, "build_patch_plan", return_value=patch_plan
            ):
                receipt = {"preparation_execution": execution}
                self.assertEqual(
                    execution,
                    prepare_source.validate_preparation_execution_report(
                        receipt["preparation_execution"]
                    ),
                )
        self.assertEqual(138, prefix["count"])
        self.assertEqual(138, prefix["last_position"])
        self.assertEqual(186, execution["patches_applied_this_run"])

    def test_checkpoint_324_full_prefix_and_receipt_execution_validate(self):
        repository = self.root / "full-prefix-repository"
        patch_root = repository / "patches"
        patch_root.mkdir(parents=True)
        patch_plan = []
        for position in range(1, 325):
            path = patch_root / "{:03d}.patch".format(position)
            path.write_text("# fixture {}\n".format(position), encoding="utf-8")
            patch_plan.append(path)

        source = self.root / "full-prefix-boundary"
        source.mkdir()
        (source / "value.txt").write_text("after-324\n", encoding="utf-8")
        patch_plan[-1].write_text(
            "--- a/value.txt\n+++ b/value.txt\n"
            "@@ -1 +1 @@\n-before-324\n+after-324\n",
            encoding="utf-8",
        )
        prepare_source.check_patch_boundary(source, patch_plan[-1], reverse=True)

        with mock.patch.object(prepare_source, "REPO_ROOT", repository.resolve()):
            prefix = prepare_source.patch_slice_inventory(patch_plan, 0, 324)
            execution = {
                "mode": "resume_exact_prefix",
                "initial_applied_patch_count": 324,
                "patches_applied_this_run": 0,
                "total_patches": 324,
                "resume_checkpoint": {
                    "git_head": prepare_source.ACQUISITION_CHROMIUM_COMMIT,
                    "working_tree": prepare_source.expected_resume_working_tree(324),
                    "ignored_tree": (
                        prepare_source.expected_ignored_working_tree_inventory()
                    ),
                    "dependency_tree": (
                        prepare_source.expected_resume_dependency_tree(324)
                    ),
                    "pruning": {
                        "manifest_sha256": prepare_source.PRUNING_LIST_SHA256,
                        "listed_files": prepare_source.PRUNING_ENTRY_COUNT,
                        "all_targets_absent": True,
                        "absent_files": prepare_source.PRUNING_ENTRY_COUNT,
                        "symlink_targets": 0,
                    },
                    "applied_prefix": prefix,
                    "last_applied_patch": {
                        "position": 324,
                        "path": str(patch_plan[-1]),
                        "sha256": prepare_source.sha256_file(patch_plan[-1]),
                        "reverse_applicable": True,
                    },
                    "next_patch": None,
                },
            }
            with mock.patch.object(
                prepare_source, "build_patch_plan", return_value=patch_plan
            ):
                self.assertEqual(
                    execution,
                    prepare_source.validate_preparation_execution_report(execution),
                )
                tampered = json.loads(json.dumps(execution))
                tampered["resume_checkpoint"]["next_patch"] = {
                    "position": 325,
                    "path": "impossible.patch",
                    "sha256": "0" * 64,
                    "forward_applicable": True,
                }
                with self.assertRaisesRegex(
                    prepare_source.PreparationError, "next patch"
                ):
                    prepare_source.validate_preparation_execution_report(tampered)
        self.assertEqual(324, prefix["count"])
        self.assertEqual(1, prefix["first_position"])
        self.assertEqual(324, prefix["last_position"])
        self.assertEqual(0, execution["patches_applied_this_run"])

    def test_checkpoint_324_preflight_rejects_completion_artifacts(self):
        source = self.root / "artifact-source"
        cache = self.root / "artifact-cache"
        source.mkdir()
        cache.mkdir()
        args_plan = OrderedDict(
            (
                ("arm64", ("out/Arm", "arm\n")),
                ("x64", ("out/X64", "x64\n")),
            )
        )
        artifacts = (
            (prepare_source.PREPARATION_RECEIPT, "receipt already exists"),
            ("out/Arm/args.gn", "args.gn already exists"),
            (prepare_source.ONBOARDING_STRINGS_OUTPUT, "strings already exist"),
        )
        with mock.patch.object(
            prepare_source.focus_macos,
            "resolve_source_root",
            return_value=(source.resolve(), prepare_source.focus_macos.PINNED_CHROMIUM_VERSION),
        ), mock.patch.object(
            prepare_source, "validate_acquisition_marker", return_value={}
        ), mock.patch.object(
            prepare_source, "validate_tool_bootstrap_marker", return_value={}
        ), mock.patch.object(
            prepare_source,
            "validate_pinned_git_head",
            return_value=prepare_source.ACQUISITION_CHROMIUM_COMMIT,
        ), mock.patch.object(
            prepare_source.focus_macos, "validate_repository_contract", return_value={}
        ), mock.patch.object(
            prepare_source, "validate_dependency_manifest", return_value=OrderedDict()
        ), mock.patch.object(
            prepare_source,
            "validate_offline_cache",
            return_value=(cache.resolve(), []),
        ), mock.patch.object(
            prepare_source, "validate_dependency_cache_marker", return_value={}
        ), mock.patch.object(
            prepare_source, "args_gn_plan", return_value=args_plan
        ):
            for relative, message in artifacts:
                artifact = source / relative
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text("unexpected\n", encoding="utf-8")
                with self.subTest(relative=relative), self.assertRaisesRegex(
                    prepare_source.PreparationError, message
                ):
                    prepare_source.resume_preflight_exact(source, cache, 324)
                artifact.unlink()

    def test_checkpoint_324_preflight_has_only_last_reverse_boundary(self):
        repository = self.root / "preflight-repository"
        patch_root = repository / "patches"
        source = self.root / "preflight-source"
        cache = self.root / "preflight-cache"
        patch_root.mkdir(parents=True)
        (source / "chrome").mkdir(parents=True)
        cache.mkdir()
        (source / "chrome/VERSION").write_text("MAJOR=150\n", encoding="utf-8")
        patch_plan = []
        for position in range(1, 325):
            path = patch_root / "{:03d}.patch".format(position)
            path.write_text("# fixture {}\n".format(position), encoding="utf-8")
            patch_plan.append(path)
        expected_working = prepare_source.expected_resume_working_tree(324)
        expected_ignored = prepare_source.expected_ignored_working_tree_inventory()
        expected_dependency = prepare_source.expected_resume_dependency_tree(324)
        pruning = {
            "manifest_sha256": prepare_source.PRUNING_LIST_SHA256,
            "listed_files": prepare_source.PRUNING_ENTRY_COUNT,
            "all_targets_absent": True,
            "absent_files": prepare_source.PRUNING_ENTRY_COUNT,
            "symlink_targets": 0,
        }
        repository_report = {
            "shared_series": {"planned_entries": 321},
            "platform_patches": [{}, {}, {}],
        }
        args_plan = OrderedDict(
            (
                ("arm64", ("out/Arm", "arm\n")),
                ("x64", ("out/X64", "x64\n")),
            )
        )
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(prepare_source, "REPO_ROOT", repository.resolve())
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source.focus_macos,
                    "resolve_source_root",
                    return_value=(
                        source.resolve(),
                        prepare_source.focus_macos.PINNED_CHROMIUM_VERSION,
                    ),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "validate_acquisition_marker", return_value={}
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "validate_tool_bootstrap_marker", return_value={}
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "validate_pinned_git_head",
                    return_value=prepare_source.ACQUISITION_CHROMIUM_COMMIT,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source.focus_macos,
                    "validate_repository_contract",
                    return_value=repository_report,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "validate_dependency_manifest",
                    return_value=OrderedDict(),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "validate_offline_cache",
                    return_value=(cache.resolve(), []),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "validate_dependency_cache_marker", return_value={}
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source.focus_version, "check_existing_version"
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "installed_dependency_tree",
                    return_value=expected_dependency,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "validate_completed_pruning", return_value=pruning
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "working_tree_inventory",
                    return_value=expected_working,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "ignored_working_tree_inventory",
                    return_value=expected_ignored,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "build_patch_plan", return_value=patch_plan
                )
            )
            stack.enter_context(mock.patch.object(prepare_source, "validate_patch_tool"))
            boundary = stack.enter_context(
                mock.patch.object(prepare_source, "check_patch_boundary")
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "build_overlay_plan", return_value=([], [], [])
                )
            )
            stack.enter_context(
                mock.patch.object(prepare_source, "parse_resource_plan", return_value=[])
            )
            stack.enter_context(
                mock.patch.object(prepare_source.focus_macos, "validate_icns_asset")
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "expected_upstream_source_contracts", return_value={}
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "args_gn_plan", return_value=args_plan
                )
            )
            report = prepare_source.resume_preflight_exact(source, cache, 324)

        boundary.assert_called_once_with(source.resolve(), patch_plan[-1], reverse=True)
        execution = report["preparation_execution"]
        self.assertEqual(0, execution["patches_applied_this_run"])
        self.assertIsNone(execution["resume_checkpoint"]["next_patch"])
        self.assertEqual(324, execution["resume_checkpoint"]["applied_prefix"]["count"])
        self.assertEqual(0, report["patches"]["remaining"])

    def test_checkpoint_324_mutation_skips_empty_patch_batch(self):
        source = self.root / "mutation-source"
        cache = self.root / "mutation-cache"
        source.mkdir()
        cache.mkdir()
        expected_working = prepare_source.expected_resume_working_tree(324)
        expected_ignored = prepare_source.expected_ignored_working_tree_inventory()
        preflight_report = {
            "source_root": str(source),
            "preparation_execution": {
                "initial_applied_patch_count": 324,
                "resume_checkpoint": {
                    "working_tree": expected_working,
                    "ignored_tree": expected_ignored,
                },
            },
            "pruning": {},
            "dependency_install": {},
        }
        patch_plan = [self.root / "{:03d}.patch".format(value) for value in range(1, 325)]
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "resume_preflight_exact",
                    return_value=preflight_report,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "build_patch_plan", return_value=patch_plan
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "working_tree_inventory",
                    return_value=expected_working,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "ignored_working_tree_inventory",
                    return_value=expected_ignored,
                )
            )
            boundary = stack.enter_context(
                mock.patch.object(prepare_source, "check_patch_boundary")
            )
            patch_apply = stack.enter_context(
                mock.patch.object(prepare_source, "apply_patch_plan")
            )
            transformations = stack.enter_context(
                mock.patch.object(
                    prepare_source, "apply_common_transformations", return_value={}
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "build_overlay_plan", return_value=([], [], [])
                )
            )
            stack.enter_context(
                mock.patch.object(prepare_source, "apply_overlay", return_value={})
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source,
                    "append_focus_version_once",
                    return_value="1.0.5.0",
                )
            )
            stack.enter_context(
                mock.patch.object(prepare_source, "parse_resource_plan", return_value=[])
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "copy_common_resources", return_value=0
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "install_focus_icns", return_value="icon"
                )
            )
            stack.enter_context(
                mock.patch.object(prepare_source, "write_args_gn", return_value={})
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "generate_onboarding_strings", return_value={}
                )
            )
            stack.enter_context(
                mock.patch.object(
                    prepare_source, "write_preparation_receipt", return_value={}
                )
            )
            report = prepare_source.resume_patch_failure(source, cache, 324)

        boundary.assert_not_called()
        patch_apply.assert_not_called()
        transformations.assert_called_once_with(source, workers=None)
        self.assertEqual(0, report["patches_applied_this_run"])
        self.assertEqual("remaining 0-patch batch", report["disk_gates"][1]["phase"])

    def test_resume_preflight_cli_is_read_only_and_needs_exact_count(self):
        report = {"resume_ready": True}
        with mock.patch.object(
            prepare_source, "resume_preflight_exact", return_value=report
        ) as preflight, mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(
                0,
                prepare_source.main(
                    [
                        "resume-preflight",
                        "--source-root",
                        str(self.root),
                        "--cache",
                        str(self.root),
                        "--applied-patches",
                        "98",
                        "--json",
                    ]
                ),
            )
        preflight.assert_called_once_with(str(self.root), str(self.root), 98)

    def test_resume_mutation_cli_requires_explicit_confirmation(self):
        with mock.patch("sys.stderr", new=io.StringIO()), self.assertRaises(
            SystemExit
        ) as context:
            prepare_source.main(
                [
                    "resume-patch-failure",
                    "--source-root",
                    str(self.root),
                    "--cache",
                    str(self.root),
                    "--applied-patches",
                    "98",
                ]
            )
        self.assertEqual(2, context.exception.code)

    def test_patch_failure_reports_global_resume_position(self):
        source = self.root / "patch-position"
        source.mkdir()
        target = source / "example.txt"
        target.write_text("different\n", encoding="utf-8")
        patch = self.root / "position.patch"
        patch.write_text(
            "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+new\n",
            encoding="utf-8",
        )
        for base_position, expected_position in ((98, 99), (138, 139)):
            with self.subTest(base_position=base_position), self.assertRaisesRegex(
                prepare_source.PreparationError,
                r"\({}/324\)".format(expected_position),
            ):
                prepare_source.apply_patch_plan(
                    source,
                    [patch],
                    base_position=base_position,
                    total_patches=324,
                )
        self.assertEqual(
            [],
            prepare_source.apply_patch_plan(
                source, [], base_position=324, total_patches=324
            ),
        )

    def test_preparation_execution_report_rejects_count_drift(self):
        report = prepare_source.fresh_preparation_execution_report()
        prepare_source.validate_preparation_execution_report(report)
        changed = dict(report)
        changed["patches_applied_this_run"] = 323
        with self.assertRaisesRegex(prepare_source.PreparationError, "counts"):
            prepare_source.validate_preparation_execution_report(changed)


if __name__ == "__main__":
    unittest.main()
