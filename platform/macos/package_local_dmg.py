#!/usr/bin/env python3
"""Safely package an already-signed Focus Browser app into a local DMG."""

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path


APP_BUNDLE_NAME = "Focus Browser.app"
BUNDLE_ID = "com.focusbrowser.browser"
VOLUME_NAME = "Focus Browser"

DITTO = "/usr/bin/ditto"
HDIUTIL = "/usr/bin/hdiutil"
LIPO = "/usr/bin/lipo"
CODESIGN = "/usr/bin/codesign"
SYSTEM_TOOLS = (DITTO, HDIUTIL, LIPO, CODESIGN)

ARCHITECTURE_ORDER = ("arm64", "x86_64")
ACCEPTED_ARCHITECTURE_SETS = frozenset(
    (
        frozenset(("arm64",)),
        frozenset(("x86_64",)),
        frozenset(("arm64", "x86_64")),
    )
)


class PackageError(RuntimeError):
    """Raised when an app or image fails the local packaging contract."""


def checked_run(command):
    """Run one fixed-shape subprocess command without a shell."""
    if not isinstance(command, list) or not command or not all(
        isinstance(item, str) and item for item in command
    ):
        raise PackageError("subprocess command must be a non-empty list of strings")
    result = subprocess.run(
        command,
        check=False,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise PackageError(
            "command failed ({}): {}\n{}".format(
                result.returncode, " ".join(command), detail
            )
        )
    return result.stdout


def require_system_tools():
    """Fail closed unless every required tool exists at its system path."""
    missing = [
        tool
        for tool in SYSTEM_TOOLS
        if not os.path.isfile(tool) or not os.access(tool, os.X_OK)
    ]
    if missing:
        raise PackageError("required system tool is unavailable: {}".format(", ".join(missing)))


def resolve_app_path(value):
    """Resolve an explicit, existing Focus Browser.app directory."""
    candidate = Path(value).expanduser()
    if candidate.name != APP_BUNDLE_NAME:
        raise PackageError("--app must name exactly {!r}".format(APP_BUNDLE_NAME))
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PackageError("app does not exist: {}".format(candidate)) from exc
    if resolved.name != APP_BUNDLE_NAME or not resolved.is_dir():
        raise PackageError("app must be an existing {} directory".format(APP_BUNDLE_NAME))
    return resolved


def resolve_output_path(value):
    """Resolve an explicit, non-existing DMG destination without creating it."""
    candidate = Path(value).expanduser()
    if candidate.suffix != ".dmg" or candidate.name == ".dmg":
        raise PackageError("--output must be a .dmg file path")
    if os.path.lexists(str(candidate)):
        raise PackageError("refusing to overwrite existing output: {}".format(candidate))
    try:
        parent = candidate.parent.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PackageError("output parent does not exist: {}".format(candidate.parent)) from exc
    if not parent.is_dir():
        raise PackageError("output parent is not a directory: {}".format(parent))
    resolved = parent / candidate.name
    if os.path.lexists(str(resolved)):
        raise PackageError("refusing to overwrite existing output: {}".format(resolved))
    return resolved


def _require_output_outside_app(app, output):
    try:
        output.relative_to(app)
    except ValueError:
        return
    raise PackageError("DMG output must not be inside the source app bundle")


def _read_info_plist(app):
    info_path = app / "Contents" / "Info.plist"
    if not info_path.is_file() or info_path.is_symlink():
        raise PackageError("missing regular Info.plist: {}".format(info_path))
    try:
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError, TypeError, OverflowError) as exc:
        raise PackageError("invalid Info.plist: {}".format(info_path)) from exc
    if not isinstance(info, dict):
        raise PackageError("Info.plist root must be a dictionary")
    return info


def _validate_executable_leaf(value):
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or Path(value).name != value
    ):
        raise PackageError("CFBundleExecutable must be a non-empty leaf name")
    return value


def _read_architectures(executable_path):
    output = checked_run([LIPO, "-archs", str(executable_path)])
    tokens = output.split()
    architecture_set = frozenset(tokens)
    if len(tokens) != len(architecture_set) or architecture_set not in ACCEPTED_ARCHITECTURE_SETS:
        raise PackageError(
            "main executable must be arm64, x86_64, or universal arm64+x86_64; got {!r}".format(
                output.strip()
            )
        )
    return [name for name in ARCHITECTURE_ORDER if name in architecture_set]


def validate_app(app_path):
    """Validate identity, main architecture, and the complete existing signature."""
    app = resolve_app_path(app_path)
    info = _read_info_plist(app)
    bundle_id = info.get("CFBundleIdentifier")
    if bundle_id != BUNDLE_ID:
        raise PackageError(
            "unexpected CFBundleIdentifier: expected {!r}, got {!r}".format(
                BUNDLE_ID, bundle_id
            )
        )
    executable_name = _validate_executable_leaf(info.get("CFBundleExecutable"))
    executable_path = app / "Contents" / "MacOS" / executable_name
    if not executable_path.is_file() or executable_path.is_symlink():
        raise PackageError("missing regular main executable: {}".format(executable_path))
    architectures = _read_architectures(executable_path)
    checked_run([CODESIGN, "--verify", "--deep", "--strict", str(app)])
    return {
        "app": str(app),
        "bundle_id": bundle_id,
        "executable": executable_name,
        "architectures": architectures,
    }


def _identity(report):
    return (
        report["bundle_id"],
        report["executable"],
        tuple(report["architectures"]),
    )


def _require_same_app(expected, observed, location):
    if _identity(observed) != _identity(expected):
        raise PackageError("{} app identity or architecture changed during packaging".format(location))


