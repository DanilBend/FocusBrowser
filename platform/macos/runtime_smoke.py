#!/usr/bin/env python3
"""Fail-closed runtime acceptance for the local universal macOS build."""

import errno
import hashlib
import http.server
import os
import platform
import plistlib
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path


MACOS_DIR = Path(__file__).resolve().parent
if str(MACOS_DIR) not in sys.path:
    sys.path.insert(0, str(MACOS_DIR))

import autoupdate_contract  # pylint: disable=wrong-import-position


APP_NAME = "Focus Browser.app"
BUNDLE_ID = "com.focusbrowser.browser"
FRAMEWORK_NAME = "Focus Browser Framework.framework"
ARCHITECTURES = ("arm64", "x86_64")
UPDATE_MODES = ("manual", "autoupdate")
ARCH = "/usr/bin/arch"
CODESIGN = "/usr/bin/codesign"
HDIUTIL = "/usr/bin/hdiutil"
LIPO = "/usr/bin/lipo"
TRUE = "/usr/bin/true"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
DEFAULT_TIMEOUT_SECONDS = 60
TOOL_TIMEOUT_SECONDS = 30
PROCESS_GRACE_SECONDS = 3
MAX_LOG_BYTES = 8 * 1024 * 1024
CONTROLLED_BROWSER_EXIT_CODES = frozenset((0, 128 + signal.SIGINT))
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
JIT_LOADER_ENTITLEMENTS = {
    ALLOW_JIT: True,
    DISABLE_LIBRARY_VALIDATION: True,
}
LIBRARY_LOADING_ENTITLEMENTS = {DISABLE_LIBRARY_VALIDATION: True}

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

PROHIBITED_APPLICATION_UPDATE_PLIST_PREFIXES = ("KS", "SU")
PROHIBITED_APPLICATION_UPDATE_ARTIFACT_NAMES = frozenset(
    {
        "autoupdate.app",
        "googleupdater.app",
        "googlesoftwareupdate.bundle",
        "googlesoftwareupdateagent",
        "googlesoftwareupdateagent.app",
        "keystone.bundle",
        "ksadmin",
        "ksinstall",
        "sparkle.framework",
    }
)

RUNTIME_ARGUMENTS = (
    "--headless",
    "--incognito",
    "--disable-gpu",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-pings",
    "--use-mock-keychain",
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
)
INCOGNITO_VERIFICATION_ARGUMENTS = tuple(
    argument for argument in RUNTIME_ARGUMENTS if argument != "--incognito"
)


class RuntimeSmokeError(RuntimeError):
    """Raised when a signed app or mounted DMG fails runtime acceptance."""


class DmgDetachError(RuntimeSmokeError):
    """Raised when a mounted final DMG cannot be proven detached."""

    def __init__(self, message, mountpoint=None, retained_root=None):
        self.mountpoint = str(mountpoint) if mountpoint is not None else None
        self.retained_root = (
            str(retained_root) if retained_root is not None else None
        )
        details = []
        if self.mountpoint is not None:
            details.append("retained mountpoint={}".format(self.mountpoint))
        if self.retained_root is not None:
            details.append("retained root={}".format(self.retained_root))
        if details:
            message = "{}; {}".format(message, "; ".join(details))
        super().__init__(message)


def _require_command(command):
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item for item in command)
    ):
        raise RuntimeSmokeError("command must be a non-empty argv list")


def _drain_selected_output(selector, events, values, label):
    """Drain every currently readable descriptor under the exact byte cap."""
    for key, _ in events:
        stream_name = key.data
        value = values[stream_name]
        while True:
            maximum_read = min(64 * 1024, MAX_LOG_BYTES + 1 - len(value))
            try:
                chunk = os.read(key.fileobj.fileno(), max(1, maximum_read))
            except InterruptedError:
                continue
            except BlockingIOError:
                break
            if not chunk:
                selector.unregister(key.fileobj)
                break
            value.extend(chunk)
            if len(value) > MAX_LOG_BYTES:
                raise RuntimeSmokeError(
                    "{} {} exceeded the bounded log limit".format(
                        label, stream_name
                    )
                )


def _drain_ready_output(selector, values, label):
    while selector.get_map():
        events = selector.select(0)
        if not events:
            break
        _drain_selected_output(selector, events, values, label)


def _collect_bounded_output(process, timeout_seconds, label):
    """Drain both child pipes without ever retaining more than the hard cap."""
    deadline = time.monotonic() + timeout_seconds
    values = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    try:
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is None:
                raise RuntimeSmokeError("{} has no {} pipe".format(label, stream_name))
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, stream_name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeSmokeError(
                    "{} timed out after {} seconds".format(label, timeout_seconds)
                )
            returncode = process.poll()
            if returncode is not None:
                # Sparkle/XPC descendants may inherit these descriptors after
                # the browser itself has exited. Drain everything already
                # available from the primary process, but do not require EOF
                # from unrelated descendants before accepting its exit code.
                events = selector.select(0)
                if not events:
                    break
            else:
                events = selector.select(min(remaining, 0.1))
            _drain_selected_output(selector, events, values, label)
        returncode = process.poll()
        if returncode is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeSmokeError(
                    "{} timed out after {} seconds".format(label, timeout_seconds)
                )
            try:
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeSmokeError(
                    "{} timed out after {} seconds".format(label, timeout_seconds)
                ) from exc
    finally:
        selector.close()
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is not None:
                stream.close()
    return bytes(values["stdout"]), bytes(values["stderr"]), returncode


def _execute_bounded(
    command, timeout_seconds, environment, label, pass_fds=()
):
    """Launch a new process group and preserve the primary bounded-I/O error."""
    _require_command(command)
    try:
        process = subprocess.Popen(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            bufsize=0,
            start_new_session=True,
            pass_fds=tuple(pass_fds),
        )
    except OSError as exc:
        raise RuntimeSmokeError("failed to launch {}: {}".format(label, exc)) from exc
    primary_error = None
    result = None
    try:
        result = _collect_bounded_output(process, timeout_seconds, label)
    except BaseException as exc:
        primary_error = exc
    try:
        _clean_process_group(process)
    except BaseException as cleanup_error:
        if primary_error is not None:
            raise RuntimeSmokeError(
                "{}; process-group cleanup also failed: {!r}".format(
                    primary_error, cleanup_error
                )
            ) from primary_error
        raise
    if primary_error is not None:
        raise primary_error
    return result


def _run_capture(
    command,
    timeout_seconds=TOOL_TIMEOUT_SECONDS,
    environment=None,
    pass_fds=(),
):
    """Run one tool with hard in-flight stdout/stderr and time bounds."""
    _require_command(command)
    stdout, stderr, returncode = _execute_bounded(
        command,
        timeout_seconds,
        environment,
        "command {}".format(" ".join(command)),
        pass_fds=pass_fds,
    )
    if returncode:
        detail = (stderr or stdout)[-4096:].decode("utf-8", errors="replace").strip()
        raise RuntimeSmokeError(
            "command failed ({}): {}\n{}".format(
                returncode, " ".join(command), detail or "no output"
            )
        )
    return stdout, stderr


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_fd(descriptor):
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def _stat_snapshot(value):
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


