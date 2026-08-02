"""Tests for the Focus Browser macOS GitHub Pages landing page."""

import hashlib
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
MODULE_PATH = SCRIPT_DIR / "stage_macos_page.py"
SPEC = importlib.util.spec_from_file_location("stage_macos_page", MODULE_PATH)
stage_macos_page = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage_macos_page)
PAGE_SOURCE = REPOSITORY_ROOT / ".github/pages/macos"


class StageMacosPageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.copy_index = 0

    def tearDown(self):
        self.temporary.cleanup()

    def copy_source(self):
        self.copy_index += 1
        destination = self.root / "source-{}".format(self.copy_index)
        shutil.copytree(PAGE_SOURCE, destination)
        return destination

    def test_repository_page_contract_and_canonical_logo(self):
        _, values = stage_macos_page.validate_site(PAGE_SOURCE)
        self.assertEqual(set(stage_macos_page.EXPECTED_FILES), set(values))
        self.assertEqual(
            stage_macos_page.CANONICAL_PAGE_LOGO_SHA256,
            hashlib.sha256(values["focus-browser.png"]).hexdigest(),
        )
        canonical_icns = REPOSITORY_ROOT / "platform/macos/resources/FocusBrowser.icns"
        self.assertEqual(
            stage_macos_page.CANONICAL_ICNS_SHA256,
            hashlib.sha256(canonical_icns.read_bytes()).hexdigest(),
        )

    def test_stages_exact_page_tree_without_overwrite(self):
        destination = self.root / "site"
        destination.mkdir()
        report = stage_macos_page.stage(PAGE_SOURCE, destination)
        self.assertEqual("v1.0.6-macos", report["release_tag"])
        for relative in stage_macos_page.EXPECTED_FILES:
            self.assertEqual(
                (PAGE_SOURCE / relative).read_bytes(),
                (destination / "macos" / relative).read_bytes(),
            )
        with self.assertRaises(stage_macos_page.PageError):
            stage_macos_page.stage(PAGE_SOURCE, destination)

    def test_rejects_changed_release_link_and_non_graphite_color(self):
        source = self.copy_source()
        index = source / "index.html"
        index.write_text(
            index.read_text(encoding="utf-8").replace(
                "v1.0.6-macos", "v1.0.6", 1
            ),
            encoding="utf-8",
        )
        with self.assertRaises(stage_macos_page.PageError):
            stage_macos_page.validate_site(source)

        source = self.copy_source()
        index = source / "index.html"
        index.write_text(
            index.read_text(encoding="utf-8").replace(
                'content="#2d2f30"', 'content="#0000ff"', 1
            ),
            encoding="utf-8",
        )
        with self.assertRaises(stage_macos_page.PageError):
            stage_macos_page.validate_site(source)

    def test_rejects_removal_of_mobile_overflow_guard(self):
        source = self.copy_source()
        stylesheet = source / "styles.css"
        stylesheet.write_text(
            stylesheet.read_text(encoding="utf-8").replace(
                "overflow-wrap: anywhere", "overflow-wrap: normal"
            ),
            encoding="utf-8",
        )
        with self.assertRaises(stage_macos_page.PageError):
            stage_macos_page.validate_site(source)

    def test_rejects_non_hex_named_function_and_system_colors(self):
        additions = (
            ".bad { color: rgb(0, 0, 255); }",
            ".bad { color: hsl(240 100% 50%); }",
            ".bad { color: red; }",
            ".bad { color: LinkText; }",
            ".bad { accent-color: AccentColor; }",
            ".bad { accent-color: auto; }",
            ".bad { color: -apple-system-blue; }",
            ".bad { color: -moz-hyperlinktext; }",
            ".bad { outline: 1px auto -webkit-focus-ring-color; }",
            ".bad { color: -webkit-link; }",
            ".bad { color: device-cmyk(1 1 0 0); }",
        )
        for addition in additions:
            with self.subTest(addition=addition):
                source = self.copy_source()
                stylesheet = source / "styles.css"
                stylesheet.write_text(
                    stylesheet.read_text(encoding="utf-8")
                    + "\n"
                    + addition
                    + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(stage_macos_page.PageError):
                    stage_macos_page.validate_site(source)

    def test_rejects_missing_or_wrong_favicon(self):
        source = self.copy_source()
        index = source / "index.html"
        index.write_text(
            index.read_text(encoding="utf-8").replace(
                '  <link rel="icon" type="image/png" href="./focus-browser.png">\n',
                "",
            ),
            encoding="utf-8",
        )
        with self.assertRaises(stage_macos_page.PageError):
            stage_macos_page.validate_site(source)

        source = self.copy_source()
        english = source / "en/index.html"
        english.write_text(
            english.read_text(encoding="utf-8").replace(
                'href="../focus-browser.png"', 'href="./focus-browser.png"', 1
            ),
            encoding="utf-8",
        )
        with self.assertRaises(stage_macos_page.PageError):
            stage_macos_page.validate_site(source)

        source = self.copy_source()
        stylesheet = source / "styles.css"
        stylesheet.write_text(
            stylesheet.read_text(encoding="utf-8") + "\n.bad { color: blue; }\n",
            encoding="utf-8",
        )
        with self.assertRaises(stage_macos_page.PageError):
            stage_macos_page.validate_site(source)

    def test_rejects_unexpected_assets_and_symlinks(self):
        source = self.copy_source()
        (source / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        with self.assertRaises(stage_macos_page.PageError):
            stage_macos_page.validate_site(source)

        source = self.copy_source()
        (source / "alias.css").symlink_to(source / "styles.css")
        with self.assertRaises(stage_macos_page.PageError):
            stage_macos_page.validate_site(source)

    @unittest.skipIf(os.name == "nt", "POSIX descriptor publication contract")
    def test_rejects_source_root_rebind_after_descriptor_open(self):
        source = self.copy_source()
        moved = self.root / "opened-source"
        real_open = stage_macos_page.os.open
        replaced = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal replaced
            descriptor = real_open(path, flags, *args, **kwargs)
            if not replaced and path == str(source):
                replaced = True
                source.rename(moved)
                source.mkdir()
            return descriptor

        with mock.patch.object(
            stage_macos_page.os, "open", side_effect=racing_open
        ), self.assertRaises(stage_macos_page.PageError):
            stage_macos_page.validate_site(source)

    @unittest.skipIf(os.name == "nt", "POSIX descriptor publication contract")
    def test_rejects_destination_root_rebind_and_retains_replacement(self):
        destination = self.root / "site"
        destination.mkdir()
        moved = self.root / "opened-site"
        real_open = stage_macos_page.os.open
        replaced = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if (
                not replaced
                and path == stage_macos_page.PAGE_DIRECTORY
                and kwargs.get("dir_fd") is not None
            ):
                replaced = True
                destination.rename(moved)
                destination.mkdir()
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(
            stage_macos_page.os, "open", side_effect=racing_open
        ), self.assertRaisesRegex(
            stage_macos_page.PageError, "destination changed"
        ):
            stage_macos_page.stage(PAGE_SOURCE, destination)
        self.assertEqual([], list(destination.iterdir()))

    def test_only_macos_workflow_stages_the_landing_page(self):
        mac_text = (
            REPOSITORY_ROOT / ".github/workflows/publish-macos-appcast.yml"
        ).read_text(encoding="utf-8")
        windows_text = (
            REPOSITORY_ROOT / ".github/workflows/publish-appcast.yml"
        ).read_text(encoding="utf-8")

        self.assertEqual(1, mac_text.count("stage_macos_page.py"))
        self.assertIn("--source-dir .github/pages/macos", mac_text)
        self.assertIn("--destination-dir", mac_text)
        self.assertIn("include-hidden-files: true", mac_text)
        self.assertIn("--proto-redir '=https'", mac_text)
        self.assertIn('--max-filesize "$expected_size"', mac_text)

        self.assertNotIn("stage_macos_page.py", windows_text)
        self.assertNotIn("--feed-name appcast-macos.xml", windows_text)
        self.assertIn("-Method Head", windows_text)
        self.assertIn("coordinated cross-platform Pages workflow", windows_text)


if __name__ == "__main__":
    unittest.main()
