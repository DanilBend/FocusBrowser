#!/usr/bin/env python3
"""Fail-closed Sparkle acceptance for the universal Focus Browser app."""

import argparse
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
from pathlib import Path

import acquire_sparkle


APP_NAME = "Focus Browser.app"
APP_BUNDLE_ID = "com.focusbrowser.browser"
APP_EXECUTABLE = "Focus Browser"
APP_SHORT_VERSION = "1.0.6"
APP_VERSION = "1.0.6.0"
MINIMUM_MACOS_VERSION = "12.0"
FOCUS_FRAMEWORK_NAME = "Focus Browser Framework.framework"
SPARKLE_FRAMEWORK_RELATIVE_PATH = "Contents/Frameworks/Sparkle.framework"
ARCHITECTURES = frozenset(("arm64", "x86_64"))
LIPO = "/usr/bin/lipo"
CODESIGN = "/usr/bin/codesign"
TOOL_TIMEOUT_SECONDS = 30
OTOOL = "/usr/bin/otool"
VTOOL = "/usr/bin/vtool"

SPARKLE_VERSION = "2.9.4"
SPARKLE_BUILD_VERSION = "2059"
SPARKLE_FRAMEWORK_VERSION = "B"
SPARKLE_FEED_URL = (
    "https://danilbend.github.io/FocusBrowser/appcast-macos.xml"
)
SPARKLE_PUBLIC_ED_KEY = (
    "NcOw/DDSWLfV+kG111aN6fO8b0K4v3dygU7nYlLkkD0="
)
SPARKLE_APP_INFO_CONTRACT = {
    "SUFeedURL": SPARKLE_FEED_URL,
    "SUPublicEDKey": SPARKLE_PUBLIC_ED_KEY,
    "SURequireSignedFeed": True,
    "SUVerifyUpdateBeforeExtraction": True,
    "SUEnableAutomaticChecks": True,
    "SUAutomaticallyUpdate": True,
    "SUAllowsAutomaticUpdates": True,
    "SUScheduledCheckInterval": 86_400,
    "SUSignedFeedFailureExpirationInterval": 0,
    "SUEnableJavaScript": False,
    "SUEnableSystemProfiling": False,
}

CANONICAL_ICON_SHA256 = (
    "326ded57eec25c32ba405d3a9246fd80c1ab5cfc5a71735afc542df3594f4948"
)

FOCUS_HELPER_IDENTITIES = {
    "Focus Browser Helper.app": (
        "com.focusbrowser.browser.helper",
        "Focus Browser Helper",
    ),
    "Focus Browser Helper (Renderer).app": (
        "com.focusbrowser.browser.helper.renderer",
        "Focus Browser Helper (Renderer)",
    ),
    "Focus Browser Helper (GPU).app": (
        "com.focusbrowser.browser.helper",
        "Focus Browser Helper (GPU)",
    ),
    "Focus Browser Helper (Alerts).app": (
        "com.focusbrowser.browser.framework.AlertNotificationService",
        "Focus Browser Helper (Alerts)",
    ),
}
FOCUS_RUNTIME_PRODUCTS = {
    "focus-framework": "Focus Browser Framework",
    "app-mode-loader": "Helpers/app_mode_loader",
    "web-app-shortcut-copier": "Helpers/web_app_shortcut_copier",
    "crashpad": "Helpers/chrome_crashpad_handler",
    "libegl": "Libraries/libEGL.dylib",
    "libglesv2": "Libraries/libGLESv2.dylib",
}
SPARKLE_UPDATER_IDENTITY = (
    "org.sparkle-project.Sparkle.Updater",
    "Updater",
)

SPARKLE_PRODUCTS = {
    "framework": (
        "Sparkle",
        None,
    ),
    "autoupdate": (
        "Autoupdate",
        None,
    ),
    "updater": (
        "Updater.app/Contents/MacOS/Updater",
        (
            "Updater.app/Contents/Info.plist",
            "org.sparkle-project.Sparkle.Updater",
            "Updater",
        ),
    ),
    "downloader-xpc": (
        "XPCServices/Downloader.xpc/Contents/MacOS/Downloader",
        (
            "XPCServices/Downloader.xpc/Contents/Info.plist",
            "org.sparkle-project.DownloaderService",
            "Downloader",
        ),
    ),
    "installer-xpc": (
        "XPCServices/Installer.xpc/Contents/MacOS/Installer",
        (
            "XPCServices/Installer.xpc/Contents/Info.plist",
            "org.sparkle-project.InstallerLauncher",
            "Installer",
        ),
    ),
}

MACHO_MAGICS = frozenset(
    (
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    )
)
PROHIBITED_IDENTITY_TOKENS = (
    "chromiumupdater",
    "chromiumsoftwareupdateagent",
    "keystone",
    "googleupdater",
    "googlesoftwareupdate",
    "updaterprivilegedhelper",
    "ksadmin",
    "ksinstall",
)
PROHIBITED_PROVISIONING_SUFFIXES = (
    ".mobileprovision",
    ".provisionprofile",
)

DISABLE_LIBRARY_VALIDATION = (
    "com.apple.security.cs.disable-library-validation"
)
ALLOW_JIT = "com.apple.security.cs.allow-jit"
APP_ENTITLEMENTS = {
    "com.apple.security.device.audio-input": True,
    "com.apple.security.device.bluetooth": True,
    "com.apple.security.device.camera": True,
    "com.apple.security.device.print": True,
    "com.apple.security.device.usb": True,
    "com.apple.security.personal-information.location": True,
    "com.apple.security.personal-information.photos-library": True,
    DISABLE_LIBRARY_VALIDATION: True,
}
LOADER_ENTITLEMENTS = {DISABLE_LIBRARY_VALIDATION: True}
JIT_LOADER_ENTITLEMENTS = {
    ALLOW_JIT: True,
    DISABLE_LIBRARY_VALIDATION: True,
}
EXACT_ENTITLEMENTS = {
    "app": APP_ENTITLEMENTS,
    "helper-app": LOADER_ENTITLEMENTS,
    "helper-renderer-app": JIT_LOADER_ENTITLEMENTS,
    "helper-gpu-app": JIT_LOADER_ENTITLEMENTS,
    "helper-alerts": LOADER_ENTITLEMENTS,
    "app-mode-app": LOADER_ENTITLEMENTS,
    "web-app-shortcut-copier": LOADER_ENTITLEMENTS,
}
SPARKLE_DEPENDENCY = "@rpath/Sparkle.framework/Versions/B/Sparkle"
FOCUS_FRAMEWORK_RPATH = "@loader_path/../../.."
FRAMEWORK_LOADERS = (
    "app",
    "helper-app",
    "helper-renderer-app",
    "helper-gpu-app",
    "helper-alerts",
    "app-mode-app",
    "web-app-shortcut-copier",
)
LOADER_FLAGS = frozenset(("adhoc", "kill", "restrict", "runtime"))
FULL_RUNTIME_FLAGS = frozenset(
    ("adhoc", "kill", "restrict", "library-validation", "runtime")
)
DATA_ONLY_FLAGS = frozenset(("adhoc",))
_FLAGS_PATTERN = re.compile(
    r"^CodeDirectory\b[^\n]*\bflags=0x[0-9a-fA-F]+\(([^)]*)\)",
    flags=re.MULTILINE,
)
_VERSION_COMPONENT = r"(?:0|[1-9][0-9]*)"
_VERSION_PATTERN = re.compile(
    _VERSION_COMPONENT + r"(?:\." + _VERSION_COMPONENT + r"){1,2}\Z"
)


