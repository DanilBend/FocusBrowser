#!/usr/bin/env python3
"""Static checks for Focus Browser 1.0.5 release metadata.

This intentionally does not build, sign, install, or launch the browser.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = Path(
    os.environ.get("FOCUS_ACTIVE_SOURCE_ROOT", ROOT / "build" / "src")
).resolve()
FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    check(path.is_file(), f"missing file: {relative_path}")
    return path.read_text(encoding="utf-8") if path.is_file() else ""


focus_numeric = (
    "@FOCUS_MAJOR@,@FOCUS_MINOR@,@FOCUS_PATCH@,@FOCUS_PLATFORM@"
)
focus_dotted = (
    "@FOCUS_MAJOR@.@FOCUS_MINOR@.@FOCUS_PATCH@.@FOCUS_PLATFORM@"
)
rc_templates = (
    "chrome/app/chrome_version.rc.version",
    "chrome/installer/mini_installer/mini_installer_exe_version.rc.version",
    "chrome/installer/setup/setup_exe_version.rc.version",
)

for relative_path in rc_templates:
    # build/src is a prepared local Chromium tree and is intentionally absent
    # from a clean repository checkout. The patch mirror below is mandatory in
    # both environments; inspect active templates as an additional local gate.
    if not (ACTIVE / relative_path).is_file():
        continue
    text = (ACTIVE / relative_path).read_text(encoding="utf-8")
    check(
        text.count(f" FILEVERSION {focus_numeric}") == 1,
        f"{relative_path}: FILEVERSION is not the four-part Focus version",
    )
    check(
        text.count(f" PRODUCTVERSION {focus_numeric}") == 1,
        f"{relative_path}: PRODUCTVERSION is not the four-part Focus version",
    )
    check(
        text.count(f'VALUE "FileVersion", "{focus_dotted}"') == 1,
        f"{relative_path}: FileVersion string is not the four-part Focus version",
    )
    check(
        text.count(f'VALUE "ProductVersion", "{focus_dotted}"') == 1,
        f"{relative_path}: ProductVersion string is not the four-part Focus version",
    )
    check(
        "@MAJOR@,@MINOR@,@BUILD@,@PATCH@" not in text
        and "@MAJOR@.@MINOR@.@BUILD@.@PATCH@" not in text,
        f"{relative_path}: a Chromium version token remains in PE metadata",
    )

release_source_parts = (
    int(read("focus-chromium/version.txt").splitlines()[0].split(".")[0]),
    int(read("focus-chromium/chromium_version.txt").splitlines()[0].split(".")[0]) - 150,
    int(read("focus-chromium/revision.txt").splitlines()[0].split(".")[0]),
    int(read("revision.txt").splitlines()[0].split(".")[0]),
)
check(release_source_parts == (1, 0, 5, 0), "release inputs must resolve to Focus 1.0.5.0")

active_version_path = ACTIVE / "chrome/VERSION"
if active_version_path.is_file():
    version_values = {}
    for line in active_version_path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            version_values[key] = value
    check(
        tuple(version_values.get(key) for key in (
            "FOCUS_MAJOR", "FOCUS_MINOR", "FOCUS_PATCH", "FOCUS_PLATFORM"
        )) == ("1", "0", "5", "0"),
        "prepared chrome/VERSION must define Focus 1.0.5.0",
    )

version_patch = read("patches/focus/windows/focus-versioning.patch")
for relative_path in (
    "chrome/app/chrome_version.rc.version",
    "chrome/installer/mini_installer/mini_installer_exe_version.rc.version",
    "chrome/installer/setup/setup_exe_version.rc.version",
):
    check(
        f"--- a/{relative_path}" in version_patch
        and f"+++ b/{relative_path}" in version_patch,
        f"focus-versioning.patch does not cover {relative_path}",
    )
check(
    version_patch.count(f"+ FILEVERSION {focus_numeric}") == 3,
    "focus-versioning.patch must replace all three numeric FILEVERSION fields",
)
check(
    version_patch.count(f"+ PRODUCTVERSION {focus_numeric}") == 3,
    "focus-versioning.patch must replace all three numeric PRODUCTVERSION fields",
)
check(
    version_patch.count(f'+            VALUE "FileVersion", "{focus_dotted}"') == 3,
    "focus-versioning.patch must replace all three FileVersion strings",
)
check(
    version_patch.count(f'+            VALUE "ProductVersion", "{focus_dotted}"') == 3,
    "focus-versioning.patch must replace all three ProductVersion strings",
)

password_action_patch = read(
    "focus-chromium/patches/focus/ui/restore-password-action.patch"
)
for required_text in (
    "kActionShowPasswordsBubbleOrPage",
    "PasswordsModelDelegateFromWebContents(web_contents)",
    "ManagePasswordsUIController::FromWebContents(web_contents)",
    "if (passwords_action_item)",
    "PasswordBubbleViewBase::~PasswordBubbleViewBase()",
    "Missing optional UI state is not fatal",
):
    check(
        required_text in password_action_patch,
        f"password action crash fix is missing: {required_text}",
    )

verifier = read("build_support/verify_focus_release.ps1")
for required_text in (
    "[string]$ExpectedVersion = '1.0.5.0'",
    "$versionInfo.FileVersion -eq $ExpectedVersion",
    "$versionInfo.ProductVersion -eq $ExpectedVersion",
    "(Join-Path $focusOutDir 'chrome.dll') 'chrome.dll' $true",
    "(Join-Path $focusOutDir 'setup.exe') 'setup.exe' $true",
    "'mini_installer.exe' $true",
    "$script:InstallerPath 'NSIS installer' $true",
    "qa\\verify_locale_branding.py",
    "Compiled locale packs contain no unintended upstream product branding",
):
    check(required_text in verifier, f"release verifier is missing: {required_text}")

stage_index = read(".github/actions/stage/index.js")
for required_text in (
    "require('./release-version')",
    "parseFocusReleaseVersion(stdout)",
    "core.setOutput('version', releaseVersion.fullVersion)",
    "core.setOutput('display_version', releaseVersion.displayVersion)",
    "core.setOutput('release_tag', releaseVersion.releaseTag)",
):
    check(required_text in stage_index, f"stage action is missing: {required_text}")

stage_action = read(".github/actions/stage/action.yml")
for output_name in ("version", "display_version", "release_tag"):
    check(
        re.search(rf"(?m)^  {re.escape(output_name)}:\s*$", stage_action) is not None,
        f"stage action does not declare the {output_name} output",
    )

release_version = read(".github/actions/stage/release-version.js")
check(
    r"/^\d+\.\d+\.\d+\.\d+$/" in release_version,
    "release version parser must require four numeric components",
)
check(
    "releaseTag: `v${tagParts.join('.')}`" in release_version,
    "release version parser must produce a v-prefixed tag",
)

signing_condition = (
    "if: ${{ env.AZURE_SIGNING_ENABLED == 'true' && "
    "env.AZURE_SIGNING_CONFIGURED == 'true' }}"
)
required_secrets = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_SIGNING_ENDPOINT",
    "AZURE_SIGNING_ACCOUNT",
    "AZURE_SIGNING_CERTIFICATE_NAME",
)

workflow = read(".github/workflows/main.yml")
workflow_template = read(".github/workflows/main.yml.j2")
for label, text, signing_steps, final_jobs in (
    ("generated workflow", workflow, 6, 2),
    ("workflow template", workflow_template, 3, 1),
):
    check(
        text.count("uses: azure/artifact-signing-action@v1") == signing_steps,
        f"{label}: unexpected Azure signing step count",
    )
    check(
        text.count(signing_condition) == signing_steps,
        f"{label}: every Azure signing step must have the opt-in condition",
    )
    check(
        text.count("AZURE_SIGNING_ENABLED: ${{ vars.ENABLE_AZURE_SIGNING }}")
        == final_jobs,
        f"{label}: signing enable flag is not scoped to every final job",
    )
    for secret in required_secrets:
        check(secret in text, f"{label}: signing prerequisite {secret} is missing")
    check(
        "prerelease: false" in text and "prerelease: true" not in text,
        f"{label}: Focus 1.0.5 must be a stable release",
    )
    check(
        "name: Focus Browser ${{ needs.build-final.outputs.display_version }}" in text,
        f"{label}: release name is not derived as Focus Browser 1.0.5",
    )
    check(
        "tag_name: ${{ needs.build-final.outputs.release_tag }}" in text,
        f"{label}: release tag is not derived as v1.0.5",
    )
    legacy_brand = "".join(("he", "li", "um"))
    check(
        legacy_brand not in text.lower(),
        f"{label}: legacy product branding remains",
    )

workflow_lines = workflow.splitlines()
for index, line in enumerate(workflow_lines):
    if line.strip() != "uses: azure/artifact-signing-action@v1":
        continue
    previous_nonempty = next(
        (candidate.strip() for candidate in reversed(workflow_lines[:index]) if candidate.strip()),
        "",
    )
    check(
        previous_nonempty == signing_condition,
        f"generated workflow line {index + 1}: Azure signing is unconditional",
    )
check(
    workflow.count("- name: Build Focus installer and package") == 2
    and workflow.count("- name: Upload artifacts") == 2,
    "unsigned packaging/upload steps must remain present for both architectures",
)

# These embedded component versions are independent from the browser's release
# version. Pinning them here catches accidental rewrites during release work.
for relative_path, expected_name, expected_version, prepared_only in (
    ("third_party/focus_youtube/manifest.json", "FocusYoutube", "1.6.9.1", True),
    ("source_overrides/third_party/focus_youtube/manifest.json", "FocusYoutube", "1.6.9.1", False),
    ("third_party/ublock/manifest.json", "FocusBlock", "1.72.2.2", True),
    ("source_overrides/third_party/ublock/manifest.json", "FocusBlock", "1.72.2.2", False),
):
    manifest_path = (ACTIVE if prepared_only else ROOT) / relative_path
    if prepared_only and not manifest_path.is_file():
        continue
    manifest = json.loads(manifest_path.read_text(encoding="utf-8") or "{}")
    check(manifest.get("name") == expected_name, f"{relative_path}: component name changed")
    check(
        manifest.get("version") == expected_version,
        f"{relative_path}: component version must remain {expected_version}",
    )

winsparkle_defaults = (
    "enable_winsparkle = false",
    'winsparkle_ed_key = ""',
    'winsparkle_appcast_url = ""',
)
updater_patch = read("patches/focus/windows/updater/build-wiring.patch")
for safe_default in winsparkle_defaults:
    check(
        f"+  {safe_default}" in updater_patch,
        f"updater patch has an unsafe default: expected {safe_default}",
    )
active_winsparkle_path = ACTIVE / "chrome/updater/winsparkle.gni"
if active_winsparkle_path.is_file():
    active_winsparkle = active_winsparkle_path.read_text(encoding="utf-8")
    for safe_default in winsparkle_defaults:
        check(
            safe_default in active_winsparkle,
            f"prepared updater has an unsafe default: expected {safe_default}",
        )

publish_appcast = read(".github/workflows/publish-appcast.yml")
publish_appcast_lines = {line.strip() for line in publish_appcast.splitlines()}
for release_default in (
    "default: v1.0.5",
    "default: 1.0.5.0",
    "default: 1.0.5",
    "default: FocusBrowser_1.0.5_x64-mini-installer.exe",
):
    check(
        release_default in publish_appcast_lines,
        f"production appcast workflow default is stale: expected {release_default}",
    )

for required_text in (
    "FULL_INSTALLER_NAME: FocusBrowser_${{ inputs.short_version }}_x64-installer.exe",
    "PORTABLE_ZIP_NAME: FocusBrowser_${{ inputs.short_version }}_x64-windows.zip",
    "CHECKSUMS_NAME: SHA256SUMS-${{ inputs.short_version }}.txt",
    'throw "The release must contain exactly five production x64 assets"',
    'throw "The release tag contains too many nested annotated tags"',
    'throw "The release tag must resolve to a commit on current main history"',
    "[string]$comparison.merge_base_commit.sha -cne $tagObjectSha",
    "Assert-PeMachine `\n            $downloaded[$env:FULL_INSTALLER_NAME] 'Full installer' 0x014C",
    "Assert-PeMachine `\n            $downloaded[$env:ASSET_NAME] 'Mini-installer' 0x8664",
    'throw "The checksum file must contain exactly four non-empty lines"',
    'throw "SHA256SUMS mismatch for $checksummedName"',
    "'Focus Browser updates (x64)'",
    "'Stable updates for Focus Browser x64'",
    'throw "Appcast pubDate must be canonical RFC1123 UTC"',
    "$now.AddMinutes(5)",
    "$now.AddDays(-7)",
    "WinSparkle signature verification failed",
    "Refusing to roll the production appcast back",
):
    check(
        required_text in publish_appcast,
        f"production appcast workflow gate is missing: {required_text}",
    )

release_docs = read("docs/RELEASING.md")
for required_text in (
    "`SHA256SUMS-1.0.5.txt` с точными SHA-256 остальных четырёх x64-файлов",
    "тег `v1.0.5` должен\n   разрешаться ровно в этот commit",
    "Загрузите в draft ровно пять x64 assets",
    "`appcast-arm64.xml` добавляется только в\n  тот выпуск, где реально собраны",
    "Для 1.0.5 дополнительных Release assets нет",
):
    check(
        required_text in release_docs,
        f"release documentation gate is missing: {required_text}",
    )

onboarding_deps = read("focus-chromium/deps.ini")
for required_text in (
    "version = 202607132006-focus1",
    "https://github.com/DanilBend/FocusBrowser/releases/download/"
    "build-deps-onboarding-%(version)s/onboarding-page-%(version)s.tar.gz",
    "download_filename = onboarding-page-%(version)s.tar.gz",
    "sha256 = ddb5f5e375412dc987581103d8c64a59144097a084ab3c49166a95afeea230d7",
    "output_path = ./components/focus_onboarding",
):
    check(
        required_text in onboarding_deps,
        f"onboarding build dependency is not pinned correctly: {required_text}",
    )

if FAILURES:
    print("FAIL: Focus release configuration")
    for failure in FAILURES:
        print(f"  - {failure}")
    sys.exit(1)

print("PASS: Focus Browser 1.0.5 release configuration is internally consistent")
