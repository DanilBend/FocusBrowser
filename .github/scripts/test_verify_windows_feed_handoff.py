"""Tests for immutable Windows-feed preservation by the macOS workflow."""

import base64
import datetime
import email.utils
import hashlib
import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_windows_feed_handoff.py")
SPEC = importlib.util.spec_from_file_location(
    "verify_windows_feed_handoff", MODULE_PATH
)
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)

SIGNATURE_BYTES = bytes(range(64))
SIGNATURE = base64.b64encode(SIGNATURE_BYTES).decode("ascii")
PUBLIC_KEY_BYTES = bytes(range(32))
PUBLIC_KEY = base64.b64encode(PUBLIC_KEY_BYTES).decode("ascii")


class VerifyWindowsFeedHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        payload = bytearray(handoff.MIN_PAYLOAD_BYTES)
        payload[:2] = b"MZ"
        struct.pack_into("<I", payload, 0x3C, 0x80)
        payload[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", payload, 0x84, 0x8664)
        self.payload = self.write(handoff.PAYLOAD_NAME, bytes(payload))
        self.appcast = self.write(handoff.APPCAST_NAME, self.windows_feed())
        self.pages = self.write("current-pages-appcast-x64.xml", self.windows_feed())
        self.full_hash = "11" * 32
        self.portable_hash = "22" * 32
        self.checksums = self.write(handoff.CHECKSUMS_NAME, self.checksum_bytes())
        self.release = self.release_document()
        self.release_json = self.write_json("release.json", self.release)
        self.latest_json = self.write_json("latest.json", self.release)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def pub_date():
        value = datetime.datetime(
            2026, 7, 31, 12, 34, 56, tzinfo=datetime.timezone.utc
        )
        return email.utils.format_datetime(value, usegmt=True)

    def windows_feed(self):
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<rss version="2.0" xmlns:sparkle="{}">\n'
            "  <channel>\n"
            "    <title>Focus Browser updates (x64)</title>\n"
            "    <link>{}</link>\n"
            "    <description>Stable updates for Focus Browser x64</description>\n"
            "    <language>ru</language>\n"
            "    <item>\n"
            "      <title>Focus Browser 1.0.5</title>\n"
            "      <pubDate>{}</pubDate>\n"
            "      <link>{}</link>\n"
            "      <enclosure url=\"https://github.com/DanilBend/FocusBrowser/releases/download/v1.0.5/{}\" sparkle:version=\"1.0.5.0\" sparkle:shortVersionString=\"1.0.5\" sparkle:os=\"windows-x64\" sparkle:edSignature=\"{}\" length=\"{}\" type=\"application/octet-stream\" />\n"
            "    </item>\n"
            "  </channel>\n"
            "</rss>\n"
        ).format(
            handoff.stage_public_appcast.SPARKLE_NAMESPACE,
            handoff.RELEASE_URL,
            self.pub_date(),
            handoff.RELEASE_URL,
            handoff.PAYLOAD_NAME,
            SIGNATURE,
            handoff.MIN_PAYLOAD_BYTES,
        ).encode("utf-8")

    def checksum_bytes(self):
        values = {
            handoff.FULL_INSTALLER_NAME: self.full_hash,
            handoff.PAYLOAD_NAME: hashlib.sha256(self.payload.read_bytes()).hexdigest(),
            handoff.PORTABLE_NAME: self.portable_hash,
            handoff.APPCAST_NAME: hashlib.sha256(self.appcast.read_bytes()).hexdigest(),
        }
        return "".join(
            "{}  {}\n".format(values[name], name)
            for name in handoff.CHECKSUMMED_ASSETS
        ).encode("utf-8")

    def release_document(self):
        payload_digest = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        appcast_digest = hashlib.sha256(self.appcast.read_bytes()).hexdigest()
        checksums_digest = hashlib.sha256(self.checksums.read_bytes()).hexdigest()
        metadata = {
            handoff.FULL_INSTALLER_NAME: (1234567, self.full_hash),
            handoff.PAYLOAD_NAME: (self.payload.stat().st_size, payload_digest),
            handoff.PORTABLE_NAME: (2345678, self.portable_hash),
            handoff.CHECKSUMS_NAME: (self.checksums.stat().st_size, checksums_digest),
            handoff.APPCAST_NAME: (self.appcast.stat().st_size, appcast_digest),
        }
        return {
            "id": 105,
            "tag_name": handoff.RELEASE_TAG,
            "html_url": handoff.RELEASE_URL,
            "draft": False,
            "prerelease": False,
            "immutable": True,
            "published_at": "2026-07-31T12:34:56Z",
            "assets": [
                {
                    "name": name,
                    "state": "uploaded",
                    "size": metadata[name][0],
                    "digest": "sha256:" + metadata[name][1],
                    "browser_download_url": (
                        "https://github.com/DanilBend/FocusBrowser/releases/download/"
                        + handoff.RELEASE_TAG
                        + "/"
                        + name
                    ),
                }
                for name in handoff.EXPECTED_ASSETS
            ],
        }

    def write(self, name, value):
        path = self.root / name
        path.write_bytes(value)
        return path

    def write_json(self, name, value):
        return self.write(
            name,
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )

    def validate(self, verifier=None):
        return handoff.validate(
            self.release_json,
            self.latest_json,
            self.payload,
            self.appcast,
            self.checksums,
            self.pages,
            PUBLIC_KEY,
            signature_verifier=verifier or (lambda *_: True),
        )

    def rewrite_release_views(self):
        self.release_json.write_bytes(
            json.dumps(self.release, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        self.latest_json.write_bytes(self.release_json.read_bytes())

    def test_accepts_exact_immutable_handoff_and_verifies_signature(self):
        calls = []

        def verifier(public_key, signature, descriptor):
            calls.append((public_key, signature, descriptor))
            return True

        report = self.validate(verifier)
        self.assertEqual(handoff.RELEASE_TAG, report["release_tag"])
        self.assertEqual(handoff.VERSION, report["version"])
        self.assertEqual(
            hashlib.sha256(self.appcast.read_bytes()).hexdigest(),
            report["appcast_sha256"],
        )
        self.assertEqual(1, len(calls))
        self.assertEqual(PUBLIC_KEY_BYTES, calls[0][0])
        self.assertEqual(SIGNATURE_BYTES, calls[0][1])
        self.assertIsInstance(calls[0][2], int)

    def test_rejects_pages_bytes_not_from_immutable_release(self):
        self.pages.write_bytes(self.pages.read_bytes() + b"\n")
        with self.assertRaisesRegex(handoff.HandoffError, "Pages appcast differs"):
            self.validate()

    def test_rejects_invalid_winsparkle_signature(self):
        with self.assertRaisesRegex(handoff.HandoffError, "signature verification"):
            self.validate(lambda *_: False)

    def test_rejects_checksum_not_bound_to_release_metadata(self):
        lines = self.checksums.read_text(encoding="utf-8").splitlines()
        lines[0] = "33" * 32 + "  " + handoff.FULL_INSTALLER_NAME
        self.checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
        checksum_asset = next(
            item
            for item in self.release["assets"]
            if item["name"] == handoff.CHECKSUMS_NAME
        )
        checksum_asset["size"] = self.checksums.stat().st_size
        checksum_asset["digest"] = "sha256:" + hashlib.sha256(
            self.checksums.read_bytes()
        ).hexdigest()
        self.rewrite_release_views()
        with self.assertRaisesRegex(handoff.HandoffError, "do not match"):
            self.validate()

    def test_rejects_nonimmutable_or_nonlatest_release(self):
        self.release["immutable"] = False
        self.rewrite_release_views()
        with self.assertRaisesRegex(handoff.HandoffError, "not immutable"):
            self.validate()

        self.release["immutable"] = True
        self.rewrite_release_views()
        latest = json.loads(self.latest_json.read_text(encoding="utf-8"))
        latest["id"] = 106
        self.latest_json.write_text(json.dumps(latest), encoding="utf-8")
        with self.assertRaisesRegex(handoff.HandoffError, "metadata differ"):
            self.validate()

    def test_rejects_payload_tamper_and_wrong_architecture(self):
        payload = bytearray(self.payload.read_bytes())
        payload[-1] = 1
        self.payload.write_bytes(payload)
        with self.assertRaisesRegex(handoff.HandoffError, "release metadata"):
            self.validate()

        self.payload.write_bytes(bytes(payload[:-1]) + b"\0")
        payload = bytearray(self.payload.read_bytes())
        struct.pack_into("<H", payload, 0x84, 0x014C)
        self.payload.write_bytes(payload)
        digest = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        payload_asset = next(
            item
            for item in self.release["assets"]
            if item["name"] == handoff.PAYLOAD_NAME
        )
        payload_asset["digest"] = "sha256:" + digest
        lines = self.checksums.read_text(encoding="utf-8").splitlines()
        lines[1] = digest + "  " + handoff.PAYLOAD_NAME
        self.checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
        checksum_asset = next(
            item
            for item in self.release["assets"]
            if item["name"] == handoff.CHECKSUMS_NAME
        )
        checksum_asset["size"] = self.checksums.stat().st_size
        checksum_asset["digest"] = "sha256:" + hashlib.sha256(
            self.checksums.read_bytes()
        ).hexdigest()
        self.rewrite_release_views()
        with self.assertRaisesRegex(handoff.HandoffError, "not x64"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