class AutoupdateContractError(RuntimeError):
    """Raised when an app does not match the pinned updater contract."""


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path, app):
    try:
        return Path(path).relative_to(app).as_posix()
    except ValueError as exc:
        raise AutoupdateContractError(
            "bundle product escapes the app: {}".format(path)
        ) from exc


def _require_directory(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise AutoupdateContractError(
            "{} must be a real directory: {}".format(label, path)
        )
    return path


def _require_file(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise AutoupdateContractError(
            "{} must be a regular non-symlink file: {}".format(label, path)
        )
    return path


def _read_plist(path, label):
    path = _require_file(path, label)
    try:
        with path.open("rb") as stream:
            value = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, TypeError, ValueError) as exc:
        raise AutoupdateContractError(
            "{} is not a valid property list: {}".format(label, path)
        ) from exc
    if not isinstance(value, dict):
        raise AutoupdateContractError(
            "{} property-list root must be a dictionary".format(label)
        )
    return value


def _normalized_identity(value):
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value.casefold() if character.isalnum())


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_strings(child)


def _reject_prohibited_plist_identity(path, info):
    prohibited_keys = sorted(
        key for key in info if isinstance(key, str) and key.startswith("KS")
    )
    if prohibited_keys:
        raise AutoupdateContractError(
            "prohibited Keystone Info.plist keys in {}: {}".format(
                path, ", ".join(prohibited_keys)
            )
        )
    for value in _iter_strings(info):
        normalized = _normalized_identity(value)
        token = next(
            (
                candidate
                for candidate in PROHIBITED_IDENTITY_TOKENS
                if candidate in normalized
            ),
            None,
        )
        if token is not None:
            raise AutoupdateContractError(
                "prohibited updater identity {!r} in {}".format(token, path)
            )


def _walk_bundle(app):
    nested_apps = []
    sparkle_frameworks = []
    plist_paths = []

    def fail(error):
        raise AutoupdateContractError(
            "cannot inspect app bundle: {}".format(error)
        ) from error

    for root, directories, files in os.walk(
        str(app), topdown=True, onerror=fail, followlinks=False
    ):
        directories.sort()
        files.sort()
        for name in directories + files:
            path = Path(root) / name
            if name.casefold().endswith(PROHIBITED_PROVISIONING_SUFFIXES):
                raise AutoupdateContractError(
                    "provisioning profile is prohibited anywhere in the app bundle: {}"
                    .format(_relative(path, app))
                )
            if name.casefold() == "assets.car":
                raise AutoupdateContractError(
                    "Assets.car is prohibited anywhere in the app bundle: {}".format(
                        _relative(path, app)
                    )
                )
            normalized = _normalized_identity(name)
            token = next(
                (
                    candidate
                    for candidate in PROHIBITED_IDENTITY_TOKENS
                    if candidate in normalized
                ),
                None,
            )
            if token is not None:
                raise AutoupdateContractError(
                    "prohibited updater artifact {!r}: {}".format(
                        token, _relative(path, app)
                    )
                )
        for name in directories:
            path = Path(root) / name
            folded = name.casefold()
            if folded.endswith(".app"):
                if path.is_symlink():
                    if not (
                        name == "Updater.app"
                        and path.parent.name == "Sparkle.framework"
                    ):
                        raise AutoupdateContractError(
                            "nested app must not be a symlink: {}".format(
                                _relative(path, app)
                            )
                        )
                else:
                    nested_apps.append(path)
            if folded == "sparkle.framework":
                if name != "Sparkle.framework" or path.is_symlink():
                    raise AutoupdateContractError(
                        "Sparkle.framework must use its exact name and be real"
                    )
                sparkle_frameworks.append(path)
        for name in files:
            if name == "Info.plist":
                plist_paths.append(Path(root) / name)

    if len(sparkle_frameworks) != 1:
        raise AutoupdateContractError(
            "app must contain exactly one real Sparkle.framework; found {}".format(
                len(sparkle_frameworks)
            )
        )
    for path in sorted(plist_paths):
        info = _read_plist(path, "bundled Info.plist")
        _reject_prohibited_plist_identity(_relative(path, app), info)
        if "CFBundleIconName" in info:
            raise AutoupdateContractError(
                "CFBundleIconName must be absent from every bundled Info.plist: {}"
                .format(_relative(path, app))
            )
    return sorted(nested_apps), sparkle_frameworks[0]


def _require_exact_symlink(link, target, label):
    if not link.is_symlink():
        raise AutoupdateContractError(
            "{} must be a symlink".format(label)
        )
    try:
        observed = link.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AutoupdateContractError(
            "{} is broken".format(label)
        ) from exc
    if observed != target.resolve(strict=True):
        raise AutoupdateContractError(
            "{} selects an unexpected target".format(label)
        )


def _require_current_symlink(container, version, label):
    _require_exact_symlink(
        container / "Current",
        version,
        "{} Versions/Current".format(label),
    )


def _focus_runtime_layout(app):
    framework = _require_directory(
        app / "Contents/Frameworks" / FOCUS_FRAMEWORK_NAME,
        "Focus Browser framework",
    )
    versions = _require_directory(framework / "Versions", "Focus framework Versions")
    real_versions = sorted(
        child
        for child in versions.iterdir()
        if child.name != "Current" and child.is_dir() and not child.is_symlink()
    )
    if len(real_versions) != 1:
        raise AutoupdateContractError(
            "Focus Browser framework must contain exactly one real version"
        )
    _require_current_symlink(versions, real_versions[0], "Focus Browser framework")
    version = real_versions[0]
    helpers = _require_directory(version / "Helpers", "Focus helpers")
    libraries = _require_directory(version / "Libraries", "Focus libraries")
    resources = _require_directory(version / "Resources", "Focus resources")
    root_links = {
        framework / "Focus Browser Framework": version / "Focus Browser Framework",
        framework / "Helpers": helpers,
        framework / "Libraries": libraries,
        framework / "Resources": resources,
    }
    for link, target in root_links.items():
        _require_exact_symlink(link, target, "Focus framework/{}".format(link.name))
    products = {
        label: _require_macho(version / relative, "Focus product " + label)
        for label, relative in FOCUS_RUNTIME_PRODUCTS.items()
    }
    return {
        "framework": framework,
        "version": version,
        "helpers": helpers,
        "libraries": libraries,
        "resources": resources,
        "products": products,
    }


