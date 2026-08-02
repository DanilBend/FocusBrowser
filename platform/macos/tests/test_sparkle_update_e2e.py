#!/usr/bin/env python3
"""Unit tests for the isolated full Sparkle update acceptance path."""

import copy
import sys
import unittest
from pathlib import Path
from unittest import mock


PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import acquire_sparkle
import sparkle_update_e2e


class SparkleUpdateE2ETests(unittest.TestCase):
    RELEASE_CHALLENGE = "e" * 64

    @staticmethod
    def valid_report():
        return {
            "schema": sparkle_update_e2e.SCHEMA,
            "passed": True,
            "test": "isolated-full-sparkle-update",
            "old_version": sparkle_update_e2e.OLD_VERSION,
            "next_version": sparkle_update_e2e.NEXT_VERSION,
            "version_namespace": "CFBundleVersion/sparkle:version",
            "sparkle_version": acquire_sparkle.SPARKLE_VERSION,
            "sparkle_framework_subtree_sha256": (
                acquire_sparkle.EXPECTED_FRAMEWORK_SUBTREE_SHA256
            ),
            "sparkle_dependency_receipt_sha256": "a" * 64,
            "updater_patch_sha256": "b" * 64,
            "harness_sha256": sparkle_update_e2e._sha256(
                Path(sparkle_update_e2e.__file__)
            ),
            "release_challenge": SparkleUpdateE2ETests.RELEASE_CHALLENGE,
            "architecture": "arm64",
            "feed_transport": "loopback-http-only",
            "feed_request_verified": True,
            "archive_download_verified": True,
            "eddsa_archive_verified_by_sparkle": True,
            "signed_feed_verified_by_sparkle": True,
            "bundle_replacement_verified": True,
            "relaunch_verified": True,
            "user_profile_isolated": True,
            "keychain_private_key_used": False,
            "production_private_key_used": False,
            "real_application_install_used": False,
            "public_network_used": False,
            "archive": {"bytes": 123, "sha256": "c" * 64},
            "appcast_sha256": "d" * 64,
            "event_sequence": [
                "launched:" + sparkle_update_e2e.OLD_VERSION,
                "updater-started",
                "feed-request-started",
                "feed-loaded",
                "valid-update:" + sparkle_update_e2e.NEXT_VERSION,
                "update-found:" + sparkle_update_e2e.NEXT_VERSION,
                "will-download",
                "download-started",
                "did-download",
                "will-extract",
                "extract-started",
                "did-extract",
                "ready-to-install",
                "will-install",
                "will-relaunch",
                "launched:" + sparkle_update_e2e.NEXT_VERSION,
                "relaunch-next-version",
            ],
            "http_requests": [
                {
                    "method": "GET",
                    "path": "/appcast.xml",
                    "peer": "127.0.0.1",
                    "status": 200,
                    "bytes": 100,
                },
                {
                    "method": "GET",
                    "path": "/" + sparkle_update_e2e.ARCHIVE_NAME,
                    "peer": "127.0.0.1",
                    "status": 200,
                    "bytes": 123,
                },
            ],
        }

    def test_report_accepts_only_complete_isolated_real_sparkle_evidence(self):
        report = self.valid_report()
        self.assertIs(
            report,
            sparkle_update_e2e.validate_report(
                report,
                expected_patch_sha256="b" * 64,
                expected_release_challenge=self.RELEASE_CHALLENGE,
            ),
        )

    def test_report_fails_closed_for_every_security_or_update_claim(self):
        for key in (
            "passed",
            "feed_request_verified",
            "archive_download_verified",
            "eddsa_archive_verified_by_sparkle",
            "signed_feed_verified_by_sparkle",
            "bundle_replacement_verified",
            "relaunch_verified",
            "user_profile_isolated",
        ):
            with self.subTest(key=key):
                candidate = copy.deepcopy(self.valid_report())
                candidate[key] = False
                with self.assertRaisesRegex(
                    sparkle_update_e2e.SparkleE2EError, "incomplete"
                ):
                    sparkle_update_e2e.validate_report(
                        candidate, expected_patch_sha256="b" * 64
                    )
        for key in (
            "keychain_private_key_used",
            "production_private_key_used",
            "real_application_install_used",
            "public_network_used",
        ):
            with self.subTest(key=key):
                candidate = copy.deepcopy(self.valid_report())
                candidate[key] = True
                with self.assertRaisesRegex(
                    sparkle_update_e2e.SparkleE2EError, "isolation"
                ):
                    sparkle_update_e2e.validate_report(
                        candidate, expected_patch_sha256="b" * 64
                    )

    def test_report_is_bound_to_exact_updater_patch_and_version_namespace(self):
        candidate = self.valid_report()
        candidate["updater_patch_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            sparkle_update_e2e.SparkleE2EError, "bind the updater patch"
        ):
            sparkle_update_e2e.validate_report(
                candidate, expected_patch_sha256="b" * 64
            )

        candidate = self.valid_report()
        candidate["release_challenge"] = "f" * 64
        with self.assertRaisesRegex(
            sparkle_update_e2e.SparkleE2EError, "bind this release run"
        ):
            sparkle_update_e2e.validate_report(
                candidate,
                expected_patch_sha256="b" * 64,
                expected_release_challenge=self.RELEASE_CHALLENGE,
            )

    def test_report_rejects_incomplete_events_requests_and_hashes(self):
        cases = []
        missing_event = self.valid_report()
        missing_event["event_sequence"].remove("did-extract")
        cases.append((missing_event, "event evidence is incomplete"))

        non_loopback = self.valid_report()
        non_loopback["http_requests"][0]["peer"] = "192.168.1.1"
        cases.append((non_loopback, "omitted request evidence"))

        wrong_archive_size = self.valid_report()
        wrong_archive_size["http_requests"][1]["bytes"] = 122
        cases.append((wrong_archive_size, "omitted request evidence"))

        bad_dependency_hash = self.valid_report()
        bad_dependency_hash["sparkle_dependency_receipt_sha256"] = "A" * 64
        cases.append((bad_dependency_hash, "dependency receipt hash is invalid"))

        for candidate, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                sparkle_update_e2e.SparkleE2EError, message
            ):
                sparkle_update_e2e.validate_report(
                    candidate,
                    expected_patch_sha256="b" * 64,
                    expected_release_challenge=self.RELEASE_CHALLENGE,
                )
        candidate = self.valid_report()
        candidate["version_namespace"] = "Chromium"
        with self.assertRaisesRegex(
            sparkle_update_e2e.SparkleE2EError, "identity contract"
        ):
            sparkle_update_e2e.validate_report(
                candidate, expected_patch_sha256="b" * 64
            )

    def test_commands_never_use_shell_or_inherit_login_environment(self):
        completed = mock.Mock(returncode=0, stdout=b"ok\n", stderr=b"")
        with mock.patch.object(
            sparkle_update_e2e.subprocess, "run", return_value=completed
        ) as run:
            sparkle_update_e2e._run(["/usr/bin/true"])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertTrue(run.call_args.kwargs["close_fds"])
        self.assertEqual(
            "/usr/bin:/bin:/usr/sbin:/sbin",
            run.call_args.kwargs["env"]["PATH"],
        )
        self.assertNotIn("HOME", run.call_args.kwargs["env"])

    def test_source_contract_has_no_production_key_or_public_endpoint(self):
        source = Path(sparkle_update_e2e.__file__).read_text(encoding="utf-8")
        self.assertIn('(\"127.0.0.1\", 0)', source)
        self.assertIn('"CFFIXED_USER_HOME": str(private_home)', source)
        self.assertIn('"CFBundleVersion": version', source)
        self.assertIn('"SURequireSignedFeed": True', source)
        self.assertIn('"SUVerifyUpdateBeforeExtraction": True', source)
        self.assertNotIn("danilbend.github.io", source)
        self.assertNotIn("NcOw/DDS", source)
        self.assertNotIn("generate_keys", source)
        self.assertNotIn("security find-generic-password", source)


if __name__ == "__main__":
    unittest.main()
