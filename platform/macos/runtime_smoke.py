#!/usr/bin/env python3
"""Fail-closed runtime acceptance for the local universal macOS build."""

import hashlib
import os
import platform
import plistlib
import re
import secrets
import selectors
import signal
import stat
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path


APP_NAME = "Focus Browser.app"
BUNDLE_ID = "com.focusbrowser.browser"
FRAMEWORK_NAME = "Focus Browser Framework.framework"
ARCHITECTURES = ("arm64", "x86_64")
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
DISABLE_LIBRARY_VALIDATION = (
    "com.apple.security.cs.disable-library-validation"
)

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
    "--host-resolver-rules=MAP * ~NOTFOUND",
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
            events = selector.select(min(remaining, 0.1))
            for key, _ in events:
                stream_name = key.data
                value = values[stream_name]
                maximum_read = min(64 * 1024, MAX_LOG_BYTES + 1 - len(value))
                try:
                    chunk = os.read(key.fileobj.fileno(), max(1, maximum_read))
                except (BlockingIOError, InterruptedError):
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                value.extend(chunk)
                if len(value) > MAX_LOG_BYTES:
                    raise RuntimeSmokeError(
                        "{} {} exceeded the bounded log limit".format(
                            label, stream_name
                        )
                    )
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


def _execute_bounded(command, timeout_seconds, environment, label):
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