def _sparkle_version_directory(framework):
    if (
        framework.parent.name != "Frameworks"
        or framework.parent.parent.name != "Contents"
    ):
        raise AutoupdateContractError(
            "Sparkle.framework must be embedded directly in Contents/Frameworks"
        )
    versions = _require_directory(framework / "Versions", "Sparkle Versions")
    real_versions = sorted(
        child
        for child in versions.iterdir()
        if child.name != "Current" and child.is_dir() and not child.is_symlink()
    )
    if [child.name for child in real_versions] != [SPARKLE_FRAMEWORK_VERSION]:
        raise AutoupdateContractError(
            "Sparkle.framework must contain only real version {!r}".format(
                SPARKLE_FRAMEWORK_VERSION
            )
        )
    version = real_versions[0]
    _require_current_symlink(versions, version, "Sparkle.framework")
    links = (
        ("Sparkle", version / "Sparkle"),
        ("Autoupdate", version / "Autoupdate"),
        ("Resources", version / "Resources"),
        ("Updater.app", version / "Updater.app"),
        ("XPCServices", version / "XPCServices"),
    )
    for name, target in links:
        _require_exact_symlink(
            framework / name,
            target,
            "Sparkle.framework/{}".format(name),
        )
    return version


def _validate_symlink_inventory(app, focus_layout, sparkle, sparkle_version):
    focus = focus_layout["framework"]
    focus_version = focus_layout["version"]
    allowed = {
        focus / "Versions/Current": focus_version,
        focus / "Focus Browser Framework": focus_version / "Focus Browser Framework",
        focus / "Helpers": focus_layout["helpers"],
        focus / "Libraries": focus_layout["libraries"],
        focus / "Resources": focus_layout["resources"],
        sparkle / "Versions/Current": sparkle_version,
        sparkle / "Sparkle": sparkle_version / "Sparkle",
        sparkle / "Autoupdate": sparkle_version / "Autoupdate",
        sparkle / "Resources": sparkle_version / "Resources",
        sparkle / "Updater.app": sparkle_version / "Updater.app",
        sparkle / "XPCServices": sparkle_version / "XPCServices",
        sparkle / "Headers": sparkle_version / "Headers",
        sparkle / "Modules": sparkle_version / "Modules",
        sparkle / "PrivateHeaders": sparkle_version / "PrivateHeaders",
    }
    observed = set()

    def fail(error):
        raise AutoupdateContractError(
            "cannot inspect app symlinks: {}".format(error)
        ) from error

    for root, directories, files in os.walk(
        str(app), topdown=True, onerror=fail, followlinks=False
    ):
        directories.sort()
        files.sort()
        for name in directories + files:
            path = Path(root) / name
            if path.is_symlink():
                observed.add(path)
    unexpected = sorted(observed - set(allowed))
    if unexpected:
        raise AutoupdateContractError(
            "unexpected app-bundle symlinks: {}".format(
                ", ".join(_relative(path, app) for path in unexpected)
            )
        )
    for link in sorted(observed):
        _require_exact_symlink(
            link,
            allowed[link],
            "bundle symlink {}".format(_relative(link, app)),
        )


def _require_exact_fields(info, expected, label):
    mismatches = []
    for key, value in expected.items():
        observed = info.get(key)
        if type(observed) is not type(value) or observed != value:
            mismatches.append(
                "{} expected {!r}, got {!r}".format(key, value, observed)
            )
    if mismatches:
        raise AutoupdateContractError(
            "{} metadata mismatch: {}".format(label, "; ".join(mismatches))
        )


def _plist_true(value):
    return value is True or value == 1 or value == "1"


def _reject_nested_app_icon(path, info, app):
    icon_keys = sorted(
        key
        for key in (
            "CFBundleIconFile",
            "CFBundleIconFiles",
            "CFBundleIconName",
            "CFBundleIcons",
        )
        if key in info
    )
    if icon_keys:
        raise AutoupdateContractError(
            "nested helper must be iconless; prohibited metadata {} in {}".format(
                ", ".join(icon_keys),
                _relative(path / "Contents/Info.plist", app),
            )
        )
    resources = path / "Contents/Resources"
    if resources.is_symlink():
        raise AutoupdateContractError(
            "nested helper Resources must not be a symlink: {}".format(
                _relative(resources, app)
            )
        )
    if resources.is_dir():
        icon_files = sorted(
            candidate
            for candidate in resources.rglob("*")
            if candidate.name.casefold().endswith(".icns")
        )
        if icon_files:
            raise AutoupdateContractError(
                "nested helper must be iconless; prohibited icon files: {}".format(
                    ", ".join(_relative(path, app) for path in icon_files)
                )
            )


def _validate_nested_apps(app, nested_apps, helpers, sparkle_version):
    expected_paths = {
        name: helpers / name for name in FOCUS_HELPER_IDENTITIES
    }
    expected_paths["Updater.app"] = sparkle_version / "Updater.app"

    observed = {}
    for nested_app in nested_apps:
        if nested_app.name in observed:
            raise AutoupdateContractError(
                "duplicate nested app name: {}".format(nested_app.name)
            )
        observed[nested_app.name] = nested_app
    if set(observed) != set(expected_paths):
        missing = sorted(set(expected_paths) - set(observed))
        extra = sorted(set(observed) - set(expected_paths))
        raise AutoupdateContractError(
            "nested app inventory mismatch; missing={}, extra={}".format(
                missing, extra
            )
        )

    products = {}
    for name, expected_path in expected_paths.items():
        path = observed[name]
        if path != expected_path:
            raise AutoupdateContractError(
                "nested app is outside its pinned location: {}".format(
                    _relative(path, app)
                )
            )
        info = _read_plist(path / "Contents/Info.plist", "nested app Info.plist")
        if name == "Updater.app":
            bundle_id, executable_name = SPARKLE_UPDATER_IDENTITY
            _require_exact_fields(
                info,
                {
                    "CFBundleIdentifier": bundle_id,
                    "CFBundleExecutable": executable_name,
                    "CFBundleShortVersionString": SPARKLE_VERSION,
                    "CFBundleVersion": SPARKLE_BUILD_VERSION,
                },
                "Sparkle Updater.app",
            )
        else:
            bundle_id, executable_name = FOCUS_HELPER_IDENTITIES[name]
            _require_exact_fields(
                info,
                {
                    "CFBundleIdentifier": bundle_id,
                    "CFBundleExecutable": executable_name,
                },
                name,
            )
        if not _plist_true(info.get("LSUIElement")):
            raise AutoupdateContractError(
                "nested helper app must be an LSUIElement agent: {}".format(
                    _relative(path, app)
                )
            )
        if name != "Focus Browser Helper (Alerts).app":
            _reject_nested_app_icon(path, info, app)
        executable = _require_macho(
            path / "Contents/MacOS" / executable_name,
            "nested app executable",
        )
        products["nested-app:" + name] = executable
    return products


