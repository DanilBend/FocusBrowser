#!/usr/bin/env python3
"""Lightweight validation for Focus Browser's WinSparkle configuration."""

import importlib.util
import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = Path(
    os.environ.get("FOCUS_ACTIVE_SOURCE_ROOT", ROOT / "build" / "src")
).resolve()


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "focus_windows_build", ROOT / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = _load_build_module()


class WinSparkleConfigTest(unittest.TestCase):
    PUBLIC_KEY = "PUAXw+hDiVqStwqnTRt+vJyYLM8uxJaMwM1V8Sr0Zgw="

    def test_accepts_canonical_ed25519_public_key(self):
        self.assertTrue(BUILD._is_valid_winsparkle_public_key(self.PUBLIC_KEY))

    def test_rejects_malformed_or_wrong_sized_public_keys(self):
        for value in ("", "not-base64", "AA==", self.PUBLIC_KEY.rstrip("="),
                      f" {self.PUBLIC_KEY}"):
            with self.subTest(value=value):
                self.assertFalse(BUILD._is_valid_winsparkle_public_key(value))

    def test_accepts_safe_https_appcast_urls(self):
        for value in (
                "https://updates.example.org/appcast.xml",
                "https://github.com/example/project/releases/download/feed/appcast.xml?channel=stable"):
            with self.subTest(value=value):
                self.assertTrue(BUILD._is_valid_winsparkle_appcast_url(value))

    def test_rejects_unsafe_appcast_urls(self):
        for value in (
                "", "http://updates.example.org/appcast.xml",
                "https://user:password@updates.example.org/appcast.xml",
                "https://updates.example.org/appcast.xml#fragment",
                "https://updates.example.org\\appcast.xml",
                'https://updates.example.org/"appcast.xml',
                "https://updates.example.org:99999/appcast.xml"):
            with self.subTest(value=value):
                self.assertFalse(BUILD._is_valid_winsparkle_appcast_url(value))

    def test_updater_gn_flags_require_key_and_url_together(self):
        self.assertEqual(BUILD._winsparkle_gn_flags({}), "")
        with self.assertRaises(ValueError):
            BUILD._winsparkle_gn_flags({"WINSPARKLE_ED_KEY": self.PUBLIC_KEY})
        with self.assertRaises(ValueError):
            BUILD._winsparkle_gn_flags({
                "WINSPARKLE_APPCAST_URL":
                    "https://updates.example.org/appcast.xml",
            })

        flags = BUILD._winsparkle_gn_flags({
            "WINSPARKLE_ED_KEY": self.PUBLIC_KEY,
            "WINSPARKLE_APPCAST_URL":
                "https://updates.example.org/appcast.xml",
        })
        self.assertIn("enable_winsparkle=true", flags)
        self.assertIn(f'winsparkle_ed_key="{self.PUBLIC_KEY}"', flags)
        self.assertIn(
            'winsparkle_appcast_url="https://updates.example.org/appcast.xml"',
            flags)

    def test_runtime_is_detached_from_private_services(self):
        helper = (ROOT / "source_overrides/components/focus_services/"
                  "focus_services_helpers.cc").read_text(encoding="utf-8")
        body = re.search(
            r"bool ShouldAccessUpdateService\([^)]*\) \{(.*?)\n\}",
            helper, re.DOTALL)
        self.assertIsNotNone(body)
        self.assertIn("kFocusUpdateFetchingEnabled", body.group(1))
        self.assertNotIn("ShouldAccessServices", body.group(1))
        self.assertNotIn("custom-update-server-url", helper)

    def test_compile_time_key_and_url_are_both_required(self):
        gni = (ACTIVE / "chrome/updater/winsparkle.gni").read_text(
            encoding="utf-8")
        self.assertIn('winsparkle_ed_key = ""', gni)
        self.assertIn('winsparkle_appcast_url = ""', gni)
        self.assertRegex(
            gni,
            r'(?s)!enable_winsparkle.*winsparkle_ed_key != "".*'
            r'winsparkle_appcast_url != ""',
        )

        glue = (ACTIVE / "chrome/browser/win/"
                "winsparkle_glue.cc").read_text(encoding="utf-8")
        self.assertIn("WINSPARKLE_APPCAST_URL", glue)
        self.assertIn("UpdaterRuntimeConfigured", glue)
        self.assertNotIn("GetBrowserUpdateURL", glue)

        core_patch = (ROOT / "focus-chromium/patches/focus/core/"
                      "add-updater-preference.patch").read_text(
                          encoding="utf-8")
        self.assertNotIn("updates.focus.computer", core_patch)
        self.assertNotIn("custom-update-server-url", core_patch)

    def test_pref_default_and_ui_follow_updater_buildflag(self):
        prefs = (ROOT / "source_overrides/chrome/browser/ui/"
                 "browser_ui_prefs.cc").read_text(encoding="utf-8")
        self.assertRegex(
            prefs,
            r"kFocusUpdateFetchingEnabled,\s*"
            r"BUILDFLAG\(ENABLE_UPDATE_NOTIFICATIONS\)",
        )

        provider = (ROOT / "source_overrides/chrome/browser/ui/webui/"
                    "settings/settings_localized_strings_provider.cc").read_text(
                        encoding="utf-8")
        self.assertIn('AddBoolean("focusUpdaterAvailable"', provider)
        self.assertIn("BUILDFLAG(ENABLE_UPDATE_NOTIFICATIONS)", provider)

        privacy_page = (ROOT / "source_overrides/chrome/browser/resources/"
                        "settings/privacy_page/privacy_page.html").read_text(
                            encoding="utf-8")
        self.assertIn("focusBrowserUpdatesToggle", privacy_page)
        self.assertIn("prefs.focus.services.browser_updates", privacy_page)

    def test_elevated_helper_always_requires_appcast_signature(self):
        verifier = (ACTIVE / "chrome/installer/focus_update_helper/"
                    "payload_verifier.cc").read_text(encoding="utf-8")
        self.assertIn("if (!VerifyEdDSA(bytes, signature_b64))", verifier)
        self.assertIn("return false;", verifier)
        self.assertLess(
            verifier.index("if (!VerifyEdDSA(bytes, signature_b64))"),
            verifier.index("VerifyAuthenticode(file, path.value())"),
        )
        self.assertNotRegex(
            verifier,
            r"if \(VerifyEdDSA\(bytes, signature_b64\)\)\s*\{\s*return true;",
        )

    def test_source_overlay_matches_active_runtime_files(self):
        relative_files = (
            "components/focus_services/focus_services_helpers.cc",
            "components/focus_services/focus_services_helpers.h",
            "chrome/browser/ui/browser_ui_prefs.cc",
            "chrome/browser/resources/settings/privacy_page/privacy_page.ts",
            "chrome/browser/resources/settings/privacy_page/privacy_page.html",
            "chrome/browser/ui/webui/settings/settings_localized_strings_provider.cc",
            "chrome/app/settings_strings.grdp",
        )
        for relative in relative_files:
            with self.subTest(relative=relative):
                self.assertEqual(
                    (ROOT / "source_overrides" / relative).read_bytes(),
                    (ACTIVE / relative).read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