def _stable_dmg_snapshot(value):
    """Identity/metadata which an intentional hard-link cannot change."""
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        getattr(value, "st_flags", 0),
    )


def _require_private_runtime_root(path, expected_identity):
    observed = os.lstat(str(path))
    if (
        not stat.S_ISDIR(observed.st_mode)
        or stat.S_ISLNK(observed.st_mode)
        or (observed.st_dev, observed.st_ino) != expected_identity
        or stat.S_IMODE(observed.st_mode) != 0o700
        or observed.st_uid != os.geteuid()
    ):
        raise RuntimeSmokeError("DMG temporary root is not owner-only and identity-bound")
    return observed


def _copy_descriptor_exact(source_descriptor, destination_descriptor, size):
    offset = 0
    while offset < size:
        block = os.pread(source_descriptor, min(1024 * 1024, size - offset), offset)
        if not block:
            raise RuntimeSmokeError("runtime DMG private copy ended early")
        written_offset = 0
        while written_offset < len(block):
            written = os.write(destination_descriptor, block[written_offset:])
            if written <= 0:
                raise RuntimeSmokeError("runtime DMG private copy write was short")
            written_offset += written
        offset += len(block)
    if os.pread(source_descriptor, 1, size):
        raise RuntimeSmokeError("runtime DMG changed size during private copy")


def _private_dmg_mount_input(
    dmg, descriptor, before, before_digest, temporary_root, root_identity
):
    """Create one descriptor-verified pathname which hdiutil can consume."""
    _require_private_runtime_root(temporary_root, root_identity)
    named = os.lstat(str(dmg))
    current = os.fstat(descriptor)
    if (
        _stat_snapshot(current) != _stat_snapshot(before)
        or _stat_snapshot(named) != _stat_snapshot(before)
        or _sha256_fd(descriptor) != before_digest
    ):
        raise RuntimeSmokeError("runtime DMG changed before private mount input")
    path = temporary_root / (
        ".focus-runtime-input-{}.dmg".format(secrets.token_hex(24))
    )
    if os.path.lexists(str(path)):
        raise RuntimeSmokeError("runtime DMG private mount input already exists")
    input_descriptor = None
    created = False
    mode = "hardlink"
    hardlink_error = None
    try:
        try:
            os.link(str(dmg), str(path), follow_symlinks=False)
            created = True
            input_descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            fallback_errors = {
                errno.EACCES,
                errno.EMLINK,
                errno.EPERM,
                errno.EROFS,
                errno.EXDEV,
                getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
                errno.EOPNOTSUPP,
            }
            if created or exc.errno not in fallback_errors:
                raise
            hardlink_error = exc.errno
            mode = "verified-private-copy"
            input_descriptor = os.open(
                str(path),
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
            )
            created = True
            _copy_descriptor_exact(descriptor, input_descriptor, before.st_size)
            os.fchmod(input_descriptor, 0o400)
            os.fsync(input_descriptor)
        _require_private_runtime_root(temporary_root, root_identity)
        path_stat = os.lstat(str(path))
        input_stat = os.fstat(input_descriptor)
        current = os.fstat(descriptor)
        named = os.lstat(str(dmg))
        if (
            not stat.S_ISREG(input_stat.st_mode)
            or _stat_snapshot(path_stat) != _stat_snapshot(input_stat)
            or _stable_dmg_snapshot(current) != _stable_dmg_snapshot(before)
            or _stable_dmg_snapshot(named) != _stable_dmg_snapshot(before)
            or _sha256_fd(descriptor) != before_digest
            or _sha256_fd(input_descriptor) != before_digest
        ):
            raise RuntimeSmokeError("runtime DMG private mount input verification failed")
        if mode == "hardlink":
            if (
                _stat_snapshot(input_stat) != _stat_snapshot(current)
                or _stat_snapshot(named) != _stat_snapshot(current)
                or current.st_nlink != before.st_nlink + 1
            ):
                raise RuntimeSmokeError("runtime DMG private hardlink identity mismatch")
        elif (
            _stat_snapshot(current) != _stat_snapshot(before)
            or _stat_snapshot(named) != _stat_snapshot(before)
            or input_stat.st_nlink != 1
            or stat.S_IMODE(input_stat.st_mode) != 0o400
            or input_stat.st_uid != os.geteuid()
        ):
            raise RuntimeSmokeError("runtime DMG verified private copy contract mismatch")
        return {
            "path": path,
            "descriptor": input_descriptor,
            "snapshot": input_stat,
            "mode": mode,
            "hardlink_errno": hardlink_error,
        }
    except BaseException:
        cleanup_stat = None
        if input_descriptor is not None:
            try:
                cleanup_stat = os.fstat(input_descriptor)
            except OSError:
                cleanup_stat = None
        elif mode == "hardlink":
            try:
                cleanup_stat = os.fstat(descriptor)
            except OSError:
                cleanup_stat = None
        if created:
            try:
                path_stat = os.lstat(str(path))
            except OSError:
                path_stat = None
            if (
                path_stat is not None
                and cleanup_stat is not None
                and (path_stat.st_dev, path_stat.st_ino)
                == (cleanup_stat.st_dev, cleanup_stat.st_ino)
            ):
                try:
                    os.unlink(str(path))
                except OSError:
                    pass
        if input_descriptor is not None:
            try:
                os.close(input_descriptor)
            except OSError:
                pass
        raise


def _verify_private_dmg_mount_input(
    mount_input,
    dmg,
    descriptor,
    before,
    before_digest,
    temporary_root,
    root_identity,
):
    _require_private_runtime_root(temporary_root, root_identity)
    path_stat = os.lstat(str(mount_input["path"]))
    input_stat = os.fstat(mount_input["descriptor"])
    current = os.fstat(descriptor)
    named = os.lstat(str(dmg))
    if (
        _stat_snapshot(path_stat) != _stat_snapshot(input_stat)
        or _stat_snapshot(input_stat) != _stat_snapshot(mount_input["snapshot"])
        or _sha256_fd(mount_input["descriptor"]) != before_digest
        or _sha256_fd(descriptor) != before_digest
        or _stable_dmg_snapshot(current) != _stable_dmg_snapshot(before)
        or _stable_dmg_snapshot(named) != _stable_dmg_snapshot(before)
    ):
        raise RuntimeSmokeError("runtime DMG private mount input changed")
    if mount_input["mode"] == "hardlink":
        if (
            _stat_snapshot(current) != _stat_snapshot(input_stat)
            or _stat_snapshot(named) != _stat_snapshot(input_stat)
            or current.st_nlink != before.st_nlink + 1
        ):
            raise RuntimeSmokeError("runtime DMG private hardlink changed")
    elif (
        _stat_snapshot(current) != _stat_snapshot(before)
        or _stat_snapshot(named) != _stat_snapshot(before)
    ):
        raise RuntimeSmokeError("runtime DMG changed while private copy was mounted")


