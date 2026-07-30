#!/usr/bin/env python3
"""Safely package an already-signed Focus Browser app into a local DMG."""

import argparse
import hashlib
import json
import os
import plistlib
import stat
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


class CommittedPublishError(PackageError):
    """Publication committed durably, but candidate cleanup did not finish."""

    def __init__(self, message, final_identity):
        super().__init__(message)
        self.final_identity = tuple(final_identity)


def _directory_flags():
    """Return the mandatory flags used to pin a directory without symlinks."""
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    missing = [name for name in required if not hasattr(os, name)]
    if missing:
        raise PackageError(
            "safe DMG publication requires {}".format(", ".join(missing))
        )
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _entry_stat(name, directory_fd):
    """lstat one leaf relative to an already pinned directory descriptor."""
    try:
        return os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None


def _same_inode(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _require_pinned_directory(path, directory_fd, private=False):
    """Fail if a pinned directory or its absolute pathname was replaced."""
    pinned = os.fstat(directory_fd)
    try:
        named = os.lstat(str(path))
    except OSError as exc:
        raise PackageError("pinned DMG directory pathname changed: {}".format(path)) from exc
    if (
        not stat.S_ISDIR(pinned.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or stat.S_ISLNK(named.st_mode)
        or not _same_inode(pinned, named)
    ):
        raise PackageError("pinned DMG directory was replaced: {}".format(path))
    if private and (
        stat.S_IMODE(pinned.st_mode) != 0o700
        or pinned.st_uid != os.geteuid()
    ):
        raise PackageError("DMG candidate root must be owner-only mode 0700")
    return pinned


def _require_safe_candidate(observed, expected_identity, expected_size):
    """Require one owner-controlled, immutable-for-publication candidate inode."""
    unsafe_mode = (
        stat.S_IWGRP
        | stat.S_IWOTH
        | stat.S_IXUSR
        | stat.S_IXGRP
        | stat.S_IXOTH
        | stat.S_ISUID
        | stat.S_ISGID
        | stat.S_ISVTX
    )
    if (
        not stat.S_ISREG(observed.st_mode)
        or (observed.st_dev, observed.st_ino) != tuple(expected_identity)
        or observed.st_size != expected_size
        or observed.st_nlink != 1
        or observed.st_uid != os.geteuid()
        or not (observed.st_mode & stat.S_IRUSR)
        or observed.st_mode & unsafe_mode
    ):
        raise PackageError(
            "DMG candidate must be an owner-controlled, non-executable regular "
            "file with one link and no group/world write permission"
        )


def _sha256_fd(file_fd):
    """Hash the exact descriptor-pinned inode without reopening its pathname."""
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(file_fd, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()


def _rollback_exact_output(parent_fd, output_name, identity):
    """Rollback only our exact output inode and durably record its removal."""
    observed = _entry_stat(output_name, parent_fd)
    if observed is None:
        return False
    if not stat.S_ISREG(observed.st_mode) or (
        observed.st_dev,
        observed.st_ino,
    ) != tuple(identity):
        return False
    os.unlink(output_name, dir_fd=parent_fd)
    if _entry_stat(output_name, parent_fd) is not None:
        raise PackageError("failed to remove rejected DMG output inode")
    os.fsync(parent_fd)
    return True


def durable_publish_candidate(
    candidate,
    output,
    expected_identity,
    expected_size,
    expected_sha256,
):
    """Durably publish one accepted inode without overwrite or pathname races.

    The commit boundary is the successful fsync of the descriptor-pinned output
    parent followed by pathname identity revalidation.  Before that boundary,
    any exact link created by this call is rolled back by inode and the parent is
    fsynced.  After it, cleanup failures retain the accepted final inode and are
    reported as :class:`CommittedPublishError`.

    This is intentionally a bounded transaction, not a persistent recovery
    journal.  A process or machine crash after the final directory fsync but
    before candidate cleanup can leave the private candidate as a second link;
    the accepted output remains durable and is never silently overwritten.
    """
    candidate = Path(candidate)
    output = Path(output)
    if (
        candidate.name in ("", ".", "..")
        or output.name in ("", ".", "..")
        or candidate.parent == output.parent
    ):
        raise PackageError("DMG publication requires distinct directory leaf paths")

    root_fd = parent_fd = candidate_fd = output_fd = None
    committed = False
    link_syscall_rejected = False
    try:
        root_fd = os.open(str(candidate.parent), _directory_flags())
        parent_fd = os.open(str(output.parent), _directory_flags())
        _require_pinned_directory(candidate.parent, root_fd, private=True)
        _require_pinned_directory(output.parent, parent_fd)
        candidate_fd = os.open(
            candidate.name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        pinned_candidate = os.fstat(candidate_fd)
        named_candidate = _entry_stat(candidate.name, root_fd)
        if named_candidate is None or not _same_inode(
            pinned_candidate, named_candidate
        ):
            raise PackageError("DMG candidate pathname changed before publication")
        _require_safe_candidate(
            pinned_candidate,
            expected_identity,
            expected_size,
        )
        if _sha256_fd(candidate_fd) != expected_sha256:
            raise PackageError("DMG candidate hash changed before publication")
        if _entry_stat(output.name, parent_fd) is not None:
            raise PackageError("refusing to overwrite existing DMG output")

        # Flush content before creating any public directory entry.
        os.fsync(candidate_fd)
        _require_pinned_directory(candidate.parent, root_fd, private=True)
        _require_pinned_directory(output.parent, parent_fd)
        named_candidate = _entry_stat(candidate.name, root_fd)
        if named_candidate is None or not _same_inode(
            pinned_candidate, named_candidate
        ):
            raise PackageError("DMG candidate pathname changed before link")
        _require_safe_candidate(os.fstat(candidate_fd), expected_identity, expected_size)

        try:
            os.link(
                candidate.name,
                output.name,
                src_dir_fd=root_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            # A real link(2) EEXIST proves this call did not create the entry.
            # It can even be a racing hardlink to the candidate itself, so
            # inode equality alone is not authority to remove it.
            link_syscall_rejected = True
            raise PackageError("refusing to overwrite existing DMG output") from exc
        except OSError as exc:
            link_syscall_rejected = True
            raise PackageError(
                "failed to atomically place DMG output: {}".format(exc)
            ) from exc

        output_fd = os.open(
            output.name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        published = os.fstat(output_fd)
        candidate_after_link = os.fstat(candidate_fd)
        if (
            not stat.S_ISREG(published.st_mode)
            or (published.st_dev, published.st_ino) != tuple(expected_identity)
            or published.st_size != expected_size
            or published.st_nlink != 2
            or not _same_inode(published, candidate_after_link)
            or candidate_after_link.st_nlink != 2
            or _sha256_fd(output_fd) != expected_sha256
        ):
            raise PackageError("published DMG does not match accepted candidate")

        # The final name is durable before the private backing name is removed.
        _require_pinned_directory(candidate.parent, root_fd, private=True)
        _require_pinned_directory(output.parent, parent_fd)
        os.fsync(parent_fd)
        _require_pinned_directory(candidate.parent, root_fd, private=True)
        _require_pinned_directory(output.parent, parent_fd)
        published_name = _entry_stat(output.name, parent_fd)
        if published_name is None or not _same_inode(published, published_name):
            raise PackageError("DMG output pathname changed before commit")
        committed = True

        try:
            candidate_name = _entry_stat(candidate.name, root_fd)
            if candidate_name is None or not _same_inode(
                candidate_name, pinned_candidate
            ):
                raise PackageError("DMG candidate pathname changed during cleanup")
            os.unlink(candidate.name, dir_fd=root_fd)
            if _entry_stat(candidate.name, root_fd) is not None:
                raise PackageError("failed to unlink private DMG candidate")
            os.fsync(root_fd)
            _require_pinned_directory(candidate.parent, root_fd, private=True)
            _require_pinned_directory(output.parent, parent_fd)
            final_stat = os.fstat(output_fd)
            final_name = _entry_stat(output.name, parent_fd)
            if (
                final_name is None
                or not _same_inode(final_stat, final_name)
                or (final_stat.st_dev, final_stat.st_ino)
                != tuple(expected_identity)
                or final_stat.st_nlink != 1
                or final_stat.st_size != expected_size
                or _sha256_fd(output_fd) != expected_sha256
            ):
                raise PackageError("final DMG inode changed after candidate cleanup")
            return final_stat
        except BaseException as exc:
            raise CommittedPublishError(
                "DMG output is durably committed, but private candidate cleanup "
                "did not complete: {!r}".format(exc),
                expected_identity,
            ) from exc
    except BaseException as original_error:
        if committed or isinstance(original_error, CommittedPublishError):
            raise
        rollback_error = None
        if parent_fd is not None and not link_syscall_rejected:
            try:
                _rollback_exact_output(parent_fd, output.name, expected_identity)
            except BaseException as exc:
                rollback_error = exc
        if rollback_error is not None:
            raise PackageError(
                "DMG publication failed and exact-inode rollback also failed: "
                "original={!r}; rollback={!r}".format(
                    original_error, rollback_error
                )
            ) from original_error
        raise
    finally:
        for descriptor in (output_fd, candidate_fd, parent_fd, root_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


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
    report = None
    temporary_manager = tempfile.TemporaryDirectory(
        dir=str(output.parent), prefix=".focusbrowser-dmg-"
    )
    temporary_cleanup_warning = None
    publication_committed = False
    try:
        temporary_root = Path(temporary_manager.name)
        os.chmod(str(temporary_root), 0o700)
        try:
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
            candidate_stat = os.lstat(str(candidate))
            candidate_identity = (candidate_stat.st_dev, candidate_stat.st_ino)
            durable_publish_candidate(
                candidate,
                output,
                candidate_identity,
                size,
                digest,
            )
            publication_committed = True
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
        finally:
            try:
                temporary_manager.cleanup()
            except BaseException as exc:
                if not publication_committed or isinstance(
                    exc, (KeyboardInterrupt, SystemExit)
                ):
                    raise
                temporary_cleanup_warning = repr(exc)
    except BaseException:
        # durable_publish_candidate owns all pre-commit exact-inode rollback.
        # Once committed, no later cleanup or reporting failure may remove the
        # accepted final inode.
        raise
    report["temporary_cleanup_complete"] = temporary_cleanup_warning is None
    if temporary_cleanup_warning is not None:
        report["temporary_cleanup_warning"] = temporary_cleanup_warning
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