def inspect_mounted_image(image_path, mountpoint, expected_app):
    """Attach read-only, inspect the mounted payload, and always detach it."""
    attach = [
        HDIUTIL,
        "attach",
        "-readonly",
        "-nobrowse",
        "-noautoopen",
        "-mountpoint",
        str(mountpoint),
        str(image_path),
    ]
    attached = False
    primary_error = None
    detach_error = None
    observed = None
    try:
        checked_run(attach)
        attached = True
        if not os.path.ismount(str(mountpoint)):
            raise PackageError("hdiutil did not mount the image at {}".format(mountpoint))
        if not (os.statvfs(str(mountpoint)).f_flag & os.ST_RDONLY):
            raise PackageError("mounted image is not read-only")

        applications_link = mountpoint / "Applications"
        if not applications_link.is_symlink():
            raise PackageError("mounted image is missing the Applications symlink")
        if os.readlink(str(applications_link)) != "/Applications":
            raise PackageError("mounted Applications link has an unexpected target")

        observed = validate_app(mountpoint / APP_BUNDLE_NAME)
        _require_same_app(expected_app, observed, "mounted")
    except Exception as exc:  # Detachment still has to run for validation failures.
        primary_error = exc
    finally:
        if attached or os.path.ismount(str(mountpoint)):
            try:
                checked_run([HDIUTIL, "detach", str(mountpoint)])
            except Exception as exc:
                detach_error = exc

    if primary_error is not None:
        if detach_error is not None:
            raise PackageError(
                "{}; additionally failed to detach image: {}".format(
                    primary_error, detach_error
                )
            ) from primary_error
        raise primary_error
    if detach_error is not None:
        raise PackageError("failed to detach image: {}".format(detach_error)) from detach_error
    return observed


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_local_dmg(app_value, output_value, require_universal=False):
    """Create, verify, mount-inspect, and atomically place one local DMG."""
    require_system_tools()
    app = resolve_app_path(app_value)
    output = resolve_output_path(output_value)
    _require_output_outside_app(app, output)
    source_report = validate_app(app)
    if require_universal and source_report["architectures"] != list(ARCHITECTURE_ORDER):
        raise PackageError(
            "universal DMG requires main executable architectures arm64+x86_64; got {}".format(
                ",".join(source_report["architectures"])
            )
        )
    placed = False
    placed_identity = None
    report = None
    try:
        with tempfile.TemporaryDirectory(
            dir=str(output.parent), prefix=".focusbrowser-dmg-"
        ) as temporary:
            temporary_root = Path(temporary)
            staging = temporary_root / "staging"
            staging.mkdir()
            staged_app = staging / APP_BUNDLE_NAME
            checked_run([DITTO, str(app), str(staged_app)])
            os.symlink("/Applications", str(staging / "Applications"))

            staged_report = validate_app(staged_app)
            _require_same_app(source_report, staged_report, "staged")

            candidate = temporary_root / "FocusBrowser-local.dmg"
            checked_run(
                [
                    HDIUTIL,
                    "create",
                    "-volname",
                    VOLUME_NAME,
                    "-srcfolder",
                    str(staging),
                    "-format",
                    "UDZO",
                    str(candidate),
                ]
            )
            if not candidate.is_file() or candidate.is_symlink() or candidate.stat().st_size <= 0:
                raise PackageError("hdiutil did not create a non-empty regular DMG")
            checked_run([HDIUTIL, "verify", str(candidate)])

            mountpoint = temporary_root / "mounted"
            mountpoint.mkdir()
            mounted_report = inspect_mounted_image(candidate, mountpoint, source_report)
            _require_same_app(source_report, mounted_report, "mounted")

            size = candidate.stat().st_size
            digest = sha256_file(candidate)
            if os.path.lexists(str(output)):
                raise PackageError("refusing to overwrite output created during packaging: {}".format(output))
            candidate_stat = candidate.stat()
            placed_identity = (candidate_stat.st_dev, candidate_stat.st_ino)
            try:
                os.link(str(candidate), str(output))
            except FileExistsError as exc:
                raise PackageError("refusing to overwrite existing output: {}".format(output)) from exc
            except OSError as exc:
                raise PackageError("failed to atomically place output: {}".format(exc)) from exc
            placed = True
            candidate.unlink()
            report = {
                "app": str(app),
                "output": str(output),
                "bundle_id": source_report["bundle_id"],
                "executable": source_report["executable"],
                "architectures": source_report["architectures"],
                "require_universal": bool(require_universal),
                "format": "UDZO",
                "size_bytes": size,
                "sha256": digest,
                "signature": "pre-existing; verified source, staged, and mounted",
                "signing_performed": False,
                "notarization_performed": False,
                "local_only": True,
            }
    except Exception:
        if placed and os.path.lexists(str(output)):
            try:
                output_stat = os.lstat(str(output))
                if (output_stat.st_dev, output_stat.st_ino) == placed_identity:
                    output.unlink()
            except OSError:
                pass
        raise
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, help="existing Focus Browser.app path")
    parser.add_argument("--output", required=True, help="new, non-existing .dmg path")
    parser.add_argument(
        "--require-universal",
        action="store_true",
        help="reject thin apps; require both arm64 and x86_64",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)
    try:
        report = package_local_dmg(
            args.app,
            args.output,
            require_universal=args.require_universal,
        )
    except (OSError, PackageError, plistlib.InvalidFileException) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("OK: created and verified local Focus Browser DMG")
        print("Output: {}".format(report["output"]))
        print("Architectures: {}".format(", ".join(report["architectures"])))
        print("Size: {} bytes".format(report["size_bytes"]))
        print("SHA-256: {}".format(report["sha256"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