def _unlink_exact_private_dmg_mount_input(
    mount_input, temporary_root, root_identity
):
    """Unlink only the exact inode opened for the private hdiutil pathname."""
    _require_private_runtime_root(temporary_root, root_identity)
    path = mount_input["path"]
    path_stat = os.lstat(str(path))
    input_stat = os.fstat(mount_input["descriptor"])
    expected = mount_input["snapshot"]
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or (path_stat.st_dev, path_stat.st_ino)
        != (expected.st_dev, expected.st_ino)
        or (input_stat.st_dev, input_stat.st_ino)
        != (expected.st_dev, expected.st_ino)
    ):
        raise RuntimeSmokeError("refusing to unlink replaced DMG private mount input")
    os.unlink(str(path))
    if os.path.lexists(str(path)):
        raise RuntimeSmokeError("DMG private mount input survived exact unlink")


def _verify_final_runtime_dmg(
    dmg, descriptor, before, before_digest, mount_input_mode
):
    """Rebind the original inode after the private mount input is gone."""
    current = os.fstat(descriptor)
    named = os.lstat(str(dmg))
    digest = _sha256_fd(descriptor)
    if (
        _stable_dmg_snapshot(current) != _stable_dmg_snapshot(before)
        or _stable_dmg_snapshot(named) != _stable_dmg_snapshot(before)
        or current.st_nlink != before.st_nlink
        or named.st_nlink != before.st_nlink
        or digest != before_digest
    ):
        raise RuntimeSmokeError("final DMG changed during mounted runtime acceptance")
    if mount_input_mode != "hardlink" and (
        _stat_snapshot(current) != _stat_snapshot(before)
        or _stat_snapshot(named) != _stat_snapshot(before)
    ):
        raise RuntimeSmokeError("final DMG metadata changed during private-copy acceptance")
    return digest


def _validate_manual_update_only_bundle(app, info):
    """Reject application-level updater metadata and bundled updater tools."""
    prohibited_keys = sorted(
        key
        for key in info
        if isinstance(key, str)
        and key.startswith(PROHIBITED_APPLICATION_UPDATE_PLIST_PREFIXES)
    )
    if prohibited_keys:
        raise RuntimeSmokeError(
            "manual-update-only app contains prohibited Info.plist keys: {}".format(
                ", ".join(prohibited_keys)
            )
        )

    def fail_walk(error):
        raise RuntimeSmokeError(
            "cannot inspect manual-update-only app bundle: {}".format(error)
        ) from error

    for root, directories, files in os.walk(
        str(app), topdown=True, onerror=fail_walk, followlinks=False
    ):
        directories.sort()
        files.sort()
        for name in directories + files:
            folded = name.casefold()
            if (
                folded in PROHIBITED_APPLICATION_UPDATE_ARTIFACT_NAMES
                or folded.endswith("updaterprivilegedhelper")
            ):
                path = Path(root) / name
                raise RuntimeSmokeError(
                    "manual-update-only app contains prohibited updater artifact: {}".format(
                        path.relative_to(app).as_posix()
                    )
                )


def _read_app(app_value, update_mode="manual", sparkle_source_root=None):
    if update_mode not in UPDATE_MODES:
        raise RuntimeSmokeError(
            "update mode must be one of {}".format(", ".join(UPDATE_MODES))
        )
    candidate = Path(app_value).expanduser()
    if candidate.name != APP_NAME:
        raise RuntimeSmokeError("app must be named exactly {!r}".format(APP_NAME))
    if candidate.is_symlink():
        raise RuntimeSmokeError("app path must not be a symlink")
    try:
        app = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeSmokeError("app does not exist: {}".format(candidate)) from exc
    if app.name != APP_NAME or app.is_symlink() or not app.is_dir():
        raise RuntimeSmokeError("app must be a real directory")
    info_path = app / "Contents" / "Info.plist"
    if info_path.is_symlink() or not info_path.is_file():
        raise RuntimeSmokeError("app has no regular Info.plist")
    try:
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise RuntimeSmokeError("app Info.plist is invalid") from exc
    if not isinstance(info, dict) or info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise RuntimeSmokeError("unexpected app bundle identifier")
    if update_mode == "manual":
        if sparkle_source_root is not None:
            raise RuntimeSmokeError(
                "Sparkle provenance is unavailable in manual update mode"
            )
        _validate_manual_update_only_bundle(app, info)
    else:
        if sparkle_source_root is None:
            raise RuntimeSmokeError(
                "automatic-update runtime requires pinned Sparkle provenance"
            )
        try:
            update_report = autoupdate_contract.validate_release_bundle(
                app,
                sparkle_source_root,
            )
        except autoupdate_contract.AutoupdateContractError as exc:
            raise RuntimeSmokeError(
                "automatic-update app contract failed: {}".format(exc)
            ) from exc
        if not isinstance(update_report, dict) or update_report.get("passed") is not True:
            raise RuntimeSmokeError(
                "automatic-update app contract did not return a passing report"
            )
    executable_name = info.get("CFBundleExecutable")
    if (
        not isinstance(executable_name, str)
        or not executable_name
        or Path(executable_name).name != executable_name
        or "/" in executable_name
        or "\\" in executable_name
    ):
        raise RuntimeSmokeError("unsafe CFBundleExecutable")
    executable = app / "Contents" / "MacOS" / executable_name
    if executable.is_symlink() or not executable.is_file():
        raise RuntimeSmokeError("app executable is not a regular file")
    stdout, _ = _run_capture([LIPO, "-archs", str(executable)])
    tokens = stdout.decode("utf-8", errors="strict").split()
    if len(tokens) != len(set(tokens)) or set(tokens) != set(ARCHITECTURES):
        raise RuntimeSmokeError(
            "runtime acceptance requires a universal arm64+x86_64 app"
        )
    return app, executable


def _framework_version(app):
    framework = app / "Contents" / "Frameworks" / FRAMEWORK_NAME
    versions = framework / "Versions"
    if framework.is_symlink() or not framework.is_dir():
        raise RuntimeSmokeError("Focus Browser Framework is missing")
    if versions.is_symlink() or not versions.is_dir():
        raise RuntimeSmokeError("Focus Browser Framework Versions is missing")
    real_versions = sorted(
        child
        for child in versions.iterdir()
        if child.name != "Current" and child.is_dir() and not child.is_symlink()
    )
    if len(real_versions) != 1:
        raise RuntimeSmokeError("Focus Browser Framework must have one real version")
    return framework, real_versions[0]


