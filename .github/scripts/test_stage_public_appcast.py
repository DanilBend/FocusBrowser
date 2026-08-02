"""Tests for the shared GitHub Pages appcast staging gate."""

import base64
import datetime
import email.utils
import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("stage_public_appcast.py")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("stage_public_appcast", MODULE_PATH)
stage_public_appcast = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage_public_appcast)


SIGNATURE = base64.b64encode(bytes(range(64))).decode("ascii")
NAMESPACE = stage_public_appcast.SPARKLE_NAMESPACE


class StagePublicAppcastTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / "site"
        self.output.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def pub_date():
        value = datetime.datetime(
            2026, 7, 31, 12, 34, 56, tzinfo=datetime.timezone.utc
        )
        return email.utils.format_datetime(value, usegmt=True)

    def windows_feed(self, version="1.0.5"):
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<rss version="2.0" xmlns:sparkle="{}">\n'
            "  <channel>\n"
            "    <title>Focus Browser updates (x64)</title>\n"
            "    <link>https://github.com/DanilBend/FocusBrowser/releases/tag/v{}</link>\n"
            "    <description>Stable updates for Focus Browser x64</description>\n"
            "    <language>ru</language>\n"
            "    <item>\n"
            "      <title>Focus Browser {}</title>\n"
            "      <pubDate>{}</pubDate>\n"
            "      <link>https://github.com/DanilBend/FocusBrowser/releases/tag/v{}</link>\n"
            "      <enclosure url=\"https://github.com/DanilBend/FocusBrowser/releases/download/v{}/FocusBrowser_{}_x64-mini-installer.exe\" sparkle:version=\"{}.0\" sparkle:shortVersionString=\"{}\" sparkle:os=\"windows-x64\" sparkle:edSignature=\"{}\" length=\"1234567\" type=\"application/octet-stream\" />\n"
            "    </item>\n"
            "  </channel>\n"
            "</rss>\n"
        ).format(
            NAMESPACE,
            version,
            version,
            self.pub_date(),
            version,
            version,
            version,
            version,
            version,
            SIGNATURE,
        ).encode("utf-8")

    def mac_feed(self, version="1.0.6"):
        release_tag = version + "-macos"
        content = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<rss version="2.0" xmlns:sparkle="{}">\n'
            "  <channel>\n"
            "    <title>Focus Browser updates (macOS universal)</title>\n"
            "    <link>https://github.com/DanilBend/FocusBrowser/releases/tag/v{}</link>\n"
            "    <description>Prerelease automatic updates for Focus Browser on macOS</description>\n"
            "    <language>en</language>\n"
            "    <item>\n"
            "      <title>Focus Browser {}</title>\n"
            "      <pubDate>{}</pubDate>\n"
            "      <link>https://github.com/DanilBend/FocusBrowser/releases/tag/v{}</link>\n"
            "      <sparkle:version>{}.0</sparkle:version>\n"
            "      <sparkle:shortVersionString>{}</sparkle:shortVersionString>\n"
            "      <sparkle:minimumSystemVersion>12.0.0</sparkle:minimumSystemVersion>\n"
            "      <enclosure url=\"https://github.com/DanilBend/FocusBrowser/releases/download/v{}/FocusBrowser-macOS-{}-universal-autoupdate.dmg\" sparkle:os=\"macos\" sparkle:edSignature=\"{}\" length=\"7654321\" type=\"application/octet-stream\" />\n"
            "    </item>\n"
            "  </channel>\n"
            "</rss>\n"
        ).format(
            NAMESPACE,
            release_tag,
            version,
            self.pub_date(),
            release_tag,
            version,
            version,
            release_tag,
            version,
            SIGNATURE,
        ).encode("utf-8")
        block = (
            "<!-- sparkle-signatures:\n"
            "edSignature: {}\n"
            "length: {}\n"
            "-->\n"
        ).format(SIGNATURE, len(content)).encode("ascii")
        return content + block

    def write(self, name, value):
        path = self.root / name
        path.write_bytes(value)
        return path

    def test_stages_both_feed_families_without_overwriting(self):
        windows = self.write("current-windows.xml", self.windows_feed())
        mac = self.write("candidate-mac.xml", self.mac_feed())
        windows_report = stage_public_appcast.stage(
            windows, "appcast-x64.xml", self.output
        )
        mac_report = stage_public_appcast.stage(
            mac, "appcast-macos.xml", self.output
        )
        self.assertEqual(
            self.windows_feed(), (self.output / "appcast-x64.xml").read_bytes()
        )
        self.assertEqual(
            self.mac_feed(), (self.output / "appcast-macos.xml").read_bytes()
        )
        self.assertEqual("1.0.5.0", windows_report["version"])
        self.assertEqual("1.0.6.0", mac_report["version"])

    def test_rejects_rollback_and_same_version_byte_change(self):
        candidate = self.write("candidate.xml", self.mac_feed("1.0.5"))
        previous = self.write("previous.xml", self.mac_feed("1.0.6"))
        with self.assertRaises(stage_public_appcast.FeedError):
            stage_public_appcast.stage(
                candidate,
                "appcast-macos.xml",
                self.output,
                previous=previous,
            )
        candidate.write_bytes(self.mac_feed("1.0.6").replace(b"7654321", b"7654322"))
        with self.assertRaises(stage_public_appcast.FeedError):
            stage_public_appcast.stage(
                candidate,
                "appcast-macos.xml",
                self.output,
                previous=previous,
            )

    def test_allows_identical_idempotent_version(self):
        value = self.mac_feed("1.0.6")
        candidate = self.write("candidate.xml", value)
        previous = self.write("previous.xml", value)
        report = stage_public_appcast.stage(
            candidate,
            "appcast-macos.xml",
            self.output,
            previous=previous,
        )
        self.assertTrue(report["previous_checked"])
        self.assertEqual(hashlib.sha256(value).hexdigest(), report["sha256"])

    def test_rejects_path_tricks_unsafe_xml_and_wrong_asset_identity(self):
        source = self.write("feed.xml", self.windows_feed())
        symlink = self.root / "symlink.xml"
        symlink.symlink_to(source)
        with self.assertRaises(stage_public_appcast.FeedError):
            stage_public_appcast.validate_feed(symlink, "appcast-x64.xml")

        source.write_bytes(
            self.windows_feed().replace(
                b"FocusBrowser_1.0.5_x64-mini-installer.exe",
                b"FocusBrowser_1.0.5_x64-installer.exe",
            )
        )
        with self.assertRaises(stage_public_appcast.FeedError):
            stage_public_appcast.validate_feed(source, "appcast-x64.xml")

        windows_with_mac_tag = self.write(
            "windows-mac-tag.xml",
            self.windows_feed().replace(b"v1.0.5/", b"v1.0.5-macos/"),
        )
        with self.assertRaises(stage_public_appcast.FeedError):
            stage_public_appcast.validate_feed(
                windows_with_mac_tag, "appcast-x64.xml"
            )

        mac_without_suffix = self.write(
            "mac-global-tag.xml",
            self.mac_feed().replace(b"v1.0.6-macos", b"v1.0.6"),
        )
        with self.assertRaises(stage_public_appcast.FeedError):
            stage_public_appcast.validate_feed(
                mac_without_suffix, "appcast-macos.xml"
            )

        source.write_bytes(
            b'<?xml version="1.0" encoding="utf-8"?>\n'
            b"<!DOCTYPE rss [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>\n"
            b'<rss version="2.0">&xxe;</rss>\n'
        )
        with self.assertRaises(stage_public_appcast.FeedError):
            stage_public_appcast.validate_feed(source, "appcast-x64.xml")

    def test_rejects_malformed_signed_feed_and_existing_destination(self):
        malformed = self.write(
            "mac.xml", self.mac_feed().replace(b"length: ", b"length: 9")
        )
        with self.assertRaises(stage_public_appcast.FeedError):
            stage_public_appcast.validate_feed(malformed, "appcast-macos.xml")

        source = self.write("windows.xml", self.windows_feed())
        (self.output / "appcast-x64.xml").write_text("occupied", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            stage_public_appcast.stage(
                source, "appcast-x64.xml", self.output
            )

    def test_rejects_source_path_rebind_after_descriptor_open(self):
        source = self.write("source.xml", self.windows_feed())
        moved = self.root / "source-opened.xml"
        real_open = stage_public_appcast.os.open
        replaced = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal replaced
            descriptor = real_open(path, flags, *args, **kwargs)
            if not replaced and path == str(source):
                replaced = True
                source.rename(moved)
                source.write_bytes(self.windows_feed("1.0.4"))
            return descriptor

        with mock.patch.object(
            stage_public_appcast.os, "open", side_effect=racing_open
        ), self.assertRaisesRegex(stage_public_appcast.FeedError, "changed"):
            stage_public_appcast.validate_feed(source, "appcast-x64.xml")

    @unittest.skipIf(os.name == "nt", "POSIX openat publication contract")
    def test_rejects_destination_directory_rebind_and_does_not_write_replacement(self):
        source = self.write("source.xml", self.windows_feed())
        moved = self.root / "original-site"
        real_open = stage_public_appcast.os.open
        replaced = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if (
                not replaced
                and path == "appcast-x64.xml"
                and kwargs.get("dir_fd") is not None
            ):
                replaced = True
                self.output.rename(moved)
                self.output.mkdir()
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(
            stage_public_appcast.os, "open", side_effect=racing_open
        ), self.assertRaisesRegex(
            stage_public_appcast.FeedError, "destination directory changed"
        ):
            stage_public_appcast.stage(
                source, "appcast-x64.xml", self.output
            )
        self.assertEqual([], list(self.output.iterdir()))

    def test_workflows_share_pages_lock_and_fail_closed_at_platform_handoff(self):
        mac_workflow = (
            REPOSITORY_ROOT / ".github/workflows/publish-macos-appcast.yml"
        ).read_text(encoding="utf-8")
        windows_workflow = (
            REPOSITORY_ROOT / ".github/workflows/publish-appcast.yml"
        ).read_text(encoding="utf-8")
        for workflow in (mac_workflow, windows_workflow):
            self.assertIn("group: focus-update-appcasts-pages", workflow)
            self.assertIn("Reject dispatches outside main", workflow)
            self.assertIn("if: github.ref != 'refs/heads/main'", workflow)
            self.assertNotIn("if: github.ref == 'refs/heads/main'", workflow)
            self.assertIn("stage_public_appcast.py", workflow)
            self.assertIn("appcast-x64.xml", workflow)
            self.assertIn("appcast-macos.xml", workflow)
        self.assertIn("prepare_sparkle_appcast.py validate-public", mac_workflow)
        self.assertIn("runs-on: macos-15", mac_workflow)
        self.assertIn(".immutable", mac_workflow)
        self.assertIn("v1.0.6-macos", mac_workflow)
        self.assertIn(
            "$(jq -r '.prerelease' \"$release_json\")\" != true",
            mac_workflow,
        )
        self.assertIn("releases/latest", mac_workflow)
        self.assertIn("EXPECTED_WINDOWS_LATEST_TAG: v1.0.5", mac_workflow)
        self.assertIn("RESERVED_STABLE_TAG: v1.0.6", mac_workflow)
        self.assertIn(
            "git/ref/tags/$RESERVED_STABLE_TAG", mac_workflow
        )
        self.assertIn(
            "releases/tags/$RESERVED_STABLE_TAG", mac_workflow
        )
        self.assertIn("Authorization: Bearer $GH_TOKEN", mac_workflow)
        self.assertIn('[[ "$status" == 404 ]]', mac_workflow)
        self.assertIn(
            "WINSPARKLE_ED_KEY: ${{ vars.WINSPARKLE_ED_KEY }}",
            mac_workflow,
        )
        self.assertIn("verify_windows_feed_handoff.py", mac_workflow)
        self.assertIn(
            "releases/tags/$EXPECTED_WINDOWS_LATEST_TAG", mac_workflow
        )
        for asset_name in (
            "FocusBrowser_1.0.5_x64-installer.exe",
            "FocusBrowser_1.0.5_x64-mini-installer.exe",
            "FocusBrowser_1.0.5_x64-windows.zip",
            "SHA256SUMS-1.0.5.txt",
            "appcast-x64.xml",
        ):
            self.assertIn(asset_name, mac_workflow)
        self.assertIn("FOCUS_WINDOWS_CANONICAL_APPCAST", mac_workflow)
        self.assertIn("FOCUS_WINDOWS_PAGES_APPCAST", mac_workflow)
        self.assertIn(
            '--source "$FOCUS_WINDOWS_CANONICAL_APPCAST"', mac_workflow
        )
        self.assertIn(
            '--previous "$FOCUS_WINDOWS_PAGES_APPCAST"', mac_workflow
        )
        self.assertNotIn('--source "$previous_x64"', mac_workflow)
        self.assertIn("published immutable prerelease", mac_workflow)
        self.assertIn(".assets | length", mac_workflow)
        self.assertIn(
            "current Pages appcast differs from immutable v1.0.5 release bytes",
            (
                REPOSITORY_ROOT
                / ".github/scripts/verify_windows_feed_handoff.py"
            ).read_text(encoding="utf-8"),
        )
        self.assertIn("EXPECTED_MACOS_RELEASE_TAG: v1.0.6-macos", mac_workflow)
        self.assertIn("EXPECTED_MACOS_VERSION: 1.0.6.0", mac_workflow)
        self.assertIn("EXPECTED_MACOS_SHORT_VERSION: 1.0.6", mac_workflow)
        self.assertIn(
            "EXPECTED_MACOS_PAYLOAD_NAME: "
            "FocusBrowser-macOS-1.0.6-universal-autoupdate.dmg",
            mac_workflow,
        )
        for comparison in (
            '[[ "$RELEASE_TAG" != "$EXPECTED_MACOS_RELEASE_TAG" ]]',
            '[[ "$FOCUS_VERSION" != "$EXPECTED_MACOS_VERSION" ]]',
            '[[ "$FOCUS_SHORT_VERSION" != '
            '"$EXPECTED_MACOS_SHORT_VERSION" ]]',
            '[[ "$PAYLOAD_NAME" != "$EXPECTED_MACOS_PAYLOAD_NAME" ]]',
        ):
            self.assertIn(comparison, mac_workflow)
        self.assertIn("--public-key", mac_workflow)
        self.assertNotIn("--keychain-account", mac_workflow)
        self.assertNotIn("--sparkle-tool", mac_workflow)
        self.assertIn("--proto-redir '=https'", mac_workflow)
        self.assertIn('--max-filesize "$expected_size"', mac_workflow)

        self.assertIn("-Method Head", windows_workflow)
        self.assertIn(
            "coordinated cross-platform Pages workflow", windows_workflow
        )
        self.assertNotIn("--feed-name appcast-macos.xml", windows_workflow)
        self.assertNotIn("stage_macos_page.py", windows_workflow)

    def test_lint_runs_all_release_critical_helper_suites(self):
        lint_workflow = (
            REPOSITORY_ROOT / ".github/workflows/lint.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("timeout-minutes: 20", lint_workflow)
        self.assertIn("macos-release-tests:", lint_workflow)
        self.assertIn("runs-on: macos-15", lint_workflow)
        self.assertIn(
            "python -m unittest discover -s .github/scripts -p 'test_*.py'",
            lint_workflow,
        )
        self.assertIn(
            "python -m unittest discover -s platform/macos/tests "
            "-p 'test_*.py'",
            lint_workflow,
        )


if __name__ == "__main__":
    unittest.main()