def _run_capture(command, timeout_seconds=TOOL_TIMEOUT_SECONDS, environment=None):
    """Run one tool with hard in-flight stdout/stderr and time bounds."""
    _require_command(command)
    stdout, stderr, returncode = _execute_bounded(
        command,
        timeout_seconds,
        environment,
        "command {}".format(" ".join(command)),
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


def _read_app(app_value):
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
        privileged = sorted(launch_services.glob("*UpdaterPrivilegedHelper"))
        if len(privileged) > 1:
            raise RuntimeSmokeError("multiple updater privileged helpers found")
        if privileged:
            helper = privileged[0]
            if helper.is_symlink() or not helper.is_file():
                raise RuntimeSmokeError("updater privileged helper is not regular")
            protected["privileged-helper"] = helper
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


def validate_adhoc_signing_matrix(app_value):
    """Verify each architecture's effective flags and LV entitlement policy."""
    app, _ = _read_app(app_value)
    loaders, protected = _signing_inventory(app)
    report = {"app": str(app), "identity": "adhoc", "products": {}}
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
            if entitlements.get(DISABLE_LIBRARY_VALIDATION) is not True:
                raise RuntimeSmokeError(
                    "{} {} does not explicitly disable Library Validation".format(
                        label, architecture
                    )
                )
            product["architectures"][architecture] = {
                "flags": sorted(flags),
                "disable_library_validation": True,
                "entitlement_keys": sorted(entitlements),
            }
        report["products"][label] = product
    for label, path in protected.items():
        relative = path.relative_to(app).as_posix()
        expected_flags = (
            FULL_RUNTIME_FLAGS
            if label in ("crashpad", "privileged-helper")
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
            if DISABLE_LIBRARY_VALIDATION in entitlements:
                raise RuntimeSmokeError(
                    "{} {} unexpectedly disables Library Validation".format(
                        label, architecture
                    )
                )
            product["architectures"][architecture] = {
                "flags": sorted(flags),
                "disable_library_validation": False,
                "entitlement_keys": sorted(entitlements),
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


def _run_browser(executable, architecture, profile, root, timeout_seconds, environment):
    marker = "FOCUSBROWSER_{}_{}_OK".format(
        architecture.upper(), secrets.token_hex(12).upper()
    )
    html = (
        "<!doctype html><html><head><title>FocusRuntimeSmoke</title></head>"
        '<body><main id="focus-runtime-smoke">{}</main></body></html>'.format(marker)
    )
    data_url = "data:text/html;charset=utf-8," + urllib.parse.quote(html, safe="")
    command = [ARCH, "-" + architecture, str(executable)]
    command.extend(RUNTIME_ARGUMENTS)
    command.extend(
        [
            "--user-data-dir={}".format(profile),
            "--dump-dom",
            data_url,
        ]
    )
    del root  # Output is drained through bounded pipes, never unbounded files.
    started = time.monotonic()
    stdout_value, stderr_value, returncode = _execute_bounded(
        command,
        timeout_seconds,
        environment,
        "{} runtime smoke".format(architecture),
    )
    duration = time.monotonic() - started
    if returncode != 0:
        detail = stderr_value[-4096:].decode("utf-8", errors="replace").strip()
        raise RuntimeSmokeError(
            "{} runtime smoke exited {}: {}".format(
                architecture, returncode, detail or "no stderr"
            )
        )
    marker_bytes = marker.encode("ascii")
    if stdout_value.count(marker_bytes) != 1:
        raise RuntimeSmokeError(
            "{} runtime smoke did not emit exactly one incognito marker".format(
                architecture
            )
        )
    return {
        "architecture": architecture,
        "execution": "native" if architecture == "arm64" else "Rosetta",
        "exit_code": returncode,
        "incognito": True,
        "offline_navigation": "data:text/html",
        "marker": marker,
        "marker_observed": True,
        "fresh_profile": True,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": round(duration, 3),
        "stdout_sha256": hashlib.sha256(stdout_value).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_value).hexdigest(),
        "network_disabling_arguments": list(RUNTIME_ARGUMENTS),
    }


def validate_universal_app_runtime(app_value, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
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
    app, executable = _read_app(app_value)
    environment = _runtime_environment()
    _probe_architecture("arm64", environment)
    _probe_architecture("x86_64", environment)
    results = []
    with tempfile.TemporaryDirectory(prefix="focus-runtime-smoke-") as temporary:
        root = Path(temporary).resolve()
        for architecture in ARCHITECTURES:
            profile = root / (architecture + "-profile")
            profile.mkdir(mode=0o700)
            results.append(
                _run_browser(
                    executable,
                    architecture,
                    profile,
                    root,
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


def validate_mounted_dmg_runtime(dmg_value, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    """Mount the exact candidate/final DMG and repeat both runtime launches."""
    candidate = Path(dmg_value).expanduser()
    if candidate.is_symlink():
        raise RuntimeSmokeError("runtime DMG path must not be a symlink")
    try:
        dmg = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeSmokeError("DMG does not exist: {}".format(candidate)) from exc
    if dmg.suffix != ".dmg" or dmg.is_symlink() or not dmg.is_file():
        raise RuntimeSmokeError("runtime DMG must be a regular .dmg file")
    before = os.lstat(str(dmg))
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise RuntimeSmokeError("runtime DMG is empty or not regular")
    before_digest = _sha256_file(dmg)
    ever_attached = False
    attach_attempted = False
    detach_proven = False
    primary_error = None
    detach_error = None
    runtime_report = None
    temporary_root = Path(tempfile.mkdtemp(prefix="focus-runtime-dmg-")).resolve()
    root_identity = _real_directory_identity(temporary_root, "DMG temporary root")
    mountpoint = temporary_root / "mounted"
    mountpoint_identity = None
    try:
        mountpoint.mkdir()
        mountpoint_identity = _real_directory_identity(
            mountpoint, "DMG mountpoint"
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
                    str(dmg),
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
                raise RuntimeSmokeError("mounted final DMG has an invalid Applications link")
            runtime_report = validate_universal_app_runtime(
                mountpoint / APP_NAME, timeout_seconds=timeout_seconds
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
    if primary_error is not None:
        if detach_error is not None:
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
    try:
        _remove_empty_detached_mount_root(
            temporary_root,
            root_identity,
            mountpoint,
            mountpoint_identity,
        )
    except BaseException as cleanup_error:
        if primary_error is not None:
            raise RuntimeSmokeError(
                "{}; detached mountpoint cleanup failed: {!r}".format(
                    primary_error, cleanup_error
                )
            ) from primary_error
        raise
    if primary_error is not None:
        raise primary_error
    digest = _sha256_file(dmg)
    after = os.lstat(str(dmg))
    if (
        (after.st_dev, after.st_ino, after.st_size)
        != (before.st_dev, before.st_ino, before.st_size)
        or (after.st_mtime_ns, after.st_ctime_ns)
        != (before.st_mtime_ns, before.st_ctime_ns)
        or not stat.S_ISREG(after.st_mode)
    ):
        raise RuntimeSmokeError("final DMG changed during mounted runtime acceptance")
    if digest != before_digest:
        raise RuntimeSmokeError(
            "final DMG content changed during mounted runtime acceptance"
        )
    return {
        "dmg": str(dmg),
        "size_bytes": before.st_size,
        "sha256": digest,
        "mounted_read_only": True,
        "runtime": runtime_report,
        "passed": True,
    }
