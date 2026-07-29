#!/usr/bin/env python3
"""Unit tests for the offline macOS Chromium source preparer."""

import hashlib
import io
import json
import stat
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
            ["search_engines_data", "onboarding", "ublock_origin"], list(contracts)
        )
        self.assertEqual(
            "ublock-origin-1.72.2.zip",
            contracts["ublock_origin"]["download_filename"],
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
        with self.assertRaisesRegex(prepare_source.PreparationError, "regular"):
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
        self.assertEqual("0", command[command.index("-F") + 1])
        self.assertNotIn("--ignore-whitespace", command)

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
            "out/Arm/args.gn": "arm\n",
            "out/X64/args.gn": "x64\n",
        }
        for relative, content in fixture_files.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        report = prepare_source.write_preparation_receipt(
            source,
            {
                "acquisition": {
                    "status": "acquisition_complete",
                    "sha256": "b" * 64,
                },
                "tool_bootstrap": {
                    "hooks_complete": True,
                    "sha256": "c" * 64,
                },
                "upstream_baseline_sha256": {"chrome/BUILD.gn": "a" * 64},
            },
            {
                "arm64": str((source / "out/Arm/args.gn").resolve()),
                "x64": str((source / "out/X64/args.gn").resolve()),
            },
        )
        receipt = Path(report["path"])
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(1, payload["schema"])
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
            )

    def test_common_transformations_run_in_domain_name_i18n_order(self):
        source = self.root / "src"
        source.mkdir()
        calls = []
        with mock.patch.object(
            prepare_source, "validate_domain_targets", return_value=(Path("r"), Path("f"), 3)
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
        self.assertEqual(6, report["i18n_xtb_targets"])

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
                "preparation receipt write",
                "post-preparation completion",
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


if __name__ == "__main__":
    unittest.main()
