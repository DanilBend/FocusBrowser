"""Tests for the no-overwrite macOS Auto args.gn writer."""

import hashlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parents[1]
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))
MODULE_PATH = PLATFORM_DIR / "write_autoupdate_args.py"
SPEC = importlib.util.spec_from_file_location("write_autoupdate_args", MODULE_PATH)
writer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(writer)


class AutoArgsWriterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.source = (Path(self.temporary.name) / "src").resolve()
        for relative in ("chrome/browser", "components", "third_party"):
            (self.source / relative).mkdir(parents=True, exist_ok=True)
        (self.source / "BUILD.gn").write_text("# fixture\n", encoding="utf-8")
        (self.source / "chrome/VERSION").write_text(
            "MAJOR=150\nMINOR=0\nBUILD=7871\nPATCH=128\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_x64_dry_run_is_read_only_and_exact(self):
        before = sorted(path.relative_to(self.source).as_posix() for path in self.source.rglob("*"))
        report = writer.public_plan(writer.args_plan(str(self.source), "x64"))
        after = sorted(path.relative_to(self.source).as_posix() for path in self.source.rglob("*"))
        self.assertEqual(before, after)
        self.assertFalse(report["executed"])
        self.assertTrue(report["no_replace"])
        self.assertEqual("0600", report["mode"])
        self.assertEqual(writer.EXPECTED_ARGS_SHA256["x64"], report["sha256"])
        self.assertEqual(
            self.source / "out/FocusMacX64Auto/args.gn",
            Path(report["destination"]),
        )

    def test_each_architecture_writes_exact_canonical_bytes(self):
        for architecture in ("arm64", "x64"):
            with self.subTest(architecture=architecture):
                plan = writer.args_plan(str(self.source), architecture)
                report = writer.execute_plan(plan)
                destination = Path(report["destination"])
                self.assertTrue(report["executed"])
                self.assertEqual(report["bytes"], destination.stat().st_size)
                self.assertEqual(report["sha256"], self.digest(destination))
                self.assertEqual(0o600, stat.S_IMODE(destination.stat().st_mode))
                self.assertEqual(
                    plan["payload"],
                    destination.read_bytes(),
                )

    def test_retry_refuses_to_overwrite_and_preserves_bytes(self):
        writer.execute_plan(writer.args_plan(str(self.source), "x64"))
        destination = self.source / "out/FocusMacX64Auto/args.gn"
        before = destination.read_bytes()
        with self.assertRaisesRegex(writer.ArgsWriterError, "refusing to overwrite"):
            writer.args_plan(str(self.source), "x64")
        self.assertEqual(before, destination.read_bytes())

    def test_symlinked_output_parent_is_rejected(self):
        outside = (Path(self.temporary.name) / "outside").resolve()
        outside.mkdir()
        (self.source / "out").mkdir()
        (self.source / "out/FocusMacX64Auto").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(writer.ArgsWriterError, "must not be a symlink"):
            writer.args_plan(str(self.source), "x64")
        self.assertEqual([], list(outside.iterdir()))

    def test_execute_rechecks_destination_race_without_replacing(self):
        plan = writer.args_plan(str(self.source), "x64")
        destination = Path(plan["destination"])
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"rival\n")
        with self.assertRaisesRegex(writer.ArgsWriterError, "refusing to overwrite"):
            writer.execute_plan(plan)
        self.assertEqual(b"rival\n", destination.read_bytes())

    def test_hidden_candidate_hardlink_is_rejected_before_commit(self):
        plan = writer.args_plan(str(self.source), "x64")
        destination = Path(plan["destination"])
        hidden = self.source / "hidden-args-candidate"
        real_link = os.link
        injected = False

        def link_with_hidden_alias(source, target, *args, **kwargs):
            nonlocal injected
            if not injected:
                source_directory = kwargs.get("src_dir_fd")
                self.assertIsNotNone(source_directory)
                real_link(
                    source,
                    str(hidden),
                    src_dir_fd=source_directory,
                    follow_symlinks=False,
                )
                injected = True
            return real_link(source, target, *args, **kwargs)

        with mock.patch("os.link", side_effect=link_with_hidden_alias):
            with self.assertRaisesRegex(
                writer.ArgsWriterError, "hardlink count changed before commit"
            ):
                writer.execute_plan(plan)

        self.assertTrue(injected)
        self.assertFalse(destination.exists())
        self.assertEqual(plan["payload"], hidden.read_bytes())
        hidden.unlink()

    def test_interrupted_link_is_typed_and_retains_exact_paths(self):
        plan = writer.args_plan(str(self.source), "x64")
        destination = Path(plan["destination"])
        real_link = os.link

        def link_then_interrupt(source, target, *args, **kwargs):
            real_link(source, target, *args, **kwargs)
            raise KeyboardInterrupt("synthetic post-link interrupt")

        with mock.patch("os.link", side_effect=link_then_interrupt):
            with self.assertRaises(writer.RetainedArgsWriterError) as raised:
                writer.execute_plan(plan)

        self.assertTrue(raised.exception.destination_present)
        self.assertEqual(str(destination), raised.exception.destination)
        self.assertEqual(plan["payload"], destination.read_bytes())
        retained = Path(raised.exception.retained_candidate)
        self.assertTrue(retained.is_file())
        self.assertEqual(
            raised.exception.final_identity,
            (destination.stat().st_dev, destination.stat().st_ino),
        )
        destination.unlink()
        retained.unlink()

    def test_precommit_destination_unlink_failure_is_typed_and_retained(self):
        plan = writer.args_plan(str(self.source), "x64")
        destination = Path(plan["destination"])
        hidden = self.source / "hidden-precommit-candidate"
        real_link = os.link
        real_unlink = os.unlink
        injected = False

        def link_with_hidden_alias(source, target, *args, **kwargs):
            nonlocal injected
            if not injected:
                real_link(
                    source,
                    str(hidden),
                    src_dir_fd=kwargs["src_dir_fd"],
                    follow_symlinks=False,
                )
                injected = True
            return real_link(source, target, *args, **kwargs)

        def reject_destination_unlink(name, *args, **kwargs):
            if name == destination.name and kwargs.get("dir_fd") is not None:
                raise PermissionError("synthetic destination unlink failure")
            return real_unlink(name, *args, **kwargs)

        with mock.patch("os.link", side_effect=link_with_hidden_alias), mock.patch(
            "os.unlink", side_effect=reject_destination_unlink
        ):
            with self.assertRaises(writer.RetainedArgsWriterError) as raised:
                writer.execute_plan(plan)

        self.assertTrue(injected)
        self.assertTrue(raised.exception.destination_present)
        self.assertEqual(plan["payload"], destination.read_bytes())
        retained = Path(raised.exception.retained_candidate)
        self.assertTrue(retained.is_file())
        destination.unlink()
        retained.unlink()
        hidden.unlink()

    def test_precommit_rollback_fsync_failure_is_typed_after_unlink(self):
        plan = writer.args_plan(str(self.source), "arm64")
        destination = Path(plan["destination"])
        hidden = self.source / "hidden-fsync-candidate"
        real_link = os.link
        real_fsync = os.fsync
        injected = False

        def link_with_hidden_alias(source, target, *args, **kwargs):
            nonlocal injected
            if not injected:
                real_link(
                    source,
                    str(hidden),
                    src_dir_fd=kwargs["src_dir_fd"],
                    follow_symlinks=False,
                )
                injected = True
            return real_link(source, target, *args, **kwargs)

        def reject_directory_fsync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("synthetic rollback directory fsync failure")
            return real_fsync(descriptor)

        with mock.patch("os.link", side_effect=link_with_hidden_alias), mock.patch(
            "os.fsync", side_effect=reject_directory_fsync
        ):
            with self.assertRaises(writer.RetainedArgsWriterError) as raised:
                writer.execute_plan(plan)

        self.assertTrue(injected)
        self.assertFalse(raised.exception.destination_present)
        self.assertFalse(destination.exists())
        retained = Path(raised.exception.retained_candidate)
        self.assertTrue(retained.is_file())
        retained.unlink()
        hidden.unlink()

    def test_post_commit_candidate_unlink_failure_is_typed_and_retained(self):
        plan = writer.args_plan(str(self.source), "arm64")
        destination = Path(plan["destination"])
        real_unlink = os.unlink

        def reject_private_candidate(name, *args, **kwargs):
            if str(name).startswith(".args.gn.") and kwargs.get("dir_fd") is not None:
                raise OSError("synthetic private candidate unlink failure")
            return real_unlink(name, *args, **kwargs)

        with mock.patch("os.unlink", side_effect=reject_private_candidate):
            with self.assertRaisesRegex(
                writer.CommittedArgsWriterError, "remains committed"
            ) as raised:
                writer.execute_plan(plan)

        self.assertEqual(str(destination), raised.exception.destination)
        self.assertEqual(plan["payload"], destination.read_bytes())
        self.assertEqual(2, destination.stat().st_nlink)
        retained = Path(raised.exception.retained_candidate)
        self.assertTrue(retained.is_file())
        self.assertEqual(plan["payload"], retained.read_bytes())
        retained.unlink()

    def test_json_cli_dry_run_does_not_create_output(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = writer.main(
                [
                    "--source-root",
                    str(self.source),
                    "--architecture",
                    "arm64",
                    "--json",
                ]
            )
        self.assertEqual(0, result)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["result"]["executed"])
        self.assertFalse((self.source / "out").exists())


if __name__ == "__main__":
    unittest.main()
