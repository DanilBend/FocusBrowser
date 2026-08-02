"""Tests for the fail-closed macOS Sparkle appcast helper."""

import base64
import hashlib
import importlib.util
import io
import json
import os
import plistlib
import shutil
import stat
import tempfile
import textwrap
import unittest
import warnings
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PLATFORM_DIR / "prepare_sparkle_appcast.py"
SPEC = importlib.util.spec_from_file_location("prepare_sparkle_appcast", MODULE_PATH)
prepare_sparkle_appcast = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_sparkle_appcast)


PUBLIC_KEY_BYTES = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
)
# Public RFC 8032 test vector seed; never release signing material.
RFC8032_TEST_SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
PUBLIC_KEY = base64.b64encode(PUBLIC_KEY_BYTES).decode("ascii")
FAKE_SIGNATURE = base64.b64encode(bytes(range(64))).decode("ascii")


class SparkleAppcastTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.sparkle_root = self.root / "sparkle-dependency"
        (self.sparkle_root / "bin").mkdir(parents=True)
        self.tool = self.sparkle_root / "bin/sign_update"
        self.calls = self.root / "sign_update.calls"
        self._write_fake_tool()
        self.short_version = "1.0.6"
        self.version = "1.0.6.0"
        self.payload = (
            self.root
            / "FocusBrowser-macOS-1.0.6-universal-autoupdate.zip"
        )
        self.info = self.valid_info()
        self.write_payload()
        self.output = self.root / "appcast-macos.xml"

    def tearDown(self):
        self.temporary.cleanup()

    def _write_fake_tool(self):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import base64
            import json
            import os
            import re
            import sys

            signature = base64.b64encode(bytes(range(64))).decode("ascii")
            args = sys.argv[1:]
            with open(__file__ + ".calls", "a", encoding="utf-8") as stream:
                stream.write(json.dumps(args) + "\\n")
            if len(args) < 3 or args[0] != "--account":
                sys.exit(90)
            account = args[1]
            rest = args[2:]
            if account == "fail":
                print("DO-NOT-LEAK-PRIVATE-KEY-MATERIAL", file=sys.stderr)
                sys.exit(7)
            print_only = False
            if rest[0] == "-p":
                print_only = True
                rest = rest[1:]
            if rest[0] == "--verify":
                if len(rest) == 2:
                    data = open(rest[1], "rb").read()
                    pattern = (
                        rb"<!-- sparkle-signatures:\\n"
                        rb"edSignature: [A-Za-z0-9+/]{86}==\\n"
                        rb"length: ([0-9]+)\\n-->\\n$"
                    )
                    match = re.search(pattern, data)
                    if match is None or int(match.group(1)) != match.start():
                        sys.exit(8)
                elif len(rest) != 3 or rest[2] != signature:
                    sys.exit(9)
                sys.exit(0)
            if rest[0] == "--disable-signing-warning":
                path = rest[1]
                if account == "badfeed":
                    sys.exit(0)
                content = open(path, "rb").read()
                marker = b"<!-- sparkle-signatures:\\n"
                if marker in content:
                    content = content[:content.index(marker)]
                block = (
                    "<!-- sparkle-signatures:\\n"
                    "edSignature: {}\\n"
                    "length: {}\\n"
                    "-->\\n"
                ).format(signature, len(content)).encode("ascii")
                replacement = path + ".replacement"
                with open(replacement, "wb") as stream:
                    stream.write(content + block)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(replacement, 0o600)
                os.replace(replacement, path)
                if not print_only:
                    print("<!-- Updated signature inside {} -->".format(path))
                sys.exit(0)
            if len(rest) != 1:
                sys.exit(91)
            if account == "badformat":
                print("not a signature field")
            else:
                print('sparkle:edSignature="{}" length="{}"'.format(
                    signature, os.path.getsize(rest[0])))
            """
        )
        script = script.replace(
            'open(__file__ + ".calls", "a", encoding="utf-8")',
            'open({!r}, "a", encoding="utf-8")'.format(str(self.calls)),
        )
        self.tool.write_text(script, encoding="utf-8")
        self.tool.chmod(0o755)

    def valid_info(self):
        return {
            "CFBundleIdentifier": prepare_sparkle_appcast.BUNDLE_ID,
            "CFBundleVersion": self.version,
            "CFBundleShortVersionString": self.short_version,
            "LSMinimumSystemVersion": prepare_sparkle_appcast.MINIMUM_MACOS,
            "SUFeedURL": prepare_sparkle_appcast.FEED_URL,
            "SURequireSignedFeed": True,
            "SUVerifyUpdateBeforeExtraction": True,
            "SUPublicEDKey": PUBLIC_KEY,
        }

    @staticmethod
    def regular_zip_info(name):
        value = zipfile.ZipInfo(name)
        value.external_attr = (stat.S_IFREG | 0o600) << 16
        return value

    def write_payload(self, extra_entries=()):
        with zipfile.ZipFile(self.payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                self.regular_zip_info(
                    "Focus Browser.app/Contents/Info.plist"
                ),
                plistlib.dumps(self.info, fmt=plistlib.FMT_BINARY),
            )
            archive.writestr(
                self.regular_zip_info(
                    "Focus Browser.app/Contents/MacOS/focus_browser"
                ),
                b"mock Mach-O",
            )
            for name, value in extra_entries:
                archive.writestr(self.regular_zip_info(name), value)
        self.payload.chmod(0o600)

    def common_args(self, mode="generate", account="FocusBrowserUpdates"):
        size = self.payload.stat().st_size
        digest = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        values = [mode]
        if mode != "validate-public":
            values.extend(
                (
                    "--sparkle-tool",
                    str(self.tool),
                    "--sparkle-source-root",
                    str(self.sparkle_root),
                    "--keychain-account",
                    account,
                )
            )
        values.extend(
            [
                "--public-key",
                PUBLIC_KEY,
                "--payload",
                str(self.payload),
                "--expected-size",
                str(size),
                "--expected-sha256",
                digest,
                "--asset-url",
                "https://github.com/DanilBend/FocusBrowser/releases/download/"
                "v1.0.6-macos/FocusBrowser-macOS-1.0.6-universal-autoupdate.zip",
                "--release-url",
                "https://github.com/DanilBend/FocusBrowser/releases/tag/v1.0.6-macos",
                "--version",
                self.version,
                "--short-version",
                self.short_version,
                "--published-at",
                "2026-07-31T12:34:56Z",
            ]
        )
        if mode == "generate":
            values.extend(("--output", str(self.output)))
        else:
            values.extend(("--appcast", str(self.output)))
        return values

    @staticmethod
    def sign_for_public_test(content):
        expanded = hashlib.sha512(RFC8032_TEST_SEED).digest()
        scalar_bytes = bytearray(expanded[:32])
        scalar_bytes[0] &= 248
        scalar_bytes[31] &= 63
        scalar_bytes[31] |= 64
        private_scalar = int.from_bytes(scalar_bytes, "little")
        derived_public = prepare_sparkle_appcast._encode_point(
            prepare_sparkle_appcast._scalar_multiply(
                prepare_sparkle_appcast._ED_BASE, private_scalar
            )
        )
        if derived_public != PUBLIC_KEY_BYTES:
            raise AssertionError("RFC 8032 test seed does not match public key")
        nonce = int.from_bytes(
            hashlib.sha512(expanded[32:] + content).digest(), "little"
        ) % prepare_sparkle_appcast._ED_L
        encoded_nonce = prepare_sparkle_appcast._encode_point(
            prepare_sparkle_appcast._scalar_multiply(
                prepare_sparkle_appcast._ED_BASE, nonce
            )
        )
        challenge = int.from_bytes(
            hashlib.sha512(encoded_nonce + PUBLIC_KEY_BYTES + content).digest(),
            "little",
        ) % prepare_sparkle_appcast._ED_L
        response = (
            nonce + challenge * private_scalar
        ) % prepare_sparkle_appcast._ED_L
        return encoded_nonce + response.to_bytes(32, "little")

    def write_publicly_signed_appcast(self):
        arguments = self.common_args("validate-public")
        parsed = prepare_sparkle_appcast.build_parser().parse_args(arguments)
        contract = prepare_sparkle_appcast._build_contract(
            parsed, require_signing=False
        )
        archive_signature = base64.b64encode(
            self.sign_for_public_test(self.payload.read_bytes())
        ).decode("ascii")
        unsigned = prepare_sparkle_appcast._render_unsigned_appcast(
            contract, archive_signature
        )
        feed_signature = base64.b64encode(
            self.sign_for_public_test(unsigned)
        ).decode("ascii")
        signed_block = (
            "<!-- sparkle-signatures:\n"
            "edSignature: {}\n"
            "length: {}\n"
            "-->\n"
        ).format(feed_signature, len(unsigned)).encode("ascii")
        self.output.write_bytes(unsigned + signed_block)
        self.output.chmod(0o600)
        return arguments

    @staticmethod
    def replace_arg(arguments, option, value):
        result = list(arguments)
        result[result.index(option) + 1] = value
        return result

    def run_main(self, arguments, verify=True):
        stdout = io.StringIO()
        stderr = io.StringIO()
        tool_path = (
            Path(arguments[arguments.index("--sparkle-tool") + 1])
            if arguments[:1] != ["validate-public"]
            and "--sparkle-tool" in arguments
            else self.tool
        )
        tool_digest = hashlib.sha256(tool_path.read_bytes()).hexdigest()
        dependency_report = {
            "root": str(self.sparkle_root),
            "payload": {
                "binary_sha256": {"bin/sign_update": tool_digest},
                "binary_modes": {"bin/sign_update": "0755"},
            },
        }
        patches = [
            mock.patch.object(
                prepare_sparkle_appcast.acquire_sparkle,
                "validate_dependency_root",
                return_value=dependency_report,
            ),
            mock.patch.object(
                prepare_sparkle_appcast.acquire_sparkle,
                "EXPECTED_BINARY_SHA256",
                {
                    **prepare_sparkle_appcast.acquire_sparkle.EXPECTED_BINARY_SHA256,
                    "bin/sign_update": tool_digest,
                },
            ),
        ]
        if verify:
            patches.extend([
                mock.patch.object(
                    prepare_sparkle_appcast,
                    "_verify_ed25519_file",
                    return_value=True,
                ),
                mock.patch.object(
                    prepare_sparkle_appcast,
                    "_verify_ed25519_bytes",
                    return_value=True,
                ),
            ])
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = prepare_sparkle_appcast.main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def generate(self):
        status, stdout, stderr = self.run_main(self.common_args())
        self.assertEqual(0, status, stderr)
        return json.loads(stdout)

    def recorded_calls(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_generate_creates_and_validates_one_signed_appcast(self):
        report = self.generate()
        self.assertEqual("generate", report["mode"])
        self.assertEqual(self.output, Path(report["appcast"]))
        self.assertEqual(self.payload.stat().st_size, report["payload_size"])
        self.assertEqual(
            hashlib.sha256(self.output.read_bytes()).hexdigest(),
            report["appcast_sha256"],
        )
        value = self.output.read_bytes()
        self.assertIn(b"sparkle:edSignature=", value)
        self.assertRegex(value, prepare_sparkle_appcast.SIGNED_FEED_BLOCK_RE)
        calls = self.recorded_calls()
        self.assertEqual(5, len(calls))
        for call in calls:
            self.assertEqual(
                ["--account", "FocusBrowserUpdates"], call[:2]
            )
            self.assertTrue(
                prepare_sparkle_appcast.FORBIDDEN_PRIVATE_KEY_OPTIONS.isdisjoint(call)
            )
        feed_sign_calls = [
            call for call in calls
            if "--disable-signing-warning" in call
        ]
        self.assertEqual(1, len(feed_sign_calls))
        self.assertIn("-p", feed_sign_calls[0])

    def test_signing_executes_only_a_private_hash_bound_tool_copy(self):
        original_run = prepare_sparkle_appcast._run_command
        observed = []

        def capture(
            command,
            label,
            timeout=prepare_sparkle_appcast.TOOL_TIMEOUT_SECONDS,
        ):
            tool = Path(command[0])
            observed.append(tool)
            self.assertNotEqual(self.tool, tool)
            self.assertEqual("sign_update", tool.name)
            self.assertEqual(0o500, stat.S_IMODE(tool.parent.stat().st_mode))
            self.assertEqual(
                hashlib.sha256(self.tool.read_bytes()).hexdigest(),
                hashlib.sha256(tool.read_bytes()).hexdigest(),
            )
            return original_run(command, label, timeout=timeout)

        with mock.patch.object(
            prepare_sparkle_appcast, "_run_command", side_effect=capture
        ):
            self.generate()
        self.assertEqual(5, len(observed))
        self.assertTrue(all(not path.exists() for path in observed))

    def test_private_tool_mutation_is_detected_before_publication(self):
        original_run = prepare_sparkle_appcast._run_command
        mutated = False

        def mutate(
            command,
            label,
            timeout=prepare_sparkle_appcast.TOOL_TIMEOUT_SECONDS,
        ):
            nonlocal mutated
            result = original_run(command, label, timeout=timeout)
            if not mutated:
                Path(command[0]).chmod(0o700)
                with Path(command[0]).open("ab") as stream:
                    stream.write(b"tampered")
                mutated = True
            return result

        with mock.patch.object(
            prepare_sparkle_appcast, "_run_command", side_effect=mutate
        ):
            status, stdout, error = self.run_main(self.common_args())
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertIn("sign_update execution identity became ambiguous", error)
        retained = Path(error.rsplit("private signing state retained at ", 1)[1].strip())
        self.assertTrue(retained.is_dir())
        retained.chmod(0o700)
        shutil.rmtree(retained)
        self.assertFalse(self.output.exists())

    def test_swapped_signing_path_never_executes_after_descriptor_pin(self):
        private_root = self.root / "descriptor-bound-source"
        private_root.mkdir(mode=0o700)
        tool = private_root / "sign_update"
        trusted_marker = self.root / "trusted-executed"
        malicious_marker = self.root / "malicious-executed"
        tool.write_text(
            "#!/bin/sh\nprintf trusted > {!s}\n".format(trusted_marker),
            encoding="utf-8",
        )
        tool.chmod(0o500)
        metadata = tool.lstat()
        digest = hashlib.sha256(tool.read_bytes()).hexdigest()
        private_root.chmod(0o500)
        contract = prepare_sparkle_appcast.ReleaseContract(
            sparkle_tool=tool,
            sparkle_source_root=None,
            sparkle_tool_identity=prepare_sparkle_appcast._identity(metadata),
            sparkle_tool_sha256=digest,
            sparkle_tool_private=True,
            keychain_account="FocusBrowserUpdates",
            payload=self.payload,
            expected_size=self.payload.stat().st_size,
            expected_sha256=hashlib.sha256(self.payload.read_bytes()).hexdigest(),
            asset_url="https://example.invalid/payload.zip",
            release_url="https://example.invalid/release",
            version=self.version,
            short_version=self.short_version,
            published_at=None,
            public_key=PUBLIC_KEY_BYTES,
        )
        real_copy = prepare_sparkle_appcast._copy_descriptor_bound_tool

        def swap_named_source(source_fd, source_identity, expected_sha256, destination):
            private_root.chmod(0o700)
            tool.unlink()
            tool.write_text(
                "#!/bin/sh\nprintf malicious > {!s}\n".format(malicious_marker),
                encoding="utf-8",
            )
            tool.chmod(0o500)
            private_root.chmod(0o500)
            return real_copy(
                source_fd, source_identity, expected_sha256, destination
            )

        try:
            with mock.patch.object(
                prepare_sparkle_appcast,
                "_copy_descriptor_bound_tool",
                side_effect=swap_named_source,
            ), self.assertRaisesRegex(
                prepare_sparkle_appcast.AppcastError,
                "trusted sign_update identity or mode changed",
            ):
                prepare_sparkle_appcast._run_sparkle(
                    contract, (), "descriptor-bound signing test"
                )
            # Unlinking the named source changes the pinned inode's ctime, so
            # the descriptor copy itself fails closed before either pathname
            # can execute.  Most importantly, the racing replacement is never
            # invoked with Keychain arguments.
            self.assertFalse(trusted_marker.exists())
            self.assertFalse(malicious_marker.exists())
        finally:
            private_root.chmod(0o700)

    def test_candidate_path_replacement_cannot_cross_publication_gate(self):
        real_publish = (
            prepare_sparkle_appcast.package_local_dmg.durable_publish_candidate
        )

        def replace_candidate(candidate, output, identity, size, digest):
            candidate = Path(candidate)
            candidate.unlink()
            candidate.write_bytes(b"racing replacement")
            candidate.chmod(0o600)
            return real_publish(candidate, output, identity, size, digest)

        with mock.patch.object(
            prepare_sparkle_appcast.package_local_dmg,
            "durable_publish_candidate",
            side_effect=replace_candidate,
        ):
            status, stdout, error = self.run_main(self.common_args())
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertIn("descriptor-pinned appcast publication failed", error)
        self.assertFalse(self.output.exists())

    def test_appcast_read_rejects_replacement_between_lstat_and_open(self):
        self.output.write_bytes(b"first appcast inode")
        self.output.chmod(0o600)
        replacement = self.root / "replacement-appcast.xml"
        replacement.write_bytes(b"racing appcast inode")
        replacement.chmod(0o600)
        real_open = os.open
        replaced = False

        def replace_then_open(path, flags, *arguments):
            nonlocal replaced
            if Path(path) == self.output and not replaced:
                os.replace(str(replacement), str(self.output))
                replaced = True
            return real_open(path, flags, *arguments)

        with mock.patch.object(
            prepare_sparkle_appcast.os,
            "open",
            side_effect=replace_then_open,
        ), self.assertRaisesRegex(
            prepare_sparkle_appcast.AppcastError,
            "descriptor-pinned",
        ):
            prepare_sparkle_appcast._read_appcast_file(self.output)

        self.assertTrue(replaced)

    def test_validate_rechecks_existing_signed_appcast_without_resigning(self):
        self.generate()
        self.calls.unlink()
        status, stdout, stderr = self.run_main(self.common_args("validate"))
        self.assertEqual(0, status, stderr)
        self.assertEqual("validate", json.loads(stdout)["mode"])
        calls = self.recorded_calls()
        self.assertEqual(2, len(calls))
        self.assertTrue(all("--verify" in call for call in calls))

    def test_validate_public_uses_only_public_ed25519_verification(self):
        arguments = self.write_publicly_signed_appcast()
        with mock.patch.object(prepare_sparkle_appcast, "_run_command") as run:
            status, stdout, stderr = self.run_main(arguments, verify=False)
        self.assertEqual(0, status, stderr)
        report = json.loads(stdout)
        self.assertEqual("validate-public", report["mode"])
        self.assertTrue(report["signed_feed"])
        run.assert_not_called()
        self.assertEqual([], self.recorded_calls())

    def test_validate_public_rejects_tool_account_and_private_key_flags(self):
        arguments = self.write_publicly_signed_appcast()
        forbidden = (
            ("--sparkle-tool", "DO-NOT-ECHO-TOOL"),
            ("--keychain-account", "DO-NOT-ECHO-ACCOUNT"),
            ("--private-key", "DO-NOT-ECHO-PRIVATE-KEY"),
            ("--ed-key-file", "DO-NOT-ECHO-KEY-PATH"),
        )
        for option, value in forbidden:
            with self.subTest(option=option):
                status, stdout, error = self.run_main(
                    arguments + [option, value], verify=False
                )
                self.assertEqual(1, status)
                self.assertEqual("", stdout)
                self.assertNotIn(value, error)
        self.assertEqual([], self.recorded_calls())

    def test_validate_public_rejects_payload_tampering(self):
        arguments = self.write_publicly_signed_appcast()
        payload = bytearray(self.payload.read_bytes())
        payload[len(payload) // 2] ^= 1
        self.payload.write_bytes(payload)
        self.payload.chmod(0o600)
        status, _, error = self.run_main(arguments, verify=False)
        self.assertEqual(1, status)
        self.assertIn("SHA-256", error)
        self.assertEqual([], self.recorded_calls())

    def test_validate_public_rejects_signed_feed_tampering(self):
        arguments = self.write_publicly_signed_appcast()
        value = self.output.read_bytes().replace(
            b"Focus Browser 1.0.6", b"Focus Browser 9.0.6", 1
        )
        self.output.write_bytes(value)
        self.output.chmod(0o600)
        status, _, error = self.run_main(arguments, verify=False)
        self.assertEqual(1, status)
        self.assertIn("signed feed", error)
        self.assertEqual([], self.recorded_calls())

    def test_validate_public_rejects_bad_archive_signature_in_valid_feed(self):
        arguments = self.write_publicly_signed_appcast()
        signed = self.output.read_bytes()
        content, _ = prepare_sparkle_appcast._split_signed_feed(signed)
        valid_archive_signature = base64.b64encode(
            self.sign_for_public_test(self.payload.read_bytes())
        )
        invalid_archive_signature = base64.b64encode(b"\x00" * 64)
        self.assertIn(valid_archive_signature, content)
        self.assertEqual(len(valid_archive_signature), len(invalid_archive_signature))
        content = content.replace(
            valid_archive_signature, invalid_archive_signature, 1
        )
        feed_signature = base64.b64encode(
            self.sign_for_public_test(content)
        ).decode("ascii")
        block = (
            "<!-- sparkle-signatures:\n"
            "edSignature: {}\n"
            "length: {}\n"
            "-->\n"
        ).format(feed_signature, len(content)).encode("ascii")
        self.output.write_bytes(content + block)
        self.output.chmod(0o600)
        status, _, error = self.run_main(arguments, verify=False)
        self.assertEqual(1, status)
        self.assertIn("archive signature does not match", error)
        self.assertEqual([], self.recorded_calls())

    def test_exact_size_and_sha256_are_mandatory(self):
        arguments = self.common_args()
        wrong_size = self.replace_arg(
            arguments, "--expected-size", str(self.payload.stat().st_size + 1)
        )
        status, _, error = self.run_main(wrong_size)
        self.assertEqual(1, status)
        self.assertIn("size", error)
        self.assertEqual([], self.recorded_calls())

        wrong_digest = self.replace_arg(arguments, "--expected-sha256", "0" * 64)
        status, _, error = self.run_main(wrong_digest)
        self.assertEqual(1, status)
        self.assertIn("SHA-256", error)
        self.assertEqual([], self.recorded_calls())

    def test_sha256_must_be_lowercase_canonical(self):
        arguments = self.common_args()
        digest = arguments[arguments.index("--expected-sha256") + 1]
        arguments = self.replace_arg(
            arguments, "--expected-sha256", digest.upper()
        )
        status, _, error = self.run_main(arguments)
        self.assertEqual(1, status)
        self.assertIn("lowercase canonical", error)
        self.assertEqual([], self.recorded_calls())

    def test_urls_must_be_exact_versioned_github_release_urls(self):
        invalid_assets = (
            "https://github.com/DanilBend/FocusBrowser/releases/latest/download/"
            "FocusBrowser-macOS-1.0.6-universal-autoupdate.zip",
            "https://github.com/DanilBend/FocusBrowser/releases/download/v1.0.5/"
            "FocusBrowser-macOS-1.0.6-universal-autoupdate.zip",
            "https://github.com/DanilBend/FocusBrowser/releases/download/v1.0.6/"
            "FocusBrowser-macOS-1.0.6-universal-autoupdate.zip",
            "https://github.com/DanilBend/FocusBrowser/releases/download/v1.0.6-macos/"
            "other.zip",
            "https://github.com/DanilBend/FocusBrowser/releases/download/v1.0.6-macos/"
            "FocusBrowser-macOS-1.0.6-universal-autoupdate.zip?download=1",
            "http://github.com/DanilBend/FocusBrowser/releases/download/v1.0.6-macos/"
            "FocusBrowser-macOS-1.0.6-universal-autoupdate.zip",
        )
        for value in invalid_assets:
            with self.subTest(value=value):
                arguments = self.replace_arg(
                    self.common_args(), "--asset-url", value
                )
                status, _, _ = self.run_main(arguments)
                self.assertEqual(1, status)
        wrong_release = self.replace_arg(
            self.common_args(),
            "--release-url",
            "https://github.com/DanilBend/FocusBrowser/releases/tag/v1.0.6",
        )
        status, _, _ = self.run_main(wrong_release)
        self.assertEqual(1, status)
        self.assertEqual([], self.recorded_calls())

    def test_versions_are_exact_and_related(self):
        invalid = (
            ("--version", "1.0.6"),
            ("--version", "1.0.6.1"),
            ("--short-version", "1.0.6.0"),
            ("--short-version", "01.0.6"),
        )
        for option, value in invalid:
            with self.subTest(option=option, value=value):
                arguments = self.replace_arg(self.common_args(), option, value)
                status, _, _ = self.run_main(arguments)
                self.assertEqual(1, status)
        self.assertEqual([], self.recorded_calls())

    def test_payload_info_plist_contract_is_exact(self):
        invalid_values = {
            "CFBundleIdentifier": "org.example.FocusBrowser",
            "CFBundleVersion": "1.0.6.1",
            "CFBundleShortVersionString": "1.0.6.0",
            "LSMinimumSystemVersion": "11.0",
            "SUFeedURL": "https://example.org/appcast.xml",
            "SURequireSignedFeed": False,
            "SUVerifyUpdateBeforeExtraction": False,
            "SUPublicEDKey": base64.b64encode(b"x" * 32).decode("ascii"),
        }
        for key, value in invalid_values.items():
            with self.subTest(key=key):
                self.info = self.valid_info()
                self.info[key] = value
                self.write_payload()
                status, _, error = self.run_main(self.common_args())
                self.assertEqual(1, status)
                self.assertIn(key, error)
        self.assertEqual([], self.recorded_calls())

    def test_signed_feed_boolean_cannot_be_integer_one(self):
        self.info["SURequireSignedFeed"] = 1
        self.write_payload()
        status, _, error = self.run_main(self.common_args())
        self.assertEqual(1, status)
        self.assertIn("SURequireSignedFeed", error)

    def test_zip_rejects_traversal_and_unrelated_roots(self):
        for name in ("../escape", "/absolute", "Other.app/file", "bad\\name"):
            with self.subTest(name=name):
                self.write_payload(((name, b"bad"),))
                status, _, _ = self.run_main(self.common_args())
                self.assertEqual(1, status)
        self.assertEqual([], self.recorded_calls())

    def test_dmg_reader_mounts_read_only_reads_plist_and_always_detaches(self):
        dmg = self.root / "FocusBrowser-macOS-1.0.6-universal.dmg"
        dmg.write_bytes(b"dmg fixture")
        mounted = {"value": False}
        commands = []

        def run(command, label, timeout=prepare_sparkle_appcast.TOOL_TIMEOUT_SECONDS):
            del label, timeout
            commands.append(command)
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                info_path = (
                    mountpoint
                    / "Focus Browser.app"
                    / "Contents"
                    / "Info.plist"
                )
                info_path.parent.mkdir(parents=True)
                info_path.write_bytes(plistlib.dumps(self.valid_info()))
                mounted["value"] = True
            elif command[1] == "detach":
                mountpoint = Path(command[-1])
                shutil.rmtree(mountpoint / "Focus Browser.app")
                mounted["value"] = False
            return b"", b""

        with mock.patch.object(
            prepare_sparkle_appcast, "_run_command", side_effect=run
        ), mock.patch("os.path.ismount", side_effect=lambda _path: mounted["value"]), mock.patch(
            "os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
        ):
            info = prepare_sparkle_appcast._read_info_from_dmg(
                dmg
            )
        self.assertEqual(self.valid_info(), info)
        self.assertIn("-readonly", commands[0])
        self.assertIn("-nobrowse", commands[0])
        self.assertEqual("pinned-update.dmg", Path(commands[0][-1]).name)
        self.assertEqual("detach", commands[-1][1])
        self.assertFalse(mounted["value"])

    def test_dmg_reader_allows_hdiutil_checksum_ctime_change(self):
        dmg = self.root / "FocusBrowser-macOS-1.0.6-universal.dmg"
        dmg.write_bytes(b"dmg fixture")
        mounted = {"value": False}
        private_root = {"path": None}

        def run(command, label, timeout=prepare_sparkle_appcast.TOOL_TIMEOUT_SECONDS):
            del label, timeout
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                private_root["path"] = Path(command[-1]).parent
                # hdiutil may add a checksum xattr, which changes only ctime among
                # the fields relevant to our private-copy identity. Repeating the
                # exact mode exercises that same ctime-only cleanup case without
                # relying on Python builds that expose os.setxattr.
                os.chmod(command[-1], 0o600)
                info_path = (
                    mountpoint
                    / "Focus Browser.app"
                    / "Contents"
                    / "Info.plist"
                )
                info_path.parent.mkdir(parents=True)
                info_path.write_bytes(plistlib.dumps(self.valid_info()))
                mounted["value"] = True
            else:
                mountpoint = Path(command[-1])
                shutil.rmtree(mountpoint / "Focus Browser.app")
                mounted["value"] = False
            return b"", b""

        with mock.patch.object(
            prepare_sparkle_appcast, "_run_command", side_effect=run
        ), mock.patch(
            "os.path.ismount", side_effect=lambda _path: mounted["value"]
        ), mock.patch(
            "os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
        ):
            info = prepare_sparkle_appcast._read_info_from_dmg(dmg)
        self.assertEqual(self.valid_info(), info)
        self.assertFalse(private_root["path"].exists())

    def test_dmg_reader_retains_same_size_content_tamper(self):
        dmg = self.root / "FocusBrowser-macOS-1.0.6-universal.dmg"
        dmg.write_bytes(b"dmg fixture")
        mounted = {"value": False}

        def run(command, label, timeout=prepare_sparkle_appcast.TOOL_TIMEOUT_SECONDS):
            del label, timeout
            if command[1] == "attach":
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                Path(command[-1]).write_bytes(b"bad fixture")
                info_path = (
                    mountpoint
                    / "Focus Browser.app"
                    / "Contents"
                    / "Info.plist"
                )
                info_path.parent.mkdir(parents=True)
                info_path.write_bytes(plistlib.dumps(self.valid_info()))
                mounted["value"] = True
            else:
                mountpoint = Path(command[-1])
                shutil.rmtree(mountpoint / "Focus Browser.app")
                mounted["value"] = False
            return b"", b""

        with mock.patch.object(
            prepare_sparkle_appcast, "_run_command", side_effect=run
        ), mock.patch(
            "os.path.ismount", side_effect=lambda _path: mounted["value"]
        ), mock.patch(
            "os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
        ), self.assertRaisesRegex(
            prepare_sparkle_appcast.AppcastError,
            "DMG inspection private root was retained",
        ) as raised:
            prepare_sparkle_appcast._read_info_from_dmg(dmg)
        retained = Path(
            str(raised.exception).split("retained at ", 1)[1].split("; original=", 1)[0]
        )
        self.assertTrue((retained / "pinned-update.dmg").is_file())
        shutil.rmtree(retained)

    def test_dmg_reader_detaches_after_payload_validation_failure(self):
        dmg = self.root / "FocusBrowser-macOS-1.0.6-universal.dmg"
        dmg.write_bytes(b"dmg fixture")
        mounted = {"value": False}
        detached = {"value": False}

        def run(command, label, timeout=prepare_sparkle_appcast.TOOL_TIMEOUT_SECONDS):
            del label, timeout
            if command[1] == "attach":
                mounted["value"] = True
            elif command[1] == "detach":
                detached["value"] = True
                mounted["value"] = False
            return b"", b""

        with mock.patch.object(
            prepare_sparkle_appcast, "_run_command", side_effect=run
        ), mock.patch("os.path.ismount", side_effect=lambda _path: mounted["value"]), mock.patch(
            "os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
        ):
            with self.assertRaises(prepare_sparkle_appcast.AppcastError):
                prepare_sparkle_appcast._read_info_from_dmg(
                    dmg
                )
        self.assertTrue(detached["value"])
        self.assertFalse(mounted["value"])

    def test_committed_publication_retains_private_candidate_root(self):
        retained = []

        def committed(candidate, _output, identity, _size, _digest):
            retained.append(Path(candidate).parent)
            raise prepare_sparkle_appcast.package_local_dmg.CommittedPublishError(
                "synthetic committed cleanup failure", identity
            )

        with mock.patch.object(
            prepare_sparkle_appcast.package_local_dmg,
            "durable_publish_candidate",
            side_effect=committed,
        ):
            status, stdout, error = self.run_main(self.common_args())
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertIn("private state retained", error)
        self.assertEqual(1, len(retained))
        self.assertTrue((retained[0] / "appcast-macos.xml").is_file())
        shutil.rmtree(retained[0])

    def test_post_commit_appcast_verification_failure_is_reported_as_committed(self):
        real_read = prepare_sparkle_appcast._read_appcast_file
        tool_metadata = self.tool.lstat()
        contract = prepare_sparkle_appcast.ReleaseContract(
            sparkle_tool=self.tool,
            sparkle_source_root=self.sparkle_root,
            sparkle_tool_identity=prepare_sparkle_appcast._identity(tool_metadata),
            sparkle_tool_sha256=hashlib.sha256(self.tool.read_bytes()).hexdigest(),
            sparkle_tool_private=False,
            keychain_account="FocusBrowserUpdates",
            payload=self.payload,
            expected_size=self.payload.stat().st_size,
            expected_sha256=hashlib.sha256(self.payload.read_bytes()).hexdigest(),
            asset_url=(
                "https://github.com/DanilBend/FocusBrowser/releases/download/"
                "v1.0.6-macos/{}".format(self.payload.name)
            ),
            release_url=(
                "https://github.com/DanilBend/FocusBrowser/releases/tag/"
                "v1.0.6-macos"
            ),
            version=self.version,
            short_version=self.short_version,
            published_at=prepare_sparkle_appcast._parse_published_at(
                "2026-08-01T00:00:00Z"
            ),
            public_key=PUBLIC_KEY_BYTES,
        )

        def reject_committed_output(path):
            if Path(path) == self.output and self.output.exists():
                raise prepare_sparkle_appcast.AppcastError(
                    "synthetic final appcast verification failure"
                )
            return real_read(path)

        with mock.patch.object(
            prepare_sparkle_appcast,
            "_read_appcast_file",
            side_effect=reject_committed_output,
        ), mock.patch.object(
            prepare_sparkle_appcast, "_verify_ed25519_file", return_value=True
        ), mock.patch.object(
            prepare_sparkle_appcast, "_verify_ed25519_bytes", return_value=True
        ), self.assertRaisesRegex(
            prepare_sparkle_appcast.CommittedAppcastPublishError,
            "publication committed but post-commit verification failed",
        ) as raised:
            prepare_sparkle_appcast.generate_appcast(contract, self.output)
        self.assertTrue(self.output.is_file())
        observed = self.output.lstat()
        self.assertEqual(
            (observed.st_dev, observed.st_ino),
            raised.exception.final_identity[:2],
        )
        self.assertIsNone(raised.exception.retained_private_root)
        self.assertIn(
            prepare_sparkle_appcast.SIGNED_FEED_BLOCK_RE.pattern.split(b"\\n")[0],
            self.output.read_bytes(),
        )

    def test_missing_or_duplicate_info_plist_is_rejected(self):
        with zipfile.ZipFile(self.payload, "w") as archive:
            archive.writestr(
                self.regular_zip_info("Focus Browser.app/README"), b"no plist"
            )
        self.payload.chmod(0o600)
        status, _, error = self.run_main(self.common_args())
        self.assertEqual(1, status)
        self.assertIn("exactly one", error)

        encoded = plistlib.dumps(self.valid_info())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.payload, "w") as archive:
                info = self.regular_zip_info(
                    "Focus Browser.app/Contents/Info.plist"
                )
                archive.writestr(info, encoded)
                archive.writestr(info, encoded)
        self.payload.chmod(0o600)
        status, _, error = self.run_main(self.common_args())
        self.assertEqual(1, status)
        self.assertIn("duplicate", error)

    def test_malformed_archive_signature_output_is_rejected(self):
        status, _, error = self.run_main(
            self.common_args(account="badformat")
        )
        self.assertEqual(1, status)
        self.assertIn("invalid archive signature", error)
        self.assertFalse(self.output.exists())

    def test_unsigned_feed_from_tool_is_rejected_and_candidate_removed(self):
        status, _, error = self.run_main(self.common_args(account="badfeed"))
        self.assertEqual(1, status)
        self.assertIn("signed-feed block", error)
        self.assertFalse(self.output.exists())
        self.assertEqual([], list(self.root.glob(".appcast-macos-*.xml")))

    def test_tool_failure_does_not_echo_tool_output(self):
        status, stdout, error = self.run_main(self.common_args(account="fail"))
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertNotIn("DO-NOT-LEAK", error)
        self.assertIn("exit code 7", error)

    def test_private_key_options_are_rejected_without_echoing_values(self):
        for option in ("--private-key", "--ed-key-file", "-s", "-f"):
            secret = "SUPER-SECRET-PRIVATE-MATERIAL"
            status, stdout, error = self.run_main(
                ["generate", option, secret]
            )
            self.assertEqual(1, status)
            self.assertEqual("", stdout)
            self.assertIn("not accepted", error)
            self.assertNotIn(secret, error)

    def test_unknown_arguments_are_rejected_without_echoing_values(self):
        secret = "SHOULD-NOT-BE-ECHOED"
        status, _, error = self.run_main(["generate", "--unknown", secret])
        self.assertEqual(1, status)
        self.assertEqual("error: invalid command line\n", error)
        self.assertNotIn(secret, error)

    def test_output_is_never_overwritten(self):
        original = b"existing appcast"
        self.output.write_bytes(original)
        status, _, error = self.run_main(self.common_args())
        self.assertEqual(1, status)
        self.assertIn("overwrite", error)
        self.assertEqual(original, self.output.read_bytes())
        self.assertEqual([], self.recorded_calls())

    def test_output_name_and_absolute_paths_are_enforced(self):
        wrong_name = self.replace_arg(
            self.common_args(), "--output", str(self.root / "other.xml")
        )
        status, _, error = self.run_main(wrong_name)
        self.assertEqual(1, status)
        self.assertIn("appcast-macos.xml", error)
        relative = self.replace_arg(
            self.common_args(), "--payload", self.payload.name
        )
        status, _, error = self.run_main(relative)
        self.assertEqual(1, status)
        self.assertIn("absolute", error)

    def test_tool_must_be_exact_executable_nonwritable_sign_update(self):
        self.tool.chmod(0o777)
        status, _, error = self.run_main(self.common_args())
        self.assertEqual(1, status)
        self.assertIn("group/world writable", error)
        self.tool.chmod(0o755)
        other = self.root / "renamed-tool"
        self.tool.rename(other)
        arguments = self.replace_arg(
            self.common_args(), "--sparkle-tool", str(other)
        )
        status, _, error = self.run_main(arguments)
        self.assertEqual(1, status)
        self.assertIn("named exactly sign_update", error)

        outside = self.root / "outside/sign_update"
        outside.parent.mkdir()
        shutil.copyfile(other, outside)
        outside.chmod(0o755)
        arguments = self.replace_arg(
            self.common_args(), "--sparkle-tool", str(outside)
        )
        status, _, error = self.run_main(arguments)
        self.assertEqual(1, status)
        self.assertIn("validated dependency root bin/sign_update", error)

    def test_dependency_report_cannot_bless_a_nonpinned_tool_hash(self):
        parsed = prepare_sparkle_appcast.build_parser().parse_args(
            self.common_args()
        )
        digest = hashlib.sha256(self.tool.read_bytes()).hexdigest()
        report = {
            "root": str(self.sparkle_root),
            "payload": {
                "binary_sha256": {"bin/sign_update": digest},
                "binary_modes": {"bin/sign_update": "0755"},
            },
        }
        with mock.patch.object(
            prepare_sparkle_appcast.acquire_sparkle,
            "validate_dependency_root",
            return_value=report,
        ), self.assertRaisesRegex(
            prepare_sparkle_appcast.AppcastError,
            "sign_update SHA-256 mismatch",
        ):
            prepare_sparkle_appcast._build_contract(parsed)

    def test_tampered_or_unsigned_existing_feed_is_rejected(self):
        self.generate()
        value = self.output.read_bytes()
        value = value.replace(
            b"Focus Browser 1.0.6", b"Focus Browser 9.0.6", 1
        )
        self.output.write_bytes(value)
        self.output.chmod(0o600)
        status, _, error = self.run_main(self.common_args("validate"))
        self.assertEqual(1, status)
        self.assertIn("title", error)

        marker = b"<!-- sparkle-signatures:\n"
        self.output.write_bytes(value[: value.index(marker)])
        status, _, error = self.run_main(self.common_args("validate"))
        self.assertEqual(1, status)
        self.assertIn("signed-feed block", error)

    def test_public_key_and_signatures_require_canonical_base64(self):
        arguments = self.replace_arg(
            self.common_args(), "--public-key", PUBLIC_KEY.rstrip("=")
        )
        status, _, error = self.run_main(arguments)
        self.assertEqual(1, status)
        self.assertIn("canonical Base64", error)
        with self.assertRaises(prepare_sparkle_appcast.AppcastError):
            prepare_sparkle_appcast._parse_archive_signature(
                b'sparkle:edSignature="AAAA" length="42"\n', 42
            )

    def test_real_ed25519_verifier_accepts_rfc8032_vector_and_rejects_tamper(self):
        signature = bytes.fromhex(
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        )
        self.assertTrue(
            prepare_sparkle_appcast._verify_ed25519_bytes(
                PUBLIC_KEY_BYTES, signature, b""
            )
        )
        self.assertFalse(
            prepare_sparkle_appcast._verify_ed25519_bytes(
                PUBLIC_KEY_BYTES, signature, b"tampered"
            )
        )
        changed = bytearray(signature)
        changed[0] ^= 1
        self.assertFalse(
            prepare_sparkle_appcast._verify_ed25519_bytes(
                PUBLIC_KEY_BYTES, bytes(changed), b""
            )
        )

    def test_archive_signature_must_match_public_key_before_tool_verify(self):
        status, _, error = self.run_main(self.common_args(), verify=False)
        self.assertEqual(1, status)
        self.assertIn("does not match SUPublicEDKey", error)
        calls = self.recorded_calls()
        self.assertEqual(1, len(calls))
        self.assertNotIn("--verify", calls[0])

    def test_appcast_metadata_is_canonical_and_exact(self):
        self.generate()
        value = self.output.read_bytes()
        content, _ = prepare_sparkle_appcast._split_signed_feed(value)
        self.assertIn(
            b"<sparkle:minimumSystemVersion>12.0.0</sparkle:minimumSystemVersion>",
            content,
        )
        self.assertIn(b'sparkle:os="macos"', content)
        self.assertIn(b"Prerelease automatic updates", content)
        self.assertIn(b"/v1.0.6-macos/", content)
        self.assertIn(b"Fri, 31 Jul 2026 12:34:56 GMT", content)
        self.assertNotIn(b"latest/download", content)
        self.assertEqual(
            content,
            prepare_sparkle_appcast._render_unsigned_appcast(
                prepare_sparkle_appcast._build_contract(
                    prepare_sparkle_appcast.build_parser().parse_args(
                        self.common_args("validate-public")
                    ),
                    require_signing=False,
                ),
                FAKE_SIGNATURE,
            ),
        )


class ChecksumGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.payload = self.root / prepare_sparkle_appcast.MACOS_RELEASE_DMG_NAME
        self.appcast = self.root / prepare_sparkle_appcast.MACOS_APPCAST_NAME
        self.output = self.root / prepare_sparkle_appcast.MACOS_CHECKSUMS_NAME
        self.payload.write_bytes(b"exact universal DMG bytes")
        self.appcast.write_bytes(b"exact signed appcast bytes\n")
        self.payload.chmod(0o600)
        self.appcast.chmod(0o600)

    def tearDown(self):
        self.temporary.cleanup()

    def arguments(self, output=None):
        return [
            "generate-checksums",
            "--payload",
            str(self.payload),
            "--appcast",
            str(self.appcast),
            "--output",
            str(self.output if output is None else output),
        ]

    @staticmethod
    def run_main(arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = prepare_sparkle_appcast.main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_generate_checksums_writes_exact_canonical_inventory_without_signing(self):
        with mock.patch.object(
            prepare_sparkle_appcast,
            "_build_contract",
            side_effect=AssertionError("signing contract must not be used"),
        ), mock.patch.object(
            prepare_sparkle_appcast,
            "_run_command",
            side_effect=AssertionError("external command must not be used"),
        ):
            status, stdout, stderr = self.run_main(self.arguments())
        self.assertEqual(0, status, stderr)
        self.assertEqual("", stderr)
        payload_sha = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        appcast_sha = hashlib.sha256(self.appcast.read_bytes()).hexdigest()
        expected = (
            "{}  {}\n{}  {}\n".format(
                payload_sha,
                prepare_sparkle_appcast.MACOS_RELEASE_DMG_NAME,
                appcast_sha,
                prepare_sparkle_appcast.MACOS_APPCAST_NAME,
            )
        ).encode("ascii")
        self.assertEqual(expected, self.output.read_bytes())
        self.assertEqual(0o600, stat.S_IMODE(self.output.stat().st_mode))
        self.assertEqual(1, self.output.stat().st_nlink)
        report = json.loads(stdout)
        self.assertEqual("generate-checksums", report["mode"])
        self.assertEqual(str(self.output), report["checksums"])
        self.assertEqual(
            hashlib.sha256(expected).hexdigest(), report["checksums_sha256"]
        )
        self.assertEqual(
            [
                prepare_sparkle_appcast.MACOS_RELEASE_DMG_NAME,
                prepare_sparkle_appcast.MACOS_APPCAST_NAME,
            ],
            [entry["name"] for entry in report["entries"]],
        )
        self.assertIs(report["network"], False)
        self.assertIs(report["signing"], False)

    def test_generate_checksums_refuses_overwrite_and_preserves_existing_bytes(self):
        original = b"do not replace this file"
        self.output.write_bytes(original)
        status, stdout, stderr = self.run_main(self.arguments())
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertIn("overwrite", stderr)
        self.assertEqual(original, self.output.read_bytes())

    def test_generate_checksums_requires_absolute_exact_names_and_no_symlinks(self):
        wrong_output = self.root / "checksums.txt"
        status, _, stderr = self.run_main(self.arguments(output=wrong_output))
        self.assertEqual(1, status)
        self.assertIn(prepare_sparkle_appcast.MACOS_CHECKSUMS_NAME, stderr)

        relative = self.arguments()
        relative[relative.index("--appcast") + 1] = self.appcast.name
        status, _, stderr = self.run_main(relative)
        self.assertEqual(1, status)
        self.assertIn("absolute", stderr)

        wrong_payload = self.root / "FocusBrowser-macOS-wrong.dmg"
        wrong_payload.write_bytes(b"wrongly named payload")
        wrong_payload.chmod(0o600)
        wrong_input = self.arguments()
        wrong_input[wrong_input.index("--payload") + 1] = str(wrong_payload)
        status, _, stderr = self.run_main(wrong_input)
        self.assertEqual(1, status)
        self.assertIn(prepare_sparkle_appcast.MACOS_RELEASE_DMG_NAME, stderr)

        real_payload = self.root / "payload-real.dmg"
        self.payload.rename(real_payload)
        self.payload.symlink_to(real_payload)
        status, _, stderr = self.run_main(self.arguments())
        self.assertEqual(1, status)
        self.assertIn("non-symlink", stderr)
        self.assertFalse(self.output.exists())

    def test_generate_checksums_rebinds_inputs_before_publication(self):
        original_create = prepare_sparkle_appcast._create_checksums_candidate

        def create_then_mutate(output, value):
            result = original_create(output, value)
            self.payload.write_bytes(b"changed after initial checksum")
            self.payload.chmod(0o600)
            return result

        with mock.patch.object(
            prepare_sparkle_appcast,
            "_create_checksums_candidate",
            side_effect=create_then_mutate,
        ):
            status, stdout, stderr = self.run_main(self.arguments())
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertIn("changed before checksum publication", stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(
            [], list(self.root.glob(".focus-macos-checksums-private-*"))
        )

    def test_post_commit_checksum_verification_failure_is_reported_as_committed(self):
        with mock.patch.object(
            prepare_sparkle_appcast,
            "_read_checksum_output",
            side_effect=prepare_sparkle_appcast.AppcastError(
                "synthetic final checksum verification failure"
            ),
        ), self.assertRaisesRegex(
            prepare_sparkle_appcast.CommittedChecksumPublishError,
            "output committed but post-commit verification failed",
        ) as raised:
            prepare_sparkle_appcast.generate_checksums(
                self.payload, self.appcast, self.output
            )
        self.assertTrue(self.output.is_file())
        observed = self.output.lstat()
        self.assertEqual(
            (observed.st_dev, observed.st_ino),
            raised.exception.final_identity[:2],
        )
        self.assertIsNone(raised.exception.retained_private_root)
        expected = (
            "{}  {}\n{}  {}\n".format(
                hashlib.sha256(self.payload.read_bytes()).hexdigest(),
                prepare_sparkle_appcast.MACOS_RELEASE_DMG_NAME,
                hashlib.sha256(self.appcast.read_bytes()).hexdigest(),
                prepare_sparkle_appcast.MACOS_APPCAST_NAME,
            )
        ).encode("ascii")
        self.assertEqual(expected, self.output.read_bytes())


if __name__ == "__main__":
    unittest.main()