def _is_macho(path):
    try:
        with Path(path).open("rb") as stream:
            return stream.read(4) in MACHO_MAGICS
    except OSError as exc:
        raise AutoupdateContractError(
            "cannot inspect executable magic for {}".format(path)
        ) from exc


def _require_macho(path, label):
    path = _require_file(path, label)
    if not _is_macho(path):
        raise AutoupdateContractError("{} is not Mach-O: {}".format(label, path))
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        mode != 0o755
        or not os.access(str(path), os.X_OK)
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise AutoupdateContractError(
            "{} must be executable mode 0755 with no unsafe write bits: {}"
            .format(label, path)
        )
    return path


def _sparkle_macho_inventory(version):
    expected = {
        label: version / relative
        for label, (relative, _metadata) in SPARKLE_PRODUCTS.items()
    }
    for label, path in expected.items():
        _require_macho(path, "Sparkle product {}".format(label))

    observed = set()

    def fail(error):
        raise AutoupdateContractError(
            "cannot inspect Sparkle.framework: {}".format(error)
        ) from error

    for root, directories, files in os.walk(
        str(version), topdown=True, onerror=fail, followlinks=False
    ):
        directories.sort()
        files.sort()
        for name in files:
            path = Path(root) / name
            if path.is_symlink():
                continue
            if _is_macho(path):
                observed.add(path.relative_to(version).as_posix())
    expected_relatives = {
        path.relative_to(version).as_posix() for path in expected.values()
    }
    if observed != expected_relatives:
        raise AutoupdateContractError(
            "Sparkle Mach-O inventory mismatch; missing={}, extra={}".format(
                sorted(expected_relatives - observed),
                sorted(observed - expected_relatives),
            )
        )
    return expected


def _bundle_macho_inventory(app):
    products = {}

    def fail(error):
        raise AutoupdateContractError(
            "cannot inspect app Mach-O inventory: {}".format(error)
        ) from error

    for root, directories, files in os.walk(
        str(app), topdown=True, onerror=fail, followlinks=False
    ):
        directories.sort()
        files.sort()
        for name in files:
            path = Path(root) / name
            if path.is_symlink():
                continue
            if _is_macho(path):
                relative = _relative(path, app)
                products["bundle-macho:" + relative] = path
    if not products:
        raise AutoupdateContractError("app contains no real Mach-O products")
    return products


def _validate_sparkle_metadata(version):
    framework_info = _read_plist(
        version / "Resources/Info.plist", "Sparkle framework Info.plist"
    )
    _require_exact_fields(
        framework_info,
        {
            "CFBundleIdentifier": "org.sparkle-project.Sparkle",
            "CFBundleExecutable": "Sparkle",
            "CFBundleShortVersionString": SPARKLE_VERSION,
            "CFBundleVersion": SPARKLE_BUILD_VERSION,
        },
        "Sparkle.framework",
    )
    for label, (_relative_binary, metadata) in SPARKLE_PRODUCTS.items():
        if metadata is None:
            continue
        relative_plist, bundle_id, executable = metadata
        info = _read_plist(version / relative_plist, label + " Info.plist")
        _require_exact_fields(
            info,
            {
                "CFBundleIdentifier": bundle_id,
                "CFBundleExecutable": executable,
                "CFBundleShortVersionString": SPARKLE_VERSION,
                "CFBundleVersion": SPARKLE_BUILD_VERSION,
            },
            label,
        )