def _signing_inventory(app):
    framework, version = _framework_version(app)
    helpers = version / "Helpers"
    libraries = version / "Libraries"
    if helpers.is_symlink() or not helpers.is_dir():
        raise RuntimeSmokeError("Framework Helpers directory is missing")
    if libraries.is_symlink() or not libraries.is_dir():
        raise RuntimeSmokeError("Framework Libraries directory is missing")
    loaders = {
        "app": app,
        "helper-app": helpers / "Focus Browser Helper.app",
        "helper-renderer-app": helpers / "Focus Browser Helper (Renderer).app",
        "helper-gpu-app": helpers / "Focus Browser Helper (GPU).app",
        "helper-alerts": helpers / "Focus Browser Helper (Alerts).app",
        "app-mode-app": helpers / "app_mode_loader",
        "web-app-shortcut-copier": helpers / "web_app_shortcut_copier",
    }
    crashpad = helpers / "chrome_crashpad_handler"
    app_bundles = {
        "app",
        "helper-app",
        "helper-renderer-app",
        "helper-gpu-app",
        "helper-alerts",
    }
    for label, path in tuple(loaders.items()) + (("crashpad", crashpad),):
        if path.is_symlink() or not path.exists():
            raise RuntimeSmokeError("missing signed product {}: {}".format(label, path))
        if label in app_bundles:
            if not path.is_dir():
                raise RuntimeSmokeError("signed app product is not a directory: {}".format(path))
        elif not path.is_file():
            raise RuntimeSmokeError("signed executable product is not regular: {}".format(path))
    dylibs = sorted(libraries.glob("*.dylib"), key=lambda path: path.name)
    required_dylibs = {"libEGL.dylib", "libGLESv2.dylib"}
    if not required_dylibs.issubset({path.name for path in dylibs}):
        raise RuntimeSmokeError("required ANGLE dylibs are missing")
    for path in dylibs:
        if path.is_symlink() or not path.is_file():
            raise RuntimeSmokeError("signed dylib is not regular: {}".format(path))
    protected = {"framework": framework, "crashpad": crashpad}
    protected.update({"dylib:" + path.name: path for path in dylibs})
    launch_services = app / "Contents" / "Library" / "LaunchServices"
    if launch_services.exists():
        if launch_services.is_symlink() or not launch_services.is_dir():
            raise RuntimeSmokeError("unsafe LaunchServices directory")
        privileged = sorted(
            child
            for child in launch_services.iterdir()
            if child.name.casefold().endswith("updaterprivilegedhelper")
        )
        if privileged:
            raise RuntimeSmokeError(
                "manual-update-only app contains an updater privileged helper"
            )
    return loaders, protected


_FLAGS_PATTERN = re.compile(
    r"^CodeDirectory\b[^\n]*\bflags=0x[0-9a-fA-F]+\(([^)]*)\)",
    flags=re.MULTILINE,
)


def _codesign_state(path, architecture):
    _, detail_bytes = _run_capture(
        [CODESIGN, "-d", "--arch", architecture, "--verbose=4", str(path)]
    )
    detail = detail_bytes.decode("utf-8", errors="replace")
    matches = _FLAGS_PATTERN.findall(detail)
    if len(matches) != 1:
        raise RuntimeSmokeError(
            "codesign did not report one semantic flag set for {} ({})".format(
                path, architecture
            )
        )
    flags = frozenset(
        value.strip() for value in matches[0].split(",") if value.strip()
    )
    if "Signature=adhoc" not in detail.splitlines():
        raise RuntimeSmokeError("signed product is not ad-hoc: {}".format(path))
    if "TeamIdentifier=not set" not in detail.splitlines():
        raise RuntimeSmokeError("ad-hoc product unexpectedly has a Team ID: {}".format(path))
    entitlements_bytes, _ = _run_capture(
        [
            CODESIGN,
            "-d",
            "--arch",
            architecture,
            "--entitlements",
            "-",
            "--xml",
            str(path),
        ]
    )
    if entitlements_bytes:
        try:
            entitlements = plistlib.loads(entitlements_bytes)
        except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
            raise RuntimeSmokeError("codesign emitted invalid entitlements") from exc
        if not isinstance(entitlements, dict):
            raise RuntimeSmokeError("codesign entitlements root is not a dictionary")
    else:
        entitlements = {}
    return flags, entitlements


def validate_adhoc_signing_matrix(
    app_value,
    update_mode="manual",
    sparkle_source_root=None,
):
    """Verify the complete per-product flags and entitlement matrix."""
    app, _ = _read_app(
        app_value,
        update_mode=update_mode,
        sparkle_source_root=sparkle_source_root,
    )
    loaders, protected = _signing_inventory(app)
    report = {"app": str(app), "identity": "adhoc", "products": {}}
    expected_loader_entitlements = {
        "app": APP_ENTITLEMENTS,
        "helper-app": LIBRARY_LOADING_ENTITLEMENTS,
        "helper-renderer-app": JIT_LOADER_ENTITLEMENTS,
        "helper-gpu-app": JIT_LOADER_ENTITLEMENTS,
        "helper-alerts": LIBRARY_LOADING_ENTITLEMENTS,
        "app-mode-app": LIBRARY_LOADING_ENTITLEMENTS,
        "web-app-shortcut-copier": LIBRARY_LOADING_ENTITLEMENTS,
    }
    for label, path in loaders.items():
        relative = "." if path == app else path.relative_to(app).as_posix()
        product = {"relative_path": relative, "architectures": {}}
        for architecture in ARCHITECTURES:
            flags, entitlements = _codesign_state(path, architecture)
            if flags != LOADER_FLAGS:
                raise RuntimeSmokeError(
                    "{} {} flags mismatch: expected {}, got {}".format(
                        label, architecture, sorted(LOADER_FLAGS), sorted(flags)
                    )
                )
            expected_entitlements = expected_loader_entitlements[label]
            if entitlements != expected_entitlements or any(
                value is not True for value in entitlements.values()
            ):
                raise RuntimeSmokeError(
                    "{} {} entitlements mismatch: expected {}, got {}".format(
                        label,
                        architecture,
                        sorted(expected_entitlements),
                        sorted(entitlements),
                    )
                )
            product["architectures"][architecture] = {
                "flags": sorted(flags),
                "disable_library_validation": True,
                "entitlement_keys": sorted(entitlements),
                "entitlements": entitlements,
            }
        report["products"][label] = product
    for label, path in protected.items():
        relative = path.relative_to(app).as_posix()
        expected_flags = (
            FULL_RUNTIME_FLAGS
            if label == "crashpad"
            else DATA_ONLY_FLAGS
        )
        product = {"relative_path": relative, "architectures": {}}
        for architecture in ARCHITECTURES:
            flags, entitlements = _codesign_state(path, architecture)
            if flags != expected_flags:
                raise RuntimeSmokeError(
                    "{} {} flags mismatch: expected {}, got {}".format(
                        label, architecture, sorted(expected_flags), sorted(flags)
                    )
                )
            if entitlements != {}:
                raise RuntimeSmokeError(
                    "{} {} must have no entitlements, got {}".format(
                        label, architecture, sorted(entitlements)
                    )
                )
            product["architectures"][architecture] = {
                "flags": sorted(flags),
                "disable_library_validation": False,
                "entitlement_keys": sorted(entitlements),
                "entitlements": entitlements,
            }
        report["products"][label] = product
    if tuple(label for label in FRAMEWORK_LOADERS if label in loaders) != FRAMEWORK_LOADERS:
        raise RuntimeSmokeError("framework loader inventory is incomplete")
    report["framework_loaders"] = list(FRAMEWORK_LOADERS)
    report["architectures"] = list(ARCHITECTURES)
    report["passed"] = True
    return report


