"""Tests for the pinned, fail-closed Sparkle dependency acquisition."""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PLATFORM_DIR / "acquire_sparkle.py"
SPEC = importlib.util.spec_from_file_location("acquire_sparkle", MODULE_PATH)
sparkle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sparkle)


class AcquireSparkleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _add_file(archive, name, payload=b"fixture", mode=0o644):
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        member.mode = mode
        archive.addfile(member, io.BytesIO(payload))

    @staticmethod
    def _add_symlink(archive, name, target):
        member = tarfile.TarInfo(name)
        member.type = tarfile.SYMTYPE
        member.linkname = target
        member.mode = 0o777
        archive.addfile(member)

    def _archive(self, entries):
        path = self.root / "fixture.tar.xz"
        with tarfile.open(path, "w:xz") as archive:
            for entry in entries:
                kind = entry[0]
                if kind == "file":
                    self._add_file(archive, *entry[1:])
                elif kind == "symlink":
                    self._add_symlink(archive, *entry[1:])
                elif kind == "hardlink":
                    member = tarfile.TarInfo(entry[1])
                    member.type = tarfile.LNKTYPE
                    member.linkname = entry[2]
                    archive.addfile(member)
                else:
                    raise AssertionError("unknown fixture entry")
        return path

    def test_release_asset_pins_are_exact(self):
        self.assertEqual("2.9.4", sparkle.SPARKLE_VERSION)
        self.assertEqual("Sparkle-2.9.4.tar.xz", sparkle.SPARKLE_ARCHIVE_NAME)
        self.assertEqual(15_554_152, sparkle.SPARKLE_ARCHIVE_BYTES)
        self.assertEqual(
            "ce89daf967db1e1893ed3ebd67575ed82d3902563e3191ca92aaec9164fbdef9",
            sparkle.SPARKLE_ARCHIVE_SHA256,
        )
        self.assertEqual(
            "https://github.com/sparkle-project/Sparkle/releases/download/"
            "2.9.4/Sparkle-2.9.4.tar.xz",
            sparkle.SPARKLE_URL,
        )
        self.assertEqual(
            {"arm64", "x86_64"}, set(sparkle.REQUIRED_ARCHITECTURES)
        )
        self.assertEqual(
            "389a4e4e9a32f059775b13a06e25a591445ba229d2838d26dd3e7c0c45127cfe",
            sparkle.SPARKLE_LICENSE_SHA256,
        )
        self.assertEqual(
            set(sparkle.EXPECTED_MACHO_PATHS),
            set(sparkle.EXPECTED_BINARY_SHA256),
        )

    def test_download_command_is_https_only_bounded_and_atomic(self):
        part = self.root / "asset.part"
        command = sparkle.curl_command(part)
        self.assertEqual("/usr/bin/curl", command[0])
        self.assertIn("--fail", command)
        self.assertIn("--location", command)
        self.assertEqual("=https", command[command.index("--proto") + 1])
        self.assertEqual("=https", command[command.index("--proto-redir") + 1])
        self.assertEqual(
            str(sparkle.SPARKLE_ARCHIVE_BYTES),
            command[command.index("--max-filesize") + 1],
        )
        self.assertEqual(str(part), command[command.index("--output") + 1])
        self.assertEqual(sparkle.SPARKLE_URL, command[-1])

    def test_download_verifies_before_atomic_rename(self):
        payload = b"pinned Sparkle fixture\n"
        digest = hashlib.sha256(payload).hexdigest()

        def runner(command, **_kwargs):
            output = Path(command[command.index("--output") + 1])
            output.write_bytes(payload)
            return SimpleNamespace(stdout="", stderr="")

        with mock.patch.object(sparkle, "SPARKLE_ARCHIVE_BYTES", len(payload)), \
                mock.patch.object(sparkle, "SPARKLE_ARCHIVE_SHA256", digest):
            final = sparkle.download_archive(self.root, runner=runner)
        self.assertEqual(payload, final.read_bytes())
        self.assertFalse((self.root / (sparkle.SPARKLE_ARCHIVE_NAME + ".part")).exists())

    def test_download_hash_mismatch_never_creates_final_name(self):
        payload = b"tampered"

        def runner(command, **_kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(payload)
            return SimpleNamespace(stdout="", stderr="")

        with mock.patch.object(sparkle, "SPARKLE_ARCHIVE_BYTES", len(payload)), \
                mock.patch.object(sparkle, "SPARKLE_ARCHIVE_SHA256", "0" * 64), \
                self.assertRaisesRegex(
                    sparkle.SparkleAcquisitionError, "SHA-256 mismatch"
                ):
            sparkle.download_archive(self.root, runner=runner)
        self.assertFalse((self.root / sparkle.SPARKLE_ARCHIVE_NAME).exists())
        self.assertTrue(
            (self.root / (sparkle.SPARKLE_ARCHIVE_NAME + ".part")).exists()
        )

    def test_archive_rejects_path_traversal_and_absolute_paths(self):
        for unsafe in ("../escape", "/absolute", "folder\\windows", "a/../../b"):
            with self.subTest(unsafe=unsafe):
                path = self.root / (hashlib.sha256(unsafe.encode()).hexdigest() + ".tar.xz")
                with tarfile.open(path, "w:xz") as archive:
                    self._add_file(archive, unsafe)
                    self._add_file(archive, "LICENSE")
                with tarfile.open(path, "r:xz") as archive, \
                        mock.patch.object(sparkle, "EXPECTED_MACHO_PATHS", ()):
                    with self.assertRaisesRegex(
                        sparkle.SparkleAcquisitionError, "unsafe path"
                    ):
                        sparkle.validated_archive_members(archive)

    def test_archive_rejects_escaping_symlink_and_hardlink(self):
        escaping = self._archive(
            [
                ("file", "LICENSE"),
                ("symlink", "Sparkle.framework/escape", "../../outside"),
            ]
        )
        with tarfile.open(escaping, "r:xz") as archive, \
                mock.patch.object(sparkle, "EXPECTED_MACHO_PATHS", ()):
            with self.assertRaisesRegex(sparkle.SparkleAcquisitionError, "escapes"):
                sparkle.validated_archive_members(archive)

        hardlink = self.root / "hardlink.tar.xz"
        with tarfile.open(hardlink, "w:xz") as archive:
            self._add_file(archive, "LICENSE")
            member = tarfile.TarInfo("Sparkle.framework/hardlink")
            member.type = tarfile.LNKTYPE
            member.linkname = "LICENSE"
            archive.addfile(member)
        with tarfile.open(hardlink, "r:xz") as archive, \
                mock.patch.object(sparkle, "EXPECTED_MACHO_PATHS", ()):
            with self.assertRaisesRegex(
                sparkle.SparkleAcquisitionError, "unsupported member type"
            ):
                sparkle.validated_archive_members(archive)

    def test_extractor_allows_only_framework_tools_and_license(self):
        path = self._archive(
            [
                ("file", "./LICENSE", b"license", 0o644),
                (
                    "file",
                    "./Sparkle.framework/Versions/B/Sparkle",
                    b"framework",
                    0o755,
                ),
                (
                    "symlink",
                    "./Sparkle.framework/Sparkle",
                    "Versions/B/Sparkle",
                ),
                ("file", "./bin/generate_appcast", b"tool", 0o755),
                ("file", "./Symbols/private", b"symbols", 0o644),
                ("file", "./Sparkle Test App.app/Contents/test", b"test", 0o644),
            ]
        )
        destination = self.root / "extracted"
        destination.mkdir()
        with mock.patch.object(
            sparkle,
            "EXPECTED_MACHO_PATHS",
            (
                "Sparkle.framework/Versions/B/Sparkle",
                "bin/generate_appcast",
            ),
        ), mock.patch.object(
            sparkle, "RELEASE_TOOL_PATHS", ("bin/generate_appcast",)
        ):
            sparkle.extract_archive(path, destination)
        self.assertEqual(b"license", (destination / "LICENSE").read_bytes())
        self.assertEqual(
            b"framework",
            (destination / "Sparkle.framework/Versions/B/Sparkle").read_bytes(),
        )
        self.assertEqual(
            "Versions/B/Sparkle",
            os.readlink(destination / "Sparkle.framework/Sparkle"),
        )
        self.assertEqual(b"tool", (destination / "bin/generate_appcast").read_bytes())
        self.assertFalse((destination / "Symbols").exists())
        self.assertFalse((destination / "Sparkle Test App.app").exists())

    def _payload_fixture(self):
        framework = self.root / "payload/Sparkle.framework"
        tools = self.root / "payload/bin"
        tools.mkdir(parents=True)
        payload_root = framework.parent
        license_payload = b"Sparkle fixture license\n"
        (payload_root / "LICENSE").write_bytes(license_payload)

        binary_hashes = {}
        for index, relative in enumerate(sparkle.EXPECTED_MACHO_PATHS):
            path = payload_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = bytes.fromhex("cafebabe") + bytes([index]) + b"universal fixture"
            path.write_bytes(data)
            path.chmod(0o755)
            binary_hashes[relative] = hashlib.sha256(data).hexdigest()

        for relative, (identifier, package_type) in sparkle.EXPECTED_BUNDLES.items():
            path = payload_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as stream:
                import plistlib

                plistlib.dump(
                    {
                        "CFBundleIdentifier": identifier,
                        "CFBundlePackageType": package_type,
                        "CFBundleShortVersionString": sparkle.SPARKLE_VERSION,
                    },
                    stream,
                )

        version_root = framework / "Versions/B"
        for name in ("Headers", "Modules", "PrivateHeaders", "Resources"):
            (version_root / name).mkdir(parents=True, exist_ok=True)
        current = framework / "Versions/Current"
        current.symlink_to("B")
        for relative, target in sparkle.EXPECTED_FRAMEWORK_SYMLINKS.items():
            if relative == "Sparkle.framework/Versions/Current":
                continue
            path = payload_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue((path.parent / target).exists(), target)
            path.symlink_to(target)
            os.chmod(path, 0o777, follow_symlinks=False)
        return payload_root, license_payload, binary_hashes

    def test_payload_requires_exact_universal_inventory_and_writes_auditable_report(self):
        payload_root, license_payload, binary_hashes = self._payload_fixture()
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            if command[0] == "/usr/bin/lipo":
                return SimpleNamespace(stdout="x86_64 arm64\n", stderr="")
            return SimpleNamespace(stdout="", stderr="")

        with mock.patch.object(
            sparkle,
            "SPARKLE_LICENSE_SHA256",
            hashlib.sha256(license_payload).hexdigest(),
        ), mock.patch.object(
            sparkle, "EXPECTED_BINARY_SHA256", binary_hashes
        ), mock.patch.object(
            sparkle,
            "EXPECTED_FRAMEWORK_SUBTREE_SHA256",
            sparkle.framework_subtree_sha256(
                sparkle.framework_subtree_manifest(
                    payload_root / "Sparkle.framework"
                )
            ),
        ):
            report = sparkle.validate_payload(payload_root, runner=runner)
        self.assertTrue(report["codesign_verified"])
        self.assertEqual(
            set(sparkle.EXPECTED_MACHO_PATHS), set(report["architectures"])
        )
        for architectures in report["architectures"].values():
            self.assertEqual(["arm64", "x86_64"], architectures)
        self.assertEqual(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict"],
            commands[-1][:-1],
        )

    def test_payload_rejects_extra_architecture(self):
        payload_root, license_payload, binary_hashes = self._payload_fixture()

        def runner(command, **_kwargs):
            if command[0] == "/usr/bin/lipo":
                return SimpleNamespace(stdout="x86_64 arm64 ppc\n", stderr="")
            return SimpleNamespace(stdout="", stderr="")

        with mock.patch.object(
            sparkle,
            "SPARKLE_LICENSE_SHA256",
            hashlib.sha256(license_payload).hexdigest(),
        ), mock.patch.object(
            sparkle, "EXPECTED_BINARY_SHA256", binary_hashes
        ), mock.patch.object(
            sparkle,
            "EXPECTED_FRAMEWORK_SUBTREE_SHA256",
            sparkle.framework_subtree_sha256(
                sparkle.framework_subtree_manifest(
                    payload_root / "Sparkle.framework"
                )
            ),
        ), self.assertRaisesRegex(
            sparkle.SparkleAcquisitionError, "exactly arm64 and x86_64"
        ):
            sparkle.validate_payload(payload_root, runner=runner)

    def test_receipt_never_contains_a_private_update_key(self):
        payload_report = {
            "architectures": {},
            "binary_sha256": {},
            "codesign_verified": True,
            "license_sha256": sparkle.SPARKLE_LICENSE_SHA256,
            "symlinks": {},
        }
        report = sparkle.receipt(payload_report)
        self.assertFalse(report["private_update_key_included"])
        self.assertEqual(sparkle.SPARKLE_ARCHIVE_SHA256, report["source"]["sha256"])
        self.assertNotIn("private", json.dumps(report["payload"]).lower())

    def test_completed_dependency_root_requires_exact_receipt_and_payload(self):
        payload_root, license_payload, binary_hashes = self._payload_fixture()
        payload_root.chmod(0o700)
        archive_payload = b"pinned archive fixture"
        archive_path = payload_root / sparkle.SPARKLE_ARCHIVE_NAME
        archive_path.write_bytes(archive_payload)
        archive_path.chmod(0o644)
        framework_digest = sparkle.framework_subtree_sha256(
            sparkle.framework_subtree_manifest(
                payload_root / "Sparkle.framework"
            )
        )

        def runner(command, **_kwargs):
            if command[0] == "/usr/bin/lipo":
                return SimpleNamespace(stdout="arm64 x86_64\n", stderr="")
            return SimpleNamespace(stdout="", stderr="")

        with mock.patch.object(
            sparkle,
            "SPARKLE_LICENSE_SHA256",
            hashlib.sha256(license_payload).hexdigest(),
        ), mock.patch.object(
            sparkle, "EXPECTED_BINARY_SHA256", binary_hashes
        ), mock.patch.object(
            sparkle, "SPARKLE_ARCHIVE_BYTES", len(archive_payload)
        ), mock.patch.object(
            sparkle,
            "SPARKLE_ARCHIVE_SHA256",
            hashlib.sha256(archive_payload).hexdigest(),
        ), mock.patch.object(
            sparkle, "EXPECTED_FRAMEWORK_SUBTREE_SHA256", framework_digest
        ):
            payload_report = sparkle.validate_payload(payload_root, runner=runner)
            receipt = sparkle.receipt(payload_report)
            receipt_path = payload_root / sparkle.RECEIPT_NAME
            receipt_path.write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = sparkle.validate_dependency_root(payload_root, runner=runner)
            self.assertEqual(
                hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                report["receipt_sha256"],
            )
            manifest = sparkle.framework_subtree_manifest(
                payload_root / "Sparkle.framework"
            )
            self.assertEqual(len(manifest), report["framework_entries"])
            self.assertEqual(
                sparkle.framework_subtree_sha256(manifest),
                report["framework_subtree_sha256"],
            )
            self.assertTrue(
                all("mode" in entry for entry in manifest.values())
            )

            changed = dict(receipt)
            changed["source"] = dict(changed["source"])
            changed["source"]["sha256"] = "0" * 64
            receipt_path.write_text(
                json.dumps(changed, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                sparkle.SparkleAcquisitionError, "receipt does not match"
            ):
                sparkle.validate_dependency_root(payload_root, runner=runner)

            receipt_path.write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            extra = payload_root / "UNEXPECTED.txt"
            extra.write_bytes(b"unexpected")
            with self.assertRaisesRegex(
                sparkle.SparkleAcquisitionError, "root inventory mismatch"
            ):
                sparkle.validate_dependency_root(payload_root, runner=runner)
            extra.unlink()

            tool_extra = payload_root / "bin/unexpected-tool"
            tool_extra.write_bytes(b"unexpected")
            with self.assertRaisesRegex(
                sparkle.SparkleAcquisitionError, "release-tool inventory mismatch"
            ):
                sparkle.validate_dependency_root(payload_root, runner=runner)
            tool_extra.unlink()

            archive_path.write_bytes(b"not the pinned archive")
            with self.assertRaisesRegex(
                sparkle.SparkleAcquisitionError,
                r"archive (?:size|SHA-256) mismatch",
            ):
                sparkle.validate_dependency_root(payload_root, runner=runner)
            archive_path.write_bytes(archive_payload)

            product = payload_root / sparkle.EXPECTED_MACHO_PATHS[0]
            product.chmod(0o644)
            with self.assertRaisesRegex(
                sparkle.SparkleAcquisitionError, "executable mode 0755"
            ):
                sparkle.validate_dependency_root(payload_root, runner=runner)
            product.chmod(0o755)

            receipt_path.chmod(0o666)
            with self.assertRaisesRegex(
                sparkle.SparkleAcquisitionError, "top-level mode inventory"
            ):
                sparkle.validate_dependency_root(payload_root, runner=runner)
            receipt_path.chmod(0o644)

            product = payload_root / sparkle.EXPECTED_MACHO_PATHS[0]
            product.write_bytes(product.read_bytes() + b"tampered")
            with self.assertRaisesRegex(
                sparkle.SparkleAcquisitionError, "binary SHA-256 mismatch"
            ):
                sparkle.validate_dependency_root(payload_root, runner=runner)

    def test_dependency_root_rejects_missing_or_noncanonical_receipt(self):
        payload_root, _license_payload, _binary_hashes = self._payload_fixture()
        payload_root.chmod(0o700)
        with self.assertRaisesRegex(
            sparkle.SparkleAcquisitionError, "missing Sparkle dependency receipt"
        ):
            sparkle.validate_dependency_root(payload_root)

        (payload_root / sparkle.RECEIPT_NAME).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(
            sparkle.SparkleAcquisitionError, "not canonically encoded"
        ):
            sparkle.validate_dependency_root(payload_root)

    def test_dependency_root_requires_framework_license_and_unique_receipt_keys(self):
        root = self.root / "dependency-shape"
        root.mkdir()
        root.chmod(0o700)
        with self.assertRaisesRegex(
            sparkle.SparkleAcquisitionError, "missing real Sparkle.framework"
        ):
            sparkle.validate_dependency_root(root)

        (root / "Sparkle.framework").mkdir()
        with self.assertRaisesRegex(
            sparkle.SparkleAcquisitionError, "missing regular LICENSE"
        ):
            sparkle.validate_dependency_root(root)

        (root / "LICENSE").write_bytes(b"license")
        with self.assertRaisesRegex(
            sparkle.SparkleAcquisitionError, "missing Sparkle dependency receipt"
        ):
            sparkle.validate_dependency_root(root)

        receipt = root / sparkle.RECEIPT_NAME
        receipt.write_text('{"source": 1, "source": 2}\n', encoding="utf-8")
        with self.assertRaisesRegex(
            sparkle.SparkleAcquisitionError, "duplicate key"
        ):
            sparkle.validate_dependency_root(root)

    def test_framework_manifest_detects_file_symlink_and_inventory_drift(self):
        payload_root, _license_payload, _binary_hashes = self._payload_fixture()
        framework = payload_root / "Sparkle.framework"
        original = sparkle.framework_subtree_manifest(framework)
        original_digest = sparkle.framework_subtree_sha256(original)
        extra = framework / "Versions/B/Resources/extra.txt"
        extra.write_bytes(b"extra")
        changed = sparkle.framework_subtree_manifest(framework)
        self.assertNotEqual(original, changed)
        self.assertNotEqual(
            original_digest, sparkle.framework_subtree_sha256(changed)
        )

    def test_framework_metadata_rejects_arbitrary_xattr_and_hardlink_drift(self):
        payload_root, _license_payload, _binary_hashes = self._payload_fixture()
        framework = payload_root / "Sparkle.framework"
        report = sparkle.validate_framework_metadata(framework)
        self.assertTrue(report["arbitrary_xattrs_prohibited"])

        product = payload_root / sparkle.EXPECTED_MACHO_PATHS[0]
        subprocess.run(
            ["/usr/bin/xattr", "-w", "com.example.unexpected", "value", str(product)],
            check=True,
        )
        with self.assertRaisesRegex(
            sparkle.SparkleAcquisitionError, "prohibited extended attribute"
        ):
            sparkle.validate_framework_metadata(framework)
        subprocess.run(
            ["/usr/bin/xattr", "-d", "com.example.unexpected", str(product)],
            check=True,
        )

        rival = self.root / "hardlink-rival"
        os.link(str(product), str(rival))
        with self.assertRaisesRegex(
            sparkle.SparkleAcquisitionError, "hardlinks are prohibited"
        ):
            sparkle.validate_framework_metadata(framework)
        rival.unlink()

    def test_dependency_root_publication_is_exclusive_and_preserves_rival(self):
        parent = self.root / "exclusive"
        parent.mkdir()
        staging = parent / ".staging"
        staging.mkdir()
        (staging / "value").write_bytes(b"accepted")
        final = parent / "sparkle"
        sparkle._publish_dependency_root_exclusive(staging, final)
        self.assertEqual(b"accepted", (final / "value").read_bytes())

        second = parent / ".second"
        second.mkdir()
        (second / "value").write_bytes(b"second")
        with self.assertRaisesRegex(
            sparkle.SparkleAcquisitionError, "refusing to replace"
        ):
            sparkle._publish_dependency_root_exclusive(second, final)
        self.assertEqual(b"accepted", (final / "value").read_bytes())
        self.assertEqual(b"second", (second / "value").read_bytes())

    def test_post_rename_fsync_failure_has_typed_uncertain_state(self):
        parent = self.root / "uncertain-publication"
        parent.mkdir()
        staging = parent / ".staging"
        staging.mkdir()
        (staging / "value").write_bytes(b"accepted")
        final = parent / "sparkle"

        with mock.patch("os.fsync", side_effect=OSError("synthetic fsync failure")):
            with self.assertRaisesRegex(
                sparkle.UncertainSparklePublicationError,
                "publication state is uncertain",
            ) as raised:
                sparkle._publish_dependency_root_exclusive(staging, final)

        self.assertEqual(str(final), raised.exception.destination)
        self.assertFalse(staging.exists())
        self.assertEqual(b"accepted", (final / "value").read_bytes())
        observed = final.lstat()
        self.assertEqual(
            (observed.st_dev, observed.st_ino), raised.exception.final_identity
        )

    def test_post_fsync_identity_failure_has_typed_committed_state(self):
        parent = self.root / "committed-publication"
        parent.mkdir()
        staging = parent / ".staging"
        staging.mkdir()
        (staging / "value").write_bytes(b"accepted")
        final = parent / "sparkle"
        real_lstat = os.lstat
        parent_observations = 0

        def drift_parent_after_commit(path, *args, **kwargs):
            nonlocal parent_observations
            observed = real_lstat(path, *args, **kwargs)
            if Path(path) == parent:
                parent_observations += 1
                if parent_observations == 2:
                    return SimpleNamespace(
                        st_dev=observed.st_dev,
                        st_ino=observed.st_ino + 1,
                    )
            return observed

        with mock.patch("os.lstat", side_effect=drift_parent_after_commit):
            with self.assertRaisesRegex(
                sparkle.CommittedSparklePublicationError,
                "remains committed",
            ) as raised:
                sparkle._publish_dependency_root_exclusive(staging, final)

        self.assertEqual(str(final), raised.exception.destination)
        self.assertFalse(staging.exists())
        self.assertEqual(b"accepted", (final / "value").read_bytes())
        observed = final.lstat()
        self.assertEqual(
            (observed.st_dev, observed.st_ino), raised.exception.final_identity
        )

    def test_acquire_never_cleans_consumed_staging_path_after_uncertain_rename(self):
        final = self.root / "sparkle-acquired"
        removed = []

        def rename_then_report_uncertain(staging, destination):
            staging.rename(destination)
            observed = destination.lstat()
            raise sparkle.UncertainSparklePublicationError(
                "synthetic post-rename interruption",
                destination,
                (observed.st_dev, observed.st_ino),
            )

        def record_rmtree(path):
            removed.append(Path(path))

        with mock.patch.object(
            sparkle.platform, "system", return_value="Darwin"
        ), mock.patch.object(
            sparkle, "validate_destination", return_value=final
        ), mock.patch.object(
            sparkle, "download_archive", return_value=self.root / "archive"
        ), mock.patch.object(
            sparkle, "extract_archive"
        ), mock.patch.object(
            sparkle, "validate_payload", return_value={"payload": "fixture"}
        ), mock.patch.object(
            sparkle, "receipt", return_value={"receipt": "fixture"}
        ), mock.patch.object(
            sparkle, "_write_receipt"
        ), mock.patch.object(
            sparkle, "validate_dependency_root"
        ), mock.patch.object(
            sparkle, "_fsync_directory"
        ), mock.patch.object(
            sparkle,
            "_publish_dependency_root_exclusive",
            side_effect=rename_then_report_uncertain,
        ), mock.patch.object(
            sparkle.shutil, "rmtree", side_effect=record_rmtree
        ), self.assertRaises(
            sparkle.UncertainSparklePublicationError
        ):
            sparkle.acquire(final)

        self.assertEqual([], removed)
        self.assertTrue(final.is_dir())

    def test_cli_defaults_to_read_only_preflight(self):
        destination = self.root / "sparkle-dependency"
        output = io.StringIO()
        with redirect_stdout(output), mock.patch.object(sparkle, "acquire") as acquire:
            result = sparkle.main(["--destination", str(destination)])
        self.assertEqual(0, result)
        acquire.assert_not_called()
        parsed = json.loads(output.getvalue())
        self.assertEqual("preflight_only", parsed["status"])
        self.assertFalse(destination.exists())

    def test_cli_validate_root_is_offline_and_reports_receipt_identity(self):
        destination = self.root / "existing-dependency"
        destination.mkdir()
        validation = {
            "root": str(destination),
            "receipt_sha256": "a" * 64,
            "framework_subtree_sha256": "b" * 64,
        }
        output = io.StringIO()
        with redirect_stdout(output), mock.patch.object(
            sparkle,
            "validate_dependency_root",
            return_value=validation,
        ) as validate, mock.patch.object(sparkle, "acquire") as acquire:
            result = sparkle.main(
                [
                    "--destination",
                    str(destination),
                    "--validate-root",
                ]
            )
        self.assertEqual(0, result)
        acquire.assert_not_called()
        validate.assert_called_once_with(destination)
        parsed = json.loads(output.getvalue())
        self.assertEqual("dependency_root_valid", parsed["status"])
        self.assertEqual("a" * 64, parsed["receipt_sha256"])


if __name__ == "__main__":
    unittest.main()