def _architecture_set(path):
    try:
        result = subprocess.run(
            [LIPO, "-archs", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TOOL_TIMEOUT_SECONDS,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AutoupdateContractError(
            "cannot inspect architectures for {}: {}".format(path, exc)
        ) from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise AutoupdateContractError(
            "lipo failed for {}: {}".format(path, detail or result.returncode)
        )
    try:
        values = result.stdout.decode("utf-8", errors="strict").split()
    except UnicodeDecodeError as exc:
        raise AutoupdateContractError("lipo emitted non-UTF-8 output") from exc
    if not values or len(values) != len(set(values)):
        raise AutoupdateContractError(
            "lipo emitted an invalid architecture list for {}".format(path)
        )
    return frozenset(values)


def _verify_codesign(app):
    try:
        result = subprocess.run(
            [CODESIGN, "--verify", "--deep", "--strict", str(app)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TOOL_TIMEOUT_SECONDS,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AutoupdateContractError(
            "cannot verify app code signature: {}".format(exc)
        ) from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise AutoupdateContractError(
            "codesign verification failed for {}: {}".format(
                app, detail or result.returncode
            )
        )
    return True


def _validate_universal_products(app, products, architecture_reader):
    report = {}
    seen_paths = {}
    for label, path in sorted(products.items()):
        relative = _relative(path, app)
        previous = seen_paths.get(relative)
        if previous is not None:
            continue
        seen_paths[relative] = label
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise AutoupdateContractError(
                "cannot inspect Mach-O mode for {}".format(relative)
            ) from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or mode != 0o755
            or not os.access(str(path), os.X_OK)
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise AutoupdateContractError(
                "Mach-O must be executable mode 0755 with no unsafe write bits: {}"
                .format(relative)
            )
        try:
            architectures = frozenset(architecture_reader(path))
        except TypeError as exc:
            raise AutoupdateContractError(
                "invalid architecture result for {}".format(relative)
            ) from exc
        if architectures != ARCHITECTURES:
            raise AutoupdateContractError(
                "{} is not exactly universal arm64+x86_64: {}".format(
                    relative, sorted(architectures)
                )
            )
        report[label] = {
            "relative_path": relative,
            "architectures": sorted(architectures),
            "mode": "{:04o}".format(mode),
            "executable": True,
            "group_world_writable": False,
        }
    return report


def _run_binary_tool(command, label):
    try:
        result = subprocess.run(
            command,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TOOL_TIMEOUT_SECONDS,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AutoupdateContractError(
            "{} could not run: {}".format(label, exc)
        ) from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise AutoupdateContractError(
            "{} failed for {}: {}".format(
                label, command[-1], detail or result.returncode
            )
        )
    return result.stdout, result.stderr


def read_codesign_state(path, architecture):
    """Read one architecture's semantic CodeDirectory and entitlement state."""
    _stdout, detail_bytes = _run_binary_tool(
        [CODESIGN, "-d", "--arch", architecture, "--verbose=4", str(path)],
        "codesign detail inspection",
    )
    detail = detail_bytes.decode("utf-8", errors="replace")
    matches = _FLAGS_PATTERN.findall(detail)
    if len(matches) != 1:
        raise AutoupdateContractError(
            "codesign did not report one semantic flag set for {} ({})"
            .format(path, architecture)
        )
    flags = frozenset(
        value.strip() for value in matches[0].split(",") if value.strip()
    )
    lines = detail.splitlines()
    if "Signature=adhoc" not in lines:
        raise AutoupdateContractError(
            "signed product is not ad-hoc: {}".format(path)
        )
    if "TeamIdentifier=not set" not in lines:
        raise AutoupdateContractError(
            "ad-hoc product unexpectedly has a Team ID: {}".format(path)
        )
    entitlements_bytes, _diagnostics = _run_binary_tool(
        [
            CODESIGN,
            "-d",
            "--arch",
            architecture,
            "--entitlements",
            "-",
            "--xml",
            str(path),
        ],
        "codesign entitlement inspection",
    )
    if entitlements_bytes:
        try:
            entitlements = plistlib.loads(entitlements_bytes)
        except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
            raise AutoupdateContractError(
                "codesign emitted invalid entitlements for {} ({})".format(
                    path, architecture
                )
            ) from exc
        if not isinstance(entitlements, dict):
            raise AutoupdateContractError(
                "codesign entitlements root must be a dictionary"
            )
    else:
        entitlements = {}
    return {
        "flags": flags,
        "entitlements": entitlements,
        "identity": "adhoc",
        "team_identifier": None,
    }


def _adhoc_signing_inventory(app):
    focus = _focus_runtime_layout(app)
    helpers = focus["helpers"]
    libraries = focus["libraries"]
    loaders = {
        "app": app,
        "helper-app": helpers / "Focus Browser Helper.app",
        "helper-renderer-app": helpers / "Focus Browser Helper (Renderer).app",
        "helper-gpu-app": helpers / "Focus Browser Helper (GPU).app",
        "helper-alerts": helpers / "Focus Browser Helper (Alerts).app",
        "app-mode-app": _require_macho(
            helpers / "app_mode_loader", "app-mode loader"
        ),
        "web-app-shortcut-copier": _require_macho(
            helpers / "web_app_shortcut_copier", "web-app shortcut copier"
        ),
    }
    for label in FRAMEWORK_LOADERS[1:5]:
        _require_directory(loaders[label], "signed loader {}".format(label))
    crashpad = _require_macho(
        helpers / "chrome_crashpad_handler", "crashpad handler"
    )
    dylibs = sorted(libraries.glob("*.dylib"), key=lambda path: path.name)
    required_dylibs = {"libEGL.dylib", "libGLESv2.dylib"}
    if not required_dylibs.issubset({path.name for path in dylibs}):
        raise AutoupdateContractError("required ANGLE dylibs are missing")
    protected = {"framework": focus["framework"], "crashpad": crashpad}
    for path in dylibs:
        protected["dylib:" + path.name] = _require_macho(
            path, "signed dylib {}".format(path.name)
        )
    if tuple(loaders) != FRAMEWORK_LOADERS:
        raise AutoupdateContractError("framework loader inventory is incomplete")
    return loaders, protected


def validate_adhoc_signing_contract(app_value, codesign_state_reader=None):
    """Require exact per-slice flags and entitlements for every signed product."""
    app = Path(app_value).resolve(strict=True)
    reader = codesign_state_reader or read_codesign_state
    loaders, protected = _adhoc_signing_inventory(app)
    report = {"identity": "adhoc", "architectures": sorted(ARCHITECTURES), "products": {}}

    def validate_product(label, path, expected_flags, expected_entitlements):
        relative = "." if path == app else _relative(path, app)
        product = {"relative_path": relative, "architectures": {}}
        for architecture in sorted(ARCHITECTURES):
            try:
                state = reader(path, architecture)
            except AutoupdateContractError:
                raise
            except Exception as exc:
                raise AutoupdateContractError(
                    "cannot inspect signing state for {} ({}): {}".format(
                        relative, architecture, exc
                    )
                ) from exc
            if not isinstance(state, dict):
                raise AutoupdateContractError("codesign state reader returned invalid data")
            flags = frozenset(state.get("flags", ()))
            entitlements = state.get("entitlements")
            if flags != expected_flags:
                raise AutoupdateContractError(
                    "{} {} flags mismatch: expected {}, got {}".format(
                        label, architecture, sorted(expected_flags), sorted(flags)
                    )
                )
            if not isinstance(entitlements, dict):
                raise AutoupdateContractError("codesign entitlements are not a dictionary")
            if entitlements != expected_entitlements:
                raise AutoupdateContractError(
                    "{} {} entitlement dictionary mismatch: expected keys {}, got {}"
                    .format(
                        label,
                        architecture,
                        sorted(expected_entitlements),
                        sorted(entitlements),
                    )
                )
            product["architectures"][architecture] = {
                "flags": sorted(flags),
                "entitlements": dict(sorted(entitlements.items())),
                "disable_library_validation": (
                    expected_entitlements.get(DISABLE_LIBRARY_VALIDATION) is True
                ),
                "entitlement_keys": sorted(entitlements),
            }
        report["products"][label] = product

    for label, path in loaders.items():
        validate_product(label, path, LOADER_FLAGS, EXACT_ENTITLEMENTS[label])
    for label, path in protected.items():
        # Chromium's framework executable is an MH_DYLIB. Its release signer
        # intentionally omits process-only hardened-runtime flags for that
        # product; crashpad is the only protected standalone executable here.
        expected = FULL_RUNTIME_FLAGS if label == "crashpad" else DATA_ONLY_FLAGS
        validate_product(label, path, expected, {})
    report["framework_loaders"] = list(FRAMEWORK_LOADERS)
    report["passed"] = True
    return report


def _parse_focus_sparkle_linkage(dependencies_output, commands_output, path, architecture):
    """Parse one thin Focus Framework slice's exact Sparkle linkage contract."""
    try:
        dependencies_text = (
            dependencies_output.decode("utf-8", errors="strict")
            if isinstance(dependencies_output, bytes)
            else str(dependencies_output)
        )
        commands_text = (
            commands_output.decode("utf-8", errors="strict")
            if isinstance(commands_output, bytes)
            else str(commands_output)
        )
    except UnicodeDecodeError as exc:
        raise AutoupdateContractError("otool emitted non-UTF-8 output") from exc

    dependency_names = []
    for line in dependencies_text.splitlines()[1:]:
        stripped = line.strip()
        if stripped:
            dependency_names.append(stripped.split(" ", 1)[0])
    sparkle_dependencies = [
        value for value in dependency_names if "Sparkle.framework" in value
    ]
    if sparkle_dependencies != [SPARKLE_DEPENDENCY]:
        raise AutoupdateContractError(
            "{} ({}) Sparkle dependency mismatch: {}".format(
                path, architecture, sparkle_dependencies
            )
        )

    rpaths = []
    blocks = re.split(
        r"(?=^Load command [0-9]+\s*$)", commands_text, flags=re.MULTILINE
    )
    for block in blocks:
        if not re.search(r"^\s*cmd LC_RPATH\s*$", block, flags=re.MULTILINE):
            continue
        matches = re.findall(
            r"^\s*path\s+(\S+)\s+\(offset\s+\d+\)\s*$",
            block,
            flags=re.MULTILINE,
        )
        if len(matches) != 1:
            raise AutoupdateContractError(
                "{} ({}) contains a malformed LC_RPATH".format(path, architecture)
            )
        rpaths.extend(matches)
    if rpaths != [FOCUS_FRAMEWORK_RPATH]:
        raise AutoupdateContractError(
            "{} ({}) rpath mismatch: expected sole {!r}, got {}".format(
                path, architecture, FOCUS_FRAMEWORK_RPATH, rpaths
            )
        )
    return {
        "sparkle_dependency": SPARKLE_DEPENDENCY,
        "rpaths": list(rpaths),
    }


def read_focus_sparkle_linkage(path, architecture):
    dependencies, _ = _run_binary_tool(
        [OTOOL, "-L", "-arch", architecture, str(path)],
        "Focus Framework dependency inspection",
    )
    commands, _ = _run_binary_tool(
        [OTOOL, "-l", "-arch", architecture, str(path)],
        "Focus Framework rpath inspection",
    )
    return _parse_focus_sparkle_linkage(
        dependencies, commands, path, architecture
    )


def validate_focus_sparkle_linkage(app_value, linkage_reader=None):
    """Require the exact Sparkle load path and sole RPATH in both slices."""
    app = Path(app_value).resolve(strict=True)
    framework = _focus_runtime_layout(app)["products"]["focus-framework"]
    reader = linkage_reader or read_focus_sparkle_linkage
    slices = {}
    for architecture in sorted(ARCHITECTURES):
        try:
            observed = reader(framework, architecture)
        except AutoupdateContractError:
            raise
        except Exception as exc:
            raise AutoupdateContractError(
                "cannot inspect Focus Framework linkage ({})".format(architecture)
            ) from exc
        expected = {
            "sparkle_dependency": SPARKLE_DEPENDENCY,
            "rpaths": [FOCUS_FRAMEWORK_RPATH],
        }
        if observed != expected:
            raise AutoupdateContractError(
                "Focus Framework linkage mismatch for {}".format(architecture)
            )
        slices[architecture] = observed
    return {
        "relative_path": _relative(framework, app),
        "architectures": slices,
        "passed": True,
    }


def _parse_macos_minimum(output, path, architecture):
    try:
        text = output.decode("utf-8", errors="strict") if isinstance(output, bytes) else str(output)
    except UnicodeDecodeError as exc:
        raise AutoupdateContractError("vtool emitted non-UTF-8 output") from exc
    observed = []
    blocks = re.split(r"(?=^Load command [0-9]+\s*$)", text, flags=re.MULTILINE)
    for block in blocks:
        if re.search(r"^\s*cmd LC_BUILD_VERSION\s*$", block, flags=re.MULTILINE):
            platform_match = re.search(r"^\s*platform\s+(\S+)\s*$", block, flags=re.MULTILINE)
            if platform_match is None:
                raise AutoupdateContractError("LC_BUILD_VERSION omitted platform")
            if platform_match.group(1).upper() not in ("1", "MACOS"):
                continue
            value = re.search(r"^\s*minos\s+(\S+)\s*$", block, flags=re.MULTILINE)
            if value is None:
                raise AutoupdateContractError("LC_BUILD_VERSION omitted minos")
            observed.append(value.group(1))
        elif re.search(r"^\s*cmd LC_VERSION_MIN_MACOSX\s*$", block, flags=re.MULTILINE):
            value = re.search(r"^\s*version\s+(\S+)\s*$", block, flags=re.MULTILINE)
            if value is None:
                raise AutoupdateContractError("LC_VERSION_MIN_MACOSX omitted version")
            observed.append(value.group(1))
    if len(observed) != 1 or not _VERSION_PATTERN.fullmatch(observed[0]):
        raise AutoupdateContractError(
            "{} ({}) must contain exactly one canonical macOS minimum load command"
            .format(path, architecture)
        )
    return observed[0]


def _minimum_file_snapshot(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        getattr(value, "st_flags", 0),
    )


def read_macos_minimum(path, architecture, runner=subprocess.run):
    """Read one slice's minimum macOS through a pinned open descriptor.

    ``otool-classic`` treats parentheses in a Mach-O pathname as archive
    syntax. Focus helper executables intentionally contain such characters, so
    the release gate uses the system ``vtool`` on an inherited ``/dev/fd``
    descriptor instead. The named file is rebound before and after inspection
    so pathname replacement cannot silently validate a different binary.
    """
    path = Path(path)
    if architecture not in ARCHITECTURES:
        raise AutoupdateContractError(
            "unsupported Mach-O architecture: {}".format(architecture)
        )
    if not hasattr(os, "O_NOFOLLOW"):
        raise AutoupdateContractError(
            "descriptor-safe Mach-O inspection requires O_NOFOLLOW"
        )
    try:
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise AutoupdateContractError(
            "cannot open Mach-O for minimum-system inspection: {}".format(path)
        ) from exc
    try:
        opened = os.fstat(descriptor)
        named = os.lstat(str(path))
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or _minimum_file_snapshot(opened) != _minimum_file_snapshot(named)
        ):
            raise AutoupdateContractError(
                "Mach-O changed before minimum-system inspection: {}".format(path)
            )
        command = [
            VTOOL,
            "-arch",
            architecture,
            "-show-build",
            "/dev/fd/{}".format(descriptor),
        ]
        try:
            result = runner(
                command,
                check=False,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=TOOL_TIMEOUT_SECONDS,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
                pass_fds=(descriptor,),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AutoupdateContractError(
                "Mach-O minimum-system inspection could not run: {}".format(exc)
            ) from exc
        rebound = os.fstat(descriptor)
        rebound_named = os.lstat(str(path))
        if (
            _minimum_file_snapshot(opened) != _minimum_file_snapshot(rebound)
            or _minimum_file_snapshot(opened)
            != _minimum_file_snapshot(rebound_named)
        ):
            raise AutoupdateContractError(
                "Mach-O changed during minimum-system inspection: {}".format(path)
            )
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise AutoupdateContractError(
                "Mach-O minimum-system inspection failed for {} ({}): {}".format(
                    path, architecture, detail or result.returncode
                )
            )
        return _parse_macos_minimum(result.stdout, path, architecture)
    finally:
        os.close(descriptor)


def parse_macos_version(value):
    """Return one validated deployment-target string as a comparable tuple."""
    if not isinstance(value, str) or not _VERSION_PATTERN.fullmatch(value):
        raise AutoupdateContractError("invalid Mach-O minimum system version")
    parts = tuple(int(component) for component in value.split("."))
    return parts + (0,) * (3 - len(parts))


def _version_tuple(value):
    return parse_macos_version(value)


def validate_macho_minimum_system_versions(
    app_value,
    universal_products,
    minimum_reader=None,
):
    """Gate every embedded Mach-O slice against the advertised macOS 12 floor."""
    app = Path(app_value).resolve(strict=True)
    reader = minimum_reader or read_macos_minimum
    advertised = _version_tuple(MINIMUM_MACOS_VERSION)
    sparkle_prefix = SPARKLE_FRAMEWORK_RELATIVE_PATH + "/"
    report = {}
    for label, product in sorted(universal_products.items()):
        relative = product.get("relative_path") if isinstance(product, dict) else None
        if not isinstance(relative, str) or not relative:
            raise AutoupdateContractError("universal product omitted its relative path")
        path = app / relative
        is_sparkle = relative.startswith(sparkle_prefix)
        architectures = {}
        for architecture in sorted(ARCHITECTURES):
            try:
                observed = reader(path, architecture)
            except AutoupdateContractError:
                raise
            except Exception as exc:
                raise AutoupdateContractError(
                    "cannot inspect minimum macOS for {} ({}): {}".format(
                        relative, architecture, exc
                    )
                ) from exc
            parsed = _version_tuple(observed)
            if parsed > advertised:
                raise AutoupdateContractError(
                    "{} {} targets macOS {} newer than advertised {}".format(
                        relative, architecture, observed, MINIMUM_MACOS_VERSION
                    )
                )
            if not is_sparkle and parsed != advertised:
                raise AutoupdateContractError(
                    "Chromium-owned {} {} must target exactly macOS {} (got {})"
                    .format(relative, architecture, MINIMUM_MACOS_VERSION, observed)
                )
            architectures[architecture] = observed
        report[label] = {
            "relative_path": relative,
            "policy": "at-most-advertised" if is_sparkle else "exact-advertised",
            "architectures": architectures,
        }
    return {
        "advertised_minimum": MINIMUM_MACOS_VERSION,
        "products": report,
        "passed": True,
    }


def _validate_icon(path, label):
    path = _require_file(path, label)
    observed = _sha256_file(path)
    if observed != CANONICAL_ICON_SHA256:
        raise AutoupdateContractError(
            "{} SHA-256 mismatch: expected {}, got {}".format(
                label, CANONICAL_ICON_SHA256, observed
            )
        )
    return observed


def _validate_sparkle_provenance(
    embedded_framework,
    source_root_value,
    dependency_validator=None,
):
    validator = dependency_validator or acquire_sparkle.validate_dependency_root
    try:
        source_report = validator(source_root_value)
    except acquire_sparkle.SparkleAcquisitionError as exc:
        raise AutoupdateContractError(
            "Sparkle dependency root provenance failed: {}".format(exc)
        ) from exc
    if not isinstance(source_report, dict):
        raise AutoupdateContractError(
            "Sparkle dependency validator returned an invalid report"
        )
    try:
        source_root = Path(source_report["root"]).resolve(strict=True)
        source_framework = source_root / "Sparkle.framework"
        source_manifest = acquire_sparkle.framework_subtree_manifest(source_framework)
        embedded_manifest = acquire_sparkle.framework_subtree_manifest(
            embedded_framework
        )
    except acquire_sparkle.SparkleAcquisitionError as exc:
        raise AutoupdateContractError(
            "cannot compare Sparkle framework provenance: {}".format(exc)
        ) from exc
    except (KeyError, OSError, RuntimeError) as exc:
        raise AutoupdateContractError(
            "Sparkle dependency provenance report is incomplete"
        ) from exc
    if source_manifest != embedded_manifest:
        source_paths = set(source_manifest)
        embedded_paths = set(embedded_manifest)
        changed = sorted(
            path
            for path in source_paths & embedded_paths
            if source_manifest[path] != embedded_manifest[path]
        )
        raise AutoupdateContractError(
            "embedded Sparkle.framework differs from pinned dependency subtree; "
            "missing={}, extra={}, changed={}".format(
                sorted(source_paths - embedded_paths),
                sorted(embedded_paths - source_paths),
                changed,
            )
        )
    subtree_sha256 = acquire_sparkle.framework_subtree_sha256(source_manifest)
    expected_subtree_sha256 = source_report.get("framework_subtree_sha256")
    if expected_subtree_sha256 != subtree_sha256:
        raise AutoupdateContractError(
            "Sparkle dependency provenance subtree digest changed"
        )
    receipt_sha256 = source_report.get("receipt_sha256")
    if (
        not isinstance(receipt_sha256, str)
        or len(receipt_sha256) != 64
        or any(character not in "0123456789abcdef" for character in receipt_sha256)
    ):
        raise AutoupdateContractError(
            "Sparkle dependency provenance receipt digest is invalid"
        )
    return {
        "source_root": str(source_root),
        "receipt_sha256": receipt_sha256,
        "framework_entries": len(source_manifest),
        "framework_subtree_sha256": subtree_sha256,
    }


def validate_app_bundle(
    app_value,
    architecture_reader=None,
    signature_verifier=None,
    sparkle_source_root=None,
    dependency_validator=None,
):
    """Validate and describe the exact production Sparkle app contract."""
    candidate = Path(app_value).expanduser()
    if candidate.name != APP_NAME or candidate.is_symlink():
        raise AutoupdateContractError(
            "app must be a real directory named {!r}".format(APP_NAME)
        )
    try:
        app = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AutoupdateContractError(
            "app does not exist: {}".format(candidate)
        ) from exc
    _require_directory(app, "Focus Browser app")

    app_info = _read_plist(app / "Contents/Info.plist", "app Info.plist")
    _require_exact_fields(
        app_info,
        {
            "CFBundleIdentifier": APP_BUNDLE_ID,
            "CFBundleExecutable": APP_EXECUTABLE,
            "CFBundleShortVersionString": APP_SHORT_VERSION,
            "CFBundleVersion": APP_VERSION,
            "LSMinimumSystemVersion": MINIMUM_MACOS_VERSION,
            "CFBundleIconFile": "app.icns",
            **SPARKLE_APP_INFO_CONTRACT,
        },
        APP_NAME,
    )
    if "CFBundleIconName" in app_info:
        raise AutoupdateContractError(
            "CFBundleIconName must be absent so macOS selects canonical app.icns"
        )
    if "SUPublicDSAKeyFile" in app_info:
        raise AutoupdateContractError("legacy Sparkle DSA key is prohibited")

    nested_apps, sparkle_framework = _walk_bundle(app)
    focus_layout = _focus_runtime_layout(app)
    helpers = focus_layout["helpers"]
    expected_sparkle_framework = app / SPARKLE_FRAMEWORK_RELATIVE_PATH
    if sparkle_framework != expected_sparkle_framework:
        raise AutoupdateContractError(
            "Sparkle.framework is outside its pinned Contents/Frameworks path: {}"
            .format(_relative(sparkle_framework, app))
        )
    sparkle_version = _sparkle_version_directory(sparkle_framework)
    _validate_sparkle_metadata(sparkle_version)
    _validate_symlink_inventory(
        app, focus_layout, sparkle_framework, sparkle_version
    )

    products = {
        "app": _require_macho(
            app / "Contents/MacOS" / APP_EXECUTABLE, "app executable"
        )
    }
    products.update(focus_layout["products"])
    products.update(
        _validate_nested_apps(
            app, nested_apps, helpers, sparkle_version
        )
    )
    products.update(
        {
            "sparkle:" + label: path
            for label, path in _sparkle_macho_inventory(sparkle_version).items()
        }
    )
    known_products = set(products.values())
    for label, path in _bundle_macho_inventory(app).items():
        if path not in known_products:
            products[label] = path
            known_products.add(path)
    alerts = helpers / "Focus Browser Helper (Alerts).app"
    alerts_info = _read_plist(
        alerts / "Contents/Info.plist", "Alerts helper Info.plist"
    )
    _require_exact_fields(
        alerts_info,
        {"CFBundleIconFile": "app.icns"},
        "Alerts helper icon metadata",
    )
    if "CFBundleIconName" in alerts_info:
        raise AutoupdateContractError(
            "Alerts helper CFBundleIconName must be absent so macOS selects canonical app.icns"
        )
    icons = {
        "app": _validate_icon(
            app / "Contents/Resources/app.icns", "main app icon"
        ),
        "alerts": _validate_icon(
            alerts / "Contents/Resources/app.icns", "Alerts helper icon"
        ),
    }

    reader = architecture_reader or _architecture_set
    universal = _validate_universal_products(app, products, reader)
    verifier = signature_verifier or _verify_codesign
    try:
        signature_result = verifier(app)
    except AutoupdateContractError:
        raise
    except Exception as exc:
        raise AutoupdateContractError(
            "codesign verification failed for {}: {}".format(app, exc)
        ) from exc
    if signature_result is False:
        raise AutoupdateContractError(
            "codesign verifier rejected the app bundle"
        )
    provenance = None
    if sparkle_source_root is not None:
        provenance = _validate_sparkle_provenance(
            sparkle_framework,
            sparkle_source_root,
            dependency_validator=dependency_validator,
        )
    return {
        "schema": 1,
        "passed": True,
        "app": str(app),
        "app_version": APP_VERSION,
        "app_short_version": APP_SHORT_VERSION,
        "minimum_macos": MINIMUM_MACOS_VERSION,
        "feed_url": SPARKLE_FEED_URL,
        "public_ed_key": SPARKLE_PUBLIC_ED_KEY,
        "sparkle": {
            "version": SPARKLE_VERSION,
            "build_version": SPARKLE_BUILD_VERSION,
            "framework_version": SPARKLE_FRAMEWORK_VERSION,
            "relative_path": _relative(sparkle_framework, app),
            "provenance": provenance,
        },
        "codesign_verified": True,
        "provisioning_profiles_absent": True,
        "icons": icons,
        "nested_apps": sorted(
            _relative(path, app) for path in nested_apps
        ),
        "universal_products": universal,
    }


def validate_release_bundle(
    app_value,
    sparkle_source_root,
    architecture_reader=None,
    signature_verifier=None,
    dependency_validator=None,
    codesign_state_reader=None,
    minimum_reader=None,
    linkage_reader=None,
):
    """Run the complete source/staged/mounted automatic-update release gate.

    Release validation deliberately requires a completed, receipt-validated
    acquire_sparkle.py root.  The returned report is JSON-serializable and
    includes executable modes, per-slice CodeDirectory/entitlement state, and
    every embedded Mach-O slice's deployment target.
    """
    if sparkle_source_root is None:
        raise AutoupdateContractError(
            "automatic-update release validation requires Sparkle provenance"
        )
    report = validate_app_bundle(
        app_value,
        architecture_reader=architecture_reader,
        signature_verifier=signature_verifier,
        sparkle_source_root=sparkle_source_root,
        dependency_validator=dependency_validator,
    )
    provenance = report.get("sparkle", {}).get("provenance")
    if not isinstance(provenance, dict):
        raise AutoupdateContractError(
            "automatic-update release validation omitted Sparkle provenance"
        )
    app = Path(report["app"])
    signing = validate_adhoc_signing_contract(
        app, codesign_state_reader=codesign_state_reader
    )
    minimums = validate_macho_minimum_system_versions(
        app,
        report["universal_products"],
        minimum_reader=minimum_reader,
    )
    linkage = validate_focus_sparkle_linkage(
        app, linkage_reader=linkage_reader
    )
    report = dict(report)
    report["schema"] = 2
    report["release_gate"] = {
        "sparkle_provenance_required": True,
        "executable_modes_verified": True,
        # This structural/runtime gate is also used while producing a local
        # development DMG. A real old-to-new Sparkle replacement is a separate
        # public-release-only acceptance and must never be implied here.
        "update_e2e_verified": False,
        "update_e2e_required_for_public_release": True,
        "adhoc_signing": signing,
        "macho_minimum_system_versions": minimums,
        "focus_sparkle_linkage": linkage,
        "passed": True,
    }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate the Focus Browser Sparkle 2.9.4 app contract."
    )
    parser.add_argument("app", help="Path to Focus Browser.app")
    parser.add_argument(
        "--sparkle-source-root",
        required=True,
        help=(
            "completed acquire_sparkle.py root; require the embedded "
            "framework to match its receipt and exact subtree"
        ),
    )
    arguments = parser.parse_args(argv)
    try:
        report = validate_release_bundle(
            arguments.app,
            sparkle_source_root=arguments.sparkle_source_root,
        )
    except AutoupdateContractError as exc:
        print("autoupdate contract failed: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