def _runtime_environment():
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "PATH": SYSTEM_PATH,
    }
    for name in ("HOME", "TMPDIR"):
        value = os.environ.get(name)
        path = Path(value) if value else None
        if (
            value
            and not any(ord(character) < 0x20 for character in value)
            and path.is_absolute()
            and path.is_dir()
            and not path.is_symlink()
        ):
            environment[name] = value
    for name in ("USER", "LOGNAME"):
        value = os.environ.get(name)
        if value and not any(ord(character) < 0x20 for character in value):
            environment[name] = value
    return environment


def _probe_architecture(architecture, environment):
    _run_capture(
        [ARCH, "-" + architecture, TRUE],
        timeout_seconds=TOOL_TIMEOUT_SECONDS,
        environment=environment,
    )


def _signal_group(process_group, value):
    try:
        os.killpg(process_group, value)
        return True
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise RuntimeSmokeError(
            "cannot clean runtime process group {}".format(process_group)
        ) from exc


def _clean_process_group(process):
    """Bound cleanup to the new session created for one browser invocation."""
    process_group = process.pid
    interrupted = _signal_group(process_group, signal.SIGINT)
    if process.poll() is None:
        try:
            process.wait(timeout=PROCESS_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    elif interrupted:
        time.sleep(0.05)
    _signal_group(process_group, signal.SIGKILL)
    if process.poll() is None:
        try:
            process.wait(timeout=PROCESS_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeSmokeError("runtime process group did not terminate") from exc


_INCOGNITO_PROBE_TEMPLATE = b"""<!doctype html>
<meta charset="utf-8"><title>FocusRuntimeIncognitoProbe</title>
<main id="focus-runtime-smoke">PROBE_NOT_RUN</main>
<script>
(async () => {
  const value = decodeURIComponent(location.hash.slice(1));
  const separator = value.indexOf(":");
  const action = value.slice(0, separator);
  const token = value.slice(separator + 1);
  const key = "focus-runtime-incognito-state";
  const output = document.getElementById("focus-runtime-smoke");
  if (action === "write") {
    localStorage.setItem(key, token);
    output.textContent = "WRITE_OK_" + token;
  } else if (action === "read" && localStorage.getItem(key) === null) {
    output.textContent = "ABSENT_" + token;
  } else {
    output.textContent = "LEAK_" + String(localStorage.getItem(key));
  }
  await fetch("__FOCUS_RESULT_PATH__", {
    method: "POST",
    cache: "no-store",
    credentials: "omit",
    headers: {"Content-Type": "text/plain;charset=US-ASCII"},
    body: output.textContent,
  });
})();
</script>
"""


class _LoopbackProbeHandler(http.server.BaseHTTPRequestHandler):
    """Serve only the fixed runtime probe from an exact loopback origin."""

    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        # A malformed local client must not pin this single-purpose server
        # thread and prevent bounded runtime cleanup.
        self.connection.settimeout(2.0)

    def _valid_request(self, expected_path):
        server = self.server
        parsed = urllib.parse.urlsplit(self.path)
        expected_host = "127.0.0.1:{}".format(server.server_address[1])
        return not (
            self.client_address[0] != "127.0.0.1"
            or parsed.scheme
            or parsed.netloc
            or parsed.path != expected_path
            or parsed.query
            or parsed.fragment
            or self.headers.get_all("Host", failobj=[]) != [expected_host]
        )

    def _empty_response(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):  # pylint: disable=invalid-name
        server = self.server
        if not self._valid_request(server.probe_path):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        if not server.record_probe_load():
            self._empty_response(409)
            return
        payload = server.probe_payload
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # pylint: disable=invalid-name
        server = self.server
        lengths = self.headers.get_all("Content-Length", failobj=[])
        content_types = self.headers.get_all("Content-Type", failobj=[])
        if (
            not self._valid_request(server.result_path)
            or self.headers.get_all("Origin", failobj=[])
            != [server.expected_origin]
            or self.headers.get_all("Transfer-Encoding", failobj=[])
            or len(lengths) != 1
            or not lengths[0].isascii()
            or not lengths[0].isdigit()
            or len(content_types) != 1
            or content_types[0] != "text/plain;charset=US-ASCII"
        ):
            self._empty_response(404)
            return
        size = int(lengths[0], 10)
        if not 1 <= size <= 256:
            self._empty_response(413)
            return
        try:
            payload = self.rfile.read(size)
        except (OSError, TimeoutError):
            self.close_connection = True
            return
        if len(payload) != size or not server.record_result(payload):
            self._empty_response(409)
            return
        self._empty_response(204)

    def log_message(self, _format, *_args):
        return


class _LoopbackProbeServer:
    """Bounded context for one LAN-inaccessible local HTTP probe."""

    def __init__(self):
        token = secrets.token_hex(24)
        self._lock = threading.Lock()
        self._result_event = threading.Event()
        self._expected_result = None
        self._received_result = None
        self._probe_loaded = False
        self._server = http.server.HTTPServer(
            ("127.0.0.1", 0), _LoopbackProbeHandler
        )
        self._server.probe_path = "/focus-runtime-probe-{}.html".format(token)
        self._server.result_path = "/focus-runtime-result-{}".format(token)
        result_path = self._server.result_path.encode("ascii")
        if _INCOGNITO_PROBE_TEMPLATE.count(b"__FOCUS_RESULT_PATH__") != 1:
            self._server.server_close()
            raise RuntimeSmokeError("runtime probe result placeholder mismatch")
        self._server.probe_payload = _INCOGNITO_PROBE_TEMPLATE.replace(
            b"__FOCUS_RESULT_PATH__", result_path
        )
        self._server.record_result = self._record_result
        self._server.record_probe_load = self._record_probe_load
        address, port = self._server.server_address
        if address != "127.0.0.1" or not isinstance(port, int) or not port:
            self._server.server_close()
            raise RuntimeSmokeError("runtime probe did not bind exact loopback")
        self.url = "http://127.0.0.1:{}{}".format(
            port, self._server.probe_path
        )
        self._server.expected_origin = "http://127.0.0.1:{}".format(port)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="focus-runtime-loopback-probe",
        )

    def __enter__(self):
        self._thread.start()
        if not self._thread.is_alive():
            self._server.server_close()
            raise RuntimeSmokeError("runtime loopback probe did not start")
        return self

    def prepare_result(self, expected):
        if (
            not isinstance(expected, bytes)
            or not 1 <= len(expected) <= 256
            or not expected.isascii()
        ):
            raise RuntimeSmokeError("runtime probe expectation is invalid")
        with self._lock:
            if self._expected_result is not None or self._received_result is not None:
                raise RuntimeSmokeError("runtime probe expectation is already active")
            self._result_event.clear()
            self._expected_result = expected
            self._probe_loaded = False

    def _record_probe_load(self):
        with self._lock:
            if self._expected_result is None or self._received_result is not None:
                return False
            self._probe_loaded = True
            return True

    def _record_result(self, payload):
        with self._lock:
            if (
                self._expected_result is None
                or self._received_result is not None
                or self._probe_loaded is not True
                or payload != self._expected_result
            ):
                return False
            self._received_result = payload
            self._result_event.set()
            return True

    def result_ready(self, expected):
        if not self._result_event.is_set():
            return False
        with self._lock:
            return (
                self._expected_result == expected
                and self._received_result == expected
            )

    def consume_result(self, expected):
        with self._lock:
            if (
                self._expected_result != expected
                or self._received_result != expected
            ):
                raise RuntimeSmokeError("runtime probe result mismatch")
            self._expected_result = None
            self._received_result = None
            self._probe_loaded = False
            self._result_event.clear()

    def cancel_result(self, expected):
        with self._lock:
            if self._expected_result == expected:
                self._expected_result = None
                self._received_result = None
                self._probe_loaded = False
                self._result_event.clear()

    def __exit__(self, exception_type, exception, traceback):
        cleanup_error = None
        try:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeSmokeError("runtime loopback probe did not stop")
        except BaseException as exc:  # pragma: no cover - defensive cleanup
            cleanup_error = exc
        if cleanup_error is not None:
            if exception is not None:
                raise RuntimeSmokeError(
                    "{}; loopback cleanup also failed: {!r}".format(
                        exception, cleanup_error
                    )
                ) from exception
            raise cleanup_error
        with self._lock:
            if self._expected_result is not None or self._received_result is not None:
                raise RuntimeSmokeError("runtime probe expectation leaked")
        return False


def _runtime_command(
    executable, architecture, profile, arguments, probe_url
):
    return [
        ARCH,
        "-" + architecture,
        str(executable),
        *arguments,
        "--user-data-dir={}".format(profile),
        probe_url,
    ]


def _execute_browser_probe(
    command,
    expected_result,
    probe_server,
    timeout_seconds,
    environment,
    label,
):
    """Run the long-lived browser until its isolated loopback proof arrives."""
    _require_command(command)
    probe_server.prepare_result(expected_result)
    process = None
    selector = selectors.DefaultSelector()
    values = {"stdout": bytearray(), "stderr": bytearray()}
    primary_error = None
    cleanup_error = None
    consumed = False
    try:
        process = subprocess.Popen(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            bufsize=0,
            start_new_session=True,
        )
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, stream_name)
        deadline = time.monotonic() + timeout_seconds
        while True:
            if probe_server.result_ready(expected_result):
                probe_server.consume_result(expected_result)
                consumed = True
                break
            returncode = process.poll()
            if returncode is not None:
                _drain_ready_output(selector, values, label)
                if probe_server.result_ready(expected_result):
                    probe_server.consume_result(expected_result)
                    consumed = True
                    break
                raise RuntimeSmokeError(
                    "{} exited {} before the loopback proof".format(
                        label, returncode
                    )
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeSmokeError(
                    "{} timed out after {} seconds".format(
                        label, timeout_seconds
                    )
                )
            events = selector.select(min(remaining, 0.05))
            _drain_selected_output(selector, events, values, label)
    except BaseException as exc:
        primary_error = exc
    finally:
        if not consumed:
            probe_server.cancel_result(expected_result)
        if process is not None:
            try:
                _clean_process_group(process)
                _drain_ready_output(selector, values, label)
            except BaseException as exc:
                cleanup_error = exc
        selector.close()
        if process is not None:
            for stream_name in ("stdout", "stderr"):
                stream = getattr(process, stream_name, None)
                if stream is not None:
                    stream.close()
    if primary_error is not None:
        if cleanup_error is not None:
            raise RuntimeSmokeError(
                "{}; process-group cleanup also failed: {!r}".format(
                    primary_error, cleanup_error
                )
            ) from primary_error
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    if process is None or process.returncode not in CONTROLLED_BROWSER_EXIT_CODES:
        detail = bytes(values["stderr"])[-4096:].decode(
            "utf-8", errors="replace"
        ).strip()
        raise RuntimeSmokeError(
            "{} controlled exit failed ({}): {}".format(
                label,
                None if process is None else process.returncode,
                detail or "no stderr",
            )
        )
    return (
        bytes(values["stdout"]),
        bytes(values["stderr"]),
        process.returncode,
    )


def _run_browser(
    executable,
    architecture,
    profile,
    root,
    probe_server,
    timeout_seconds,
    environment,
):
    """Prove an incognito write does not persist into a normal second launch."""
    marker = "FOCUSBROWSER_{}_{}_OK".format(
        architecture.upper(), secrets.token_hex(12).upper()
    )
    base_url = probe_server.url
    parsed_base_url = urllib.parse.urlsplit(base_url)
    if (
        parsed_base_url.scheme != "http"
        or parsed_base_url.hostname != "127.0.0.1"
        or not parsed_base_url.port
        or parsed_base_url.query
        or parsed_base_url.fragment
    ):
        raise RuntimeSmokeError("runtime probe URL is not exact loopback HTTP")
    control_marker = "CONTROL_" + marker
    control_profile = root / (architecture + "-storage-control-profile")
    control_profile.mkdir(mode=0o700)
    control_write_command = _runtime_command(
        executable,
        architecture,
        control_profile,
        INCOGNITO_VERIFICATION_ARGUMENTS,
        base_url
        + "#"
        + urllib.parse.quote("write:" + control_marker, safe=":"),
    )
    control_read_command = _runtime_command(
        executable,
        architecture,
        control_profile,
        INCOGNITO_VERIFICATION_ARGUMENTS,
        base_url + "#" + urllib.parse.quote("read:" + control_marker, safe=":"),
    )
    write_url = base_url + "#" + urllib.parse.quote("write:" + marker, safe=":")
    read_url = base_url + "#" + urllib.parse.quote("read:" + marker, safe=":")
    command = _runtime_command(
        executable, architecture, profile, RUNTIME_ARGUMENTS, write_url
    )
    verification_command = _runtime_command(
        executable,
        architecture,
        profile,
        INCOGNITO_VERIFICATION_ARGUMENTS,
        read_url,
    )
    started = time.monotonic()
    control_write_token = ("WRITE_OK_" + control_marker).encode("ascii")
    control_write_stdout, control_write_stderr, control_write_returncode = (
        _execute_browser_probe(
            control_write_command,
            control_write_token,
            probe_server,
            timeout_seconds,
            environment,
            "{} normal storage-control write".format(architecture),
        )
    )
    if control_write_returncode not in CONTROLLED_BROWSER_EXIT_CODES:
        raise RuntimeSmokeError(
            "{} normal storage-control write did not persist".format(architecture)
        )
    control_read_token = ("LEAK_" + control_marker).encode("ascii")
    control_read_stdout, control_read_stderr, control_read_returncode = (
        _execute_browser_probe(
            control_read_command,
            control_read_token,
            probe_server,
            timeout_seconds,
            environment,
            "{} normal storage-control read".format(architecture),
        )
    )
    if control_read_returncode not in CONTROLLED_BROWSER_EXIT_CODES:
        raise RuntimeSmokeError(
            "{} normal storage-control persistence is unproven".format(
                architecture
            )
        )
    write_token = ("WRITE_OK_" + marker).encode("ascii")
    stdout_value, stderr_value, returncode = _execute_browser_probe(
        command,
        write_token,
        probe_server,
        timeout_seconds,
        environment,
        "{} incognito write smoke".format(architecture),
    )
    if returncode not in CONTROLLED_BROWSER_EXIT_CODES:
        detail = stderr_value[-4096:].decode("utf-8", errors="replace").strip()
        raise RuntimeSmokeError(
            "{} incognito write smoke exited {}: {}".format(
                architecture, returncode, detail or "no stderr"
            )
        )
    absent_token = ("ABSENT_" + marker).encode("ascii")
    verification_stdout, verification_stderr, verification_returncode = (
        _execute_browser_probe(
            verification_command,
            absent_token,
            probe_server,
            timeout_seconds,
            environment,
            "{} post-incognito storage smoke".format(architecture),
        )
    )
    duration = time.monotonic() - started
    if verification_returncode not in CONTROLLED_BROWSER_EXIT_CODES:
        detail = verification_stderr[-4096:].decode(
            "utf-8", errors="replace"
        ).strip()
        raise RuntimeSmokeError(
            "{} post-incognito storage smoke exited {}: {}".format(
                architecture,
                verification_returncode,
                detail or "no stderr",
            )
        )
    return {
        "architecture": architecture,
        "execution": "native" if architecture == "arm64" else "Rosetta",
        "exit_code": returncode,
        "verification_exit_code": verification_returncode,
        "storage_control_persistence_verified": True,
        "storage_control_write_exit_code": control_write_returncode,
        "storage_control_read_exit_code": control_read_returncode,
        "incognito": True,
        "incognito_storage_isolated": True,
        "incognito_proof": "incognito-write/normal-read localStorage beacon isolation",
        "offline_navigation": "loopback-http/localStorage-beacon",
        "marker": marker,
        "marker_observed": True,
        "fresh_profile": True,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": round(duration, 3),
        "stdout_sha256": hashlib.sha256(stdout_value).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_value).hexdigest(),
        "verification_stdout_sha256": hashlib.sha256(
            verification_stdout
        ).hexdigest(),
        "verification_stderr_sha256": hashlib.sha256(
            verification_stderr
        ).hexdigest(),
        "storage_control_sha256": hashlib.sha256(
            control_write_stdout
            + control_write_stderr
            + control_read_stdout
            + control_read_stderr
        ).hexdigest(),
        "network_disabling_arguments": list(RUNTIME_ARGUMENTS),
    }


