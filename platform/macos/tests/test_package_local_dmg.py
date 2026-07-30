"""Unit tests for the local-only Focus Browser DMG packager."""

import hashlib
import importlib.util
import io
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PLATFORM_DIR / "package_local_dmg.py"
SPEC = importlib.util.spec_from_file_location("package_local_dmg", MODULE_PATH)
package_local_dmg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_local_dmg)


class LocalDmgPackagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = self.make_app(self.root / package_local_dmg.APP_BUNDLE_NAME)
        self.commands = []
        self.staging = None

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write_plist(app, bundle_id=None, executable="focus_browser"):
        info = app / "Contents" / "Info.plist"
        info.parent.mkdir(parents=True, exist_ok=True)
        with info.open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleIdentifier": bundle_id or package_local_dmg.BUNDLE_ID,
                    "CFBundleExecutable": executable,
                },
                stream,
            )

    def make_app(self, path, bundle_id=None, executable="focus_browser"):
        self.write_plist(path, bundle_id=bundle_id, executable=executable)
        binary = path / "Contents" / "MacOS" / executable
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"mock Mach-O")
        binary.chmod(0o755)
        return path

    @staticmethod
    def report(app, architectures=None):
        return {
            "app": str(app),
            "bundle_id": package_local_dmg.BUNDLE_ID,
            "executable": "focus_browser",
            "architectures": architectures or ["arm64"],
        }

    def command_runner(self, architectures="arm64 x86_64", failure=None, bad_link=False):
        def run(command):
            self.commands.append(list(command))
            if command[0] == package_local_dmg.LIPO:
                return architectures + "\n"
            if command[0] == package_local_dmg.CODESIGN:
                if failure == "codesign":
                    raise package_local_dmg.PackageError("synthetic codesign failure")
                return ""
            if command[0] == package_local_dmg.DITTO:
                shutil.copytree(command[1], command[2], symlinks=True)
                return ""
            if command[0] != package_local_dmg.HDIUTIL:
                self.fail("unexpected command: {!r}".format(command))

            verb = command[1]
            if failure == verb:
                raise package_local_dmg.PackageError("synthetic {} failure".format(verb))
            if verb == "create":
                self.staging = Path(command[command.index("-srcfolder") + 1])
                Path(command[-1]).write_bytes(b"verified mock DMG payload")
            elif verb == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                shutil.copytree(
                    self.staging / package_local_dmg.APP_BUNDLE_NAME,
                    mountpoint / package_local_dmg.APP_BUNDLE_NAME,
                    symlinks=True,
                )
                os.symlink(
                    "/Wrong" if bad_link else "/Applications",
                    str(mountpoint / "Applications"),
                )
            elif verb not in ("verify", "detach"):
                self.fail("unexpected hdiutil verb: {}".format(verb))
            return ""

        return run

    def publish_fixture(self, payload=b"accepted DMG payload"):
        private_root = self.root / "private-candidate"
        private_root.mkdir(mode=0o700)
        private_root.chmod(0o700)
        candidate = private_root / "candidate.dmg"
        candidate.write_bytes(payload)
        candidate.chmod(0o600)
        observed = os.lstat(str(candidate))
        output = self.root / "published.dmg"
        return {
            "root": private_root,
            "candidate": candidate,
            "output": output,
            "identity": (observed.st_dev, observed.st_ino),
            "size": len(payload),
            "digest": hashlib.sha256(payload).hexdigest(),
        }

    def publish(self, fixture):
        return package_local_dmg.durable_publish_candidate(
            fixture["candidate"],
            fixture["output"],
            fixture["identity"],
            fixture["size"],
            fixture["digest"],
        )

    def test_validate_app_accepts_each_supported_architecture_shape(self):
        cases = (
            ("arm64", ["arm64"]),
            ("x86_64", ["x86_64"]),
            ("x86_64 arm64", ["arm64", "x86_64"]),
        )
        for lipo_output, expected in cases:
            with self.subTest(lipo_output=lipo_output):
                calls = []

                def run(command):
                    calls.append(command)
                    return lipo_output + "\n" if command[0] == package_local_dmg.LIPO else ""

                with mock.patch.object(package_local_dmg, "checked_run", side_effect=run):
                    report = package_local_dmg.validate_app(self.app)
                self.assertEqual(expected, report["architectures"])
                self.assertEqual(
                    [
                        package_local_dmg.CODESIGN,
                        "--verify",
                        "--deep",
                        "--strict",
                        str(self.app.resolve()),
                    ],
                    calls[-1],
                )

    def test_validate_app_rejects_unexpected_architectures(self):
        for value in ("", "i386", "arm64e", "arm64 i386", "arm64 arm64"):
            with self.subTest(value=value), mock.patch.object(
                package_local_dmg, "checked_run", return_value=value
            ):
                with self.assertRaises(package_local_dmg.PackageError):
                    package_local_dmg.validate_app(self.app)

    def test_validate_app_rejects_wrong_bundle_identifier_before_tools(self):
        self.write_plist(self.app, bundle_id="org.example.wrong")
        with mock.patch.object(package_local_dmg, "checked_run") as run:
            with self.assertRaisesRegex(package_local_dmg.PackageError, "CFBundleIdentifier"):
                package_local_dmg.validate_app(self.app)
        run.assert_not_called()

    def test_validate_app_rejects_malformed_plist_before_tools(self):
        (self.app / "Contents" / "Info.plist").write_bytes(b"not a plist")
        with mock.patch.object(package_local_dmg, "checked_run") as run:
            with self.assertRaisesRegex(package_local_dmg.PackageError, "invalid Info.plist"):
                package_local_dmg.validate_app(self.app)
        run.assert_not_called()

    def test_validate_app_requires_leaf_executable(self):
        for value in (
            "",
            ".",
            "..",
            "../focus_browser",
            "MacOS/focus_browser",
            "a\\b",
        ):
            with self.subTest(value=value):
                self.write_plist(self.app, executable=value)
                with mock.patch.object(package_local_dmg, "checked_run") as run:
                    with self.assertRaisesRegex(package_local_dmg.PackageError, "leaf"):
                        package_local_dmg.validate_app(self.app)
                run.assert_not_called()
        with self.assertRaisesRegex(package_local_dmg.PackageError, "leaf"):
            package_local_dmg._validate_executable_leaf("bad\x00name")

    def test_validate_app_rejects_symlink_main_executable(self):
        binary = self.app / "Contents" / "MacOS" / "focus_browser"
        binary.unlink()
        target = self.root / "outside"
        target.write_bytes(b"outside")
        binary.symlink_to(target)
        with mock.patch.object(package_local_dmg, "checked_run") as run:
            with self.assertRaisesRegex(package_local_dmg.PackageError, "regular main executable"):
                package_local_dmg.validate_app(self.app)
        run.assert_not_called()

    def test_paths_require_exact_existing_app_and_new_dmg(self):
        self.assertEqual(self.app.resolve(), package_local_dmg.resolve_app_path(self.app))
        with self.assertRaises(package_local_dmg.PackageError):
            package_local_dmg.resolve_app_path(self.root / "Other.app")
        with self.assertRaises(package_local_dmg.PackageError):
            package_local_dmg.resolve_app_path(self.root / package_local_dmg.APP_BUNDLE_NAME / "missing")

        output = self.root / "FocusBrowser.dmg"
        self.assertEqual(
            output.parent.resolve() / output.name,
            package_local_dmg.resolve_output_path(output),
        )
        for invalid in (self.root / "no-extension", self.root / ".dmg"):
            with self.subTest(invalid=invalid), self.assertRaises(package_local_dmg.PackageError):
                package_local_dmg.resolve_output_path(invalid)
        output.write_bytes(b"existing")
        with self.assertRaisesRegex(package_local_dmg.PackageError, "overwrite"):
            package_local_dmg.resolve_output_path(output)

    def test_output_rejects_broken_symlink_and_missing_parent(self):
        broken = self.root / "broken.dmg"
        broken.symlink_to(self.root / "does-not-exist")
        with self.assertRaisesRegex(package_local_dmg.PackageError, "overwrite"):
            package_local_dmg.resolve_output_path(broken)
        with self.assertRaisesRegex(package_local_dmg.PackageError, "parent does not exist"):
            package_local_dmg.resolve_output_path(self.root / "missing" / "output.dmg")

    def test_package_rejects_output_inside_source_app_even_through_symlink(self):
        resources = self.app / "Contents" / "Resources"
        resources.mkdir()
        alias = self.root / "inside-app-alias"
        alias.symlink_to(resources, target_is_directory=True)
        for output in (resources / "bad.dmg", alias / "also-bad.dmg"):
            with self.subTest(output=output), mock.patch.object(
                package_local_dmg, "require_system_tools"
            ), mock.patch.object(package_local_dmg, "checked_run") as run:
                with self.assertRaisesRegex(package_local_dmg.PackageError, "inside.*app bundle"):
                    package_local_dmg.package_local_dmg(self.app, output)
            run.assert_not_called()
            self.assertFalse(os.path.lexists(str(output)))

    def test_require_system_tools_fails_closed_at_fixed_paths(self):
        with mock.patch("os.path.isfile", side_effect=lambda value: value != package_local_dmg.LIPO), mock.patch(
            "os.access", return_value=True
        ):
            with self.assertRaisesRegex(package_local_dmg.PackageError, package_local_dmg.LIPO):
                package_local_dmg.require_system_tools()

    def test_checked_run_uses_list_and_never_shell(self):
        completed = subprocess.CompletedProcess([package_local_dmg.LIPO], 0, "arm64\n", "")
        with mock.patch("subprocess.run", return_value=completed) as run:
            output = package_local_dmg.checked_run([package_local_dmg.LIPO, "-archs", "/tmp/app"])
        self.assertEqual("arm64\n", output)
        self.assertEqual(False, run.call_args.kwargs["shell"])
        self.assertEqual(subprocess.DEVNULL, run.call_args.kwargs["stdin"])
        with self.assertRaises(package_local_dmg.PackageError):
            package_local_dmg.checked_run("/usr/bin/lipo -archs app")

    def test_checked_run_reports_nonzero_tool_failure(self):
        completed = subprocess.CompletedProcess([package_local_dmg.CODESIGN], 1, "", "bad signature")
        with mock.patch("subprocess.run", return_value=completed):
            with self.assertRaisesRegex(package_local_dmg.PackageError, "bad signature"):
                package_local_dmg.checked_run([package_local_dmg.CODESIGN, "--verify", "/tmp/app"])

    def test_inspection_attaches_read_only_checks_link_signature_and_detaches(self):
        mountpoint = self.root / "mount"
        mountpoint.mkdir()
        mounted_app = self.make_app(mountpoint / package_local_dmg.APP_BUNDLE_NAME)
        (mountpoint / "Applications").symlink_to("/Applications")
        commands = []

        def run(command):
            commands.append(command)
            return ""

        expected = self.report(self.app, ["arm64", "x86_64"])
        observed = self.report(mounted_app, ["arm64", "x86_64"])
        with mock.patch.object(package_local_dmg, "checked_run", side_effect=run), mock.patch.object(
            package_local_dmg, "validate_app", return_value=observed
        ) as validate, mock.patch("os.path.ismount", return_value=True), mock.patch(
            "os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
        ):
            result = package_local_dmg.inspect_mounted_image(
                self.root / "image.dmg", mountpoint, expected
            )
        self.assertEqual(observed, result)
        self.assertIn("-readonly", commands[0])
        self.assertEqual(
            [package_local_dmg.HDIUTIL, "detach", str(mountpoint)], commands[-1]
        )
        validate.assert_called_once_with(mountpoint / package_local_dmg.APP_BUNDLE_NAME)

    def test_inspection_failure_still_detaches(self):
        mountpoint = self.root / "bad-mount"
        mountpoint.mkdir()
        commands = []

        def run(command):
            commands.append(command)
            return ""

        with mock.patch.object(package_local_dmg, "checked_run", side_effect=run), mock.patch(
            "os.path.ismount", return_value=True
        ), mock.patch("os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
            with self.assertRaisesRegex(package_local_dmg.PackageError, "Applications symlink"):
                package_local_dmg.inspect_mounted_image(
                    self.root / "image.dmg", mountpoint, self.report(self.app)
                )
        self.assertEqual(
            [package_local_dmg.HDIUTIL, "detach", str(mountpoint)], commands[-1]
        )

    def test_inspection_rejects_writable_mount_and_detaches(self):
        mountpoint = self.root / "writable-mount"
        mountpoint.mkdir()
        commands = []

        def run(command):
            commands.append(command)
            return ""

        with mock.patch.object(package_local_dmg, "checked_run", side_effect=run), mock.patch(
            "os.path.ismount", return_value=True
        ), mock.patch("os.statvfs", return_value=SimpleNamespace(f_flag=0)):
            with self.assertRaisesRegex(package_local_dmg.PackageError, "not read-only"):
                package_local_dmg.inspect_mounted_image(
                    self.root / "image.dmg", mountpoint, self.report(self.app)
                )
        self.assertEqual("detach", commands[-1][1])

    def test_complete_package_is_verified_then_atomically_placed(self):
        output = self.root / "FocusBrowser-local.dmg"
        runner = self.command_runner()
        with mock.patch.object(package_local_dmg, "require_system_tools"), mock.patch.object(
            package_local_dmg, "checked_run", side_effect=runner
        ), mock.patch("os.path.ismount", return_value=True), mock.patch(
            "os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
        ):
            report = package_local_dmg.package_local_dmg(self.app, output)

        payload = b"verified mock DMG payload"
        self.assertEqual(payload, output.read_bytes())
        self.assertEqual(hashlib.sha256(payload).hexdigest(), report["sha256"])
        self.assertEqual(len(payload), report["size_bytes"])
        self.assertEqual(["arm64", "x86_64"], report["architectures"])
        self.assertFalse(report["require_universal"])
        self.assertTrue(report["local_only"])
        self.assertFalse(report["signing_performed"])
        self.assertFalse(report["notarization_performed"])
        self.assertEqual(3, sum(command[0] == package_local_dmg.CODESIGN for command in self.commands))
        self.assertEqual(3, sum(command[0] == package_local_dmg.LIPO for command in self.commands))
        ditto = next(command for command in self.commands if command[0] == package_local_dmg.DITTO)
        self.assertEqual(str(self.app.resolve()), ditto[1])
        create = next(
            command
            for command in self.commands
            if command[:2] == [package_local_dmg.HDIUTIL, "create"]
        )
        self.assertNotIn("-ov", create)
        verbs = [command[1] for command in self.commands if command[0] == package_local_dmg.HDIUTIL]
        self.assertEqual(["create", "verify", "attach", "detach"], verbs)

    def test_default_package_allows_thin_app(self):
        output = self.root / "thin-intel.dmg"
        with mock.patch.object(package_local_dmg, "require_system_tools"), mock.patch.object(
            package_local_dmg,
            "checked_run",
            side_effect=self.command_runner(architectures="x86_64"),
        ), mock.patch("os.path.ismount", return_value=True), mock.patch(
            "os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
        ):
            report = package_local_dmg.package_local_dmg(self.app, output)
        self.assertEqual(["x86_64"], report["architectures"])
        self.assertFalse(report["require_universal"])
        self.assertTrue(output.is_file())

    def test_require_universal_rejects_thin_app_before_staging(self):
        output = self.root / "thin-rejected.dmg"
        with mock.patch.object(package_local_dmg, "require_system_tools"), mock.patch.object(
            package_local_dmg,
            "checked_run",
            side_effect=self.command_runner(architectures="arm64"),
        ):
            with self.assertRaisesRegex(package_local_dmg.PackageError, r"requires.*arm64\+x86_64"):
                package_local_dmg.package_local_dmg(
                    self.app,
                    output,
                    require_universal=True,
                )
        self.assertFalse(os.path.lexists(str(output)))
        self.assertEqual([], [command for command in self.commands if command[0] == package_local_dmg.DITTO])

    def test_require_universal_accepts_exact_dual_architecture_app(self):
        output = self.root / "universal.dmg"
        with mock.patch.object(package_local_dmg, "require_system_tools"), mock.patch.object(
            package_local_dmg,
            "checked_run",
            side_effect=self.command_runner(architectures="arm64 x86_64"),
        ), mock.patch("os.path.ismount", return_value=True), mock.patch(
            "os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
        ):
            report = package_local_dmg.package_local_dmg(
                self.app,
                output,
                require_universal=True,
            )
        self.assertEqual(["arm64", "x86_64"], report["architectures"])
        self.assertTrue(report["require_universal"])
        self.assertTrue(output.is_file())

    def test_verify_failure_leaves_no_output(self):
        output = self.root / "failed.dmg"
        with mock.patch.object(package_local_dmg, "require_system_tools"), mock.patch.object(
            package_local_dmg, "checked_run", side_effect=self.command_runner(failure="verify")
        ):
            with self.assertRaisesRegex(package_local_dmg.PackageError, "verify failure"):
                package_local_dmg.package_local_dmg(self.app, output)
        self.assertFalse(os.path.lexists(str(output)))

    def test_signature_failure_leaves_no_output(self):
        output = self.root / "bad-signature.dmg"
        with mock.patch.object(package_local_dmg, "require_system_tools"), mock.patch.object(
            package_local_dmg, "checked_run", side_effect=self.command_runner(failure="codesign")
        ):
            with self.assertRaisesRegex(package_local_dmg.PackageError, "codesign failure"):
                package_local_dmg.package_local_dmg(self.app, output)
        self.assertFalse(os.path.lexists(str(output)))
        self.assertEqual([], [command for command in self.commands if command[0] == package_local_dmg.DITTO])

    def test_mounted_payload_failure_detaches_and_leaves_no_output(self):
        output = self.root / "bad-mounted.dmg"
        with mock.patch.object(package_local_dmg, "require_system_tools"), mock.patch.object(
            package_local_dmg, "checked_run", side_effect=self.command_runner(bad_link=True)
        ), mock.patch("os.path.ismount", return_value=True), mock.patch(
            "os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
        ):
            with self.assertRaisesRegex(package_local_dmg.PackageError, "unexpected target"):
                package_local_dmg.package_local_dmg(self.app, output)
        self.assertFalse(os.path.lexists(str(output)))
        self.assertEqual("detach", self.commands[-1][1])

    def test_atomic_output_race_never_overwrites_or_removes_rival(self):
        output = self.root / "race.dmg"

        def rival_link(*_args, **_kwargs):
            output.write_bytes(b"rival")
            raise FileExistsError(str(output))

        with mock.patch.object(package_local_dmg, "require_system_tools"), mock.patch.object(
            package_local_dmg, "checked_run", side_effect=self.command_runner()
        ), mock.patch("os.path.ismount", return_value=True), mock.patch(
            "os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
        ), mock.patch("os.link", side_effect=rival_link):
            with self.assertRaisesRegex(package_local_dmg.PackageError, "overwrite"):
                package_local_dmg.package_local_dmg(self.app, output)
        self.assertEqual(b"rival", output.read_bytes())

    def test_durable_publish_fsyncs_file_then_final_entry_then_candidate_root(self):
        fixture = self.publish_fixture()
        candidate_identity = fixture["identity"]
        parent_identity = tuple(
            getattr(os.lstat(str(self.root)), name) for name in ("st_dev", "st_ino")
        )
        root_identity = tuple(
            getattr(os.lstat(str(fixture["root"])), name)
            for name in ("st_dev", "st_ino")
        )
        calls = []
        real_fsync = os.fsync

        def recording_fsync(descriptor):
            observed = os.fstat(descriptor)
            calls.append((observed.st_dev, observed.st_ino))
            return real_fsync(descriptor)

        with mock.patch("os.fsync", side_effect=recording_fsync):
            published = self.publish(fixture)

        self.assertEqual(
            [candidate_identity, parent_identity, root_identity], calls
        )
        self.assertFalse(fixture["candidate"].exists())
        self.assertEqual(1, published.st_nlink)
        self.assertEqual(1, os.lstat(str(fixture["output"])).st_nlink)
        self.assertEqual(b"accepted DMG payload", fixture["output"].read_bytes())

    def test_keyboard_interrupt_after_real_link_rolls_back_actual_inode(self):
        fixture = self.publish_fixture()
        real_link = os.link

        def link_then_interrupt(*args, **kwargs):
            real_link(*args, **kwargs)
            raise KeyboardInterrupt("synthetic interrupt after link")

        with mock.patch("os.link", side_effect=link_then_interrupt), self.assertRaises(
            KeyboardInterrupt
        ):
            self.publish(fixture)

        self.assertFalse(os.path.lexists(str(fixture["output"])))
        self.assertTrue(fixture["candidate"].is_file())
        self.assertEqual(1, os.lstat(str(fixture["candidate"])).st_nlink)

    def test_output_parent_fsync_failure_rolls_back_and_fsyncs_removal(self):
        fixture = self.publish_fixture()
        real_fsync = os.fsync
        calls = 0

        def fail_commit_fsync(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic output parent fsync failure")
            return real_fsync(descriptor)

        with mock.patch("os.fsync", side_effect=fail_commit_fsync), self.assertRaisesRegex(
            OSError, "output parent fsync"
        ):
            self.publish(fixture)

        self.assertEqual(3, calls)
        self.assertFalse(os.path.lexists(str(fixture["output"])))
        self.assertEqual(1, os.lstat(str(fixture["candidate"])).st_nlink)

    def test_candidate_with_existing_hardlink_is_rejected(self):
        fixture = self.publish_fixture()
        os.link(str(fixture["candidate"]), str(self.root / "extra-link.dmg"))
        with self.assertRaisesRegex(package_local_dmg.PackageError, "one link"):
            self.publish(fixture)
        self.assertFalse(os.path.lexists(str(fixture["output"])))
        self.assertEqual(2, os.lstat(str(fixture["candidate"])).st_nlink)

    def test_candidate_with_unsafe_mode_is_rejected(self):
        fixture = self.publish_fixture()
        fixture["candidate"].chmod(0o666)
        with self.assertRaisesRegex(
            package_local_dmg.PackageError, "group/world write"
        ):
            self.publish(fixture)
        self.assertFalse(os.path.lexists(str(fixture["output"])))

    def test_private_root_path_replacement_is_detected_before_link(self):
        fixture = self.publish_fixture()
        moved_root = self.root / "moved-private-candidate"
        real_fsync = os.fsync
        replaced = False

        def replace_after_candidate_fsync(descriptor):
            nonlocal replaced
            result = real_fsync(descriptor)
            if not replaced:
                fixture["root"].rename(moved_root)
                fixture["root"].mkdir(mode=0o700)
                fixture["root"].chmod(0o700)
                replaced = True
            return result

        with mock.patch("os.fsync", side_effect=replace_after_candidate_fsync), self.assertRaisesRegex(
            package_local_dmg.PackageError, "directory was replaced"
        ):
            self.publish(fixture)
        self.assertFalse(os.path.lexists(str(fixture["output"])))
        self.assertEqual(b"accepted DMG payload", (moved_root / "candidate.dmg").read_bytes())

    def test_output_parent_path_replacement_is_detected_before_link(self):
        fixture = self.publish_fixture()
        output_parent = self.root / "destination"
        output_parent.mkdir()
        fixture["output"] = output_parent / "published.dmg"
        moved_parent = self.root / "moved-destination"
        real_fsync = os.fsync
        replaced = False

        def replace_after_candidate_fsync(descriptor):
            nonlocal replaced
            result = real_fsync(descriptor)
            if not replaced:
                output_parent.rename(moved_parent)
                output_parent.mkdir()
                replaced = True
            return result

        with mock.patch("os.fsync", side_effect=replace_after_candidate_fsync), self.assertRaisesRegex(
            package_local_dmg.PackageError, "directory was replaced"
        ):
            self.publish(fixture)
        self.assertFalse(os.path.lexists(str(fixture["output"])))
        self.assertEqual([], list(moved_parent.iterdir()))

    def test_rival_output_inode_is_never_removed(self):
        fixture = self.publish_fixture()
        fixture["output"].write_bytes(b"rival output")
        with self.assertRaisesRegex(package_local_dmg.PackageError, "overwrite"):
            self.publish(fixture)
        self.assertEqual(b"rival output", fixture["output"].read_bytes())

    def test_racing_same_inode_hardlink_is_treated_as_rival_on_eexist(self):
        fixture = self.publish_fixture()
        real_link = os.link

        def rival_same_inode(*_args, **_kwargs):
            real_link(str(fixture["candidate"]), str(fixture["output"]))
            raise FileExistsError(str(fixture["output"]))

        with mock.patch("os.link", side_effect=rival_same_inode), self.assertRaisesRegex(
            package_local_dmg.PackageError, "overwrite"
        ):
            self.publish(fixture)
        self.assertEqual(b"accepted DMG payload", fixture["output"].read_bytes())
        self.assertEqual(2, os.lstat(str(fixture["output"])).st_nlink)

    def test_post_commit_candidate_cleanup_failure_preserves_final_inode(self):
        fixture = self.publish_fixture()
        real_unlink = os.unlink

        def reject_candidate_cleanup(path, *args, **kwargs):
            if path == fixture["candidate"].name and kwargs.get("dir_fd") is not None:
                raise OSError("synthetic candidate cleanup failure")
            return real_unlink(path, *args, **kwargs)

        with mock.patch("os.unlink", side_effect=reject_candidate_cleanup), self.assertRaisesRegex(
            package_local_dmg.CommittedPublishError, "durably committed"
        ) as raised:
            self.publish(fixture)

        self.assertEqual(fixture["identity"], raised.exception.final_identity)
        self.assertEqual(b"accepted DMG payload", fixture["output"].read_bytes())
        self.assertEqual(fixture["identity"], (
            os.lstat(str(fixture["output"])).st_dev,
            os.lstat(str(fixture["output"])).st_ino,
        ))
        self.assertEqual(2, os.lstat(str(fixture["output"])).st_nlink)

    def test_cli_json_and_error_paths(self):
        report = {
            "output": str(self.root / "ok.dmg"),
            "architectures": ["x86_64"],
            "require_universal": True,
            "size_bytes": 4,
            "sha256": "0" * 64,
        }
        stdout = io.StringIO()
        with mock.patch.object(
            package_local_dmg, "package_local_dmg", return_value=report
        ) as packager, redirect_stdout(stdout):
            result = package_local_dmg.main(
                [
                    "--app",
                    str(self.app),
                    "--output",
                    report["output"],
                    "--require-universal",
                    "--json",
                ]
            )
        self.assertEqual(0, result)
        self.assertEqual(report, json.loads(stdout.getvalue()))
        packager.assert_called_once_with(
            str(self.app),
            report["output"],
            require_universal=True,
        )

        stderr = io.StringIO()
        with mock.patch.object(
            package_local_dmg,
            "package_local_dmg",
            side_effect=package_local_dmg.PackageError("blocked"),
        ), redirect_stderr(stderr):
            result = package_local_dmg.main(
                ["--app", str(self.app), "--output", str(self.root / "no.dmg")]
            )
        self.assertEqual(2, result)
        self.assertIn("blocked", stderr.getvalue())

    def test_source_has_no_sign_notarize_network_or_shell_workflow(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("urllib", "requests", "notarytool", "--sign", "shell=True", "-ov"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn('[CODESIGN, "--verify", "--deep", "--strict", str(app)]', source)
        self.assertIn("src_dir_fd=root_fd", source)
        self.assertIn("os.fsync(parent_fd)", source)
        self.assertIn("os.fsync(root_fd)", source)
        self.assertIn("O_NOFOLLOW", source)


if __name__ == "__main__":
    unittest.main()