def validate_universal_app_runtime(
    app_value,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    update_mode="manual",
    sparkle_source_root=None,
):
    """Launch both slices with fresh profiles and an offline incognito marker."""
    if not isinstance(timeout_seconds, int) or not 5 <= timeout_seconds <= 300:
        raise RuntimeSmokeError("runtime timeout must be an integer from 5 to 300")
    if platform.system() != "Darwin":
        raise RuntimeSmokeError("runtime acceptance requires macOS")
    machine = platform.machine().lower()
    if machine == "aarch64":
        machine = "arm64"
    if machine != "arm64":
        raise RuntimeSmokeError("runtime acceptance requires a native Apple Silicon host")
    app, executable = _read_app(
        app_value,
        update_mode=update_mode,
        sparkle_source_root=sparkle_source_root,
    )
    environment = _runtime_environment()
    _probe_architecture("arm64", environment)
    _probe_architecture("x86_64", environment)
    results = []
    with tempfile.TemporaryDirectory(prefix="focus-runtime-smoke-") as temporary:
        root = Path(temporary).resolve()
        with _LoopbackProbeServer() as probe_server:
            for architecture in ARCHITECTURES:
                profile = root / (architecture + "-profile")
                profile.mkdir(mode=0o700)
                results.append(
                    _run_browser(
                        executable,
                        architecture,
                        profile,
                        root,
                        probe_server,
                        timeout_seconds,
                        environment,
                    )
                )
    return {
        "app": str(app),
        "host_architecture": "arm64",
        "rosetta_required": True,
        "rosetta_available": True,
        "architectures": results,
        "passed": True,
    }


def _detach_mounted_final_dmg(mountpoint, retained_root):
    """Try normal then forced detach and require an unmounted-state probe."""
    attempts = []
    for forced in (False, True):
        command = [HDIUTIL, "detach"]
        if forced:
            command.append("-force")
        command.append(str(mountpoint))
        command_error = None
        try:
            _run_capture(command)
        except BaseException as exc:
            command_error = exc
        try:
            mounted = os.path.ismount(str(mountpoint))
        except BaseException as probe_error:
            attempts.append(
                "{} detach={!r}, mount probe={!r}".format(
                    "forced" if forced else "normal",
                    command_error,
                    probe_error,
                )
            )
            continue
        attempts.append(
            "{} detach={!r}, still_mounted={}".format(
                "forced" if forced else "normal", command_error, mounted
            )
        )
        if not mounted:
            return {
                "forced": forced,
                "command_succeeded": command_error is None,
                "mountpoint_unmounted": True,
            }
    raise DmgDetachError(
        "could not prove final DMG detached ({})".format("; ".join(attempts)),
        mountpoint=mountpoint,
        retained_root=retained_root,
    )


def _real_directory_identity(path, label):
    observed = os.lstat(str(path))
    if not stat.S_ISDIR(observed.st_mode) or stat.S_ISLNK(observed.st_mode):
        raise RuntimeSmokeError("{} is not a real directory".format(label))
    return observed.st_dev, observed.st_ino


def _remove_empty_detached_mount_root(
    temporary_root, root_identity, mountpoint, mountpoint_identity
):
    """Remove only the two exact empty directories created for this mount."""
    if mountpoint_identity is not None:
        if _real_directory_identity(mountpoint, "detached DMG mountpoint") != (
            mountpoint_identity
        ):
            raise RuntimeSmokeError("detached DMG mountpoint identity changed")
        try:
            os.rmdir(str(mountpoint))
        except OSError as exc:
            raise RuntimeSmokeError(
                "detached DMG mountpoint is not safely empty; retained at {}".format(
                    mountpoint
                )
            ) from exc
    if (
        _real_directory_identity(temporary_root, "DMG temporary root")
        != root_identity
    ):
        raise RuntimeSmokeError("DMG temporary root identity changed")
    try:
        os.rmdir(str(temporary_root))
    except OSError as exc:
        raise RuntimeSmokeError(
            "DMG temporary root is not safely empty; retained at {}".format(
                temporary_root
            )
        ) from exc


def _validate_mounted_dmg_runtime_descriptor(
    dmg,
    descriptor,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    update_mode="manual",
    sparkle_source_root=None,
):
    """Mount one descriptor-pinned DMG through a private verified pathname."""
    before = os.fstat(descriptor)
    named_before = os.lstat(str(dmg))
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or _stat_snapshot(before) != _stat_snapshot(named_before)
    ):
        raise RuntimeSmokeError("runtime DMG descriptor/path identity mismatch")
    before_digest = _sha256_fd(descriptor)
    ever_attached = False
    attach_attempted = False
    detach_proven = False
    primary_error = None
    detach_error = None
    runtime_report = None
    temporary_root = Path(tempfile.mkdtemp(prefix="focus-runtime-dmg-")).resolve()
    root_identity = _real_directory_identity(temporary_root, "DMG temporary root")
    _require_private_runtime_root(temporary_root, root_identity)
    mountpoint = temporary_root / "mounted"
    mountpoint_identity = None
    mount_input = None
    try:
        try:
            mountpoint.mkdir()
            mountpoint_identity = _real_directory_identity(
                mountpoint, "DMG mountpoint"
            )
            mount_input = _private_dmg_mount_input(
                dmg,
                descriptor,
                before,
                before_digest,
                temporary_root,
                root_identity,
            )
            _verify_private_dmg_mount_input(
                mount_input,
                dmg,
                descriptor,
                before,
                before_digest,
                temporary_root,
                root_identity,
            )
            attach_attempted = True
            try:
                _run_capture(
                    [
                        HDIUTIL,
                        "attach",
                        "-readonly",
                        "-nobrowse",
                        "-noautoopen",
                        "-mountpoint",
                        str(mountpoint),
                        str(mount_input["path"]),
                    ]
                )
                ever_attached = True
                if not os.path.ismount(str(mountpoint)):
                    raise RuntimeSmokeError("hdiutil did not mount the final DMG")
                if not (os.statvfs(str(mountpoint)).f_flag & os.ST_RDONLY):
                    raise RuntimeSmokeError("final DMG did not mount read-only")
                applications = mountpoint / "Applications"
                if (
                    not applications.is_symlink()
                    or os.readlink(str(applications)) != "/Applications"
                ):
                    raise RuntimeSmokeError(
                        "mounted final DMG has an invalid Applications link"
                    )
                runtime_report = validate_universal_app_runtime(
                    mountpoint / APP_NAME,
                    timeout_seconds=timeout_seconds,
                    update_mode=update_mode,
                    sparkle_source_root=sparkle_source_root,
                )
            except BaseException as exc:  # Detach remains mandatory on every failure.
                primary_error = exc
            finally:
                if attach_attempted:
                    try:
                        mounted = os.path.ismount(str(mountpoint))
                    except BaseException as probe_error:
                        detach_error = DmgDetachError(
                            "could not determine final DMG mount state: {!r}".format(
                                probe_error
                            ),
                            mountpoint=mountpoint,
                            retained_root=temporary_root,
                        )
                    else:
                        if ever_attached or mounted:
                            try:
                                _detach_mounted_final_dmg(
                                    mountpoint, temporary_root
                                )
                                detach_proven = True
                            except DmgDetachError as exc:
                                detach_error = exc
                        else:
                            detach_proven = True
                else:
                    detach_proven = True
        except BaseException as exc:
            if primary_error is None:
                primary_error = exc
            if not attach_attempted:
                detach_proven = True

        if primary_error is not None and detach_error is not None:
            raise DmgDetachError(
                "{}; additionally failed to detach final DMG: {}".format(
                    primary_error, detach_error
                ),
                mountpoint=detach_error.mountpoint or mountpoint,
                retained_root=detach_error.retained_root or temporary_root,
            ) from primary_error
        if detach_error is not None:
            raise detach_error
        if not detach_proven:
            raise DmgDetachError(
                "final DMG detach state is unproven",
                mountpoint=mountpoint,
                retained_root=temporary_root,
            )

        integrity_error = None
        cleanup_error = None
        if mount_input is not None:
            try:
                _verify_private_dmg_mount_input(
                    mount_input,
                    dmg,
                    descriptor,
                    before,
                    before_digest,
                    temporary_root,
                    root_identity,
                )
            except BaseException as exc:
                integrity_error = exc
            try:
                _unlink_exact_private_dmg_mount_input(
                    mount_input, temporary_root, root_identity
                )
            except BaseException as exc:
                cleanup_error = exc
        if cleanup_error is None:
            try:
                _remove_empty_detached_mount_root(
                    temporary_root,
                    root_identity,
                    mountpoint,
                    mountpoint_identity,
                )
            except BaseException as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            prior = primary_error or integrity_error
            if prior is not None:
                raise RuntimeSmokeError(
                    "{}; private DMG mount cleanup failed and root was retained at {}: {!r}".format(
                        prior, temporary_root, cleanup_error
                    )
                ) from prior
            raise RuntimeSmokeError(
                "private DMG mount cleanup failed and root was retained at {}: {!r}".format(
                    temporary_root, cleanup_error
                )
            ) from cleanup_error
        if primary_error is not None:
            raise primary_error
        if integrity_error is not None:
            raise integrity_error

        digest = _verify_final_runtime_dmg(
            dmg,
            descriptor,
            before,
            before_digest,
            mount_input["mode"],
        )
        return {
            "dmg": str(dmg),
            "size_bytes": before.st_size,
            "sha256": digest,
            "descriptor_pinned": True,
            "mounted_read_only": True,
            "runtime": runtime_report,
            "passed": True,
        }
    finally:
        if mount_input is not None:
            try:
                os.close(mount_input["descriptor"])
            except OSError:
                pass


def validate_mounted_dmg_runtime(
    dmg_value,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    update_mode="manual",
    sparkle_source_root=None,
):
    """Open once, then give hdiutil a descriptor-verified private pathname."""
    candidate = Path(dmg_value).expanduser()
    if candidate.suffix != ".dmg" or candidate.is_symlink():
        raise RuntimeSmokeError("runtime DMG must be a non-symlink .dmg path")
    try:
        dmg = candidate.resolve(strict=True)
        descriptor = os.open(str(dmg), os.O_RDONLY | os.O_NOFOLLOW)
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeSmokeError("cannot safely open runtime DMG: {}".format(candidate)) from exc
    try:
        return _validate_mounted_dmg_runtime_descriptor(
            dmg,
            descriptor,
            timeout_seconds=timeout_seconds,
            update_mode=update_mode,
            sparkle_source_root=sparkle_source_root,
        )
    finally:
        os.close(descriptor)
